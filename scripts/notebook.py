#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read and edit the instance's learning notebook (at most 40 lessons).

Usage:
    python scripts/notebook.py --notebook <file> --list
    python scripts/notebook.py --notebook <file> --add "keep the tunnel longer"
    python scripts/notebook.py --notebook <file> --remove l003
    python scripts/notebook.py --notebook <file> --merge l001,l002 --text "..."
    python scripts/notebook.py --notebook <file> --propose members_sentence --text "..."

The notebook is a small JSON file of short lessons the director keeps between
edits. It holds at most ``NOTEBOOK_LIMIT`` (40) lessons; a write past the limit
is refused and the caller merges overlapping lessons or removes stale ones
first. A proposal lesson names a ``channel.fixed`` part to move a repeated
sentence to; the code never writes the config, the owner says yes.
"""

import argparse
import json
import re
import sys
import time

from common.channel import CHANNEL_FIXED_PARTS
from common.config import ConfigError, no_notebook_message, notebook_path
from common.errors import report_error
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.jsonio import now_iso, write_json
from common.notebook import NOTEBOOK_LIMIT, load_notebook, notebook_lessons
from common.schema import SchemaError, validate_or_raise
from common.summary import summary_fields

TOOL = "notebook"
SCHEMA_VERSION = "1"
ID_RE = re.compile(r"^l(\d+)$")


class NotebookError(RuntimeError):
    """Raised when the notebook cannot be edited (usage or input error)."""


class NotebookRuntimeError(NotebookError):
    """Raised when the notebook cannot be written after the run started."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="notebook.py",
        description="Read and edit the instance's learning notebook (at most 40 lessons).",
        epilog=(
            "examples:\n"
            "  python scripts/notebook.py --notebook <file> --list\n"
            "  python scripts/notebook.py --notebook <file> --add \"keep the tunnel longer\"\n"
            "  python scripts/notebook.py --notebook <file> --remove l003\n"
            "  python scripts/notebook.py --notebook <file> --merge l001,l002 --text \"...\"\n"
            "  python scripts/notebook.py --notebook <file> --propose members_sentence --text \"...\"\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--notebook",
        default=None,
        help=(
            "instance notebook JSON file (or YT_PIPELINE_NOTEBOOK, or notebook.path "
            "in the config; no personal default)"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=NOTEBOOK_LIMIT,
        help=f"max lessons (default: {NOTEBOOK_LIMIT}; a test override)",
    )
    parser.add_argument("--list", action="store_true", help="print the notebook")
    parser.add_argument("--add", action="append", metavar="TEXT", help="add one lesson (repeatable)")
    parser.add_argument("--remove", action="append", metavar="ID", help="remove one lesson by id (repeatable)")
    parser.add_argument("--merge", default=None, metavar="IDS", help="comma-separated lesson ids to replace with one lesson; requires --text")
    parser.add_argument("--propose", default=None, metavar="PART", help="add a proposal lesson to move a repeated sentence to channel.fixed.<part>; requires --text")
    parser.add_argument("--text", default=None, metavar="TEXT", help="the lesson text for --merge / --propose")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def command_of(args):
    """The one command named by the flags, or raise a usage error."""
    commands = []
    if args.list:
        commands.append("--list")
    if args.add:
        commands.append("--add")
    if args.remove:
        commands.append("--remove")
    if args.merge is not None:
        commands.append("--merge")
    if args.propose is not None:
        commands.append("--propose")
    if len(commands) != 1:
        raise NotebookError(
            "give exactly one of --list, --add, --remove, --merge, --propose"
        )
    return commands[0]


def next_id(lessons):
    """The next free ``lNNN`` id: one past the highest number, never reused."""
    numbers = [
        int(match.group(1))
        for lesson in lessons
        if (match := ID_RE.match(str(lesson.get("id") or "")))
    ]
    return f"l{max(numbers, default=0) + 1:03d}"


def find_index(lessons, lesson_id):
    """The index of the lesson with ``lesson_id``, or None."""
    for index, lesson in enumerate(lessons):
        if lesson.get("id") == lesson_id:
            return index
    return None


def make_lesson(lesson_id, text, kind="lesson", part=None):
    lesson = {"id": lesson_id, "text": text, "kind": kind, "created_at": now_iso()}
    if part is not None:
        lesson["part"] = part
    return lesson


def apply_command(args, lessons):
    """Apply the one command to ``lessons`` in place; return the ``changed`` summary."""
    changed = {"added": [], "removed": [], "merged": []}
    if args.list:
        return changed
    if args.add:
        for text in args.add:
            lesson_id = next_id(lessons)
            lessons.append(make_lesson(lesson_id, text))
            changed["added"].append(lesson_id)
        return changed
    if args.remove:
        for lesson_id in args.remove:
            index = find_index(lessons, lesson_id)
            if index is None:
                raise NotebookError(f"unknown lesson id: {lesson_id}")
            lessons.pop(index)
            changed["removed"].append(lesson_id)
        return changed
    if args.merge is not None:
        if args.text is None:
            raise NotebookError("--merge requires --text")
        ids = [part.strip() for part in args.merge.split(",") if part.strip()]
        if not ids:
            raise NotebookError("--merge needs at least one lesson id")
        for lesson_id in ids:
            if find_index(lessons, lesson_id) is None:
                raise NotebookError(f"unknown lesson id: {lesson_id}")
        for lesson_id in ids:
            lessons.pop(find_index(lessons, lesson_id))
        lesson_id = next_id(lessons)
        lessons.append(make_lesson(lesson_id, args.text))
        changed["removed"].extend(ids)
        changed["merged"].extend(ids)
        changed["added"].append(lesson_id)
        return changed
    if args.propose is not None:
        if args.text is None:
            raise NotebookError("--propose requires --text")
        lesson_id = next_id(lessons)
        lessons.append(make_lesson(lesson_id, args.text, kind="proposal", part=args.propose))
        changed["added"].append(lesson_id)
        return changed
    return changed


def read_lessons(path):
    """The notebook's lessons; a missing file is empty, a bad one is a usage error."""
    if not path.exists():
        return []
    if load_notebook(path) is None:
        raise NotebookError(f"notebook is not valid JSON: {path}")
    return notebook_lessons(path)


def build_document(lessons):
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "tool": TOOL,
        "lessons": lessons,
    }


