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
import base64
import copy
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common.config import ConfigError, director_reasoning_effort, load_config, notebook_path
from common.brief_facts import facts_block, load_brief_facts
from common.decisions import build_decisions
from common.director import director_review
from common.feedback import append_feedback, read_feedback
from common.intent import is_whip_short_hold
from common.notebook import notebook_block
from common.pacing import estimate_all_good
from common.peak import centre_window, choose_peak, is_centred
from common.embed import EmbedError
from common.frames import FramesError, extract_frame
from common.llm import BudgetExceeded, LLMError, make_client, reasoning_extra_body
from common import runlog
from common.constants import MIN_SHOT_LENGTH_S, SNAP_MIN_SLOT_S
from common.facts import (
    CLIP_COUNTER,
    DEFAULT_IMPORTANT_MAX_S,
    IMPORTANT_MOTION_LABELS,
    IMPORTANT_SEMANTIC_MOTIONS,
    LANDING_GAP_S,
    MAX_SLOT_S,
    NON_MOVE_MOTION_LABELS,
    PHOTO_DEFAULT_S,
    SKIP_ROLES,
    TOLERANCE_S,
    FactsError,
    _phrase_move,
    beats_by_segment_from_shotlog,
    best_overlapping_move,
    build_candidates,
    build_phrases,
    build_shot_facts,
    candidate_key,
    clamp,
    clip_counter,
    clip_position,
    dedupe_candidates,
    default_target_s,
    expected_cut,
    fact_peak,
    first_not_none,
    is_important,
    keeps_whole,
    landing_floor,
    landing_pairs,
    load_ingest,
    load_ingest_photos,
    load_shotlog,
    make_fact,
    make_photo_fact,
    make_phrase_candidate,
    order_clips,
    parse_time,
    segment_subranges,
)
from common.beatsnap import (
    BEAT_SNAP_FRACTION,
    LANDING_FLOOR_S,
    PHOTO_MAX_S,
    PHOTO_MIN_S,
    BeatsError,
    _cap_target,
    _relaxed,
    absorb_head,
    absorb_length,
    absorb_out,
    absorb_pair,
    derive_beat_period,
    load_beats,
    nearest_beat,
    over_cap,
    slot_bounds,
    snap_photo,
    snap_slots,
    snap_warning,
    source_delta,
)
from common.moves import (
    EXCEPTION_SHARE,
    SAME_TAKE_MIN_GAP_S,
    STAR_MIN_SCORE,
    STAR_SHARE,
    apply_move_policy,
    is_jump_cut,
    is_moving,
    is_star,
    middle_speed_of,
    screen_factor,
    slot_duration,
    slot_screen_length,
)
from common.overlap import overlap_seconds
from common.neighbours import drop_pointless_neighbours, owner_asked_keys, same_segment_beats
from common.photos import ken_burns_for
from common.schema import SchemaError, validate, validate_or_raise
from common.window_similarity import WindowEmbedder
from common.exit_codes import EXIT_OK, EXIT_USAGE, EXIT_RUNTIME
from common.errors import report_error

TOOL = "scripts/llm_director.py"
SCHEMA_VERSION = "0.1"
REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = REPO_ROOT / "prompts"
DIRECTOR_SCRIPT = REPO_ROOT / "scripts" / "director.py"

MAX_ROUNDS = 3
DEFAULT_ROUNDS = 2
FLOW_MAX_ROUNDS = 2
PICTURE_WIDTH = 320
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
XFADE_S = 0.3
PHOTO_TOLERANCE_S = 0.01
CENTRE_TOLERANCE_S = 0.3

MOVE_ROLES = {"move", "transition_in", "transition_out"}
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


class LoopFailure(RuntimeError):
    """Raised when the three-role loop cannot produce a valid edit."""


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
            "  python scripts/llm_director.py --workspace <workspace> --interpret-feedback \"drop 0071/s2, tunnel longer\" --json\n"
            "  python scripts/llm_director.py --workspace <workspace> --version v3 --interpret-feedback \"make the tunnel longer\"\n"
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
    parser.add_argument("--fallback-rules", dest="fallback_rules", action="store_true", default=True, help="fall back to the rule-based director when the loop fails (default)")
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
        help="read the previous edit context from versions/vNNN/edit.json (with --interpret-feedback or --chat)",
    )
    parser.add_argument(
        "--interpret-feedback",
        default=None,
        metavar="TEXT",
        help=(
            "say in the user's language how the director reads this feedback request, "
            "with a cost and time estimate; one cheap model call, no edit written"
        ),
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
    """The screenwriter's menu: every star, the top-K by score and every clip's best eligible segment, in shotlog order."""
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


def plan_pacing(plan):
    """The pacing row the plan's brief implies; the 90 s row when the plan has no target."""
    brief = plan.get("brief") if isinstance(plan, dict) else None
    target = brief.get("target_duration_s") if isinstance(brief, dict) else None
    return run_pacing(target)


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


def read_role_prompt(name):
    """Return the shared playbook followed by the role prompt."""
    return read_prompt("playbook.md").rstrip() + "\n\n" + read_prompt(name)


def read_chat_prompt():
    """Return the capability list followed by the interpret-feedback prompt."""
    return read_prompt("tools.md").rstrip() + "\n\n" + read_prompt("interpret_feedback.md")


def call_client(client, messages, state, role="director"):
    extra = reasoning_extra_body(state.reasoning_effort)
    if extra is not None:
        response = client.complete(role, messages, extra_body=extra)
    else:
        response = client.complete(role, messages)
    state.calls += 1
    state.cost_usd = round(state.cost_usd + float(response.cost_usd), 8)
    return response


def complete_plan(answer, brief_text, hints, content_type):
    plan = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": TOOL,
        "brief": {
            "text": brief_text,
            "target_duration_s": hints.get("target_duration_s"),
            "order": hints.get("order"),
            "pacing": hints.get("pacing"),
            "ambient_only": hints.get("ambient_only"),
        },
        "content_type": content_type,
        "structure": answer.get("structure") or [],
        "slot_plan": answer.get("slot_plan") or [],
        "meta": {"tool": TOOL, "wall_time_s": 0.0, "llm_cost_usd": 0.0},
    }
    logline = answer.get("logline")
    if isinstance(logline, str) and logline.strip():
        plan["logline"] = logline
    notes = answer.get("notes")
    if isinstance(notes, list) and notes:
        plan["notes"] = notes
    return plan


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


def build_interpret_user(text, workspace, edit_path=None):
    """The user message for the interpret call: the feedback plus the previous edit context."""
    lines = [build_feedback_block([text], workspace, edit_path)]
    path = Path(edit_path) if edit_path is not None else Path(workspace) / "edit.json"
    review = director_review(load_json(path))
    if review is not None:
        lines += ["", "The reviewer's last verdict on this edit (plain words):",
                  json.dumps(review, ensure_ascii=False)]
    return "\n".join(lines)


def normalize_key_list(value):
    """A list of non-empty shot keys from a model answer field; ``[]`` for anything else."""
    if not isinstance(value, list):
        return []
    keys = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in keys:
            keys.append(item.strip())
    return keys


def request_reply(client, prompt, user, state):
    """One cheap model call returning the parsed answer, retried once, then a runtime error."""
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user},
    ]
    for _ in range(2):
        response = call_client(client, messages, state, role="metadata")
        answer = parse_json(response.text)
        if not isinstance(answer, dict):
            continue
        reply = answer.get("reply")
        if not isinstance(reply, str) or not reply.strip():
            continue
        if answer.get("can_do") not in ("all", "part", "none"):
            continue
        return {
            "reply": reply.strip(),
            "can_do": answer["can_do"],
            "missing_tool": answer.get("missing_tool"),
            "add_keys": normalize_key_list(answer.get("add_keys")),
            "drop_keys": normalize_key_list(answer.get("drop_keys")),
        }
    raise LLMDirectorRuntimeError("model produced no usable reply after one retry")


def append_reply_line(workspace, text, reply, estimate_usd, estimate_s, can_do="all", missing_tool=None, add_keys=None, drop_keys=None):
    """Append one ``action: reply`` line to WORKSPACE/feedback.jsonl (append-only)."""
    append_feedback(
        workspace,
        "reply",
        text=text,
        reply=reply,
        estimate={"usd": estimate_usd, "s": estimate_s},
        can_do=can_do,
        missing_tool=missing_tool,
        add_keys=list(add_keys or []),
        drop_keys=list(drop_keys or []),
        tool=TOOL,
    )


def owner_keys_from_feedback(workspace, feedback):
    """The owner's add/drop keys from the last reply row whose text prefixes the feedback.

    The approve form posts ``<text>\\n\\nDirector's reading: <reply>``, so the
    reply row's ``text`` is a prefix of the feedback text. Returns
    ``(add_keys, drop_keys)``, both ``[]`` when no row matches.
    """
    texts = [text for text in (feedback or []) if isinstance(text, str) and text.strip()]
    if not texts:
        return [], []
    for row in reversed(read_feedback(workspace)):
        if row.get("action") != "reply":
            continue
        row_text = row.get("text")
        if not isinstance(row_text, str) or not row_text.strip():
            continue
        if not any(text.startswith(row_text) for text in texts):
            continue
        return normalize_key_list(row.get("add_keys")), normalize_key_list(row.get("drop_keys"))
    return [], []



def interpret_feedback(workspace, text, version=None, model=None, budget_usd=None, config=None, client=None, reasoning_effort=None):
    """One cheap model call that says how the director reads a feedback request."""
    started = time.monotonic()
    workspace_path = Path(workspace).expanduser().resolve()
    if not workspace_path.is_dir():
        raise LLMDirectorError(f"workspace not found: {workspace_path}")
    if not text or not text.strip():
        raise LLMDirectorError("provide --interpret-feedback TEXT")
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
            models["metadata"] = model
        effective["models"] = models
        if budget_usd is not None:
            effective["llm_budget_usd"] = budget_usd
        client = make_client(config=effective, ledger_path=workspace_path / "llm_spend.json")
    prompt = read_chat_prompt()
    state = LoopState()
    state.reasoning_effort = director_reasoning_effort(reasoning_effort, config)
    answer = request_reply(client, prompt, build_interpret_user(text, workspace_path, edit_path), state)
    can_do = answer["can_do"]
    missing_tool = answer.get("missing_tool")
    if can_do != "all" and isinstance(missing_tool, str) and missing_tool.strip():
        missing_tool = missing_tool.strip()
    else:
        missing_tool = None
    estimate_usd, estimate_s = last_director_estimate(workspace_path)
    append_reply_line(
        workspace_path, text, answer["reply"], estimate_usd, estimate_s,
        can_do=can_do, missing_tool=missing_tool,
        add_keys=answer["add_keys"], drop_keys=answer["drop_keys"],
    )
    return {
        "reply": answer["reply"],
        "can_do": can_do,
        "missing_tool": missing_tool,
        "add_keys": answer["add_keys"],
        "drop_keys": answer["drop_keys"],
        "estimate_usd": estimate_usd,
        "estimate_s": estimate_s,
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": round(state.cost_usd, 8),
    }, EXIT_OK


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


