#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measure footage quality per segment and label each segment with a role.

Usage:
    python scripts/quality.py INPUT --workspace DIR
    python scripts/quality.py INPUT --workspace DIR --json
    python scripts/quality.py INPUT --workspace DIR --force --sample-fps 2 --width 320

INPUT is one video file or a folder scanned non-recursively for ``.mp4``,
``.mov`` and ``.m4v`` files. Sampled frames are measured for blur, exposure,
obstruction, frame difference and horizon; a matching ``motion/<stem>.json`` in
the workspace drives segmentation and carries the camera label and shake. Every
segment gets a role (move, hold, transition_in, transition_out, setup,
teardown, accidental) with a confidence and the measured defects. Labels only:
nothing is ever deleted, cut or trimmed here. Each clip becomes
``DIR/quality/<stem>.json``, validated against ``schemas/quality.schema.json``
and written atomically. Source files are read only.
"""

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.overlap import overlap_seconds
from common.schema import SchemaError, validate, validate_or_raise

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
ROTATION_LABELS = {"roll_cw", "roll_ccw", "pan_left", "pan_right"}
TRANSITION_LABELS = ROTATION_LABELS | {"complex_motion"}

FLAT_STD = 6.0
STATIC_DIFF = 3.0
FRAME_DIFF_STILL = 2.0
MIN_RUN_SECONDS = 0.5
HORIZON_MAX_ANGLE = 15.0
HORIZON_MIN_LENGTH_FRAC = 0.25
HORIZON_MIN_LONG_LINES = 3
HORIZON_MAD_MAX = 3.0

ACCIDENTAL_MIN_SECONDS = 2.0
OBSTRUCTION_ACCIDENTAL = 0.6
UNIFORM_REL = 0.2
DARK_LUMA = 25.0
BRIGHT_LUMA = 235.0
STATIC_DIFF_MEAN = 0.5
STATIC_REL = 0.25

ACCIDENTAL_RUN_SECONDS = 5.0
ACCIDENTAL_RUN_FRACTION = 0.7
ACCIDENTAL_SHAKE = 0.1
ACCIDENTAL_RUN_CONFIDENCE = 0.6
ACCIDENTAL_TAKE_CONFIDENCE = 0.65
UNSTABLE_STEADINESS = {"unsteady", "very_unsteady"}
STABLE_STEADINESS = {"no_shaking", "minimal_shaking"}

TRANSITION_MAX_SECONDS = 3.0
SETUP_MAX_SECONDS = 4.0
HOLD_MIN_SECONDS = 1.0
HOLD_CLEAN_REL = 0.5

BLUR_REL = 0.5
SHAKE_MIN = 0.03
SHAKE_FULL = 0.15
OBSTRUCTION_MIN = 0.25
HORIZON_MIN = 5.0
HORIZON_FULL = 15.0
HIGH_PCT = 5.0
LOW_PCT = 10.0
LUMA_LOW = 40.0
LUMA_HIGH = 200.0


class QualityError(RuntimeError):
    """Raised when a clip cannot be analysed."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="quality.py",
        description=(
            "Measure footage quality per segment and label each segment with a role "
            "(move, hold, transition_in, transition_out, setup, teardown, accidental) "
            "plus measured defects (blur, shake, exposure, obstruction, horizon)."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/quality.py clip.mp4 --workspace <workspace>\n"
            "  python scripts/quality.py <trip-folder> --workspace <workspace> --json\n"
            "  python scripts/quality.py clip.mp4 --workspace <workspace> --force\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="one video file, or a folder of clips (non-recursive)")
    parser.add_argument("--workspace", required=True, help="output root; quality/<stem>.json is written here")
    parser.add_argument("--motion-dir", default=None, help="folder with motion/<stem>.json (default: WORKSPACE/motion)")
    parser.add_argument("--force", action="store_true", help="re-analyse clips whose output already exists")
    parser.add_argument("--sample-fps", type=float, default=2.0, help="measurement sampling rate (default 2)")
    parser.add_argument("--width", type=int, default=320, help="measurement width in pixels (default 320)")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _median(values):
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        return 0.0
    middle = count // 2
    if count % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _dedup(items):
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _horizon_angle(gray):
    """Median signed roll of the frame's dominant long near-horizontal lines, or None.

    Positive means the right end of the line is higher on screen; 0 is level.
    Returns None when no dominant sea/sky line is found instead of guessing from
    arbitrary edges.
    """
    width = gray.shape[1]
    min_length = HORIZON_MIN_LENGTH_FRAC * width
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        threshold=60,
        minLineLength=int(min_length),
        maxLineGap=20,
    )
    if lines is None:
        return None
    angles = []
    for x1, y1, x2, y2 in lines:
        if x1 > x2:
            x1, y1, x2, y2 = x2, y2, x1, y1
        dx = x2 - x1
        if dx == 0:
            continue
        if math.hypot(dx, y2 - y1) < min_length:
            continue
        angle = math.degrees(math.atan2(y1 - y2, dx))
        if abs(angle) > HORIZON_MAX_ANGLE:
            continue
        angles.append(angle)
    if len(angles) < HORIZON_MIN_LONG_LINES:
        return None
    median = float(np.median(angles))
    mad = float(np.median([abs(angle - median) for angle in angles]))
    if mad > HORIZON_MAD_MAX:
        return None
    return median


