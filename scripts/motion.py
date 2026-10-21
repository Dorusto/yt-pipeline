#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Analyse camera motion in clips with sparse optical flow on the CPU.

Usage:
    python scripts/motion.py INPUT --workspace DIR
    python scripts/motion.py INPUT --workspace DIR --json
    python scripts/motion.py INPUT --workspace DIR --force --sample-fps 10 --width 320

INPUT is one video file or a folder scanned non-recursively for ``.mp4``,
``.mov`` and ``.m4v`` files. One Lucas-Kanade optical-flow pass with a
partial-affine RANSAC fit per sampled frame pair builds a camera trajectory,
splits the take into motion phases and labels the steady spans as CameraBench
moves with a steady range and cut handles. The residual local flow after the
global motion is removed becomes a per-second ``scene_activity`` series and
scene ``events`` (a subject entering, approaching or leaving). Each clip becomes
``DIR/motion/<stem>.json``, validated against ``schemas/motion.schema.json``
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

from common.schema import SchemaError, validate, validate_or_raise

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
METHOD = "lucas_kanade_affine_partial_ransac"
SOURCE = "optical_flow"
PAN_FLOOR = 0.03
TILT_FLOOR = 0.03
ROLL_FLOOR = 5.0
DOLLY_FLOOR = 0.05
DOLLY_CONSISTENCY = 0.7
RADIAL_FLOW_FLOOR = 1.0
RADIAL_CLAIM_FLOOR = 0.8
RADIAL_SIGN_FLOOR = 0.5
RADIAL_DOM_FLOOR = 0.30
RES_SHARE_FLOOR = 0.90
FLOW_MAG_FLOOR = 0.5
PARALLAX_BAND_LOW = 0.25
PARALLAX_BAND_HIGH = 0.5
FOE_FRAME_LIMIT = 5.0
CONFIDENCE_STEP = 0.15
CONFIDENCE_MIN = 0.05
COMPLEX_SCORE = 0.05
HYSTERESIS_SECONDS = 0.6
MIN_RUN_SECONDS = 0.4
MIN_STEADY_SECONDS = 1.5
RAMP_SECONDS = 0.5
RAMP_BAND = 0.6
TRAVEL_MIN_SECONDS = 8.0
SMOOTH_STEADINESS = ("no_shaking", "minimal_shaking")
BOB_WINDOW_SECONDS = 8.0
BOB_WINDOW_STEP_SECONDS = 1.0
BOB_PERIOD_MIN = 1.5
BOB_PERIOD_MAX = 8.0
BOB_AMPLITUDE_MIN = 0.008
BOB_CORRELATION_MIN = 0.15
BOB_MIN_CYCLES = 2.0
BOB_MIN_SPAN_SECONDS = 3.0
BOB_GAP_SECONDS = 4.0
ROLL_BOB_MIN_DEG = 0.5
PARALLAX_SCALE = 0.002
TRUCK_PARALLAX_MIN = 0.5
SUBJECT_RATIO_MAX = 0.7
SUBJECT_BOX_FRACTION = 0.4
SCENE_BAND_FRACTION = 0.7
ACTIVITY_SMOOTH_WINDOW = 3
ACTIVITY_FLOOR_PERCENTILE = 0.20
ACTIVITY_START_SIGMA = 3.0
ACTIVITY_END_SIGMA = 1.0
ACTIVITY_EVENT_MIN_AMPLITUDE = 0.001
MIN_ACTIVE_SECONDS = 3
MIN_QUIET_SECONDS = 3
MIN_RISE_SECONDS = 5
QUIET_BEFORE_SECONDS = 5
INTERNAL_ROW_FIELDS = (
    "parallax",
    "subject_centre_sum",
    "subject_centre_count",
    "subject_periphery_sum",
    "subject_periphery_count",
    "residual_activity",
    "radial_signed",
    "radial_dom_frac",
    "res_share",
    "flow_mag",
)


