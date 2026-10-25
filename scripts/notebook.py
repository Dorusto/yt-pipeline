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
workspace gained since the last pass and writes at most 3 short lessons. When
the notebook is full it makes one more call to merge or remove lessons; lessons
that still do not fit are kept as ``waiting`` lessons, never dropped. When 3
lessons already wait the pass makes no model call and reports ``waiting_full``;
when some room is left it asks for at most the remaining room's worth of new
lessons.
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
from common.notebook import (
    NOTEBOOK_LIMIT,
    load_notebook,
    notebook_ledger_path,
    notebook_lessons,
    notebook_waiting,
)
from common.schema import SchemaError, validate_or_raise
from common.summary import summary_fields
from common.text import read_text

TOOL = "notebook"
SCHEMA_VERSION = "1"
ID_RE = re.compile(r"^l(\d+)$")
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEARN_PROMPT = REPO_ROOT / "prompts" / "notebook_learn.md"
DEFAULT_MERGE_PROMPT = REPO_ROOT / "prompts" / "notebook_merge.md"
LEARN_MARKER = ".learn.json"
MAX_NEW_LESSONS = 3
MAX_WAITING = 3
SHOT_ID_RE = re.compile(r"\d{3,4}/s\d+")
CLIP_NUMBER_RE = re.compile(r"\b\d{4}\b")


def lesson_is_general(text, project_name=None):
    """True when a lesson names no shot id, no 4-digit clip number and no project name."""
    if SHOT_ID_RE.search(text) or CLIP_NUMBER_RE.search(text):
        return False
    name = (project_name or "").strip()
    if name and name.lower() in text.lower():
        return False
    return True


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


def lesson_id_number(lesson):
    """The numeric part of a lesson's ``lNNN`` id, or 0 when it has none."""
    match = ID_RE.match(str(lesson.get("id") or ""))
    return int(match.group(1)) if match else 0


def next_id(lessons):
    """The next free ``lNNN`` id: one past the highest number, never reused."""
    return f"l{max((lesson_id_number(lesson) for lesson in lessons), default=0) + 1:03d}"


def next_notebook_id(lessons, waiting):
    """The next free ``lNNN`` id across the notebook's lessons and waiting lessons."""
    return next_id(lessons + waiting)


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


def remove_lessons(lessons, ids):
    """Remove each id from ``lessons``; return the removed ids, or raise on an unknown id."""
    removed = []
    for lesson_id in ids:
        index = find_index(lessons, lesson_id)
        if index is None:
            raise NotebookError(f"unknown lesson id: {lesson_id}")
        lessons.pop(index)
        removed.append(lesson_id)
    return removed


def merge_lessons(lessons, waiting, ids, text):
    """Replace the named lessons with one new lesson; return ``(removed_ids, new_id)``."""
    if len(set(ids)) != len(ids):
        raise NotebookError("--merge lists the same lesson id twice")
    for lesson_id in ids:
        if find_index(lessons, lesson_id) is None:
            raise NotebookError(f"unknown lesson id: {lesson_id}")
    removed = remove_lessons(lessons, ids)
    new_id = next_notebook_id(lessons, waiting)
    lessons.append(make_lesson(new_id, text))
    return removed, new_id


def promote_waiting(lessons, waiting, limit):
    """Move waiting lessons into ``lessons`` (oldest first) while there is room; return the ids."""
    promoted = []
    while waiting and len(lessons) < limit:
        lesson = waiting.pop(0)
        lessons.append(lesson)
        promoted.append(lesson.get("id"))
    return promoted


def apply_command(args, lessons, waiting):
    """Apply the one command to ``lessons``/``waiting`` in place; return the ``changed`` summary."""
    changed = {"added": [], "removed": [], "merged": [], "promoted": []}
    if args.list:
        return changed
    if args.add:
        for text in args.add:
            lesson_id = next_notebook_id(lessons, waiting)
            lessons.append(make_lesson(lesson_id, text))
            changed["added"].append(lesson_id)
        return changed
    if args.remove:
        for lesson_id in args.remove:
            index = find_index(lessons, lesson_id)
            if index is not None:
                lessons.pop(index)
                changed["removed"].append(lesson_id)
                continue
            waiting_index = find_index(waiting, lesson_id)
            if waiting_index is None:
                raise NotebookError(f"unknown lesson id: {lesson_id}")
            waiting.pop(waiting_index)
            changed["removed"].append(lesson_id)
        return changed
    if args.merge is not None:
        if args.text is None:
            raise NotebookError("--merge requires --text")
        ids = [part.strip() for part in args.merge.split(",") if part.strip()]
        if not ids:
            raise NotebookError("--merge needs at least one lesson id")
        removed, new_id = merge_lessons(lessons, waiting, ids, args.text)
        changed["removed"].extend(removed)
        changed["merged"].extend(removed)
        changed["added"].append(new_id)
        return changed
    if args.propose is not None:
        if args.text is None:
            raise NotebookError("--propose requires --text")
        lesson_id = next_notebook_id(lessons, waiting)
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


