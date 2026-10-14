#!/usr/bin/env python3
"""Describe every kept quality segment with the vision model.

Usage:
    python scripts/describe.py --workspace DIR
    python scripts/describe.py --workspace DIR --json
    python scripts/describe.py --workspace DIR --clips 0067,0081 --backend mock --force
    python scripts/describe.py --workspace DIR --backend mock --photos-only

Reads ``quality/<stem>.json`` from the workspace, samples frames per kept
segment and sends them with the segment facts to the vision model, then writes
``describe/<stem>.json`` validated against ``schemas/describe.schema.json``.
Default (``--frame-mode mosaic``): the frames of a segment are chosen at OpenCV
scene changes and at the motion phase boundaries recorded in
``motion/<stem>.json``, tiled 6-9 per image with their timestamps burned in,
and one mosaic image per group is sent instead of separate frames. The old path
(``--frame-mode legacy``) samples three fixed interior frames (or one every
~5 s on a segment longer than 20 s) and sends each frame as its own image.
A long segment also returns its content beats — a contiguous tiling into
scene-change parts (approach / inside / exit) — and each beat becomes its own
shotlog candidate. Confident accidental/setup/teardown segments are skipped
without a model call; every uncertain segment carries a verdict whose label wins
in the shotlog. When ``ingest.json`` lists still photos, each photo is described
with one frame as pseudo-segment ``p1`` and carries the main subject centre for
the Ken Burns move. Frames live in memory only and are never written to disk.
Sources are read-only; outputs are written atomically.
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
from PIL import Image

from common.config import ConfigError, load_config
from common.llm import BudgetExceeded, LLMError
from common.peak import valid_vision_peak
from common.photos import PHOTO_FLAWS, PhotoError, jpeg_bytes, open_photo, sharpness
from common.schema import SchemaError, validate, validate_or_raise
from common.vision import VisionFrame, VisionRequest, make_vision_model

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = REPO_ROOT / "prompts" / "describe_segment.md"
DEFAULT_PHOTO_PROMPT = REPO_ROOT / "prompts" / "describe_photo.md"
SKIP_ROLES = {"accidental", "setup", "teardown"}
FRAME_COUNT = 3
MOSAIC_MIN_FRAMES = 6
MOSAIC_MAX_FRAMES = 9
MOSAIC_TILE_W = 480
SCENE_SAMPLE_S = 0.5
SCENE_MAX_SAMPLES = 48
SCENE_MERGE_S = 1.0
SCENE_MIN_DIFF = 8.0
EVENT_MIN_SPACING_S = 0.25
FRAME_MODES = ("mosaic", "legacy")
BEATS_MIN_DURATION_S = 20.0
BEATS_SAMPLE_S = 5.0
BEATS_MAX_FRAMES = 24
BEATS_MAX = 16
JPEG_QUALITY = 85
SEMANTIC_MOTIONS = {"orbit", "tracking", "reveal", "travel", "none"}
SUMMARY_MAX_WORDS = 12
TAGS_MAX = 6
TAG_MAX_CHARS = 32
PHOTO_END_S = 0.04
PHOTO_FRAME_T_S = 0.0
PHOTO_CENTER = {"x": 0.5, "y": 0.5}
ROLES = {
    "move",
    "hold",
    "transition_in",
    "transition_out",
    "setup",
    "teardown",
    "accidental",
}
MOTION_LABELS = {
    "static",
    "minor_motion",
    "pan_left",
    "pan_right",
    "tilt_up",
    "tilt_down",
    "roll_cw",
    "roll_ccw",
    "dolly_in",
    "dolly_out",
    "zoom_in",
    "zoom_out",
    "truck_left",
    "truck_right",
    "pedestal_up",
    "pedestal_down",
    "complex_motion",
    "travel_forward",
    "travel_backward",
    "orbit_candidate",
    "orbit",
    "tracking",
    "reveal",
    "pov",
}


class DescribeError(RuntimeError):
    """Raised when a clip cannot be described."""


class RunState:
    """Model calls, spend and the reason a run stopped early."""

    def __init__(self, max_calls=None):
        self.calls = 0
        self.max_calls = max_calls
        self.cost_usd = 0.0
        self.stop_reason = None
        self.frames_sampled = 0
        self.mosaics_sent = 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="describe.py",
        description=(
            "Describe every kept quality segment with the vision model: frames in time "
            "order tiled into one mosaic image per group (or, with --frame-mode legacy, "
            "three separate frames) plus the motion label, one structured answer per "
            "segment and a verdict for every uncertain segment. Still photos listed in "
            "ingest.json are described with one frame as pseudo-segment p1."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/describe.py --workspace ~/Videos/workspace\n"
            "  python scripts/describe.py --workspace ~/Videos/workspace --json\n"
            "  python scripts/describe.py --workspace ~/Videos/workspace \\\n"
            "      --clips 0067,0081 --backend mock --force --json\n"
            "  python scripts/describe.py --workspace ~/Videos/workspace \\\n"
            "      --backend mock --photos-only\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--workspace", required=True, help="workspace holding quality/; describe/ is written here")
    parser.add_argument("--clips", default=None, help="comma-separated 4-digit counters or stem substrings to include")
    parser.add_argument("--model", default=None, help="vision model id override (default models.vision from config)")
    parser.add_argument("--backend", choices=["mock", "openrouter"], default=None, help="vision backend override")
    parser.add_argument("--config", default=None, help="config file path (default repo config.yaml)")
    parser.add_argument("--prompt-file", default=None, help="prompt path (default prompts/describe_segment.md, photos prompts/describe_photo.md)")
    parser.add_argument("--max-calls", type=int, default=None, help="stop after this many model calls")
    parser.add_argument("--budget-usd", type=float, default=None, help="refuse calls past this spend (default llm_budget_usd)")
    parser.add_argument("--spend-ledger", default=None, help="spend ledger path (default WORKSPACE/llm_spend.json)")
    parser.add_argument("--frame-width", type=int, default=640, help="width frames are resized to before sending (default 640; mosaic tiles are always 480 wide)")
    parser.add_argument(
        "--frame-mode",
        choices=list(FRAME_MODES),
        default="mosaic",
        help=(
            "how frames reach the model: mosaic picks frames at OpenCV scene changes and "
            "motion phase boundaries and tiles 6-9 of them per image (default), legacy sends "
            "three fixed interior frames (or one every ~5 s on a long segment) as separate images"
        ),
    )
    parser.add_argument("--photos-only", action="store_true", help="describe only still photos from ingest.json, skip video segments")
    parser.add_argument("--force", action="store_true", help="re-describe clips whose output already exists")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def frame_times(start_s, end_s):
    """Sample timestamps inside the segment interior, in time order (legacy mode).

    Short segments get three frames; segments longer than 20 s get one frame
    every ~5 s (capped at 24) so the model can mark content beats.
    """
    span = end_s - start_s
    if span > BEATS_MIN_DURATION_S:
        count = min(BEATS_MAX_FRAMES, max(FRAME_COUNT, int(math.ceil(span / BEATS_SAMPLE_S))))
    else:
        count = FRAME_COUNT
    return [start_s + (index + 0.5) / count * span for index in range(count)]


def detect_scene_changes(path, start_s, end_s):
    """Measure scene-change times inside the segment, strongest first, with OpenCV."""
    span = end_s - start_s
    if span <= 0:
        return []
    step = max(SCENE_SAMPLE_S, span / SCENE_MAX_SAMPLES)
    times = []
    t_s = start_s
    while t_s < end_s and len(times) < SCENE_MAX_SAMPLES:
        times.append(t_s)
        t_s += step
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise DescribeError(f"cannot read video: {path}")
    samples = []
    try:
        for sample_t in times:
            capture.set(cv2.CAP_PROP_POS_MSEC, sample_t * 1000.0)
            ok, frame = capture.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            height = max(1, int(round(gray.shape[0] * 160 / gray.shape[1])))
            gray = cv2.resize(gray, (160, height), interpolation=cv2.INTER_AREA)
            samples.append((sample_t, gray.astype(np.float32)))
    finally:
        capture.release()
    if len(samples) < 2:
        return []
    diffs = [
        float(np.mean(np.abs(samples[index][1] - samples[index - 1][1])))
        for index in range(1, len(samples))
    ]
    threshold = max(3.0 * float(np.median(diffs)), SCENE_MIN_DIFF)
    peaks = []
    for index, diff in enumerate(diffs):
        if diff <= threshold:
            continue
        left = diffs[index - 1] if index > 0 else -1.0
        right = diffs[index + 1] if index + 1 < len(diffs) else -1.0
        if diff >= left and diff >= right:
            peaks.append((samples[index + 1][0], diff))
    merged = []
    for peak_t, diff in sorted(peaks):
        if merged and peak_t - merged[-1][0] <= SCENE_MERGE_S:
            if diff > merged[-1][1]:
                merged[-1] = (peak_t, diff)
            continue
        merged.append((peak_t, diff))
    return [peak_t for peak_t, _ in sorted(merged, key=lambda peak: -peak[1])]


def motion_phase_times(motion_record, start_s, end_s):
    """Phase boundary times from the motion analysis that fall inside the segment."""
    if not motion_record:
        return []
    times = []
    for phase in motion_record.get("phases") or []:
        for key in ("start_s", "end_s"):
            value = phase.get(key)
            if isinstance(value, (int, float)) and start_s < value < end_s:
                times.append(float(value))
    return sorted(set(round(t_s, 3) for t_s in times))


def event_frame_times(start_s, end_s, event_times):
    """Pick tile timestamps at event times (priority order), anchored at the segment edges."""
    span = end_s - start_s
    if span <= 0:
        return []
    if span <= BEATS_MIN_DURATION_S:
        count = MOSAIC_MIN_FRAMES
    else:
        count = min(BEATS_MAX_FRAMES, max(MOSAIC_MIN_FRAMES, int(math.ceil(span / BEATS_SAMPLE_S))))
    chosen = [start_s + 0.05, end_s - 0.05]
    for event_t in event_times:
        if len(chosen) >= count:
            break
        if start_s < event_t < end_s and all(
            abs(event_t - kept) >= EVENT_MIN_SPACING_S for kept in chosen
        ):
            chosen.append(event_t)
    if len(chosen) < count:
        grid_size = max(count * 4, 32)
        for index in range(grid_size):
            if len(chosen) >= count:
                break
            candidate = start_s + (index + 1) * span / (grid_size + 1)
            if all(abs(candidate - kept) >= EVENT_MIN_SPACING_S for kept in chosen):
                chosen.append(candidate)
    chosen = sorted(chosen)
    if len(chosen) > count:
        middle = chosen[1:-1]
        if len(middle) > count - 2:
            step = len(middle) / (count - 2)
            middle = [middle[int(index * step)] for index in range(count - 2)]
        chosen = [chosen[0]] + middle + [chosen[-1]]
    deduped = []
    for t_s in sorted(chosen):
        if deduped and t_s - deduped[-1] < EVENT_MIN_SPACING_S:
            continue
        deduped.append(round(t_s, 3))
    return deduped


def mosaic_groups(times):
    """Split tile timestamps into consecutive groups of at most MOSAIC_MAX_FRAMES."""
    if not times:
        return []
    group_count = int(math.ceil(len(times) / MOSAIC_MAX_FRAMES))
    base, extra = divmod(len(times), group_count)
    groups = []
    index = 0
    for group_index in range(group_count):
        size = base + (1 if group_index < extra else 0)
        groups.append(times[index : index + size])
        index += size
    return groups


def build_mosaic(pairs):
    """Tile (t_s, frame) pairs into one JPEG mosaic with burned-in timestamps."""
    if not pairs:
        raise DescribeError("a mosaic needs at least one frame")
    first = pairs[0][1]
    tile_h = max(1, int(round(first.shape[0] * MOSAIC_TILE_W / first.shape[1])))
    cols = 3 if len(pairs) > 4 else 2
    rows = int(math.ceil(len(pairs) / cols))
    canvas = np.zeros((rows * tile_h, cols * MOSAIC_TILE_W, 3), dtype=np.uint8)
    for index, (t_s, frame) in enumerate(pairs):
        tile = cv2.resize(frame, (MOSAIC_TILE_W, tile_h), interpolation=cv2.INTER_AREA)
        label = f"{t_s:.1f}s"
        (text_w, text_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        cv2.rectangle(tile, (0, 0), (text_w + 12, text_h + 12), (0, 0, 0), -1)
        cv2.putText(
            tile,
            label,
            (6, text_h + 6),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        row, col = divmod(index, cols)
        canvas[row * tile_h : (row + 1) * tile_h, col * MOSAIC_TILE_W : (col + 1) * MOSAIC_TILE_W] = tile
    ok, buffer = cv2.imencode(".jpg", canvas, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise DescribeError("could not encode a mosaic as JPEG")
    return buffer.tobytes()


def read_raw_frames(path, times):
    """Read one BGR frame per timestamp from a clip; frames stay in memory."""
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise DescribeError(f"cannot read video: {path}")
    frames = []
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        for t_s in times:
            frame = _read_frame_near(capture, t_s, fps)
            if frame is None:
                raise DescribeError(f"cannot read frame at {t_s:.2f}s from {path}")
            frames.append((t_s, frame))
    finally:
        capture.release()
    return frames


def read_frames(path, times, width):
    """Read one JPEG frame per timestamp from a clip; frames stay in memory."""
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise DescribeError(f"cannot read video: {path}")
    frames = []
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        for t_s in times:
            frame = _read_frame_near(capture, t_s, fps)
            if frame is None:
                raise DescribeError(f"cannot read frame at {t_s:.2f}s from {path}")
            frames.append(VisionFrame(t_s=t_s, jpeg_bytes=_encode_jpeg(frame, width)))
    finally:
        capture.release()
    return frames


def _read_frame_near(capture, t_s, fps):
    """Read the frame at t_s, stepping one frame back when the seek lands past the end."""
    step = 1.0 / fps if fps and fps > 0 else 0.04
    for attempt in range(4):
        capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t_s - attempt * step) * 1000.0)
        ok, frame = capture.read()
        if ok:
            return frame
    return None


def build_prompt(base_prompt, clip_file, segment, times, frame_mode="legacy"):
    """Append the segment facts block to the model prompt."""
    uncertain = bool(segment.get("uncertain"))
    reasons = segment.get("uncertain_reasons") or []
    if uncertain:
        uncertain_text = "yes (" + ", ".join(reasons) + ")" if reasons else "yes"
    else:
        uncertain_text = "no"
    lines = [
        f"- clip: {clip_file}",
        f"- time: {segment['start_s']} s to {segment['end_s']} s",
        f"- geometric role: {segment.get('role')} (confidence {segment.get('confidence')})",
        f"- geometric motion label: {segment.get('motion_label')} "
        f"(confidence {segment.get('motion_label_confidence')})",
        f"- uncertain: {uncertain_text}",
        "- frames (in order): " + ", ".join(f"{t} s" for t in times),
    ]
    if frame_mode == "mosaic":
        span = segment["end_s"] - segment["start_s"]
        lines.append("- long: " + ("yes" if span > BEATS_MIN_DURATION_S else "no"))
    return base_prompt + "\n\nSegment facts:\n" + "\n".join(lines)


def read_photo_frame(path, width):
    """Read one JPEG frame from a still photo; the frame stays in memory."""
    image = open_photo(path)
    if width and image.width != width:
        height = max(1, round(image.height * width / image.width))
        image = image.resize((int(width), height), Image.LANCZOS)
    return VisionFrame(t_s=PHOTO_FRAME_T_S, jpeg_bytes=jpeg_bytes(image))


def build_photo_prompt(base_prompt, photo):
    """Append the photo facts block to the model prompt."""
    lines = [
        f"- photo: {photo.get('file')}",
        f"- captured: {photo.get('creation_time') or 'unknown'}",
        f"- size: {photo.get('width')}x{photo.get('height')}",
    ]
    return base_prompt + "\n\nPhoto facts:\n" + "\n".join(lines)


def parse_answer(text):
    """Parse the model text as a JSON object, tolerating markdown fences."""
    if not text:
        return None
    data = _loads(_strip_fences(text))
    if data is None:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            data = _loads(text[start : end + 1])
    return data


def normalize_answer(data):
    """Coerce a parsed answer into the fields the schema accepts."""
    return {
        "description": _as_string(data.get("description")),
        "summary": _normalize_summary(data.get("summary")),
        "tags": _normalize_tags(data.get("tags")),
        "subject": _as_string(data.get("subject")),
        "scenery": _as_string(data.get("scenery")),
        "semantic_motion": data.get("semantic_motion")
        if data.get("semantic_motion") in SEMANTIC_MOTIONS
        else "none",
        "interest": _clamp_interest(data.get("interest")),
        "hero": data.get("hero") if isinstance(data.get("hero"), bool) else False,
        "peak_s": _as_float(data.get("peak_s")),
        "verdict": normalize_verdict(data.get("verdict")),
        "beats": normalize_beats(data.get("beats")),
    }


def normalize_photo_answer(data):
    """Coerce a parsed photo answer into the fields the schema accepts."""
    normalized = normalize_answer(data)
    normalized["subject_position"] = normalize_subject_position(data.get("subject_position"))
    normalized["photo_flaws"] = normalize_photo_flaws(data.get("photo_flaws"))
    return normalized


def normalize_photo_flaws(value):
    """Keep only the known flaw words, once each, in the fixed order."""
    if not isinstance(value, list):
        return []
    found = {str(item).strip().lower().replace(" ", "_").replace("-", "_") for item in value}
    return [flaw for flaw in PHOTO_FLAWS if flaw in found]


def normalize_subject_position(value):
    """Return a schema-valid subject centre, or the image centre when unusable."""
    if isinstance(value, dict):
        x = _as_float(value.get("x"))
        y = _as_float(value.get("y"))
        if x is not None and y is not None:
            return {"x": min(1.0, max(0.0, x)), "y": min(1.0, max(0.0, y))}
    return dict(PHOTO_CENTER)


def normalize_beats(value):
    """Return schema-valid content beats tiling their span, or [] when unusable."""
    if not isinstance(value, list):
        return []
    raw = []
    for item in value:
        if not isinstance(item, dict):
            continue
        start_s = _as_float(item.get("start_s"))
        end_s = _as_float(item.get("end_s"))
        if start_s is None or end_s is None:
            continue
        raw.append((start_s, end_s, item))
    if len(raw) < 2:
        return []
    raw.sort(key=lambda entry: (entry[0], entry[1]))
    span_start = raw[0][0]
    span_end = max(entry[1] for entry in raw)
    beats = []
    cursor = span_start
    for start_s, end_s, item in raw:
        start_s = max(span_start, min(span_end, start_s))
        end_s = max(span_start, min(span_end, end_s))
        if end_s <= start_s:
            continue
        if start_s > cursor:
            if beats:
                beats[-1]["end_s"] = start_s
            else:
                start_s = cursor
        if start_s < cursor:
            start_s = cursor
        if end_s <= start_s:
            continue
        beats.append(
            {
                "id": "",
                "start_s": round(start_s, 3),
                "end_s": round(end_s, 3),
                "label": _as_string(item.get("label")).strip()[:32] or "beat",
                "summary": _normalize_summary(item.get("summary")),
                "interest": _clamp_interest(item.get("interest")),
                "hero": item.get("hero") if isinstance(item.get("hero"), bool) else None,
                "peak_s": valid_vision_peak(item.get("peak_s"), start_s, end_s),
            }
        )
        cursor = end_s
        if len(beats) >= BEATS_MAX:
            break
    if not beats:
        return []
    if cursor < span_end:
        beats[-1]["end_s"] = round(span_end, 3)
    if len(beats) < 2:
        return []
    for index, beat in enumerate(beats):
        beat["id"] = f"b{index + 1}"
    return beats


def normalize_verdict(value):
    """Return a schema-valid verdict object, or None when the value is invalid."""
    if not isinstance(value, dict):
        return None
    role = value.get("role")
    label = value.get("motion_label")
    confidence = value.get("confidence")
    note = value.get("note")
    if role not in ROLES or label not in MOTION_LABELS:
        return None
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    if not isinstance(note, str):
        return None
    return {
        "role": role,
        "motion_label": label,
        "confidence": min(1.0, max(0.0, float(confidence))),
        "note": note,
    }


def segment_frames(clip_path, segment, frame_width, frame_mode, motion_record, state):
    """Sample the segment and build the model images: one mosaic per group or one per frame."""
    start_s = segment["start_s"]
    end_s = segment["end_s"]
    if frame_mode == "legacy":
        times = frame_times(start_s, end_s)
        frames = read_frames(clip_path, times, frame_width)
        state.frames_sampled += len(times)
        state.mosaics_sent += len(frames)
        return times, frames
    events = motion_phase_times(motion_record, start_s, end_s)
    events += detect_scene_changes(clip_path, start_s, end_s)
    times = event_frame_times(start_s, end_s, events)
    pairs = read_raw_frames(clip_path, times)
    frames = []
    offset = 0
    for group in mosaic_groups(times):
        group_pairs = pairs[offset : offset + len(group)]
        offset += len(group)
        frames.append(VisionFrame(t_s=group[0], jpeg_bytes=build_mosaic(group_pairs)))
    state.frames_sampled += len(times)
    state.mosaics_sent += len(frames)
    return times, frames


def describe_segments(
    record,
    clip_path,
    model,
    model_id,
    base_prompt,
    frame_width,
    state,
    frame_mode="legacy",
    motion_record=None,
):
    """Describe or skip every segment of one clip, in time order."""
    warnings = []
    segments = []
    for segment in record["segments"]:
        role = segment.get("role")
        uncertain = bool(segment.get("uncertain"))
        if role in SKIP_ROLES and not uncertain:
            segments.append(_skipped_segment(segment, f"confident {role}"))
            continue
        if state.stop_reason is not None:
            segments.append(_skipped_segment(segment, state.stop_reason))
            continue
        if not _calls_available(state):
            state.stop_reason = "max_calls"
            segments.append(_skipped_segment(segment, "max_calls"))
            continue
        times, frames = segment_frames(
            clip_path, segment, frame_width, frame_mode, motion_record, state
        )
        prompt = build_prompt(base_prompt, record["file"], segment, times, frame_mode)
        request = VisionRequest(
            prompt=prompt,
            frames=frames,
            context=_context(segment),
            model=model_id,
        )
        try:
            response = _call_model(model, request, state)
        except BudgetExceeded:
            state.stop_reason = "budget_exceeded"
            segments.append(_skipped_segment(segment, "budget_exceeded"))
            continue
        segment_cost = response.cost_usd
        answer = parse_answer(response.text)
        if answer is None:
            retry_answer, retry_cost = _retry_parse(model, request, state)
            segment_cost += retry_cost
            answer = retry_answer
        if answer is None:
            warnings.append(f"{record['file']} {segment['id']}: unparseable model output")
            segments.append(_skipped_segment(segment, "unparseable model output", segment_cost))
            continue
        normalized = normalize_answer(answer)
        verdict = normalized["verdict"]
        if uncertain and verdict is None:
            segment_cost += _retry_verdict(model, request, state, normalized, warnings, record, segment)
            verdict = normalized["verdict"]
        if not uncertain:
            verdict = None
        segments.append(_described_segment(segment, times, normalized, verdict, segment_cost))
    return segments, warnings


def describe_clip(
    record,
    clip_path,
    model,
    model_id,
    base_prompt,
    frame_width,
    state,
    frame_mode="legacy",
    motion_record=None,
):
    """Describe one clip and return its full describe record."""
    started = time.monotonic()
    segments, warnings = describe_segments(
        record,
        clip_path,
        model,
        model_id,
        base_prompt,
        frame_width,
        state,
        frame_mode=frame_mode,
        motion_record=motion_record,
    )
    if frame_mode == "mosaic" and not (motion_record or {}).get("phases"):
        warnings.append(f"{record['file']}: no motion phases; frame times use scene changes only")
    return {
        "file": record["file"],
        "path": str(clip_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_s": record["duration_s"],
        "model": model_id,
        "llm_cost_usd": round(sum(segment["cost_usd"] or 0.0 for segment in segments), 8),
        "wall_time_s": round(time.monotonic() - started, 2),
        "segments": segments,
        "warnings": warnings,
    }


def photo_sharpness(path, warnings, name):
    """The photo's Laplacian sharpness rounded to one decimal, or None when unreadable."""
    try:
        return round(sharpness(path), 1)
    except (PhotoError, OSError, ValueError) as exc:
        warnings.append(f"{name} p1: sharpness not measured: {exc}")
        return None


