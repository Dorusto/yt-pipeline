#!/usr/bin/env python3
"""Rule-based director: pick cut windows from the shotlog and the labels.

Usage:
    python scripts/director.py --workspace DIR
    python scripts/director.py --workspace DIR --brief "..." --target-duration-s 90 --json
    python scripts/director.py --workspace DIR --order score --pacing uniform --force

Reads ``ingest.json``, ``motion/<stem>.json``, ``quality/<stem>.json`` and
``shotlog.json`` from the workspace and writes ``edit.json`` (or ``--out``)
validated against ``schemas/edit.schema.json``. When the shotlog is present the
fused content score, the description and the reason come from it; otherwise the
director falls back to scoring the labels. No model is called. Sources are
read-only; the output is written atomically.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common.photos import ken_burns_for
from common.schema import SchemaError, validate, validate_or_raise

TOOL = "scripts/director.py"
SCHEMA_VERSION = "0.1"

CLIP_COUNTER = re.compile(r"_(\d{4})_")
SKIP_ROLES = {"accidental", "setup", "teardown"}
ROLE_BASE = {"hold": 0.55, "move": 0.50, "transition_in": 0.35, "transition_out": 0.35}
STEADINESS_BONUS = {
    "no_shaking": 0.15,
    "minimal_shaking": 0.08,
    "static": 0.10,
    "unsteady": -0.10,
    "very_unsteady": -0.20,
}
DEFECT_WEIGHTS = {"horizon": 0.5}
DEFAULT_DEFECT_WEIGHT = 1.0
ENERGY_LABELS = {
    "travel_forward",
    "travel_backward",
    "pan_left",
    "pan_right",
    "truck_left",
    "truck_right",
    "dolly_in",
    "dolly_out",
    "orbit_candidate",
}
ENERGY_SPEEDS = {"regular", "fast"}
MOVE_ROLES = {"move", "transition_in", "transition_out"}
LANDING_GAP_S = 0.75
LANDING_FLOOR_S = 0.75
CONTIGUOUS_TOLERANCE_S = 0.5
IMPORTANT_SEMANTIC_MOTIONS = {"orbit", "reveal", "tracking", "pov", "travel"}
IMPORTANT_MOTION_LABELS = {"orbit", "orbit_candidate", "reveal", "tracking", "pov"}
NON_MOVE_MOTION_LABELS = {"static", "minor_motion"}
PHOTO_MIN_S = 2.0
PHOTO_MAX_S = 4.0
PHOTO_DEFAULT_S = 3.0


class DirectorError(RuntimeError):
    """Raised when an edit list cannot be produced."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="director.py",
        description=(
            "Pick cut windows from the workspace motion/ and quality/ labels and "
            "write edit.json. Rule-based, deterministic, no model."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/director.py --workspace ~/Videos/ws\n"
            "  python scripts/director.py --workspace ~/Videos/ws --brief \"calm start\" \\\n"
            "      --target-duration-s 90 --order chronological --pacing calm-to-energy --json\n"
            "  python scripts/director.py --workspace ~/Videos/ws --order score --pacing uniform --force\n"
            "  python scripts/director.py --workspace ~/Videos/ws --important-max-s 6.0 --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--workspace", required=True, help="workspace holding ingest.json, motion/ and quality/")
    parser.add_argument("--out", default=None, help="edit list to write (default WORKSPACE/edit.json)")
    parser.add_argument("--brief", default="", help="brief in plain language, stored verbatim")
    parser.add_argument("--target-duration-s", type=float, default=90.0, help="target edit length in seconds")
    parser.add_argument("--order", choices=["chronological", "score"], default="chronological")
    parser.add_argument("--pacing", choices=["calm-to-energy", "uniform"], default="calm-to-energy")
    parser.add_argument("--min-slot-s", type=float, default=2.0, help="drop windows shorter than this")
    parser.add_argument("--max-slot-s", type=float, default=12.0, help="cap a slot at this many seconds")
    parser.add_argument(
        "--important-max-s",
        type=float,
        default=6.0,
        help="longest a hero or semantic-move slot may be (default 6.0)",
    )
    parser.add_argument("--calm-slot-s", type=float, default=6.0, help="target length of the opening slots in calm-to-energy")
    parser.add_argument("--energy-slot-s", type=float, default=2.5, help="target length of the closing slots in calm-to-energy")
    parser.add_argument("--allow-similar", action="store_true", help="let visually similar shots into the same edit (never adjacent)")
    parser.add_argument("--xfade-s", type=float, default=0.3, help="crossfade duration for non-first slots")
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
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def first_not_none(*values):
    for value in values:
        if value is not None:
            return value
    return None