class MotionError(RuntimeError):
    """Raised when a clip cannot be analysed."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="motion.py",
        description=(
            "Analyse camera motion in a clip with optical flow: sampled trajectory, "
            "motion phases and CameraBench-labelled moves with steady range and handles."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/motion.py clip.mp4 --workspace ~/Videos/workspace\n"
            "  python scripts/motion.py ~/Videos/clip --workspace ~/Videos/workspace --json\n"
            "  python scripts/motion.py clip.mp4 --workspace ~/Videos/workspace --force\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="one video file, or a folder of clips (non-recursive)")
    parser.add_argument("--workspace", required=True, help="output root; motion/<stem>.json is written here")
    parser.add_argument("--force", action="store_true", help="re-analyse clips whose output already exists")
    parser.add_argument("--sample-fps", type=float, default=10.0, help="analysis sampling rate (default 10)")
    parser.add_argument("--width", type=int, default=320, help="analysis width in pixels (default 320)")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def smooth_series(values, window=5):
    """Moving average with the window shrinking at the edges."""
    if not values:
        return []
    half = max(1, window // 2)
    count = len(values)
    smoothed = []
    for index in range(count):
        low = max(0, index - half)
        high = min(count, index + half + 1)
        chunk = values[low:high]
        smoothed.append(sum(chunk) / len(chunk))
    return smoothed


def _invalid_pair():
    return {
        "dx": 0.0,
        "dy": 0.0,
        "scale": 1.0,
        "roll_deg": 0.0,
        "inlier_ratio": 0.0,
        "parallax": 0.0,
        "subject_centre_sum": 0.0,
        "subject_centre_count": 0,
        "subject_periphery_sum": 0.0,
        "subject_periphery_count": 0,
        "residual_activity": 0.0,
        "radial_signed": 0.0,
        "radial_dom_frac": 0.0,
        "res_share": 0.0,
        "flow_mag": 0.0,
    }


def _parallax_score(source_points, target_points, width):
    """Homography-fit residual score for one frame pair, 0..1."""
    if source_points is None or target_points is None or width <= 0:
        return 0.0
    source = np.asarray(source_points, dtype=np.float64).reshape(-1, 2)
    target = np.asarray(target_points, dtype=np.float64).reshape(-1, 2)
    if len(source) < 8 or len(target) < 8:
        return 0.0
    homography, _ = cv2.findHomography(
        source.reshape(-1, 1, 2), target.reshape(-1, 1, 2), cv2.RANSAC
    )
    if homography is None:
        return 0.0
    projected = cv2.perspectiveTransform(source.reshape(-1, 1, 2), homography).reshape(-1, 2)
    residuals = np.linalg.norm(projected - target, axis=1)
    median_residual = float(np.median(residuals))
    return min(1.0, (median_residual / width) / PARALLAX_SCALE)


def _subject_residuals(source_points, target_points, matrix, width, height):
    """Centre/periphery residual sums after removing the global affine motion."""
    if source_points is None or target_points is None or matrix is None:
        return 0.0, 0, 0.0, 0
    source = np.asarray(source_points, dtype=np.float64).reshape(-1, 2)
    target = np.asarray(target_points, dtype=np.float64).reshape(-1, 2)
    if len(source) == 0 or len(source) != len(target):
        return 0.0, 0, 0.0, 0
    predicted = source @ matrix[:, :2].T + matrix[:, 2]
    residuals = np.linalg.norm(target - predicted, axis=1)
    half = SUBJECT_BOX_FRACTION / 2.0
    centre_x = (source[:, 0] >= (0.5 - half) * width) & (source[:, 0] <= (0.5 + half) * width)
    centre_y = (source[:, 1] >= (0.5 - half) * height) & (source[:, 1] <= (0.5 + half) * height)
    centre_mask = centre_x & centre_y
    return (
        float(residuals[centre_mask].sum()),
        int(centre_mask.sum()),
        float(residuals[~centre_mask].sum()),
        int((~centre_mask).sum()),
    )


def _residual_activity(source_points, target_points, matrix, height):
    """Mean residual displacement of the tracked matches after removing the global motion.

    The residual is measured over the scene band (the upper part of the frame,
    excluding the near-field wake) and normalised by frame height, so a locked-off
    shot of still scenery stays near zero while an independent subject moving
    through the scene raises it.
    """
    if source_points is None or target_points is None or matrix is None or height <= 0:
        return 0.0
    source = np.asarray(source_points, dtype=np.float64).reshape(-1, 2)
    target = np.asarray(target_points, dtype=np.float64).reshape(-1, 2)
    if len(source) == 0 or len(source) != len(target):
        return 0.0
    predicted = source @ matrix[:, :2].T + matrix[:, 2]
    residuals = np.linalg.norm(target - predicted, axis=1)
    band = source[:, 1] < SCENE_BAND_FRACTION * height
    selected = residuals[band]
    if selected.size == 0:
        return 0.0
    return float(selected.mean()) / height


def _focus_of_expansion(matrix, width, height):
    """Fixed point of the affine flow, or the frame centre when it is unusable."""
    system = np.eye(2) - matrix[:, :2]
    translation = matrix[:, 2]
    try:
        foe = np.linalg.solve(system, translation)
    except np.linalg.LinAlgError:
        return np.array([width / 2.0, height / 2.0])
    if not np.all(np.isfinite(foe)):
        return np.array([width / 2.0, height / 2.0])
    if abs(foe[0]) > FOE_FRAME_LIMIT * width or abs(foe[1]) > FOE_FRAME_LIMIT * height:
        return np.array([width / 2.0, height / 2.0])
    return foe


def _radial_flow_signals(source, target, matrix, width, height):
    """Signed radial flow, residual share and flow magnitude for one frame pair."""
    flow = target - source
    foe = _focus_of_expansion(matrix, width, height)
    offsets = source - foe
    norms = np.linalg.norm(offsets, axis=1)
    unit = np.zeros_like(offsets)
    valid = norms > 0
    unit[valid] = offsets[valid] / norms[valid, None]
    if np.any(~valid):
        fallback = foe - np.array([width / 2.0, height / 2.0])
        fallback_norm = float(np.linalg.norm(fallback))
        direction = fallback / fallback_norm if fallback_norm > 0 else np.array([1.0, 0.0])
        unit[~valid] = direction
    radial = np.sum(flow * unit, axis=1)
    radial_signed = float(radial.mean())
    radial_dom_frac = float(
        max((radial > RADIAL_SIGN_FLOOR).mean(), (radial < -RADIAL_SIGN_FLOOR).mean())
    )
    predicted = source @ matrix[:, :2].T + matrix[:, 2]
    residual = np.linalg.norm(target - predicted, axis=1)
    flow_magnitudes = np.linalg.norm(flow, axis=1)
    res_share = float(residual.mean() / (flow_magnitudes.mean() + 1e-9))
    flow_mag = float(flow_magnitudes.mean())
    return radial_signed, radial_dom_frac, res_share, flow_mag


def compute_trajectory(prev_gray, next_gray):
    """Estimate the image motion between two sampled frames as a partial affine fit."""
    height, width = prev_gray.shape[:2]
    points = cv2.goodFeaturesToTrack(
        prev_gray,
        maxCorners=200,
        qualityLevel=0.01,
        minDistance=15,
        blockSize=7,
    )
    if points is None or len(points) < 8:
        return _invalid_pair()
    next_points, status, _ = cv2.calcOpticalFlowPyrLK(
        prev_gray,
        next_gray,
        points,
        None,
        winSize=(21, 21),
        maxLevel=3,
    )
    if next_points is None or status is None:
        return _invalid_pair()
    tracked = status.reshape(-1) == 1
    if int(tracked.sum()) < 8:
        return _invalid_pair()
    matrix, inliers = cv2.estimateAffinePartial2D(points[tracked], next_points[tracked], method=cv2.RANSAC)
    if matrix is None or inliers is None:
        return _invalid_pair()
    inlier_mask = inliers.reshape(-1)
    inlier_count = int(inlier_mask.sum())
    if inlier_count < 8:
        return _invalid_pair()
    a, _, tx = matrix[0]
    c, _, ty = matrix[1]
    scale = math.hypot(a, c)
    if scale <= 0:
        return _invalid_pair()
    parallax = _parallax_score(points[tracked], next_points[tracked], width)
    centre_sum, centre_count, periphery_sum, periphery_count = _subject_residuals(
        points[tracked], next_points[tracked], matrix, width, height
    )
    residual_activity = _residual_activity(points[tracked], next_points[tracked], matrix, height)
    source = points[tracked].reshape(-1, 2).astype(np.float64)
    target = next_points[tracked].reshape(-1, 2).astype(np.float64)
    radial_signed, radial_dom_frac, res_share, flow_mag = _radial_flow_signals(
        source, target, matrix, width, height
    )
    return {
        "dx": float(tx) / width,
        "dy": float(ty) / height,
        "scale": float(scale),
        "roll_deg": float(math.degrees(math.atan2(c, a))),
        "inlier_ratio": inlier_count / len(inlier_mask),
        "parallax": parallax,
        "subject_centre_sum": centre_sum,
        "subject_centre_count": centre_count,
        "subject_periphery_sum": periphery_sum,
        "subject_periphery_count": periphery_count,
        "residual_activity": residual_activity,
        "radial_signed": radial_signed,
        "radial_dom_frac": radial_dom_frac,
        "res_share": res_share,
        "flow_mag": flow_mag,
    }


def _smoothed_series(rows):
    return (
        smooth_series([row["dx"] for row in rows]),
        smooth_series([row["dy"] for row in rows]),
        smooth_series([math.log(row["scale"]) for row in rows]),
        smooth_series([row["roll_deg"] for row in rows]),
    )


def _sign_consistent(values):
    """True when at least 70% of the values share the sign of their mean."""
    if not values:
        return False
    mean = sum(values) / len(values)
    if mean == 0:
        return False
    same = sum(1 for value in values if value != 0 and (value > 0) == (mean > 0))
    return same / len(values) >= DOLLY_CONSISTENCY


def _sign_consistent_windows(rows, sample_fps):
    logs = [math.log(row["scale"]) for row in rows]
    half = max(1, int(round(0.25 * sample_fps)))
    count = len(rows)
    consistent = []
    for index in range(count):
        low = max(0, index - half)
        high = min(count, index + half + 1)
        consistent.append(_sign_consistent(logs[low:high]))
    return consistent


def _significant_channels(rate_dx, rate_dy, rate_roll, rate_scale, dolly_consistent):
    significant = set()
    if abs(rate_dx) >= PAN_FLOOR:
        significant.add("pan")
    if abs(rate_dy) >= TILT_FLOOR:
        significant.add("tilt")
    if abs(rate_roll) >= ROLL_FLOOR:
        significant.add("roll")
    if abs(rate_scale) >= DOLLY_FLOOR and dolly_consistent:
        significant.add("dolly")
    return significant


def _dominance_scores(rate_dx, rate_dy, rate_roll, rate_scale, significant):
    return {
        "pan": abs(rate_dx) if "pan" in significant else 0.0,
        "tilt": abs(rate_dy) if "tilt" in significant else 0.0,
        "roll": abs(rate_roll) / 45.0 if "roll" in significant else 0.0,
        "dolly": abs(rate_scale) if "dolly" in significant else 0.0,
    }


def _label_for(name, rate_dx, rate_dy, rate_roll, rate_scale):
    if name == "pan":
        return ("pan_right" if rate_dx < 0 else "pan_left"), False
    if name == "tilt":
        return ("tilt_up" if rate_dy > 0 else "tilt_down"), False
    if name == "roll":
        return ("roll_cw" if rate_roll < 0 else "roll_ccw"), False
    return ("dolly_in" if rate_scale > 0 else "dolly_out"), True


def _speed_score(rate_dx, rate_dy, rate_roll, rate_scale, significant):
    values = []
    if "pan" in significant:
        values.append(abs(rate_dx) - PAN_FLOOR)
    if "tilt" in significant:
        values.append(abs(rate_dy) - TILT_FLOOR)
    if "roll" in significant:
        values.append((abs(rate_roll) - ROLL_FLOOR) / 45.0)
    if "dolly" in significant:
        values.append((abs(rate_scale) - DOLLY_FLOOR) / 0.25)
    return max(values) if values else 0.0


def speed_and_shake(rows, sample_fps):
    """Attach smoothed speed and high-frequency shake to every trajectory row."""
    if not rows:
        return rows
    dx_smooth, dy_smooth, scale_smooth, roll_smooth = _smoothed_series(rows)
    dolly_consistent = _sign_consistent_windows(rows, sample_fps)
    for index, row in enumerate(rows):
        rate_dx = dx_smooth[index] * sample_fps
        rate_dy = dy_smooth[index] * sample_fps
        rate_roll = roll_smooth[index] * sample_fps
        rate_scale = scale_smooth[index] * sample_fps
        significant = _significant_channels(rate_dx, rate_dy, rate_roll, rate_scale, dolly_consistent[index])
        row["speed"] = _speed_score(rate_dx, rate_dy, rate_roll, rate_scale, significant)
        row["shake"] = math.hypot(row["dx"] - dx_smooth[index], row["dy"] - dy_smooth[index]) * sample_fps
    return rows


def _infer_sample_fps(rows):
    if len(rows) >= 2:
        span = rows[-1]["t"] - rows[0]["t"]
        if span > 0:
            return (len(rows) - 1) / span
    return 10.0


def label_window(rows, sample_fps=None):
    """Return the CameraBench label and semantic flag for a window of trajectory rows."""
    if not rows:
        return "static", False
    fps = sample_fps if sample_fps else _infer_sample_fps(rows)
    rate_dx = sum(row["dx"] for row in rows) / len(rows) * fps
    rate_dy = sum(row["dy"] for row in rows) / len(rows) * fps
    rate_roll = sum(row["roll_deg"] for row in rows) / len(rows) * fps
    rate_scale = sum(math.log(row["scale"]) for row in rows) / len(rows) * fps
    consistent = _sign_consistent([math.log(row["scale"]) for row in rows])
    significant = _significant_channels(rate_dx, rate_dy, rate_roll, rate_scale, consistent)
    if not significant:
        return "static", False
    scores = _dominance_scores(rate_dx, rate_dy, rate_roll, rate_scale, significant)
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    dominant, top_score = ordered[0]
    second_score = ordered[1][1]
    if second_score > COMPLEX_SCORE and top_score <= 2.0 * second_score:
        return "complex_motion", True
    return _label_for(dominant, rate_dx, rate_dy, rate_roll, rate_scale)


def _dominant_key(rate_dx, rate_dy, rate_roll, rate_scale, significant):
    scores = _dominance_scores(rate_dx, rate_dy, rate_roll, rate_scale, significant)
    name = max(("pan", "tilt", "roll", "dolly"), key=lambda item: scores[item])
    return _label_for(name, rate_dx, rate_dy, rate_roll, rate_scale)[0]


def _raw_keys(rows, sample_fps):
    dx_smooth, dy_smooth, scale_smooth, roll_smooth = _smoothed_series(rows)
    dolly_consistent = _sign_consistent_windows(rows, sample_fps)
    raw = []
    for index in range(len(rows)):
        rate_dx = dx_smooth[index] * sample_fps
        rate_dy = dy_smooth[index] * sample_fps
        rate_roll = roll_smooth[index] * sample_fps
        rate_scale = scale_smooth[index] * sample_fps
        significant = _significant_channels(rate_dx, rate_dy, rate_roll, rate_scale, dolly_consistent[index])
        if not significant:
            raw.append("still")
        else:
            raw.append(_dominant_key(rate_dx, rate_dy, rate_roll, rate_scale, significant))
    return raw


def _apply_hysteresis(raw_keys, sample_fps):
    if not raw_keys:
        return []
    hold = max(1, int(round(HYSTERESIS_SECONDS * sample_fps)))
    keys = [raw_keys[0]]
    current = raw_keys[0]
    candidate = None
    count = 0
    start = 0
    for index in range(1, len(raw_keys)):
        key = raw_keys[index]
        if key == current:
            candidate = None
            count = 0
        elif key == candidate:
            count += 1
        else:
            candidate = key
            count = 1
            start = index
        if candidate is not None and count >= hold:
            for position in range(start, index):
                keys[position] = candidate
            current = candidate
            candidate = None
            count = 0
        keys.append(current)
    return keys


def _run_keys(rows, sample_fps):
    return _apply_hysteresis(_raw_keys(rows, sample_fps), sample_fps)


def speed_word(score):
    """Classify a speed score as slow, regular or fast."""
    if score < 0.15:
        return "slow"
    if score <= 0.6:
        return "regular"
    return "fast"


def steadiness_word(shake, label=None):
    """Classify shake energy as static, no_shaking, minimal_shaking, unsteady or very_unsteady."""
    if label == "static" and shake < 0.01:
        return "static"
    if shake < 0.01:
        return "no_shaking"
    if shake < 0.03:
        return "minimal_shaking"
    if shake < 0.08:
        return "unsteady"
    return "very_unsteady"


def _median(values):
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        return 0.0
    middle = count // 2
    if count % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _percentile(values, fraction):
    """Linear-interpolated percentile of a list of numbers."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = fraction * (len(ordered) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def scene_activity_series(rows, duration_s):
    """Aggregate per-sample residual activity into one-second bins covering the clip.

    Every second of ``0 .. duration_s`` gets a row; ``activity`` is the mean
    residual displacement as a fraction of frame height, kept on its raw scale.
    """
    bin_count = max(1, int(math.ceil(duration_s)))
    sums = [0.0] * bin_count
    counts = [0] * bin_count
    for row in rows:
        index = int(row["t"])
        if index < 0:
            continue
        if index >= bin_count:
            index = bin_count - 1
        sums[index] += row.get("residual_activity", 0.0)
        counts[index] += 1
    return [
        {"t_s": index + 0.5, "activity": sums[index] / counts[index] if counts[index] else 0.0}
        for index in range(bin_count)
    ]


