#!/usr/bin/env python3
"""LLM director: screenwriter, editor and reviewer loop into edit.json.

Usage:
    python scripts/llm_director.py --workspace DIR --brief "..."
    python scripts/llm_director.py --workspace DIR --brief-file brief.txt --json
    python scripts/llm_director.py --workspace DIR --brief "..." --dry-run
    python scripts/llm_director.py --workspace DIR --brief "..." --rounds 2 --out my_edit.json

Reads ``shotlog.json``, ``ingest.json``, ``motion/<stem>.json`` and
``quality/<stem>.json`` from the workspace and runs a three-role LLM loop: the
screenwriter writes ``shot_plan.json``, the editor picks the cut windows, the
reviewer checks the result against the brief and the editing rules and sends
fixes back for at most ``--rounds`` rounds. The rule-based ``scripts/director.py``
is the fallback when the loop fails and the ``--dry-run`` baseline. Sources are
read-only; outputs are written atomically.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common.config import ConfigError, load_config
from common.llm import BudgetExceeded, LLMError, make_client
from common.schema import SchemaError, validate, validate_or_raise

TOOL = "scripts/llm_director.py"
SCHEMA_VERSION = "0.1"
REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = REPO_ROOT / "prompts"
DIRECTOR_SCRIPT = REPO_ROOT / "scripts" / "director.py"

MAX_ROUNDS = 3
MIN_SLOT_S = 2.0
MAX_SLOT_S = 12.0
DEFAULT_IMPORTANT_MAX_S = 6.0
XFADE_S = 0.3
TOLERANCE_S = 0.05

SKIP_ROLES = {"accidental", "setup", "teardown"}
MOVE_ROLES = {"move", "transition_in", "transition_out"}
LANDING_GAP_S = 0.75
CONTIGUOUS_TOLERANCE_S = 0.5
IMPORTANT_SEMANTIC_MOTIONS = {"orbit", "reveal", "tracking", "pov", "travel"}
IMPORTANT_MOTION_LABELS = {"orbit", "orbit_candidate", "reveal", "tracking", "pov"}
NON_MOVE_MOTION_LABELS = {"static", "minor_motion"}

CLIP_COUNTER = re.compile(r"_(\d{4})_")
CONTENT_TYPES = ("vacation", "tutorial", "presentation", "talking-head")
TRAVEL_LABELS = {"travel_forward", "travel_backward", "orbit", "orbit_candidate"}

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


class LLMDirectorError(RuntimeError):
    """Raised when the LLM director cannot produce an edit."""


class LoopFailure(RuntimeError):
    """Raised when the three-role loop cannot produce a valid edit."""


class LoopState:
    """Model calls and the spend of one run."""

    def __init__(self):
        self.calls = 0
        self.cost_usd = 0.0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="llm_director.py",
        description=(
            "Turn a brief and a workspace shotlog into edit.json with a three-role "
            "LLM loop: screenwriter (shot_plan.json), editor (cut windows) and "
            "reviewer (checks and fixes, max 3 rounds). Falls back to the rule-based "
            "director when the loop fails."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/llm_director.py --workspace ~/Videos/ws --brief \"about 90 seconds, calm start\"\n"
            "  python scripts/llm_director.py --workspace ~/Videos/ws --brief-file brief.txt --json\n"
            "  python scripts/llm_director.py --workspace ~/Videos/ws --brief \"...\" --dry-run\n"
            "  python scripts/llm_director.py --workspace ~/Videos/ws --brief \"...\" --rounds 2 --out my_edit.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--workspace", required=True, help="workspace holding shotlog.json, ingest.json, motion/ and quality/")
    parser.add_argument("--brief", default=None, help="the brief in plain language")
    parser.add_argument("--brief-file", default=None, help="path to a file holding the brief")
    parser.add_argument("--out", default=None, help="edit list to write (default WORKSPACE/edit.json)")
    parser.add_argument("--plan-out", default=None, help="shot plan to write (default WORKSPACE/shot_plan.json)")
    parser.add_argument("--rounds", type=int, default=MAX_ROUNDS, help="review rounds (default 3, max 3)")
    parser.add_argument("--dry-run", action="store_true", help="run only the screenwriter and print the rule-based baseline")
    parser.add_argument("--fallback-rules", dest="fallback_rules", action="store_true", default=True, help="fall back to the rule-based director when the loop fails (default)")
    parser.add_argument("--no-fallback-rules", dest="fallback_rules", action="store_false", help="error instead of falling back to the rule-based director")
    parser.add_argument("--model", default=None, help="director model id override (default models.director from config)")
    parser.add_argument("--budget-usd", type=float, default=None, help="refuse calls past this spend (default llm_budget_usd)")
    parser.add_argument("--important-max-s", type=float, default=DEFAULT_IMPORTANT_MAX_S, help="longest a hero or semantic-move slot may be (default 6.0)")
    parser.add_argument("--target-duration-s", type=float, default=None, help="override the target duration parsed from the brief")
    parser.add_argument("--order", choices=["chronological", "score"], default=None, help="override the order parsed from the brief")
    parser.add_argument("--pacing", choices=["calm-to-energy", "uniform"], default=None, help="override the pacing parsed from the brief")
    parser.add_argument("--force", action="store_true", help="rewrite even when the output is up to date")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def clamp(value, low, high):
    return max(low, min(high, value))


def parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def first_not_none(*values):
    for value in values:
        if value is not None:
            return value
    return None


def clip_counter(name):
    match = CLIP_COUNTER.search(name)
    return match.group(1) if match else Path(name).stem


def candidate_key(candidate):
    return f"{clip_counter(candidate['clip'])}/{candidate['segment'].get('id')}"


def overlap_seconds(a_start, a_end, b_start, b_end):
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def load_shotlog(workspace):
    path = workspace / "shotlog.json"
    if not path.exists():
        return None
    data = load_json(path)
    if not isinstance(data, dict):
        return None
    shots = data.get("shots") or []
    return {shot["key"]: shot for shot in shots if shot.get("key")}


def load_ingest(workspace):
    path = workspace / "ingest.json"
    if not path.exists():
        raise LLMDirectorError(f"missing ingest.json in {workspace}")
    data = load_json(path)
    if data is None:
        raise LLMDirectorError(f"unreadable ingest.json in {workspace}")
    clips = data.get("clips") or []
    if not clips:
        raise LLMDirectorError("ingest.json has no clips")
    return clips


def order_clips(clips):
    parsed = [(clip, parse_time(clip.get("creation_time"))) for clip in clips]
    known = [moment for _, moment in parsed if moment is not None]
    fallback = min(known) if known else datetime.min.replace(tzinfo=timezone.utc)
    ordered = sorted(parsed, key=lambda item: (item[1] or fallback, item[0]["file"]))
    return [(clip, moment or fallback) for clip, moment in ordered]


def clip_position(moment, first_moment, span_seconds):
    return clamp((moment - first_moment).total_seconds() / span_seconds, 0.0, 1.0)


def best_overlapping_move(segment, moves):
    best = None
    best_overlap = 0.0
    for move in moves:
        overlap = overlap_seconds(segment["start_s"], segment["end_s"], move["start_s"], move["end_s"])
        if overlap > best_overlap:
            best_overlap = overlap
            best = move
    return best


def build_candidates(clip, clip_dt, position, content_offset, quality, motion, min_slot_s, beats_by_segment=None):
    moves = (motion or {}).get("moves") or []
    beats_by_segment = beats_by_segment or {}
    candidates = []
    for segment in quality.get("segments") or []:
        role = segment.get("role")
        if role in SKIP_ROLES:
            continue
        subranges = segment_subranges(segment, beats_by_segment, clip.get("file"))
        for sub_start, sub_end, shot_key in subranges:
            move = None
            if role == "hold":
                in_s, out_s = sub_start, sub_end
            else:
                move = best_overlapping_move({"start_s": sub_start, "end_s": sub_end}, moves)
                if move is not None:
                    in_s = first_not_none(move.get("handle_in_s"), move.get("steady_start_s"), move.get("start_s"))
                    out_s = first_not_none(move.get("steady_end_s"), move.get("handle_out_s"), move.get("end_s"))
                else:
                    in_s, out_s = sub_start, sub_end
                in_s = max(in_s, sub_start)
                out_s = min(out_s, sub_end)
            if in_s is None or out_s is None or out_s - in_s < min_slot_s:
                continue
            candidates.append(
                {
                    "clip": clip["file"],
                    "clip_dt": clip_dt,
                    "position": position,
                    "content_offset": content_offset,
                    "role": role,
                    "segment": segment,
                    "move": move,
                    "in_s": float(in_s),
                    "out_s": float(out_s),
                    "motion_label": move.get("label") if move else segment.get("motion_label"),
                    "steadiness": move.get("steadiness") if move else ("static" if role == "hold" else None),
                    "hero": None,
                    "semantic_motion": None,
                    "shot_key": shot_key,
                }
            )
    return candidates


def segment_subranges(segment, beats_by_segment, clip_file=None):
    """The ``(start, end, shot key)`` units of a segment: its beats or itself."""
    beats = beats_by_segment.get((clip_file, segment.get("id"))) or beats_by_segment.get(segment.get("id")) or []
    if not beats:
        return [(float(segment["start_s"]), float(segment["end_s"]), None)]
    subranges = []
    for beat in beats:
        start_s = max(float(segment["start_s"]), float(beat.get("start_s", segment["start_s"])))
        end_s = min(float(segment["end_s"]), float(beat.get("end_s", segment["end_s"])))
        if end_s - start_s <= 0:
            continue
        subranges.append((start_s, end_s, beat.get("key")))
    return subranges or [(float(segment["start_s"]), float(segment["end_s"]), None)]


def beats_by_segment_from_shotlog(shotlog):
    """Group the shotlog's content-beat shots by their clip and parent segment id."""
    grouped = {}
    if not shotlog:
        return grouped
    for shot in shotlog.values():
        beat_of = shot.get("beat_of")
        if not beat_of:
            continue
        grouped.setdefault((shot.get("clip"), beat_of), []).append(shot)
    for beats in grouped.values():
        beats.sort(key=lambda item: (item.get("start_s") or 0.0, item.get("key") or ""))
    return grouped