def build_screenwriter_user(brief_text, content_type, hints, rows, important_max_s, feedback_block=None, notebook_block=None):
    lines = [
        f"Brief: {brief_text}",
        f"Content type: {content_type}",
        f"Target duration: {hints.get('target_duration_s')} s",
        f"Order: {hints.get('order')}",
        f"Pacing: {hints.get('pacing')}",
        f"Hero cap (important_max_s): {important_max_s} s",
    ]
    if feedback_block:
        lines += ["", feedback_block]
    if notebook_block:
        lines += ["", notebook_block]
    lines += [
        "",
        "Eligible shotlog rows:",
        json.dumps(rows),
    ]
    return "\n".join(lines)


def editor_facts(plan, facts):
    result = []
    for slot in plan.get("slot_plan") or []:
        fact = facts.get(slot.get("shot_key"))
        if fact is None:
            continue
        merged = dict(fact)
        merged["keep_whole"] = bool(fact.get("keep_whole"))
        merged["target_s"] = slot.get("target_s", fact.get("target_s"))
        for key in ("faces_max", "face_size_max", "description", "reason"):
            merged.pop(key, None)
        result.append(merged)
    return result


def build_editor_user(plan, facts, review, pacing=None, feedback_block=None, notebook_block=None):
    lines = []
    if pacing:
        lines.append(
            "Pacing for this edit: "
            f"hero cap {pacing.get('hero_max')} s, ordinary slots "
            f"{pacing.get('slot_min')}-{pacing.get('slot_max')} s."
        )
        lines.append("")
    if feedback_block:
        lines += [feedback_block, ""]
    if notebook_block:
        lines += [notebook_block, ""]
    lines += [
        "Plan:",
        json.dumps(
            {
                "structure": plan.get("structure"),
                "slot_plan": plan.get("slot_plan"),
                "notes": plan.get("notes"),
            },
        ),
        "",
        "Shot facts:",
        json.dumps(editor_facts(plan, facts)),
    ]
    if review:
        lines += ["", "Review fixes from the previous round:", json.dumps(review)]
    return "\n".join(lines)


def reviewer_slots(record):
    """The reviewer's view of the edit slots: no ``description`` and no ``reason``."""
    slots = []
    for slot in record.get("video") or []:
        row = dict(slot)
        row.pop("description", None)
        row.pop("reason", None)
        slots.append(row)
    return slots


def build_reviewer_user(brief_text, plan, record, violations, pacing=None, feedback_block=None, notebook_block=None):
    lines = []
    if pacing:
        lines.append(
            "Pacing for this edit: "
            f"hero cap {pacing.get('hero_max')} s, ordinary slots "
            f"{pacing.get('slot_min')}-{pacing.get('slot_max')} s."
        )
        lines.append("")
    if feedback_block:
        lines += [feedback_block, ""]
    if notebook_block:
        lines += [notebook_block, ""]
    lines += [
        f"Brief: {brief_text}",
        "",
        "Plan:",
        json.dumps(
            {
                "structure": plan.get("structure"),
                "slot_plan": plan.get("slot_plan"),
                "notes": plan.get("notes"),
            },
        ),
        "",
        "Draft edit slots:",
        json.dumps(reviewer_slots(record)),
        "",
        "Validator violations:",
        json.dumps(violations),
    ]
    return "\n".join(lines)


def screenwriter(client, prompt, brief_text, content_type, hints, rows, state, important_max_s, feedback_block=None, notebook_block=None):
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": build_screenwriter_user(brief_text, content_type, hints, rows, important_max_s, feedback_block, notebook_block),
        },
    ]
    for _ in range(2):
        response = call_client(client, messages, state)
        answer = parse_json(response.text)
        if answer is None:
            continue
        plan = complete_plan(answer, brief_text, hints, content_type)
        if not validate(plan, "shot_plan"):
            return plan
    return None


def editor_round(client, prompt, plan, facts, review, state, build_record_fn, feedback_block=None, notebook_block=None):
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": build_editor_user(plan, facts, review, plan_pacing(plan), feedback_block, notebook_block)},
    ]
    for _ in range(2):
        response = call_client(client, messages, state)
        answer = parse_json(response.text)
        if answer is None:
            continue
        record = build_record_fn(answer)
        if record is None:
            continue
        if validate(record, "edit"):
            continue
        return record
    return None


def reviewer(client, prompt, brief_text, plan, record, violations, state, feedback_block=None, notebook_block=None):
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": build_reviewer_user(
                brief_text, plan, record, violations, plan_pacing(plan), feedback_block, notebook_block
            ),
        },
    ]
    for _ in range(2):
        response = call_client(client, messages, state)
        verdict = parse_json(response.text)
        if verdict is not None and isinstance(verdict.get("approved"), bool):
            return verdict
    return None


def violation(slot_id, rule, message):
    return {"slot": slot_id, "rule": rule, "message": message}


def shots_similar(first_key, first_links, second_key, second_links):
    if first_key is None or second_key is None:
        return False
    return second_key in (first_links or []) or first_key in (second_links or [])


def make_window_similar(embedder):
    """The neighbour check: window similarity for video slots, whole-segment for photos.

    Two video slots are compared on the frames inside their ``in_s..out_s``; a
    photo slot (no window) falls back to the whole-segment ``similar_to`` check.
    """

    def window_similar(first_slot, second_slot, first_fact, second_fact):
        if (
            first_slot.get("in_s") is not None
            and first_slot.get("out_s") is not None
            and second_slot.get("in_s") is not None
            and second_slot.get("out_s") is not None
        ):
            try:
                return embedder.similar(first_slot, second_slot)
            except EmbedError:
                return shots_similar(
                    first_fact.get("shot_key"),
                    first_fact.get("similar_to"),
                    second_fact.get("shot_key"),
                    second_fact.get("similar_to"),
                )
        return shots_similar(
            first_fact.get("shot_key"),
            first_fact.get("similar_to"),
            second_fact.get("shot_key"),
            second_fact.get("similar_to"),
        )

    return window_similar


def slot_fact(slot, plan_by_id, facts_by_key):
    plan_slot = plan_by_id.get(slot["id"])
    if plan_slot is None:
        return None
    return facts_by_key.get(plan_slot.get("shot_key"))


def is_trimmed_cut(in_s, out_s, fact):
    """True when the cut is shorter than the shot's window."""
    return (out_s - in_s) < (float(fact["window_out_s"]) - float(fact["window_in_s"])) - TOLERANCE_S


def stars_share_one_group(first_fact, second_fact):
    """True when two stars count once: beats of one segment or a ``similar_to`` pair."""
    if same_segment_beats(first_fact, second_fact):
        return True
    return shots_similar(
        first_fact.get("shot_key"),
        first_fact.get("similar_to"),
        second_fact.get("shot_key"),
        second_fact.get("similar_to"),
    )


def star_groups(stars):
    """Group ``stars`` (best-first) into transitive groups; return a list of key lists.

    Two stars are in one group when they are beats of one segment (same clip and
    ``segment_id``) or a ``similar_to`` pair; groups are transitive, so a chain of
    pairs is one group. Each group keeps the input order, so its first key is the
    best score (then the smallest key).
    """
    parent = {key: key for key, _ in stars}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for index, (first_key, first_fact) in enumerate(stars):
        for second_key, second_fact in stars[index + 1:]:
            if not stars_share_one_group(first_fact, second_fact):
                continue
            first_root, second_root = root(first_key), root(second_key)
            if first_root != second_root:
                parent[first_root] = second_root
    groups = {}
    order = []
    for key, _ in stars:
        group = root(key)
        if group not in groups:
            groups[group] = []
            order.append(group)
        groups[group].append(key)
    return [groups[group] for group in order]


def sorted_stars(facts_by_key):
    """Every star fact (never a photo), best score first, then by key."""
    stars = [
        (key, fact)
        for key, fact in facts_by_key.items()
        if fact.get("media_type") != "photo" and is_star(fact)
    ]
    stars.sort(key=lambda item: (-float(item[1].get("score") or 0.0), item[0]))
    return stars


def star_group_keys(facts_by_key, drop_keys=frozenset()):
    """The keys in any star group that contains one of the owner's drop keys.

    A group the owner dropped is not required at all, and no repair may put any
    of its members back.
    """
    dropped = set()
    for group in star_groups(sorted_stars(facts_by_key)):
        if any(key in drop_keys for key in group):
            dropped.update(group)
    return dropped


def required_star_keys(facts_by_key, n_slots, drop_keys=frozenset()):
    """The top stars an edit must keep: heroes or scores >= STAR_MIN_SCORE, best first.

    At most ``STAR_SHARE`` of the slots' worth of stars, at least one. Photos
    keep their own non-blocking ``hero`` rule and are never required here. Stars
    count once per group: two stars are one group when they are beats of one
    segment (same clip and ``segment_id``) or a ``similar_to`` pair (groups are
    transitive), and only the best of each group is required, so the pointless-cut
    rule can never drop a required star. A group that contains any of the owner's
    drop keys is not required at all, so an owner removal sticks through every
    repair.
    """
    kept = []
    for group in star_groups(sorted_stars(facts_by_key)):
        if any(key in drop_keys for key in group):
            continue
        kept.append(group[0])
    budget = max(1, round(STAR_SHARE * max(int(n_slots), 1)))
    return kept[:budget]