def _activity_start_event(smoothed, onset, low, floor):
    quiet = onset >= QUIET_BEFORE_SECONDS and all(
        value <= low for value in smoothed[onset - QUIET_BEFORE_SECONDS:onset]
    )
    note = f"activity {smoothed[onset] - floor:.4f} above floor"
    return {"t_s": float(onset), "type": "subject_enters" if quiet else "activity_start", "note": note}


def _activity_rise_event(smoothed, onset, peak_index, noise):
    if peak_index - onset < MIN_RISE_SECONDS:
        return None
    climbing = all(
        smoothed[index + 1] >= smoothed[index] for index in range(onset, onset + MIN_RISE_SECONDS)
    )
    if not climbing:
        return None
    rise = smoothed[onset + MIN_RISE_SECONDS] - smoothed[onset]
    if rise < ACTIVITY_EVENT_MIN_AMPLITUDE:
        return None
    note = f"rising activity, {rise:.4f} above start"
    event_type = "subject_approaches" if rise > ACTIVITY_START_SIGMA * noise else "activity_rise"
    return {"t_s": float(onset + MIN_RISE_SECONDS), "type": event_type, "note": note}


def _activity_end_event(smoothed, fall_index, peak_index, floor, noise):
    note = (
        f"activity fell from peak {smoothed[peak_index] - floor:.4f} "
        f"to {smoothed[fall_index] - floor:.4f} above floor"
    )
    strong = smoothed[peak_index] - floor >= 2.0 * ACTIVITY_START_SIGMA * noise
    event_type = "subject_leaves" if strong else "activity_end"
    return {"t_s": float(fall_index), "type": event_type, "note": note}