def measure_frame(gray, prev_gray=None):
    """Measure blur, exposure, obstruction, frame difference and horizon on one gray frame."""
    laplacian = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    mean_luma = float(gray.mean())
    clip_high_pct = float((gray >= 250).mean() * 100.0)
    clip_low_pct = float((gray <= 5).mean() * 100.0)
    gray_float = gray.astype(np.float32)
    local_mean = cv2.blur(gray_float, (9, 9))
    local_sq = cv2.blur(gray_float * gray_float, (9, 9))
    local_std = np.sqrt(np.clip(local_sq - local_mean * local_mean, 0.0, None))
    flat = local_std < FLAT_STD
    if prev_gray is None:
        obstruction = 0.0
        frame_diff = 0.0
    else:
        diff = np.abs(gray_float - prev_gray.astype(np.float32))
        obstruction = float(np.mean(flat & (diff <= STATIC_DIFF)))
        frame_diff = float(diff.mean())
    horizon_deg = _horizon_angle(gray)
    return {
        "laplacian": laplacian,
        "mean_luma": mean_luma,
        "clip_high_pct": clip_high_pct,
        "clip_low_pct": clip_low_pct,
        "obstruction": obstruction,
        "frame_diff": frame_diff,
        "horizon_deg": horizon_deg,
    }


def aggregate_frames(frames):
    """Mean of the per-frame measurements over a list of sampled frames."""
    if not frames:
        return {
            "laplacian": 0.0,
            "mean_luma": 0.0,
            "clip_high_pct": 0.0,
            "clip_low_pct": 0.0,
            "obstruction": 0.0,
            "frame_diff": 0.0,
        }
    return {
        "laplacian": _mean([frame["laplacian"] for frame in frames]),
        "mean_luma": _mean([frame["mean_luma"] for frame in frames]),
        "clip_high_pct": _mean([frame["clip_high_pct"] for frame in frames]),
        "clip_low_pct": _mean([frame["clip_low_pct"] for frame in frames]),
        "obstruction": _mean([frame["obstruction"] for frame in frames]),
        "frame_diff": _mean([frame["frame_diff"] for frame in frames]),
    }


def segment_metrics(frames, clip_median_laplacian, shake=0.0, horizon_deg=None):
    """Aggregate a segment's frames into the raw metrics written to the output."""
    means = aggregate_frames(frames)
    if clip_median_laplacian > 0:
        laplacian_rel = means["laplacian"] / clip_median_laplacian
    else:
        laplacian_rel = 0.0
    return {
        "laplacian_rel": laplacian_rel,
        "clip_high_pct": means["clip_high_pct"],
        "clip_low_pct": means["clip_low_pct"],
        "mean_luma": means["mean_luma"],
        "shake": shake,
        "obstruction": means["obstruction"],
        "horizon_deg": horizon_deg,
    }


def segment_horizon(frames):
    """Mean measured horizon roll over a segment's frames, or None when none measured."""
    measured = [frame["horizon_deg"] for frame in frames if frame.get("horizon_deg") is not None]
    if not measured:
        return None
    return _mean(measured)


def _exposure_defect(metrics):
    high = metrics.get("clip_high_pct", 0.0)
    low = metrics.get("clip_low_pct", 0.0)
    luma = metrics.get("mean_luma", 128.0)
    candidates = []
    if high > HIGH_PCT:
        candidates.append((high / 100.0, f"clipped highlights {high:.1f}%"))
    if low > LOW_PCT:
        candidates.append((low / 100.0, f"crushed shadows {low:.1f}%"))
    if luma < LUMA_LOW:
        candidates.append(((LUMA_LOW - luma) / 100.0, f"mean luma {luma:.0f}"))
    elif luma > LUMA_HIGH:
        candidates.append(((luma - LUMA_HIGH) / 100.0, f"mean luma {luma:.0f}"))
    if not candidates:
        return None
    value, detail = max(candidates, key=lambda item: item[0])
    return {"name": "exposure", "value": min(1.0, value), "detail": detail}


