#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""LLM director: one director call writes the plan, the code editor cuts it, the director reviews the cut.

Usage:
    python scripts/llm_director.py --workspace DIR --brief "..."
    python scripts/llm_director.py --workspace DIR --brief-file brief.txt --json
    python scripts/llm_director.py --workspace DIR --brief "..." --dry-run
    python scripts/llm_director.py --workspace DIR --brief "..." --rounds 2 --out my_edit.json
    python scripts/llm_director.py --workspace DIR --brief "..." --feedback "drop 0071/s2, tunnel longer"

Reads ``shotlog.json``, ``ingest.json``, ``motion/<stem>.json`` and
``quality/<stem>.json`` from the workspace and runs the film crew: one director
call writes ``director_plan.json``, the code editor (``director_editor.py``)
cuts it into ``edit.json`` and ``editor_report.json`` without changing the
shots, and the director reviews the cut (one picture per shot plus the report)
for at most 2 rounds. The rule-based ``scripts/director.py`` is the fallback
only when the model gives no usable plan and the ``--dry-run`` baseline.
Sources are read-only; outputs are written atomically.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from common.config import ConfigError, director_reasoning_effort, load_config, notebook_path
from common.brief_facts import facts_block, load_brief_facts
from common.feedback import append_feedback, read_feedback
from common.intent import is_whip_short_hold
from common.notebook import notebook_block
from common.pacing import estimate_all_good
from common.frames import FramesError, THUMBNAIL_WIDTH, extract_frame, file_data_uri
from common.llm import BudgetExceeded, LLMError, make_client, reasoning_extra_body
from common import runlog
from common.facts import (
    DEFAULT_IMPORTANT_MAX_S,
    FactsError,
    build_shot_facts,
    clamp,
    load_shotlog,
)
from common.beatsnap import BeatsError, load_beats
from common.moves import is_star, slot_duration
from common.schema import SchemaError, validate, validate_or_raise
from common.exit_codes import EXIT_OK, EXIT_USAGE, EXIT_RUNTIME
from common.errors import report_error

TOOL = "scripts/llm_director.py"
SCHEMA_VERSION = "0.1"
REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = REPO_ROOT / "prompts"
DIRECTOR_SCRIPT = REPO_ROOT / "scripts" / "director.py"

DEFAULT_ROUNDS = 2
FLOW_MAX_ROUNDS = 2
PLAN_FILE = "director_plan.json"
REPORT_FILE = "editor_report.json"
ROUNDS_FILE = "director_rounds.json"
REVIEW_FILE = "director_review.json"
REVIEW_APPLIED_FILE = "director_review.applied.json"
SHORTLIST_FACTOR = 2
SHORTLIST_MIN = 24
SHORTLIST_MAX = 60
LONG_CUT_PACING_S = 180.0
DEFAULT_TARGET_S = 90.0
TRAVEL_LABELS = {"travel_forward", "travel_backward", "orbit", "orbit_candidate"}

DIRECTOR_PROGRESS_RE = re.compile(r"^\[\s*\d+/\s*\d+\]\s+llm_director\s+\S+\s+(\d+(?:\.\d+)?)s\b")
DURATION_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(seconds|second|secs|sec|minutes|minute|mins|min|s)\b",
    re.IGNORECASE,
)
MINUTE_UNITS = {"minutes", "minute", "mins", "min"}
ORDER_CHRONOLOGICAL = ("chronological", "in order", "as it happened")
ORDER_SCORE = ("by score", "best first", "ranked", "top shots")
UNIFORM_PHRASES = ("uniform", "same length", "even")
AMBIENT_PHRASES = ("ambient sound only", "ambient only", "ambient audio only", "no music")

BRIEF_KEYWORDS = (
    (
        "tutorial",
        ("tutorial", "how to", "how-to", "lesson", "guide", "walkthrough", "explain", "step by step", "steps"),
    ),
    (
        "presentation",
        ("presentation", "slides", "keynote", "conference", "lecture", "talk", "demo", "pitch"),
    ),
    (
        "talking-head",
        (
            "talking head",
            "talking-head",
            "vlog",
            "podcast",
            "interview",
            "monologue",
            "face to camera",
            "face-to-camera",
            "sit down",
        ),
    ),
    (
        "vacation",
        ("trip", "travel", "vacation", "holiday", "recap", "adventure", "hike", "boat", "weekend", "day out", "journey"),
    ),
)


class LLMDirectorRuntimeError(RuntimeError):
    """Raised when the rule-based fallback director fails."""


class LLMDirectorError(RuntimeError):
    """Raised when the LLM director cannot produce an edit."""


class LoopState:
    """Model calls and the spend of one run."""

    def __init__(self):
        self.calls = 0
        self.cost_usd = 0.0
        self.reasoning_effort = None