def clip_counter(name):
    """The 4-digit counter in a clip file name, or the stem when there is none."""
    match = CLIP_COUNTER.search(name)
    return match.group(1) if match else Path(name).stem


def candidate_key(candidate):
    """The ``<clip counter>/<segment id>`` key the shotlog is indexed by."""
    return f"{clip_counter(candidate['clip'])}/{candidate['segment'].get('id')}"


def load_shotlog(workspace):
    """The shotlog shots indexed by key, or None when there is no shotlog."""
    path = workspace / "shotlog.json"
    if not path.exists():
        return None
    data = load_json(path)
    if not isinstance(data, dict):
        return None
    shots = data.get("shots") or []
    return {shot["key"]: shot for shot in shots if shot.get("key")}


def overlap_seconds(a_start, a_end, b_start, b_end):
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def load_ingest(workspace):
    path = workspace / "ingest.json"
    if not path.exists():
        raise DirectorError(f"missing ingest.json in {workspace}")
    data = load_json(path)
    if data is None:
        raise DirectorError(f"unreadable ingest.json in {workspace}")
    clips = data.get("clips") or []
    if not clips:
        raise DirectorError("ingest.json has no clips")
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


def photo_content_offset(capture_time, first_moment, span_seconds, total_content):
    """Map a photo's capture time into the content span the clips use."""
    moment = parse_time(capture_time)
    if moment is None:
        return 1.0, total_content
    position = clip_position(moment, first_moment, span_seconds)
    return position, position * total_content


def build_photo_candidates(shotlog, photos_by_name, first_moment, span_seconds, total_content):
    """Photo shots from the shotlog as candidates, with their content position."""
    if not shotlog:
        return [], 0, 0
    candidates = []
    skipped_ineligible = 0
    skipped_roles = 0
    for shot in shotlog.values():
        if shot.get("media_type") != "photo":
            continue
        if not shot.get("eligible", True):
            skipped_ineligible += 1
            continue
        role = shot.get("role") or "hold"
        if role in SKIP_ROLES:
            skipped_roles += 1
            continue
        name = shot.get("clip") or ""
        photo = photos_by_name.get(name) or {}
        capture_time = shot.get("capture_time") or photo.get("creation_time")
        position, content_offset = photo_content_offset(
            capture_time, first_moment, span_seconds, total_content
        )
        segment = {
            "id": shot.get("segment_id") or "p1",
            "start_s": float(shot.get("start_s") or 0.0),
            "end_s": float(shot.get("end_s") or 0.0),
            "role": role,
            "confidence": shot.get("confidence") or 0.0,
            "defects": [],
        }
        candidates.append(
            {
                "clip": name,
                "clip_dt": parse_time(capture_time),
                "position": position,
                "content_offset": content_offset,
                "role": role,
                "segment": segment,
                "move": None,
                "in_s": 0.0,
                "out_s": float(shot.get("end_s") or 0.0),
                "motion_label": shot.get("motion_label") or "static",
                "steadiness": "static",
                "hero": shot.get("hero"),
                "semantic_motion": shot.get("semantic_motion"),
                "shot_key": shot.get("key"),
                "media_type": "photo",
                "photo": name,
                "capture_time": capture_time,
                "subject_position": shot.get("subject_position"),
                "tags": list(shot.get("tags") or []),
                "score": shot.get("score") or 0.0,
                "description": shot.get("description"),
                "shot_reason": shot.get("reason"),
                "hook": shot.get("hook"),
                "summary": shot.get("summary"),
                "similar_to": list(shot.get("similar_to") or []),
            }
        )
    return candidates, skipped_ineligible, skipped_roles


def best_overlapping_move(segment, moves):
    best = None
    best_overlap = 0.0
    for move in moves:
        overlap = overlap_seconds(segment["start_s"], segment["end_s"], move["start_s"], move["end_s"])
        if overlap > best_overlap:
            best_overlap = overlap
            best = move
    return best


def build_candidates(
    clip, clip_dt, position, content_offset, quality, motion, min_slot_s, skip_roles=True, beats_by_segment=None,
):
    moves = (motion or {}).get("moves") or []
    beats_by_segment = beats_by_segment or {}
    candidates = []
    skipped_roles = 0
    for segment in quality.get("segments") or []:
        role = segment.get("role")
        if skip_roles and role in SKIP_ROLES:
            skipped_roles += 1
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
    return candidates, skipped_roles


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


