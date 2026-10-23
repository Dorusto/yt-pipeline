# SPDX-License-Identifier: AGPL-3.0-or-later
"""Append-only feedback log shared by the director and the web app.

Every feedback, reply and approval is one JSON line in
``WORKSPACE/feedback.jsonl``; the file is only ever appended to, so the history
of what was asked and answered stays readable.
"""

import json
from datetime import datetime, timezone
from pathlib import Path


def append_feedback(workspace, action, **fields):
    """Append one JSON line to WORKSPACE/feedback.jsonl (append-only)."""
    path = Path(workspace) / "feedback.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(timezone.utc).isoformat(), "action": action}
    row.update(fields)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