def build_parser():
    parser = argparse.ArgumentParser(
        prog="llm_director.py",
        description=(
            "Turn a brief and a workspace shotlog into edit.json with the film crew: "
            "one director call writes director_plan.json, the code editor cuts it into "
            "edit.json and editor_report.json without changing the shots, and the "
            "director reviews the cut (one picture per shot plus the report) for at "
            "most 2 rounds. The rule-based director is the fallback only when the "
            "model gives no usable plan."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/llm_director.py --workspace <workspace> --brief \"about 90 seconds, calm start\"\n"
            "  python scripts/llm_director.py --workspace <workspace> --brief-file brief.txt --json\n"
            "  python scripts/llm_director.py --workspace <workspace> --brief \"...\" --dry-run\n"
            "  python scripts/llm_director.py --workspace <workspace> --brief \"...\" --rounds 2 --out my_edit.json\n"
            "  python scripts/llm_director.py --workspace <workspace> --brief \"...\" --feedback \"drop 0071/s2, tunnel longer\"\n"
            "  python scripts/llm_director.py --workspace <workspace> --chat \"drop 0071/s2, tunnel longer\" --json\n"
            "  python scripts/llm_director.py --workspace <workspace> --brief \"...\" --prepare --json\n"
            "  python scripts/llm_director.py --workspace <workspace> --brief \"...\" --apply --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--workspace", required=True, help="workspace holding shotlog.json, ingest.json, motion/ and quality/")
    parser.add_argument("--brief", default=None, help="the brief in plain language")
    parser.add_argument("--brief-file", default=None, help="path to a file holding the brief")
    parser.add_argument("--out", default=None, help="edit list to write (default WORKSPACE/edit.json)")
    parser.add_argument("--plan-out", default=None, help="director plan to write (default WORKSPACE/director_plan.json)")
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS, help="review rounds (default 2, max 2)")
    parser.add_argument("--dry-run", action="store_true", help="run only the director and print the rule-based baseline")
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="write WORKSPACE/director/inputs.md for the host agent; no model call",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="read the agent's director_plan.json (and director_review.json), validate, run the editor and write the final edit.json",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="with --prepare: write the pictures and the editor report for the notes round",
    )
    parser.add_argument("--fallback-rules", dest="fallback_rules", action="store_true", default=True, help="fall back to the rule-based director when the model gives no usable plan (default)")
    parser.add_argument("--no-fallback-rules", dest="fallback_rules", action="store_false", help="error instead of falling back to the rule-based director")
    parser.add_argument("--model", default=None, help="director model id override (default models.director from config)")
    parser.add_argument("--budget-usd", type=float, default=None, help="refuse calls past this spend (default llm_budget_usd)")
    parser.add_argument(
        "--reasoning-effort",
        choices=["low", "medium", "high", "off"],
        default=None,
        help=(
            "director reasoning effort sent to OpenRouter on every call "
            "(default: director.reasoning_effort in the config, else medium)"
        ),
    )
    parser.add_argument(
        "--important-max-s",
        type=float,
        default=None,
        help=(
            "longest a hero or semantic-move slot may be "
            "(default: from the pacing table, 6.0 s ceiling)"
        ),
    )
    parser.add_argument("--target-duration-s", type=float, default=None, help="override the target duration parsed from the brief")
    parser.add_argument("--order", choices=["chronological", "score"], default=None, help="override the order parsed from the brief")
    parser.add_argument("--pacing", choices=["calm-to-energy", "uniform"], default=None, help="override the pacing parsed from the brief")
    parser.add_argument(
        "--beats",
        default=None,
        help="beat grid JSON from beats.py; snap every cut to the nearest beat and join with hard cuts",
    )
    parser.add_argument(
        "--music",
        default=None,
        help="music track to record in edit.json audio.music (the render mixes it under the ambient bus)",
    )
    parser.add_argument(
        "--ambient-under-music-db",
        type=float,
        default=None,
        help="how far the ambient bus sits under the music, in dB (recorded in edit.json)",
    )
    parser.add_argument(
        "--feedback",
        action="append",
        default=None,
        metavar="TEXT",
        help=(
            "feedback in plain words against the current edit's short ids, "
            'e.g. "drop 0071/s2, tunnel longer"; repeatable'
        ),
    )
    parser.add_argument(
        "--version",
        default=None,
        metavar="vNNN",
        help="read the previous edit context from versions/vNNN/edit.json (with --chat)",
    )
    parser.add_argument(
        "--chat",
        default=None,
        metavar="TEXT",
        help=(
            "talk to the director about the current edit: one model call with the director prompt "
            "plus the chat addendum, the last plan, the editor report, the rounds, the owner's notes "
            "and the notebook; the answer is a plain reply and, when the owner decided something, "
            "a short decision_note stored as an owner note; no edit written"
        ),
    )
    parser.add_argument(
        "--speed-ramp",
        action="store_true",
        help=(
            "optional effect: let the middle half of a long camera move play up to 1.2x so more of "
            "the move fits (default off: every slot plays at normal speed)"
        ),
    )
    parser.add_argument("--force", action="store_true", help="rewrite even when the output is up to date")
    parser.add_argument(
        "--notebook",
        default=None,
        help=(
            "instance notebook JSON file (or YT_PIPELINE_NOTEBOOK, or notebook.path "
            "in the config; no personal default)"
        ),
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def best_eligible_per_clip(rows):
    """The highest-scoring eligible row of every clip, skipping whip short-holds."""
    best = {}
    for row in rows:
        if not row.get("eligible", True):
            continue
        if is_whip_short_hold(row):
            continue
        clip = row.get("clip")
        current = best.get(clip)
        if current is None or float(row.get("score") or 0.0) > float(current.get("score") or 0.0):
            best[clip] = row
    return list(best.values())


def shortlist_rows(rows, n_slots):
    """The director's menu: every star, the top-K by score and every clip's best eligible segment, in shotlog order."""
    stars = [row for row in rows if is_star(row)]
    k = int(clamp(n_slots * SHORTLIST_FACTOR, SHORTLIST_MIN, SHORTLIST_MAX))
    ranked = sorted(rows, key=lambda row: (-float(row.get("score") or 0.0), row.get("key") or ""))
    keep = {id(row) for row in stars}
    keep.update(id(row) for row in ranked[:k])
    keep.update(id(row) for row in best_eligible_per_clip(rows))
    return [row for row in rows if id(row) in keep]


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


PACING_ANCHORS = (
    (20.0, 1.5, 2.0, 2.5),
    (90.0, 2.0, 3.6, 5.0),
    (180.0, 2.5, 5.0, 6.0),
)


def pacing_table(target_duration_s):
    """Slot pacing for a brief's target duration: per-shot min/target/max, hero cap, slot count.

    Linear interpolation between the anchors, clamped outside them. ``hero_max``
    keeps hero and semantic-move slots the longest while shrinking with the
    target, under the ``DEFAULT_IMPORTANT_MAX_S`` ceiling.
    """
    t = clamp(float(target_duration_s), PACING_ANCHORS[0][0], PACING_ANCHORS[-1][0])
    low, high = (
        (PACING_ANCHORS[0], PACING_ANCHORS[1])
        if t <= PACING_ANCHORS[1][0]
        else (PACING_ANCHORS[1], PACING_ANCHORS[2])
    )
    fraction = (t - low[0]) / (high[0] - low[0])
    slot_min = round(low[1] + (high[1] - low[1]) * fraction, 2)
    slot_target = round(low[2] + (high[2] - low[2]) * fraction, 2)
    slot_max = round(low[3] + (high[3] - low[3]) * fraction, 2)
    hero_max = round(min(DEFAULT_IMPORTANT_MAX_S, slot_target * 1.75), 2)
    n_slots = int(clamp(round(t / slot_target), 4, 32))
    return {
        "slot_min": slot_min,
        "slot_target": slot_target,
        "slot_max": slot_max,
        "hero_max": hero_max,
        "n_slots": n_slots,
    }


def run_pacing(target_duration_s):
    """The pacing row for a run: the brief's target, or the 90 s row when it has none."""
    return pacing_table(target_duration_s if target_duration_s is not None else DEFAULT_TARGET_S)


def parse_brief(text):
    signals = []
    duration, phrase = parse_duration(text)
    if phrase:
        signals.append(phrase)
    order, phrase = parse_order(text)
    if phrase:
        signals.append(phrase)
    pacing, phrases = parse_pacing(text)
    signals.extend(phrases)
    ambient_only, phrase = parse_ambient_only(text)
    if phrase:
        signals.append(phrase)
    return {
        "target_duration_s": duration,
        "order": order,
        "pacing": pacing,
        "ambient_only": ambient_only,
        "signals": signals,
    }


def keyword_re(keyword):
    return re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)


def first_phrase(text, phrases):
    for phrase in phrases:
        if keyword_re(phrase).search(text):
            return phrase
    return None


def parse_duration(text):
    match = DURATION_RE.search(text)
    if not match:
        return None, None
    value = float(match.group(1))
    seconds = value * 60 if match.group(2).lower() in MINUTE_UNITS else value
    return seconds, match.group(0)


def parse_order(text):
    phrase = first_phrase(text, ORDER_CHRONOLOGICAL)
    if phrase:
        return "chronological", phrase
    phrase = first_phrase(text, ORDER_SCORE)
    if phrase:
        return "score", phrase
    return None, None


def parse_pacing(text):
    has_calm = bool(keyword_re("calm").search(text))
    has_energy = bool(keyword_re("energy").search(text))
    if has_calm and has_energy:
        return "calm-to-energy", ["calm", "energy"]
    if keyword_re("slow start").search(text):
        return "calm-to-energy", ["slow start"]
    if keyword_re("build").search(text):
        return "calm-to-energy", ["build"]
    phrase = first_phrase(text, UNIFORM_PHRASES)
    if phrase:
        return "uniform", [phrase]
    return None, []


def parse_ambient_only(text):
    phrase = first_phrase(text, AMBIENT_PHRASES)
    return (phrase is not None), phrase


def detect_content_type(text, workspace):
    for content_type, keywords in BRIEF_KEYWORDS:
        if first_phrase(text, keywords):
            return content_type
    detected = detect_from_workspace(workspace)
    return detected or "vacation"


def detect_from_workspace(workspace):
    data = load_json(workspace / "shotlog.json")
    if not isinstance(data, dict):
        return None
    shots = data.get("shots") or []
    if not shots:
        return None
    travel = sum(
        1
        for shot in shots
        if shot.get("motion_label") in TRAVEL_LABELS or shot.get("semantic_motion") == "travel"
    )
    if travel * 2 >= len(shots):
        return "vacation"
    holds = sum(1 for shot in shots if shot.get("role") == "hold")
    if holds * 2 > len(shots):
        return "talking-head"
    return None


def strip_fences(text):
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def loads_object(text):
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def parse_json(text):
    if not text:
        return None
    data = loads_object(strip_fences(text))
    if data is None:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            data = loads_object(text[start : end + 1])
    return data