def pacing_bonus(candidate, position):
    role = candidate["role"]
    if position <= 0.4:
        if role == "hold":
            return 0.15
        if role == "move":
            return -0.05
        return 0.0
    if position >= 0.6:
        bonus = 0.0
        speed = candidate["move"].get("speed") if candidate["move"] else None
        if candidate["motion_label"] in ENERGY_LABELS or speed in ENERGY_SPEEDS:
            bonus += 0.15
        if role == "hold":
            bonus -= 0.05
        return bonus
    return 0.0


def score_candidate(candidate, pacing):
    segment = candidate["segment"]
    score = ROLE_BASE.get(candidate["role"], 0.0)
    score += 0.25 * segment.get("confidence", 0.0)
    if candidate["steadiness"] in STEADINESS_BONUS:
        score += STEADINESS_BONUS[candidate["steadiness"]]
    defects = segment.get("defects") or []
    if defects:
        penalty = sum(
            defect.get("value", 0.0) * DEFECT_WEIGHTS.get(defect.get("name"), DEFAULT_DEFECT_WEIGHT)
            for defect in defects
        )
        score -= min(0.4, penalty)
    else:
        score += 0.10
    if pacing == "calm-to-energy":
        score += pacing_bonus(candidate, candidate["position"])
    return clamp(score, 0.0, 1.0)


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


def landing_pairs(quality):
    """Consecutive (move, hold) quality segments that touch or nearly touch."""
    segments = quality.get("segments") or []
    pairs = []
    for first, second in zip(segments, segments[1:]):
        if first.get("role") in MOVE_ROLES and second.get("role") == "hold":
            if second["start_s"] - first["end_s"] <= LANDING_GAP_S:
                pairs.append((first, second))
    return pairs


def make_phrase_candidate(move_candidate, hold_segment):
    """One cut window covering a move and the hold it lands on."""
    phrase = dict(move_candidate)
    phrase["out_s"] = float(hold_segment["end_s"])
    phrase["score"] = move_candidate["score"]
    phrase["phrase"] = True
    phrase["landing_start_s"] = float(hold_segment["start_s"])
    phrase["landing_end_s"] = float(hold_segment["end_s"])
    return phrase


def build_phrases(scored, quality):
    """Add a phrase for each landing move and keep the hold alone as a rival."""
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


def source_contiguous(first, second):
    """True when two slots from the same clip touch in source time."""
    if first["clip"] != second["clip"]:
        return False
    return (
        abs(first["out_s"] - second["in_s"]) <= CONTIGUOUS_TOLERANCE_S
        or abs(second["out_s"] - first["in_s"]) <= CONTIGUOUS_TOLERANCE_S
    )


def neighbours_at(chosen, insert_at):
    neighbours = []
    if insert_at > 0:
        neighbours.append(chosen[insert_at - 1])
    if insert_at < len(chosen):
        neighbours.append(chosen[insert_at])
    return neighbours


def same_take_conflict(slot, chosen, insert_at):
    """The same-clip neighbour that would make the insert a jump cut, or None."""
    for neighbour in neighbours_at(chosen, insert_at):
        if neighbour["clip"] == slot["clip"] and not source_contiguous(neighbour, slot):
            return neighbour
    return None


def warn_same_take(warnings, slot, neighbour):
    warnings.append(
        f"same-take jump cut avoided: {slot['clip']} {slot['in_s']:.1f}-{slot['out_s']:.1f} "
        f"next to {neighbour['in_s']:.1f}-{neighbour['out_s']:.1f}"
    )


def slot_label(item):
    """The shot key of a candidate or slot, or its clip and window when it has none."""
    key = item.get("shot_key")
    if key:
        return key
    return f"{item['clip']} {item['in_s']:.1f}-{item['out_s']:.1f}"


def resolve_same_take(candidate, chosen, insert_at, warnings, recompute):
    """Resolve a same-take conflict for a finished slot before it joins the timeline.

    The check runs on the slot's final cut window, after trimming, so a trim that
    breaks source contiguity cannot slip through as a jump cut. Returns
    ``(insert_at, removed, upgraded)``. ``removed`` is the ``(index, slot)``
    popped for an upgrade, or None. ``upgraded`` is True when a weaker same-take
    neighbour was replaced. Returns ``(None, None, False)`` to skip the slot.
    """
    conflict = same_take_conflict(candidate, chosen, insert_at)
    if conflict is None:
        return insert_at, None, False
    if candidate["score"] <= conflict["score"]:
        warn_same_take(warnings, candidate, conflict)
        return None, None, False
    index = next(position for position, slot in enumerate(chosen) if slot is conflict)
    chosen.pop(index)
    new_at = recompute(chosen)
    second = same_take_conflict(candidate, chosen, new_at)
    if second is not None:
        chosen.insert(index, conflict)
        warn_same_take(warnings, candidate, second)
        return None, None, False
    warnings.append(f"same-take upgrade: {slot_label(candidate)} replaces {slot_label(conflict)}")
    return new_at, (index, conflict), True