def landing_pairs(quality):
    segments = quality.get("segments") or []
    pairs = []
    for first, second in zip(segments, segments[1:]):
        if first.get("role") in MOVE_ROLES and second.get("role") == "hold":
            if second["start_s"] - first["end_s"] <= LANDING_GAP_S:
                pairs.append((first, second))
    return pairs


def make_phrase_candidate(move_candidate, hold_segment):
    phrase = dict(move_candidate)
    phrase["out_s"] = float(hold_segment["end_s"])
    phrase["score"] = move_candidate["score"]
    phrase["phrase"] = True
    phrase["landing_start_s"] = float(hold_segment["start_s"])
    phrase["landing_end_s"] = float(hold_segment["end_s"])
    phrase["landing_hold_key"] = f"{clip_counter(move_candidate['clip'])}/{hold_segment.get('id')}"
    return phrase


def build_phrases(scored, quality):
    pairs = landing_pairs(quality)
    if not pairs:
        return scored
    removed = set()
    phrases = []
    for move_segment, hold_segment in pairs:
        siblings = [c for c in scored if c.get("segment") is move_segment]
        move_candidate = _phrase_move(siblings)
        if move_candidate is None:
            continue
        removed.add(id(move_candidate))
        phrases.append(make_phrase_candidate(move_candidate, hold_segment))
    kept = [candidate for candidate in scored if id(candidate) not in removed]
    return kept + phrases