def read_prompt(name):
    path = PROMPTS_DIR / name
    if not path.exists():
        raise LLMDirectorError(f"prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def call_client(client, messages, state, role="director"):
    extra = reasoning_extra_body(state.reasoning_effort)
    if extra is not None:
        response = client.complete(role, messages, extra_body=extra)
    else:
        response = client.complete(role, messages)
    state.calls += 1
    state.cost_usd = round(state.cost_usd + float(response.cost_usd), 8)
    return response


def append_feedback_lines(workspace, feedback):
    """Append one JSON line per feedback text to WORKSPACE/feedback.jsonl (append-only)."""
    for text in feedback:
        append_feedback(workspace, "feedback", text=text, tool=TOOL)


def last_director_estimate(workspace):
    """The last run's director time and cost from run.log and llm_spend.json, or (None, None)."""
    workspace = Path(workspace)
    start = runlog.last_run_start(workspace)
    if start is None:
        return None, None
    estimate_s = None
    for line in runlog.last_run_lines(workspace):
        match = DIRECTOR_PROGRESS_RE.match(line)
        if match:
            estimate_s = float(match.group(1))
            break
    estimate_usd = runlog.spend_between(workspace, start, role="director")
    return estimate_usd, estimate_s


def build_chat_user(text, workspace, edit_path=None, config=None):
    """The user message for the chat call: the message plus the plan, report, rounds, notes and notebook."""
    workspace = Path(workspace)
    blocks = [f"The owner's message:\n{text}"]
    for label, name in (
        ("The last plan (director_plan.json)", PLAN_FILE),
        ("The editor's report (editor_report.json)", REPORT_FILE),
        ("The rounds (director_rounds.json)", ROUNDS_FILE),
    ):
        data = load_json(workspace / name)
        blocks.append(f"{label}:\n{json.dumps(data, ensure_ascii=False) if data is not None else 'none'}")
    notes = [
        row["text"]
        for row in read_feedback(workspace)
        if row.get("action") == "feedback" and isinstance(row.get("text"), str) and row["text"].strip()
    ][-10:]
    notes_block = "\n".join(f"- {note}" for note in notes) if notes else "none"
    blocks.append(f"The owner's earlier notes (feedback.jsonl):\n{notes_block}")
    notebook_file = notebook_path(None, config)
    notebook_text = notebook_block(notebook_file) if notebook_file is not None else None
    blocks.append(f"The notebook:\n{notebook_text or 'none'}")
    path = Path(edit_path) if edit_path is not None else workspace / "edit.json"
    edit = load_json(path)
    director = (edit.get("meta") or {}).get("director") if isinstance(edit, dict) else None
    blocks.append(
        "The shown version's director info (edit.json meta.director):\n"
        + (json.dumps(director, ensure_ascii=False) if director is not None else "none")
    )
    return "\n\n".join(blocks)


def director_chat(workspace, text, version=None, model=None, budget_usd=None, config=None, client=None, reasoning_effort=None):
    """One director call that answers the owner's chat message about the current edit."""
    started = time.monotonic()
    workspace_path = Path(workspace).expanduser().resolve()
    if not workspace_path.is_dir():
        raise LLMDirectorError(f"workspace not found: {workspace_path}")
    if not text or not text.strip():
        raise LLMDirectorError("provide --chat TEXT")
    edit_path = None
    if version is not None:
        if not re.fullmatch(r"v\d+", version):
            raise LLMDirectorError(f"--version must look like vNNN, got {version!r}")
        edit_path = workspace_path / "versions" / version / "edit.json"
    if client is None:
        if config is None:
            config = load_config()
        effective = dict(config)
        models = dict(effective.get("models") or {})
        if model is not None:
            models["director"] = model
        effective["models"] = models
        if budget_usd is not None:
            effective["llm_budget_usd"] = budget_usd
        client = make_client(config=effective, ledger_path=workspace_path / "llm_spend.json")
    prompt = (
        read_prompt("director.md").rstrip()
        + "\n\n"
        + read_prompt("tools.md").rstrip()
        + "\n\n"
        + read_prompt("director_chat.md")
    )
    state = LoopState()
    state.reasoning_effort = director_reasoning_effort(reasoning_effort, config)
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": build_chat_user(text, workspace_path, edit_path, config)},
    ]
    answer = None
    for _ in range(2):
        try:
            response = call_client(client, messages, state)
        except RuntimeError as exc:
            raise LLMDirectorRuntimeError(str(exc)) from exc
        data = parse_json(response.text)
        if not isinstance(data, dict):
            continue
        reply = data.get("reply")
        if not isinstance(reply, str) or not reply.strip():
            continue
        if data.get("can_do") not in ("all", "part", "none"):
            continue
        answer = data
        break
    if answer is None:
        raise LLMDirectorRuntimeError("model produced no usable reply after one retry")
    reply = answer["reply"].strip()
    decision_note = answer.get("decision_note")
    if isinstance(decision_note, str) and decision_note.strip():
        decision_note = decision_note.strip()
    else:
        decision_note = None
    can_do = answer["can_do"]
    missing_tool = answer.get("missing_tool")
    if can_do != "all" and isinstance(missing_tool, str) and missing_tool.strip():
        missing_tool = missing_tool.strip()
    else:
        missing_tool = None
    estimate_usd, estimate_s = last_director_estimate(workspace_path)
    append_feedback(
        workspace_path,
        "reply",
        text=text,
        reply=reply,
        decision_note=decision_note,
        estimate={"usd": estimate_usd, "s": estimate_s},
        can_do=can_do,
        missing_tool=missing_tool,
        tool=TOOL,
    )
    if decision_note is not None:
        append_feedback(workspace_path, "feedback", text=decision_note, tool=TOOL)
    return {
        "reply": reply,
        "decision_note": decision_note,
        "can_do": can_do,
        "missing_tool": missing_tool,
        "estimate_usd": estimate_usd,
        "estimate_s": estimate_s,
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": round(state.cost_usd, 8),
    }, EXIT_OK


def previous_edit_context(workspace, edit_path=None):
    """The previous edit's used slots and rejected candidates, for the feedback block."""
    path = Path(edit_path) if edit_path is not None else Path(workspace) / "edit.json"
    data = load_json(path)
    if not isinstance(data, dict):
        return None
    shotlog = load_shotlog(Path(workspace))
    used = []
    for slot in data.get("video") or []:
        row = {"id": slot.get("id"), "shot_key": slot.get("shot_key")}
        if slot.get("kind") == "photo":
            row["photo"] = slot.get("photo")
            row["duration_s"] = slot.get("duration_s")
        else:
            row["clip"] = slot.get("clip")
            row["in_s"] = slot.get("in_s")
            row["out_s"] = slot.get("out_s")
        row["reason"] = slot.get("reason")
        shot = shotlog.get(slot.get("shot_key")) if shotlog else None
        row["summary"] = shot.get("summary") if shot else None
        row["subject"] = shot.get("subject") if shot else None
        row["tags"] = list(shot.get("tags") or []) if shot else []
        used.append(row)
    rejected = []
    for row in data.get("decisions") or []:
        if not isinstance(row, dict) or row.get("status") != "rejected":
            continue
        shot = shotlog.get(row.get("id")) if shotlog else None
        rejected.append(
            {
                "id": row.get("id"),
                "reason": row.get("reason"),
                "summary": shot.get("summary") if shot else None,
                "subject": shot.get("subject") if shot else None,
            }
        )
    return {"used": used, "rejected": rejected}


def build_feedback_block(feedback, workspace, edit_path=None):
    """The user-message block that carries the feedback and the short ids it may name."""
    lines = ["Feedback to apply (plain words, refers to the short ids below):"]
    lines.extend(f"- {text}" for text in feedback)
    lines.append("")
    previous = previous_edit_context(workspace, edit_path)
    if previous is None:
        lines.append("Previous edit: none (no previous edit.json; the feedback refers to no existing ids)")
        return "\n".join(lines)
    lines.append("Previous edit (the feedback refers to these ids):")
    lines.append(json.dumps(previous["used"], ensure_ascii=False))
    lines.append(
        "Rejected candidates the feedback may name: "
        + json.dumps(previous["rejected"], ensure_ascii=False)
    )
    return "\n".join(lines)


def atomic_write_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


DIRECTOR_NOTES_MAX = 20


def director_info(by, approved, warnings, review=None):
    """``meta.director``: who made the edit, whether the director approved it, and the notes."""
    info = {
        "by": by,
        "approved": approved,
        "notes": [str(note)[:300] for note in warnings][:DIRECTOR_NOTES_MAX],
    }
    if review is not None:
        info["review"] = review
    return info


def write_edit(path, record, started, cost_usd, director=None):
    record["meta"] = {
        "tool": TOOL,
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": round(cost_usd, 8),
        "speed_ramp": bool((record.get("meta") or {}).get("speed_ramp", False)),
    }
    if director is not None:
        record["meta"]["director"] = director
    validate_or_raise(record, "edit")
    atomic_write_json(path, record)


def hint_flags(hints, music=None, ambient_under_db=None, speed_ramp=False):
    flags = []
    if speed_ramp:
        flags.append("--speed-ramp")
    if hints.get("target_duration_s") is not None:
        flags += ["--target-duration-s", str(hints["target_duration_s"])]
    if hints.get("order") is not None:
        flags += ["--order", hints["order"]]
    if hints.get("pacing") is not None:
        flags += ["--pacing", hints["pacing"]]
    if music is not None:
        flags += ["--music", str(music)]
    if ambient_under_db is not None:
        flags += ["--ambient-under-music-db", str(ambient_under_db)]
    return flags