def merge_contiguous(slots):
    """Join adjacent source-contiguous slots of one take into a single slot."""
    merged = []
    for slot in slots:
        if merged and merged[-1]["clip"] == slot["clip"] and source_contiguous(merged[-1], slot):
            previous = merged[-1]
            previous["in_s"] = min(previous["in_s"], slot["in_s"])
            previous["out_s"] = max(previous["out_s"], slot["out_s"])
            if slot["score"] > previous["score"]:
                previous["score"] = slot["score"]
                previous["shot_reason"] = slot.get("shot_reason")
                previous["description"] = slot.get("description")
                previous["shot_key"] = slot.get("shot_key")
                previous["similar_to"] = list(slot.get("similar_to") or [])
                previous["role"] = slot["role"]
                previous["motion_label"] = slot["motion_label"]
                previous["steadiness"] = slot["steadiness"]
                previous["segment"] = slot["segment"]
            continue
        merged.append(slot)
    return merged


def slot_targets(n_slots, pacing, target_duration_s, calm_slot_s, energy_slot_s, min_slot_s, max_slot_s):
    """Per-slot target lengths from the pacing, clamped to the min/max."""
    if pacing == "calm-to-energy":
        if n_slots <= 1:
            raw = [calm_slot_s]
        else:
            raw = [
                calm_slot_s + (energy_slot_s - calm_slot_s) * index / (n_slots - 1)
                for index in range(n_slots)
            ]
    else:
        raw = [target_duration_s / n_slots] * n_slots
    return [clamp(target, min_slot_s, max_slot_s) for target in raw]


def shots_similar(first_key, first_links, second_key, second_links):
    """True when two shot keys are linked by the symmetric similarity relation."""
    if first_key is None or second_key is None:
        return False
    return second_key in (first_links or []) or first_key in (second_links or [])


def slot_similar_to_candidate(slot, candidate):
    return shots_similar(
        slot.get("shot_key"),
        slot.get("similar_to"),
        candidate.get("shot_key"),
        candidate.get("similar_to"),
    )


def slots_similar(first, second):
    return shots_similar(
        first.get("shot_key"),
        first.get("similar_to"),
        second.get("shot_key"),
        second.get("similar_to"),
    )


def content_insert_index(chosen, position):
    for index, existing in enumerate(chosen):
        if existing["content_offset"] + existing["in_s"] > position:
            return index
    return len(chosen)


def candidate_conflicts(candidate, chosen, allow_similar, insert_at):
    if allow_similar:
        neighbours = []
        if insert_at > 0:
            neighbours.append(chosen[insert_at - 1])
        if insert_at < len(chosen):
            neighbours.append(chosen[insert_at])
        return any(slot_similar_to_candidate(slot, candidate) for slot in neighbours)
    return any(slot_similar_to_candidate(slot, candidate) for slot in chosen)


def warn_similar(warnings, candidate):
    message = f"skipped similar shot {candidate.get('shot_key')}"
    if message not in warnings:
        warnings.append(message)


def subtract_interval(intervals, cut_start, cut_end):
    result = []
    for start, end in intervals:
        if cut_end <= start or cut_start >= end:
            result.append((start, end))
            continue
        if start < cut_start:
            result.append((start, cut_start))
        if end > cut_end:
            result.append((cut_end, end))
    return result


def apply_overlap_guard(candidate, in_s, out_s, chosen, min_slot_s):
    same_clip = [slot for slot in chosen if slot["clip"] == candidate["clip"]]
    if not same_clip:
        return in_s, out_s
    free = [(in_s, out_s)]
    for slot in same_clip:
        free = subtract_interval(free, slot["in_s"], slot["out_s"])
    if not free:
        return None
    best = max(free, key=lambda interval: interval[1] - interval[0])
    if best[1] - best[0] < min_slot_s:
        return None
    return best


def is_important(candidate):
    """True when a hero or a complete semantic move keeps its whole cut window."""
    if candidate.get("hero"):
        return True
    motion_label = candidate.get("motion_label")
    if motion_label in NON_MOVE_MOTION_LABELS:
        return False
    if candidate.get("semantic_motion") in IMPORTANT_SEMANTIC_MOTIONS:
        return True
    return motion_label in IMPORTANT_MOTION_LABELS


