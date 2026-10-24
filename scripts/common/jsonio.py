# SPDX-License-Identifier: AGPL-3.0-or-later
"""JSON helpers shared by the tools: tolerant reads, atomic writes, model answers.

``load_json`` is the tolerant read used by the web app and the metadata tool: a
missing or unreadable file is a normal state, so it returns ``None``. The strict
counterpart is ``common.timeline_words.load_json``, which raises
``TimelineWordsError`` because a bad words file must stop the run with a message
naming the file. ``write_json`` is the atomic writer that leaves no temporary
file behind; ``now_iso`` is the UTC timestamp every generated document records;
``parse_model_json`` unwraps a model answer that may sit inside a ```json
fence; and ``parse_child_json`` reads the JSON summary a chained child printed.
"""

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def load_json(path):
    """Load JSON from path, or None when it is missing or unreadable."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path, document):
    """Write the document atomically, leaving no temporary file behind."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def now_iso():
    """The current UTC time as an ISO-8601 string with seconds precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_model_json(text):
    """Parse a model answer that may be wrapped in a ```json fence; raise ValueError."""
    raw = text.strip()
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


def parse_child_json(stdout):
    """Return the child's JSON summary object, or None when there is none."""
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None