def validate_edit(record, plan, facts_by_key, important_max_s=DEFAULT_IMPORTANT_MAX_S, drop_keys=frozenset(), similar_fn=None):
    pacing = plan_pacing(plan)
    slot_min_s = pacing["slot_min"]
    slot_max_s = pacing["slot_max"]
    violations = []
    long_moves = []
    plan_by_id = {slot["id"]: slot for slot in plan.get("slot_plan") or []}
    slots = record.get("video") or []
    video_ids = {slot.get("id") for slot in slots}
    used_keys = {slot.get("shot_key") for slot in plan.get("slot_plan") or []}
    for plan_slot in plan.get("slot_plan") or []:
        if plan_slot["id"] not in video_ids:
            violations.append(
                violation(
                    plan_slot["id"],
                    "missing",
                    "planned but the editor produced no cut window",
                )
            )
    for key, fact in facts_by_key.items():
        if fact.get("media_type") != "photo" or not fact.get("hero"):
            continue
        if key not in used_keys:
            violations.append(violation(key, "hero", "eligible hero photo is missing from the plan"))
    star_budget_source = len(slots) or len(plan.get("slot_plan") or [])
    for key in required_star_keys(facts_by_key, star_budget_source, drop_keys):
        if key in used_keys:
            continue
        fact = facts_by_key[key]
        score = fact.get("score")
        violations.append(
            violation(
                key,
                "hero_missing",
                f"top star {key} (score {score}) is left out of the plan",
            )
        )
    for slot in slots:
        fact = slot_fact(slot, plan_by_id, facts_by_key)
        if fact is None:
            violations.append(violation(slot["id"], "plan", "slot is not in the plan or names an unknown shot"))
            continue
        if fact.get("media_type") == "photo":
            duration = slot.get("duration_s")
            if (
                not isinstance(duration, (int, float))
                or isinstance(duration, bool)
                or duration < PHOTO_MIN_S - PHOTO_TOLERANCE_S
                or duration > PHOTO_MAX_S + PHOTO_TOLERANCE_S
            ):
                violations.append(
                    violation(
                        slot["id"],
                        "photo_length",
                        f"{duration}s is outside {PHOTO_MIN_S:.1f}-{PHOTO_MAX_S:.1f}s",
                    )
                )
            continue
        in_s = slot["in_s"]
        out_s = slot["out_s"]
        if (
            in_s < fact["window_in_s"] - TOLERANCE_S
            or out_s > fact["window_out_s"] + TOLERANCE_S
            or out_s <= in_s
        ):
            violations.append(
                violation(
                    slot["id"],
                    "window",
                    f"{in_s:.2f}-{out_s:.2f} is outside the shot window "
                    f"{fact['window_in_s']:.2f}-{fact['window_out_s']:.2f}",
                )
            )
        if fact.get("landing_at_s") is not None and out_s < fact["landing_at_s"] - TOLERANCE_S:
            violations.append(
                violation(
                    slot["id"],
                    "landing",
                    f"ends at {out_s:.2f} before the landing at {fact['landing_at_s']:.2f}",
                )
            )
        if fact.get("landing_hold_key") and fact["landing_hold_key"] in used_keys:
            violations.append(
                violation(
                    slot["id"],
                    "landing",
                    f"the landing hold {fact['landing_hold_key']} is planned again as its own slot",
                )
            )
        duration = slot_duration(slot)
        if is_trimmed_cut(in_s, out_s, fact):
            expected_in, _ = expected_cut(fact, out_s - in_s)
            if abs(in_s - expected_in) > CENTRE_TOLERANCE_S:
                violations.append(
                    violation(
                        slot["id"],
                        "peak",
                        f"{in_s:.2f}-{out_s:.2f} is not centred on the best moment at "
                        f"{fact_peak(fact):.2f}s: expected it to start at {expected_in:.2f}",
                    )
                )
        is_move = fact.get("role") in MOVE_ROLES and fact.get("motion_label") not in NON_MOVE_MOTION_LABELS
        if duration < slot_min_s - TOLERANCE_S or duration > slot_max_s + TOLERANCE_S:
            if is_move and slot_max_s < duration <= max(important_max_s, slot_max_s) + TOLERANCE_S:
                long_moves.append(slot["id"])
            else:
                violations.append(
                    violation(
                        slot["id"],
                        "length",
                        f"{duration:.2f}s is outside {slot_min_s:.1f}-{slot_max_s:.1f}s",
                    )
                )
    allowed_long = max(1, math.ceil(round(EXCEPTION_SHARE * len(slots), 9)))
    for slot_id in long_moves[allowed_long:]:
        violations.append(
            violation(
                slot_id,
                "length",
                f"more than {allowed_long} slots run past {slot_max_s:.1f}s on screen "
                f"(at most {EXCEPTION_SHARE:.0%} of the slots, minimum one)",
            )
        )
    for first, second in zip(slots, slots[1:]):
        if first.get("clip") is None or second.get("clip") is None:
            continue
        first_fact = slot_fact(first, plan_by_id, facts_by_key) or {}
        second_fact = slot_fact(second, plan_by_id, facts_by_key) or {}
        if is_jump_cut(
            first,
            second,
            is_moving(first_fact.get("role"), first_fact.get("motion_label")),
            is_moving(second_fact.get("role"), second_fact.get("motion_label")),
        ):
            violations.append(
                violation(
                    second["id"],
                    "same_take",
                    f"same take as {first['id']} without a different take between them "
                    f"(allowed only for two camera moves at least {SAME_TAKE_MIN_GAP_S:.0f}s apart)",
                )
            )
    for first, second in zip(slots, slots[1:]):
        first_fact = slot_fact(first, plan_by_id, facts_by_key)
        second_fact = slot_fact(second, plan_by_id, facts_by_key)
        if first_fact is None or second_fact is None:
            continue
        if similar_fn is not None:
            is_similar = similar_fn(first, second, first_fact, second_fact)
        else:
            is_similar = shots_similar(
                first_fact.get("shot_key"),
                first_fact.get("similar_to"),
                second_fact.get("shot_key"),
                second_fact.get("similar_to"),
            )
        if is_similar:
            if plan_by_id.get(second["id"], {}).get("deliberate") is True:
                continue
            violations.append(
                violation(second["id"], "similar", f"visually similar to {first['id']}")
            )
    for first, second in zip(slots, slots[1:]):
        first_fact = slot_fact(first, plan_by_id, facts_by_key)
        second_fact = slot_fact(second, plan_by_id, facts_by_key)
        if first_fact is None or second_fact is None:
            continue
        if not same_segment_beats(first_fact, second_fact):
            continue
        if plan_by_id.get(second["id"], {}).get("deliberate") is True:
            continue
        violations.append(
            violation(second["id"], "same_segment", f"another beat of the same segment as {first['id']}")
        )
    return violations


def build_reason(shot, plan_slot):
    parts = []
    for part in (shot.get("reason"), plan_slot.get("why")):
        if part and part not in parts:
            parts.append(part)
    return " · ".join(parts) or "llm director"


def photo_duration_from_answer(answer, fact):
    """The photo's on-screen length: ``duration_s``, else ``out_s - in_s``, clamped to 2-4 s."""
    duration = answer.get("duration_s")
    if duration is None:
        try:
            duration = float(answer["out_s"]) - float(answer["in_s"])
        except (KeyError, TypeError, ValueError):
            duration = None
    if duration is None:
        duration = fact.get("target_s")
    if duration is None:
        duration = PHOTO_DEFAULT_S
    try:
        return round(clamp(float(duration), PHOTO_MIN_S, PHOTO_MAX_S), 2)
    except (TypeError, ValueError):
        return PHOTO_DEFAULT_S


def photo_source_row(fact):
    """The ``sources[]`` row for a used photo, resolved from the ingest ``photos[]`` join."""
    return {
        "file": fact["photo"],
        "path": fact.get("photo_path") or fact["photo"],
        "duration_s": None,
        "media_type": "photo",
    }


def make_photo_slot_record(sid, fact, shot, plan_slot, duration, transition):
    """A photo slot: a still held 2-4 s with its deterministic Ken Burns move."""
    return {
        "id": sid,
        "kind": "photo",
        "photo": fact["photo"],
        "duration_s": duration,
        "ken_burns": ken_burns_for(fact.get("subject_position"), fact.get("tags")),
        "score": float(shot.get("score") or fact.get("score") or 0.0),
        "reason": build_reason(shot, plan_slot),
        "transition": transition,
        "description": shot.get("description") or fact.get("description"),
        "capture_time": fact.get("capture_time"),
        "shot_key": fact.get("shot_key"),
    }


def apply_length_policy(record, plan, facts_by_key, pacing, important_max_s, warnings, speed_ramp=False):
    """Apply the move-versus-static length policy to a record's slots; return the cap per slot id.

    A camera move keeps as much of its window as fits, centred on its peak, at
    most 15 % of the slots (rounded) may run past ``slot_max`` on screen, and
    static shots, holds and photos stay within ``slot_max``. With ``speed_ramp``
    the middle half of a move may play up to 1.2x and the slot gains
    ``middle_speed``; the caps keep beat snapping inside the rule.
    """
    plan_by_id = {slot.get("id"): slot for slot in plan.get("slot_plan") or []}
    slots = record.get("video") or []
    working = []
    for slot in slots:
        if slot.get("kind") == "photo":
            working.append({"kind": "photo", "duration_s": float(slot["duration_s"]), "id": slot.get("id")})
            continue
        fact = facts_by_key.get((plan_by_id.get(slot.get("id")) or {}).get("shot_key")) or {}
        landing = fact.get("landing_at_s")
        working.append(
            {
                "id": slot.get("id"),
                "clip": slot.get("clip"),
                "in_s": float(slot["in_s"]),
                "out_s": float(slot["out_s"]),
                "window_in_s": fact.get("window_in_s", slot["in_s"]),
                "window_out_s": fact.get("window_out_s", slot["out_s"]),
                "landing_floor_s": float(landing) + LANDING_FLOOR_S if landing is not None else None,
                "role": slot.get("role"),
                "motion_label": slot.get("motion_label"),
                "important": keeps_whole(fact),
                "score": slot.get("score"),
                "shot_key": slot.get("shot_key"),
                "peak_s": fact_peak(fact) if fact else None,
            }
        )
    warnings.extend(
        apply_move_policy(
            working, {"slot_max": pacing["slot_max"], "hero_max": important_max_s}, speed_ramp=speed_ramp
        )
    )
    caps = {}
    for slot, item in zip(slots, working):
        caps[slot.get("id")] = item.get("max_screen_s")
        if slot.get("kind") == "photo":
            slot["duration_s"] = item["duration_s"]
            continue
        slot["in_s"] = item["in_s"]
        slot["out_s"] = item["out_s"]
        if item.get("middle_speed"):
            slot["middle_speed"] = item["middle_speed"]
        else:
            slot.pop("middle_speed", None)
    return caps


def annotate_peaks(record, plan, facts_by_key):
    """Write ``peak_s``, ``peak_source`` and ``centred`` on every video slot of the record."""
    plan_by_id = {slot.get("id"): slot for slot in plan.get("slot_plan") or []}
    for slot in record.get("video") or []:
        if slot.get("kind") == "photo":
            continue
        fact = facts_by_key.get((plan_by_id.get(slot.get("id")) or {}).get("shot_key"))
        if fact is None or fact.get("media_type") == "photo":
            continue
        slot["peak_s"] = round(fact_peak(fact), 3)
        slot["peak_source"] = fact.get("peak_source") or "middle"
        slot["centred"] = is_centred(
            slot["in_s"], slot["out_s"], fact["window_in_s"], fact["window_out_s"], slot["peak_s"]
        )


def clear_snap_notes(warnings):
    """Drop the previous pass's beat-snap and length-exception notes."""
    warnings[:] = [
        note for note in warnings
        if not (note.startswith("cut ") or note.startswith("exception:"))
    ]


