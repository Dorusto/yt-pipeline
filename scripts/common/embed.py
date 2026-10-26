# SPDX-License-Identifier: AGPL-3.0-or-later
"""Embedding primitives shared by ``similarity.py`` and the window similarity helper.

Samples frames from a clip, embeds them with MobileNetV2 ONNX through
``cv2.dnn`` (default) or handcrafted histograms (``hist``, offline fallback) and
returns one L2-normalised vector per segment or window. Frames stay in memory
only and are never written to disk.
"""

import os
import shutil
import sys
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from common.frames import read_frame_pairs
from common.photos import PhotoError, read_bgr

FRAME_COUNT = 3
FRAME_WIDTH = 640
DEFAULT_BACKEND = "mobilenet"
DEFAULT_THRESHOLD = 0.90
DEFAULT_MODEL_PATH = "~/.cache/yt-pipeline/models/mobilenetv2-12.onnx"
DEFAULT_MODEL_URL = (
    "https://github.com/onnx/models/raw/main/validated/vision/classification/"
    "mobilenet/model/mobilenetv2-12.onnx"
)
MOBILENET_SIZE = 224
MOBILENET_PAD = 114
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 3, 1, 1)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 3, 1, 1)
GRADIENT_MAGNITUDE_MIN = 30.0


class EmbedError(RuntimeError):
    """Raised when a frame cannot be read or the model cannot be loaded or downloaded."""


def frame_times(start_s, end_s):
    """Three evenly spaced timestamps inside the segment interior, in time order."""
    span = end_s - start_s
    return [start_s + (index + 0.5) / FRAME_COUNT * span for index in range(FRAME_COUNT)]


def read_frames(path, times, width=FRAME_WIDTH, error=EmbedError):
    """Read one BGR frame per timestamp from a clip, resized to ``width``; frames stay in memory."""
    return [
        _resize_width(frame, width)
        for _t_s, frame in read_frame_pairs(path, times, error=error)
    ]


def embed_mobilenet(frames, net):
    """Mean of the L2-normalised MobileNetV2 logits of the frames, L2-normalised again."""
    vectors = [_l2_normalise(_mobilenet_frame(frame, net)) for frame in frames]
    return _l2_normalise(np.mean(vectors, axis=0))


def embed_hist(frames):
    """Mean of the L2-normalised handcrafted histograms of the frames, L2-normalised again."""
    vectors = [_l2_normalise(_hist_frame(frame)) for frame in frames]
    return _l2_normalise(np.mean(vectors, axis=0))


def embed_frames(frames, backend, net):
    """Embed one or more BGR frames with the selected backend."""
    if backend == "mobilenet":
        return embed_mobilenet(frames, net)
    return embed_hist(frames)


def embed_photo(path, backend, net):
    """Embed one still photo from a single resized frame; the frame stays in memory."""
    try:
        frame = read_bgr(path, FRAME_WIDTH)
    except PhotoError as exc:
        raise EmbedError(f"cannot read photo {path}: {exc}") from exc
    return embed_frames([frame], backend, net)


def load_mobilenet(model_path, model_url):
    """Return the ONNX network, downloading the model once when it is missing."""
    path = Path(model_path).expanduser()
    if not path.exists():
        _download_model(path, model_url)
    try:
        return cv2.dnn.readNetFromONNX(str(path))
    except cv2.error as exc:
        raise EmbedError(f"cannot load ONNX model {path}: {exc}") from exc


def _mobilenet_frame(frame, net):
    canvas = _letterbox(frame, MOBILENET_SIZE, MOBILENET_PAD)
    blob = cv2.dnn.blobFromImage(canvas, scalefactor=1 / 255, size=(MOBILENET_SIZE, MOBILENET_SIZE), swapRB=True)
    blob = (blob - IMAGENET_MEAN) / IMAGENET_STD
    net.setInput(blob)
    return net.forward().flatten().astype(np.float32)


def _hist_frame(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hue_sat = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256]).flatten()
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gradient = _gradient_histogram(gray)
    luma = cv2.calcHist([gray], [0], None, [16], [0, 256]).flatten()
    return np.concatenate(
        [_l1_normalise(hue_sat), _l1_normalise(gradient), _l1_normalise(luma)]
    ).astype(np.float32)


def _gradient_histogram(gray):
    gray_float = gray.astype(np.float32)
    gradient_x = cv2.Sobel(gray_float, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(gray_float, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = cv2.magnitude(gradient_x, gradient_y)
    angle = np.mod(cv2.phase(gradient_x, gradient_y, angleInDegrees=True), 180.0)
    selected = angle[magnitude > GRADIENT_MAGNITUDE_MIN]
    histogram, _ = np.histogram(selected, bins=8, range=(0.0, 180.0))
    return histogram.astype(np.float32)


def _letterbox(frame, size, pad):
    height, width = frame.shape[:2]
    scale = min(size / width, size / height)
    new_width = max(1, int(round(width * scale)))
    new_height = max(1, int(round(height * scale)))
    resized = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_AREA)
    canvas = np.full((size, size, 3), pad, dtype=np.uint8)
    top = (size - new_height) // 2
    left = (size - new_width) // 2
    canvas[top : top + new_height, left : left + new_width] = resized
    return canvas


def _resize_width(frame, width):
    height, frame_width = frame.shape[:2]
    if frame_width == width:
        return frame
    scaled_height = max(1, int(round(height * width / frame_width)))
    return cv2.resize(frame, (width, scaled_height), interpolation=cv2.INTER_AREA)


def _l1_normalise(vector):
    total = float(np.sum(np.abs(vector)))
    if total == 0.0:
        return vector.astype(np.float32)
    return (vector / total).astype(np.float32)


def _l2_normalise(vector):
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        return vector.astype(np.float32)
    return (vector / norm).astype(np.float32)


def _download_model(path, url):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with urllib.request.urlopen(url) as response, open(temporary, "wb") as handle:
            shutil.copyfileobj(response, handle)
        os.replace(temporary, path)
    except Exception as exc:
        if temporary.exists():
            temporary.unlink()
        raise EmbedError(
            f"cannot download model from {url}: {exc}; use --backend hist for an offline run"
        ) from exc
    print(f"[info] fetched {url} -> {path} ({path.stat().st_size} bytes)", file=sys.stderr)