def run_rules_director(workspace, brief_text, hints, out_path, beats_path=None, music=None, ambient_under_db=None, speed_ramp=False, keep_keys=frozenset(), add_keys=frozenset(), drop_keys=frozenset()):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / "rules_edit.json"
        command = [
            sys.executable,
            str(DIRECTOR_SCRIPT),
            "--workspace",
            str(workspace),
            "--brief",
            brief_text,
            "--out",
            str(tmp),
            "--json",
        ] + hint_flags(hints, music, ambient_under_db, speed_ramp)
        if keep_keys:
            command += ["--keep-shots", ",".join(sorted(keep_keys))]
        if add_keys:
            command += ["--add-shots", ",".join(sorted(add_keys))]
        if drop_keys:
            command += ["--drop-shots", ",".join(sorted(drop_keys))]
        if beats_path is not None:
            command += ["--beats", str(beats_path)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise LLMDirectorRuntimeError(f"rule-based director failed: {result.stderr.strip()}")
        data = load_json(tmp)
        summary = parse_json(result.stdout) or {}
    if data is None:
        raise LLMDirectorRuntimeError("rule-based director wrote no edit")
    atomic_write_json(out_path, data)
    return data, summary.get("beat_snap"), summary.get("applied_keys") or [], summary.get("not_applied_keys") or []


def run_baseline(workspace, brief_text, hints, beats_path=None, music=None, ambient_under_db=None, speed_ramp=False):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / "baseline_edit.json"
        command = [
            sys.executable,
            str(DIRECTOR_SCRIPT),
            "--workspace",
            str(workspace),
            "--brief",
            brief_text,
            "--out",
            str(tmp),
            "--json",
        ] + hint_flags(hints, music, ambient_under_db, speed_ramp)
        if beats_path is not None:
            command += ["--beats", str(beats_path)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise LLMDirectorRuntimeError(f"rule-based director failed: {result.stderr.strip()}")
        data = load_json(tmp)
    if data is None:
        raise LLMDirectorRuntimeError("rule-based director wrote no edit")
    slots = data.get("video") or []
    total = sum(slot_duration(slot) for slot in slots)
    return {
        "slots": len(slots),
        "total_duration_s": round(total, 2),
        "reasons": [slot["reason"] for slot in slots[:5]],
    }


def input_paths(workspace, brief_path, plan_path, beats_path=None):
    paths = [workspace / "shotlog.json", workspace / "brief_facts.json"]
    if brief_path is not None:
        paths.append(brief_path)
    if plan_path is not None:
        paths.append(plan_path)
    if beats_path is not None:
        paths.append(beats_path)
    return paths


def is_up_to_date(out_path, workspace, brief_path, plan_path, beats_path=None, speed_ramp=False):
    if not out_path.exists():
        return False
    data = load_json(out_path)
    if data is None or validate(data, "edit"):
        return False
    if bool((data.get("meta") or {}).get("speed_ramp", False)) != bool(speed_ramp):
        return False
    out_mtime = out_path.stat().st_mtime
    for path in input_paths(workspace, brief_path, plan_path, beats_path):
        if path.exists() and path.stat().st_mtime > out_mtime:
            return False
    return True


def base_summary(status, out_path, plan_path, rounds_used, approved, fallback, slots, total, violations, warnings, started, cost_usd, candidates=0, beat_snap=None, shortlist=0, applied_keys=None, not_applied_keys=None):
    return {
        "status": status,
        "out": str(out_path) if out_path is not None else None,
        "plan": str(plan_path) if plan_path is not None and plan_path.exists() else None,
        "rounds_used": rounds_used,
        "approved": approved,
        "fallback": fallback,
        "slots": slots,
        "candidates": candidates,
        "shortlist": shortlist,
        "total_duration_s": round(total, 2),
        "validator_violations": violations,
        "warnings": warnings,
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": round(cost_usd, 8),
        "beat_snap": beat_snap,
        "applied_keys": list(applied_keys or []),
        "not_applied_keys": list(not_applied_keys or []),
    }


def fallback_result(workspace, brief_text, hints, out_path, plan_path, warnings, state, rounds_used, approved, violations, started, candidates=0, beats_path=None, music=None, ambient_under_db=None, speed_ramp=False, shortlist=0, keep_keys=frozenset(), add_keys=frozenset(), drop_keys=frozenset()):
    try:
        data, beat_snap, applied_keys, not_applied_keys = run_rules_director(
            workspace, brief_text, hints, out_path, beats_path, music, ambient_under_db, speed_ramp,
            keep_keys, add_keys, drop_keys,
        )
        data.setdefault("meta", {})["director"] = director_info("rules", approved, warnings)
        validate_or_raise(data, "edit")
        atomic_write_json(out_path, data)
    except (LLMDirectorError, FactsError, BeatsError) as exc:
        warnings.append(str(exc))
        return base_summary("error", out_path, plan_path, rounds_used, approved, None, 0, 0.0, len(violations), warnings, started, state.cost_usd, candidates, shortlist=shortlist), 1
    slots = data.get("video") or []
    total = sum(slot_duration(slot) for slot in slots)
    return base_summary("fallback", out_path, plan_path, rounds_used, approved, "rules", len(slots), total, len(violations), warnings, started, state.cost_usd, candidates, beat_snap, shortlist=shortlist, applied_keys=applied_keys, not_applied_keys=not_applied_keys), 0


def stop_result(out_path, plan_path, warnings, state, rounds_used, approved, violations, started, code, candidates=0, shortlist=0):
    return base_summary("error", out_path, plan_path, rounds_used, approved, None, 0, 0.0, len(violations), warnings, started, state.cost_usd, candidates, shortlist=shortlist), code


def run(
    workspace,
    brief=None,
    brief_file=None,
    out=None,
    plan_out=None,
    rounds=DEFAULT_ROUNDS,
    dry_run=False,
    fallback_rules=True,
    model=None,
    budget_usd=None,
    force=False,
    config=None,
    client=None,
    important_max_s=None,
    target_duration_s=None,
    order=None,
    pacing=None,
    beats=None,
    music=None,
    ambient_under_music_db=None,
    feedback=None,
    speed_ramp=False,
    notebook=None,
    reasoning_effort=None,
    picture_fn=None,
):
    """Run the director flow and return ``(summary, exit_code)``."""
    summary, code = run_director_flow(
        workspace,
        brief=brief,
        brief_file=brief_file,
        out=out,
        plan_out=plan_out,
        rounds=rounds,
        dry_run=dry_run,
        fallback_rules=fallback_rules,
        model=model,
        budget_usd=budget_usd,
        force=force,
        config=config,
        client=client,
        important_max_s=important_max_s,
        target_duration_s=target_duration_s,
        order=order,
        pacing=pacing,
        beats=beats,
        music=music,
        ambient_under_music_db=ambient_under_music_db,
        feedback=feedback,
        speed_ramp=speed_ramp,
        notebook=notebook,
        reasoning_effort=reasoning_effort,
        picture_fn=picture_fn,
    )
    summary["feedback"] = list(feedback or [])
    return summary, code


def run_director_flow(
    workspace,
    brief=None,
    brief_file=None,
    out=None,
    plan_out=None,
    rounds=DEFAULT_ROUNDS,
    dry_run=False,
    fallback_rules=True,
    model=None,
    budget_usd=None,
    force=False,
    config=None,
    client=None,
    important_max_s=None,
    target_duration_s=None,
    order=None,
    pacing=None,
    beats=None,
    music=None,
    ambient_under_music_db=None,
    feedback=None,
    speed_ramp=False,
    notebook=None,
    reasoning_effort=None,
    picture_fn=None,
):
    """The film-crew flow: one director call, the code editor, at most 2 review rounds."""
    started = time.monotonic()
    workspace_path, brief_text, brief_path = _resolve_workspace_brief(workspace, brief, brief_file)
    feedback = list(feedback or [])
    if feedback:
        append_feedback_lines(workspace_path, feedback)
    feedback_block = build_feedback_block(feedback, workspace_path) if feedback else None
    notebook_file = notebook_path(notebook)
    notebook_text = notebook_block(notebook_file) if notebook_file is not None else None

    out_path = Path(out).expanduser().resolve() if out else workspace_path / "edit.json"
    plan_path = Path(plan_out).expanduser().resolve() if plan_out else workspace_path / PLAN_FILE
    beats_path = Path(beats).expanduser().resolve() if beats else None
    if music:
        music_path = Path(music).expanduser().resolve()
        if not music_path.is_file():
            raise LLMDirectorError(f"music file not found: {music}")
        music = str(music_path)
    beat_grid = load_beats(beats_path) if beats_path is not None else None

    if not force and not dry_run and not feedback and is_up_to_date(out_path, workspace_path, brief_path, plan_path, beats_path, speed_ramp):
        existing = load_json(out_path) or {}
        slots = existing.get("video") or []
        total = sum(slot_duration(slot) for slot in slots)
        return base_summary("skipped", out_path, plan_path, 0, None, None, len(slots), total, 0, [], started, 0.0), 0

    warnings = []
    state = LoopState()
    shotlog = load_shotlog(workspace_path)
    if shotlog is None:
        warnings.append("no shotlog.json in the workspace; using the rule-based director")
        hints = parse_brief(brief_text)
        if target_duration_s is not None:
            hints["target_duration_s"] = target_duration_s
        if order is not None:
            hints["order"] = order
        if pacing is not None:
            hints["pacing"] = pacing
        if fallback_rules:
            return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, 0, None, [], started, beats_path=beats_path, music=music, ambient_under_db=ambient_under_music_db, speed_ramp=speed_ramp)
        return stop_result(out_path, plan_path, warnings, state, 0, None, [], started, EXIT_USAGE)

    context = _agent_context(workspace_path, shotlog, brief_text, important_max_s, target_duration_s, order, pacing)
    hints = context["hints"]
    prompt_brief = context["prompt_brief"]
    content_type = context["content_type"]
    pacing = context["pacing"]
    effective_important_max_s = context["effective_important_max_s"]
    facts = context["facts"]
    clips = context["clips"]
    fact_warnings = context["fact_warnings"]
    sources = context["sources"]
    shotlog_rows = context["shotlog_rows"]
    shortlist = context["shortlist"]
    brief_obj = context["brief_obj"]
    drop_reasons = context["drop_reasons"]
    warnings.extend(fact_warnings)
    if not facts:
        warnings.append("no eligible shots in the shotlog; using the rule-based director")
        if fallback_rules:
            return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, 0, None, [], started, beats_path=beats_path, music=music, ambient_under_db=ambient_under_music_db, speed_ramp=speed_ramp, shortlist=len(shortlist))
        return stop_result(out_path, plan_path, warnings, state, 0, None, [], started, EXIT_USAGE, shortlist=len(shortlist))
    candidates = len(shotlog_rows)

    if client is None:
        if config is None:
            config = load_config()
        effective = dict(config)
        models = dict(effective.get("models") or {})
        if model is not None:
            models["director"] = model
        effective["models"] = models
        if budget_usd is not None:
            effective["llm_budget_usd"] = budget_usd
        client = make_client(config=effective, ledger_path=workspace_path / "llm_spend.json")

    state.reasoning_effort = director_reasoning_effort(reasoning_effort, config)

    prompt = read_prompt("director.md")
    payload = _director_payload(context, brief_text, feedback_block, notebook_text)

    try:
        plan, plan_errors = _director_plan_call(client, prompt, payload, state, facts, brief_obj, content_type)
    except (LLMError, BudgetExceeded) as exc:
        plan, plan_errors = None, str(exc)
    if plan is None:
        warnings.append("the director gave no usable plan: " + plan_errors)
        if fallback_rules:
            return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, 0, None, [], started, candidates, beats_path, music, ambient_under_music_db, speed_ramp, shortlist=len(shortlist))
        return stop_result(out_path, plan_path, warnings, state, 0, None, [], started, EXIT_RUNTIME, candidates, shortlist=len(shortlist))

    _write_director_plan(plan_path, plan, started, state.cost_usd)
    if dry_run:
        baseline = run_baseline(workspace_path, prompt_brief, hints, beats_path, music, ambient_under_music_db, speed_ramp)
        summary = base_summary("written", None, plan_path, 0, None, None, 0, 0.0, 0, warnings, started, state.cost_usd, candidates, shortlist=len(shortlist))
        summary["baseline"] = baseline
        summary["dry_run"] = True
        return summary, 0

    max_rounds = max(1, min(rounds, FLOW_MAX_ROUNDS))
    rounds_log = []
    approved = None
    notes = []
    round_cost_start = 0.0
    report = None
    for round_index in range(1, max_rounds + 1):
        edit, report = _run_editor(
            workspace_path, plan_path, out_path, workspace_path / REPORT_FILE, beats_path,
            music=music, ambient_under_music_db=ambient_under_music_db, speed_ramp=speed_ramp,
        )
        for note in report.get("could_not_do") or []:
            if note not in warnings:
                warnings.append(note)
        pictures = _make_pictures(edit, workspace_path, round_index, picture_fn, warnings)
        review, review_errors = _director_review_call(client, prompt, payload, report, pictures, state, facts, brief_obj, content_type)
        round_cost = round(state.cost_usd - round_cost_start, 8)
        round_cost_start = state.cost_usd
        if review is None:
            detail = review_errors if isinstance(review_errors, str) else "; ".join(review_errors)
            warnings.append("the director gave no usable review: " + detail)
            rounds_log.append({"round": round_index, "plan": plan, "report": report, "review": None, "cost_usd": round_cost})
            _write_rounds(workspace_path / ROUNDS_FILE, rounds_log, state.cost_usd)
            break
        approved = bool(review["approved"])
        rounds_log.append(
            {
                "round": round_index,
                "plan": plan,
                "report": report,
                "review": {"approved": approved, "summary": review["summary"], "notes": list(review.get("notes") or [])},
                "cost_usd": round_cost,
            }
        )
        _write_rounds(workspace_path / ROUNDS_FILE, rounds_log, state.cost_usd)
        if approved:
            break
        notes = list(review.get("notes") or [])
        plan = review["plan"]
        _write_director_plan(plan_path, plan, started, state.cost_usd)
        if round_index == max_rounds:
            warnings.append("the director did not approve after 2 rounds; delivering the cut with its notes")
            break

    edit = load_json(out_path)
    if not isinstance(edit, dict) or not edit.get("video"):
        raise LLMDirectorRuntimeError("the editor wrote no usable edit")
    edit["decisions"] = flow_decisions(plan, report, shotlog, facts)
    edit.setdefault("meta", {})["speed_ramp"] = bool(speed_ramp)
    write_edit(out_path, edit, started, state.cost_usd, director_info("llm", approved, notes))
    slots = edit["video"]
    total = sum(slot_duration(slot) for slot in slots)
    return base_summary("written", out_path, plan_path, len(rounds_log), approved, None, len(slots), total, 0, warnings, started, state.cost_usd, candidates, shortlist=len(shortlist)), 0