def _span_active_end(smoothed, span_start, span_end, high):
    """Last index of a floor-level span that is above ``high``, or None when the span is not active."""
    active_end = None
    longest = 0
    current = 0
    for position in range(span_start, span_end + 1):
        if smoothed[position] > high:
            current += 1
            longest = max(longest, current)
            active_end = position
        else:
            current = 0
    if longest < MIN_ACTIVE_SECONDS:
        return None
    return active_end


def _span_fall_index(smoothed, active_end, low):
    """First index from ``active_end`` where activity stays below ``low`` for MIN_QUIET_SECONDS."""
    count = len(smoothed)
    position = active_end
    while position < count:
        if all(
            smoothed[position + offset] < low
            for offset in range(min(MIN_QUIET_SECONDS, count - position))
        ):
            return position
        position += 1
    return None


def _dedupe_events(events):
    """Drop events whose ``(t_s, type)`` was already emitted."""
    seen = set()
    unique = []
    for event in events:
        key = (event["t_s"], event["type"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    return unique


def detect_scene_events(activities):
    """Detect scene events from a per-second activity series.

    A floor-level span (consecutive samples above ``floor``) is one active period;
    the ``high`` threshold only decides whether it is interesting, so a long span
    that dips below ``high`` but stays above ``floor`` still yields one start, one
    peak and one end. A span whose peak stays below ``floor`` plus
    ``ACTIVITY_EVENT_MIN_AMPLITUDE`` is a near-zero blip and yields nothing. The
    start event is placed at the first sample of the span that is at least
    ``ACTIVITY_EVENT_MIN_AMPLITUDE`` above the floor, so its note never reports
    sub-floor evidence. Events are proposals, never verdicts: a clear-cut pattern
    gets the semantic name (subject_enters / subject_approaches / subject_leaves),
    otherwise the honest activity_* name is used.
    """
    if not activities:
        return []
    smoothed = smooth_series(activities, ACTIVITY_SMOOTH_WINDOW)
    floor = _percentile(smoothed, ACTIVITY_FLOOR_PERCENTILE)
    noise = _median([abs(value - floor) for value in smoothed])
    high = floor + ACTIVITY_START_SIGMA * noise
    low = floor + ACTIVITY_END_SIGMA * noise
    count = len(smoothed)
    events = []
    index = 0
    while index < count:
        if smoothed[index] <= floor:
            index += 1
            continue
        span_start = index
        while index < count and smoothed[index] > floor:
            index += 1
        span_end = index - 1
        active_end = _span_active_end(smoothed, span_start, span_end, high)
        if active_end is None:
            continue
        peak_index = max(range(span_start, span_end + 1), key=lambda position: smoothed[position])
        if smoothed[peak_index] - floor < ACTIVITY_EVENT_MIN_AMPLITUDE:
            continue
        onset = next(
            position
            for position in range(span_start, span_end + 1)
            if smoothed[position] - floor >= ACTIVITY_EVENT_MIN_AMPLITUDE
        )
        events.append(_activity_start_event(smoothed, onset, low, floor))
        events.append(
            {
                "t_s": float(peak_index),
                "type": "activity_peak",
                "note": f"peak {smoothed[peak_index] - floor:.4f} above floor",
            }
        )
        rise = _activity_rise_event(smoothed, span_start, peak_index, noise)
        if rise is not None:
            events.append(rise)
        fall_index = _span_fall_index(smoothed, active_end, low)
        if fall_index is not None:
            events.append(_activity_end_event(smoothed, fall_index, peak_index, floor, noise))
    events = _dedupe_events(events)
    events.sort(key=lambda event: event["t_s"])
    return events


def _solve_3x3(matrix, vector):
    a, b, c = matrix[0]
    d, e, f = matrix[1]
    g, h, i = matrix[2]
    determinant = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(determinant) < 1e-12:
        return None
    x = (
        vector[0] * (e * i - f * h)
        - b * (vector[1] * i - f * vector[2])
        + c * (vector[1] * h - e * vector[2])
    ) / determinant
    y = (
        a * (vector[1] * i - f * vector[2])
        - vector[0] * (d * i - f * g)
        + c * (d * vector[2] - vector[1] * g)
    ) / determinant
    z = (
        a * (e * vector[2] - vector[1] * h)
        - b * (d * vector[2] - vector[1] * g)
        + vector[0] * (d * h - e * g)
    ) / determinant
    return x, y, z


def _sinusoid_fit(times, values, period):
    """Least-squares constant/sin/cos fit at ``period``; returns (c, a, b) or None."""
    if period <= 0 or len(values) < 4:
        return None
    omega = 2.0 * math.pi / period
    count = len(values)
    sum_s = sum_c = sum_y = 0.0
    sum_ss = sum_cc = sum_sc = 0.0
    sum_sy = sum_cy = 0.0
    for moment, value in zip(times, values):
        sine = math.sin(omega * moment)
        cosine = math.cos(omega * moment)
        sum_s += sine
        sum_c += cosine
        sum_y += value
        sum_ss += sine * sine
        sum_cc += cosine * cosine
        sum_sc += sine * cosine
        sum_sy += sine * value
        sum_cy += cosine * value
    matrix = [
        [count, sum_s, sum_c],
        [sum_s, sum_ss, sum_sc],
        [sum_c, sum_sc, sum_cc],
    ]
    solution = _solve_3x3(matrix, [sum_y, sum_sy, sum_cy])
    if solution is None:
        return None
    return solution[0], solution[1], solution[2]


def _autocorr_period(values, sample_fps):
    """Dominant period in seconds of a detrended series and its correlation, or None."""
    count = len(values)
    if count < 4 or sample_fps <= 0:
        return None
    mean = sum(values) / count
    centered = [value - mean for value in values]
    energy = sum(value * value for value in centered)
    if energy <= 0:
        return None
    low = max(1, int(round(BOB_PERIOD_MIN * sample_fps)))
    high = min(count - 2, int(round(BOB_PERIOD_MAX * sample_fps)))
    if high <= low:
        return None
    correlations = {}
    best_lag = None
    best_value = -2.0
    for lag in range(low, high + 1):
        total = sum(centered[index] * centered[index + lag] for index in range(count - lag))
        value = total / energy
        correlations[lag] = value
        if value > best_value:
            best_value = value
            best_lag = lag
    if best_lag is None:
        return None
    threshold = 0.7 * best_value
    for lag in range(low + 1, best_lag):
        if (
            correlations[lag] >= threshold
            and correlations[lag] >= correlations[lag - 1]
            and correlations[lag] >= correlations[lag + 1]
        ):
            return lag / sample_fps, correlations[lag]
    return best_lag / sample_fps, best_value


def detect_platform_bob(rows, sample_fps):
    """Find spans where the camera bobs periodically on a floating platform."""
    if not rows or sample_fps <= 0:
        return []
    window = max(4, int(round(BOB_WINDOW_SECONDS * sample_fps)))
    step = max(1, int(round(BOB_WINDOW_STEP_SECONDS * sample_fps)))
    if len(rows) < window:
        return []
    accepted = []
    for start in range(0, len(rows) - window + 1, step):
        chunk = rows[start : start + window]
        dy = [row["dy"] for row in chunk]
        amplitude = max(dy) - min(dy)
        if amplitude < BOB_AMPLITUDE_MIN:
            continue
        estimate = _autocorr_period(dy, sample_fps)
        if estimate is None:
            continue
        period, correlation = estimate
        if correlation < BOB_CORRELATION_MIN:
            continue
        if not (BOB_PERIOD_MIN <= period <= BOB_PERIOD_MAX):
            continue
        if BOB_WINDOW_SECONDS / period < BOB_MIN_CYCLES:
            continue
        accepted.append((start // step, chunk[0]["t"], chunk[-1]["t"], period, amplitude))
    spans = []
    for ordinal, start_s, end_s, period, amplitude in accepted:
        if spans and ordinal == spans[-1]["last_ordinal"] + 1:
            spans[-1]["last_ordinal"] = ordinal
            spans[-1]["end_s"] = end_s
            spans[-1]["windows"].append({"start_s": start_s, "end_s": end_s, "period_s": period})
            spans[-1]["periods"].append(period)
            spans[-1]["amplitudes"].append(amplitude)
        else:
            spans.append(
                {
                    "last_ordinal": ordinal,
                    "start_s": start_s,
                    "end_s": end_s,
                    "windows": [{"start_s": start_s, "end_s": end_s, "period_s": period}],
                    "periods": [period],
                    "amplitudes": [amplitude],
                }
            )
    for index in range(len(spans) - 1):
        spans[index]["end_s"] = min(spans[index]["end_s"], spans[index + 1]["start_s"])
    merged = []
    for span in spans:
        if merged and span["start_s"] - merged[-1]["end_s"] <= BOB_GAP_SECONDS:
            merged[-1]["end_s"] = max(merged[-1]["end_s"], span["end_s"])
            merged[-1]["windows"].extend(span["windows"])
            merged[-1]["periods"].extend(span["periods"])
            merged[-1]["amplitudes"].extend(span["amplitudes"])
        else:
            merged.append(span)
    result = []
    for span in merged:
        if span["end_s"] - span["start_s"] < BOB_MIN_SPAN_SECONDS:
            continue
        result.append(
            {
                "start_s": span["start_s"],
                "end_s": span["end_s"],
                "period_s": _median(span["periods"]),
                "amplitude": _median(span["amplitudes"]),
                "windows": span["windows"],
            }
        )
    return result


def _clean_bob(rows, spans, sample_fps):
    """Return copies of ``rows`` with the fitted platform oscillation removed."""
    cleaned = [dict(row) for row in rows]
    for span in spans:
        windows = span.get("windows") or [span]
        fits = []
        for window in windows:
            indices = [index for index, row in enumerate(rows) if window["start_s"] <= row["t"] <= window["end_s"]]
            if len(indices) < 4:
                continue
            times = [rows[index]["t"] for index in indices]
            dy_fit = _sinusoid_fit(times, [rows[index]["dy"] for index in indices], window["period_s"])
            roll_fit = _sinusoid_fit(times, [rows[index]["roll_deg"] for index in indices], window["period_s"])
            fits.append((window, dy_fit, roll_fit))
        if not fits:
            continue
        for row in cleaned:
            if not (span["start_s"] <= row["t"] <= span["end_s"]):
                continue
            window, dy_fit, roll_fit = min(
                fits, key=lambda item: abs((item[0]["start_s"] + item[0]["end_s"]) / 2 - row["t"])
            )
            omega = 2.0 * math.pi / window["period_s"]
            if dy_fit is not None:
                constant, a, b = dy_fit
                row["dy"] = row["dy"] - (
                    constant + a * math.sin(omega * row["t"]) + b * math.cos(omega * row["t"])
                )
            if roll_fit is not None and math.hypot(roll_fit[1], roll_fit[2]) >= ROLL_BOB_MIN_DEG:
                constant, a, b = roll_fit
                row["roll_deg"] = row["roll_deg"] - (
                    constant + a * math.sin(omega * row["t"]) + b * math.cos(omega * row["t"])
                )
    return cleaned


def _travel_upgrade(label, duration_s, steadiness):
    """Upgrade a long smooth scale run to a travel label."""
    if duration_s < TRAVEL_MIN_SECONDS or steadiness not in SMOOTH_STEADINESS:
        return label
    if label == "dolly_in":
        return "travel_forward"
    if label == "dolly_out":
        return "travel_backward"
    return label


def _platform_bob_for_move(start_s, end_s, spans):
    if not spans:
        return None
    overlapping = [span for span in spans if span["start_s"] < end_s and span["end_s"] > start_s]
    if not overlapping:
        return None
    return {
        "period_s": _median([span["period_s"] for span in overlapping]),
        "amplitude": _median([span["amplitude"] for span in overlapping]),
    }


def _subject_is_centred(rows):
    """True when the centre of frame moves with the global motion and the periphery does not."""
    centre_sum = sum(row.get("subject_centre_sum", 0.0) for row in rows)
    centre_count = sum(row.get("subject_centre_count", 0) for row in rows)
    periphery_sum = sum(row.get("subject_periphery_sum", 0.0) for row in rows)
    periphery_count = sum(row.get("subject_periphery_count", 0) for row in rows)
    if centre_count == 0 or periphery_count == 0:
        return False
    centre_mean = centre_sum / centre_count
    periphery_mean = periphery_sum / periphery_count
    if periphery_mean <= 0:
        return False
    return centre_mean <= SUBJECT_RATIO_MAX * periphery_mean


def _runs(classes):
    runs = []
    for index, value in enumerate(classes):
        if runs and runs[-1][0] == value:
            runs[-1][2] = index + 1
        else:
            runs.append([value, index, index + 1])
    return runs


def _merge_short_runs(runs, sample_fps):
    merged = []
    for value, start, end in runs:
        if merged and (end - start) / sample_fps < MIN_RUN_SECONDS:
            merged[-1][2] = end
        else:
            merged.append([value, start, end])
    if len(merged) > 1 and (merged[0][2] - merged[0][1]) / sample_fps < MIN_RUN_SECONDS:
        merged[1][1] = merged[0][1]
        merged.pop(0)
    coalesced = []
    for value, start, end in merged:
        if coalesced and coalesced[-1][0] == value:
            coalesced[-1][2] = end
        else:
            coalesced.append([value, start, end])
    return coalesced


def window_uncertainty(rows, label, parallax, sample_fps, label_geometric=None):
    """Return ``(uncertain, reasons)`` for a labelled window of trajectory rows.

    A reason is added at most once; ``uncertain`` is true when any reason fired.
    """
    reasons = []
    if label == "complex_motion":
        reasons.append("mixed_motion_signals")
    pan_family = label in ("pan_left", "pan_right") or label_geometric in ("pan_left", "pan_right")
    if pan_family and PARALLAX_BAND_LOW <= parallax < PARALLAX_BAND_HIGH:
        reasons.append("parallax_vs_rotation")
    if not rows:
        return bool(reasons), reasons
    count = len(rows)
    radial_signed = sum(row.get("radial_signed", 0.0) for row in rows) / count
    radial_dom_frac = sum(row.get("radial_dom_frac", 0.0) for row in rows) / count
    res_share = sum(row.get("res_share", 0.0) for row in rows) / count
    flow_mag = sum(row.get("flow_mag", 0.0) for row in rows) / count
    scale_rate = sum(math.log(row["scale"]) for row in rows) / count * sample_fps
    rate_dx = sum(row["dx"] for row in rows) / count * sample_fps
    rate_dy = sum(row["dy"] for row in rows) / count * sample_fps
    rate_roll = sum(row["roll_deg"] for row in rows) / count * sample_fps
    if (
        abs(radial_signed) >= RADIAL_FLOW_FLOOR
        and radial_dom_frac >= RADIAL_DOM_FLOOR
        and abs(scale_rate) < DOLLY_FLOOR
    ):
        reasons.append("scale_vs_radial_flow")
    camera_claim = (
        abs(radial_signed) >= RADIAL_CLAIM_FLOOR
        or abs(scale_rate) >= DOLLY_FLOOR
        or abs(rate_dx) >= PAN_FLOOR
        or abs(rate_dy) >= TILT_FLOOR
        or abs(rate_roll) >= ROLL_FLOOR
    )
    if res_share >= RES_SHARE_FLOOR and flow_mag >= FLOW_MAG_FLOOR and camera_claim:
        reasons.append("self_moving_regions")
    return bool(reasons), reasons


def _phase_confidence(length_s, mean_inlier, mean_shake, reasons):
    confidence = 0.5
    if length_s >= 3:
        confidence += 0.1
    if mean_inlier >= 0.7:
        confidence += 0.1
    if mean_shake < 0.03:
        confidence += 0.1
    confidence -= CONFIDENCE_STEP * len(reasons)
    return min(1.0, max(CONFIDENCE_MIN, confidence))


def _make_phase(start_s, end_s, rows, sample_fps, phase_type, label_rows=None):
    label_source = label_rows if label_rows is not None else rows
    label, _ = label_window(label_source, sample_fps)
    mean_speed = sum(row["speed"] for row in rows) / len(rows)
    mean_shake = sum(row["shake"] for row in rows) / len(rows)
    mean_inlier = sum(row["inlier_ratio"] for row in rows) / len(rows)
    parallax = _median([row.get("parallax", 0.0) for row in rows])
    if phase_type == "still":
        dominant = "static"
        speed = "slow"
        steadiness = steadiness_word(mean_shake, "static")
    else:
        dominant = label
        speed = speed_word(mean_speed)
        steadiness = steadiness_word(mean_shake, label)
        if phase_type == "steady_move":
            dominant = _travel_upgrade(dominant, end_s - start_s, steadiness)
    uncertain, reasons = window_uncertainty(rows, dominant, parallax, sample_fps)
    confidence = _phase_confidence(end_s - start_s, mean_inlier, mean_shake, reasons)
    return {
        "start_s": start_s,
        "end_s": end_s,
        "type": phase_type,
        "dominant_motion": dominant,
        "speed": speed,
        "steadiness": steadiness,
        "confidence": confidence,
        "uncertain": uncertain,
        "uncertain_reasons": reasons,
    }


def _split_moving_run(run_rows, start_s, end_s, sample_fps, label_rows=None):
    if end_s - start_s <= 1.0:
        return [_make_phase(start_s, end_s, run_rows, sample_fps, "irregular", label_rows)]
    mean_shake = sum(row["shake"] for row in run_rows) / len(run_rows)
    if mean_shake > 0.08:
        return [_make_phase(start_s, end_s, run_rows, sample_fps, "irregular", label_rows)]
    band = RAMP_BAND * _median([row["speed"] for row in run_rows])
    ramp_up_end = 0
    for index, row in enumerate(run_rows):
        if row["t"] - start_s >= RAMP_SECONDS:
            break
        if row["speed"] >= band:
            break
        ramp_up_end = index + 1
    ramp_down_start = len(run_rows)
    for index in range(len(run_rows) - 1, -1, -1):
        row = run_rows[index]
        if end_s - row["t"] > RAMP_SECONDS:
            break
        if row["speed"] >= band:
            break
        ramp_down_start = index
    if ramp_up_end >= ramp_down_start:
        return [_make_phase(start_s, end_s, run_rows, sample_fps, "irregular", label_rows)]
    phases = []
    if ramp_up_end > 0:
        phases.append(
            _make_phase(
                start_s,
                start_s + ramp_up_end / sample_fps,
                run_rows[:ramp_up_end],
                sample_fps,
                "ramp_up",
                label_rows[:ramp_up_end] if label_rows is not None else None,
            )
        )
    phases.append(
        _make_phase(
            start_s + ramp_up_end / sample_fps,
            start_s + ramp_down_start / sample_fps,
            run_rows[ramp_up_end:ramp_down_start],
            sample_fps,
            "steady_move",
            label_rows[ramp_up_end:ramp_down_start] if label_rows is not None else None,
        )
    )
    if ramp_down_start < len(run_rows):
        phases.append(
            _make_phase(
                start_s + ramp_down_start / sample_fps,
                end_s,
                run_rows[ramp_down_start:],
                sample_fps,
                "ramp_down",
                label_rows[ramp_down_start:] if label_rows is not None else None,
            )
        )
    return phases


def segment_phases(rows, duration_s, sample_fps, label_rows=None):
    """Split the take into motion phases that tile ``[0, duration_s]`` exactly."""
    if not rows or duration_s <= 0:
        total = max(duration_s, 0.0)
        return [
            {
                "start_s": 0.0,
                "end_s": total,
                "type": "irregular",
                "dominant_motion": "static",
                "speed": "slow",
                "steadiness": steadiness_word(0.0),
                "confidence": _phase_confidence(total, 1.0, 0.0, []),
                "uncertain": False,
                "uncertain_reasons": [],
            }
        ]
    if "speed" not in rows[0]:
        speed_and_shake(rows, sample_fps)
    phases = []
    for key, start, end in _merge_short_runs(_runs(_run_keys(rows, sample_fps)), sample_fps):
        run_rows = rows[start:end]
        run_label_rows = label_rows[start:end] if label_rows is not None else None
        start_s = start / sample_fps
        end_s = end / sample_fps
        if key == "still":
            phases.append(_make_phase(start_s, end_s, run_rows, sample_fps, "still", run_label_rows))
        else:
            phases.extend(_split_moving_run(run_rows, start_s, end_s, sample_fps, run_label_rows))
    phases[0]["start_s"] = 0.0
    for index in range(1, len(phases)):
        phases[index]["start_s"] = phases[index - 1]["end_s"]
    phases[-1]["end_s"] = max(phases[-1]["start_s"], duration_s)
    return phases


def _window_rows(rows, start_s, end_s):
    return [row for row in rows if start_s <= row["t"] < end_s]


def build_moves(phases, rows, sample_fps, bob_spans=None):
    """Carve usable moves out of the steady phases, with range, handles and label."""
    if rows and "speed" not in rows[0]:
        speed_and_shake(rows, sample_fps)
    steady_indices = [
        index
        for index, phase in enumerate(phases)
        if phase["type"] == "steady_move" and phase["end_s"] - phase["start_s"] >= MIN_STEADY_SECONDS
    ]
    groups = []
    for index in steady_indices:
        phase = phases[index]
        joins = False
        if groups:
            between = phases[groups[-1]["last_index"] + 1 : index]
            joins = (
                groups[-1]["label"] == phase["dominant_motion"]
                and phase["start_s"] - groups[-1]["steady_end_s"] < 0.5
                and all(item["type"] in ("ramp_up", "ramp_down") for item in between)
            )
        if joins:
            groups[-1]["steady_end_s"] = phase["end_s"]
            groups[-1]["last_index"] = index
        else:
            groups.append(
                {
                    "first_index": index,
                    "last_index": index,
                    "steady_start_s": phase["start_s"],
                    "steady_end_s": phase["end_s"],
                    "label": phase["dominant_motion"],
                }
            )
    moves = []
    for group in groups:
        steady_rows = _window_rows(rows, group["steady_start_s"], group["steady_end_s"])
        if not steady_rows:
            continue
        first_index = group["first_index"]
        last_index = group["last_index"]
        start_s = group["steady_start_s"]
        if first_index - 1 >= 0 and phases[first_index - 1]["type"] == "ramp_up":
            start_s = phases[first_index - 1]["start_s"]
        end_s = group["steady_end_s"]
        if last_index + 1 < len(phases) and phases[last_index + 1]["type"] == "ramp_down":
            end_s = phases[last_index + 1]["end_s"]
        label, semantic = label_window(steady_rows, sample_fps)
        mean_speed = sum(row["speed"] for row in steady_rows) / len(steady_rows)
        mean_shake = sum(row["shake"] for row in steady_rows) / len(steady_rows)
        mean_inlier = sum(row["inlier_ratio"] for row in steady_rows) / len(steady_rows)
        steady_length = group["steady_end_s"] - group["steady_start_s"]
        steadiness = steadiness_word(mean_shake, label)
        parallax = _median([row.get("parallax", 0.0) for row in steady_rows])
        label_geometric = None
        platform = None
        if label == "static":
            platform = "static"
        elif label in ("dolly_in", "dolly_out") and steady_length >= TRAVEL_MIN_SECONDS and steadiness in SMOOTH_STEADINESS:
            label_geometric = label
            label = "travel_forward" if label == "dolly_in" else "travel_backward"
            platform = "vehicle"
        elif label in ("pan_left", "pan_right") and parallax >= TRUCK_PARALLAX_MIN:
            label_geometric = label
            if steadiness in SMOOTH_STEADINESS and _subject_is_centred(steady_rows):
                label = "orbit_candidate"
                semantic = True
            else:
                label = "truck_right" if label == "pan_right" else "truck_left"
        confidence = 0.5
        if steady_length >= 3:
            confidence += 0.2
        if mean_inlier >= 0.7:
            confidence += 0.1
        if mean_shake < 0.03:
            confidence += 0.1
        if semantic:
            confidence -= 0.2
        uncertain, reasons = window_uncertainty(
            steady_rows, label, parallax, sample_fps, label_geometric
        )
        confidence -= CONFIDENCE_STEP * len(reasons)
        confidence = min(1.0, max(CONFIDENCE_MIN, confidence))
        invalid = sum(
            1 for row in rows if start_s <= row["t"] < end_s and row.get("inlier_ratio", 1.0) == 0
        )
        moves.append(
            {
                "id": f"m{len(moves) + 1}",
                "start_s": start_s,
                "end_s": end_s,
                "label": label,
                "label_geometric": label_geometric,
                "platform": platform,
                "platform_bob": _platform_bob_for_move(start_s, end_s, bob_spans),
                "parallax": parallax,
                "speed": speed_word(mean_speed),
                "steadiness": steadiness,
                "steady_start_s": group["steady_start_s"],
                "steady_end_s": group["steady_end_s"],
                "handle_in_s": max(start_s, group["steady_start_s"] - 0.25),
                "handle_out_s": min(end_s, group["steady_end_s"] + 0.25),
                "confidence": confidence,
                "mean_dx": sum(row["dx"] for row in steady_rows) / len(steady_rows),
                "mean_dy": sum(row["dy"] for row in steady_rows) / len(steady_rows),
                "mean_scale": sum(row["scale"] for row in steady_rows) / len(steady_rows),
                "mean_roll_deg": sum(row["roll_deg"] for row in steady_rows) / len(steady_rows),
                "semantic_check": semantic,
                "uncertain": uncertain,
                "uncertain_reasons": reasons,
                "notes": f"invalid trajectory rows: {invalid}" if invalid else "",
            }
        )
    return moves


def _to_analysis_gray(frame, width):
    height, frame_width = frame.shape[:2]
    if frame_width != width:
        scaled_height = max(1, int(round(height * width / frame_width)))
        frame = cv2.resize(frame, (width, scaled_height), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def analyze_clip(path, sample_fps=10.0, width=320):
    """Analyse one clip and return its full motion record."""
    path = Path(path)
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise MotionError(f"cannot read video: {path}")
    try:
        fps_source = float(capture.get(cv2.CAP_PROP_FPS))
        if not fps_source or fps_source <= 0:
            raise MotionError(f"could not determine frame rate: {path}")
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_s = frame_count / fps_source if frame_count > 0 else 0.0
        step = max(1, int(round(fps_source / sample_fps)))
        effective_fps = fps_source / step
        analysis_width = int(width)
        rows = []
        frames_analyzed = 0
        prev_gray = None
        index = 0
        while True:
            if not capture.grab():
                break
            if index % step == 0:
                ok, frame = capture.retrieve()
                if not ok:
                    break
                gray = _to_analysis_gray(frame, analysis_width)
                frames_analyzed += 1
                if prev_gray is not None:
                    motion = compute_trajectory(prev_gray, gray)
                    motion["t"] = (len(rows) + 0.5) / effective_fps
                    rows.append(motion)
                prev_gray = gray
            index += 1
    finally:
        capture.release()
    if duration_s <= 0 and frames_analyzed:
        duration_s = frames_analyzed * step / fps_source
    speed_and_shake(rows, effective_fps)
    bob_spans = detect_platform_bob(rows, effective_fps)
    cleaned = _clean_bob(rows, bob_spans, effective_fps)
    speed_and_shake(cleaned, effective_fps)
    phases = segment_phases(rows, duration_s, effective_fps, label_rows=cleaned)
    moves = build_moves(phases, cleaned, effective_fps, bob_spans)
    warnings = []
    invalid = sum(1 for row in rows if row["inlier_ratio"] == 0)
    if rows and invalid == len(rows):
        warnings.append("no valid trajectory rows")
    elif invalid:
        warnings.append(f"invalid trajectory rows: {invalid}")
    trajectory = [
        {key: value for key, value in row.items() if key not in INTERNAL_ROW_FIELDS}
        for row in rows
    ]
    scene_activity = scene_activity_series(rows, duration_s)
    events = detect_scene_events([row["activity"] for row in scene_activity])
    return {
        "file": path.name,
        "path": str(path.resolve()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_s": duration_s,
        "fps_source": fps_source,
        "analysis": {
            "method": METHOD,
            "source": SOURCE,
            "sample_fps": effective_fps,
            "width": analysis_width,
            "frames_analyzed": frames_analyzed,
        },
        "trajectory": trajectory,
        "phases": phases,
        "moves": moves,
        "scene_activity": scene_activity,
        "events": events,
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
        raise MotionError(f"input not found: {path}")
    if path.is_dir():
        clips = find_clips(path)
        if not clips:
            raise MotionError(f"no video files ({', '.join(sorted(VIDEO_EXTENSIONS))}) in {path}")
        return clips
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise MotionError(f"not a video file: {path}")
    return [path]


def _output_is_valid(path):
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return validate(data, "motion") == []


def _atomic_write_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def run(input_path, workspace, force=False, sample_fps=10.0, width=320):
    """Analyse every clip of ``input_path`` into ``workspace/motion``; return a summary."""
    started = time.monotonic()
    clips = resolve_inputs(input_path)
    output_dir = Path(workspace).expanduser().resolve() / "motion"
    output_dir.mkdir(parents=True, exist_ok=True)
    analyzed = 0
    skipped = 0
    for clip in clips:
        output_path = output_dir / f"{clip.stem}.json"
        if not force and _output_is_valid(output_path):
            skipped += 1
            continue
        record = analyze_clip(clip, sample_fps=sample_fps, width=width)
        validate_or_raise(record, "motion")
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
            force=args.force,
            sample_fps=args.sample_fps,
            width=args.width,
        )
    except (MotionError, SchemaError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary))
    else:
        print(f"analyzed {summary['analyzed']} clips, skipped {summary['skipped']} (of {summary['clips']})")
        print(f"wrote {summary['output_dir']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
