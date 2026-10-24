# SPDX-License-Identifier: AGPL-3.0-or-later
"""Text-file reading shared by the tools."""

from pathlib import Path


def read_text(path, label, error=ValueError):
    """Read a UTF-8 text file, raising ``error(message)`` when it is missing or unreadable."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise error(f"{label} file not found: {path}")
    except OSError as exc:
        raise error(f"cannot read {label} file '{path}': {exc}")