def describe_photo(photo, model, model_id, base_prompt, frame_width, state):
    """Describe one still photo with a single frame and return its describe record."""
    started = time.monotonic()
    warnings = []
    cost_usd = 0.0
    if state.stop_reason is not None:
        segment = _skipped_photo_segment(state.stop_reason, None)
    elif not _calls_available(state):
        state.stop_reason = "max_calls"
        segment = _skipped_photo_segment("max_calls", None)
    else:
        frames = [read_photo_frame(photo["path"], frame_width)]
        prompt = build_photo_prompt(base_prompt, photo)
        request = VisionRequest(
            prompt=prompt,
            frames=frames,
            context=_photo_context(photo),
            model=model_id,
        )
        answer = None
        try:
            response = _call_model(model, request, state)
            cost_usd += response.cost_usd
            answer = parse_answer(response.text)
            if answer is None:
                retry_answer, retry_cost = _retry_parse(model, request, state)
                cost_usd += retry_cost
                answer = retry_answer
        except BudgetExceeded:
            state.stop_reason = "budget_exceeded"
        if answer is None:
            warnings.append(f"{photo['file']} p1: unparseable model output")
            segment = _skipped_photo_segment("unparseable model output", cost_usd)
        else:
            segment = _photo_segment(normalize_photo_answer(answer), cost_usd)
            segment["sharpness"] = photo_sharpness(photo["path"], warnings, photo["file"])
    return {
        "media_type": "photo",
        "file": photo["file"],
        "path": str(photo["path"]),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_s": 0.0,
        "model": model_id,
        "llm_cost_usd": round(cost_usd, 8),
        "wall_time_s": round(time.monotonic() - started, 2),
        "segments": [segment],
        "warnings": warnings,
    }