def read_waiting(path):
    """The notebook's waiting lessons; a missing file is empty, a bad one is a usage error."""
    if not path.exists():
        return []
    if load_notebook(path) is None:
        raise NotebookError(f"notebook is not valid JSON: {path}")
    return notebook_waiting(path)


def build_document(lessons, waiting):
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "tool": TOOL,
        "lessons": lessons,
        "waiting": waiting,
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
    return read_text(path, "learning prompt", error=NotebookError)


def read_merge_prompt():
    """The merge prompt text; a missing file is a usage error."""
    return read_text(DEFAULT_MERGE_PROMPT, "merge prompt", error=NotebookError)


def build_merge_message(prompt_text, lessons, limit):
    """The merge-pass user message: the prompt and the current lessons."""
    lines = [prompt_text, "", f"## Lessons ({len(lessons)} of {limit})"]
    for lesson in lessons:
        lines.append(f"- {lesson.get('id')}: {lesson.get('text')}")
    return "\n".join(lines)


def build_learn_message(prompt_text, lessons, rows, brief_text, max_new=MAX_NEW_LESSONS):
    """The user message: the prompt, the existing lessons, the new feedback and the brief."""
    lines = [prompt_text]
    if max_new < MAX_NEW_LESSONS:
        lines += ["", f"Write at most {max_new} new lesson(s) this pass."]
    lines += ["", "## Existing lessons"]
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


def normalize_learn_answer(answer, max_new=MAX_NEW_LESSONS, project_name=None):
    """Validate the model answer; return ``(lessons, remove_ids)`` or raise a runtime error."""
    if not isinstance(answer, dict):
        raise NotebookRuntimeError("model answer is not a JSON object")
    raw_lessons = answer.get("lessons")
    if not isinstance(raw_lessons, list):
        raise NotebookRuntimeError("model answer 'lessons' must be a list")
    lessons = []
    for raw in raw_lessons:
        if len(lessons) >= max_new:
            break
        if not isinstance(raw, dict):
            continue
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        if not lesson_is_general(text.strip(), project_name):
            print(
                f"[warn] skipping a lesson that names a shot, a clip or the project: {text.strip()}",
                file=sys.stderr,
            )
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


def normalize_merge_answer(answer):
    """Validate the merge answer; return ``(merges, remove_ids)``, never raising on a bad answer."""
    if not isinstance(answer, dict):
        return [], []
    raw_merges = answer.get("merge")
    merges = []
    if isinstance(raw_merges, list):
        for raw in raw_merges:
            if not isinstance(raw, dict):
                continue
            text = raw.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            raw_ids = raw.get("ids")
            if not isinstance(raw_ids, list):
                continue
            ids = []
            for item in raw_ids:
                if isinstance(item, str) and item not in ids:
                    ids.append(item)
            if len(ids) < 2:
                continue
            merges.append({"ids": ids, "text": text.strip()})
    raw_remove = answer.get("remove")
    remove = (
        [item for item in raw_remove if isinstance(item, str)]
        if isinstance(raw_remove, list)
        else []
    )
    return merges, remove