def _director_payload(context, brief_text, feedback_block, notebook_text):
    """The user payload of the director call: the brief, the shortlist facts, the notes and the notebook."""
    hints = context["hints"]
    return {
        "brief": {
            "text": brief_text,
            "target_duration_s": hints.get("target_duration_s"),
            "order": hints.get("order"),
            "pacing": hints.get("pacing"),
            "ambient_only": hints.get("ambient_only"),
        },
        "content_type": context["content_type"],
        "facts": [
            context["facts"][row["key"]]
            for row in context["shortlist"]
            if row.get("key") in context["facts"]
        ],
        "feedback": feedback_block or None,
        "notebook": notebook_text or None,
    }


def _director_plan_call(client, prompt, payload, state, facts, brief_obj, content_type):
    """One director call plus one repair retry that sends the exact validation errors."""
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    response = call_client(client, messages, state)
    plan, errors = _parse_director_plan(response.text, facts, brief_obj, content_type)
    if plan is not None:
        return plan, None
    messages.append({"role": "assistant", "content": response.text or ""})
    messages.append(
        {
            "role": "user",
            "content": "Your answer was not a valid plan: "
            + "; ".join(errors)
            + ". Answer again with ONE JSON object in the plan shape, no other text.",
        }
    )
    response = call_client(client, messages, state)
    plan, errors = _parse_director_plan(response.text, facts, brief_obj, content_type)
    if plan is not None:
        return plan, None
    return None, "; ".join(errors)


def _parse_director_plan(text, facts, brief_obj, content_type):
    """The plan from a model answer, with the envelope fields set and the shot keys checked."""
    data = parse_json(text)
    if data is None:
        return None, ["the answer is not a JSON object"]
    plan = dict(data)
    plan["schema_version"] = plan.get("schema_version") or SCHEMA_VERSION
    plan["generated_at"] = plan.get("generated_at") or datetime.now(timezone.utc).isoformat()
    plan["tool"] = TOOL
    plan["brief"] = brief_obj
    plan["content_type"] = content_type
    plan.pop("meta", None)
    errors = validate(plan, "director_plan")
    shots = plan.get("shots")
    unknown = [
        shot.get("shot_key")
        for shot in (shots if isinstance(shots, list) else [])
        if isinstance(shot, dict) and shot.get("shot_key") not in facts
    ]
    if unknown:
        errors.append("unknown shot_key: " + ", ".join(unknown))
    if errors:
        return None, errors
    return plan, []


def _write_director_plan(path, plan, started, cost_usd):
    """Write the plan with its meta block, validated against the director_plan schema."""
    plan["meta"] = {
        "tool": TOOL,
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": round(cost_usd, 8),
    }
    validate_or_raise(plan, "director_plan")
    atomic_write_json(path, plan)


