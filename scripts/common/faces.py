"""YuNet face detection shared by the station tools."""

import os
import shutil
import sys
import urllib.request
from pathlib import Path

import cv2

DEFAULT_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
    "face_detection_yunet_2026may.onnx"
)
DEFAULT_MODEL_PATH = Path.home() / ".cache" / "yt-pipeline" / "models" / "face_detection_yunet_2026may.onnx"
DETECT_WIDTH = 640
SCORE_THRESHOLD = 0.6
NMS_THRESHOLD = 0.3


class FacesError(RuntimeError):
    """A user-facing face-detector error."""


def ensure_model(path, url=DEFAULT_MODEL_URL):
    """Return the YuNet model path, downloading it once when it is missing."""
    path = Path(path).expanduser()
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with urllib.request.urlopen(url) as response, open(temporary, "wb") as handle:
            shutil.copyfileobj(response, handle)
        os.replace(temporary, path)
    except Exception as exc:
        if temporary.exists():
            temporary.unlink()
        raise FacesError(f"cannot download model from {url}: {exc}") from exc
    print(f"[info] fetched {url} -> {path} ({path.stat().st_size} bytes)", file=sys.stderr)
    return path


def make_detector(model_path):
    """Create the YuNet face detector on the given ONNX model."""
    detector = cv2.FaceDetectorYN_create(
        str(model_path), "", (DETECT_WIDTH, DETECT_WIDTH), SCORE_THRESHOLD, NMS_THRESHOLD, 5000
    )
    return detector


def detect_faces(detector, frame):
    """Detect faces on a DETECT_WIDTH copy and return boxes in source pixels."""
    height, width = frame.shape[:2]
    scale = DETECT_WIDTH / width
    small = cv2.resize(frame, (DETECT_WIDTH, max(1, int(round(height * scale)))), interpolation=cv2.INTER_AREA)
    detector.setInputSize((small.shape[1], small.shape[0]))
    _, faces = detector.detect(small)
    boxes = []
    if faces is None:
        return boxes
    for face in faces:
        x, y, w, h = [float(value) / scale for value in face[:4]]
        score = float(face[-1])
        boxes.append((x, y, w, h, score))
    return boxes