def finalize_lengths(record, plan, facts_by_key, beat_grid, warnings, important_max_s, speed_ramp=False):
    """Run the length policy, then snap to the beat grid when there is one.

    Returns ``(snap summary, caps)`` so a later repair can re-snap with the same
    per-slot caps.
    """
    clear_snap_notes(warnings)
    caps = apply_length_policy(
        record, plan, facts_by_key, plan_pacing(plan), important_max_s, warnings, speed_ramp
    )
    summary = None
    if beat_grid is not None:
        summary = apply_beat_snap(record, plan, facts_by_key, beat_grid, warnings, caps)
    annotate_peaks(record, plan, facts_by_key)
    record.setdefault("meta", {})["speed_ramp"] = bool(speed_ramp)
    return summary, caps


def finish_record(record, plan, facts_by_key, shotlog_by_key, beat_grid, warnings, important_max_s, speed_ramp=False, drop_keys=frozenset(), similar_fn=None, add_keys=frozenset(), used_ids=None):
    """Length policy, then grow back to the brief length, then police the added slots.

    Returns ``(snap summary, caps)``.
    """
    if enforce_chronological_order(record, plan, facts_by_key, warnings, beat_grid):
        drop_pointless_cuts(record, plan, facts_by_key, shotlog_by_key, important_max_s, warnings, add_keys, similar_fn, drop_keys, used_ids)
    beat_snap, caps = finalize_lengths(record, plan, facts_by_key, beat_grid, warnings, important_max_s, speed_ramp)
    fill_notes = fill_to_target(record, plan, facts_by_key, shotlog_by_key, important_max_s, [], drop_keys, used_ids)
    for note in fill_notes:
        if note not in warnings:
            warnings.append(note)
    if fill_notes:
        beat_snap, caps = finalize_lengths(record, plan, facts_by_key, beat_grid, warnings, important_max_s, speed_ramp)
    return beat_snap, caps


def final_neighbour_check(record, plan, facts_by_key, shotlog_by_key, important_max_s, warnings, add_keys, similar_fn, beat_grid, caps, drop_keys=frozenset(), used_ids=None):
    """Run the window neighbour check once more and re-snap the cuts when it dropped a slot.

    Returns the new beat-snap summary when a drop forced a re-snap, else None.
    """
    before = len(record.get("video") or [])
    drop_pointless_cuts(record, plan, facts_by_key, shotlog_by_key, important_max_s, warnings, add_keys, similar_fn, drop_keys, used_ids)
    if len(record.get("video") or []) < before and beat_grid is not None:
        return apply_beat_snap(record, plan, facts_by_key, beat_grid, warnings, caps)
    return None


def apply_beat_snap(record, plan, facts_by_key, beat_grid, warnings, caps=None):
    """Snap a record's cuts to the grid, preferring downbeats."""
    slots = record.get("video") or []
    plan_by_id = {slot.get("id"): slot for slot in plan.get("slot_plan") or []}
    bounds = []
    for slot in slots:
        plan_slot = plan_by_id.get(slot.get("id")) or {}
        fact = facts_by_key.get(plan_slot.get("shot_key"))
        bounds.append(slot_bounds(slot, fact, (caps or {}).get(slot.get("id"))))
    return snap_slots(slots, bounds, beat_grid, warnings, plan_pacing(plan)["slot_min"])


def default_window(fact, plan_slot, important_max_s, slot_min_s, slot_max_s):
    """The cut window the editor spec asks for when the model gave none."""
    window_in = fact["window_in_s"]
    window_out = fact["window_out_s"]
    if keeps_whole(fact):
        return expected_cut(fact, important_max_s)
    target = plan_slot.get("target_s")
    if target is None:
        target = fact.get("target_s")
    if target is None:
        target = window_out - window_in
    target = clamp(float(target), slot_min_s, slot_max_s)
    return expected_cut(fact, target)


def normalise_window(in_s, out_s, fact, plan_slot, important_max_s, slot_min_s, slot_max_s):
    """Apply the editor spec's length and peak rules when the answer is out of policy."""
    notes = []
    target = plan_slot.get("target_s")
    if target is None:
        target = fact.get("target_s")
    if target is None:
        target = out_s - in_s
    target = clamp(float(target), slot_min_s, slot_max_s)
    duration = out_s - in_s
    if slot_min_s - TOLERANCE_S <= duration <= slot_max_s + TOLERANCE_S:
        outside = in_s < fact["window_in_s"] - TOLERANCE_S or out_s > fact["window_out_s"] + TOLERANCE_S
        if outside or not is_trimmed_cut(in_s, out_s, fact):
            return in_s, out_s, notes
        expected_in, expected_out = expected_cut(fact, duration)
        if abs(in_s - expected_in) <= CENTRE_TOLERANCE_S:
            return in_s, out_s, notes
        notes.append(f"window centred on the best moment at {fact_peak(fact):.2f}s")
        return expected_in, expected_out, notes
    new_in, new_out = expected_cut(fact, target)
    notes.append(f"length set to {new_out - new_in:.2f}s around the best moment at {fact_peak(fact):.2f}s")
    return new_in, new_out, notes


def build_edit_record(plan, answer, facts_by_key, shotlog_by_key, sources, brief_obj, wall_time_s, cost_usd, warnings=None, important_max_s=DEFAULT_IMPORTANT_MAX_S, beats=None, music=None, ambient_under_db=None):
    pacing = plan_pacing(plan)
    answer_by_id = {}
    for slot in answer.get("slots") or []:
        if isinstance(slot, dict) and slot.get("id"):
            answer_by_id[slot["id"]] = slot
    video = []
    fixed = []
    photo_rows = []
    for plan_slot in plan.get("slot_plan") or []:
        sid = plan_slot["id"]
        ans = answer_by_id.get(sid)
        if ans is None:
            continue
        fact = facts_by_key.get(plan_slot.get("shot_key"))
        if fact is None:
            continue
        shot = shotlog_by_key.get(plan_slot.get("shot_key")) or {}
        if not video or beats is not None or ans.get("transition") == "cut":
            transition = {"type": "cut", "duration_s": 0}
        else:
            transition = {"type": "crossfade", "duration_s": XFADE_S}
        if fact.get("media_type") == "photo":
            duration = photo_duration_from_answer(ans, fact)
            video.append(make_photo_slot_record(sid, fact, shot, plan_slot, duration, transition))
            photo_rows.append(photo_source_row(fact))
            continue
        try:
            in_s = float(ans["in_s"])
            out_s = float(ans["out_s"])
        except (KeyError, TypeError, ValueError):
            continue
        in_s, out_s, notes = normalise_window(
            in_s, out_s, fact, plan_slot, important_max_s, pacing["slot_min"], pacing["slot_max"],
        )
        for note in notes:
            fixed.append(f"fixed {sid}: {note}")
        video.append(
            {
                "id": sid,
                "clip": fact["clip"],
                "in_s": in_s,
                "out_s": out_s,
                "score": float(shot.get("score") or 0.0),
                "reason": build_reason(shot, plan_slot),
                "transition": transition,
                "speed": 1.0,
                "role": shot.get("role") or fact.get("role"),
                "motion_label": shot.get("motion_label") or fact.get("motion_label"),
                "description": shot.get("description"),
                "shot_key": fact.get("shot_key"),
            }
        )
    if warnings is not None:
        for note in fixed:
            if note not in warnings:
                warnings.append(note)
    if not video:
        return None
    record_sources = list(sources)
    seen_files = {source.get("file") for source in record_sources}
    for row in photo_rows:
        if row["file"] not in seen_files:
            record_sources.append(row)
            seen_files.add(row["file"])
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": TOOL,
        "brief": brief_obj,
        "sources": record_sources,
        "video": video,
        "audio": {
            "ambience": {"enabled": True, "gain_db": 0, "source": "slot", "file": None},
            "music": {"path": str(Path(music).expanduser())} if music else None,
            "ambient_under_music_db": ambient_under_db,
            "sfx": [],
            "levels": {"target_lufs": None, "true_peak_db": None},
        },
        "captions": [],
        "graphics": [],
        "meta": {"tool": TOOL, "wall_time_s": round(wall_time_s, 2), "llm_cost_usd": round(cost_usd, 8)},
    }


REBUILT_RULES = {"window", "peak", "length", "landing", "photo_length"}
NON_BLOCKING_RULES = {"hero"}


def swap_length_for(fact, victim_length, important_max_s, slot_min_s, slot_max_s):
    """On-screen length of the star's slot when it replaces a victim of ``victim_length`` seconds."""
    window = float(fact["window_out_s"]) - float(fact["window_in_s"])
    if keeps_whole(fact):
        return min(window, float(important_max_s))
    return clamp(float(victim_length), float(slot_min_s), min(float(slot_max_s), window))


def length_compatible(fact, victim_length, important_max_s, slot_min_s, slot_max_s):
    """True when the star can replace the victim without moving the edit's length far.

    A hero (``keep_whole``) may take its whole window up to the hero cap even
    when the victim is shorter — that is how the edit grows back to the brief
    length. An ordinary star stays within a factor of two of the victim, and
    nothing replaces a much longer slot with a much shorter shot.
    """
    star_length = swap_length_for(fact, victim_length, important_max_s, slot_min_s, slot_max_s)
    if star_length <= 0:
        return False
    victim_length = float(victim_length)
    if star_length < 0.5 * victim_length - TOLERANCE_S:
        return False
    if keeps_whole(fact):
        return star_length <= float(important_max_s) + TOLERANCE_S
    return star_length <= 2.0 * victim_length + TOLERANCE_S


def star_slot_cut(fact, victim_length, important_max_s, slot_min_s, slot_max_s):
    """The ``(in_s, out_s)`` of the star's replacement cut, centred on its peak."""
    length = swap_length_for(fact, victim_length, important_max_s, slot_min_s, slot_max_s)
    return expected_cut(fact, length)


def star_would_jump_cut(slots, index, star_fact, victim_length, important_max_s, slot_min_s, slot_max_s, plan_by_id, facts_by_key):
    """True when the star's replacement cut would jump-cut a neighbour of the victim."""
    in_s, out_s = star_slot_cut(star_fact, victim_length, important_max_s, slot_min_s, slot_max_s)
    replacement = {"clip": star_fact["clip"], "in_s": in_s, "out_s": out_s}
    star_moving = is_moving(star_fact.get("role"), star_fact.get("motion_label"))
    if index > 0:
        previous = slots[index - 1]
        previous_fact = slot_fact(previous, plan_by_id, facts_by_key) or {}
        if previous.get("clip") is not None and is_jump_cut(
            previous,
            replacement,
            is_moving(previous_fact.get("role"), previous_fact.get("motion_label")),
            star_moving,
        ):
            return True
    if index + 1 < len(slots):
        following = slots[index + 1]
        following_fact = slot_fact(following, plan_by_id, facts_by_key) or {}
        if following.get("clip") is not None and is_jump_cut(
            replacement,
            following,
            star_moving,
            is_moving(following_fact.get("role"), following_fact.get("motion_label")),
        ):
            return True
    return False