def _run_editor(workspace_path, plan_path, out_path, report_path, beats_path, music=None, ambient_under_music_db=None, speed_ramp=False):
    """Run the code editor as a subprocess and return ``(edit, report)``."""
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "director_editor.py"),
        "--workspace",
        str(workspace_path),
        "--plan",
        str(plan_path),
        "--out",
        str(out_path),
        "--report",
        str(report_path),
        "--json",
    ]
    if beats_path is not None:
        command += ["--beats", str(beats_path)]
    if music is not None:
        command += ["--music", str(music)]
    if ambient_under_music_db is not None:
        command += ["--ambient-under-music-db", str(ambient_under_music_db)]
    if speed_ramp:
        command.append("--speed-ramp")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise LLMDirectorRuntimeError(f"the editor failed: {detail[-1] if detail else 'unknown error'}")
    return load_json(out_path), load_json(report_path)


def _make_pictures(edit, workspace_path, round_number, picture_fn, warnings):
    """One picture per slot of the cut, in timeline order, under director/rounds/round-N/."""
    picture_fn = picture_fn or extract_frame
    round_dir = workspace_path / "director" / "rounds" / f"round-{round_number}"
    round_dir.mkdir(parents=True, exist_ok=True)
    sources = {row.get("file"): row.get("path") for row in edit.get("sources") or []}
    pictures = []
    for slot in edit.get("video") or []:
        slot_id = slot.get("id")
        if slot.get("kind") == "photo":
            name = slot.get("photo")
            t_s = 0.0
        else:
            name = slot.get("clip")
            t_s = (float(slot.get("in_s") or 0.0) + float(slot.get("out_s") or 0.0)) / 2
        path = sources.get(name)
        if not path:
            warnings.append(f"no source path for {slot_id}; no picture")
            continue
        safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", str(slot_id))
        out = round_dir / f"{safe_id}.jpg"
        try:
            picture_fn(path, t_s, out, THUMBNAIL_WIDTH)
        except FramesError as exc:
            warnings.append(f"no picture for {slot_id}: {exc}")
            continue
        pictures.append({"id": slot_id, "shot_key": slot.get("shot_key"), "path": str(out)})
    return pictures


def _director_review_call(client, prompt, payload, report, pictures, state, facts, brief_obj, content_type):
    """One review call: the payload, the editor report and one picture per shot."""
    user_text = (
        json.dumps(payload, ensure_ascii=False)
        + "\n\nThe editor cut your plan. Its report:\n"
        + json.dumps(report, ensure_ascii=False)
        + "\n\nPictures of the cut, one per shot, in timeline order: "
        + ", ".join(f"{picture['id']} ({picture['shot_key']})" for picture in pictures)
        + ". Answer with a review: approve, or send notes with a complete revised plan."
    )
    content = [{"type": "text", "text": user_text}]
    for picture in pictures:
        uri = file_data_uri(picture["path"])
        if uri is not None:
            content.append({"type": "image_url", "image_url": {"url": uri}})
    messages = [
        {"role": "system", "content": prompt + "\n\n" + read_prompt("director_review.md")},
        {"role": "user", "content": content},
    ]
    try:
        response = call_client(client, messages, state)
    except (LLMError, BudgetExceeded) as exc:
        return None, str(exc)
    return _parse_director_review(response.text, facts, brief_obj, content_type)


def _parse_director_review(text, facts, brief_obj, content_type):
    """The review from a model answer; a not-approved review carries a complete revised plan."""
    data = parse_json(text)
    if data is None:
        return None, ["the answer is not a JSON object"]
    review = dict(data)
    review["schema_version"] = review.get("schema_version") or SCHEMA_VERSION
    review["generated_at"] = review.get("generated_at") or datetime.now(timezone.utc).isoformat()
    review["tool"] = TOOL
    errors = validate(review, "director_review")
    if review.get("approved") is False and isinstance(review.get("plan"), dict):
        plan = dict(review["plan"])
        plan["schema_version"] = plan.get("schema_version") or SCHEMA_VERSION
        plan["generated_at"] = plan.get("generated_at") or datetime.now(timezone.utc).isoformat()
        plan["tool"] = TOOL
        plan["brief"] = brief_obj
        plan["content_type"] = content_type
        plan.pop("meta", None)
        plan_errors = validate(plan, "director_plan")
        shots = plan.get("shots")
        unknown = [
            shot.get("shot_key")
            for shot in (shots if isinstance(shots, list) else [])
            if isinstance(shot, dict) and shot.get("shot_key") not in facts
        ]
        if unknown:
            plan_errors.append("plan unknown shot_key: " + ", ".join(unknown))
        if plan_errors:
            errors.extend("plan: " + error for error in plan_errors)
        else:
            review["plan"] = plan
    if errors:
        return None, errors
    return review, []


def _write_rounds(path, rounds, total_cost):
    """Write director_rounds.json: every round with its review and its cost."""
    atomic_write_json(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tool": TOOL,
            "rounds": rounds,
            "total_cost_usd": round(total_cost, 8),
        },
    )


def flow_decisions(plan, report, shotlog, facts):
    """The ``decisions[]`` rows of the film-crew flow: one used row per plan shot plus the editor's could-not-do items."""
    report_by_id = {row.get("id"): row for row in (report or {}).get("shots") or []}
    used_keys = []
    rows = []
    for shot in plan.get("shots") or []:
        key = shot.get("shot_key")
        used_keys.append(key)
        fact = facts.get(key) or {}
        entry = shotlog.get(key) or {}
        row = report_by_id.get(shot.get("id")) or {}
        reason = f"{shot.get('purpose')} — {shot.get('why')}"
        asked = row.get("technique_asked")
        done = row.get("technique_done")
        why_not = row.get("why_not")
        if done == asked and asked != "none":
            reason += f" (technique {asked} done)"
        elif done is not None and done != asked:
            reason += f" (technique {asked} done as {done})"
        elif done is None and why_not:
            reason += f" (technique {asked} not done: {why_not})"
        rows.append(
            {
                "id": key,
                "status": "used",
                "reason": reason,
                "slot": shot.get("id"),
                "score": entry.get("score"),
                "clip": entry.get("clip"),
                "window_in_s": fact.get("window_in_s"),
                "window_out_s": fact.get("window_out_s"),
            }
        )
    for item in (report or {}).get("could_not_do") or []:
        named = next((key for key in used_keys if key and key in item), None)
        rows.append(
            {
                "id": named or "editor",
                "status": "rejected",
                "reason": item,
                "slot": None,
                "score": None,
                "clip": None,
                "window_in_s": None,
                "window_out_s": None,
            }
        )
    return rows


def _resolve_workspace_brief(workspace, brief, brief_file):
    """Resolve the workspace dir, the brief text and the brief path the way the flow does."""
    workspace_path = Path(workspace).expanduser().resolve()
    if not workspace_path.is_dir():
        raise LLMDirectorError(f"workspace not found: {workspace_path}")
    if brief is None and brief_file is None:
        raise LLMDirectorError("provide --brief or --brief-file")
    if brief is not None and brief_file is not None:
        raise LLMDirectorError("give either --brief or --brief-file, not both")
    if brief_file is not None:
        brief_path = Path(brief_file).expanduser().resolve()
        if not brief_path.is_file():
            raise LLMDirectorError(f"brief file not found: {brief_path}")
        return workspace_path, brief_path.read_text(encoding="utf-8"), brief_path
    return workspace_path, brief, None


def _agent_context(workspace_path, shotlog, brief_text, important_max_s, target_duration_s, order, pacing):
    """The flow's context block: hints, content type, pacing, shot facts and sources."""
    hints = parse_brief(brief_text)
    facts_block_text = facts_block(load_brief_facts(workspace_path))
    prompt_brief = brief_text
    if facts_block_text:
        prompt_brief = brief_text.rstrip() + "\n\n" + facts_block_text
    all_good = target_duration_s is not None and target_duration_s <= 0
    if all_good:
        estimate = estimate_all_good(load_json(workspace_path / "shotlog.json"))
        target_duration_s = max(20.0, estimate["seconds"])
    if target_duration_s is not None:
        hints["target_duration_s"] = target_duration_s
    if order is not None:
        hints["order"] = order
    if pacing is not None:
        hints["pacing"] = pacing
    content_type = detect_content_type(prompt_brief, workspace_path)
    pacing_row = run_pacing(LONG_CUT_PACING_S if all_good else hints.get("target_duration_s"))
    effective_important_max_s = important_max_s if important_max_s is not None else pacing_row["hero_max"]
    drop_reasons = {}
    facts, clips, fact_warnings = build_shot_facts(
        workspace_path, shotlog, effective_important_max_s, drop_reasons,
        pacing_row["slot_min"], pacing_row["slot_max"],
    )
    sources = [
        {"file": clip["file"], "path": clip.get("path") or clip["file"], "duration_s": clip.get("duration_s")}
        for clip, _ in clips
    ]
    shotlog_rows = [shot for key, shot in shotlog.items() if key in facts]
    shortlist = shortlist_rows(shotlog_rows, pacing_row["n_slots"])
    brief_obj = {
        "text": brief_text,
        "target_duration_s": hints.get("target_duration_s"),
        "order": hints.get("order"),
        "pacing": hints.get("pacing"),
    }
    return {
        "hints": hints,
        "prompt_brief": prompt_brief,
        "content_type": content_type,
        "pacing": pacing_row,
        "effective_important_max_s": effective_important_max_s,
        "facts": facts,
        "clips": clips,
        "fact_warnings": fact_warnings,
        "sources": sources,
        "shotlog_rows": shotlog_rows,
        "shortlist": shortlist,
        "brief_obj": brief_obj,
        "drop_reasons": drop_reasons,
    }