def waiting_full_summary(path, count, waiting, limit, started, reason="waiting lessons are full"):
    """The summary of a learn pass that wrote nothing because there is no room."""
    summary = {
        "ok": True,
        "status": "waiting_full",
        "reason": reason,
        "notebook": str(path),
        "count": count,
        "limit": limit,
        "waiting": waiting,
        "added": [],
        "removed": [],
        "model": None,
    }
    summary.update(summary_fields(started, 0.0))
    return summary


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
    waiting = notebook_waiting(path)
    count_on_disk = len(lessons)
    waiting_on_disk = len(waiting)
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
            "waiting": len(waiting),
            "added": [],
            "removed": [],
            "model": None,
        }
        summary.update(summary_fields(started, 0.0))
        return summary
    room = MAX_WAITING - len(waiting)
    if room <= 0:
        return waiting_full_summary(path, count_on_disk, waiting_on_disk, limit, started)
    if len(lessons) > limit:
        return waiting_full_summary(
            path,
            count_on_disk,
            waiting_on_disk,
            limit,
            started,
            reason="the notebook is over its limit",
        )
    max_new = min(MAX_NEW_LESSONS, room + max(0, limit - len(lessons)))
    prompt_text = read_learn_prompt()
    edit = load_json(workspace_path / "edit.json")
    brief_text = ""
    if isinstance(edit, dict):
        brief = edit.get("brief")
        if isinstance(brief, dict) and isinstance(brief.get("text"), str):
            brief_text = brief["text"]
    message = build_learn_message(prompt_text, lessons, new_rows, brief_text, max_new)
    client = make_client(load_config(), ledger_path=notebook_ledger_path(path))
    response = client.complete_json("metadata", [{"role": "user", "content": message}])
    llm_cost = response.cost_usd
    new_lessons, remove_ids = normalize_learn_answer(response.data, max_new, workspace_path.name)
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
        lesson_id = next_notebook_id(lessons, waiting)
        lessons.append(
            make_lesson(lesson_id, lesson["text"], kind=lesson["kind"], part=lesson["part"])
        )
        added.append(lesson_id)
    if len(lessons) > limit:
        merge_prompt = read_merge_prompt()
        merge_message = build_merge_message(merge_prompt, lessons, limit)
        merge_response = client.complete_json(
            "metadata", [{"role": "user", "content": merge_message}]
        )
        llm_cost += merge_response.cost_usd
        merges, merge_remove = normalize_merge_answer(merge_response.data)
        for lesson_id in merge_remove:
            index = find_index(lessons, lesson_id)
            if index is None:
                print(f"[warn] unknown lesson id in remove: {lesson_id}", file=sys.stderr)
                continue
            lessons.pop(index)
            removed.append(lesson_id)
        for merge in merges:
            known = [
                lesson_id
                for lesson_id in merge["ids"]
                if find_index(lessons, lesson_id) is not None
            ]
            if len(known) < 2:
                print(
                    f"[warn] skipping a merge with fewer than 2 known ids: {merge['ids']}",
                    file=sys.stderr,
                )
                continue
            merge_removed, new_id = merge_lessons(lessons, waiting, known, merge["text"])
            removed.extend(merge_removed)
            added.append(new_id)
    if len(lessons) > limit:
        room = MAX_WAITING - len(waiting)
        move_count = min(len(lessons) - limit, max(0, room))
        if move_count > 0:
            overflow = sorted(lessons, key=lesson_id_number, reverse=True)[:move_count]
            overflow_ids = {lesson.get("id") for lesson in overflow}
            waiting.extend(overflow)
            waiting.sort(key=lesson_id_number)
            lessons[:] = [lesson for lesson in lessons if lesson.get("id") not in overflow_ids]
    promote_waiting(lessons, waiting, limit)
    present = {lesson.get("id") for lesson in lessons}
    added = [lesson_id for lesson_id in added if lesson_id in present]
    document = build_document(lessons, waiting)
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
        "waiting": len(waiting),
        "added": added,
        "removed": removed,
        "model": response.model,
    }
    summary.update(summary_fields(started, llm_cost))
    return summary


def print_learn(summary):
    if summary["status"] == "skipped":
        print(f"skipped: {summary['reason']}")
        return
    if summary["status"] == "waiting_full":
        print(f"waiting_full: {summary['reason']}")
        return
    for lesson_id in summary["added"]:
        print(f"added {lesson_id}")
    for lesson_id in summary["removed"]:
        print(f"removed {lesson_id}")
    print(f"notebook: {summary['count']}/{summary['limit']} lessons")
    if summary.get("waiting"):
        print(f"waiting: {summary['waiting']} lessons (the notebook is full)")


def print_human(changed, count, limit):
    for lesson_id in changed["added"]:
        print(f"added {lesson_id}")
    for lesson_id in changed["removed"]:
        print(f"removed {lesson_id}")
    if changed["merged"]:
        print(f"merged {','.join(changed['merged'])} -> {changed['added'][-1]}")
    if changed.get("promoted"):
        print(f"promoted {','.join(changed['promoted'])}")
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
        waiting = read_waiting(path)
        changed = apply_command(args, lessons, waiting)
        if len(lessons) > args.limit:
            raise NotebookError(
                f"notebook is full ({args.limit} lessons): merge overlapping lessons "
                "or remove stale ones first, then retry"
            )
        if not args.list:
            changed["promoted"] = promote_waiting(lessons, waiting, args.limit)
            document = build_document(lessons, waiting)
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
            "waiting": len(waiting),
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
