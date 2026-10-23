#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""LLM director: screenwriter, editor and reviewer loop into edit.json.

Usage:
    python scripts/llm_director.py --workspace DIR --brief "..."
    python scripts/llm_director.py --workspace DIR --brief-file brief.txt --json
    python scripts/llm_director.py --workspace DIR --brief "..." --dry-run
    python scripts/llm_director.py --workspace DIR --brief "..." --rounds 2 --out my_edit.json
    python scripts/llm_director.py --workspace DIR --brief "..." --feedback "drop 0071/s2, tunnel longer"

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
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common.config import ConfigError, load_config
from common.brief_facts import facts_block, load_brief_facts
from common.decisions import build_decisions
from common.feedback import append_feedback
from common.pacing import estimate_all_good
from common.peak import centre_window, choose_peak, is_centred, peak_in_cut
from common.llm import BudgetExceeded, LLMError, make_client
from common import runlog
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
    slot_screen_length,
)
from common.overlap import overlap_seconds
from common.photos import ken_burns_for
from common.schema import SchemaError, validate, validate_or_raise
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME
from common.errors import report_error

TOOL = "scripts/llm_director.py"
SCHEMA_VERSION = "0.1"
REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = REPO_ROOT / "prompts"
DIRECTOR_SCRIPT = REPO_ROOT / "scripts" / "director.py"

MAX_ROUNDS = 3
MIN_SLOT_S = 1.0
MAX_SLOT_S = 12.0
DEFAULT_IMPORTANT_MAX_S = 6.0
LONG_CUT_PACING_S = 180.0
DEFAULT_TARGET_S = 90.0
XFADE_S = 0.3
TOLERANCE_S = 0.05
PHOTO_MIN_S = 2.0
PHOTO_MAX_S = 4.0
PHOTO_DEFAULT_S = 3.0
PHOTO_TOLERANCE_S = 0.01
BEAT_SNAP_FRACTION = 0.5
SNAP_MIN_SLOT_S = 0.5
LANDING_FLOOR_S = 0.75
CENTRE_TOLERANCE_S = 0.3

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
            "  python scripts/llm_director.py --workspace <workspace> --brief \"about 90 seconds, calm start\"\n"
            "  python scripts/llm_director.py --workspace <workspace> --brief-file brief.txt --json\n"
            "  python scripts/llm_director.py --workspace <workspace> --brief \"...\" --dry-run\n"
            "  python scripts/llm_director.py --workspace <workspace> --brief \"...\" --rounds 2 --out my_edit.json\n"
            "  python scripts/llm_director.py --workspace <workspace> --brief \"...\" --feedback \"drop 0071/s2, tunnel longer\"\n"
            "  python scripts/llm_director.py --workspace <workspace> --interpret-feedback \"drop 0071/s2, tunnel longer\" --json\n"
            "  python scripts/llm_director.py --workspace <workspace> --version v3 --interpret-feedback \"make the tunnel longer\"\n"
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
        help="read the previous edit context from versions/vNNN/edit.json (with --interpret-feedback)",
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
        "--speed-ramp",
        action="store_true",
        help=(
            "optional effect: let the middle half of a long camera move play up to 1.2x so more of "
            "the move fits (default off: every slot plays at normal speed)"
        ),
    )
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


def load_ingest_photos(workspace):
    """The still photos listed in ``ingest.json``, or [] when there are none."""
    data = load_json(workspace / "ingest.json")
    if not isinstance(data, dict):
        return []
    photos = data.get("photos") or []
    return [
        photo
        for photo in photos
        if isinstance(photo, dict) and (photo.get("file") or photo.get("path"))
    ]


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


def dedupe_candidates(candidates, drop_reasons=None):
    kept = []
    ordered = sorted(
        candidates,
        key=lambda item: (-item["score"], 0 if item.get("phrase") else 1, item["in_s"]),
    )
    for candidate in ordered:
        clash = next(
            (
                other
                for other in kept
                if overlap_seconds(candidate["in_s"], candidate["out_s"], other["in_s"], other["out_s"]) > 0
            ),
            None,
        )
        if clash is not None:
            if drop_reasons is not None:
                key = candidate.get("shot_key") or candidate_key(candidate)
                drop_reasons.setdefault(
                    key, f"overlapping window of {clash.get('shot_key') or candidate_key(clash)}, lower score"
                )
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


def default_target_s(candidate, important_max_s, slot_min_s=MIN_SLOT_S, slot_max_s=MAX_SLOT_S):
    window = candidate["out_s"] - candidate["in_s"]
    if is_important(candidate):
        return round(min(window, important_max_s), 3)
    return round(clamp(window, slot_min_s, slot_max_s), 3)


def keeps_whole(fact):
    """True when the shot keeps its window up to the hero cap: a hero or semantic move, never a static hold."""
    return bool(fact.get("keep_whole")) and fact.get("motion_label") not in NON_MOVE_MOTION_LABELS


def make_fact(candidate, important_max_s, slot_min_s=MIN_SLOT_S, slot_max_s=MAX_SLOT_S):
    return {
        "shot_key": candidate.get("shot_key"),
        "clip": candidate["clip"],
        "window_in_s": round(float(candidate["in_s"]), 3),
        "window_out_s": round(float(candidate["out_s"]), 3),
        "peak_s": candidate.get("peak_s"),
        "peak_source": candidate.get("peak_source"),
        "landing_at_s": round(float(candidate["landing_start_s"]), 3) if candidate.get("phrase") else None,
        "landing_hold_key": candidate.get("landing_hold_key"),
        "keep_whole": is_important(candidate),
        "target_s": default_target_s(candidate, important_max_s, slot_min_s, slot_max_s),
        "score": candidate.get("score"),
        "summary": candidate.get("summary"),
        "role": candidate.get("role"),
        "motion_label": candidate.get("motion_label"),
        "hero": candidate.get("hero"),
        "similar_to": list(candidate.get("similar_to") or []),
    }


