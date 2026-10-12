#!/usr/bin/env python3
"""Analyse camera motion in clips with sparse optical flow on the CPU.

Usage:
    python scripts/motion.py INPUT --workspace DIR
    python scripts/motion.py INPUT --workspace DIR --json
    python scripts/motion.py INPUT --workspace DIR --force --sample-fps 10 --width 320

INPUT is one video file or a folder scanned non-recursively for ``.mp4``,
``.mov`` and ``.m4v`` files. One Lucas-Kanade optical-flow pass with a
partial-affine RANSAC fit per sampled frame pair builds a camera trajectory,
splits the take into motion phases and labels the steady spans as CameraBench
moves with a steady range and cut handles. Each clip becomes
``DIR/motion/<stem>.json``, validated against ``schemas/motion.schema.json``
and written atomically. Source files are read only.
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2

from common.schema import SchemaError, validate, validate_or_raise

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
METHOD = "lucas_kanade_affine_partial_ransac"
SOURCE = "optical_flow"
PAN_FLOOR = 0.03
TILT_FLOOR = 0.03
ROLL_FLOOR = 5.0
DOLLY_FLOOR = 0.05
DOLLY_CONSISTENCY = 0.7
COMPLEX_SCORE = 0.05
HYSTERESIS_SECONDS = 0.6
MIN_RUN_SECONDS = 0.4
MIN_STEADY_SECONDS = 1.5
RAMP_SECONDS = 0.5
RAMP_BAND = 0.6


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
    return {"dx": 0.0, "dy": 0.0, "scale": 1.0, "roll_deg": 0.0, "inlier_ratio": 0.0}


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
    return {
        "dx": float(tx) / width,
        "dy": float(ty) / height,
        "scale": float(scale),
        "roll_deg": float(math.degrees(math.atan2(c, a))),
        "inlier_ratio": inlier_count / len(inlier_mask),
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


def _make_phase(start_s, end_s, rows, sample_fps, phase_type):
    label, _ = label_window(rows, sample_fps)
    mean_speed = sum(row["speed"] for row in rows) / len(rows)
    mean_shake = sum(row["shake"] for row in rows) / len(rows)
    if phase_type == "still":
        dominant = "static"
        speed = "slow"
        steadiness = steadiness_word(mean_shake, "static")
    else:
        dominant = label
        speed = speed_word(mean_speed)
        steadiness = steadiness_word(mean_shake, label)
    return {
        "start_s": start_s,
        "end_s": end_s,
        "type": phase_type,
        "dominant_motion": dominant,
        "speed": speed,
        "steadiness": steadiness,
    }


def _split_moving_run(run_rows, start_s, end_s, sample_fps):
    if end_s - start_s <= 1.0:
        return [_make_phase(start_s, end_s, run_rows, sample_fps, "irregular")]
    mean_shake = sum(row["shake"] for row in run_rows) / len(run_rows)
    if mean_shake > 0.08:
        return [_make_phase(start_s, end_s, run_rows, sample_fps, "irregular")]
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
        return [_make_phase(start_s, end_s, run_rows, sample_fps, "irregular")]
    phases = []
    if ramp_up_end > 0:
        phases.append(
            _make_phase(start_s, start_s + ramp_up_end / sample_fps, run_rows[:ramp_up_end], sample_fps, "ramp_up")
        )
    phases.append(
        _make_phase(
            start_s + ramp_up_end / sample_fps,
            start_s + ramp_down_start / sample_fps,
            run_rows[ramp_up_end:ramp_down_start],
            sample_fps,
            "steady_move",
        )
    )
    if ramp_down_start < len(run_rows):
        phases.append(
            _make_phase(start_s + ramp_down_start / sample_fps, end_s, run_rows[ramp_down_start:], sample_fps, "ramp_down")
        )
    return phases


def segment_phases(rows, duration_s, sample_fps):
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
            }
        ]
    if "speed" not in rows[0]:
        speed_and_shake(rows, sample_fps)
    phases = []
    for key, start, end in _merge_short_runs(_runs(_run_keys(rows, sample_fps)), sample_fps):
        run_rows = rows[start:end]
        start_s = start / sample_fps
        end_s = end / sample_fps
        if key == "still":
            phases.append(_make_phase(start_s, end_s, run_rows, sample_fps, "still"))
        else:
            phases.extend(_split_moving_run(run_rows, start_s, end_s, sample_fps))
    phases[0]["start_s"] = 0.0
    for index in range(1, len(phases)):
        phases[index]["start_s"] = phases[index - 1]["end_s"]
    phases[-1]["end_s"] = max(phases[-1]["start_s"], duration_s)
    return phases


def _window_rows(rows, start_s, end_s):
    return [row for row in rows if start_s <= row["t"] < end_s]


def build_moves(phases, rows, sample_fps):
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
        confidence = 0.5
        if steady_length >= 3:
            confidence += 0.2
        if mean_inlier >= 0.7:
            confidence += 0.1
        if mean_shake < 0.03:
            confidence += 0.1
        if semantic:
            confidence -= 0.2
        confidence = min(1.0, max(0.0, confidence))
        invalid = sum(
            1 for row in rows if start_s <= row["t"] < end_s and row.get("inlier_ratio", 1.0) == 0
        )
        moves.append(
            {
                "id": f"m{len(moves) + 1}",
                "start_s": start_s,
                "end_s": end_s,
                "label": label,
                "speed": speed_word(mean_speed),
                "steadiness": steadiness_word(mean_shake, label),
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
    phases = segment_phases(rows, duration_s, effective_fps)
    moves = build_moves(phases, rows, effective_fps)
    warnings = []
    invalid = sum(1 for row in rows if row["inlier_ratio"] == 0)
    if rows and invalid == len(rows):
        warnings.append("no valid trajectory rows")
    elif invalid:
        warnings.append(f"invalid trajectory rows: {invalid}")
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
        "trajectory": rows,
        "phases": phases,
        "moves": moves,
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