def build_defects(metrics, role=None, label=None, speed=None):
    """Return the measured defects that cross their thresholds for a segment."""
    defects = []
    laplacian_rel = metrics.get("laplacian_rel", 1.0)
    if laplacian_rel < BLUR_REL:
        value = min(1.0, (BLUR_REL - laplacian_rel) / BLUR_REL)
        defects.append(
            {"name": "blur", "value": value, "detail": f"laplacian {laplacian_rel:.2f}x clip median"}
        )
    exposure = _exposure_defect(metrics)
    if exposure is not None:
        defects.append(exposure)
    shake = metrics.get("shake", 0.0)
    if shake >= SHAKE_MIN:
        defects.append(
            {"name": "shake", "value": min(1.0, shake / SHAKE_FULL), "detail": f"shake {shake:.3f}"}
        )
    obstruction = metrics.get("obstruction", 0.0)
    if obstruction >= OBSTRUCTION_MIN:
        defects.append(
            {
                "name": "obstruction",
                "value": min(1.0, obstruction),
                "detail": f"obstruction {obstruction:.2f} of frame",
            }
        )
    horizon = metrics.get("horizon_deg")
    if horizon is not None and abs(horizon) > HORIZON_MIN:
        defects.append(
            {
                "name": "horizon",
                "value": min(1.0, abs(horizon) / HORIZON_FULL),
                "detail": f"horizon {horizon:.1f} deg",
            }
        )
    return defects


def intent_evidence(defects, role, speed):
    """Return the intent notes for defects that are deliberate on a fast move."""
    if role != "move" or speed != "fast":
        return []
    names = {defect["name"] for defect in defects}
    notes = []
    if "blur" in names:
        notes.append("motion blur during a fast move (intent)")
    if "shake" in names:
        notes.append("shake during a fast move (intent)")
    return notes


def _is_content_candidate(part):
    """Return True when a part is real content for the setup/teardown placement."""
    if part.get("move") is not None:
        return True
    label = part.get("label")
    if label is not None and label not in ("static", "minor_motion"):
        return True
    return (
        part.get("phase_type") == "still"
        and (part.get("end_s", 0.0) - part.get("start_s", 0.0)) >= HOLD_MIN_SECONDS
    )


def _part_positions(parts, duration_s):
    """Return head/middle/tail per part; setup/teardown only outside the content."""
    content = [index for index, part in enumerate(parts) if _is_content_candidate(part)]
    if content:
        first_content = content[0]
        last_content = content[-1]
        return [
            "head" if index < first_content else "tail" if index > last_content else "middle"
            for index in range(len(parts))
        ]
    midpoint = duration_s / 2.0
    return ["head" if part["start_s"] < midpoint else "tail" for part in parts]


def _is_transition(phase_type, speed, label, steadiness, duration_s):
    return (
        phase_type in ("steady_move", "irregular")
        and speed == "fast"
        and label in TRANSITION_LABELS
        and steadiness in ("no_shaking", "minimal_shaking")
        and duration_s <= TRANSITION_MAX_SECONDS
    )


def _is_setup(phase_type, steadiness, speed, duration_s):
    return duration_s <= SETUP_MAX_SECONDS and (
        phase_type == "irregular"
        or steadiness in ("unsteady", "very_unsteady")
        or speed == "fast"
    )


def _accidental_trigger(facts):
    """Return ``(message, bonus)`` when the accidental trigger fires, else None."""
    if facts.get("duration_s", 0.0) < ACCIDENTAL_MIN_SECONDS:
        return None
    metrics = facts.get("metrics") or {}
    laplacian_rel = metrics.get("laplacian_rel", 1.0)
    mean_luma = metrics.get("mean_luma", 128.0)
    obstruction = metrics.get("obstruction", 0.0)
    frame_diff = facts.get("frame_diff_mean", 0.0)
    if obstruction >= OBSTRUCTION_ACCIDENTAL:
        return f"obstruction {obstruction:.2f} of frame", True
    if laplacian_rel <= UNIFORM_REL and (mean_luma <= DARK_LUMA or mean_luma >= BRIGHT_LUMA):
        return f"near-uniform frame (laplacian {laplacian_rel:.2f}x, luma {mean_luma:.0f})", True
    if frame_diff <= STATIC_DIFF_MEAN and laplacian_rel <= STATIC_REL:
        return f"static low-detail frame (diff {frame_diff:.2f}, laplacian {laplacian_rel:.2f}x)", False
    return None