def make_photo_fact(shot, photo):
    """One photo shot as a shot fact: no window, no landing, a fixed 3 s target."""
    return {
        "shot_key": shot.get("key"),
        "media_type": "photo",
        "photo": shot.get("clip") or photo.get("file"),
        "photo_path": photo.get("path") or shot.get("clip"),
        "capture_time": shot.get("capture_time") or photo.get("creation_time"),
        "subject_position": shot.get("subject_position"),
        "tags": list(shot.get("tags") or []),
        "score": shot.get("score"),
        "summary": shot.get("summary"),
        "hero": shot.get("hero"),
        "similar_to": list(shot.get("similar_to") or []),
        "window_in_s": 0.0,
        "window_out_s": 0.04,
        "landing_at_s": None,
        "landing_hold_key": None,
        "keep_whole": False,
        "target_s": PHOTO_DEFAULT_S,
        "role": shot.get("role") or "hold",
        "motion_label": shot.get("motion_label") or "static",
        "description": shot.get("description"),
    }


def build_shot_facts(
    workspace,
    shotlog,
    important_max_s=DEFAULT_IMPORTANT_MAX_S,
    drop_reasons=None,
    slot_min_s=MIN_SLOT_S,
    slot_max_s=MAX_SLOT_S,
):
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
            candidate["peak_s"], candidate["peak_source"] = choose_peak(
                candidate["in_s"], candidate["out_s"], shot.get("peak_s"), (motion or {}).get("scene_activity"),
            )
            scored.append(candidate)
        scored = build_phrases(scored, quality)
        for candidate in dedupe_candidates(scored, drop_reasons):
            key = candidate.get("shot_key")
            if key:
                facts[key] = make_fact(candidate, important_max_s, slot_min_s, slot_max_s)
    photos_by_name = {
        photo.get("file") or Path(photo["path"]).name: photo
        for photo in load_ingest_photos(workspace)
    }
    for shot in shotlog.values():
        if shot.get("media_type") != "photo":
            continue
        if not shot.get("eligible", True):
            continue
        if (shot.get("role") or "hold") in SKIP_ROLES:
            continue
        key = shot.get("key")
        if not key:
            continue
        name = shot.get("clip") or ""
        facts[key] = make_photo_fact(shot, photos_by_name.get(name) or {})
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


def call_client(client, messages, state, role="director"):
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
    return build_feedback_block([text], workspace, edit_path)


def request_reply(client, prompt, user, state):
    """One cheap model call returning the reply, retried once, then a runtime error."""
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user},
    ]
    for _ in range(2):
        response = call_client(client, messages, state, role="metadata")
        answer = parse_json(response.text)
        if isinstance(answer, dict):
            reply = answer.get("reply")
            if isinstance(reply, str) and reply.strip():
                return reply.strip()
    raise LLMDirectorRuntimeError("model produced no usable reply after one retry")


def append_reply_line(workspace, text, reply, estimate_usd, estimate_s):
    """Append one ``action: reply`` line to WORKSPACE/feedback.jsonl (append-only)."""
    append_feedback(
        workspace,
        "reply",
        text=text,
        reply=reply,
        estimate={"usd": estimate_usd, "s": estimate_s},
        tool=TOOL,
    )