def pick_swap_victim(record, plan, facts_by_key, star_fact, important_max_s, slot_min_s, slot_max_s, add_keys=frozenset(), drop_keys=frozenset()):
    """The id of the weakest non-star slot the star can replace without a jump cut.

    The victim is never the owner's added shot, never a keep_whole slot, never a
    shot the owner named to drop and never a member of the owner's dropped group.
    """
    plan_by_id = {slot["id"]: slot for slot in plan.get("slot_plan") or []}
    slots = record.get("video") or []
    star_key = star_fact.get("shot_key")
    blocked = set(drop_keys) | star_group_keys(facts_by_key, drop_keys)
    best = None
    for index, slot in enumerate(slots):
        plan_slot = plan_by_id.get(slot.get("id"))
        if plan_slot is None:
            continue
        key = plan_slot.get("shot_key") or slot.get("shot_key")
        if key is None or key == star_key:
            continue
        victim_fact = facts_by_key.get(key) or {}
        if key in add_keys or key in blocked or keeps_whole(victim_fact):
            continue
        if is_star(victim_fact) or victim_fact.get("media_type") == "photo" or slot.get("kind") == "photo":
            continue
        victim_length = slot_duration(slot)
        if not length_compatible(star_fact, victim_length, important_max_s, slot_min_s, slot_max_s):
            continue
        if star_would_jump_cut(
            slots, index, star_fact, victim_length, important_max_s, slot_min_s, slot_max_s, plan_by_id, facts_by_key
        ):
            continue
        score = float(victim_fact.get("score") or slot.get("score") or 0.0)
        drift = abs(swap_length_for(star_fact, victim_length, important_max_s, slot_min_s, slot_max_s) - victim_length)
        candidate = (score, drift, slot.get("id"))
        if best is None or candidate < best:
            best = candidate
    return None if best is None else best[2]


def swap_star_in(record, plan, facts_by_key, shotlog_by_key, star_key, victim_id, important_max_s, slot_min_s, slot_max_s, notes):
    """Replace the victim plan slot with the star, rebuilt from its shot fact."""
    plan_by_id = {slot["id"]: slot for slot in plan.get("slot_plan") or []}
    plan_slot = plan_by_id.get(victim_id)
    if plan_slot is None:
        return
    star_fact = facts_by_key[star_key]
    victim_slot = next((row for row in record.get("video") or [] if row.get("id") == victim_id), None)
    victim_length = slot_duration(victim_slot) if victim_slot is not None else float(star_fact.get("target_s") or slot_min_s)
    length = swap_length_for(star_fact, victim_length, important_max_s, slot_min_s, slot_max_s)
    victim_key = plan_slot.get("shot_key") or (victim_slot or {}).get("shot_key")
    plan_slot["shot_key"] = star_key
    plan_slot["target_s"] = round(float(length), 3)
    plan_slot["keep_whole"] = bool(star_fact.get("keep_whole"))
    plan_slot["why"] = f"swapped in for {victim_key} (top star left out)"
    record["video"] = [row for row in record.get("video") or [] if row.get("id") != victim_id]
    ensure_all_planned_slots(record, plan, facts_by_key, shotlog_by_key, important_max_s)
    notes.append(f"repaired: swapped {star_key} in for {victim_key} (top star left out)")


def neighbour_rows(plan, facts_by_key, shotlog_by_key, record=None):
    """The plan's slots as neighbour rows for the shared pointless-neighbour rule.

    When ``record`` is given, each row also carries the ``in_s``/``out_s`` of the
    slot actually in the edit (matched by id), so the window similarity check can
    compare what is on screen.
    """
    windows = {}
    if record is not None:
        for slot in record.get("video") or []:
            windows[slot.get("id")] = (slot.get("in_s"), slot.get("out_s"))
    rows = []
    for slot in plan.get("slot_plan") or []:
        key = slot.get("shot_key")
        fact = facts_by_key.get(key) or {}
        shot = shotlog_by_key.get(key) or {}
        in_s, out_s = windows.get(slot.get("id"), (None, None))
        rows.append(
            {
                "shot_key": key,
                "clip": fact.get("clip"),
                "segment_id": fact.get("segment_id"),
                "score": shot.get("score"),
                "similar_to": fact.get("similar_to"),
                "in_s": in_s,
                "out_s": out_s,
            }
        )
    return rows


def drop_pointless_cuts(record, plan, facts_by_key, shotlog_by_key, important_max_s, notes, add_keys=frozenset(), similar_fn=None, drop_keys=frozenset(), used_ids=None):
    """Drop the weaker shot of every pointless jump-cut pair until none is left."""
    for _ in range(len(plan.get("slot_plan") or [])):
        pairs = [
            item for item in validate_edit(record, plan, facts_by_key, important_max_s)
            if item.get("rule") == "same_take"
        ]
        if not pairs:
            break
        plan_by_id = {slot["id"]: slot for slot in plan.get("slot_plan") or []}
        video = record.get("video") or []
        index = next(
            (position for position, slot in enumerate(video) if slot.get("id") == pairs[0].get("slot")),
            None,
        )
        if index is None or index == 0:
            break
        pair = [video[index - 1], video[index]]

        def score(slot):
            key = (plan_by_id.get(slot.get("id")) or {}).get("shot_key")
            return float((shotlog_by_key.get(key) or {}).get("score") or slot.get("score") or 0.0), key

        first_key = (plan_by_id.get(pair[0].get("id")) or {}).get("shot_key")
        second_key = (plan_by_id.get(pair[1].get("id")) or {}).get("shot_key")
        if first_key in add_keys and second_key in add_keys:
            break
        if first_key in add_keys:
            weaker = second_key
        elif second_key in add_keys:
            weaker = first_key
        else:
            weaker = min((score(slot) for slot in pair), key=lambda item: item[0])[1]
        if weaker is None:
            break
        drop_from_plan(plan, {weaker}, "pointless jump cut with its neighbour", notes, used_ids)
        planned = {row["id"] for row in plan.get("slot_plan") or []}
        record["video"] = [slot for slot in record.get("video") or [] if slot.get("id") in planned]
        ensure_all_planned_slots(record, plan, facts_by_key, shotlog_by_key, important_max_s)
    rows = neighbour_rows(plan, facts_by_key, shotlog_by_key, record)

    def similar(first, second):
        if similar_fn is not None:
            return similar_fn(first, second, first, second)
        return shots_similar(
            first.get("shot_key"),
            first.get("similar_to"),
            second.get("shot_key"),
            second.get("similar_to"),
        )

    keep_keys = (
        owner_asked_keys(plan)
        | set(add_keys)
        | set(required_star_keys(facts_by_key, len(plan.get("slot_plan") or []), drop_keys))
    )
    _, dropped = drop_pointless_neighbours(rows, similar, keep_keys=keep_keys)
    if dropped:
        drop_from_plan(
            plan,
            {row.get("shot_key") for row, _ in dropped},
            "pointless jump cut with its neighbour",
            notes,
            used_ids,
        )
        planned = {row["id"] for row in plan.get("slot_plan") or []}
        record["video"] = [slot for slot in record.get("video") or [] if slot.get("id") in planned]
        ensure_all_planned_slots(record, plan, facts_by_key, shotlog_by_key, important_max_s)


def repair_missing_stars(record, plan, facts_by_key, shotlog_by_key, important_max_s, notes, drop_keys=frozenset(), add_keys=frozenset()):
    """Swap every missing top star in for the weakest compatible non-star slot.

    The victim is never a shot the owner named (add or drop) and never a member
    of the owner's dropped group, so a repair never drops a named shot and never
    puts a dropped group back. A star with no compatible slot stays out with a
    note.
    """
    pacing = plan_pacing(plan)
    slot_min_s = pacing["slot_min"]
    slot_max_s = pacing["slot_max"]
    for _ in range(len(facts_by_key) + 1):
        missing = [
            item.get("slot")
            for item in validate_edit(record, plan, facts_by_key, important_max_s, drop_keys)
            if item.get("rule") == "hero_missing"
        ]
        if not missing:
            break
        swapped = False
        for star_key in missing:
            star_fact = facts_by_key.get(star_key)
            if star_fact is None:
                continue
            victim_id = pick_swap_victim(
                record, plan, facts_by_key, star_fact, important_max_s, slot_min_s, slot_max_s, add_keys, drop_keys
            )
            if victim_id is None:
                continue
            swap_star_in(
                record, plan, facts_by_key, shotlog_by_key, star_key, victim_id,
                important_max_s, slot_min_s, slot_max_s, notes,
            )
            swapped = True
            break
        if not swapped:
            for star_key in missing:
                note = f"could not add {star_key}: no compatible slot to swap"
                if note not in notes:
                    notes.append(note)
            break


TARGET_SLACK_S = 3.0


def edit_order_key(fact):
    """Chronological sort key of a shot: the clip's 4-digit counter, then its window start."""
    counter = clip_counter(fact.get("clip") or fact.get("photo") or "")
    return (counter, float(fact.get("window_in_s") or 0.0))


def is_chronological(plan):
    """True when the plan's brief asks for chronological order."""
    brief = plan.get("brief") if isinstance(plan, dict) else None
    return isinstance(brief, dict) and brief.get("order") == "chronological"


def slot_order_key(slot, plan_by_id, facts_by_key):
    """The chronological sort key of a record slot, from its shot fact."""
    fact = facts_by_key.get((plan_by_id.get(slot.get("id")) or {}).get("shot_key")) or {}
    return edit_order_key(fact)


def reset_transitions(record, beat_grid=None):
    """Set every slot's transition: first cut, the rest crossfade, or hard cuts with a beat grid."""
    for index, slot in enumerate(record.get("video") or []):
        if index == 0 or beat_grid is not None:
            slot["transition"] = {"type": "cut", "duration_s": 0}
        else:
            slot["transition"] = {"type": "crossfade", "duration_s": XFADE_S}


