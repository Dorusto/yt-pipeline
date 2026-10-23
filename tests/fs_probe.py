"""Safe filesystem probes for test skip conditions.

``Path.is_dir()`` and ``Path.is_file()`` raise ``OSError`` (e.g. ``PermissionError``
when an ancestor directory is not traversable, as on the GitHub runners where
``/root`` is unreadable) instead of returning ``False``. A skip condition must
never break collection, so every path check in a ``pytest.mark.skipif`` goes
through these helpers.
"""

from pathlib import Path


def safe_is_dir(path):
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def safe_is_file(path):
    try:
        return Path(path).is_file()
    except OSError:
        return False