def assign_role(segment_facts):
    """Return ``(role, confidence, evidence)`` for a segment's plain facts dict."""
    metrics = segment_facts.get("metrics") or {}
    position = segment_facts.get("position")
    duration_s = segment_facts.get("duration_s", 0.0)
    phase_type = segment_facts.get("phase_type")
    label = segment_facts.get("label")
    speed = segment_facts.get("speed")
    steadiness = segment_facts.get("steadiness")
    move = segment_facts.get("move")
    laplacian_rel = metrics.get("laplacian_rel", 1.0)

    trigger = _accidental_trigger(segment_facts)
    if trigger is not None:
        message, bonus = trigger
        confidence = 0.55 + (0.15 if bonus else 0.0)
        return "accidental", confidence, [message]

    if position == "head" and _is_transition(phase_type, speed, label, steadiness, duration_s):
        confidence = 0.5 + (0.1 if label in ROTATION_LABELS else 0.0)
        return "transition_in", confidence, ["fast clean rotation at take head"]
    if position == "tail" and _is_transition(phase_type, speed, label, steadiness, duration_s):
        confidence = 0.5 + (0.1 if label in ROTATION_LABELS else 0.0)
        return "transition_out", confidence, ["fast clean rotation at take tail"]

    if position == "head" and _is_setup(phase_type, steadiness, speed, duration_s):
        triggers = sum(
            [
                phase_type == "irregular",
                steadiness in ("unsteady", "very_unsteady"),
                speed == "fast",
            ]
        )
        confidence = 0.65 if triggers >= 2 else 0.5
        return "setup", confidence, ["short irregular motion at the head of the take"]
    if position == "tail" and _is_setup(phase_type, steadiness, speed, duration_s):
        triggers = sum(
            [
                phase_type == "irregular",
                steadiness in ("unsteady", "very_unsteady"),
                speed == "fast",
            ]
        )
        confidence = 0.65 if triggers >= 2 else 0.5
        return "teardown", confidence, ["short irregular motion at the tail of the take"]

    if label != "static" and (
        move is not None or phase_type in ("steady_move", "ramp_up", "ramp_down")
    ):
        confidence = move["confidence"] if move is not None else 0.5
        evidence = [f"steady {label} with handles"] if move is not None else [f"motion phase {phase_type}"]
        return "move", confidence, evidence

    if phase_type == "still" and duration_s >= HOLD_MIN_SECONDS:
        defects = build_defects(metrics, "hold", label)
        clean = laplacian_rel >= HOLD_CLEAN_REL and _exposure_defect(metrics) is None
        if position == "middle" and clean:
            if not defects:
                return "hold", 0.7, ["still, clean"]
            names = ", ".join(defect["name"] for defect in defects)
            return "hold", 0.7, [f"still, {names}"]
        if position in ("head", "tail"):
            return "hold", 0.55, [f"still at take {position}"]
        names = ", ".join(defect["name"] for defect in defects)
        return "hold", 0.4, [f"still, {names}"]

    if label == "static":
        return "hold", 0.4, ["no significant camera motion"]

    return "move", 0.4, ["unclassified motion"]


def role_signals_disagree(facts, evidence):
    """Return True when the role triggers contradict each other."""
    position = facts.get("position")
    duration_s = facts.get("duration_s", 0.0)
    phase_type = facts.get("phase_type")
    label = facts.get("label")
    speed = facts.get("speed")
    steadiness = facts.get("steadiness")
    move = facts.get("move")
    if (
        position in ("head", "tail")
        and _is_transition(phase_type, speed, label, steadiness, duration_s)
        and _is_setup(phase_type, steadiness, speed, duration_s)
    ):
        return True
    if _accidental_trigger(facts) is not None and (
        move is not None or label not in ("static", "minor_motion", None)
    ):
        return True
    if "unclassified motion" in evidence:
        return True
    return False


def _segment_confidence(role_confidence, uncertain_reasons):
    """Apply one deduction per quality-level flag and clamp to [0.05, 1.0]."""
    confidence = role_confidence
    if "role_signals_disagree" in uncertain_reasons:
        confidence -= 0.15
    if "motion_uncertain" in uncertain_reasons:
        confidence -= 0.15
    return min(1.0, max(0.05, confidence))


def _runs(classes):
    runs = []
    for index, value in enumerate(classes):
        if runs and runs[-1][0] == value:
            runs[-1][2] = index + 1
        else:
            runs.append([value, index, index + 1])
    return runs


def _merge_short_runs(runs, effective_fps, min_seconds):
    merged = []
    for value, start, end in runs:
        if merged and (end - start) / effective_fps < min_seconds:
            merged[-1][2] = end
        else:
            merged.append([value, start, end])
    if len(merged) > 1 and (merged[0][2] - merged[0][1]) / effective_fps < min_seconds:
        merged[1][1] = merged[0][1]
        merged.pop(0)
    coalesced = []
    for value, start, end in merged:
        if coalesced and coalesced[-1][0] == value:
            coalesced[-1][2] = end
        else:
            coalesced.append([value, start, end])
    return coalesced