def enforce_chronological_order(record, plan, facts_by_key, notes, beat_grid=None):
    """Re-sort a chronological edit (and its plan) by capture/source time; return True when it changed.

    A repair (a swapped-in top star, a rebuilt cut) can put a late shot in an
    early slot; for a chronological brief the edit must stay in time order.
    After a re-sort every slot's transition is set again by the same rule
    ``build_edit_record`` uses.
    """
    if not is_chronological(plan):
        return False
    video = record.get("video") or []
    if len(video) < 2:
        return False
    plan_by_id = {slot["id"]: slot for slot in plan.get("slot_plan") or []}
    keys = {slot.get("id"): slot_order_key(slot, plan_by_id, facts_by_key) for slot in video}
    ordered = sorted(video, key=lambda slot: keys.get(slot.get("id"), ("", 0.0)))
    if [slot.get("id") for slot in ordered] == [slot.get("id") for slot in video]:
        return False
    record["video"] = ordered
    plan_by_id = {slot["id"]: slot for slot in plan.get("slot_plan") or []}
    plan["slot_plan"] = [plan_by_id[slot["id"]] for slot in ordered if slot.get("id") in plan_by_id]
    reset_transitions(record, beat_grid)
    notes.append("repaired: re-sorted the edit into chronological order")
    return True


def next_slot_id(plan, used_ids=frozenset()):
    """A free plan slot id like ``v13``, never one used earlier in the run."""
    used = {row.get("id") for row in plan.get("slot_plan") or []} | set(used_ids)
    index = 1
    while f"v{index}" in used:
        index += 1
    return f"v{index}"


def plan_insert_index(plan, facts_by_key, new_fact):
    """The plan position that keeps the slots in chronological order."""
    new_key = edit_order_key(new_fact)
    index = 0
    for row in plan.get("slot_plan") or []:
        fact = facts_by_key.get(row.get("shot_key"))
        if fact is None:
            index += 1
            continue
        if edit_order_key(fact) > new_key:
            break
        index += 1
    return index


def fill_to_target(record, plan, facts_by_key, shotlog_by_key, important_max_s, notes, drop_keys=frozenset(), used_ids=None):
    """Add unused strong shots as slots until the edit reaches the brief's target length.

    The screenwriter may plan fewer slots than the duration needs; this pass
    grows the edit with the best unused cuttable shots in chronological order,
    never creating a same-take jump cut or an adjacent similar pair. It is a
    repair of the draft the model wrote, not a fallback to the rule director. A
    key the owner dropped, or any member of its star group, is never added back.
    """
    brief = plan.get("brief") if isinstance(plan, dict) else None
    target = brief.get("target_duration_s") if isinstance(brief, dict) else None
    if not target:
        return notes
    pacing = plan_pacing(plan)
    slot_min_s = pacing["slot_min"]
    slot_max_s = pacing["slot_max"]
    blocked = set(drop_keys) | star_group_keys(facts_by_key, drop_keys)
    used_keys = {row.get("shot_key") for row in plan.get("slot_plan") or []}
    ranked = sorted(
        (
            (key, fact) for key, fact in facts_by_key.items()
            if key not in used_keys and key not in blocked and fact.get("media_type") != "photo"
        ),
        key=lambda item: (-float(item[1].get("score") or 0.0), item[0]),
    )
    for key, fact in ranked:
        total = sum(slot_duration(row) for row in record.get("video") or [])
        if total >= float(target) - TARGET_SLACK_S:
            break
        length = swap_length_for(fact, slot_max_s, important_max_s, slot_min_s, slot_max_s)
        if length < slot_min_s - TOLERANCE_S and not keeps_whole(fact):
            continue
        in_s, out_s = star_slot_cut(fact, slot_max_s, important_max_s, slot_min_s, slot_max_s)
        if out_s <= in_s:
            continue
        sid = next_slot_id(plan, used_ids or frozenset())
        if used_ids is not None:
            used_ids.add(sid)
        new_slot = {
            "id": sid,
            "clip": fact["clip"],
            "in_s": in_s,
            "out_s": out_s,
            "score": float(fact.get("score") or 0.0),
            "reason": f"added to reach the brief length ({key})",
            "transition": {"type": "crossfade", "duration_s": XFADE_S},
            "speed": 1.0,
            "role": fact.get("role"),
            "motion_label": fact.get("motion_label"),
            "description": fact.get("summary"),
            "shot_key": key,
        }
        insert_at = plan_insert_index(plan, facts_by_key, fact)
        if _fill_conflicts(record, plan, facts_by_key, new_slot, insert_at):
            continue
        beat = None
        if insert_at > 0:
            beat = (plan.get("slot_plan") or [])[insert_at - 1].get("beat")
        elif plan.get("slot_plan"):
            beat = plan["slot_plan"][0].get("beat")
        plan["slot_plan"].insert(
            insert_at,
            {
                "id": sid,
                "beat": beat,
                "shot_key": key,
                "target_s": round(float(length), 3),
                "why": new_slot["reason"],
                "keep_whole": bool(fact.get("keep_whole")),
            },
        )
        record["video"].insert(insert_at, new_slot)
        record["video"][0]["transition"] = {"type": "cut", "duration_s": 0}
        notes.append(f"repaired: added {key} to reach the brief length")
        used_keys.add(key)
    return notes


def _fill_conflicts(record, plan, facts_by_key, new_slot, insert_at):
    """True when the new slot would jump-cut or sit next to a similar shot."""
    plan_by_id = {slot["id"]: slot for slot in plan.get("slot_plan") or []}
    slots = record.get("video") or []
    fact = facts_by_key.get(new_slot.get("shot_key")) or {}
    new_moving = is_moving(fact.get("role"), fact.get("motion_label"))
    neighbours = []
    if insert_at > 0 and insert_at - 1 < len(slots):
        neighbours.append(slots[insert_at - 1])
    if insert_at < len(slots):
        neighbours.append(slots[insert_at])
    for neighbour in neighbours:
        neighbour_fact = slot_fact(neighbour, plan_by_id, facts_by_key) or {}
        if neighbour.get("clip") is not None and (
            is_jump_cut(
                neighbour,
                new_slot,
                is_moving(neighbour_fact.get("role"), neighbour_fact.get("motion_label")),
                new_moving,
            )
            or is_jump_cut(
                new_slot,
                neighbour,
                new_moving,
                is_moving(neighbour_fact.get("role"), neighbour_fact.get("motion_label")),
            )
        ):
            return True
        if shots_similar(
            fact.get("shot_key"),
            fact.get("similar_to"),
            neighbour_fact.get("shot_key"),
            neighbour_fact.get("similar_to"),
        ):
            return True
    return False


def repair_draft(record, plan, facts_by_key, shotlog_by_key, violations, important_max_s=DEFAULT_IMPORTANT_MAX_S, add_keys=frozenset(), drop_keys=frozenset(), similar_fn=None, used_ids=None):
    """Fix a draft's mechanical violations from the shot facts instead of discarding the draft.

    First pass: slots that are not in the plan are dropped, a landing hold
    planned again as its own slot leaves the plan, and a slot whose cut breaks
    a window, length, peak, keep-whole or landing rule is rebuilt from its shot
    fact the way the editor spec defines it. Second pass: of two same-take
    slots that would jump-cut, the lower-scoring one leaves the plan. Third
    pass: a missing top star the owner asked to add is swapped in for the weakest
    non-star slot of a compatible length that does not jump-cut its neighbours.
    Returns the plain-word notes of what changed.
    """
    notes = []
    plan_by_id = {slot["id"]: slot for slot in plan.get("slot_plan") or []}
    rebuild = set()
    landing_drop_keys = set()
    for item in violations:
        slot_id = item.get("slot")
        rule = item.get("rule")
        if rule == "landing" and "planned again" in (item.get("message") or ""):
            slot = next((row for row in record.get("video") or [] if row.get("id") == slot_id), None)
            fact = slot_fact(slot, plan_by_id, facts_by_key) if slot else None
            if fact and fact.get("landing_hold_key"):
                landing_drop_keys.add(fact["landing_hold_key"])
                continue
        if rule in REBUILT_RULES and slot_id in plan_by_id:
            rebuild.add(slot_id)
    drop_from_plan(plan, landing_drop_keys, "landing hold planned as its own slot", notes)
    planned = {row["id"] for row in plan.get("slot_plan") or []}
    kept = []
    for slot in record.get("video") or []:
        if slot.get("id") not in planned:
            notes.append(f"repaired: dropped {slot.get('id')}, it is not in the plan")
            continue
        if slot.get("id") in rebuild:
            notes.append(f"repaired: {slot['id']} cut rebuilt from its shot")
            continue
        kept.append(slot)
    record["video"] = kept
    ensure_all_planned_slots(record, plan, facts_by_key, shotlog_by_key, important_max_s)
    still_bad = {
        item.get("slot") for item in validate_edit(record, plan, facts_by_key, important_max_s, drop_keys)
        if item.get("rule") in REBUILT_RULES
    }
    if still_bad:
        plan_by_id = {slot["id"]: slot for slot in plan.get("slot_plan") or []}
        keys = {plan_by_id[slot_id].get("shot_key") for slot_id in still_bad if slot_id in plan_by_id}
        drop_from_plan(plan, keys, "its window cannot give a valid cut", notes)
        planned = {row["id"] for row in plan.get("slot_plan") or []}
        record["video"] = [slot for slot in record.get("video") or [] if slot.get("id") in planned]
        ensure_all_planned_slots(record, plan, facts_by_key, shotlog_by_key, important_max_s)
    drop_pointless_cuts(record, plan, facts_by_key, shotlog_by_key, important_max_s, notes, add_keys, similar_fn, drop_keys, used_ids)
    repair_missing_stars(record, plan, facts_by_key, shotlog_by_key, important_max_s, notes, drop_keys, add_keys)
    drop_pointless_cuts(record, plan, facts_by_key, shotlog_by_key, important_max_s, notes, add_keys, similar_fn, drop_keys, used_ids)
    fill_to_target(record, plan, facts_by_key, shotlog_by_key, important_max_s, notes, drop_keys, used_ids)
    drop_pointless_cuts(record, plan, facts_by_key, shotlog_by_key, important_max_s, notes, add_keys, similar_fn, drop_keys, used_ids)
    if enforce_chronological_order(record, plan, facts_by_key, notes):
        drop_pointless_cuts(record, plan, facts_by_key, shotlog_by_key, important_max_s, notes, add_keys, similar_fn, drop_keys, used_ids)
    return notes


def drop_from_plan(plan, keys, why, notes, used_ids=None):
    keys = {key for key in keys if key}
    if not keys:
        return
    if used_ids is not None:
        for row in plan.get("slot_plan") or []:
            if row.get("shot_key") in keys:
                used_ids.add(row.get("id"))
    plan["slot_plan"] = [row for row in plan.get("slot_plan") or [] if row.get("shot_key") not in keys]
    notes.extend(f"repaired: dropped {key} from the plan ({why})" for key in sorted(keys))


def blocking(violations):
    """The violations that make a draft unusable; a missing hero photo is only a warning."""
    return [item for item in violations if item.get("rule") not in NON_BLOCKING_RULES]


def violation_note(item):
    return f"violation {item.get('slot')}: {item.get('rule')}: {item.get('message')}"