def interpret_feedback(workspace, text, version=None, model=None, budget_usd=None, config=None, client=None):
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
    prompt = read_prompt("interpret_feedback.md")
    state = LoopState()
    reply = request_reply(client, prompt, build_interpret_user(text, workspace_path, edit_path), state)
    estimate_usd, estimate_s = last_director_estimate(workspace_path)
    append_reply_line(workspace_path, text, reply, estimate_usd, estimate_s)
    return {
        "reply": reply,
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
        used.append(row)
    rejected = [
        {"id": row.get("id"), "reason": row.get("reason")}
        for row in data.get("decisions") or []
        if isinstance(row, dict) and row.get("status") == "rejected"
    ]
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
    lines.append(json.dumps(previous["used"], indent=2, ensure_ascii=False))
    lines.append(
        "Rejected candidates the feedback may name: "
        + json.dumps(previous["rejected"], ensure_ascii=False)
    )
    return "\n".join(lines)


def build_screenwriter_user(brief_text, content_type, hints, rows, important_max_s, feedback_block=None):
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
    lines += [
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


def build_editor_user(plan, facts, review, pacing=None, feedback_block=None):
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
    lines += [
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


def build_reviewer_user(brief_text, plan, record, violations, pacing=None, feedback_block=None):
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


def screenwriter(client, prompt, brief_text, content_type, hints, rows, state, important_max_s, feedback_block=None):
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": build_screenwriter_user(brief_text, content_type, hints, rows, important_max_s, feedback_block),
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


def editor_round(client, prompt, plan, facts, review, state, build_record_fn, feedback_block=None):
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": build_editor_user(plan, facts, review, plan_pacing(plan), feedback_block)},
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


def reviewer(client, prompt, brief_text, plan, record, violations, state, feedback_block=None):
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": build_reviewer_user(
                brief_text, plan, record, violations, plan_pacing(plan), feedback_block
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


def fact_peak(fact):
    """The shot's best moment: its ``peak_s``, else the middle of its window."""
    if fact.get("peak_s") is not None:
        return float(fact["peak_s"])
    return (float(fact["window_in_s"]) + float(fact["window_out_s"])) / 2.0


def landing_floor(fact):
    """The earliest end of a cut that lands on a hold, or None."""
    landing = fact.get("landing_at_s")
    return None if landing is None else float(landing) + LANDING_FLOOR_S


def expected_cut(fact, length_s):
    """The ``(in_s, out_s)`` of a ``length_s`` cut centred on the shot's peak, inside its window."""
    window_in = float(fact["window_in_s"])
    window_out = float(fact["window_out_s"])
    length = min(float(length_s), window_out - window_in)
    return centre_window(window_in, window_out, length, fact_peak(fact), landing_floor(fact))


def is_trimmed_cut(in_s, out_s, fact):
    """True when the cut is shorter than the shot's window."""
    return (out_s - in_s) < (float(fact["window_out_s"]) - float(fact["window_in_s"])) - TOLERANCE_S


def required_star_keys(facts_by_key, n_slots):
    """The top stars an edit must keep: heroes or scores >= STAR_MIN_SCORE, best first.

    At most ``STAR_SHARE`` of the slots' worth of stars, at least one. Photos
    keep their own non-blocking ``hero`` rule and are never required here.
    """
    stars = [
        (key, fact)
        for key, fact in facts_by_key.items()
        if fact.get("media_type") != "photo" and is_star(fact)
    ]
    stars.sort(key=lambda item: (-float(item[1].get("score") or 0.0), item[0]))
    budget = max(1, round(STAR_SHARE * max(int(n_slots), 1)))
    return [key for key, _ in stars[:budget]]


def validate_edit(record, plan, facts_by_key, important_max_s=DEFAULT_IMPORTANT_MAX_S):
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
    for key in required_star_keys(facts_by_key, star_budget_source):
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
        if keeps_whole(fact):
            expected_in, expected_out = expected_cut(fact, important_max_s)
            if abs(in_s - expected_in) > CENTRE_TOLERANCE_S or abs(out_s - expected_out) > CENTRE_TOLERANCE_S:
                violations.append(
                    violation(
                        slot["id"],
                        "keep_whole",
                        f"hero or semantic shot must keep its window up to {important_max_s:.1f}s, centred on "
                        f"the best moment at {fact_peak(fact):.2f}s: expected {expected_in:.2f}-{expected_out:.2f}",
                    )
                )
        else:
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


def slot_duration(slot):
    """The on-screen length of an edit.json slot, video or photo."""
    if slot.get("kind") == "photo":
        return slot.get("duration_s") or 0.0
    return slot_screen_length(slot)


def load_beats(path):
    """The beat grid from ``beats.py``: ``measurement`` nested or the keys at the top."""
    data = load_json(Path(path))
    if not isinstance(data, dict):
        raise LLMDirectorError(f"unreadable beats file: {path}")
    measurement = data.get("measurement")
    source = measurement if isinstance(measurement, dict) else data
    beat_s = [float(value) for value in (source.get("beat_s") or [])]
    downbeat_s = [float(value) for value in (source.get("downbeat_s") or [])]
    period = source.get("beat_period_s")
    period = float(period) if period else derive_beat_period(beat_s)
    if not beat_s or period <= 0:
        raise LLMDirectorError(f"beats file has no beat grid: {path}")
    return {"beat_s": beat_s, "downbeat_s": downbeat_s or list(beat_s), "beat_period_s": period}


def derive_beat_period(beat_s):
    """The median spacing of a beat list, for grids written without ``beat_period_s``."""
    if len(beat_s) < 2:
        return 0.0
    spacings = sorted(later - earlier for earlier, later in zip(beat_s, beat_s[1:]))
    return spacings[len(spacings) // 2]


def nearest_beat(value, times):
    if not times:
        return None
    return min(times, key=lambda moment: abs(moment - value))


def snap_warning(cut_number, end, target):
    if target is None:
        return f"cut {cut_number}: not snapped to a beat (no beat grid)"
    return f"cut {cut_number}: not snapped to a beat (nearest {abs(end - target):.1f} s away)"


def slot_bounds(slot, fact, cap=None):
    """The range a snap may move this slot's end within: photo length or video window.

    ``cap`` is the longest on-screen length the move policy allows the slot.
    """
    if fact is not None and fact.get("media_type") == "photo":
        return {"photo": True, "important": False, "cap": cap}
    if slot.get("kind") == "photo":
        return {"photo": True, "important": False, "cap": cap}
    low = float(slot["in_s"]) + SNAP_MIN_SLOT_S
    high = float(slot["out_s"])
    important = False
    window_in = float(slot["in_s"])
    if fact is not None:
        high = float(fact.get("window_out_s", slot["out_s"]))
        important = keeps_whole(fact)
        window_in = float(fact.get("window_in_s", slot["in_s"]))
        if fact.get("landing_at_s") is not None:
            low = max(low, float(fact["landing_at_s"]) + LANDING_FLOOR_S)
    return {"photo": False, "important": important, "low": low, "high": high, "window_in": window_in, "cap": cap}


def over_cap(slot, bounds, delta):
    """True when lengthening the slot on screen by ``delta`` would pass its policy cap."""
    cap = bounds.get("cap")
    if cap is None or delta <= 0:
        return False
    return slot_duration(slot) + delta > float(cap) + 1e-9


def source_delta(slot, delta):
    """The source-time change that moves the slot's on-screen length by ``delta``."""
    return delta / screen_factor(middle_speed_of(slot))


def _relaxed(bounds, slack):
    """The bounds of a slot with the beat-snap slack applied."""
    low = float(bounds["low"])
    high = float(bounds["high"])
    window_in = float(bounds.get("window_in", low))
    if slack:
        low -= slack
        high += slack
        window_in -= slack
    return low, high, window_in


def absorb_out(slot, bounds, delta, slack=0.0):
    """Move a slot's end by ``delta`` within its bounds; False when it cannot.

    ``slack`` lets a hero or semantic-move slot nudge its cut by up to half a
    beat past its window so the cut still lands on the music.
    """
    if over_cap(slot, bounds, delta):
        return False
    if bounds.get("photo"):
        target = float(slot["duration_s"]) + delta
        if not (PHOTO_MIN_S - 1e-9 <= target <= PHOTO_MAX_S + 1e-9):
            return False
        slot["duration_s"] = round(target, 3)
        return True
    low, high, _ = _relaxed(bounds, slack)
    target = float(slot["out_s"]) + source_delta(slot, delta)
    if not (low - 1e-9 <= target <= high + 1e-9):
        return False
    slot["out_s"] = round(target, 3)
    return True


def absorb_length(slot, bounds, delta, slack=0.0):
    """Change a slot's on-screen length by ``delta``, from its end or its head.

    The end first; when the end is pinned the head moves instead, never before
    the window's start. False when neither fits.
    """
    if absorb_out(slot, bounds, delta, slack):
        return True
    if bounds.get("photo"):
        return False
    low, high, window_in = _relaxed(bounds, slack)
    head_target = float(slot["in_s"]) - source_delta(slot, delta)
    if head_target < window_in - 1e-9:
        return False
    if float(slot["out_s"]) - head_target < SNAP_MIN_SLOT_S - 1e-9:
        return False
    slot["in_s"] = round(head_target, 3)
    return True


def absorb_head(slot, bounds, delta, slack=0.0):
    """Change a slot's length by ``delta`` from its head only; False when it cannot.

    The head move keeps the slot's end (and every cut after it) in place, which
    is what the pair absorb needs from the right slot.
    """
    if bounds.get("photo"):
        return absorb_out(slot, bounds, delta, slack)
    if over_cap(slot, bounds, delta):
        return False
    _, _, window_in = _relaxed(bounds, slack)
    head_target = float(slot["in_s"]) - source_delta(slot, delta)
    if head_target < window_in - 1e-9:
        return False
    if float(slot["out_s"]) - head_target < SNAP_MIN_SLOT_S - 1e-9:
        return False
    slot["in_s"] = round(head_target, 3)
    return True


def absorb_pair(left, left_bounds, right, right_bounds, delta, slack=0.0):
    """Move the cut between ``left`` and ``right`` by ``delta`` and nothing else.

    ``left`` grows by ``delta`` (from its end or its head) and ``right`` gives
    the same length back from its head, so only their shared boundary moves.
    Both must fit; on a partial fit the left change is restored exactly.
    """
    snapshot = {key: left[key] for key in ("in_s", "out_s", "duration_s") if key in left}
    if not absorb_length(left, left_bounds, delta, slack):
        return False
    if not absorb_head(right, right_bounds, -delta, slack):
        left.update(snapshot)
        return False
    return True


def snap_photo(slot, start, times, cap=None):
    """Put a photo's end on a beat that keeps its length in 2-4 s; False when none fits."""
    current = float(slot["duration_s"])
    longest = min(PHOTO_MAX_S, float(cap)) if cap is not None else PHOTO_MAX_S
    fitting = [
        moment
        for moment in times
        if PHOTO_MIN_S - 1e-9 <= moment - start <= longest + 1e-9
    ]
    if not fitting:
        return False
    target = min(fitting, key=lambda moment: abs(moment - (start + current)))
    slot["duration_s"] = round(target - start, 3)
    return True


def snap_slots(slots, bounds, beat_grid, downbeat_at, warnings):
    """Snap every timeline cut to the nearest beat, in place; return the summary counts."""
    beat_s = beat_grid["beat_s"]
    downbeat_s = beat_grid["downbeat_s"] or beat_s
    tolerance = BEAT_SNAP_FRACTION * beat_grid["beat_period_s"]
    snapped = 0
    unsnapped = 0
    start = 0.0
    for index, slot in enumerate(slots):
        if index > 0:
            slot["transition"] = {"type": "cut", "duration_s": 0}
        end = start + slot_duration(slot)
        times = downbeat_s if downbeat_at[index] else beat_s
        target = nearest_beat(end, times)
        reach = tolerance
        cap = bounds[index].get("cap")
        if target is not None and cap is not None and target - start > float(cap) + 1e-9:
            within = [moment for moment in times if moment - start <= float(cap) + 1e-9]
            target = max(within) if within else None
            reach = 2 * tolerance
        if target is None or abs(end - target) > reach:
            warnings.append(snap_warning(index + 1, end, target))
            unsnapped += 1
            start = end
            continue
        if bounds[index].get("photo"):
            if not snap_photo(slot, start, times, bounds[index].get("cap")):
                warnings.append(snap_warning(index + 1, end, target))
                unsnapped += 1
                start = end
                continue
            snapped += 1
            start += slot_duration(slot)
            continue
        delta = target - end
        if abs(delta) < 1e-9 or absorb_out(slot, bounds[index], delta, tolerance):
            snapped += 1
            start += slot_duration(slot)
            continue
        if index + 1 < len(slots):
            moved = absorb_pair(slot, bounds[index], slots[index + 1], bounds[index + 1], delta, tolerance)
        else:
            moved = absorb_length(slot, bounds[index], delta, tolerance)
        if moved:
            snapped += 1
            start += slot_duration(slot)
            continue
        warnings.append(snap_warning(index + 1, end, target))
        unsnapped += 1
        start = end
    return {"beats": len(beat_s), "snapped": snapped, "unsnapped": unsnapped}


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


def finalize_lengths(record, plan, facts_by_key, beat_grid, warnings, important_max_s, speed_ramp=False):
    """Run the length policy, then snap to the beat grid when there is one; return the snap summary."""
    caps = apply_length_policy(
        record, plan, facts_by_key, plan_pacing(plan), important_max_s, warnings, speed_ramp
    )
    summary = None
    if beat_grid is not None:
        summary = apply_beat_snap(record, plan, facts_by_key, beat_grid, warnings, caps)
    annotate_peaks(record, plan, facts_by_key)
    record.setdefault("meta", {})["speed_ramp"] = bool(speed_ramp)
    return summary


def finish_record(record, plan, facts_by_key, shotlog_by_key, beat_grid, warnings, important_max_s, speed_ramp=False):
    """Length policy, then grow back to the brief length, then police the added slots."""
    beat_snap = finalize_lengths(record, plan, facts_by_key, beat_grid, warnings, important_max_s, speed_ramp)
    fill_notes = fill_to_target(record, plan, facts_by_key, shotlog_by_key, important_max_s, [])
    for note in fill_notes:
        if note not in warnings:
            warnings.append(note)
    if fill_notes:
        beat_snap = finalize_lengths(record, plan, facts_by_key, beat_grid, warnings, important_max_s, speed_ramp)
    return beat_snap


def apply_beat_snap(record, plan, facts_by_key, beat_grid, warnings, caps=None):
    """Snap a record's cuts to the grid; a cut that starts a new plan beat snaps to a downbeat."""
    slots = record.get("video") or []
    plan_by_id = {slot.get("id"): slot for slot in plan.get("slot_plan") or []}
    plan_beat = {slot_id: entry.get("beat") for slot_id, entry in plan_by_id.items()}
    downbeat_at = []
    for index, slot in enumerate(slots):
        current = plan_beat.get(slot.get("id"))
        following = plan_beat.get(slots[index + 1].get("id")) if index + 1 < len(slots) else None
        downbeat_at.append(
            bool(index + 1 < len(slots) and following is not None and current is not None and following != current)
        )
    bounds = []
    for slot in slots:
        plan_slot = plan_by_id.get(slot.get("id")) or {}
        fact = facts_by_key.get(plan_slot.get("shot_key"))
        bounds.append(slot_bounds(slot, fact, (caps or {}).get(slot.get("id"))))
    return snap_slots(slots, bounds, beat_grid, downbeat_at, warnings)


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
    if keeps_whole(fact):
        expected_in, expected_out = expected_cut(fact, important_max_s)
        if abs(in_s - expected_in) > CENTRE_TOLERANCE_S or abs(out_s - expected_out) > CENTRE_TOLERANCE_S:
            notes.append(
                f"keep_whole window set to {expected_in:.2f}-{expected_out:.2f} (best moment {fact_peak(fact):.2f}s)"
            )
            return expected_in, expected_out, notes
        return in_s, out_s, notes
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


REBUILT_RULES = {"window", "keep_whole", "peak", "length", "landing", "photo_length"}
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


def pick_swap_victim(record, plan, facts_by_key, star_fact, important_max_s, slot_min_s, slot_max_s):
    """The id of the weakest non-star slot the star can replace without a jump cut."""
    plan_by_id = {slot["id"]: slot for slot in plan.get("slot_plan") or []}
    slots = record.get("video") or []
    star_key = star_fact.get("shot_key")
    best = None
    for index, slot in enumerate(slots):
        plan_slot = plan_by_id.get(slot.get("id"))
        if plan_slot is None:
            continue
        key = plan_slot.get("shot_key") or slot.get("shot_key")
        if key is None or key == star_key:
            continue
        victim_fact = facts_by_key.get(key) or {}
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


def drop_jump_cuts(record, plan, facts_by_key, shotlog_by_key, important_max_s, notes):
    """Drop the weaker shot of every same-take jump-cut pair until none is left."""
    for _ in range(len(plan.get("slot_plan") or [])):
        pairs = [
            item for item in validate_edit(record, plan, facts_by_key, important_max_s)
            if item.get("rule") == "same_take"
        ]
        if not pairs:
            break
        plan_by_id = {slot["id"]: slot for slot in plan.get("slot_plan") or []}
        weaker = weaker_of_pair(record, pairs[0].get("slot"), plan_by_id, shotlog_by_key)
        if weaker is None:
            break
        drop_from_plan(plan, {weaker}, "jump cut with its neighbour from the same take", notes)
        planned = {row["id"] for row in plan.get("slot_plan") or []}
        record["video"] = [slot for slot in record.get("video") or [] if slot.get("id") in planned]
        ensure_all_planned_slots(record, plan, facts_by_key, shotlog_by_key, important_max_s)


def repair_missing_stars(record, plan, facts_by_key, shotlog_by_key, important_max_s, notes):
    """Swap every missing top star in for the weakest non-star slot of a compatible length."""
    pacing = plan_pacing(plan)
    slot_min_s = pacing["slot_min"]
    slot_max_s = pacing["slot_max"]
    for _ in range(len(facts_by_key) + 1):
        missing = [
            item.get("slot")
            for item in validate_edit(record, plan, facts_by_key, important_max_s)
            if item.get("rule") == "hero_missing"
        ]
        if not missing:
            break
        star_key = missing[0]
        star_fact = facts_by_key.get(star_key)
        if star_fact is None:
            break
        victim_id = pick_swap_victim(
            record, plan, facts_by_key, star_fact, important_max_s, slot_min_s, slot_max_s
        )
        if victim_id is None:
            break
        swap_star_in(
            record, plan, facts_by_key, shotlog_by_key, star_key, victim_id,
            important_max_s, slot_min_s, slot_max_s, notes,
        )


TARGET_SLACK_S = 3.0


def edit_order_key(fact):
    """Chronological sort key of a shot: the clip's 4-digit counter, then its window start."""
    counter = clip_counter(fact.get("clip") or fact.get("photo") or "")
    return (counter, float(fact.get("window_in_s") or 0.0))


def next_slot_id(plan):
    """A free plan slot id like ``v13``."""
    used = {row.get("id") for row in plan.get("slot_plan") or []}
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


def fill_to_target(record, plan, facts_by_key, shotlog_by_key, important_max_s, notes):
    """Add unused strong shots as slots until the edit reaches the brief's target length.

    The screenwriter may plan fewer slots than the duration needs; this pass
    grows the edit with the best unused cuttable shots in chronological order,
    never creating a same-take jump cut or an adjacent similar pair. It is a
    repair of the draft the model wrote, not a fallback to the rule director.
    """
    brief = plan.get("brief") if isinstance(plan, dict) else None
    target = brief.get("target_duration_s") if isinstance(brief, dict) else None
    if not target:
        return notes
    pacing = plan_pacing(plan)
    slot_min_s = pacing["slot_min"]
    slot_max_s = pacing["slot_max"]
    used_keys = {row.get("shot_key") for row in plan.get("slot_plan") or []}
    ranked = sorted(
        (
            (key, fact) for key, fact in facts_by_key.items()
            if key not in used_keys and fact.get("media_type") != "photo"
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
        sid = next_slot_id(plan)
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


def repair_draft(record, plan, facts_by_key, shotlog_by_key, violations, important_max_s=DEFAULT_IMPORTANT_MAX_S):
    """Fix a draft's mechanical violations from the shot facts instead of discarding the draft.

    First pass: slots that are not in the plan are dropped, a landing hold
    planned again as its own slot leaves the plan, and a slot whose cut breaks
    a window, length, peak, keep-whole or landing rule is rebuilt from its shot
    fact the way the editor spec defines it. Second pass: of two same-take
    slots that would jump-cut, the lower-scoring one leaves the plan. Third
    pass: a missing top star is swapped in for the weakest non-star slot of a
    compatible length that does not jump-cut its neighbours. Returns the
    plain-word notes of what changed.
    """
    notes = []
    plan_by_id = {slot["id"]: slot for slot in plan.get("slot_plan") or []}
    rebuild = set()
    drop_keys = set()
    for item in violations:
        slot_id = item.get("slot")
        rule = item.get("rule")
        if rule == "landing" and "planned again" in (item.get("message") or ""):
            slot = next((row for row in record.get("video") or [] if row.get("id") == slot_id), None)
            fact = slot_fact(slot, plan_by_id, facts_by_key) if slot else None
            if fact and fact.get("landing_hold_key"):
                drop_keys.add(fact["landing_hold_key"])
                continue
        if rule in REBUILT_RULES and slot_id in plan_by_id:
            rebuild.add(slot_id)
    drop_from_plan(plan, drop_keys, "landing hold planned as its own slot", notes)
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
        item.get("slot") for item in validate_edit(record, plan, facts_by_key, important_max_s)
        if item.get("rule") in REBUILT_RULES
    }
    if still_bad:
        plan_by_id = {slot["id"]: slot for slot in plan.get("slot_plan") or []}
        keys = {plan_by_id[slot_id].get("shot_key") for slot_id in still_bad if slot_id in plan_by_id}
        drop_from_plan(plan, keys, "its window cannot give a valid cut", notes)
        planned = {row["id"] for row in plan.get("slot_plan") or []}
        record["video"] = [slot for slot in record.get("video") or [] if slot.get("id") in planned]
        ensure_all_planned_slots(record, plan, facts_by_key, shotlog_by_key, important_max_s)
    drop_jump_cuts(record, plan, facts_by_key, shotlog_by_key, important_max_s, notes)
    repair_missing_stars(record, plan, facts_by_key, shotlog_by_key, important_max_s, notes)
    drop_jump_cuts(record, plan, facts_by_key, shotlog_by_key, important_max_s, notes)
    fill_to_target(record, plan, facts_by_key, shotlog_by_key, important_max_s, notes)
    drop_jump_cuts(record, plan, facts_by_key, shotlog_by_key, important_max_s, notes)
    return notes


def drop_from_plan(plan, keys, why, notes):
    keys = {key for key in keys if key}
    if not keys:
        return
    plan["slot_plan"] = [row for row in plan.get("slot_plan") or [] if row.get("shot_key") not in keys]
    notes.extend(f"repaired: dropped {key} from the plan ({why})" for key in sorted(keys))


def weaker_of_pair(record, slot_id, plan_by_id, shotlog_by_key):
    """The shot key of the lower-scoring slot of a same-take pair ending at ``slot_id``."""
    video = record.get("video") or []
    index = next((position for position, slot in enumerate(video) if slot.get("id") == slot_id), None)
    if index is None or index == 0:
        return None
    pair = [video[index - 1], video[index]]

    def score(slot):
        key = (plan_by_id.get(slot.get("id")) or {}).get("shot_key")
        return float((shotlog_by_key.get(key) or {}).get("score") or slot.get("score") or 0.0), key

    return min((score(slot) for slot in pair), key=lambda item: item[0])[1]


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


def review_object(verdict, record):
    """``meta.director.review`` from the reviewer's last verdict, or None when it is not a dict."""
    if not isinstance(verdict, dict):
        return None
    approved = bool(verdict.get("approved"))
    objections = []
    if not approved:
        shot_positions = {
            slot.get("id"): index + 1 for index, slot in enumerate(record.get("video") or [])
        }
        for fix in verdict.get("fixes") or []:
            if not isinstance(fix, dict):
                continue
            text = objection_text(fix.get("problem"), fix.get("change"))
            if not text:
                continue
            slot = fix.get("slot")
            slot = slot if isinstance(slot, str) and slot else None
            objections.append(
                {"slot": slot, "shot": shot_positions.get(slot) if slot is not None else None, "text": text}
            )
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


def run_rules_director(workspace, brief_text, hints, out_path, beats_path=None, music=None, ambient_under_db=None, speed_ramp=False):
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
    return data, summary.get("beat_snap")


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


def base_summary(status, out_path, plan_path, rounds_used, approved, fallback, slots, total, violations, warnings, started, cost_usd, candidates=0, beat_snap=None):
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
        "beat_snap": beat_snap,
    }


def fallback_result(workspace, brief_text, hints, out_path, plan_path, warnings, state, rounds_used, approved, violations, started, candidates=0, beats_path=None, music=None, ambient_under_db=None, speed_ramp=False):
    try:
        data, beat_snap = run_rules_director(workspace, brief_text, hints, out_path, beats_path, music, ambient_under_db, speed_ramp)
        data.setdefault("meta", {})["director"] = director_info("rules", approved, warnings)
        validate_or_raise(data, "edit")
        atomic_write_json(out_path, data)
    except LLMDirectorError as exc:
        warnings.append(str(exc))
        return base_summary("error", out_path, plan_path, rounds_used, approved, None, 0, 0.0, len(violations), warnings, started, state.cost_usd, candidates), 1
    slots = data.get("video") or []
    total = sum(slot_duration(slot) for slot in slots)
    return base_summary("fallback", out_path, plan_path, rounds_used, approved, "rules", len(slots), total, len(violations), warnings, started, state.cost_usd, candidates, beat_snap), 0


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
    important_max_s=None,
    target_duration_s=None,
    order=None,
    pacing=None,
    beats=None,
    music=None,
    ambient_under_music_db=None,
    feedback=None,
    speed_ramp=False,
):
    """Run the three-role loop and return ``(summary, exit_code)``."""
    summary, code = _run_loop(
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
    )
    summary["feedback"] = list(feedback or [])
    return summary, code


def _run_loop(
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
    important_max_s=None,
    target_duration_s=None,
    order=None,
    pacing=None,
    beats=None,
    music=None,
    ambient_under_music_db=None,
    feedback=None,
    speed_ramp=False,
):
    """Run the three-role loop body; ``run`` adds the ``feedback`` summary key."""
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
    feedback = list(feedback or [])
    if feedback:
        append_feedback_lines(workspace_path, feedback)
    feedback_block = build_feedback_block(feedback, workspace_path) if feedback else None

    out_path = Path(out).expanduser().resolve() if out else workspace_path / "edit.json"
    plan_path = Path(plan_out).expanduser().resolve() if plan_out else workspace_path / "shot_plan.json"
    beats_path = Path(beats).expanduser().resolve() if beats else None
    if music:
        music_path = Path(music).expanduser().resolve()
        if not music_path.is_file():
            raise LLMDirectorError(f"music file not found: {music}")
        music = str(music_path)
    beat_grid = load_beats(beats_path) if beats_path is not None else None

    hints = parse_brief(brief_text)
    facts_block_text = facts_block(load_brief_facts(workspace_path))
    if facts_block_text:
        brief_text = brief_text.rstrip() + "\n\n" + facts_block_text
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
        if fallback_rules:
            return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, 0, None, [], started, beats_path=beats_path, music=music, ambient_under_db=ambient_under_music_db, speed_ramp=speed_ramp)
        return stop_result(out_path, plan_path, warnings, state, 0, None, [], started, EXIT_USAGE)

    drop_reasons = {}
    pacing = run_pacing(LONG_CUT_PACING_S if all_good else hints.get("target_duration_s"))
    effective_important_max_s = important_max_s if important_max_s is not None else pacing["hero_max"]
    facts, clips, fact_warnings = build_shot_facts(
        workspace_path, shotlog, effective_important_max_s, drop_reasons, pacing["slot_min"], pacing["slot_max"],
    )
    warnings.extend(fact_warnings)
    if not facts:
        warnings.append("no eligible shots in the shotlog; using the rule-based director")
        if fallback_rules:
            return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, 0, None, [], started, beats_path=beats_path, music=music, ambient_under_db=ambient_under_music_db, speed_ramp=speed_ramp)
        return stop_result(out_path, plan_path, warnings, state, 0, None, [], started, EXIT_USAGE)

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
        plan = screenwriter(client, screenwriter_prompt, brief_text, content_type, hints, shotlog_rows, state, effective_important_max_s, feedback_block)
        if plan is None:
            raise LoopFailure("screenwriter produced no valid plan")
        write_plan(plan_path, plan, started, state.cost_usd)
        if dry_run:
            baseline = run_baseline(workspace_path, brief_text, hints, beats_path, music, ambient_under_music_db, speed_ramp)
            summary = base_summary("written", None, plan_path, 0, None, None, 0, 0.0, 0, warnings, started, state.cost_usd, candidates)
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
        for round_index in range(rounds):
            rounds_used = round_index + 1
            record = editor_round(client, editor_prompt, plan, facts, review, state, build_record_fn, feedback_block)
            if record is None:
                raise LoopFailure("editor produced no valid edit")
            violations = validate_edit(record, plan, facts, effective_important_max_s)
            verdict = reviewer(client, reviewer_prompt, brief_text, plan, record, violations, state, feedback_block)
            if verdict is None:
                raise LoopFailure("reviewer produced no valid answer")
            last_verdict = verdict
            if verdict.get("approved") is True and not violations:
                approved = True
                break
            approved = False
            review = verdict
    except BudgetExceeded as exc:
        warnings.append(str(exc))
        if fallback_rules:
            return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, rounds_used, approved, violations, started, candidates, beats_path, music, ambient_under_music_db, speed_ramp)
        return stop_result(out_path, plan_path, warnings, state, rounds_used, approved, violations, started, EXIT_RUNTIME, candidates)
    except LoopFailure as exc:
        warnings.append(str(exc))
        if fallback_rules:
            return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, rounds_used, approved, violations, started, candidates, beats_path, music, ambient_under_music_db, speed_ramp)
        return stop_result(out_path, plan_path, warnings, state, rounds_used, approved, violations, started, EXIT_RUNTIME, candidates)

    if approved:
        for note in fill_to_target(record, plan, facts, shotlog, effective_important_max_s, []):
            if note not in warnings:
                warnings.append(note)
        leftover = validate_edit(record, plan, facts, effective_important_max_s)
        if blocking(leftover):
            for note in repair_draft(record, plan, facts, shotlog, leftover, effective_important_max_s):
                if note not in warnings:
                    warnings.append(note)
        beat_snap = finish_record(record, plan, facts, shotlog, beat_grid, warnings, effective_important_max_s, speed_ramp)
        attach_decisions(record, facts, shotlog, drop_reasons)
        try:
            write_edit(out_path, record, started, state.cost_usd, director_info("llm", True, warnings, review_object(last_verdict, record)))
        except SchemaError as exc:
            warnings.append(str(exc))
            if fallback_rules:
                return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, rounds_used, approved, violations, started, candidates, beats_path, music, ambient_under_music_db, speed_ramp)
            return stop_result(out_path, plan_path, warnings, state, rounds_used, approved, violations, started, EXIT_RUNTIME, candidates)
        slots = record["video"]
        total = sum(slot_duration(slot) for slot in slots)
        return base_summary("written", out_path, plan_path, rounds_used, True, None, len(slots), total, len(violations), warnings, started, state.cost_usd, candidates, beat_snap), 0

    if violations:
        added = ensure_all_planned_slots(record, plan, facts, shotlog, effective_important_max_s)
        for sid in added:
            note = f"fixed {sid}: slot synthesised from the shot fact"
            if note not in warnings:
                warnings.append(note)
        if added:
            violations = validate_edit(record, plan, facts, effective_important_max_s)
    if blocking(violations):
        for note in repair_draft(record, plan, facts, shotlog, violations, effective_important_max_s):
            if note not in warnings:
                warnings.append(note)
        violations = validate_edit(record, plan, facts, effective_important_max_s)
        if record.get("video"):
            write_plan(plan_path, plan, started, state.cost_usd)
    for item in violations:
        if item.get("rule") in NON_BLOCKING_RULES and violation_note(item) not in warnings:
            warnings.append(violation_note(item))
    violations = blocking(violations)

    if not violations and record.get("video"):
        warnings.append("reviewer did not approve; keeping the last valid draft")
        beat_snap = finish_record(record, plan, facts, shotlog, beat_grid, warnings, effective_important_max_s, speed_ramp)
        attach_decisions(record, facts, shotlog, drop_reasons)
        try:
            write_edit(out_path, record, started, state.cost_usd, director_info("llm", False, warnings, review_object(last_verdict, record)))
        except SchemaError as exc:
            warnings.append(str(exc))
            if fallback_rules:
                return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, rounds_used, approved, violations, started, candidates, beats_path, music, ambient_under_music_db, speed_ramp)
            return stop_result(out_path, plan_path, warnings, state, rounds_used, approved, violations, started, EXIT_RUNTIME, candidates)
        slots = record["video"]
        total = sum(slot_duration(slot) for slot in slots)
        return base_summary("written", out_path, plan_path, rounds_used, False, None, len(slots), total, 0, warnings, started, state.cost_usd, candidates, beat_snap), 0

    warnings.append("reviewer did not approve and the draft still has violations")
    for item in violations:
        note = violation_note(item)
        if note not in warnings:
            warnings.append(note)
    if fallback_rules:
        return fallback_result(workspace_path, brief_text, hints, out_path, plan_path, warnings, state, rounds_used, approved, violations, started, candidates, beats_path, music, ambient_under_music_db, speed_ramp)
    return stop_result(out_path, plan_path, warnings, state, rounds_used, approved, violations, started, EXIT_RUNTIME, candidates)


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
    estimate = []
    if summary["estimate_usd"] is not None:
        estimate.append(f"${summary['estimate_usd']:.4f}")
    if summary["estimate_s"] is not None:
        estimate.append(f"{summary['estimate_s']:.1f}s")
    if estimate:
        print("estimate: " + ", ".join(estimate))


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.interpret_feedback is not None:
            summary, code = interpret_feedback(
                args.workspace,
                args.interpret_feedback,
                version=args.version,
                model=args.model,
                budget_usd=args.budget_usd,
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
            )
    except (LLMDirectorRuntimeError, LLMError, OSError) as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except (LLMDirectorError, ConfigError, SchemaError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)
    if args.json:
        print(json.dumps(summary))
    elif args.interpret_feedback is not None:
        print_interpret(summary)
    else:
        record = load_json(summary["out"]) if summary.get("out") else None
        print_human(summary, record)
    if code != EXIT_OK:
        detail = summary["warnings"][-1] if summary["warnings"] else "run failed"
        print(f"[error] {detail}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
