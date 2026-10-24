# SPDX-License-Identifier: AGPL-3.0-or-later
"""Byte-size formatting shared by the tools."""

BYTES_PER_GB = 1024 ** 3


def format_gb(value):
    """A byte count as a short GB string, e.g. '34 GB' or '0.3 GB'."""
    return f"{round(value / BYTES_PER_GB, 1):g} GB"


def format_size(num_bytes):
    """Render a byte count as B, KB, MB or GB with one decimal."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    if num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    return f"{num_bytes / (1024 * 1024 * 1024):.1f} GB"


def format_size_short(num_bytes):
    """Render a byte count as a short MB or GB string, e.g. '45 MB' or '1.5 GB'."""
    if not isinstance(num_bytes, (int, float)) or isinstance(num_bytes, bool):
        return "0 MB"
    if num_bytes >= BYTES_PER_GB:
        return f"{num_bytes / BYTES_PER_GB:.1f} GB"
    return f"{num_bytes / (1024 * 1024):.0f} MB"