def ensure_all_planned_slots(record, plan, facts_by_key, shotlog_by_key, important_max_s=DEFAULT_IMPORTANT_MAX_S):
    """Put every plan slot back in plan order, synthesising any the editor dropped."""
    pacing = plan_pacing(plan)
    existing = {slot["id"]: slot for slot in record.get("video") or [] if slot.get("id")}
    video = []
    added = []
    added_photo_rows = []
    for plan_slot in plan.get("slot_plan") or []:
        sid = plan_slot["id"]
        slot = existing.get(sid)
        if slot is None:
            fact = facts_by_key.get(plan_slot.get("shot_key"))
            if fact is None:
                continue
            shot = shotlog_by_key.get(plan_slot.get("shot_key")) or {}
            if fact.get("media_type") == "photo":
                duration = photo_duration_from_answer({}, fact)
                slot = make_photo_slot_record(
                    sid, fact, shot, plan_slot, duration, {"type": "crossfade", "duration_s": XFADE_S}
                )
                added_photo_rows.append(photo_source_row(fact))
            else:
                in_s, out_s = default_window(
                    fact, plan_slot, important_max_s, pacing["slot_min"], pacing["slot_max"],
                )
                slot = {
                    "id": sid,
                    "clip": fact["clip"],
                    "in_s": in_s,
                    "out_s": out_s,
                    "score": float(shot.get("score") or 0.0),
                    "reason": build_reason(shot, plan_slot),
                    "transition": {"type": "crossfade", "duration_s": XFADE_S},
                    "speed": 1.0,
                    "role": shot.get("role") or fact.get("role"),
                    "motion_label": shot.get("motion_label") or fact.get("motion_label"),
                    "description": shot.get("description"),
                    "shot_key": plan_slot.get("shot_key") or fact.get("shot_key"),
                }
            added.append(sid)
        video.append(slot)
    if added and video:
        video[0]["transition"] = {"type": "cut", "duration_s": 0}
    record["video"] = video
    if added_photo_rows:
        record_sources = list(record.get("sources") or [])
        seen_files = {source.get("file") for source in record_sources}
        for row in added_photo_rows:
            if row["file"] not in seen_files:
                record_sources.append(row)
                seen_files.add(row["file"])
        record["sources"] = record_sources
    return added


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


def write_plan(path, plan, started, cost_usd):
    plan["meta"] = {
        "tool": TOOL,
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": round(cost_usd, 8),
    }
    validate_or_raise(plan, "shot_plan")
    atomic_write_json(path, plan)


def attach_decisions(record, facts_by_key, shotlog_by_key, drop_reasons=None):
    """Set ``record["decisions"]``: every candidate used or rejected with a reason."""
    used_rows = []
    for slot in record.get("video") or []:
        key = slot.get("shot_key")
        fact = facts_by_key.get(key) or {}
        row = {
            "id": key or slot.get("id"),
            "slot": slot.get("id"),
            "reason": slot.get("reason") or "llm director",
            "score": slot.get("score"),
            "clip": slot.get("clip") or slot.get("photo"),
            "window_in_s": fact.get("window_in_s"),
            "window_out_s": fact.get("window_out_s"),
        }
        if fact.get("landing_hold_key"):
            row["extra_ids"] = [fact["landing_hold_key"]]
        used_rows.append(row)
    record["decisions"] = build_decisions(shotlog_by_key, used_rows, set(facts_by_key), drop_reasons)


DIRECTOR_NOTES_MAX = 20


def objection_text(problem, change):
    """The plain-word text of one reviewer fix: problem, change, or both joined."""
    problem = problem.strip() if isinstance(problem, str) else ""
    change = change.strip() if isinstance(change, str) else ""
    if problem and change:
        return f"{problem} — {change}"
    return problem or change


def annotate_review_slots(record, plan, facts_by_key, caps):
    """Write ``keep_whole`` and ``max_screen_s`` on every slot the reviewer sees."""
    plan_by_id = {slot.get("id"): slot for slot in plan.get("slot_plan") or []}
    for slot in record.get("video") or []:
        fact = facts_by_key.get((plan_by_id.get(slot.get("id")) or {}).get("shot_key")) or {}
        slot["keep_whole"] = bool(keeps_whole(fact))
        slot["max_screen_s"] = caps.get(slot.get("id"))


def review_copy(record, plan, facts_by_key, shotlog_by_key, important_max_s, beat_grid, speed_ramp=False, drop_keys=frozenset(), add_keys=frozenset(), similar_fn=None, used_ids=None):
    """A copy of the draft and its plan with the repair chain applied, for the reviewer.

    The reviewer judges what ships: the copy has been through
    ``ensure_all_planned_slots``, ``repair_draft`` and ``finish_record``, and each
    video slot carries ``keep_whole`` and ``max_screen_s``.
    """
    record_copy = copy.deepcopy(record)
    plan_copy = copy.deepcopy(plan)
    scratch = []
    ensure_all_planned_slots(record_copy, plan_copy, facts_by_key, shotlog_by_key, important_max_s)
    violations = validate_edit(record_copy, plan_copy, facts_by_key, important_max_s, drop_keys, similar_fn)
    if blocking(violations):
        repair_draft(record_copy, plan_copy, facts_by_key, shotlog_by_key, violations, important_max_s, add_keys, drop_keys, similar_fn, set(used_ids or ()))
    _, caps = finish_record(record_copy, plan_copy, facts_by_key, shotlog_by_key, beat_grid, scratch, important_max_s, speed_ramp, drop_keys, similar_fn, add_keys, set(used_ids or ()))
    annotate_review_slots(record_copy, plan_copy, facts_by_key, caps)
    return record_copy, plan_copy


def review_object(verdict, record, seen_keys=None):
    """``meta.director.review`` from the reviewer's last verdict, or None when it is not a dict.

    An objection whose slot is gone from the shipped edit, or now holds another
    shot than the reviewer saw, is dropped.
    """
    if not isinstance(verdict, dict):
        return None
    approved = bool(verdict.get("approved"))
    objections = []
    if not approved:
        shot_positions = {
            slot.get("id"): index + 1 for index, slot in enumerate(record.get("video") or [])
        }
        current_keys = {slot.get("id"): slot.get("shot_key") for slot in record.get("video") or []}
        for fix in verdict.get("fixes") or []:
            if not isinstance(fix, dict):
                continue
            text = objection_text(fix.get("problem"), fix.get("change"))
            if not text:
                continue
            slot = fix.get("slot")
            slot = slot if isinstance(slot, str) and slot else None
            if slot is not None:
                if slot not in shot_positions:
                    continue
                if seen_keys is not None and seen_keys.get(slot) != current_keys.get(slot):
                    continue
            objections.append(
                {"slot": slot, "shot": shot_positions.get(slot) if slot is not None else None, "text": text}
            )
        for note in verdict.get("notes") if isinstance(verdict.get("notes"), list) else []:
            text = note.strip() if isinstance(note, str) else ""
            if not text:
                continue
            objections.append({"slot": None, "shot": None, "text": text})
        if not objections:
            verdict_text = verdict.get("verdict")
            if isinstance(verdict_text, str) and verdict_text.strip():
                objections.append({"slot": None, "shot": None, "text": verdict_text})
    return {"approved": approved, "objections": objections}


def director_info(by, approved, warnings, review=None):
    """``meta.director``: who made the edit, whether the reviewer approved it, and the notes."""
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
        edit, report = _run_editor(workspace_path, plan_path, out_path, workspace_path / REPORT_FILE, beats_path)
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


def _run_editor(workspace_path, plan_path, out_path, report_path, beats_path):
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
            picture_fn(path, t_s, out, PICTURE_WIDTH)
        except FramesError as exc:
            warnings.append(f"no picture for {slot_id}: {exc}")
            continue
        pictures.append({"id": slot_id, "shot_key": slot.get("shot_key"), "path": str(out)})
    return pictures