def _phrase_move(candidates):
    """The candidate that lands on the hold: the one latest in source time."""
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item["out_s"], item.get("score") or 0.0))


def dedupe_candidates(candidates):
    kept = []
    ordered = sorted(
        candidates,
        key=lambda item: (-item["score"], 0 if item.get("phrase") else 1, item["in_s"]),
    )
    for candidate in ordered:
        if any(overlap_seconds(candidate["in_s"], candidate["out_s"], other["in_s"], other["out_s"]) > 0 for other in kept):
            continue
        kept.append(candidate)
    return kept


def is_important(candidate):
    if candidate.get("hero"):
        return True
    motion_label = candidate.get("motion_label")
    if motion_label in NON_MOVE_MOTION_LABELS:
        return False
    if candidate.get("semantic_motion") in IMPORTANT_SEMANTIC_MOTIONS:
        return True
    return motion_label in IMPORTANT_MOTION_LABELS


def default_target_s(candidate, important_max_s):
    window = candidate["out_s"] - candidate["in_s"]
    if is_important(candidate):
        return round(min(window, important_max_s), 3)
    return round(clamp(window, MIN_SLOT_S, MAX_SLOT_S), 3)


def make_fact(candidate, important_max_s):
    return {
        "shot_key": candidate.get("shot_key"),
        "clip": candidate["clip"],
        "window_in_s": round(float(candidate["in_s"]), 3),
        "window_out_s": round(float(candidate["out_s"]), 3),
        "landing_at_s": round(float(candidate["landing_start_s"]), 3) if candidate.get("phrase") else None,
        "landing_hold_key": candidate.get("landing_hold_key"),
        "keep_whole": is_important(candidate),
        "target_s": default_target_s(candidate, important_max_s),
        "score": candidate.get("score"),
        "summary": candidate.get("summary"),
        "role": candidate.get("role"),
        "motion_label": candidate.get("motion_label"),
        "hero": candidate.get("hero"),
        "similar_to": list(candidate.get("similar_to") or []),
    }