def _normalize_bounds(bounds, duration_s):
    if not bounds:
        return bounds
    bounds[0][0] = 0.0
    for index in range(1, len(bounds)):
        bounds[index][0] = bounds[index - 1][1]
    bounds[-1][1] = max(bounds[-1][0], duration_s)
    return bounds


def _merge_slivers(intervals):
    """Fold every sub-threshold interval into a neighbour so no sliver survives."""
    merged = []
    for interval in intervals:
        if merged and interval["end_s"] - interval["start_s"] < MIN_RUN_SECONDS:
            merged[-1]["end_s"] = interval["end_s"]
            merged[-1]["sources"].extend(interval["sources"])
        else:
            merged.append(interval)
    if len(merged) > 1 and merged[0]["end_s"] - merged[0]["start_s"] < MIN_RUN_SECONDS:
        merged[1]["start_s"] = merged[0]["start_s"]
        merged[1]["sources"][:0] = merged[0]["sources"]
        merged.pop(0)
    return merged


def _longest_source(sources, start_s, end_s):
    """Return the source span overlapping a merged interval the most."""

    def overlap(source):
        return overlap_seconds(start_s, end_s, source["start_s"], source["end_s"])

    return max(sources, key=overlap)


def _frames_in(frames, start_s, end_s):
    selected = [frame for frame in frames if start_s <= frame["t"] < end_s]
    if selected or not frames:
        return selected
    midpoint = (start_s + end_s) / 2.0
    return [min(frames, key=lambda frame: abs(frame["t"] - midpoint))]


def _window_shake(trajectory, start_s, end_s):
    values = [float(row.get("shake", 0.0)) for row in trajectory if start_s <= row["t"] < end_s]
    return _mean(values)


def _overlapping_move(moves, start_s, end_s):
    best = None
    best_overlap = 0.0
    for move in moves:
        overlap = overlap_seconds(start_s, end_s, move["start_s"], move["end_s"])
        if overlap > best_overlap:
            best_overlap = overlap
            best = move
    return best


def _part(
    start_s,
    end_s,
    role,
    confidence,
    evidence,
    phase_type,
    label,
    speed,
    steadiness,
    move,
    label_source=None,
    role_signals_disagree=False,
):
    return {
        "start_s": start_s,
        "end_s": end_s,
        "role": role,
        "confidence": confidence,
        "evidence": evidence,
        "phase_type": phase_type,
        "label": label,
        "speed": speed,
        "steadiness": steadiness,
        "move": move,
        "label_source": label_source,
        "role_signals_disagree": role_signals_disagree,
    }


def _facts(position, start_s, end_s, phase_type, label, speed, steadiness, move, metrics, frames):
    return {
        "position": position,
        "duration_s": end_s - start_s,
        "phase_type": phase_type,
        "label": label,
        "speed": speed,
        "steadiness": steadiness,
        "metrics": metrics,
        "move": move,
        "frame_diff_mean": _mean([frame["frame_diff"] for frame in frames]),
    }


def _drafts_to_parts(drafts, duration_s):
    """Assign positions and roles to interval drafts and build the final parts."""
    positions = _part_positions(drafts, duration_s)
    parts = []
    for draft, position in zip(drafts, positions):
        facts = _facts(
            position,
            draft["start_s"],
            draft["end_s"],
            draft["phase_type"],
            draft["label"],
            draft["speed"],
            draft["steadiness"],
            draft["move"],
            draft["metrics"],
            draft["frames"],
        )
        role, confidence, evidence = assign_role(facts)
        disagree = role_signals_disagree(facts, evidence)
        parts.append(
            _part(
                draft["start_s"],
                draft["end_s"],
                role,
                confidence,
                evidence,
                draft["phase_type"],
                draft["label"],
                draft["speed"],
                draft["steadiness"],
                draft["move"],
                draft["label_source"],
                disagree,
            )
        )
    return parts


