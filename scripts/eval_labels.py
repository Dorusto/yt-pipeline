#!/usr/bin/env python3
"""Score workspace motion and quality outputs against a ground-truth YAML.

Usage:
    python scripts/eval_labels.py --ground-truth GT.yaml --workspace WS
    python scripts/eval_labels.py --ground-truth GT.yaml --workspace WS --json
    python scripts/eval_labels.py --ground-truth GT.yaml --workspace WS --out report.json

The ground-truth YAML holds the labels a human wrote from what they filmed.
Every clip id is matched to ``WS/motion/*.json`` and ``WS/quality/*.json`` by the
4-digit counter in the file name; move and role components score interval
overlap between the expected and predicted label, horizon counts false
positives and level claims, and events score one-to-one precision and recall
within a tolerance. The
report is validated against ``schemas/eval.schema.json``. The workspace is read
only; the only file written is ``--out``.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from common.schema import SchemaError, validate_or_raise

CLIP_COUNTER = re.compile(r"_(\d{4})_")
EQUIVALENT_LABELS = (("hold", "static"), ("orbit", "orbit_candidate"))
FAMILY_LABELS = {
    ("travel_forward", "dolly_in"),
    ("travel_backward", "dolly_out"),
    ("truck_left", "pan_left"),
    ("truck_right", "pan_right"),
    ("orbit", "pan_left"),
    ("orbit", "pan_right"),
}


class EvalError(RuntimeError):
    """Raised when the inputs cannot be read or the report cannot be produced."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="eval_labels.py",
        description=(
            "Score workspace motion and quality outputs against a ground-truth YAML: "
            "interval overlap of expected vs predicted move and role, horizon false "
            "positives and event precision/recall (F1)."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/eval_labels.py --ground-truth eval/ground_truth.yaml --workspace /path/to/ws\n"
            "  python scripts/eval_labels.py --ground-truth eval/ground_truth.yaml --workspace /path/to/ws --json\n"
            "  python scripts/eval_labels.py --ground-truth eval/ground_truth.yaml --workspace /path/to/ws --out report.json\n"
            "  python scripts/eval_labels.py --ground-truth GT.yaml --workspace WS --horizon-tol-deg 3 --event-tol-s 2\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ground-truth", required=True, help="ground-truth YAML to score against")
    parser.add_argument("--workspace", required=True, help="workspace holding motion/*.json and quality/*.json")
    parser.add_argument("--json", action="store_true", help="print the full report object instead of the table")
    parser.add_argument("--out", default=None, help="also write the report object to FILE, atomically")
    parser.add_argument(
        "--horizon-tol-deg",
        type=float,
        default=5.0,
        help="|horizon_deg| at or below this counts as a level claim (default 5.0)",
    )
    parser.add_argument(
        "--event-tol-s",
        type=float,
        default=3.0,
        help="a predicted event matches a ground-truth event within this many seconds (default 3.0)",
    )
    return parser


