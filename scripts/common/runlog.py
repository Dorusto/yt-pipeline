# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read a workspace's run.log and its spend ledger.

The web app and the director both need the latest run's start time, its log
lines and how much the model spent since then. The log is read from its tail
(``LOG_TAIL_BYTES``) so a long run stays cheap to inspect.
"""

import os
import time
from datetime import datetime
from pathlib import Path

from common.llm import LLMError, read_ledger

RUN_MARKER = "=== recap run started"
LOG_TAIL_BYTES = 262144


def last_run_lines(directory):
    """The run.log lines of the latest run (after the last start marker)."""
    path = Path(directory) / "run.log"
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - LOG_TAIL_BYTES))
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = text.splitlines()
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].startswith(RUN_MARKER):
            return lines[index + 1:]
    return lines


def last_run_start(directory):
    """Epoch seconds of the latest run's start marker, or None."""
    path = Path(directory) / "run.log"
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - LOG_TAIL_BYTES))
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return None
    for line in reversed(text.splitlines()):
        if line.startswith(RUN_MARKER):
            stamp = line[len(RUN_MARKER):].strip(" =")
            try:
                return time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                return None
    return None


def epoch_of(stamp):
    """Epoch seconds of an ISO time with an offset, or None."""
    text = str(stamp or "")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z",):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def spend_between(directory, start, end=None, role=None):
    """Model spend in USD recorded in llm_spend.json between two epoch times.

    With `role`, only entries of that role count and None is returned when none match; without it the sum is returned (0.0 when there are none). None when the ledger cannot be read.
    """
    ledger_path = Path(directory) / "llm_spend.json"
    if not ledger_path.exists():
        return None
    try:
        ledger = read_ledger(ledger_path)
    except LLMError:
        return None
    total = 0.0
    found = False
    for entry in ledger.get("entries") or []:
        if role is not None and entry.get("role") != role:
            continue
        when = epoch_of(entry.get("ts"))
        if when is None or (start is not None and when < start) or (end is not None and when > end):
            continue
        total += float(entry.get("cost_usd") or 0.0)
        found = True
    if role is not None:
        return round(total, 8) if found else None
    return total