def segments_from_motion(motion_data, duration_s, frames, clip_median_laplacian):
    """Build role-assigned parts from a motion file's phases, moves and trajectory."""
    phases = motion_data.get("phases") or []
    trajectory = motion_data.get("trajectory") or []
    moves = motion_data.get("moves") or []
    bounds = _normalize_bounds(
        [[float(phase.get("start_s", 0.0)), float(phase.get("end_s", 0.0))] for phase in phases],
        duration_s,
    )
    intervals = _merge_slivers(
        [
            {
                "start_s": start_s,
                "end_s": end_s,
                "sources": [{"phase": phase, "start_s": start_s, "end_s": end_s}],
            }
            for phase, (start_s, end_s) in zip(phases, bounds)
        ]
    )
    drafts = []
    for interval in intervals:
        start_s = interval["start_s"]
        end_s = interval["end_s"]
        phase = _longest_source(interval["sources"], start_s, end_s)["phase"]
        seg_frames = _frames_in(frames, start_s, end_s)
        shake = _window_shake(trajectory, start_s, end_s)
        horizon = segment_horizon(seg_frames)
        metrics = segment_metrics(seg_frames, clip_median_laplacian, shake, horizon)
        move = _overlapping_move(moves, start_s, end_s)
        label = move["label"] if move is not None else phase.get("dominant_motion")
        label_source = move if move is not None else phase
        drafts.append(
            {
                "start_s": start_s,
                "end_s": end_s,
                "phase_type": phase.get("type"),
                "label": label,
                "speed": phase.get("speed"),
                "steadiness": phase.get("steadiness"),
                "move": move,
                "label_source": label_source,
                "metrics": metrics,
                "frames": seg_frames,
            }
        )
    return _drafts_to_parts(drafts, duration_s)


def segments_from_frames(frames, duration_s, effective_fps, clip_median_laplacian):
    """Build role-assigned parts from coarse still/moving runs of the frame-diff series."""
    if not frames:
        return [
            _part(
                0.0,
                max(duration_s, 0.0),
                "move",
                0.4,
                ["unclassified motion"],
                "irregular",
                None,
                None,
                None,
                None,
                role_signals_disagree=True,
            )
        ]
    classes = ["moving" if frame["frame_diff"] > FRAME_DIFF_STILL else "still" for frame in frames]
    runs = _merge_short_runs(_runs(classes), effective_fps, MIN_RUN_SECONDS)
    bounds = _normalize_bounds(
        [[start / effective_fps, end / effective_fps] for _, start, end in runs], duration_s
    )
    intervals = _merge_slivers(
        [
            {
                "start_s": start_s,
                "end_s": end_s,
                "sources": [{"value": value, "start_s": start_s, "end_s": end_s}],
            }
            for (value, _, _), (start_s, end_s) in zip(runs, bounds)
        ]
    )
    drafts = []
    for interval in intervals:
        start_s = interval["start_s"]
        end_s = interval["end_s"]
        value = _longest_source(interval["sources"], start_s, end_s)["value"]
        phase_type = "still" if value == "still" else "irregular"
        seg_frames = _frames_in(frames, start_s, end_s)
        horizon = segment_horizon(seg_frames)
        metrics = segment_metrics(seg_frames, clip_median_laplacian, 0.0, horizon)
        drafts.append(
            {
                "start_s": start_s,
                "end_s": end_s,
                "phase_type": phase_type,
                "label": None,
                "speed": None,
                "steadiness": None,
                "move": None,
                "label_source": None,
                "metrics": metrics,
                "frames": seg_frames,
            }
        )
    return _drafts_to_parts(drafts, duration_s)


def _is_unstable(segment, steadiness):
    """Return True when a final segment is shaky or its steadiness word is unsteady."""
    if segment["metrics"].get("shake", 0.0) >= ACCIDENTAL_SHAKE:
        return True
    return steadiness in UNSTABLE_STEADINESS


def _is_stable_content(segment, steadiness, move):
    """Return True when a final segment is a long, low-shake move or hold."""
    if segment["end_s"] - segment["start_s"] < ACCIDENTAL_MIN_SECONDS:
        return False
    if segment["metrics"].get("shake", 0.0) >= ACCIDENTAL_SHAKE:
        return False
    if segment["role"] not in ("move", "hold"):
        return False
    if segment["role"] == "hold":
        return True
    return move is not None and move.get("steadiness") in STABLE_STEADINESS


def _upgrade_to_accidental(segment, fact, confidence, evidence):
    """Set the accidental role, keeping uncertainty and flagging a contradicted move."""
    if segment["role"] == "move" and fact["move"] is not None:
        segment["uncertain_reasons"] = _dedup(
            list(segment.get("uncertain_reasons") or []) + ["role_signals_disagree"]
        )
        segment["uncertain"] = True
    segment["role"] = "accidental"
    segment["confidence"] = confidence
    segment["role_evidence"] = [evidence]