def parse_clock(value):
    """Parse ``MM:SS`` or ``HH:MM:SS`` into seconds; numbers pass through."""
    if isinstance(value, bool):
        raise ValueError(f"cannot parse time {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    parts = str(value).strip().split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    raise ValueError(f"cannot parse time {value!r}")


def label_credit(expected, predicted):
    """Credit a predicted move label against an expected one: 1.0, 0.5 or 0.0."""
    if predicted == expected:
        return 1.0
    for first, second in EQUIVALENT_LABELS:
        if {expected, predicted} == {first, second}:
            return 1.0
    if (expected, predicted) in FAMILY_LABELS:
        return 0.5
    return 0.0


def overlap(start_a, end_a, start_b, end_b):
    """Overlap in seconds of two intervals; zero when they do not touch."""
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def predicted_label_intervals(motion_doc):
    """Moves and still phases as (start_s, end_s, label, priority); moves win on overlap."""
    intervals = []
    for move in (motion_doc or {}).get("moves") or []:
        intervals.append(
            (float(move.get("start_s", 0.0)), float(move.get("end_s", 0.0)), move.get("label"), 0)
        )
    for phase in (motion_doc or {}).get("phases") or []:
        if phase.get("type") == "still":
            intervals.append(
                (float(phase.get("start_s", 0.0)), float(phase.get("end_s", 0.0)), "static", 1)
            )
    return intervals


def credit_breakdown(start, end, intervals, expected_label):
    """Split ``[start, end]`` by the winning predicted label and sum exact/family/wrong seconds."""
    boundaries = {start, end}
    for interval_start, interval_end, _, _ in intervals:
        if start < interval_start < end:
            boundaries.add(interval_start)
        if start < interval_end < end:
            boundaries.add(interval_end)
    points = sorted(boundaries)
    seconds_exact = 0.0
    seconds_family = 0.0
    seconds_wrong = 0.0
    for left, right in zip(points, points[1:]):
        width = right - left
        if width <= 0:
            continue
        midpoint = (left + right) / 2
        covering = [interval for interval in intervals if interval[0] <= midpoint < interval[1]]
        if not covering:
            continue
        winner = min(covering, key=lambda interval: interval[3])
        credit = label_credit(expected_label, winner[2])
        if credit == 1.0:
            seconds_exact += width
        elif credit == 0.5:
            seconds_family += width
        else:
            seconds_wrong += width
    return seconds_exact, seconds_family, seconds_wrong


def move_component(gt_segments, motion_doc):
    """Score the ground-truth ``expect_move`` segments against moves and still phases."""
    expected = [segment for segment in gt_segments if segment.get("expect_move")]
    if not expected:
        return None
    intervals = predicted_label_intervals(motion_doc)
    scored = []
    for segment in expected:
        start = parse_clock(segment["start"])
        end = parse_clock(segment["end"])
        span = end - start
        seconds_exact, seconds_family, seconds_wrong = credit_breakdown(
            start, end, intervals, segment["expect_move"]
        )
        seconds_uncovered = max(0.0, span - seconds_exact - seconds_family - seconds_wrong)
        score = min(1.0, (seconds_exact + 0.5 * seconds_family) / span) if span > 0 else 0.0
        scored.append(
            {
                "start_s": start,
                "end_s": end,
                "expected": segment["expect_move"],
                "score": score,
                "seconds_exact": seconds_exact,
                "seconds_family": seconds_family,
                "seconds_wrong": seconds_wrong,
                "seconds_uncovered": seconds_uncovered,
            }
        )
    return {"score": sum(item["score"] for item in scored) / len(scored), "segments": scored}


def role_component(gt_segments, quality_doc):
    """Score the ground-truth ``expect_role`` segments against the quality roles."""
    expected = [segment for segment in gt_segments if segment.get("expect_role")]
    if not expected:
        return None
    predicted = (quality_doc or {}).get("segments") or []
    scored = []
    for segment in expected:
        start = parse_clock(segment["start"])
        end = parse_clock(segment["end"])
        span = end - start
        seconds_exact = 0.0
        seconds_wrong = 0.0
        for quality_segment in predicted:
            covered = overlap(
                start,
                end,
                float(quality_segment.get("start_s", 0.0)),
                float(quality_segment.get("end_s", 0.0)),
            )
            if covered <= 0.0:
                continue
            if quality_segment.get("role") == segment["expect_role"]:
                seconds_exact += covered
            else:
                seconds_wrong += covered
        seconds_uncovered = max(0.0, span - seconds_exact - seconds_wrong)
        score = min(1.0, seconds_exact / span) if span > 0 else 0.0
        scored.append(
            {
                "start_s": start,
                "end_s": end,
                "expected": segment["expect_role"],
                "score": score,
                "seconds_exact": seconds_exact,
                "seconds_wrong": seconds_wrong,
                "seconds_uncovered": seconds_uncovered,
            }
        )
    return {"score": sum(item["score"] for item in scored) / len(scored), "segments": scored}


def horizon_claim(quality_segment, tolerance):
    """Classify a quality segment's horizon as ``tilted``, ``level`` or ``abstain``."""
    defects = quality_segment.get("defects") or []
    has_horizon_defect = any(defect.get("name") == "horizon" for defect in defects)
    degrees = (quality_segment.get("metrics") or {}).get("horizon_deg")
    if has_horizon_defect or (degrees is not None and abs(degrees) > tolerance):
        return "tilted"
    if degrees is not None:
        return "level"
    return "abstain"


def horizon_component(gt_segments, quality_doc, tolerance):
    """Score the ground-truth ``horizon: level`` segments against the quality claims."""
    expected = [segment for segment in gt_segments if segment.get("horizon") == "level"]
    if not expected:
        return None
    predicted = (quality_doc or {}).get("segments") or []
    scored = []
    total_fp = 0.0
    total_level = 0.0
    total_truth = 0.0
    for segment in expected:
        start = parse_clock(segment["start"])
        end = parse_clock(segment["end"])
        span = end - start
        fp_seconds = 0.0
        level_seconds = 0.0
        abstain_seconds = 0.0
        for quality_segment in predicted:
            covered = overlap(
                start,
                end,
                float(quality_segment.get("start_s", 0.0)),
                float(quality_segment.get("end_s", 0.0)),
            )
            if covered <= 0.0:
                continue
            claim = horizon_claim(quality_segment, tolerance)
            if claim == "tilted":
                fp_seconds += covered
            elif claim == "level":
                level_seconds += covered
            else:
                abstain_seconds += covered
        fp_rate = min(1.0, fp_seconds / span) if span > 0 else 0.0
        level_rate = min(1.0, level_seconds / span) if span > 0 else 0.0
        score = min(1.0, level_rate * (1.0 - fp_rate))
        scored.append(
            {
                "start_s": start,
                "end_s": end,
                "expected": "level",
                "score": score,
                "fp_seconds": fp_seconds,
                "level_seconds": level_seconds,
                "abstain_seconds": abstain_seconds,
            }
        )
        total_fp += fp_seconds
        total_level += level_seconds
        total_truth += span
    return {
        "score": sum(item["score"] for item in scored) / len(scored),
        "fp_rate": min(1.0, total_fp / total_truth) if total_truth > 0 else 0.0,
        "level_rate": min(1.0, total_level / total_truth) if total_truth > 0 else 0.0,
        "segments": scored,
    }


def events_component(gt_events, motion_doc, tolerance):
    """Score ground-truth point events against detected events: one-to-one match, F1.

    Recall is matched / ground-truth events, precision is matched / predicted
    events, each predicted event and each ground-truth event counts at most
    once. The component score is the F1 of the two.
    """
    if not gt_events:
        return None
    predicted = []
    for event in (motion_doc or {}).get("events") or []:
        if event.get("t_s") is not None:
            predicted.append(float(event["t_s"]))
    gt_times = [parse_clock(event["at"]) for event in gt_events]
    pairs = []
    for gt_index, at_s in enumerate(gt_times):
        for pred_index, candidate in enumerate(predicted):
            distance = abs(candidate - at_s)
            if distance <= tolerance:
                pairs.append((distance, gt_index, pred_index))
    pairs.sort()
    matched_gt = {}
    matched_pred = {}
    for distance, gt_index, pred_index in pairs:
        if gt_index in matched_gt or pred_index in matched_pred:
            continue
        matched_gt[gt_index] = pred_index
        matched_pred[pred_index] = gt_index
    matched = len(matched_gt)
    predicted_count = len(predicted)
    recall = matched / len(gt_times)
    precision = matched / predicted_count if predicted_count else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    items = [
        {
            "at_s": at_s,
            "matched_t_s": predicted[matched_gt[gt_index]] if gt_index in matched_gt else None,
        }
        for gt_index, at_s in enumerate(gt_times)
    ]
    return {
        "score": f1,
        "recall": recall,
        "precision": precision,
        "f1": f1,
        "matched_count": matched,
        "predicted_count": predicted_count,
        "gt_count": len(gt_times),
        "events": items,
    }


def score_clip(clip_id, gt_clip, motion_doc, quality_doc, notes, horizon_tolerance, event_tolerance):
    """Score one ground-truth clip and return its report row."""
    gt_segments = gt_clip.get("segments") or []
    move = move_component(gt_segments, motion_doc)
    role = role_component(gt_segments, quality_doc)
    horizon = horizon_component(gt_segments, quality_doc, horizon_tolerance)
    events = events_component(gt_clip.get("events") or [], motion_doc, event_tolerance)
    components = [component["score"] for component in (move, role, horizon, events) if component is not None]
    overall = sum(components) / len(components) if components else 0.0
    if motion_doc and motion_doc.get("file"):
        file_name = motion_doc["file"]
    elif quality_doc and quality_doc.get("file"):
        file_name = quality_doc["file"]
    else:
        file_name = None
    return {
        "clip_id": clip_id,
        "file": file_name,
        "move": move,
        "role": role,
        "horizon": horizon,
        "events": events,
        "overall": overall,
        "notes": notes,
    }


def _clip_counter(name):
    match = CLIP_COUNTER.search(name)
    return match.group(1) if match else None


def find_output(output_dir, clip_id):
    """Return the first matching output file and an optional note about duplicates."""
    if not output_dir.is_dir():
        return None, None
    matches = sorted(path for path in output_dir.glob("*.json") if _clip_counter(path.name) == clip_id)
    if not matches:
        return None, None
    if len(matches) > 1:
        return matches[0], f"multiple outputs matched {clip_id}; using {matches[0].name}"
    return matches[0], None


def _load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_component(workspace, kind, clip_id):
    output_dir = workspace / kind
    path, note = find_output(output_dir, clip_id)
    if path is None:
        return None, [f"missing {kind} output"]
    document = _load_json(path)
    if document is None:
        return None, [f"unreadable {kind} output: {path.name}"]
    return document, ([note] if note else [])


def totals_row(clips):
    """Mean each component over the clips that have it, plus the run overall."""
    def component(name):
        values = [clip[name]["score"] for clip in clips if clip[name] is not None]
        return sum(values) / len(values) if values else None

    overalls = [clip["overall"] for clip in clips]
    return {
        "move": component("move"),
        "role": component("role"),
        "horizon": component("horizon"),
        "events": component("events"),
        "overall": sum(overalls) / len(overalls) if overalls else 0.0,
        "clips_scored": len(clips),
    }


def evaluate(gt, workspace, horizon_tolerance=5.0, event_tolerance=3.0, ground_truth_path=None, generated_at=None):
    """Score every ground-truth clip against the workspace outputs and build the report."""
    workspace = Path(workspace)
    clips = []
    for clip_id, gt_clip in (gt.get("clips") or {}).items():
        motion_doc, motion_notes = _read_component(workspace, "motion", clip_id)
        quality_doc, quality_notes = _read_component(workspace, "quality", clip_id)
        clips.append(
            score_clip(
                clip_id,
                gt_clip or {},
                motion_doc,
                quality_doc,
                motion_notes + quality_notes,
                horizon_tolerance,
                event_tolerance,
            )
        )
    return {
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "ground_truth": str(Path(ground_truth_path).resolve()) if ground_truth_path else "",
        "workspace": str(workspace.resolve()),
        "horizon_tol_deg": horizon_tolerance,
        "event_tol_s": event_tolerance,
        "clips": clips,
        "totals": totals_row(clips),
    }


def _cell(component):
    return "-" if component is None else f"{component['score']:.2f}"


def format_table(report):
    """Render the report as a fixed-width per-clip table with a TOTAL row."""
    lines = [f"{'clip':<8}{'move':<6}{'role':<6}{'horizon':<9}{'events':<8}{'overall'}".rstrip()]
    for clip in report["clips"]:
        row = (
            f"{clip['clip_id']:<8}"
            f"{_cell(clip['move']):<6}"
            f"{_cell(clip['role']):<6}"
            f"{_cell(clip['horizon']):<9}"
            f"{_cell(clip['events']):<8}"
            f"{clip['overall']:.2f}"
        )
        lines.append(row.rstrip())
    totals = report["totals"]

    def total_cell(name):
        value = totals[name]
        return "-" if value is None else f"{value:.2f}"

    total_row = (
        f"{'TOTAL':<8}"
        f"{total_cell('move'):<6}"
        f"{total_cell('role'):<6}"
        f"{total_cell('horizon'):<9}"
        f"{total_cell('events'):<8}"
        f"{totals['overall']:.2f}"
    )
    lines.append(total_row.rstrip())
    return "\n".join(lines)


def _atomic_write_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_ground_truth(path):
    if not path.is_file():
        raise EvalError(f"ground truth not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvalError(f"cannot read ground truth {path}: {exc}") from exc
    try:
        gt = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise EvalError(f"cannot parse ground truth {path}: {exc}") from exc
    if not isinstance(gt, dict) or not isinstance(gt.get("clips"), dict):
        raise EvalError(f"ground truth has no clips mapping: {path}")
    return gt


def main(argv=None):
    started = time.monotonic()
    args = build_parser().parse_args(argv)
    try:
        ground_truth = _load_ground_truth(Path(args.ground_truth).expanduser())
        workspace = Path(args.workspace).expanduser()
        if not workspace.is_dir():
            raise EvalError(f"workspace not found: {workspace}")
        report = evaluate(
            ground_truth,
            workspace,
            horizon_tolerance=args.horizon_tol_deg,
            event_tolerance=args.event_tol_s,
            ground_truth_path=args.ground_truth,
        )
        report["meta"] = {
            "tool": "scripts/eval_labels.py",
            "wall_time_s": round(time.monotonic() - started, 2),
            "llm_cost_usd": 0.0,
        }
        validate_or_raise(report, "eval")
    except (EvalError, SchemaError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    if args.out:
        try:
            _atomic_write_json(Path(args.out).expanduser(), report)
        except OSError as exc:
            print(f"[error] cannot write {args.out}: {exc}", file=sys.stderr)
            return 1
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_table(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