def build_shot_facts(workspace, shotlog, important_max_s=DEFAULT_IMPORTANT_MAX_S):
    clips = order_clips(load_ingest(workspace))
    first_moment = clips[0][1]
    last_clip, last_moment = clips[-1]
    span_end = last_moment + timedelta(seconds=last_clip["duration_s"])
    span_seconds = max(1e-9, (span_end - first_moment).total_seconds())
    content_offsets = []
    running = 0.0
    for clip, _ in clips:
        content_offsets.append(running)
        running += clip.get("duration_s") or 0.0
    facts = {}
    warnings = []
    beats_by_segment = beats_by_segment_from_shotlog(shotlog)
    for (clip, moment), content_offset in zip(clips, content_offsets):
        stem = Path(clip["file"]).stem
        quality = load_json(workspace / "quality" / f"{stem}.json")
        if quality is None:
            warnings.append(f"missing quality document for {clip['file']}")
            continue
        motion = load_json(workspace / "motion" / f"{stem}.json")
        position = clip_position(moment, first_moment, span_seconds)
        candidates = build_candidates(
            clip, moment, position, content_offset, quality, motion, MIN_SLOT_S,
            beats_by_segment=beats_by_segment,
        )
        scored = []
        for candidate in candidates:
            lookup_key = candidate.get("shot_key") or candidate_key(candidate)
            shot = shotlog.get(lookup_key)
            if shot is None or not shot.get("eligible", True):
                continue
            candidate["score"] = shot["score"]
            candidate["role"] = shot.get("role") or candidate["role"]
            candidate["motion_label"] = shot.get("motion_label") or candidate["motion_label"]
            candidate["description"] = shot.get("description")
            candidate["shot_reason"] = shot.get("reason")
            candidate["shot_key"] = shot.get("key")
            candidate["similar_to"] = list(shot.get("similar_to") or [])
            candidate["hero"] = shot.get("hero")
            candidate["semantic_motion"] = shot.get("semantic_motion")
            candidate["summary"] = shot.get("summary")
            scored.append(candidate)
        scored = build_phrases(scored, quality)
        for candidate in dedupe_candidates(scored):
            key = candidate.get("shot_key")
            if key:
                facts[key] = make_fact(candidate, important_max_s)
    return facts, clips, warnings


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


def call_client(client, messages, state):
    response = client.complete("director", messages)
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


def build_screenwriter_user(brief_text, content_type, hints, rows):
    lines = [
        f"Brief: {brief_text}",
        f"Content type: {content_type}",
        f"Target duration: {hints.get('target_duration_s')} s",
        f"Order: {hints.get('order')}",
        f"Pacing: {hints.get('pacing')}",
        "",
        "Eligible shotlog rows:",
        json.dumps(rows, indent=2),
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
        result.append(merged)
    return result


def build_editor_user(plan, facts, review):
    lines = [
        "Plan:",
        json.dumps(
            {
                "structure": plan.get("structure"),
                "slot_plan": plan.get("slot_plan"),
                "notes": plan.get("notes"),
            },
            indent=2,
        ),
        "",
        "Shot facts:",
        json.dumps(editor_facts(plan, facts), indent=2),
    ]
    if review:
        lines += ["", "Review fixes from the previous round:", json.dumps(review, indent=2)]
    return "\n".join(lines)


def build_reviewer_user(brief_text, plan, record, violations):
    lines = [
        f"Brief: {brief_text}",
        "",
        "Plan:",
        json.dumps(
            {
                "structure": plan.get("structure"),
                "slot_plan": plan.get("slot_plan"),
                "notes": plan.get("notes"),
            },
            indent=2,
        ),
        "",
        "Draft edit slots:",
        json.dumps(record.get("video"), indent=2),
        "",
        "Validator violations:",
        json.dumps(violations, indent=2),
    ]
    return "\n".join(lines)


def screenwriter(client, prompt, brief_text, content_type, hints, rows, state):
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": build_screenwriter_user(brief_text, content_type, hints, rows)},
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


def editor_round(client, prompt, plan, facts, review, state, build_record_fn):
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": build_editor_user(plan, facts, review)},
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


def reviewer(client, prompt, brief_text, plan, record, violations, state):
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": build_reviewer_user(brief_text, plan, record, violations)},
    ]
    for _ in range(2):
        response = call_client(client, messages, state)
        verdict = parse_json(response.text)
        if verdict is not None and isinstance(verdict.get("approved"), bool):
            return verdict
    return None