def _agent_inputs_text(
    workspace_path,
    brief_text,
    context,
    payload,
    plan_path,
    out_path,
    beat_grid=None,
    music=None,
    ambient_under_music_db=None,
    speed_ramp=False,
    notebook_block=None,
    review=False,
    report=None,
    pictures=None,
):
    """The ``inputs.md`` the host agent reads: the director prompt, the payload and what to write."""
    hints = context["hints"]
    pacing = context["pacing"]
    parts = [read_prompt("agent_director.md").rstrip()]
    brief_lines = ["## The brief", "", brief_text.rstrip()]
    facts_block_text = facts_block(load_brief_facts(workspace_path))
    if facts_block_text:
        brief_lines += ["", facts_block_text]
    parts.append("\n".join(brief_lines))
    parts.append(
        "\n".join(
            [
                "## The brief settings",
                "",
                f"- content_type: {context['content_type']}",
                f"- target_duration_s: {hints.get('target_duration_s')}",
                f"- order: {hints.get('order')}",
                f"- pacing: {hints.get('pacing')}",
                f"- ambient_only: {hints.get('ambient_only')}",
                f"- slot_min: {pacing['slot_min']}",
                f"- slot_target: {pacing['slot_target']}",
                f"- slot_max: {pacing['slot_max']}",
                f"- hero_max: {pacing['hero_max']}",
                f"- n_slots: {pacing['n_slots']}",
            ]
        )
    )
    if beat_grid is not None:
        parts.append("## The beat grid\n\n```json\n" + json.dumps(beat_grid, indent=2) + "\n```")
    else:
        parts.append("## The beat grid\n\nnone given")
    parts.append("## The notebook\n\n" + (notebook_block if notebook_block else "none"))
    parts.append(
        "\n".join(
            [
                "## The music",
                "",
                f"- music: {music if music is not None else 'none'}",
                f"- ambient_under_music_db: {ambient_under_music_db}",
                f"- speed_ramp: {speed_ramp}",
            ]
        )
    )
    parts.append("## The director prompt\n\n" + read_prompt("director.md").rstrip())
    parts.append("## The payload\n\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```")
    if review:
        parts.append("## The editor report\n\n```json\n" + json.dumps(report, ensure_ascii=False) + "\n```")
        picture_lines = ["## The pictures of the cut", ""]
        for picture in pictures or []:
            picture_lines.append(f"- {picture['id']} ({picture['shot_key']}): {picture['path']}")
        parts.append("\n".join(picture_lines))
    apply_flags = []
    if beat_grid is not None:
        apply_flags += ["--beats", "<path>"]
    if music is not None:
        apply_flags += ["--music", "<path>"]
    if ambient_under_music_db is not None:
        apply_flags += ["--ambient-under-music-db", "<db>"]
    if speed_ramp:
        apply_flags.append("--speed-ramp")
    run_line = f'    yt llm_director --workspace {workspace_path} --brief "..." --apply'
    if apply_flags:
        run_line += " " + " ".join(apply_flags)
    if review:
        what_lines = [
            "## What to write",
            "",
            f"Write the review to `{workspace_path / REVIEW_FILE}` (schema `schemas/director_review.schema.json`): "
            "approve the cut, or answer with a complete revised plan and the notes.",
        ]
    else:
        what_lines = [
            "## What to write",
            "",
            f"Write the plan to `{plan_path}` (schema `schemas/director_plan.schema.json`).",
        ]
    what_lines += ["", "Then run:", "", run_line]
    parts.append("\n".join(what_lines))
    return "\n\n".join(parts) + "\n"


def prepare_agent_inputs(
    workspace,
    brief=None,
    brief_file=None,
    out=None,
    plan_out=None,
    important_max_s=None,
    target_duration_s=None,
    order=None,
    pacing=None,
    beats=None,
    music=None,
    ambient_under_music_db=None,
    speed_ramp=False,
    notebook=None,
    review=False,
    picture_fn=None,
):
    """Write WORKSPACE/director/inputs.md for the host agent; no model call."""
    started = time.monotonic()
    workspace_path, brief_text, brief_path = _resolve_workspace_brief(workspace, brief, brief_file)
    shotlog = load_shotlog(workspace_path)
    if shotlog is None:
        raise LLMDirectorError(f"missing shotlog.json in {workspace_path}")
    context = _agent_context(workspace_path, shotlog, brief_text, important_max_s, target_duration_s, order, pacing)
    out_path = Path(out).expanduser().resolve() if out else workspace_path / "edit.json"
    plan_path = Path(plan_out).expanduser().resolve() if plan_out else workspace_path / PLAN_FILE
    inputs_path = workspace_path / "director" / "inputs.md"
    inputs_path.parent.mkdir(parents=True, exist_ok=True)
    beats_path = Path(beats).expanduser().resolve() if beats else None
    beat_grid = load_beats(beats_path) if beats_path is not None else None
    if music:
        music_path = Path(music).expanduser().resolve()
        if not music_path.is_file():
            raise LLMDirectorError(f"music file not found: {music}")
        music = str(music_path)
    notebook_file = notebook_path(notebook)
    notebook_text = notebook_block(notebook_file) if notebook_file is not None else None
    payload = _director_payload(context, brief_text, None, notebook_text)
    warnings = list(context["fact_warnings"])
    report = None
    pictures = []
    if review:
        edit = load_json(out_path)
        if not isinstance(edit, dict) or not edit.get("video"):
            raise LLMDirectorError(f"missing edit.json for the review: {out_path}")
        report = load_json(workspace_path / REPORT_FILE)
        if not isinstance(report, dict):
            raise LLMDirectorError(f"missing editor report for the review: {workspace_path / REPORT_FILE}")
        rounds = (load_json(workspace_path / ROUNDS_FILE) or {}).get("rounds") or []
        round_number = len(rounds) or 1
        pictures = _make_pictures(edit, workspace_path, round_number, picture_fn, warnings)
    inputs_path.write_text(
        _agent_inputs_text(
            workspace_path,
            brief_text,
            context,
            payload,
            plan_path,
            out_path,
            beat_grid=beat_grid,
            music=music,
            ambient_under_music_db=ambient_under_music_db,
            speed_ramp=speed_ramp,
            notebook_block=notebook_text,
            review=review,
            report=report,
            pictures=pictures,
        ),
        encoding="utf-8",
    )
    summary = {
        "status": "prepared",
        "inputs": str(inputs_path),
        "candidates": len(context["shotlog_rows"]),
        "shortlist": len(context["shortlist"]),
        "review": bool(review),
        "warnings": warnings,
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": 0.0,
    }
    return summary, EXIT_OK