def make_photo_slot(candidate, duration_s):
    """A photo slot: a still held 2-4 s with its deterministic Ken Burns move."""
    target = duration_s if duration_s is not None else PHOTO_DEFAULT_S
    duration = round(clamp(target, PHOTO_MIN_S, PHOTO_MAX_S), 2)
    return {
        "clip": candidate["clip"],
        "in_s": 0.0,
        "out_s": duration,
        "score": round(candidate["score"], 4),
        "role": candidate["role"],
        "motion_label": candidate["motion_label"],
        "steadiness": candidate["steadiness"],
        "position": candidate["position"],
        "content_offset": candidate["content_offset"],
        "segment": candidate["segment"],
        "shot_key": candidate.get("shot_key"),
        "similar_to": candidate.get("similar_to") or [],
        "description": candidate.get("description"),
        "shot_reason": candidate.get("shot_reason"),
        "media_type": "photo",
        "photo": candidate["photo"],
        "capture_time": candidate.get("capture_time"),
        "subject_position": candidate.get("subject_position"),
        "tags": candidate.get("tags") or [],
        "hook": candidate.get("hook"),
        "summary": candidate.get("summary"),
        "duration_s": duration,
        "ken_burns": ken_burns_for(candidate.get("subject_position"), candidate.get("tags")),
    }


def make_slot(candidate, duration_s, max_slot_s, min_slot_s, chosen, warnings=None, important_max_s=None):
    if candidate.get("media_type") == "photo":
        return make_photo_slot(candidate, duration_s)
    in_s = candidate["in_s"]
    if important_max_s is not None and is_important(candidate):
        out_s = candidate["out_s"]
        in_s = max(candidate["in_s"], out_s - important_max_s)
    elif candidate.get("phrase"):
        floor = min(candidate["landing_start_s"] + LANDING_FLOOR_S, candidate["out_s"])
        out_s = clamp(in_s + duration_s, floor, candidate["out_s"])
        if out_s - in_s > duration_s:
            in_s = max(candidate["in_s"], out_s - duration_s)
            if warnings is not None:
                warnings.append(
                    f"phrase trimmed from the head: {candidate['clip']} {candidate['segment'].get('id')} "
                    f"keeps the last {out_s - in_s:.1f}s of the move"
                )
        if out_s - in_s > max_slot_s and warnings is not None:
            warnings.append(
                f"phrase kept through landing: {candidate['clip']} {candidate['segment'].get('id')} "
                f"is {out_s - in_s:.1f}s (cap {max_slot_s:.1f})"
            )
    else:
        out_s = min(candidate["out_s"], in_s + duration_s, in_s + max_slot_s)
    guarded = apply_overlap_guard(candidate, in_s, out_s, chosen, min_slot_s)
    if guarded is None:
        return None
    in_s, out_s = guarded
    in_s = round(in_s, 2)
    out_s = round(out_s, 2)
    if out_s <= in_s:
        return None
    return {
        "clip": candidate["clip"],
        "in_s": in_s,
        "out_s": out_s,
        "score": round(candidate["score"], 4),
        "role": candidate["role"],
        "motion_label": candidate["motion_label"],
        "steadiness": candidate["steadiness"],
        "position": candidate["position"],
        "content_offset": candidate["content_offset"],
        "segment": candidate["segment"],
        "shot_key": candidate.get("shot_key"),
        "similar_to": candidate.get("similar_to") or [],
        "description": candidate.get("description"),
        "shot_reason": candidate.get("shot_reason"),
    }


def pacing_part(slot):
    if slot["position"] <= 0.4:
        return "calm opening"
    if slot["position"] >= 0.6:
        return "late hold" if slot["role"] == "hold" else "energy build"
    return "neutral"


def build_reason(slot):
    if slot.get("shot_reason"):
        parts = slot["shot_reason"].split(" · ") + [pacing_part(slot)]
        deduped = []
        for part in parts:
            if part not in deduped:
                deduped.append(part)
        return " · ".join(deduped)
    parts = [slot["role"]]
    if slot["motion_label"]:
        parts.append(slot["motion_label"])
    if slot["steadiness"]:
        parts.append(slot["steadiness"])
    defects = slot["segment"].get("defects") or []
    if defects:
        parts.append("+".join(defect["name"] for defect in defects))
    else:
        parts.append("clean")
    parts.append(pacing_part(slot))
    deduped = []
    for part in parts:
        if part not in deduped:
            deduped.append(part)
    return " · ".join(deduped)


def build_photo_reason(slot):
    """The photo's hook or summary plus the pacing word, like a video reason."""
    base = slot.get("hook") or slot.get("summary") or slot.get("shot_reason")
    parts = [base] if base else []
    parts.append(pacing_part(slot))
    deduped = []
    for part in parts:
        if part not in deduped:
            deduped.append(part)
    return " · ".join(deduped)