def _picture_data_uri(path):
    """The JPEG file as a data URI, or None when it cannot be read."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def _director_review_call(client, prompt, payload, report, pictures, state, facts, brief_obj, content_type):
    """One review call: the payload, the editor report and one picture per shot."""
    user_text = (
        json.dumps(payload, ensure_ascii=False)
        + "\n\nThe editor cut your plan. Its report:\n"
        + json.dumps(report, ensure_ascii=False)
        + "\n\nPictures of the cut, one per shot, in timeline order: "
        + ", ".join(f"{picture['id']} ({picture['shot_key']})" for picture in pictures)
        + ". Approve or answer with a complete revised plan."
    )
    content = [{"type": "text", "text": user_text}]
    for picture in pictures:
        uri = _picture_data_uri(picture["path"])
        if uri is not None:
            content.append({"type": "image_url", "image_url": {"url": uri}})
    messages = [
        {"role": "system", "content": prompt},
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


def _run_loop(
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
):
    """Run the three-role loop body; ``run`` adds the ``feedback`` summary key."""
    started = time.monotonic()
    workspace_path, brief_text, brief_path = _resolve_workspace_brief(workspace, brief, brief_file)
    if rounds < 1:
        raise LLMDirectorError("--rounds must be at least 1")
    rounds = min(rounds, MAX_ROUNDS)
    feedback = list(feedback or [])
    if feedback:
        append_feedback_lines(workspace_path, feedback)
    feedback_block = build_feedback_block(feedback, workspace_path) if feedback else None
    owner_add_keys, owner_drop_keys = owner_keys_from_feedback(workspace_path, feedback)
    owner_add_keys = frozenset(owner_add_keys)
    owner_drop_keys = frozenset(owner_drop_keys)
    notebook_file = notebook_path(notebook)
    notebook_text = notebook_block(notebook_file) if notebook_file is not None else None

    out_path = Path(out).expanduser().resolve() if out else workspace_path / "edit.json"
    plan_path = Path(plan_out).expanduser().resolve() if plan_out else workspace_path / "shot_plan.json"
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

    content_type = detect_content_type(prompt_brief, workspace_path)
    screenwriter_prompt = read_role_prompt("screenwriter.md")
    editor_prompt = read_role_prompt("editor.md")
    reviewer_prompt = read_role_prompt("reviewer.md")

    embedder = WindowEmbedder(workspace_path)
    window_similar = make_window_similar(embedder)
    try:
        rounds_used = 0
        approved = None
        violations = []
        record = None
        plan = None
        try:
            plan = screenwriter(client, screenwriter_prompt, prompt_brief, content_type, hints, shortlist, state, effective_important_max_s, feedback_block, notebook_text)
            if plan is None:
                raise LoopFailure("screenwriter produced no valid plan")
            write_plan(plan_path, plan, started, state.cost_usd)
            if dry_run:
                baseline = run_baseline(workspace_path, prompt_brief, hints, beats_path, music, ambient_under_music_db, speed_ramp)
                summary = base_summary("written", None, plan_path, 0, None, None, 0, 0.0, 0, warnings, started, state.cost_usd, candidates, shortlist=len(shortlist))
                summary["baseline"] = baseline
                summary["dry_run"] = True
                return summary, 0

            def build_record_fn(answer):
                return build_edit_record(
                    plan, answer, facts, shotlog, sources, brief_obj, time.monotonic() - started, state.cost_usd, warnings, effective_important_max_s, beats=beat_grid,
                    music=music, ambient_under_db=ambient_under_music_db,
                )

            review = None
            last_verdict = None
            last_review_keys = None
            previous_blocking = None
            used_ids = set()
            for round_index in range(rounds):
                rounds_used = round_index + 1
                record = editor_round(client, editor_prompt, plan, facts, review, state, build_record_fn, feedback_block, notebook_text)
                if record is None:
                    raise LoopFailure("editor produced no valid edit")
                violations = validate_edit(record, plan, facts, effective_important_max_s, owner_drop_keys, window_similar)
                review_record, review_plan = review_copy(record, plan, facts, shotlog, effective_important_max_s, beat_grid, speed_ramp, owner_drop_keys, owner_add_keys, window_similar, used_ids)
                review_violations = validate_edit(review_record, review_plan, facts, effective_important_max_s, owner_drop_keys, window_similar)
                verdict = reviewer(client, reviewer_prompt, prompt_brief, review_plan, review_record, review_violations, state, feedback_block, notebook_text)
                if verdict is None:
                    raise LoopFailure("reviewer produced no valid answer")
                last_verdict = verdict
                last_review_keys = {slot.get("id"): slot.get("shot_key") for slot in review_record.get("video") or []}
                if verdict.get("approved") is True and not review_violations:
                    approved = True
                    break
                approved = False
                review = verdict
                current_blocking = {(item.get("slot"), item.get("rule")) for item in blocking(violations)}
                if previous_blocking is not None and current_blocking == previous_blocking:
                    warnings.append("stopped early: the same blocking violations came back after a fix")
                    break
                previous_blocking = current_blocking
        except BudgetExceeded as exc:
            warnings.append(str(exc))
            if fallback_rules:
                return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, rounds_used, approved, violations, started, candidates, beats_path, music, ambient_under_music_db, speed_ramp, shortlist=len(shortlist), keep_keys=owner_asked_keys(plan) if plan else frozenset(), add_keys=owner_add_keys, drop_keys=owner_drop_keys)
            return stop_result(out_path, plan_path, warnings, state, rounds_used, approved, violations, started, EXIT_RUNTIME, candidates, shortlist=len(shortlist))
        except LoopFailure as exc:
            warnings.append(str(exc))
            if fallback_rules:
                return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, rounds_used, approved, violations, started, candidates, beats_path, music, ambient_under_music_db, speed_ramp, shortlist=len(shortlist), keep_keys=owner_asked_keys(plan) if plan else frozenset(), add_keys=owner_add_keys, drop_keys=owner_drop_keys)
            return stop_result(out_path, plan_path, warnings, state, rounds_used, approved, violations, started, EXIT_RUNTIME, candidates, shortlist=len(shortlist))

        if approved:
            for note in fill_to_target(record, plan, facts, shotlog, effective_important_max_s, [], owner_drop_keys, used_ids):
                if note not in warnings:
                    warnings.append(note)
            leftover = validate_edit(record, plan, facts, effective_important_max_s, owner_drop_keys, window_similar)
            if blocking(leftover):
                for note in repair_draft(record, plan, facts, shotlog, leftover, effective_important_max_s, owner_add_keys, owner_drop_keys, window_similar, used_ids):
                    if note not in warnings:
                        warnings.append(note)
            beat_snap, caps = finish_record(record, plan, facts, shotlog, beat_grid, warnings, effective_important_max_s, speed_ramp, owner_drop_keys, window_similar, owner_add_keys, used_ids)
            final_snap = final_neighbour_check(record, plan, facts, shotlog, effective_important_max_s, warnings, owner_add_keys, window_similar, beat_grid, caps, owner_drop_keys, used_ids)
            if final_snap is not None:
                beat_snap = final_snap
            for item in blocking(validate_edit(record, plan, facts, effective_important_max_s, owner_drop_keys, window_similar)):
                note = violation_note(item)
                if note not in warnings:
                    warnings.append(note)
            attach_decisions(record, facts, shotlog, drop_reasons)
            write_plan(plan_path, plan, started, state.cost_usd)
            try:
                write_edit(out_path, record, started, state.cost_usd, director_info("llm", True, warnings, review_object(last_verdict, record, last_review_keys)))
            except SchemaError as exc:
                warnings.append(str(exc))
                if fallback_rules:
                    return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, rounds_used, approved, violations, started, candidates, beats_path, music, ambient_under_music_db, speed_ramp, shortlist=len(shortlist), keep_keys=owner_asked_keys(plan) if plan else frozenset(), add_keys=owner_add_keys, drop_keys=owner_drop_keys)
                return stop_result(out_path, plan_path, warnings, state, rounds_used, approved, violations, started, EXIT_RUNTIME, candidates, shortlist=len(shortlist))
            slots = record["video"]
            total = sum(slot_duration(slot) for slot in slots)
            return base_summary("written", out_path, plan_path, rounds_used, True, None, len(slots), total, len(violations), warnings, started, state.cost_usd, candidates, beat_snap, shortlist=len(shortlist)), 0

        if violations:
            added = ensure_all_planned_slots(record, plan, facts, shotlog, effective_important_max_s)
            for sid in added:
                note = f"fixed {sid}: slot synthesised from the shot fact"
                if note not in warnings:
                    warnings.append(note)
            if added:
                violations = validate_edit(record, plan, facts, effective_important_max_s, owner_drop_keys, window_similar)
        if blocking(violations):
            for note in repair_draft(record, plan, facts, shotlog, violations, effective_important_max_s, owner_add_keys, owner_drop_keys, window_similar, used_ids):
                if note not in warnings:
                    warnings.append(note)
            violations = validate_edit(record, plan, facts, effective_important_max_s, owner_drop_keys, window_similar)
            if record.get("video"):
                write_plan(plan_path, plan, started, state.cost_usd)
        for item in violations:
            if item.get("rule") in NON_BLOCKING_RULES and violation_note(item) not in warnings:
                warnings.append(violation_note(item))
        violations = blocking(violations)

        if not violations and record.get("video"):
            warnings.append("reviewer did not approve; keeping the last valid draft")
            beat_snap, caps = finish_record(record, plan, facts, shotlog, beat_grid, warnings, effective_important_max_s, speed_ramp, owner_drop_keys, window_similar, owner_add_keys, used_ids)
            final_snap = final_neighbour_check(record, plan, facts, shotlog, effective_important_max_s, warnings, owner_add_keys, window_similar, beat_grid, caps, owner_drop_keys, used_ids)
            if final_snap is not None:
                beat_snap = final_snap
            for item in blocking(validate_edit(record, plan, facts, effective_important_max_s, owner_drop_keys, window_similar)):
                note = violation_note(item)
                if note not in warnings:
                    warnings.append(note)
            attach_decisions(record, facts, shotlog, drop_reasons)
            write_plan(plan_path, plan, started, state.cost_usd)
            try:
                write_edit(out_path, record, started, state.cost_usd, director_info("llm", False, warnings, review_object(last_verdict, record, last_review_keys)))
            except SchemaError as exc:
                warnings.append(str(exc))
                if fallback_rules:
                    return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, rounds_used, approved, violations, started, candidates, beats_path, music, ambient_under_music_db, speed_ramp, shortlist=len(shortlist), keep_keys=owner_asked_keys(plan) if plan else frozenset(), add_keys=owner_add_keys, drop_keys=owner_drop_keys)
                return stop_result(out_path, plan_path, warnings, state, rounds_used, approved, violations, started, EXIT_RUNTIME, candidates, shortlist=len(shortlist))
            slots = record["video"]
            total = sum(slot_duration(slot) for slot in slots)
            return base_summary("written", out_path, plan_path, rounds_used, False, None, len(slots), total, 0, warnings, started, state.cost_usd, candidates, beat_snap, shortlist=len(shortlist)), 0

        warnings.append("reviewer did not approve and the draft still has violations")
        for item in violations:
            note = violation_note(item)
            if note not in warnings:
                warnings.append(note)
        if fallback_rules:
            return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, rounds_used, approved, violations, started, candidates, beats_path, music, ambient_under_music_db, speed_ramp, shortlist=len(shortlist), keep_keys=owner_asked_keys(plan) if plan else frozenset(), add_keys=owner_add_keys, drop_keys=owner_drop_keys)
        return stop_result(out_path, plan_path, warnings, state, rounds_used, approved, violations, started, EXIT_RUNTIME, candidates, shortlist=len(shortlist))
    finally:
        embedder.save()


def _resolve_workspace_brief(workspace, brief, brief_file):
    """Resolve the workspace dir, the brief text and the brief path the way the loop does."""
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
    """The loop's context block: hints, content type, pacing, shot facts and sources."""
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


def _prompt_section(name, heading):
    """The text of one ``##`` section of a prompt, without the heading."""
    lines = read_prompt(name).splitlines()
    collected = []
    inside = False
    for line in lines:
        if line.strip() == heading:
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        if inside:
            collected.append(line)
    return "\n".join(collected).strip()


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
            edit, report = _run_editor(workspace_path, plan_path, out_path, workspace_path / REPORT_FILE, beats_path)
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
        edit, report = _run_editor(workspace_path, plan_path, out_path, workspace_path / REPORT_FILE, beats_path)
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
    """Reject more than one of --prepare, --apply, --interpret-feedback, --chat and --dry-run."""
    modes = []
    if args.prepare:
        modes.append("--prepare")
    if args.apply:
        modes.append("--apply")
    if args.interpret_feedback is not None:
        modes.append("--interpret-feedback")
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


def print_interpret(summary):
    print(summary["reply"])
    if summary.get("can_do") != "all" and summary.get("missing_tool"):
        print(f"missing tool: {summary['missing_tool']}")
    estimate = []
    if summary["estimate_usd"] is not None:
        estimate.append(f"${summary['estimate_usd']:.4f}")
    if summary["estimate_s"] is not None:
        estimate.append(f"{summary['estimate_s']:.1f}s")
    if estimate:
        print("estimate: " + ", ".join(estimate))


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
        elif args.interpret_feedback is not None:
            summary, code = interpret_feedback(
                args.workspace,
                args.interpret_feedback,
                version=args.version,
                model=args.model,
                budget_usd=args.budget_usd,
                reasoning_effort=args.reasoning_effort,
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
    elif args.interpret_feedback is not None:
        print_interpret(summary)
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