def run(
    workspace,
    clips=None,
    model=None,
    model_id=None,
    prompt_path=None,
    max_calls=None,
    budget_usd=None,
    spend_ledger=None,
    frame_width=640,
    force=False,
    config=None,
    backend=None,
    photos_only=False,
    frame_mode="mosaic",
):
    """Describe the selected clips and photos into ``workspace/describe``; return summary and exit code."""
    started = time.monotonic()
    if frame_width <= 0:
        raise DescribeError("--frame-width must be positive")
    if frame_mode not in FRAME_MODES:
        raise DescribeError(f"unknown frame mode '{frame_mode}'")
    workspace_path = Path(workspace).expanduser().resolve()
    quality_dir = workspace_path / "quality"
    if config is None:
        config = load_config()
    effective = dict(config)
    if backend is not None:
        effective["llm_backend"] = backend
    if budget_usd is not None:
        effective["llm_budget_usd"] = budget_usd
    if model_id is None:
        model_id = (effective.get("models") or {}).get("vision")
    if not model_id:
        raise DescribeError("no vision model id: set models.vision in the config or pass --model")
    if prompt_path is not None:
        prompt_path = Path(prompt_path)
        if not prompt_path.exists():
            raise DescribeError(f"prompt file not found: {prompt_path}")
        video_prompt = prompt_path.read_text(encoding="utf-8")
        photo_prompt = video_prompt
    else:
        video_prompt = _read_prompt(DEFAULT_PROMPT)
        photo_prompt = _read_prompt(DEFAULT_PHOTO_PROMPT)
    if spend_ledger is None:
        spend_ledger = workspace_path / "llm_spend.json"
    if model is None:
        model = make_vision_model(effective, ledger_path=spend_ledger)
    photos = _load_photos(workspace_path)
    if photos_only:
        selected = []
    elif quality_dir.is_dir():
        selected = _select_quality_files(quality_dir, clips)
    elif photos:
        selected = []
    else:
        raise DescribeError(f"no quality folder in workspace: {quality_dir}")
    output_dir = workspace_path / "describe"
    output_dir.mkdir(parents=True, exist_ok=True)
    state = RunState(max_calls=max_calls)
    described = 0
    skipped = 0
    segments_described = 0
    segments_skipped = 0
    beats_found = 0
    for quality_file in selected:
        output_path = output_dir / quality_file.name
        if not force and _output_is_valid(output_path):
            skipped += 1
            continue
        record = _load_quality(quality_file)
        clip_path = _clip_path(record, quality_file)
        motion_record = _load_motion(workspace_path, quality_file)
        clip_record = describe_clip(
            record,
            clip_path,
            model,
            model_id,
            video_prompt,
            frame_width,
            state,
            frame_mode=frame_mode,
            motion_record=motion_record,
        )
        validate_or_raise(clip_record, "describe")
        _atomic_write_json(output_path, clip_record)
        described += 1
        segments_described += sum(
            1 for segment in clip_record["segments"] if segment["status"] == "described"
        )
        segments_skipped += sum(
            1 for segment in clip_record["segments"] if segment["status"] == "skipped"
        )
        beats_found += sum(len(segment.get("beats") or []) for segment in clip_record["segments"])
        if state.stop_reason is not None:
            break
    photos_described = 0
    for photo in photos:
        output_path = output_dir / f"{Path(photo['file']).stem}.json"
        if not force and _output_is_valid(output_path) and _photo_has_flaws(output_path):
            continue
        if state.stop_reason is not None:
            break
        photo_record = describe_photo(photo, model, model_id, photo_prompt, frame_width, state)
        validate_or_raise(photo_record, "describe")
        _atomic_write_json(output_path, photo_record)
        photos_described += 1
        segments_described += sum(
            1 for segment in photo_record["segments"] if segment["status"] == "described"
        )
        segments_skipped += sum(
            1 for segment in photo_record["segments"] if segment["status"] == "skipped"
        )
        if state.stop_reason is not None:
            break
    summary = {
        "clips": len(selected),
        "described": described,
        "skipped": skipped,
        "segments_described": segments_described,
        "segments_skipped": segments_skipped,
        "beats": beats_found,
        "photos": photos_described,
        "model": model_id,
        "frame_mode": frame_mode,
        "frames_sampled": state.frames_sampled,
        "mosaics_sent": state.mosaics_sent,
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": round(state.cost_usd, 8),
    }
    return summary, (2 if state.stop_reason is not None else 0)


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary, exit_code = run(
            args.workspace,
            clips=args.clips,
            model_id=args.model,
            prompt_path=args.prompt_file,
            max_calls=args.max_calls,
            budget_usd=args.budget_usd,
            spend_ledger=args.spend_ledger,
            frame_width=args.frame_width,
            force=args.force,
            config=load_config(args.config),
            backend=args.backend,
            photos_only=args.photos_only,
            frame_mode=args.frame_mode,
        )
    except (DescribeError, ConfigError, SchemaError, LLMError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary))
    else:
        print(
            f"described {summary['described']} clips, skipped {summary['skipped']} "
            f"(of {summary['clips']})"
        )
        print(
            f"segments: {summary['segments_described']} described, "
            f"{summary['segments_skipped']} skipped, {summary['beats']} beats"
        )
        print(f"photos: {summary['photos']} described")
        print(
            f"frames: {summary['frames_sampled']} sampled, "
            f"{summary['mosaics_sent']} images sent ({summary['frame_mode']})"
        )
        print(
            f"model {summary['model']}, cost ${summary['llm_cost_usd']:.4f}, "
            f"{summary['wall_time_s']}s"
        )
    return exit_code