def apply_accidental_runs(segments, facts, duration_s):
    """Upgrade long chaotic runs and junk takes to the accidental role.

    ``facts`` is one ``{"steadiness", "move"}`` dict per final segment, taken from
    its longest part. A run is a maximal sequence of segments that are unstable and
    not stable content; it qualifies when it lasts at least ``ACCIDENTAL_RUN_SECONDS``
    or at least ``ACCIDENTAL_RUN_FRACTION`` of the clip. When the qualifying runs
    cover that fraction of the clip, the whole clip becomes one ``accidental`` verdict.
    """
    if not segments or duration_s <= 0:
        return
    chaotic = [
        _is_unstable(segment, fact["steadiness"])
        and not _is_stable_content(segment, fact["steadiness"], fact["move"])
        for segment, fact in zip(segments, facts)
    ]
    runs = []
    index = 0
    while index < len(segments):
        if not chaotic[index]:
            index += 1
            continue
        end = index
        while end < len(segments) and chaotic[end]:
            end += 1
        runs.append((index, end))
        index = end
    qualifying = []
    for start, end in runs:
        run_s = segments[end - 1]["end_s"] - segments[start]["start_s"]
        if run_s >= ACCIDENTAL_RUN_SECONDS or run_s >= ACCIDENTAL_RUN_FRACTION * duration_s:
            qualifying.append((start, end, run_s))
    if not qualifying:
        return
    covered_s = sum(run_s for _, _, run_s in qualifying)
    if covered_s >= ACCIDENTAL_RUN_FRACTION * duration_s:
        evidence = f"junk take: {covered_s:.1f}s of {duration_s:.1f}s has no stable move or hold"
        for segment, fact in zip(segments, facts):
            _upgrade_to_accidental(segment, fact, ACCIDENTAL_TAKE_CONFIDENCE, evidence)
        return
    for start, end, run_s in qualifying:
        mean_shake = _mean([segments[i]["metrics"].get("shake", 0.0) for i in range(start, end)])
        evidence = f"chaotic run of {run_s:.1f}s with no stable move or hold (shake {mean_shake:.2f})"
        for i in range(start, end):
            _upgrade_to_accidental(segments[i], facts[i], ACCIDENTAL_RUN_CONFIDENCE, evidence)


def _merge_and_assemble(parts, frames, clip_median_laplacian, trajectory, duration_s):
    merged = []
    for part in parts:
        if merged and merged[-1]["role"] == part["role"]:
            merged[-1]["end_s"] = part["end_s"]
            merged[-1]["confidence"] = max(merged[-1]["confidence"], part["confidence"])
            merged[-1]["evidence"] = _dedup(merged[-1]["evidence"] + part["evidence"])
            merged[-1]["parts"].append(part)
        else:
            merged.append(
                {
                    "start_s": part["start_s"],
                    "end_s": part["end_s"],
                    "role": part["role"],
                    "confidence": part["confidence"],
                    "evidence": list(part["evidence"]),
                    "parts": [part],
                }
            )
    segments = []
    facts = []
    for index, item in enumerate(merged):
        longest = max(item["parts"], key=lambda part: part["end_s"] - part["start_s"])
        label = longest["label"]
        speed = longest["speed"]
        label_source = longest["label_source"]
        seg_frames = _frames_in(frames, item["start_s"], item["end_s"])
        shake = _window_shake(trajectory, item["start_s"], item["end_s"]) if trajectory else 0.0
        horizon = segment_horizon(seg_frames)
        metrics = segment_metrics(seg_frames, clip_median_laplacian, shake, horizon)
        defects = build_defects(metrics, item["role"], label, speed)
        evidence = _dedup(item["evidence"] + intent_evidence(defects, item["role"], speed))
        motion_label_confidence = None
        uncertain_reasons = []
        if label is not None and label_source is not None:
            motion_label_confidence = label_source.get("confidence")
            uncertain_reasons = list(label_source.get("uncertain_reasons") or [])
            if label_source.get("uncertain"):
                uncertain_reasons.append("motion_uncertain")
        if longest["role_signals_disagree"]:
            uncertain_reasons.append("role_signals_disagree")
        uncertain_reasons = _dedup(uncertain_reasons)
        segments.append(
            {
                "id": f"s{index + 1}",
                "start_s": item["start_s"],
                "end_s": item["end_s"],
                "role": item["role"],
                "confidence": _segment_confidence(item["confidence"], uncertain_reasons),
                "role_evidence": evidence,
                "motion_label": label,
                "motion_label_confidence": motion_label_confidence,
                "uncertain": bool(uncertain_reasons),
                "uncertain_reasons": uncertain_reasons,
                "defects": defects,
                "metrics": metrics,
            }
        )
        facts.append({"steadiness": longest["steadiness"], "move": longest["move"]})
    apply_accidental_runs(segments, facts, duration_s)
    return segments