def violation(slot_id, rule, message):
    return {"slot": slot_id, "rule": rule, "message": message}


def source_contiguous(first, second):
    if first["clip"] != second["clip"]:
        return False
    return (
        abs(first["out_s"] - second["in_s"]) <= CONTIGUOUS_TOLERANCE_S
        or abs(second["out_s"] - first["in_s"]) <= CONTIGUOUS_TOLERANCE_S
    )


def shots_similar(first_key, first_links, second_key, second_links):
    if first_key is None or second_key is None:
        return False
    return second_key in (first_links or []) or first_key in (second_links or [])


def slot_fact(slot, plan_by_id, facts_by_key):
    plan_slot = plan_by_id.get(slot["id"])
    if plan_slot is None:
        return None
    return facts_by_key.get(plan_slot.get("shot_key"))


def validate_edit(record, plan, facts_by_key, important_max_s=DEFAULT_IMPORTANT_MAX_S):
    violations = []
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
    for slot in slots:
        fact = slot_fact(slot, plan_by_id, facts_by_key)
        if fact is None:
            violations.append(violation(slot["id"], "plan", "slot is not in the plan or names an unknown shot"))
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
        if fact.get("keep_whole"):
            expected_in = max(fact["window_in_s"], fact["window_out_s"] - important_max_s)
            if abs(out_s - fact["window_out_s"]) > TOLERANCE_S or in_s > expected_in + TOLERANCE_S:
                violations.append(
                    violation(
                        slot["id"],
                        "keep_whole",
                        f"hero or semantic shot must keep its window up to {important_max_s:.1f}s",
                    )
                )
        else:
            duration = out_s - in_s
            if duration < MIN_SLOT_S - TOLERANCE_S or duration > MAX_SLOT_S + TOLERANCE_S:
                violations.append(
                    violation(
                        slot["id"],
                        "length",
                        f"{duration:.2f}s is outside {MIN_SLOT_S:.1f}-{MAX_SLOT_S:.1f}s",
                    )
                )
    for first, second in zip(slots, slots[1:]):
        if first["clip"] == second["clip"] and not source_contiguous(first, second):
            violations.append(
                violation(
                    second["id"],
                    "same_take",
                    f"same take as {first['id']} without a different take between them",
                )
            )
    for first, second in zip(slots, slots[1:]):
        first_fact = slot_fact(first, plan_by_id, facts_by_key)
        second_fact = slot_fact(second, plan_by_id, facts_by_key)
        if first_fact is None or second_fact is None:
            continue
        if shots_similar(
            first_fact.get("shot_key"),
            first_fact.get("similar_to"),
            second_fact.get("shot_key"),
            second_fact.get("similar_to"),
        ):
            violations.append(
                violation(second["id"], "similar", f"visually similar to {first['id']}")
            )
    return violations


def build_reason(shot, plan_slot):
    parts = []
    for part in (shot.get("reason"), plan_slot.get("why")):
        if part and part not in parts:
            parts.append(part)
    return " · ".join(parts) or "llm director"


def default_window(fact, plan_slot, important_max_s):
    """The cut window the editor spec asks for when the model gave none."""
    window_in = fact["window_in_s"]
    window_out = fact["window_out_s"]
    if fact.get("keep_whole"):
        return max(window_in, window_out - important_max_s), window_out
    target = plan_slot.get("target_s")
    if target is None:
        target = fact.get("target_s")
    if target is None:
        target = window_out - window_in
    target = clamp(float(target), MIN_SLOT_S, MAX_SLOT_S)
    return max(window_in, window_out - target), window_out


def normalise_window(in_s, out_s, fact, plan_slot, important_max_s):
    """Apply the editor spec's length rules when the answer is out of policy."""
    notes = []
    if fact.get("keep_whole"):
        window_in = fact["window_in_s"]
        window_out = fact["window_out_s"]
        expected_in = max(window_in, window_out - important_max_s)
        if abs(out_s - window_out) > TOLERANCE_S or in_s > expected_in + TOLERANCE_S:
            notes.append(f"keep_whole window set to {expected_in:.2f}-{window_out:.2f}")
            return expected_in, window_out, notes
        return in_s, out_s, notes
    window_in = fact["window_in_s"]
    window_out = fact["window_out_s"]
    target = plan_slot.get("target_s")
    if target is None:
        target = fact.get("target_s")
    if target is None:
        target = out_s - in_s
    target = clamp(float(target), MIN_SLOT_S, MAX_SLOT_S)
    duration = out_s - in_s
    if MIN_SLOT_S - TOLERANCE_S <= duration <= MAX_SLOT_S + TOLERANCE_S:
        return in_s, out_s, notes
    new_out = out_s
    new_in = out_s - target
    if new_in < window_in:
        new_in = window_in
        new_out = min(window_out, new_in + target)
    notes.append(f"length set to {new_out - new_in:.2f}s")
    return new_in, new_out, notes


