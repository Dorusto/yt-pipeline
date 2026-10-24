#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read and edit the instance's learning notebook (at most 40 lessons).

Usage:
    python scripts/notebook.py --notebook <file> --list
    python scripts/notebook.py --notebook <file> --add "keep the tunnel longer"
    python scripts/notebook.py --notebook <file> --remove l003
    python scripts/notebook.py --notebook <file> --merge l001,l002 --text "..."
    python scripts/notebook.py --notebook <file> --propose members_sentence --text "..."
    python scripts/notebook.py --notebook <file> --learn --workspace <workspace>

The notebook is a small JSON file of short lessons the director keeps between
edits. It holds at most ``NOTEBOOK_LIMIT`` (40) lessons; a write past the limit
is refused and the caller merges overlapping lessons or removes stale ones
first. A proposal lesson names a ``channel.fixed`` part to move a repeated
sentence to; the code never writes the config, the owner says yes.

``--learn`` runs the learning pass: one cheap model call reads the feedback the
workspace gained since the last pass and writes at most 3 short lessons.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from common.channel import CHANNEL_FIXED_PARTS
from common.config import ConfigError, load_config, no_notebook_message, notebook_path
from common.errors import report_error
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.feedback import read_feedback
from common.jsonio import load_json, now_iso, write_json
from common.llm import LLMError, make_client
from common.notebook import NOTEBOOK_LIMIT, load_notebook, notebook_lessons
from common.schema import SchemaError, validate_or_raise
from common.summary import summary_fields