def _retry_parse(model, request, state):
    """Retry once for an unparseable answer; return the parsed answer or None plus the cost."""
    if not _calls_available(state):
        state.stop_reason = "max_calls"
        return None, 0.0
    try:
        retry = _call_model(model, request, state)
    except BudgetExceeded:
        state.stop_reason = "budget_exceeded"
        return None, 0.0
    return parse_answer(retry.text), retry.cost_usd


def _retry_verdict(model, request, state, normalized, warnings, record, segment):
    """Retry once for a missing verdict; keep the description and fall back if it fails."""
    cost = 0.0
    if not _calls_available(state):
        state.stop_reason = "max_calls"
    else:
        try:
            retry = _call_model(model, request, state)
            cost = retry.cost_usd
            retry_answer = parse_answer(retry.text)
            if retry_answer is not None:
                retry_normalized = normalize_answer(retry_answer)
                if retry_normalized["verdict"] is not None:
                    normalized.update(retry_normalized)
                    return cost
        except BudgetExceeded:
            state.stop_reason = "budget_exceeded"
    normalized["verdict"] = _fallback_verdict(segment)
    warnings.append(f"{record['file']} {segment['id']}: model gave no verdict; geometry kept")
    return cost


def _fallback_verdict(segment):
    label = segment.get("motion_label")
    if label not in MOTION_LABELS:
        label = "static"
    return {
        "role": segment.get("role"),
        "motion_label": label,
        "confidence": 0.0,
        "note": "model gave no verdict; geometry kept",
    }


