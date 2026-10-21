"""Media file discovery and file-name timestamps shared by the station tools."""

import re
from datetime import datetime
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".mts", ".insv"}
MATTE_SUFFIXES = ("-Alpha", "_matte")
FILENAME_TIMESTAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _](\d{2})-(\d{2})-(\d{2})")


def is_matte_export(path):
    """Return True for a derived matte export (``*-Alpha.mov``, ``*_matte.mov``)."""
    return Path(path).stem.endswith(MATTE_SUFFIXES)


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
