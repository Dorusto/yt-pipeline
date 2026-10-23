#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Follow the subject of a clip with a moving crop window and write a crop path.

Usage:
    python scripts/reframe.py --video CLIP.mp4
    python scripts/reframe.py --video CLIP.mp4 --out path.json --aspect 9:16 --json
    python scripts/reframe.py --video CLIP.mp4 --start 10 --end 40 --sample-fps 4

Samples the clip, detects faces with OpenCV YuNet per sample, tracks the subject
between detections with TrackerMIL, falls back to a saliency map when no face is
in frame, smooths the crop centre (no jumps, limited speed) and writes a crop
path JSON validated against ``schemas/reframe.schema.json``: one point per
sample with the crop window's top-left corner in source pixels for the requested
aspect (9:16 by default). The YuNet model downloads once to
``~/.cache/yt-pipeline/models/`` and never into the repository. Sources are
read-only; the output is written atomically. Makes no LLM calls.
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

from common.faces import (
    DEFAULT_MODEL_PATH,
    FacesError,
    detect_faces,
    ensure_model,
    make_detector,
)
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.schema import SchemaError, validate_or_raise
from common.summary import summary_fields

REPO_ROOT = Path(__file__).resolve().parents[1]
SUBJECT_GATE = 0.5
SALIENCY_SIZE = 64
SALIENCY_PEAK = 0.6
WAKE_BAND = 0.25
WAKE_WEIGHT = 0.3
HOLD_DECAY = 0.85
JUMP_FRACTION = 0.35
SOURCES = ("face", "track", "saliency", "hold")


class ReframeError(RuntimeError):
    """Raised when a crop path cannot be produced."""


