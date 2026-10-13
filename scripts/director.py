#!/usr/bin/env python3
"""Rule-based director: pick cut windows from motion/ and quality/ labels.

Usage:
    python scripts/director.py --workspace DIR
    python scripts/director.py --workspace DIR --brief "..." --target-duration-s 90 --json
    python scripts/director.py --workspace DIR --order score --pacing uniform --force

Reads ``ingest.json``, ``motion/<stem>.json`` and ``quality/<stem>.json`` from
the workspace and writes ``edit.json`` (or ``--out``) validated against
``schemas/edit.schema.json``. No model is called: every ``reason`` is built from
the labels. Sources are read-only; the output is written atomically.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common.schema import SchemaError, validate, validate_or_raise

TOOL = "scripts/director.py"
SCHEMA_VERSION = "0.1"

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


def build_candidates(clip, clip_dt, position, content_offset, quality, motion, min_slot_s):
    moves = (motion or {}).get("moves") or []
    candidates = []
    skipped_roles = 0
    for segment in quality.get("segments") or []:
        role = segment.get("role")
        if role in SKIP_ROLES:
            skipped_roles += 1
            continue
        move = None
        if role == "hold":
            in_s, out_s = segment["start_s"], segment["end_s"]
        else:
            move = best_overlapping_move(segment, moves)
            if move is not None:
                in_s = first_not_none(move.get("handle_in_s"), move.get("steady_start_s"), move.get("start_s"))
                out_s = first_not_none(move.get("handle_out_s"), move.get("steady_end_s"), move.get("end_s"))
            else:
                in_s, out_s = segment["start_s"], segment["end_s"]
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
            }
        )
    return candidates, skipped_roles


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
    for candidate in sorted(candidates, key=lambda item: (-item["score"], item["in_s"])):
        if any(overlap_seconds(candidate["in_s"], candidate["out_s"], other["in_s"], other["out_s"]) > 0 for other in kept):
            continue
        kept.append(candidate)
    return kept


def slot_durations(target_duration_s, n_slots, pacing):
    if pacing == "calm-to-energy":
        weights = [1.0 + (0.35 - 1.0) * index / (n_slots - 1) for index in range(n_slots)]
    else:
        weights = [1.0] * n_slots
    total = sum(weights)
    return [target_duration_s * weight / total for weight in weights]


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


def make_slot(candidate, duration_s, max_slot_s, min_slot_s, chosen):
    in_s = candidate["in_s"]
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
    }


def build_reason(slot):
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
    if slot["position"] <= 0.4:
        parts.append("calm opening")
    elif slot["position"] >= 0.6:
        parts.append("late hold" if slot["role"] == "hold" else "energy build")
    else:
        parts.append("neutral")
    deduped = []
    for part in parts:
        if part not in deduped:
            deduped.append(part)
    return " · ".join(deduped)


def pick_chronological(candidates, n_slots, durations, total_content, max_slot_s, min_slot_s, warnings):
    bucket_width = max(1e-9, total_content) / n_slots
    chosen = []
    used = set()
    for index in range(n_slots):
        bucket_start = index * bucket_width
        bucket_end = (index + 1) * bucket_width
        best = None
        for candidate in candidates:
            if id(candidate) in used:
                continue
            candidate_start = candidate["content_offset"] + candidate["in_s"]
            candidate_end = candidate["content_offset"] + candidate["out_s"]
            if overlap_seconds(candidate_start, candidate_end, bucket_start, bucket_end) <= 0:
                continue
            if best is None or candidate["score"] > best["score"]:
                best = candidate
        if best is None:
            warnings.append(f"bucket {index + 1}: no candidate")
            continue
        used.add(id(best))
        slot = make_slot(best, durations[index], max_slot_s, min_slot_s, chosen)
        if slot is None:
            warnings.append(f"bucket {index + 1}: candidate dropped by overlap guard")
            continue
        chosen.append(slot)
    return chosen, used


def pick_by_score(candidates, n_slots, durations, max_slot_s, min_slot_s, warnings):
    ordered = sorted(candidates, key=lambda item: (-item["score"], item["in_s"]))
    chosen = []
    used = set()
    for candidate in ordered:
        if len(chosen) >= n_slots:
            break
        used.add(id(candidate))
        slot = make_slot(candidate, durations[len(chosen)], max_slot_s, min_slot_s, chosen)
        if slot is None:
            continue
        chosen.append(slot)
    if not chosen:
        warnings.append("no candidate survived the overlap guard")
    return chosen, used


def fill_to_target(candidates, chosen, used, target_duration_s, max_slot_s, min_slot_s, order, warnings):
    total = sum(slot["out_s"] - slot["in_s"] for slot in chosen)
    remaining = [candidate for candidate in candidates if id(candidate) not in used]
    while remaining:
        budget = target_duration_s - total
        if budget < min_slot_s:
            break
        remaining.sort(key=lambda item: (-item["score"], item["content_offset"] + item["in_s"]))
        candidate = remaining.pop(0)
        used.add(id(candidate))
        slot = make_slot(candidate, min(max_slot_s, budget), max_slot_s, min_slot_s, chosen)
        if slot is None:
            continue
        if order == "chronological":
            position = slot["content_offset"] + slot["in_s"]
            insert_at = len(chosen)
            for index, existing in enumerate(chosen):
                if existing["content_offset"] + existing["in_s"] > position:
                    insert_at = index
                    break
            chosen.insert(insert_at, slot)
        else:
            chosen.append(slot)
        total += slot["out_s"] - slot["in_s"]
    return chosen


def build_record(args, sources, slots, wall_time_s):
    video = []
    for index, slot in enumerate(slots):
        transition = (
            {"type": "cut", "duration_s": 0}
            if index == 0
            else {"type": "crossfade", "duration_s": args.xfade_s}
        )
        video.append(
            {
                "id": f"v{index + 1}",
                "clip": slot["clip"],
                "in_s": slot["in_s"],
                "out_s": slot["out_s"],
                "score": slot["score"],
                "reason": build_reason(slot),
                "transition": transition,
                "speed": 1.0,
                "role": slot["role"],
                "motion_label": slot["motion_label"],
                "description": None,
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
    paths = [workspace / "ingest.json"]
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


def run(args, started):
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise DirectorError(f"workspace not found: {workspace}")
    out_path = Path(args.out).expanduser().resolve() if args.out else workspace / "edit.json"

    if not args.force and is_up_to_date(out_path, workspace):
        existing = load_json(out_path) or {}
        slots = existing.get("video") or []
        total = sum(slot["out_s"] - slot["in_s"] for slot in slots)
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
            clip, moment, position, content_offset, quality, motion, args.min_slot_s
        )
        skipped_roles += clip_skipped
        for candidate in clip_candidates:
            candidate["score"] = score_candidate(candidate, args.pacing)
        candidates.extend(dedupe_candidates(clip_candidates))

    if skipped_roles:
        warnings.append(f"skipped {skipped_roles} accidental/setup/teardown segments")

    if not candidates:
        raise DirectorError("no usable candidates")

    n_slots = int(clamp(round(args.target_duration_s / 6.0), 4, 24))
    durations = slot_durations(args.target_duration_s, n_slots, args.pacing)

    if args.order == "chronological":
        slots, used = pick_chronological(
            candidates, n_slots, durations, total_content, args.max_slot_s, args.min_slot_s, warnings
        )
    else:
        slots, used = pick_by_score(candidates, n_slots, durations, args.max_slot_s, args.min_slot_s, warnings)

    slots = fill_to_target(
        candidates, slots, used, args.target_duration_s, args.max_slot_s, args.min_slot_s, args.order, warnings
    )

    if not slots:
        raise DirectorError("no usable candidates")

    wall_time_s = time.monotonic() - started
    record = build_record(args, sources, slots, wall_time_s)
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
