"""Still-photo helpers: JPEG/HEIC decode, EXIF capture time, renderable copies.

Shared by ingest, describe, similarity, index and render. Pillow plus
pillow-heif are the single decode path (DECISIONS 2026-10-14): one register
call covers JPEG and HEIC pixels and the EXIF tags the pipeline needs. ffmpeg
on the build machines has no HEIC decoder, so render asks for a JPEG copy of
every photo before building its filter graph. Photos are read-only; copies go
to the workspace.
"""

import hashlib
import io
import os
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".heic"}
EXIF_DATETIME_ORIGINAL = 36867
EXIF_DATETIME = 306
EXIF_ORIENTATION = 274
JPEG_QUALITY = 92
KEN_BURNS_AMOUNT = 0.08
KEN_BURNS_CENTRE_X = 0.15
KEN_BURNS_CENTRE_Y = 0.2
CLOSE_UP_TAGS = {"close-up", "closeup"}

_registered = False


class PhotoError(RuntimeError):
    """Raised when a photo cannot be read or converted."""


def _register_heif():
    global _registered
    if _registered:
        return
    try:
        from pillow_heif import register_heif_opener
    except ImportError as exc:
        raise PhotoError(
            "pillow-heif is required for HEIC photos; install requirements.txt"
        ) from exc
    register_heif_opener()
    _registered = True


def is_photo(path):
    return Path(path).suffix.lower() in PHOTO_EXTENSIONS


def open_photo(path):
    """Open a JPEG/HEIC still with EXIF orientation applied; returns an RGB image."""
    _register_heif()
    try:
        image = Image.open(path)
        image = ImageOps.exif_transpose(image)
        return image.convert("RGB")
    except Exception as exc:
        raise PhotoError(f"cannot read photo {path}: {exc}") from exc


def _parse_exif_datetime(value):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def capture_time(path):
    """Return ``(iso_time, source)`` from EXIF DateTimeOriginal; source is 'exif' or None."""
    _register_heif()
    try:
        with Image.open(path) as image:
            exif = image.getexif()
    except Exception:
        return None, None
    moment = _parse_exif_datetime(exif.get(EXIF_DATETIME_ORIGINAL)) or _parse_exif_datetime(
        exif.get(EXIF_DATETIME)
    )
    if moment is None:
        return None, None
    return moment.isoformat(), "exif"


def photo_metadata(path):
    """Return size, orientation and capture time of one photo."""
    _register_heif()
    path = Path(path)
    try:
        with Image.open(path) as image:
            width, height = image.size
            exif = image.getexif()
            orientation = exif.get(EXIF_ORIENTATION)
            image_format = image.format
    except Exception as exc:
        raise PhotoError(f"cannot read photo {path}: {exc}") from exc
    iso_time, source = capture_time(path)
    return {
        "file": path.name,
        "path": str(path),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "width": int(width),
        "height": int(height),
        "orientation": int(orientation) if orientation else None,
        "creation_time": iso_time,
        "creation_time_source": source,
        "format": image_format,
    }


def read_bgr(path, width=None):
    """Decode a photo to an OpenCV-style BGR array, optionally scaled to ``width``."""
    image = open_photo(path)
    if width and image.width != width:
        height = max(1, round(image.height * width / image.width))
        image = image.resize((int(width), height), Image.LANCZOS)
    rgb = image.convert("RGB")
    buffer = io.BytesIO()
    rgb.save(buffer, "JPEG", quality=90)
    buffer.seek(0)
    import numpy as np

    data = np.frombuffer(buffer.getvalue(), dtype=np.uint8)
    import cv2

    bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if bgr is None:
        raise PhotoError(f"cannot decode photo {path}")
    return bgr


def jpeg_bytes(image, quality=JPEG_QUALITY):
    """Encode an RGB image as JPEG bytes."""
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


def ensure_jpeg(path, out_dir):
    """Return a JPEG path renderable by ffmpeg; HEIC stills are converted once.

    JPEG inputs are returned unchanged (sources are read-only). Converted files
    land in ``out_dir`` keyed by content hash and are reused on later runs.
    """
    path = Path(path)
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        return path
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(f"{path}-{path.stat().st_mtime_ns}".encode("utf-8")).hexdigest()[:16]
    target = out_dir / f"{path.stem}_{digest}.jpg"
    if target.exists():
        return target
    image = open_photo(path)
    temporary = target.with_name(target.name + ".tmp")
    try:
        image.save(temporary, "JPEG", quality=JPEG_QUALITY)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def subject_xy(subject_position):
    """The subject centre as ``(x, y)`` floats, or ``(None, None)`` when unusable."""
    if not isinstance(subject_position, dict):
        return None, None
    x = subject_position.get("x")
    y = subject_position.get("y")
    if isinstance(x, bool) or isinstance(y, bool):
        return None, None
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None, None
    return float(x), float(y)


def ken_burns_for(subject_position, tags):
    """The deterministic Ken Burns move for a photo from its subject and tags.

    Missing subject -> slow zoom on the centre; a subject in the centre box ->
    zoom in on it; otherwise pan toward the subject; close-up tags -> zoom out.
    """
    tag_set = {str(tag).strip().lower() for tag in (tags or [])}
    x, y = subject_xy(subject_position)
    if tag_set & CLOSE_UP_TAGS:
        return {
            "move": "zoom_out",
            "amount": KEN_BURNS_AMOUNT,
            "focus_x": x if x is not None else 0.5,
            "focus_y": y if y is not None else 0.5,
        }
    if x is None or y is None:
        return {"move": "zoom_in", "amount": KEN_BURNS_AMOUNT, "focus_x": 0.5, "focus_y": 0.5}
    if abs(x - 0.5) <= KEN_BURNS_CENTRE_X and abs(y - 0.5) <= KEN_BURNS_CENTRE_Y:
        return {"move": "zoom_in", "amount": KEN_BURNS_AMOUNT, "focus_x": x, "focus_y": y}
    move = "pan_left" if x < 0.5 else "pan_right"
    return {"move": move, "amount": KEN_BURNS_AMOUNT, "focus_x": x, "focus_y": y}