def build_edit_record(plan, answer, facts_by_key, shotlog_by_key, sources, brief_obj, wall_time_s, cost_usd, warnings=None, important_max_s=DEFAULT_IMPORTANT_MAX_S):
    answer_by_id = {}
    for slot in answer.get("slots") or []:
        if isinstance(slot, dict) and slot.get("id"):
            answer_by_id[slot["id"]] = slot
    video = []
    fixed = []
    for plan_slot in plan.get("slot_plan") or []:
        sid = plan_slot["id"]
        ans = answer_by_id.get(sid)
        if ans is None:
            continue
        fact = facts_by_key.get(plan_slot.get("shot_key"))
        if fact is None:
            continue
        try:
            in_s = float(ans["in_s"])
            out_s = float(ans["out_s"])
        except (KeyError, TypeError, ValueError):
            continue
        in_s, out_s, notes = normalise_window(in_s, out_s, fact, plan_slot, important_max_s)
        for note in notes:
            fixed.append(f"fixed {sid}: {note}")
        shot = shotlog_by_key.get(plan_slot.get("shot_key")) or {}
        if not video or ans.get("transition") == "cut":
            transition = {"type": "cut", "duration_s": 0}
        else:
            transition = {"type": "crossfade", "duration_s": XFADE_S}
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
            }
        )
    if warnings is not None:
        for note in fixed:
            if note not in warnings:
                warnings.append(note)
    if not video:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": TOOL,
        "brief": brief_obj,
        "sources": sources,
        "video": video,
        "audio": {
            "ambience": {"enabled": True, "gain_db": 0, "source": "slot", "file": None},
            "music": None,
            "sfx": [],
            "levels": {"target_lufs": None, "true_peak_db": None},
        },
        "captions": [],
        "graphics": [],
        "meta": {"tool": TOOL, "wall_time_s": round(wall_time_s, 2), "llm_cost_usd": round(cost_usd, 8)},
    }


def ensure_all_planned_slots(record, plan, facts_by_key, shotlog_by_key, important_max_s=DEFAULT_IMPORTANT_MAX_S):
    """Put every plan slot back in plan order, synthesising any the editor dropped."""
    existing = {slot["id"]: slot for slot in record.get("video") or [] if slot.get("id")}
    video = []
    added = []
    for plan_slot in plan.get("slot_plan") or []:
        sid = plan_slot["id"]
        slot = existing.get(sid)
        if slot is None:
            fact = facts_by_key.get(plan_slot.get("shot_key"))
            if fact is None:
                continue
            in_s, out_s = default_window(fact, plan_slot, important_max_s)
            shot = shotlog_by_key.get(plan_slot.get("shot_key")) or {}
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
            }
            added.append(sid)
        video.append(slot)
    if added and video:
        video[0]["transition"] = {"type": "cut", "duration_s": 0}
    record["video"] = video
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


def write_edit(path, record, started, cost_usd):
    record["meta"] = {
        "tool": TOOL,
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": round(cost_usd, 8),
    }
    validate_or_raise(record, "edit")
    atomic_write_json(path, record)


def hint_flags(hints):
    flags = []
    if hints.get("target_duration_s") is not None:
        flags += ["--target-duration-s", str(hints["target_duration_s"])]
    if hints.get("order") is not None:
        flags += ["--order", hints["order"]]
    if hints.get("pacing") is not None:
        flags += ["--pacing", hints["pacing"]]
    return flags


def run_rules_director(workspace, brief_text, hints, out_path):
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
        ] + hint_flags(hints)
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise LLMDirectorError(f"rule-based director failed: {result.stderr.strip()}")
        data = load_json(tmp)
    if data is None:
        raise LLMDirectorError("rule-based director wrote no edit")
    atomic_write_json(out_path, data)
    return data


def run_baseline(workspace, brief_text, hints):
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
        ] + hint_flags(hints)
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise LLMDirectorError(f"rule-based director failed: {result.stderr.strip()}")
        data = load_json(tmp)
    if data is None:
        raise LLMDirectorError("rule-based director wrote no edit")
    slots = data.get("video") or []
    total = sum(slot["out_s"] - slot["in_s"] for slot in slots)
    return {
        "slots": len(slots),
        "total_duration_s": round(total, 2),
        "reasons": [slot["reason"] for slot in slots[:5]],
    }