TOOL = "notebook"
SCHEMA_VERSION = "1"
ID_RE = re.compile(r"^l(\d+)$")
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEARN_PROMPT = REPO_ROOT / "prompts" / "notebook_learn.md"
LEARN_MARKER = ".learn.json"
MAX_NEW_LESSONS = 3


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
            "  python scripts/notebook.py --notebook <file> --learn --workspace <workspace>\n"
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
    parser.add_argument("--learn", action="store_true", help="run the learning pass: read new feedback and write at most 3 lessons")
    parser.add_argument("--workspace", default=None, metavar="PATH", help="the workspace whose feedback to read; required with --learn")
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
    if args.learn:
        commands.append("--learn")
    if len(commands) != 1:
        raise NotebookError(
            "give exactly one of --list, --add, --remove, --merge, --propose, --learn"
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
        if len(set(ids)) != len(ids):
            raise NotebookError("--merge lists the same lesson id twice")
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


def read_learn_marker(workspace):
    """The workspace's ``.learn.json`` marker, or {} when it is absent or unreadable."""
    data = load_json(Path(workspace) / LEARN_MARKER)
    return data if isinstance(data, dict) else {}


def write_learn_marker(workspace):
    """Record the time of the last learning pass in the workspace marker."""
    write_json(Path(workspace) / LEARN_MARKER, {"last_learn_at": now_iso()})


def read_learn_prompt(prompt_path=None):
    """The learning prompt text; a missing file is a usage error."""
    path = Path(prompt_path).expanduser() if prompt_path else DEFAULT_LEARN_PROMPT
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise NotebookError(f"cannot read the learning prompt '{path}': {exc}") from exc


def build_learn_message(prompt_text, lessons, rows, brief_text):
    """The user message: the prompt, the existing lessons, the new feedback and the brief."""
    lines = [prompt_text, "", "## Existing lessons"]
    if lessons:
        for lesson in lessons:
            part = lesson.get("part")
            suffix = (
                f" [proposal -> channel.fixed.{part}]"
                if lesson.get("kind") == "proposal" and part
                else ""
            )
            lines.append(f"- {lesson.get('id')}{suffix}: {lesson.get('text')}")
    else:
        lines.append("- (none)")
    lines += ["", "## New feedback"]
    if rows:
        for row in rows:
            lines.append("- " + json.dumps(row, ensure_ascii=False))
    else:
        lines.append("- (none)")
    lines += ["", "## Brief", brief_text or "(none)"]
    return "\n".join(lines)


def normalize_learn_answer(answer):
    """Validate the model answer; return ``(lessons, remove_ids)`` or raise a runtime error."""
    if not isinstance(answer, dict):
        raise NotebookRuntimeError("model answer is not a JSON object")
    raw_lessons = answer.get("lessons")
    if not isinstance(raw_lessons, list):
        raise NotebookRuntimeError("model answer 'lessons' must be a list")
    lessons = []
    for raw in raw_lessons:
        if len(lessons) >= MAX_NEW_LESSONS:
            break
        if not isinstance(raw, dict):
            continue
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        kind = raw.get("kind") or "lesson"
        if kind not in ("lesson", "proposal"):
            continue
        part = raw.get("part")
        if kind == "proposal":
            if part not in CHANNEL_FIXED_PARTS:
                continue
        else:
            part = None
        lessons.append({"text": text.strip(), "kind": kind, "part": part})
    raw_remove = answer.get("remove")
    remove = (
        [item for item in raw_remove if isinstance(item, str)]
        if isinstance(raw_remove, list)
        else []
    )
    return lessons, remove


def trim_to_limit(lessons, limit):
    """Drop the oldest lessons (``created_at``, then id) until the list fits the limit."""
    ordered = sorted(
        lessons,
        key=lambda lesson: (str(lesson.get("created_at") or ""), str(lesson.get("id") or "")),
    )
    dropped = []
    while len(lessons) > limit and ordered:
        oldest = ordered.pop(0)
        index = find_index(lessons, oldest.get("id"))
        if index is not None:
            lessons.pop(index)
            dropped.append(oldest.get("id"))
    return dropped


def learn(workspace, notebook, limit):
    """Run the learning pass: read new feedback, ask the model, write at most 3 lessons."""
    started = time.monotonic()
    path = notebook_path(notebook)
    if path is None:
        raise NotebookError(no_notebook_message("--notebook"))
    workspace_path = Path(workspace).expanduser()
    if not workspace_path.is_dir():
        raise NotebookError(f"workspace is not a directory: {workspace_path}")
    lessons = notebook_lessons(path)
    marker = read_learn_marker(workspace_path)
    last_learn_at = marker.get("last_learn_at")
    rows = read_feedback(workspace_path)
    new_rows = [
        row
        for row in rows
        if last_learn_at is None or str(row.get("ts") or "") > str(last_learn_at)
    ]
    if not new_rows:
        summary = {
            "ok": True,
            "status": "skipped",
            "reason": "no new feedback",
            "notebook": str(path),
            "count": len(lessons),
            "limit": limit,
            "added": [],
            "removed": [],
            "model": None,
        }
        summary.update(summary_fields(started, 0.0))
        return summary
    prompt_text = read_learn_prompt()
    edit = load_json(workspace_path / "edit.json")
    brief_text = ""
    if isinstance(edit, dict):
        brief = edit.get("brief")
        if isinstance(brief, dict) and isinstance(brief.get("text"), str):
            brief_text = brief["text"]
    message = build_learn_message(prompt_text, lessons, new_rows, brief_text)
    client = make_client(load_config())
    response = client.complete_json("metadata", [{"role": "user", "content": message}])
    new_lessons, remove_ids = normalize_learn_answer(response.data)
    removed = []
    for lesson_id in remove_ids:
        index = find_index(lessons, lesson_id)
        if index is None:
            print(f"[warn] unknown lesson id in remove: {lesson_id}", file=sys.stderr)
            continue
        lessons.pop(index)
        removed.append(lesson_id)
    added = []
    for lesson in new_lessons:
        lesson_id = next_id(lessons)
        lessons.append(
            make_lesson(lesson_id, lesson["text"], kind=lesson["kind"], part=lesson["part"])
        )
        added.append(lesson_id)
    removed.extend(trim_to_limit(lessons, limit))
    document = build_document(lessons)
    validate_or_raise(document, "notebook")
    try:
        write_json(path, document)
    except OSError as exc:
        raise NotebookRuntimeError(f"could not write {path}: {exc}") from exc
    try:
        write_learn_marker(workspace_path)
    except OSError as exc:
        raise NotebookRuntimeError(f"could not write the learn marker: {exc}") from exc
    summary = {
        "ok": True,
        "status": "written",
        "notebook": str(path),
        "count": len(lessons),
        "limit": limit,
        "added": added,
        "removed": removed,
        "model": response.model,
    }
    summary.update(summary_fields(started, response.cost_usd))
    return summary


def print_learn(summary):
    if summary["status"] == "skipped":
        print(f"skipped: {summary['reason']}")
        return
    for lesson_id in summary["added"]:
        print(f"added {lesson_id}")
    for lesson_id in summary["removed"]:
        print(f"removed {lesson_id}")
    print(f"notebook: {summary['count']}/{summary['limit']} lessons")


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
        command = command_of(args)
        if args.workspace is not None and command != "--learn":
            raise NotebookError("--workspace is only valid with --learn")
        if command == "--learn":
            if args.workspace is None:
                raise NotebookError("--learn requires --workspace")
            summary = learn(args.workspace, args.notebook, args.limit)
            if args.json:
                print(json.dumps(summary))
            else:
                print_learn(summary)
            return EXIT_OK
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
    except (NotebookRuntimeError, LLMError) as exc:
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
