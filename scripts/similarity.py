#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Embed every quality segment and list the visually similar pairs.

Usage:
    python scripts/similarity.py --workspace DIR
    python scripts/similarity.py --workspace DIR --json
    python scripts/similarity.py --workspace DIR --backend hist --force --json

Reads ``quality/<stem>.json`` and the clips from the workspace, samples three
frames per segment (the same interior spacing as ``describe``), embeds them with
MobileNetV2 ONNX through ``cv2.dnn`` (default) or handcrafted histograms
(``--backend hist``, offline fallback) and writes one ``similarity.json`` per
workspace, validated against ``schemas/similarity.schema.json``. When
``ingest.json`` lists still photos, each photo is embedded from one frame as
pseudo-segment ``p1``, so photo/photo and photo/clip pairs are listed as well.
Pairs cross clips and photos, so the output is per workspace, not per clip.
Frames live in memory only and are never written to disk. Sources are read-only;
the output is written atomically.
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from common.excluded import is_excluded, load_excluded
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.errors import report_error
from common.photos import PhotoError, read_bgr
from common.frames import read_frame_pairs
from common.schema import SchemaError, validate, validate_or_raise

FRAME_COUNT = 3
FRAME_WIDTH = 640
PHOTO_SEGMENT_ID = "p1"
PHOTO_START_S = 0.0
PHOTO_END_S = 0.04
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
CLIP_COUNTER = re.compile(r"_(\d{4})_")


class SimilarityError(RuntimeError):
    """Raised when the workspace cannot be embedded."""