def input_paths(workspace, brief_path, plan_path):
    paths = [workspace / "shotlog.json"]
    if brief_path is not None:
        paths.append(brief_path)
    if plan_path is not None:
        paths.append(plan_path)
    return paths


def is_up_to_date(out_path, workspace, brief_path, plan_path):
    if not out_path.exists():
        return False
    data = load_json(out_path)
    if data is None or validate(data, "edit"):
        return False
    out_mtime = out_path.stat().st_mtime
    for path in input_paths(workspace, brief_path, plan_path):
        if path.exists() and path.stat().st_mtime > out_mtime:
            return False
    return True


def base_summary(status, out_path, plan_path, rounds_used, approved, fallback, slots, total, violations, warnings, started, cost_usd, candidates=0):
    return {
        "status": status,
        "out": str(out_path) if out_path is not None else None,
        "plan": str(plan_path) if plan_path is not None and plan_path.exists() else None,
        "rounds_used": rounds_used,
        "approved": approved,
        "fallback": fallback,
        "slots": slots,
        "candidates": candidates,
        "total_duration_s": round(total, 2),
        "validator_violations": violations,
        "warnings": warnings,
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": round(cost_usd, 8),
    }


def fallback_result(workspace, brief_text, hints, out_path, plan_path, warnings, state, rounds_used, approved, violations, started, candidates=0):
    try:
        data = run_rules_director(workspace, brief_text, hints, out_path)
    except LLMDirectorError as exc:
        warnings.append(str(exc))
        return base_summary("error", out_path, plan_path, rounds_used, approved, None, 0, 0.0, len(violations), warnings, started, state.cost_usd, candidates), 1
    slots = data.get("video") or []
    total = sum(slot["out_s"] - slot["in_s"] for slot in slots)
    return base_summary("fallback", out_path, plan_path, rounds_used, approved, "rules", len(slots), total, len(violations), warnings, started, state.cost_usd, candidates), 0


def stop_result(out_path, plan_path, warnings, state, rounds_used, approved, violations, started, code, candidates=0):
    return base_summary("error", out_path, plan_path, rounds_used, approved, None, 0, 0.0, len(violations), warnings, started, state.cost_usd, candidates), code


