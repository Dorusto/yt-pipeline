# SPDX-License-Identifier: AGPL-3.0-or-later
"""Text-file reading shared by the tools."""

from pathlib import Path


def read_text(path, label, error=ValueError, quote=True):
    """Read a UTF-8 text file, raising ``error(message)`` when it is missing or unreadable."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise error(f"{label} file not found: {path}")
    except OSError as exc:
        shown = f"'{path}'" if quote else f"{path}"
        raise error(f"cannot read {label} file {shown}: {exc}")