class SimilarityRuntimeError(RuntimeError):
    """Raised when the run fails after it started (model load, download, disk)."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="similarity.py",
        description=(
            "Embed every quality segment and still photo and list the pairs whose "
            "cosine similarity reaches the threshold. The director uses the pairs to "
            "refuse near-duplicate shots. Still photos listed in ingest.json are "
            "embedded from one frame as pseudo-segment p1."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/similarity.py --workspace <workspace>\n"
            "  python scripts/similarity.py --workspace <workspace> --json\n"
            "  python scripts/similarity.py --workspace <workspace> \\\n"
            "      --backend hist --force --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--workspace", required=True, help="workspace holding quality/; similarity.json is written here")
    parser.add_argument("--backend", choices=["mobilenet", "hist"], default=DEFAULT_BACKEND, help="embedding backend (default mobilenet)")
    parser.add_argument("--model-path", default=DEFAULT_MODEL_PATH, help="ONNX model file for the mobilenet backend")
    parser.add_argument("--model-url", default=DEFAULT_MODEL_URL, help="URL the ONNX model is downloaded from when missing")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="pairs at or above this cosine similarity are listed (default 0.90)")
    parser.add_argument("--clips", default=None, help="comma-separated 4-digit counters or stem substrings to include")
    parser.add_argument("--force", action="store_true", help="recompute even when similarity.json is up to date")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def frame_times(start_s, end_s):
    """Three evenly spaced timestamps inside the segment interior, in time order."""
    span = end_s - start_s
    return [start_s + (index + 0.5) / FRAME_COUNT * span for index in range(FRAME_COUNT)]


def clip_counter(name):
    """The 4-digit counter in a quality file name, or the stem when there is none."""
    match = CLIP_COUNTER.search(name)
    return match.group(1) if match else Path(name).stem


def segment_key(counter, segment_id):
    """The ``<clip counter>/<segment id>`` key used in the output."""
    return f"{counter}/{segment_id}"


def photo_keys(photos):
    """Return ``(photo, key)`` pairs with deterministic ``<counter>/p1`` keys.

    The counter comes from the photo file name the same way ``clip_counter``
    derives it for clips; a photo without a counter falls back to its stem, and
    a counter already used by an earlier photo in file-name order falls back to
    that photo's stem.
    """
    used = set()
    entries = []
    for photo in photos:
        name = photo.get("file") or Path(photo["path"]).name
        counter = clip_counter(name)
        if counter in used:
            counter = Path(name).stem
        used.add(counter)
        entries.append((photo, segment_key(counter, PHOTO_SEGMENT_ID)))
    return entries


def read_frames(path, times, width=FRAME_WIDTH):
    """Read one BGR frame per timestamp from a clip, resized to ``width``; frames stay in memory."""
    return [
        _resize_width(frame, width)
        for _t_s, frame in read_frame_pairs(path, times, error=SimilarityError)
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
        raise SimilarityError(f"cannot read photo {path}: {exc}") from exc
    return embed_frames([frame], backend, net)


def load_mobilenet(model_path, model_url):
    """Return the ONNX network, downloading the model once when it is missing."""
    path = Path(model_path).expanduser()
    if not path.exists():
        _download_model(path, model_url)
    try:
        return cv2.dnn.readNetFromONNX(str(path))
    except cv2.error as exc:
        raise SimilarityRuntimeError(f"cannot load ONNX model {path}: {exc}") from exc


def run(
    workspace,
    backend=DEFAULT_BACKEND,
    model_path=DEFAULT_MODEL_PATH,
    model_url=DEFAULT_MODEL_URL,
    threshold=DEFAULT_THRESHOLD,
    clips=None,
    force=False,
):
    """Embed the selected quality segments and still photos into ``workspace/similarity.json``; return summary and exit code."""
    started = time.monotonic()
    if not -1.0 <= threshold <= 1.0:
        raise SimilarityError("--threshold must be between -1 and 1")
    workspace_path = Path(workspace).expanduser().resolve()
    quality_dir = workspace_path / "quality"
    photos = _load_photos(workspace_path)
    excluded = load_excluded(workspace_path)
    if quality_dir.is_dir():
        selected = _select_quality_files(quality_dir, clips, excluded)
    elif photos:
        selected = []
    else:
        raise SimilarityError(f"no quality folder in workspace: {quality_dir}")
    ingest_path = workspace_path / "ingest.json"
    output_path = workspace_path / "similarity.json"
    if not force and _output_is_up_to_date(output_path, _input_paths(selected, ingest_path, photos)):
        return _skipped_summary(output_path, started), EXIT_OK
    net = load_mobilenet(model_path, model_url) if backend == "mobilenet" else None
    model_name = Path(model_path).name if backend == "mobilenet" else "none"
    segments = []
    for quality_file in selected:
        record = _load_quality(quality_file)
        clip_path = _clip_path(record, quality_file)
        counter = clip_counter(quality_file.name)
        for segment in record["segments"]:
            times = frame_times(segment["start_s"], segment["end_s"])
            frames = read_frames(clip_path, times)
            embedding = embed_frames(frames, backend, net)
            segments.append(
                {
                    "key": segment_key(counter, segment["id"]),
                    "clip": record["file"],
                    "segment_id": segment["id"],
                    "start_s": segment["start_s"],
                    "end_s": segment["end_s"],
                    "embedding": [round(float(value), 4) for value in embedding],
                }
            )
    photos_embedded = 0
    for photo, key in photo_keys(photos):
        photo_path = Path(photo["path"])
        if not photo_path.exists():
            raise SimilarityError(f"photo not found: {photo_path}")
        name = photo.get("file") or photo_path.name
        embedding = embed_photo(photo_path, backend, net)
        segments.append(
            {
                "key": key,
                "clip": name,
                "segment_id": PHOTO_SEGMENT_ID,
                "start_s": PHOTO_START_S,
                "end_s": PHOTO_END_S,
                "embedding": [round(float(value), 4) for value in embedding],
                "media_type": "photo",
            }
        )
        photos_embedded += 1
    pairs = _similar_pairs(segments, threshold)
    record = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "backend": backend,
        "model": model_name,
        "threshold": threshold,
        "segments": segments,
        "pairs": pairs,
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": 0.0,
        "warnings": [],
    }
    validate_or_raise(record, "similarity")
    _atomic_write_json(output_path, record)
    summary = {
        "segments": len(segments),
        "photos": photos_embedded,
        "pairs": len(pairs),
        "backend": backend,
        "model": model_name,
        "threshold": threshold,
        "wall_time_s": record["wall_time_s"],
        "llm_cost_usd": 0.0,
        "output": str(output_path),
        "skipped": False,
    }
    return summary, EXIT_OK


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary, exit_code = run(
            args.workspace,
            backend=args.backend,
            model_path=args.model_path,
            model_url=args.model_url,
            threshold=args.threshold,
            clips=args.clips,
            force=args.force,
        )
    except SimilarityRuntimeError as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except (SimilarityError, SchemaError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)
    except OSError as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    if args.json:
        print(json.dumps(summary))
    else:
        if summary["skipped"]:
            print(f"up to date: {summary['output']}")
        else:
            print(
                f"embedded {summary['segments']} segments, {summary['pairs']} pairs "
                f"at or above {summary['threshold']}"
            )
            print(
                f"backend {summary['backend']} ({summary['model']}), "
                f"{summary['wall_time_s']}s"
            )
    return exit_code


def _similar_pairs(segments, threshold):
    """Every pair at or above the threshold, most similar first, then by key."""
    pairs = []
    for i in range(len(segments)):
        first = segments[i]
        vector_a = np.asarray(first["embedding"], dtype=np.float64)
        for j in range(i + 1, len(segments)):
            second = segments[j]
            vector_b = np.asarray(second["embedding"], dtype=np.float64)
            similarity = float(np.dot(vector_a, vector_b))
            if similarity >= threshold:
                pairs.append(
                    {
                        "a": first["key"],
                        "b": second["key"],
                        "similarity": round(similarity, 4),
                    }
                )
    pairs.sort(key=lambda pair: (-pair["similarity"], pair["a"], pair["b"]))
    return pairs


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
        raise SimilarityRuntimeError(
            f"cannot download model from {url}: {exc}; use --backend hist for an offline run"
        ) from exc
    print(f"[info] fetched {url} -> {path} ({path.stat().st_size} bytes)", file=sys.stderr)


def _select_quality_files(quality_dir, clips, excluded=None):
    tokens = _clip_tokens(clips)
    files = sorted(quality_dir.glob("*.json"))
    selected = [
        path for path in files if _matches(path.stem, tokens) and not is_excluded(path.stem, excluded)
    ]
    if not selected:
        raise SimilarityError(f"no quality files matched in {quality_dir}")
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
        raise SimilarityError(f"unreadable quality file {path}: {exc}") from exc
    if not isinstance(data, dict) or not data.get("segments"):
        raise SimilarityError(f"quality file {path} has no segments")
    return data


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
    valid = [photo for photo in photos if isinstance(photo, dict) and photo.get("path")]
    return sorted(valid, key=lambda photo: photo.get("file") or "")


def _clip_path(record, quality_file):
    raw = record.get("path")
    if not raw:
        raise SimilarityError(f"quality file {quality_file.name} has no clip path")
    path = Path(raw)
    if not path.exists():
        raise SimilarityError(f"clip not found: {path}")
    return path


def _input_paths(quality_files, ingest_path, photos):
    """Every file ``similarity.json`` is built from: quality files, ingest.json and the photos."""
    inputs = list(quality_files)
    if ingest_path is not None and Path(ingest_path).exists():
        inputs.append(Path(ingest_path))
    inputs.extend(Path(photo["path"]) for photo in photos)
    return inputs


def _output_is_up_to_date(output_path, input_paths):
    if not output_path.exists() or not input_paths:
        return False
    try:
        output_mtime = output_path.stat().st_mtime
        newest_input = max(path.stat().st_mtime for path in input_paths)
    except OSError:
        return False
    return output_mtime >= newest_input


def _skipped_summary(output_path, started):
    try:
        data = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    segments = data.get("segments") or []
    return {
        "segments": len(segments),
        "photos": sum(1 for entry in segments if entry.get("media_type") == "photo"),
        "pairs": len(data.get("pairs") or []),
        "backend": data.get("backend"),
        "model": data.get("model"),
        "threshold": data.get("threshold"),
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": 0.0,
        "output": str(output_path),
        "skipped": True,
    }


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
