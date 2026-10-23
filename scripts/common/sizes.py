# SPDX-License-Identifier: AGPL-3.0-or-later
"""Byte-size formatting shared by the tools."""

BYTES_PER_GB = 1024 ** 3


def format_gb(value):
    """A byte count as a short GB string, e.g. '34 GB' or '0.3 GB'."""
    return f"{round(value / BYTES_PER_GB, 1):g} GB"