def candidate_overlaps_bucket(candidate, bucket_start, bucket_end):
    """True when a candidate's content window touches the bucket.

    A photo is a point on the content timeline, so it belongs to the bucket that
    contains its capture position rather than to a window.
    """
    start = candidate["content_offset"] + candidate["in_s"]
    end = candidate["content_offset"] + candidate["out_s"]
    if candidate.get("media_type") == "photo":
        return bucket_start <= start <= bucket_end
    return overlap_seconds(start, end, bucket_start, bucket_end) > 0


def pick_chronological(candidates, n_slots, durations, total_content, max_slot_s, min_slot_s, warnings, allow_similar, important_max_s):
    bucket_width = max(1e-9, total_content) / n_slots
    chosen = []
    used = set()
    for index in range(n_slots):
        bucket_start = index * bucket_width
        bucket_end = (index + 1) * bucket_width
        best = None
        for candidate in sorted(candidates, key=lambda item: (-item["score"], item["in_s"])):
            if id(candidate) in used:
                continue
            candidate_start = candidate["content_offset"] + candidate["in_s"]
            if not candidate_overlaps_bucket(candidate, bucket_start, bucket_end):
                continue
            insert_at = content_insert_index(chosen, candidate_start)
            if candidate_conflicts(candidate, chosen, allow_similar, insert_at):
                warn_similar(warnings, candidate)
                continue
            best = candidate
            break
        if best is None:
            warnings.append(f"bucket {index + 1}: no candidate")
            continue
        used.add(id(best))
        insert_at = content_insert_index(chosen, best["content_offset"] + best["in_s"])
        slot = make_slot(best, durations[index], max_slot_s, min_slot_s, chosen, warnings, important_max_s)
        if slot is None:
            warnings.append(f"bucket {index + 1}: candidate dropped by overlap guard")
            continue
        insert_at = content_insert_index(chosen, slot["content_offset"] + slot["in_s"])
        insert_at, removed, upgraded = resolve_same_take(
            slot, chosen, insert_at, warnings,
            lambda current: content_insert_index(current, slot["content_offset"] + slot["in_s"]),
        )
        if insert_at is None:
            continue
        if not upgraded:
            insert_at = content_insert_index(chosen, slot["content_offset"] + slot["in_s"])
        chosen.insert(insert_at, slot)
    return chosen, used


def pick_by_score(candidates, n_slots, durations, max_slot_s, min_slot_s, warnings, allow_similar, important_max_s):
    ordered = sorted(candidates, key=lambda item: (-item["score"], item["in_s"]))
    chosen = []
    used = set()
    for candidate in ordered:
        if len(chosen) >= n_slots:
            break
        if candidate_conflicts(candidate, chosen, allow_similar, len(chosen)):
            warn_similar(warnings, candidate)
            continue
        used.add(id(candidate))
        duration_index = len(chosen)
        slot = make_slot(candidate, durations[duration_index], max_slot_s, min_slot_s, chosen, warnings, important_max_s)
        if slot is None:
            continue
        insert_at, removed, _ = resolve_same_take(slot, chosen, len(chosen), warnings, len)
        if insert_at is None:
            continue
        chosen.insert(insert_at, slot)
    if not chosen:
        warnings.append("no candidate survived the overlap guard")
    return chosen, used


def fill_to_target(candidates, chosen, used, target_duration_s, max_slot_s, min_slot_s, order, warnings, last_target, allow_similar, important_max_s):
    total = sum(slot["out_s"] - slot["in_s"] for slot in chosen)
    remaining = [candidate for candidate in candidates if id(candidate) not in used]
    while remaining:
        budget = target_duration_s - total
        if budget < min_slot_s:
            break
        remaining.sort(key=lambda item: (-item["score"], item["content_offset"] + item["in_s"]))
        candidate = remaining.pop(0)
        used.add(id(candidate))
        if order == "chronological":
            insert_at = content_insert_index(chosen, candidate["content_offset"] + candidate["in_s"])
            recompute = lambda current: content_insert_index(current, candidate["content_offset"] + candidate["in_s"])
        else:
            insert_at = len(chosen)
            recompute = len
        if candidate_conflicts(candidate, chosen, allow_similar, insert_at):
            warn_similar(warnings, candidate)
            continue
        slot = make_slot(candidate, last_target, max_slot_s, min_slot_s, chosen, warnings, important_max_s)
        if slot is None:
            continue
        if order == "chronological":
            insert_at = content_insert_index(chosen, slot["content_offset"] + slot["in_s"])
            recompute = lambda current: content_insert_index(current, slot["content_offset"] + slot["in_s"])
        else:
            insert_at = len(chosen)
            recompute = len
        insert_at, removed, upgraded = resolve_same_take(slot, chosen, insert_at, warnings, recompute)
        if insert_at is None:
            continue
        if not upgraded and order == "chronological":
            insert_at = content_insert_index(chosen, slot["content_offset"] + slot["in_s"])
        chosen.insert(insert_at, slot)
        total += slot["out_s"] - slot["in_s"]
    return chosen


