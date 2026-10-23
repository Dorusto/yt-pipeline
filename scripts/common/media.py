"""Media file discovery and file-name timestamps shared by the station tools."""

import re
from datetime import datetime
from pathlib import Path

from common.jsonio import load_json

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".mts", ".insv"}
MATTE_SUFFIXES = ("-Alpha", "_matte")
MATTE_RANGE_RE = re.compile(r"_\d+\.\d{2}-\d+\.\d{2}$")
CAMERA_PREFIX = "camera "
SCREEN_TOKEN = "screen"
FILENAME_TIMESTAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _](\d{2})-(\d{2})-(\d{2})")


def is_matte_export(path):
    """Return True for a derived matte export.

    Matches ``*-Alpha.mov`` and ``*_matte.mov`` exports and the by-hand range
    export name ``<stem>_<A:07.2f>-<B:07.2f>.mov`` (one matted source range).
    """
    stem = Path(str(path)).stem
    return stem.endswith(MATTE_SUFFIXES) or bool(MATTE_RANGE_RE.search(stem))


def is_video_name(name):
    """Return True when a sequence item's file name is a video clip, not a still or a graphic."""
    return Path(str(name)).suffix.lower() in VIDEO_EXTENSIONS


def clip_kind(name):
    """Return ``camera``, ``screen`` or None for a source clip's file name.

    Mode B names are ``camera YYYY-MM-DD HH-MM-SS.mp4`` for the camera angle and
    a bare ``YYYY-MM-DD HH-MM-SS`` stem (or a name carrying the ``screen``
    token) for the screen recording. Anything else (Mode A footage) has no
    angle and never takes part in a split layout.
    """
    stem = Path(str(name)).stem
    lowered = stem.lower()
    if lowered.startswith(CAMERA_PREFIX):
        return "camera"
    if SCREEN_TOKEN in lowered:
        return "screen"
    if stem_timestamp(name):
        return "screen"
    return None


def video_files(directory, exclude_matte=False):
    """Return the video files directly inside ``directory``, sorted by name."""
    if directory is None:
        return []
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix.lower() in VIDEO_EXTENSIONS
            and not (exclude_matte and is_matte_export(path))
        ),
        key=lambda path: path.name,
    )


def parse_stamp(name):
    """Return the first ``YYYY-MM-DD HH-MM-SS`` in the file name as a datetime, or None."""
    match = FILENAME_TIMESTAMP.search(Path(str(name)).name)
    if not match:
        return None
    year, month, day, hour, minute, second = (int(group) for group in match.groups())
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def stem_timestamp(name):
    """Return the first ``YYYY-MM-DD HH-MM-SS`` in the file name as a sortable string."""
    match = FILENAME_TIMESTAMP.search(Path(str(name)).name)
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    return f"{year}-{month}-{day} {hour}-{minute}-{second}"


def earliest_creation_date(workspace_path):
    """The date part (YYYY-MM-DD) of the earliest clip or photo creation_time in
    <workspace>/ingest.json, or None when it is missing, unreadable or empty."""
    data = load_json(Path(workspace_path) / "ingest.json")
    if not isinstance(data, dict):
        return None
    values = []
    for group in ("clips", "photos"):
        for item in data.get(group) or []:
            if not isinstance(item, dict):
                continue
            value = item.get("creation_time")
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
    if not values:
        return None
    earliest = min(values)
    return earliest[:10] if len(earliest) >= 10 else None