def _call_model(model, request, state):
    response = model.describe(request)
    state.calls += 1
    state.cost_usd += response.cost_usd
    return response


def _calls_available(state):
    return state.max_calls is None or state.calls < state.max_calls


def _context(segment):
    return {
        "id": segment.get("id"),
        "start_s": segment.get("start_s"),
        "end_s": segment.get("end_s"),
        "role": segment.get("role"),
        "motion_label": segment.get("motion_label"),
        "motion_label_confidence": segment.get("motion_label_confidence"),
        "uncertain": bool(segment.get("uncertain")),
        "uncertain_reasons": list(segment.get("uncertain_reasons") or []),
    }


def _described_segment(segment, times, normalized, verdict, cost_usd):
    beats = beats_for_segment(segment, normalized["beats"])
    return {
        "id": segment["id"],
        "start_s": segment["start_s"],
        "end_s": segment["end_s"],
        "status": "described",
        "role": segment["role"],
        "motion_label": segment.get("motion_label"),
        "uncertain": bool(segment.get("uncertain")),
        "uncertain_reasons": list(segment.get("uncertain_reasons") or []),
        "frames": [{"t_s": t_s} for t_s in times],
        "description": normalized["description"],
        "summary": normalized["summary"],
        "tags": normalized["tags"],
        "subject": normalized["subject"],
        "scenery": normalized["scenery"],
        "semantic_motion": normalized["semantic_motion"],
        "interest": normalized["interest"],
        "hero": normalized["hero"],
        "peak_s": valid_vision_peak(normalized.get("peak_s"), segment["start_s"], segment["end_s"]),
        "beats": beats,
        "verdict": verdict,
        "skip_reason": None,
        "cost_usd": cost_usd,
    }