def run(
    workspace,
    brief=None,
    brief_file=None,
    out=None,
    plan_out=None,
    rounds=MAX_ROUNDS,
    dry_run=False,
    fallback_rules=True,
    model=None,
    budget_usd=None,
    force=False,
    config=None,
    client=None,
    important_max_s=DEFAULT_IMPORTANT_MAX_S,
    target_duration_s=None,
    order=None,
    pacing=None,
):
    """Run the three-role loop and return ``(summary, exit_code)``."""
    started = time.monotonic()
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
        brief_text = brief_path.read_text(encoding="utf-8")
    else:
        brief_path = None
        brief_text = brief
    if rounds < 1:
        raise LLMDirectorError("--rounds must be at least 1")
    rounds = min(rounds, MAX_ROUNDS)

    out_path = Path(out).expanduser().resolve() if out else workspace_path / "edit.json"
    plan_path = Path(plan_out).expanduser().resolve() if plan_out else workspace_path / "shot_plan.json"

    hints = parse_brief(brief_text)
    if target_duration_s is not None:
        hints["target_duration_s"] = target_duration_s
    if order is not None:
        hints["order"] = order
    if pacing is not None:
        hints["pacing"] = pacing

    if not force and not dry_run and is_up_to_date(out_path, workspace_path, brief_path, plan_path):
        existing = load_json(out_path) or {}
        slots = existing.get("video") or []
        total = sum(slot["out_s"] - slot["in_s"] for slot in slots)
        return base_summary("skipped", out_path, plan_path, 0, None, None, len(slots), total, 0, [], started, 0.0), 0

    warnings = []
    state = LoopState()
    shotlog = load_shotlog(workspace_path)
    if shotlog is None:
        warnings.append("no shotlog.json in the workspace; using the rule-based director")
        if fallback_rules:
            return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, 0, None, [], started)
        return stop_result(out_path, plan_path, warnings, state, 0, None, [], started, 1)

    facts, clips, fact_warnings = build_shot_facts(workspace_path, shotlog, important_max_s)
    warnings.extend(fact_warnings)
    if not facts:
        warnings.append("no eligible shots in the shotlog; using the rule-based director")
        if fallback_rules:
            return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, 0, None, [], started)
        return stop_result(out_path, plan_path, warnings, state, 0, None, [], started, 1)

    sources = [
        {"file": clip["file"], "path": clip.get("path") or clip["file"], "duration_s": clip.get("duration_s")}
        for clip, _ in clips
    ]
    shotlog_rows = [shot for key, shot in shotlog.items() if key in facts]
    candidates = len(shotlog_rows)
    brief_obj = {
        "text": brief_text,
        "target_duration_s": hints.get("target_duration_s"),
        "order": hints.get("order"),
        "pacing": hints.get("pacing"),
    }

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

    content_type = detect_content_type(brief_text, workspace_path)
    screenwriter_prompt = read_prompt("screenwriter.md")
    editor_prompt = read_prompt("editor.md")
    reviewer_prompt = read_prompt("reviewer.md")

    rounds_used = 0
    approved = None
    violations = []
    record = None
    plan = None
    try:
        plan = screenwriter(client, screenwriter_prompt, brief_text, content_type, hints, shotlog_rows, state)
        if plan is None:
            raise LoopFailure("screenwriter produced no valid plan")
        write_plan(plan_path, plan, started, state.cost_usd)
        if dry_run:
            baseline = run_baseline(workspace_path, brief_text, hints)
            summary = base_summary("written", None, plan_path, 0, None, None, 0, 0.0, 0, warnings, started, state.cost_usd, candidates)
            summary["baseline"] = baseline
            summary["dry_run"] = True
            return summary, 0

        def build_record_fn(answer):
            return build_edit_record(
                plan, answer, facts, shotlog, sources, brief_obj, time.monotonic() - started, state.cost_usd, warnings, important_max_s
            )

        review = None
        for round_index in range(rounds):
            rounds_used = round_index + 1
            record = editor_round(client, editor_prompt, plan, facts, review, state, build_record_fn)
            if record is None:
                raise LoopFailure("editor produced no valid edit")
            violations = validate_edit(record, plan, facts, important_max_s)
            verdict = reviewer(client, reviewer_prompt, brief_text, plan, record, violations, state)
            if verdict is None:
                raise LoopFailure("reviewer produced no valid answer")
            if verdict.get("approved") is True and not violations:
                approved = True
                break
            approved = False
            review = verdict
    except BudgetExceeded as exc:
        warnings.append(str(exc))
        if fallback_rules:
            return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, rounds_used, approved, violations, started, candidates)
        return stop_result(out_path, plan_path, warnings, state, rounds_used, approved, violations, started, 2, candidates)
    except LoopFailure as exc:
        warnings.append(str(exc))
        if fallback_rules:
            return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, rounds_used, approved, violations, started, candidates)
        return stop_result(out_path, plan_path, warnings, state, rounds_used, approved, violations, started, 1, candidates)

    if approved:
        write_edit(out_path, record, started, state.cost_usd)
        slots = record["video"]
        total = sum(slot["out_s"] - slot["in_s"] for slot in slots)
        return base_summary("written", out_path, plan_path, rounds_used, True, None, len(slots), total, len(violations), warnings, started, state.cost_usd, candidates), 0

    if violations:
        added = ensure_all_planned_slots(record, plan, facts, shotlog, important_max_s)
        for sid in added:
            note = f"fixed {sid}: slot synthesised from the shot fact"
            if note not in warnings:
                warnings.append(note)
        if added:
            violations = validate_edit(record, plan, facts, important_max_s)

    if not violations:
        warnings.append("reviewer did not approve; keeping the last valid draft")
        write_edit(out_path, record, started, state.cost_usd)
        slots = record["video"]
        total = sum(slot["out_s"] - slot["in_s"] for slot in slots)
        return base_summary("written", out_path, plan_path, rounds_used, False, None, len(slots), total, 0, warnings, started, state.cost_usd, candidates), 0

    warnings.append("reviewer did not approve and the draft still has violations")
    for item in violations:
        note = f"violation {item.get('id')}: {item.get('rule')}: {item.get('message')}"
        if note not in warnings:
            warnings.append(note)
    if fallback_rules:
        return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, rounds_used, approved, violations, started, candidates)
    return stop_result(out_path, plan_path, warnings, state, rounds_used, approved, violations, started, 1, candidates)


def print_human(summary, record):
    if record is not None:
        for slot in record.get("video") or []:
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


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
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
        )
    except (LLMDirectorError, ConfigError, SchemaError, LLMError, OSError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary))
    else:
        record = load_json(summary["out"]) if summary.get("out") else None
        print_human(summary, record)
    if code == 1:
        detail = summary["warnings"][-1] if summary["warnings"] else "run failed"
        print(f"[error] {detail}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