class ReframeRuntimeError(ReframeError):
    """Raised when a frame cannot be decoded after the video opened."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="reframe.py",
        description=(
            "Follow the subject of a clip with a moving crop window: YuNet face detection "
            "per sample, TrackerMIL between detections, a saliency map when there is no "
            "face, smoothed centre path (no jumps, limited speed). Writes a crop path JSON "
            "(time -> x, y) for any aspect, 9:16 by default."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/reframe.py --video clip.mp4\n"
            "  python scripts/reframe.py --video clip.mp4 --out /tmp/crop.json --json\n"
            "  python scripts/reframe.py --video clip.mp4 --start 10 --end 40 --aspect 9:16\n"
            "  python scripts/reframe.py --video clip.mp4 --sample-fps 4 --smooth 0.5 --max-speed 0.35\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--video", required=True, help="source clip (read-only)")
    parser.add_argument("--out", default=None, help="crop path JSON (default <video_dir>/auto/<stem>_reframe.json)")
    parser.add_argument("--start", type=float, default=None, help="start of the range in seconds (default 0)")
    parser.add_argument("--end", type=float, default=None, help="end of the range in seconds (default clip end)")
    parser.add_argument("--aspect", default="9:16", help="target aspect as W:H (default 9:16)")
    parser.add_argument("--sample-fps", type=float, default=4.0, help="samples per second (default 4)")
    parser.add_argument("--model", default=None, help="YuNet ONNX path (default ~/.cache/yt-pipeline/models/face_detection_yunet_2026may.onnx)")
    parser.add_argument("--smooth", type=float, default=0.5, help="EMA time constant of the centre path in seconds (default 0.5)")
    parser.add_argument("--max-speed", type=float, default=0.35, help="crop window speed limit in frame widths per second (default 0.35)")
    parser.add_argument("--force", action="store_true", help="recompute when the output already exists")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def parse_aspect(text):
    """Parse a ``W:H`` aspect string into a positive (width, height) ratio."""
    try:
        left, right = text.split(":")
        width = float(left)
        height = float(right)
    except (AttributeError, ValueError) as exc:
        raise ReframeError(f"bad aspect '{text}', expected W:H") from exc
    if width <= 0 or height <= 0:
        raise ReframeError(f"bad aspect '{text}', expected W:H")
    return width, height


def crop_size(frame_w, frame_h, aspect_w, aspect_h):
    """Largest even crop of the given aspect that fits inside the frame."""
    if frame_w <= 0 or frame_h <= 0:
        raise ReframeError("frame size must be positive")
    if frame_w * aspect_h >= frame_h * aspect_w:
        crop_h = frame_h
        crop_w = int(round(frame_h * aspect_w / aspect_h))
    else:
        crop_w = frame_w
        crop_h = int(round(frame_w * aspect_h / aspect_w))
    crop_w = max(2, min(frame_w, crop_w - crop_w % 2))
    crop_h = max(2, min(frame_h, crop_h - crop_h % 2))
    return crop_w, crop_h


def sample_times(start_s, end_s, sample_fps):
    """Sample timestamps from start to end at sample_fps, both ends included."""
    if end_s <= start_s:
        raise ReframeError(f"empty range {start_s}..{end_s}")
    if sample_fps <= 0:
        raise ReframeError("--sample-fps must be positive")
    step = 1.0 / sample_fps
    times = []
    t_s = start_s
    while t_s < end_s:
        times.append(round(t_s, 3))
        t_s += step
    times.append(round(end_s, 3))
    return times


def pick_subject(boxes, current_centre, width):
    """Pick the detection nearest the current subject centre, else the largest one."""
    if not boxes:
        return None
    if current_centre is not None:
        gate = SUBJECT_GATE * width
        best = None
        best_distance = None
        for box in boxes:
            cx = box[0] + box[2] / 2.0
            cy = box[1] + box[3] / 2.0
            distance = math.hypot(cx - current_centre[0], cy - current_centre[1])
            if distance <= gate and (best_distance is None or distance < best_distance):
                best = box
                best_distance = distance
        if best is not None:
            return best
    return max(boxes, key=lambda box: box[2] * box[3])


def spectral_saliency(gray):
    """Spectral residual saliency map of a grayscale frame, min-max normalised."""
    small = cv2.resize(gray, (SALIENCY_SIZE, SALIENCY_SIZE), interpolation=cv2.INTER_AREA).astype(np.float32)
    spectrum = np.fft.fft2(small)
    log_amplitude = np.log1p(np.abs(spectrum))
    residual = log_amplitude - cv2.blur(log_amplitude, (3, 3))
    saliency = np.abs(np.fft.ifft2(np.exp(residual + 1j * np.angle(spectrum)))) ** 2
    saliency = cv2.GaussianBlur(saliency, (9, 9), 2.5)
    return _normalize(saliency)


def motion_saliency(previous_gray, gray):
    """Frame-difference saliency map, min-max normalised."""
    if previous_gray is None:
        return np.zeros((SALIENCY_SIZE, SALIENCY_SIZE), dtype=np.float32)
    diff = cv2.absdiff(gray, previous_gray)
    diff = cv2.GaussianBlur(diff, (9, 9), 3)
    small = cv2.resize(diff, (SALIENCY_SIZE, SALIENCY_SIZE), interpolation=cv2.INTER_AREA).astype(np.float32)
    return _normalize(small)


def _normalize(map_):
    low = float(map_.min())
    high = float(map_.max())
    if high - low <= 1e-6:
        return np.zeros_like(map_)
    return (map_ - low) / (high - low)


def saliency_centre(previous_gray, gray, current_centre=None, width=None, height=None):
    """Centre of the salient region near the current centre, or None when flat or unstable."""
    combined = 0.35 * spectral_saliency(gray) + 0.65 * motion_saliency(previous_gray, gray)
    band = int(SALIENCY_SIZE * (1.0 - WAKE_BAND))
    combined[band:, :] *= WAKE_WEIGHT
    window = combined
    x0 = 0
    y0 = 0
    if current_centre is not None and width and height:
        radius = max(2, int(round(SUBJECT_GATE * SALIENCY_SIZE)))
        x0 = max(0, int(round(current_centre[0] / width * SALIENCY_SIZE)) - radius)
        y0 = max(0, int(round(current_centre[1] / height * SALIENCY_SIZE)) - radius)
        x1 = min(SALIENCY_SIZE, x0 + 2 * radius + 1)
        y1 = min(SALIENCY_SIZE, y0 + 2 * radius + 1)
        window = combined[y0:y1, x0:x1]
        if window.size == 0:
            return None, 0.0
    peak = float(window.max())
    mean = float(window.mean())
    if peak <= 0 or peak < 2.0 * mean:
        return None, 0.0
    mask = (window >= SALIENCY_PEAK * peak).astype(np.float32)
    if mask.sum() == 0:
        return None, 0.0
    ys, xs = np.nonzero(mask)
    cx = (float(xs.mean()) + x0) / SALIENCY_SIZE
    cy = (float(ys.mean()) + y0) / SALIENCY_SIZE
    confidence = min(1.0, max(0.0, (peak - mean) / max(peak, 1e-6)))
    return (cx, cy), confidence


def smooth_path(targets, times, width, smooth_s, max_speed):
    """EMA-smooth the target centres and clamp the speed; targets may hold None."""
    centres = []
    current = None
    for index, target in enumerate(targets):
        t_s = times[index]
        if index > 0:
            dt = max(0.0, t_s - times[index - 1])
        else:
            dt = 0.0
        if target is not None:
            if current is None:
                current = target
            else:
                alpha = 1.0 - math.exp(-dt / smooth_s) if smooth_s > 0 else 1.0
                current = (
                    current[0] + alpha * (target[0] - current[0]),
                    current[1] + alpha * (target[1] - current[1]),
                )
        if current is None:
            centres.append(None)
            continue
        if centres and centres[-1] is not None and dt > 0:
            limit = max_speed * width * dt
            dx = current[0] - centres[-1][0]
            dy = current[1] - centres[-1][1]
            distance = math.hypot(dx, dy)
            if distance > limit and distance > 0:
                current = (
                    centres[-1][0] + dx / distance * limit,
                    centres[-1][1] + dy / distance * limit,
                )
        centres.append(current)
    return centres


def crop_points(times, centres, sources, confidences, crop_w, crop_h, width, height):
    """Turn centre positions into clamped crop window top-left points."""
    points = []
    for index, t_s in enumerate(times):
        centre = centres[index]
        if centre is None:
            x = (width - crop_w) // 2
            y = (height - crop_h) // 2
        else:
            x = int(round(centre[0] - crop_w / 2.0))
            y = int(round(centre[1] - crop_h / 2.0))
        x = max(0, min(width - crop_w, x))
        y = max(0, min(height - crop_h, y))
        points.append(
            {
                "t_s": t_s,
                "x": x,
                "y": y,
                "source": sources[index],
                "confidence": round(min(1.0, max(0.0, confidences[index])), 3),
            }
        )
    return points


def clamp_box(box, width, height):
    """Clamp a detection box to the frame so the tracker never sees out-of-frame pixels."""
    x, y, w, h = box[:4]
    x = max(0.0, min(float(width - 2), float(x)))
    y = max(0.0, min(float(height - 2), float(y)))
    w = max(2.0, min(float(width) - x, float(w)))
    h = max(2.0, min(float(height) - y, float(h)))
    return (x, y, w, h) + tuple(box[4:])


def fallback_target(previous_gray, gray, current_centre, width, height):
    """Saliency centre with the stability gate, or a held centre; returns (centre, source, confidence)."""
    centre, confidence = saliency_centre(previous_gray, gray, current_centre, width, height)
    if centre is None:
        return current_centre, "hold", HOLD_DECAY
    target = (centre[0] * width, centre[1] * height)
    if current_centre is not None:
        jump = math.hypot(target[0] - current_centre[0], target[1] - current_centre[1])
        if jump > JUMP_FRACTION * width:
            return current_centre, "hold", HOLD_DECAY
    return target, "saliency", confidence


def steady_targets(targets, width, height):
    """One fixed centre for a range with no face: the median of the salient centres, else the middle.

    Without a face the saliency centre wanders with the waves and the wake; on a
    moving camera that reads as shake, so the crop stays still instead.
    """
    known = [target for target in targets if target is not None]
    if known:
        xs = sorted(target[0] for target in known)
        ys = sorted(target[1] for target in known)
        centre = (xs[len(xs) // 2], ys[len(ys) // 2])
    else:
        centre = (width / 2.0, height / 2.0)
    return [centre for _ in targets]


def read_frame_near(capture, t_s, fps):
    """Read the frame at t_s, stepping one frame back when the seek lands past the end."""
    step = 1.0 / fps if fps and fps > 0 else 0.04
    for attempt in range(4):
        capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t_s - attempt * step) * 1000.0)
        ok, frame = capture.read()
        if ok:
            return frame
    return None


def analyse(video_path, start_s, end_s, aspect, sample_fps, model_path, smooth_s, max_speed):
    """Run detection, tracking and smoothing over the range; return the crop path record."""
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ReframeError(f"cannot read video: {video_path}")
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0)
        duration = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0) / fps if fps > 0 else 0.0
    finally:
        capture.release()
    if width <= 0 or height <= 0:
        raise ReframeError(f"cannot read video: {video_path}")
    if start_s is None:
        start_s = 0.0
    if end_s is None:
        end_s = duration if duration > 0 else start_s + 1.0
    if start_s < 0 or end_s <= start_s:
        raise ReframeError(f"bad range {start_s}..{end_s}")
    aspect_w, aspect_h = parse_aspect(aspect)
    crop_w, crop_h = crop_size(width, height, aspect_w, aspect_h)
    times = sample_times(start_s, end_s, sample_fps)
    detector = make_detector(model_path)
    tracker = None
    previous_gray = None
    current_centre = None
    targets = []
    sources = []
    confidences = []
    warnings = []
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ReframeError(f"cannot read video: {video_path}")
    try:
        for t_s in times:
            frame = read_frame_near(capture, t_s, fps)
            if frame is None:
                raise ReframeRuntimeError(f"cannot read frame at {t_s:.2f}s from {video_path}")
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            boxes = detect_faces(detector, frame)
            subject = pick_subject(boxes, current_centre, width)
            if subject is not None:
                subject = clamp_box(subject, width, height)
                current_centre = (subject[0] + subject[2] / 2.0, subject[1] + subject[3] / 2.0)
                targets.append(current_centre)
                sources.append("face")
                confidences.append(subject[4])
                tracker = cv2.TrackerMIL_create()
                tracker.init(
                    frame,
                    (
                        int(round(subject[0])),
                        int(round(subject[1])),
                        int(round(subject[2])),
                        int(round(subject[3])),
                    ),
                )
            elif tracker is not None:
                updated, box = tracker.update(frame)
                score = float(tracker.getTrackingScore()) if updated else 0.0
                if updated and score > 0.0:
                    box = clamp_box(tuple(float(value) for value in box), width, height)
                    current_centre = (box[0] + box[2] / 2.0, box[1] + box[3] / 2.0)
                    targets.append(current_centre)
                    sources.append("track")
                    confidences.append(min(1.0, max(0.0, score)))
                else:
                    tracker = None
                    current_centre, source, confidence = fallback_target(
                        previous_gray, gray, current_centre, width, height
                    )
                    targets.append(current_centre)
                    sources.append(source)
                    confidences.append(confidence)
            else:
                current_centre, source, confidence = fallback_target(
                    previous_gray, gray, current_centre, width, height
                )
                targets.append(current_centre)
                sources.append(source)
                confidences.append(confidence)
            previous_gray = gray
    finally:
        capture.release()
    held = sum(1 for source in sources if source == "hold")
    if held:
        warnings.append(f"{held} sample(s) held the last crop centre")
    if not any(source == "face" for source in sources):
        warnings.append("no face detected in range")
        targets = steady_targets(targets, width, height)
        warnings.append("no face: static crop on the median salient centre (no wandering)")
    centres = smooth_path(targets, times, width, smooth_s, max_speed)
    points = crop_points(times, centres, sources, confidences, crop_w, crop_h, width, height)
    return {
        "file": video_path.name,
        "path": str(video_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start_s": round(start_s, 3),
        "end_s": round(end_s, 3),
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "aspect": f"{int(aspect_w)}:{int(aspect_h)}" if aspect_w == int(aspect_w) and aspect_h == int(aspect_h) else f"{aspect_w}:{aspect_h}",
        "crop_w": crop_w,
        "crop_h": crop_h,
        "detector": "yunet",
        "tracker": "mil",
        "fallback": "saliency",
        "points": points,
        "warnings": warnings,
    }


def output_is_valid(path, video_path):
    """True when the output exists, validates and is newer than the video."""
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        validate_or_raise(data, "reframe")
    except (OSError, json.JSONDecodeError, SchemaError):
        return False
    return path.stat().st_mtime >= video_path.stat().st_mtime


def write_json(path, data):
    """Write the crop path JSON atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run(args):
    """Produce the crop path for one clip; return (summary, exit code)."""
    started = time.monotonic()
    if args.sample_fps <= 0:
        raise ReframeError("--sample-fps must be positive")
    if args.smooth < 0:
        raise ReframeError("--smooth must be >= 0")
    if args.max_speed <= 0:
        raise ReframeError("--max-speed must be positive")
    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        raise ReframeError(f"file not found: {video_path}")
    out_path = (
        Path(args.out).expanduser().resolve()
        if args.out
        else video_path.parent / "auto" / f"{video_path.stem}_reframe.json"
    )
    if not args.force and output_is_valid(out_path, video_path):
        data = json.loads(out_path.read_text(encoding="utf-8"))
        return (
            {
                "ok": True,
                "skipped": True,
                "out": str(out_path),
                "points": len(data.get("points") or []),
                "warnings": list(data.get("warnings") or []),
                **summary_fields(started),
            },
            EXIT_OK,
        )
    model_path = ensure_model(Path(args.model).expanduser() if args.model else DEFAULT_MODEL_PATH)
    record = analyse(
        video_path,
        args.start,
        args.end,
        args.aspect,
        args.sample_fps,
        model_path,
        args.smooth,
        args.max_speed,
    )
    record["wall_time_s"] = round(time.monotonic() - started, 2)
    record["cost_usd"] = 0.0
    validate_or_raise(record, "reframe")
    write_json(out_path, record)
    return (
        {
            "ok": True,
            "skipped": False,
            "out": str(out_path),
            "points": len(record["points"]),
            "warnings": record["warnings"],
            **summary_fields(started),
        },
        EXIT_OK,
    )


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary, exit_code = run(args)
    except ReframeRuntimeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except (FacesError, OSError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except (ReframeError, SchemaError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(summary))
    else:
        state = "skipped (up to date)" if summary["skipped"] else "written"
        print(f"crop path {state}: {summary['out']} ({summary['points']} points)")
        for warning in summary["warnings"]:
            print(f"[warn] {warning}")
        print(f"{summary['wall_time_s']}s, cost ${summary['llm_cost_usd']:.4f}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