def build_record(args, sources, slots, wall_time_s):
    video = []
    video_index = 0
    photo_index = 0
    for index, slot in enumerate(slots):
        transition = (
            {"type": "cut", "duration_s": 0}
            if index == 0
            else {"type": "crossfade", "duration_s": args.xfade_s}
        )
        if slot.get("media_type") == "photo":
            photo_index += 1
            video.append(
                {
                    "id": f"p{photo_index}",
                    "kind": "photo",
                    "photo": slot["photo"],
                    "duration_s": slot["duration_s"],
                    "ken_burns": slot["ken_burns"],
                    "score": slot["score"],
                    "reason": build_photo_reason(slot),
                    "transition": transition,
                    "description": slot.get("description"),
                    "capture_time": slot.get("capture_time"),
                }
            )
            continue
        video_index += 1
        video.append(
            {
                "id": f"v{video_index}",
                "clip": slot["clip"],
                "in_s": slot["in_s"],
                "out_s": slot["out_s"],
                "score": slot["score"],
                "reason": build_reason(slot),
                "transition": transition,
                "speed": 1.0,
                "role": slot["role"],
                "motion_label": slot["motion_label"],
                "description": slot.get("description"),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": TOOL,
        "brief": {
            "text": args.brief,
            "target_duration_s": args.target_duration_s,
            "order": args.order,
            "pacing": args.pacing,
        },
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
        "meta": {"tool": TOOL, "wall_time_s": round(wall_time_s, 2), "llm_cost_usd": 0.0},
    }


def atomic_write_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def input_paths(workspace):
    paths = [workspace / "ingest.json", workspace / "shotlog.json"]
    paths.extend(sorted((workspace / "motion").glob("*.json")))
    paths.extend(sorted((workspace / "quality").glob("*.json")))
    return paths


def is_up_to_date(out_path, workspace):
    if not out_path.exists():
        return False
    data = load_json(out_path)
    if data is None or validate(data, "edit"):
        return False
    out_mtime = out_path.stat().st_mtime
    for path in input_paths(workspace):
        if path.exists() and path.stat().st_mtime > out_mtime:
            return False
    return True


def slot_duration(slot):
    """The on-screen length of an edit.json slot, video or photo."""
    if slot.get("kind") == "photo":
        return slot.get("duration_s") or 0.0
    return slot["out_s"] - slot["in_s"]


def photo_source(name, photos_by_name):
    """The ``sources[]`` row for a used photo, resolved from ingest ``photos[]``."""
    photo = photos_by_name.get(name) or {}
    return {
        "file": name,
        "path": photo.get("path") or name,
        "duration_s": None,
        "media_type": "photo",
    }


def run(args, started):
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise DirectorError(f"workspace not found: {workspace}")
    out_path = Path(args.out).expanduser().resolve() if args.out else workspace / "edit.json"

    if not args.force and is_up_to_date(out_path, workspace):
        existing = load_json(out_path) or {}
        slots = existing.get("video") or []
        total = sum(slot_duration(slot) for slot in slots)
        return (
            {
                "status": "skipped",
                "out": str(out_path),
                "slots": len(slots),
                "total_duration_s": round(total, 2),
                "target_duration_s": args.target_duration_s,
                "warnings": [],
                "wall_time_s": round(time.monotonic() - started, 2),
                "llm_cost_usd": 0.0,
            },
            None,
        )

    clips = order_clips(load_ingest(workspace))
    first_moment = clips[0][1]
    last_clip, last_moment = clips[-1]
    span_end = last_moment + timedelta(seconds=last_clip["duration_s"])
    span_seconds = max(1e-9, (span_end - first_moment).total_seconds())

    sources = [
        {
            "file": clip["file"],
            "path": clip.get("path") or clip["file"],
            "duration_s": clip.get("duration_s"),
        }
        for clip, _ in clips
    ]

    warnings = []
    candidates = []
    skipped_roles = 0
    skipped_ineligible = 0
    shotlog = load_shotlog(workspace)
    photos_by_name = {
        photo.get("file") or Path(photo["path"]).name: photo
        for photo in load_ingest_photos(workspace)
    }
    beats_by_segment = beats_by_segment_from_shotlog(shotlog)
    content_offsets = []
    running_content = 0.0
    for clip, _ in clips:
        content_offsets.append(running_content)
        running_content += clip.get("duration_s") or 0.0
    total_content = running_content
    for (clip, moment), content_offset in zip(clips, content_offsets):
        stem = Path(clip["file"]).stem
        quality = load_json(workspace / "quality" / f"{stem}.json")
        if quality is None:
            warnings.append(f"missing quality document for {clip['file']}")
            continue
        motion = load_json(workspace / "motion" / f"{stem}.json")
        if motion is None:
            warnings.append(f"missing motion document for {clip['file']}")
        position = clip_position(moment, first_moment, span_seconds)
        clip_candidates, clip_skipped = build_candidates(
            clip, moment, position, content_offset, quality, motion, args.min_slot_s,
            beats_by_segment=beats_by_segment,
        )
        skipped_roles += clip_skipped
        scored = []
        for candidate in clip_candidates:
            lookup_key = candidate.get("shot_key") or candidate_key(candidate)
            shot = shotlog.get(lookup_key) if shotlog is not None else None
            if shot is not None:
                if not shot.get("eligible", True):
                    skipped_ineligible += 1
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
            else:
                candidate["score"] = score_candidate(candidate, args.pacing)
                candidate["description"] = None
                candidate["shot_reason"] = None
                candidate["shot_key"] = None
                candidate["similar_to"] = []
            scored.append(candidate)
        scored = build_phrases(scored, quality)
        candidates.extend(dedupe_candidates(scored))

    photo_candidates, photo_ineligible, photo_roles = build_photo_candidates(
        shotlog, photos_by_name, first_moment, span_seconds, total_content,
    )
    candidates.extend(photo_candidates)
    skipped_ineligible += photo_ineligible
    skipped_roles += photo_roles

    if skipped_roles:
        warnings.append(f"skipped {skipped_roles} accidental/setup/teardown segments")
    if skipped_ineligible:
        warnings.append(f"skipped {skipped_ineligible} ineligible shots")

    if not candidates:
        raise DirectorError("no usable candidates")

    n_slots = int(clamp(round(args.target_duration_s / 6.0), 4, 24))
    targets = slot_targets(
        n_slots, args.pacing, args.target_duration_s, args.calm_slot_s, args.energy_slot_s,
        args.min_slot_s, args.max_slot_s,
    )

    if args.order == "chronological":
        slots, used = pick_chronological(
            candidates, n_slots, targets, total_content, args.max_slot_s, args.min_slot_s, warnings,
            args.allow_similar, args.important_max_s,
        )
    else:
        slots, used = pick_by_score(
            candidates, n_slots, targets, args.max_slot_s, args.min_slot_s, warnings, args.allow_similar,
            args.important_max_s,
        )

    slots = fill_to_target(
        candidates, slots, used, args.target_duration_s, args.max_slot_s, args.min_slot_s, args.order,
        warnings, targets[-1], args.allow_similar, args.important_max_s,
    )
    slots = merge_contiguous(slots)

    for first, second in zip(slots, slots[1:]):
        if slots_similar(first, second):
            warnings.append(f"adjacent similar shots: {first['shot_key']} and {second['shot_key']}")

    if not slots:
        raise DirectorError("no usable candidates")

    used_photos = [slot["photo"] for slot in slots if slot.get("media_type") == "photo"]
    photo_sources = [photo_source(name, photos_by_name) for name in used_photos]

    wall_time_s = time.monotonic() - started
    record = build_record(args, sources + photo_sources, slots, wall_time_s)
    validate_or_raise(record, "edit")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out_path, record)

    total = sum(slot["out_s"] - slot["in_s"] for slot in slots)
    summary = {
        "status": "written",
        "out": str(out_path),
        "slots": len(slots),
        "total_duration_s": round(total, 2),
        "target_duration_s": args.target_duration_s,
        "warnings": warnings,
        "wall_time_s": round(wall_time_s, 2),
        "llm_cost_usd": 0.0,
    }
    return summary, record


def print_human(summary, record):
    if record is not None:
        for slot in record["video"]:
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
    print(f"total {summary['total_duration_s']:.1f}s of target {summary['target_duration_s']:.1f}s")
    for warning in summary["warnings"]:
        print(f"warning: {warning}")


def main(argv=None):
    args = build_parser().parse_args(argv)
    started = time.monotonic()
    try:
        summary, record = run(args, started)
    except (DirectorError, SchemaError, OSError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary))
    else:
        print_human(summary, record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