def print_human(changed, count, limit):
    for lesson_id in changed["added"]:
        print(f"added {lesson_id}")
    for lesson_id in changed["removed"]:
        print(f"removed {lesson_id}")
    if changed["merged"]:
        print(f"merged {','.join(changed['merged'])} -> {changed['added'][-1]}")
    print(f"notebook: {count}/{limit} lessons")


def main(argv=None):
    args = build_parser().parse_args(argv)
    started = time.monotonic()
    try:
        command_of(args)
        if args.propose is not None and args.propose not in CHANNEL_FIXED_PARTS:
            raise NotebookError(
                f"--propose part must be one of: {', '.join(CHANNEL_FIXED_PARTS)}"
            )
        path = notebook_path(args.notebook)
        if path is None:
            raise NotebookError(no_notebook_message("--notebook"))
        lessons = read_lessons(path)
        changed = apply_command(args, lessons)
        if len(lessons) > args.limit:
            raise NotebookError(
                f"notebook is full ({args.limit} lessons): merge overlapping lessons "
                "or remove stale ones first, then retry"
            )
        if not args.list:
            document = build_document(lessons)
            validate_or_raise(document, "notebook")
            try:
                write_json(path, document)
            except OSError as exc:
                raise NotebookRuntimeError(f"could not write {path}: {exc}") from exc
        summary = {
            "ok": True,
            "notebook": str(path),
            "count": len(lessons),
            "limit": args.limit,
            "changed": changed,
            "lessons": lessons,
            **summary_fields(started),
        }
    except NotebookRuntimeError as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except (NotebookError, ConfigError, SchemaError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)
    if args.json:
        print(json.dumps(summary))
    else:
        print_human(changed, summary["count"], summary["limit"])
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