def _to_analysis_gray(frame, width):
    height, frame_width = frame.shape[:2]
    if frame_width != width:
        scaled_height = max(1, int(round(height * width / frame_width)))
        frame = cv2.resize(frame, (width, scaled_height), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def _load_motion(motion_path):
    if motion_path is None:
        return None, None, None
    motion_path = Path(motion_path)
    if not motion_path.exists():
        return None, None, None
    try:
        data = json.loads(motion_path.read_text(encoding="utf-8"))
        if not data.get("phases"):
            raise ValueError("no phases")
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None, None, f"unreadable motion file: {motion_path.name}"
    return data, str(motion_path.resolve()), None


def analyze_clip(path, motion_path=None, sample_fps=2.0, width=320):
    """Analyse one clip and return its full quality record."""
    path = Path(path)
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise QualityError(f"cannot read video: {path}")
    try:
        fps_source = float(capture.get(cv2.CAP_PROP_FPS))
        if not fps_source or fps_source <= 0:
            raise QualityError(f"could not determine frame rate: {path}")
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_s = frame_count / fps_source if frame_count > 0 else 0.0
        step = max(1, int(round(fps_source / sample_fps)))
        effective_fps = fps_source / step
        frames = []
        prev_gray = None
        index = 0
        while True:
            if not capture.grab():
                break
            if index % step == 0:
                ok, frame = capture.retrieve()
                if not ok:
                    break
                gray = _to_analysis_gray(frame, width)
                measurement = measure_frame(gray, prev_gray)
                measurement["t"] = index / fps_source
                frames.append(measurement)
                prev_gray = gray
            index += 1
    finally:
        capture.release()
    if duration_s <= 0 and frames:
        duration_s = frames[-1]["t"] + 1.0 / effective_fps
    clip_median_laplacian = _median([frame["laplacian"] for frame in frames])
    warnings = []
    motion_data, motion_file, motion_warning = _load_motion(motion_path)
    if motion_warning is not None:
        warnings.append(motion_warning)
    if motion_data is not None:
        parts = segments_from_motion(motion_data, duration_s, frames, clip_median_laplacian)
        trajectory = motion_data.get("trajectory") or []
    else:
        parts = segments_from_frames(frames, duration_s, effective_fps, clip_median_laplacian)
        trajectory = None
        warnings.append("no motion file; segments are coarse")
    segments = _merge_and_assemble(parts, frames, clip_median_laplacian, trajectory, duration_s)
    for segment in segments:
        if segment["end_s"] <= segment["start_s"]:
            raise QualityError(f"zero-length segment {segment['id']} in {path.name}")
    return {
        "file": path.name,
        "path": str(path.resolve()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_s": duration_s,
        "motion_file": motion_file,
        "segments": segments,
        "warnings": warnings,
    }


def find_clips(source):
    return sorted(
        (
            path
            for path in source.iterdir()
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        ),
        key=lambda path: path.name,
    )


def resolve_inputs(target):
    path = Path(target).expanduser().resolve()
    if not path.exists():
        raise QualityError(f"input not found: {path}")
    if path.is_dir():
        clips = find_clips(path)
        if not clips:
            raise QualityError(f"no video files ({', '.join(sorted(VIDEO_EXTENSIONS))}) in {path}")
        return clips
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise QualityError(f"not a video file: {path}")
    return [path]


def _output_is_valid(path):
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return validate(data, "quality") == []


def _atomic_write_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def run(input_path, workspace, motion_dir=None, force=False, sample_fps=2.0, width=320):
    """Analyse every clip of ``input_path`` into ``workspace/quality``; return a summary."""
    started = time.monotonic()
    clips = resolve_inputs(input_path)
    workspace_path = Path(workspace).expanduser().resolve()
    output_dir = workspace_path / "quality"
    output_dir.mkdir(parents=True, exist_ok=True)
    if motion_dir is None:
        motion_path_dir = workspace_path / "motion"
    else:
        motion_path_dir = Path(motion_dir).expanduser().resolve()
    analyzed = 0
    skipped = 0
    for clip in clips:
        output_path = output_dir / f"{clip.stem}.json"
        if not force and _output_is_valid(output_path):
            skipped += 1
            continue
        record = analyze_clip(
            clip,
            motion_path=motion_path_dir / f"{clip.stem}.json",
            sample_fps=sample_fps,
            width=width,
        )
        validate_or_raise(record, "quality")
        _atomic_write_json(output_path, record)
        analyzed += 1
    return {
        "clips": len(clips),
        "analyzed": analyzed,
        "skipped": skipped,
        "output_dir": str(output_dir),
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": 0.0,
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = run(
            args.input,
            args.workspace,
            motion_dir=args.motion_dir,
            force=args.force,
            sample_fps=args.sample_fps,
            width=args.width,
        )
    except (QualityError, SchemaError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_USAGE
    except OSError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    if args.json:
        print(json.dumps(summary))
    else:
        print(f"analyzed {summary['analyzed']} clips, skipped {summary['skipped']} (of {summary['clips']})")
        print(f"wrote {summary['output_dir']}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
