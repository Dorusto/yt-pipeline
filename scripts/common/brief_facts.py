# SPDX-License-Identifier: AGPL-3.0-or-later
"""The four optional brief facts (place, when, who, occasion) as a plain helper.

The facts live in ``brief_facts.json`` as a JSON object with four optional
string keys. ``serve.py`` writes the file from the Upload and Re-edit forms and
the Recap tab's Rewrite form; the director prompt and ``recap_meta.py`` read it
as facts. There is no schema: a plain object with four optional string keys.
"""

import json
import os
from pathlib import Path

FACT_FIELDS = ("place", "when", "who", "occasion")
FACT_LABELS = {"place": "Place", "when": "When", "who": "Who is in it", "occasion": "Occasion"}
BRIEF_FACTS_NAME = "brief_facts.json"


def _facts_path(path):
    """The ``brief_facts.json`` path for a directory or the file itself."""
    path = Path(path)
    if path.is_dir():
        return path / BRIEF_FACTS_NAME
    return path


def load_brief_facts(path):
    """The four fact fields from brief_facts.json; empty strings when the file or a field is absent.

    ``path`` may be a directory (reads ``path/brief_facts.json``) or the file itself.
    Non-string values are ignored. Always returns a dict with all four keys.
    """
    facts = {name: "" for name in FACT_FIELDS}
    try:
        data = json.loads(_facts_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return facts
    if not isinstance(data, dict):
        return facts
    for name in FACT_FIELDS:
        value = data.get(name)
        if isinstance(value, str):
            facts[name] = value.strip()
    return facts


def write_brief_facts(path, facts):
    """Write ``path/brief_facts.json`` atomically with only the non-empty string fields.

    ``path`` is a directory (created if missing). Writes ``{}`` when every field is empty.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    document = {}
    for name in FACT_FIELDS:
        value = (facts or {}).get(name)
        if isinstance(value, str) and value.strip():
            document[name] = value.strip()
    target = path / BRIEF_FACTS_NAME
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def facts_block(facts):
    """A ``Place: ...`` block for the director prompt, one line per non-empty field, or ""."""
    lines = []
    for name in FACT_FIELDS:
        value = (facts or {}).get(name)
        if isinstance(value, str) and value.strip():
            lines.append(f"{FACT_LABELS[name]}: {value.strip()}")
    return "\n".join(lines)
