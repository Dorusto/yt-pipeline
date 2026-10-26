# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read the instance's learning notebook: short lessons the director keeps between edits.

The notebook is a small JSON file (``schemas/notebook.schema.json``) of lessons
the director learns on this instance. It is optional: with no path set the
director reads nothing and the web app shows no section. The file holds at most
``NOTEBOOK_LIMIT`` lessons; a write past the limit is refused and the caller
merges overlapping lessons or removes stale ones first. A proposal lesson names
a ``channel.fixed`` part to move a repeated sentence to; the code never writes
the config, the owner says yes.
"""

from pathlib import Path

from common.jsonio import load_json

NOTEBOOK_LIMIT = 40


def load_notebook(path):
    """Return the notebook document, or None when the file is missing or unreadable."""
    if path is None:
        return None
    data = load_json(path)
    return data if isinstance(data, dict) else None


def notebook_lessons(path):
    """Return the list of lesson dicts, or [] when there is no notebook."""
    document = load_notebook(path)
    if document is None:
        return []
    lessons = document.get("lessons")
    if not isinstance(lessons, list):
        return []
    return [lesson for lesson in lessons if isinstance(lesson, dict)]


def notebook_waiting(path):
    """Return the list of waiting lesson dicts, or [] when there is none."""
    document = load_notebook(path)
    if document is None:
        return []
    waiting = document.get("waiting")
    if not isinstance(waiting, list):
        return []
    return [lesson for lesson in waiting if isinstance(lesson, dict)]


def notebook_ledger_path(path):
    """The learn-pass spend ledger next to the notebook file."""
    return Path(path).with_suffix(".spend.json")


def notebook_block(path):
    """Return the director's ``## Notebook`` text block, or None when there are no lessons."""
    lines = []
    for lesson in notebook_lessons(path):
        text = lesson.get("text")
        if not isinstance(text, str) or not text:
            continue
        lesson_id = lesson.get("id")
        if lesson.get("kind") == "proposal" and lesson.get("part"):
            lines.append(f"- {lesson_id} [proposal \u2192 channel.fixed.{lesson['part']}]: {text}")
        else:
            lines.append(f"- {lesson_id}: {text}")
    if not lines:
        return None
    return "## Notebook (lessons learned on this instance)\n" + "\n".join(lines)