def beats_for_segment(segment, beats):
    """Content beats of a segment, or [] when the segment is short or unsplit."""
    if segment["end_s"] - segment["start_s"] <= BEATS_MIN_DURATION_S:
        return []
    if len(beats) < 2:
        return []
    return beats


def _skipped_segment(segment, reason, cost_usd=None):
    return {
        "id": segment["id"],
        "start_s": segment["start_s"],
        "end_s": segment["end_s"],
        "status": "skipped",
        "role": segment["role"],
        "motion_label": segment.get("motion_label"),
        "uncertain": bool(segment.get("uncertain")),
        "uncertain_reasons": list(segment.get("uncertain_reasons") or []),
        "skip_reason": reason,
        "cost_usd": cost_usd,
    }


def _photo_context(photo):
    return {
        "id": "p1",
        "media_type": "photo",
        "file": photo.get("file"),
        "creation_time": photo.get("creation_time"),
        "role": "hold",
        "motion_label": "static",
        "uncertain": False,
        "uncertain_reasons": [],
    }


def _photo_segment(normalized, cost_usd):
    return {
        "id": "p1",
        "start_s": 0.0,
        "end_s": PHOTO_END_S,
        "status": "described",
        "role": "hold",
        "motion_label": "static",
        "uncertain": False,
        "uncertain_reasons": [],
        "frames": [{"t_s": PHOTO_FRAME_T_S}],
        "description": normalized["description"],
        "summary": normalized["summary"],
        "tags": normalized["tags"],
        "subject": normalized["subject"],
        "scenery": normalized["scenery"],
        "semantic_motion": "none",
        "interest": normalized["interest"],
        "hero": normalized["hero"],
        "verdict": None,
        "skip_reason": None,
        "cost_usd": cost_usd,
        "subject_position": normalized["subject_position"],
        "photo_flaws": normalized["photo_flaws"],
    }