def apply_agent_answer(
    workspace,
    brief=None,
    brief_file=None,
    out=None,
    plan_out=None,
    important_max_s=None,
    target_duration_s=None,
    order=None,
    pacing=None,
    beats=None,
    music=None,
    ambient_under_music_db=None,
    speed_ramp=False,
    force=False,
):
    """Validate the host agent's plan or review, run the editor and write edit.json."""
    started = time.monotonic()
    workspace_path, brief_text, brief_path = _resolve_workspace_brief(workspace, brief, brief_file)
    shotlog = load_shotlog(workspace_path)
    if shotlog is None:
        raise LLMDirectorError(f"missing shotlog.json in {workspace_path}")
    context = _agent_context(workspace_path, shotlog, brief_text, important_max_s, target_duration_s, order, pacing)
    out_path = Path(out).expanduser().resolve() if out else workspace_path / "edit.json"
    plan_path = Path(plan_out).expanduser().resolve() if plan_out else workspace_path / PLAN_FILE
    beats_path = Path(beats).expanduser().resolve() if beats else None
    if music:
        music_path = Path(music).expanduser().resolve()
        if not music_path.is_file():
            raise LLMDirectorError(f"music file not found: {music}")
        music = str(music_path)
    warnings = list(context["fact_warnings"])
    approved = None
    notes = []
    rounds_log = []
    plan = None
    report = None
    review_path = workspace_path / REVIEW_FILE
    if review_path.is_file():
        review, review_errors = _parse_director_review(
            review_path.read_text(encoding="utf-8"),
            context["facts"],
            context["brief_obj"],
            context["content_type"],
        )
        if review is None:
            raise LLMDirectorError("the review is not valid: " + "; ".join(review_errors))
        rounds_log = (load_json(workspace_path / ROUNDS_FILE) or {}).get("rounds") or []
        if review["approved"]:
            approved = True
            if rounds_log:
                rounds_log[-1]["review"] = {"approved": True, "summary": review["summary"], "notes": []}
            _write_rounds(workspace_path / ROUNDS_FILE, rounds_log, 0.0)
        elif len(rounds_log) >= FLOW_MAX_ROUNDS:
            approved = False
            notes = list(review.get("notes") or [])
        else:
            _write_director_plan(plan_path, review["plan"], started, 0.0)
            edit, report = _run_editor(
                workspace_path, plan_path, out_path, workspace_path / REPORT_FILE, beats_path,
                music=music, ambient_under_music_db=ambient_under_music_db, speed_ramp=speed_ramp,
            )
            rounds_log.append(
                {
                    "round": len(rounds_log) + 1,
                    "plan": review["plan"],
                    "report": report,
                    "review": None,
                    "cost_usd": 0.0,
                }
            )
            _write_rounds(workspace_path / ROUNDS_FILE, rounds_log, 0.0)
        plan = load_json(plan_path)
        if report is None:
            report = load_json(workspace_path / REPORT_FILE)
        os.replace(review_path, workspace_path / REVIEW_APPLIED_FILE)
    else:
        raw_plan = load_json(plan_path)
        if not isinstance(raw_plan, dict):
            raise LLMDirectorError(f"missing agent plan: {plan_path}")
        plan, plan_errors = _parse_director_plan(
            json.dumps(raw_plan),
            context["facts"],
            context["brief_obj"],
            context["content_type"],
        )
        if plan is None:
            raise LLMDirectorError("the plan is not valid: " + "; ".join(plan_errors))
        existing = load_json(out_path)
        if (
            not force
            and isinstance(existing, dict)
            and existing.get("video")
            and (workspace_path / ROUNDS_FILE).is_file()
        ):
            slots = existing.get("video") or []
            total = sum(slot_duration(slot) for slot in slots)
            summary = {
                "status": "skipped",
                "out": str(out_path),
                "plan": str(plan_path),
                "slots": len(slots),
                "total_duration_s": round(total, 2),
                "validator_violations": 0,
                "shortlist": len(context["shortlist"]),
                "warnings": [],
                "wall_time_s": round(time.monotonic() - started, 2),
                "llm_cost_usd": 0.0,
            }
            return summary, EXIT_OK
        _write_director_plan(plan_path, plan, started, 0.0)
        edit, report = _run_editor(
            workspace_path, plan_path, out_path, workspace_path / REPORT_FILE, beats_path,
            music=music, ambient_under_music_db=ambient_under_music_db, speed_ramp=speed_ramp,
        )
        rounds_log = [
            {
                "round": 1,
                "plan": plan,
                "report": report,
                "review": None,
                "cost_usd": 0.0,
            }
        ]
        _write_rounds(workspace_path / ROUNDS_FILE, rounds_log, 0.0)
    edit = load_json(out_path)
    if not isinstance(edit, dict) or not edit.get("video"):
        raise LLMDirectorRuntimeError("the editor wrote no usable edit")
    if isinstance(plan, dict):
        edit["decisions"] = flow_decisions(plan, report, shotlog, context["facts"])
    edit.setdefault("meta", {})["speed_ramp"] = bool(speed_ramp)
    write_edit(out_path, edit, started, 0.0, director_info("agent", approved, notes))
    slots = edit["video"]
    total = sum(slot_duration(slot) for slot in slots)
    summary = {
        "status": "written",
        "out": str(out_path),
        "plan": str(plan_path),
        "slots": len(slots),
        "total_duration_s": round(total, 2),
        "validator_violations": 0,
        "rounds_used": len(rounds_log),
        "shortlist": len(context["shortlist"]),
        "warnings": warnings,
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": 0.0,
    }
    return summary, EXIT_OK


def _check_agent_mode_conflict(args):
    """Reject more than one of --prepare, --apply, --chat and --dry-run."""
    modes = []
    if args.prepare:
        modes.append("--prepare")
    if args.apply:
        modes.append("--apply")
    if args.chat is not None:
        modes.append("--chat")
    if args.dry_run:
        modes.append("--dry-run")
    if len(modes) > 1:
        raise LLMDirectorError("give only one of " + ", ".join(modes))
    if args.prepare and args.force:
        raise LLMDirectorError("--prepare does not accept --force")
    if args.review and not args.prepare:
        raise LLMDirectorError("--review needs --prepare")


def print_human(summary, record):
    if record is not None:
        for slot in record.get("video") or []:
            if slot.get("kind") == "photo":
                print(
                    f"{slot['id']}  {slot['photo']}  photo  {slot['duration_s']:.2f}s  "
                    f"{slot['score']:.2f}  {slot['reason']}"
                )
            else:
                print(
                    f"{slot['id']}  {slot['clip']}  {slot['in_s']:.2f}-{slot['out_s']:.2f}  "
                    f"{slot['score']:.2f}  {slot['reason']}"
                )
    baseline = summary.get("baseline")
    if baseline:
        print(f"baseline (rules): {baseline['slots']} slots, {baseline['total_duration_s']:.1f}s")
        for reason in baseline["reasons"]:
            print(f"  {reason}")
    print(f"total {summary['total_duration_s']:.1f}s")
    for warning in summary["warnings"]:
        print(f"warning: {warning}")


def print_prepare(summary):
    print(f"prepared {summary['candidates']} candidates")
    print(f"inputs: {summary['inputs']}")
    for warning in summary["warnings"]:
        print(f"warning: {warning}")


def print_apply(summary):
    print(f"{summary['slots']} slots, {summary['total_duration_s']:.1f}s")
    for warning in summary["warnings"]:
        print(f"warning: {warning}")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        _check_agent_mode_conflict(args)
        if args.prepare:
            summary, code = prepare_agent_inputs(
                args.workspace,
                brief=args.brief,
                brief_file=args.brief_file,
                out=args.out,
                plan_out=args.plan_out,
                important_max_s=args.important_max_s,
                target_duration_s=args.target_duration_s,
                order=args.order,
                pacing=args.pacing,
                beats=args.beats,
                music=args.music,
                ambient_under_music_db=args.ambient_under_music_db,
                speed_ramp=args.speed_ramp,
                notebook=args.notebook,
                review=args.review,
                picture_fn=None,
            )
        elif args.apply:
            summary, code = apply_agent_answer(
                args.workspace,
                brief=args.brief,
                brief_file=args.brief_file,
                out=args.out,
                plan_out=args.plan_out,
                important_max_s=args.important_max_s,
                target_duration_s=args.target_duration_s,
                order=args.order,
                pacing=args.pacing,
                beats=args.beats,
                music=args.music,
                ambient_under_music_db=args.ambient_under_music_db,
                speed_ramp=args.speed_ramp,
                force=args.force,
            )
        elif args.chat is not None:
            summary, code = director_chat(
                args.workspace,
                args.chat,
                version=args.version,
                model=args.model,
                budget_usd=args.budget_usd,
                reasoning_effort=args.reasoning_effort,
            )
        else:
            summary, code = run(
                args.workspace,
                brief=args.brief,
                brief_file=args.brief_file,
                out=args.out,
                plan_out=args.plan_out,
                rounds=args.rounds,
                dry_run=args.dry_run,
                fallback_rules=args.fallback_rules,
                model=args.model,
                budget_usd=args.budget_usd,
                force=args.force,
                important_max_s=args.important_max_s,
                target_duration_s=args.target_duration_s,
                order=args.order,
                pacing=args.pacing,
                beats=args.beats,
                music=args.music,
                ambient_under_music_db=args.ambient_under_music_db,
                feedback=args.feedback,
                speed_ramp=args.speed_ramp,
                notebook=args.notebook,
                reasoning_effort=args.reasoning_effort,
            )
    except (LLMDirectorRuntimeError, LLMError, OSError) as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except (LLMDirectorError, FactsError, BeatsError, ConfigError, SchemaError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)
    if args.json:
        print(json.dumps(summary))
    elif args.prepare:
        print_prepare(summary)
    elif args.apply:
        print_apply(summary)
    elif args.chat is not None:
        print(summary["reply"])
    else:
        record = load_json(summary["out"]) if summary.get("out") else None
        print_human(summary, record)
    if code != EXIT_OK:
        detail = summary["warnings"][-1] if summary["warnings"] else "run failed"
        print(f"[error] {detail}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
