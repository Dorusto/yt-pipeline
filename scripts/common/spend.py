# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared run-spend ledger reading used by ``scripts/agent_watchdog.py`` and ``scripts/costs.py``.

No tool imports another tool: both read the same run-spend JSON through this module.
"""

import json
from pathlib import Path


def read_spend_ledger(path):
    """Return the spend ledger entries (list of dicts with ``cost_usd``), [] when absent."""
    target = Path(path).expanduser()
    if not target.exists():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"spend ledger {target} is unreadable: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("entries", [])
    if not isinstance(data, list):
        raise ValueError(f"spend ledger {target} must hold an entries list")
    return [
        entry
        for entry in data
        if isinstance(entry, dict) and isinstance(entry.get("cost_usd"), (int, float))
    ]
