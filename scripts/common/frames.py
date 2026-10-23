"""Read sampled frames from a clip at given times (OpenCV).

Shared by ``describe.py``, ``similarity.py``, ``mode_b.py``, ``explain.py`` and
``index.py``: one ``VideoCapture`` seek loop plus the one JPEG thumbnail
encoder. Frames stay in memory as BGR numpy arrays; the callers compare or
resize them and use ``thumbnail_data_uri`` for the HTML contact sheets.
"""

import base64
import subprocess
from pathlib import Path

import cv2

from common.ffmpeg_tools import ffmpeg_path

THUMBNAIL_WIDTH = 320
THUMBNAIL_QUALITY = 70


class FramesError(Exception):
    """A clip cannot be opened or a requested frame cannot be read."""


def extract_frame(path, t_s, out, width):
    """Write the frame at t_s from path as a JPEG scaled to width px wide (ffmpeg)."""
    command = [
        ffmpeg_path(), "-y", "-ss", f"{t_s:.3f}", "-i", str(path),
        "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(out),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise FramesError(f"could not run ffmpeg: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise FramesError(
            f"ffmpeg failed for {Path(path).name}: {detail[-1] if detail else 'unknown error'}"
        )


def read_frame_pairs(path, times, error=FramesError, near_seek=False, skip_missing=False):
    """Read one BGR frame per time from ``path``; return ``(t_s, frame)`` pairs.

    ``near_seek`` steps one frame back per attempt when the seek lands past the
    end of the clip. ``skip_missing`` returns the frames found (and ``[]`` for an
    unopenable clip) instead of raising ``error``.
    """
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        if skip_missing:
            return []
        raise error(f"cannot read video: {path}")
    frames = []
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        for t_s in times:
            frame = _read_frame_near(capture, t_s, fps) if near_seek else _read_frame(capture, t_s)
            if frame is None:
                if skip_missing:
                    continue
                raise error(f"cannot read frame at {t_s:.2f}s from {path}")
            frames.append((float(t_s), frame))
    finally:
        capture.release()
    return frames


def read_frame(path, t_s):
    """Return the BGR frame at ``t_s`` from ``path``, or None when it cannot be read."""
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        return None
    try:
        return _read_frame(capture, t_s)
    finally:
        capture.release()


def thumbnail_data_uri(path, t_s):
    """Return the frame at ``t_s`` as a JPEG base64 data URI, or None."""
    if not path:
        return None
    frame = read_frame(path, t_s)
    if frame is None:
        return None
    return encode_thumbnail(frame)


def encode_thumbnail(frame):
    """Encode a BGR frame as a width-capped JPEG base64 data URI, or None."""
    height, width = frame.shape[:2]
    if width > THUMBNAIL_WIDTH:
        scaled_height = max(1, int(round(height * THUMBNAIL_WIDTH / width)))
        frame = cv2.resize(frame, (THUMBNAIL_WIDTH, scaled_height), interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), THUMBNAIL_QUALITY])
    if not ok:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buffer.tobytes()).decode("ascii")


def _read_frame(capture, t_s):
    capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(t_s)) * 1000.0)
    ok, frame = capture.read()
    return frame if ok and frame is not None else None


def _read_frame_near(capture, t_s, fps):
    """Read the frame at t_s, stepping one frame back when the seek lands past the end."""
    step = 1.0 / fps if fps and fps > 0 else 0.04
    for attempt in range(4):
        capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t_s - attempt * step) * 1000.0)
        ok, frame = capture.read()
        if ok:
            return frame
    return None