def _skipped_photo_segment(reason, cost_usd):
    return {
        "id": "p1",
        "start_s": 0.0,
        "end_s": PHOTO_END_S,
        "status": "skipped",
        "role": "hold",
        "motion_label": "static",
        "uncertain": False,
        "uncertain_reasons": [],
        "skip_reason": reason,
        "cost_usd": cost_usd,
    }


def _encode_jpeg(frame, width):
    height, frame_width = frame.shape[:2]
    if frame_width != width:
        scaled_height = max(1, int(round(height * width / frame_width)))
        frame = cv2.resize(frame, (width, scaled_height), interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise DescribeError("could not encode a frame as JPEG")
    return buffer.tobytes()


def _strip_fences(text):
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _loads(text):
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _as_string(value):
    return value if isinstance(value, str) else ""


def _as_float(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _clamp_interest(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return min(100, max(0, value))


def _normalize_summary(value):
    text = _as_string(value).strip()
    if not text:
        return None
    words = text.split()
    return " ".join(words[:SUMMARY_MAX_WORDS])


def _normalize_tags(value):
    if not isinstance(value, list):
        return []
    tags = []
    for item in value:
        tag = _as_string(item).strip().lower()[:TAG_MAX_CHARS]
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= TAGS_MAX:
            break
    return tags


def _select_quality_files(quality_dir, clips):
    tokens = _clip_tokens(clips)
    files = sorted(quality_dir.glob("*.json"))
    selected = [path for path in files if _matches(path.stem, tokens)]
    if not selected:
        raise DescribeError(f"no quality files matched in {quality_dir}")
    return selected


def _clip_tokens(clips):
    if not clips:
        return None
    return [token.strip() for token in clips.split(",") if token.strip()]


def _matches(stem, tokens):
    if not tokens:
        return True
    return any(token in stem for token in tokens)


def _load_quality(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DescribeError(f"unreadable quality file {path}: {exc}") from exc
    if not isinstance(data, dict) or not data.get("segments"):
        raise DescribeError(f"quality file {path} has no segments")
    return data


def _load_motion(workspace_path, quality_file):
    path = workspace_path / "motion" / quality_file.name
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_prompt(path):
    path = Path(path)
    if not path.exists():
        raise DescribeError(f"prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def _photo_has_flaws(path):
    """False for a photo described before ``photo_flaws`` existed, so it is described again."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    segments = [segment for segment in data.get("segments") or [] if segment.get("status") == "described"]
    return all("photo_flaws" in segment for segment in segments)


def _load_photos(workspace_path):
    ingest_path = workspace_path / "ingest.json"
    if not ingest_path.exists():
        return []
    try:
        data = json.loads(ingest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    photos = data.get("photos") if isinstance(data, dict) else None
    if not isinstance(photos, list):
        return []
    proxy_dir = workspace_path / "proxy"
    usable = []
    for photo in photos:
        if not isinstance(photo, dict) or not photo.get("path"):
            continue
        if not Path(photo["path"]).is_file() and (proxy_dir / str(photo.get("file") or "")).is_file():
            photo = {**photo, "path": str(proxy_dir / photo["file"])}
        usable.append(photo)
    return usable


def _clip_path(record, quality_file):
    raw = record.get("path")
    if not raw:
        raise DescribeError(f"quality file {quality_file.name} has no clip path")
    path = Path(raw)
    if not path.exists():
        raise DescribeError(f"clip not found: {path}")
    return path


def _output_is_valid(path):
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return validate(data, "describe") == []


def _atomic_write_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    sys.exit(main())
