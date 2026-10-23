#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Watch an inbox folder and turn each day of new clips into a recap project.

Usage:
    python scripts/inbox.py --inbox <inbox> --projects <projects>
    python scripts/inbox.py --inbox <inbox> --once --json

``inbox.py`` is the phone flow front door. A sync tool drops clips into the
inbox (Syncthing target), the tool groups them by day, waits until the inbox
has been quiet for a few minutes, then runs ``scripts/recap.py`` as a
subprocess with the folder's ``brief.txt`` or a default brief. One workspace
per day is created under ``--projects``. The inbox is read-only: nothing is
written, moved or deleted there, and everything this tool writes lives under
``--projects``. It imports no other tool and calls no model itself.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from common.config import ConfigError, no_projects_message, projects_path
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME
from common.errors import report_error

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".insv"}
DEFAULT_BRIEF = (
    "about 90 seconds, chronological, calm start, more energy towards the end, "
    "ambient sound only"
)
STATE_VERSION = 1
STATE_NAME = "inbox_state.json"
BRIEF_NAME = "brief.txt"
RECAP_BRIEF_NAME = "inbox_brief.txt"
FARM_NAME = "source"
REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = "scripts/inbox.py"

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_DASH_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
DATE_COMPACT_RE = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")
DATE_UNDERSCORE_RE = re.compile(r"(?<!\d)(\d{4})_(\d{2})_(\d{2})(?!\d)")
GROUP_JSON_FIELDS = (
    "day",
    "files",
    "source",
    "workspace",
    "brief_source",
    "status",
    "wall_time_s",
    "llm_cost_usd",
)


class InboxError(RuntimeError):
    """Raised when the inbox watcher cannot start or complete a run."""


class Settings:
    """Resolved options shared by every cycle and group."""

    def __init__(self, quiet_minutes, default_brief, python, repo_root):
        self.quiet_minutes = quiet_minutes
        self.default_brief = default_brief
        self.python = python
        self.repo_root = repo_root


def build_parser():
    parser = argparse.ArgumentParser(
        prog="inbox.py",
        usage=(
            "inbox.py [-h] --inbox INBOX [--projects PROJECTS] [--quiet-minutes N]"
            " [--poll-seconds N] [--default-brief TEXT] [--preview-base URL]"
            " [--notify] [--budget-usd X] [--force] [--once] [--json]"
        ),
        description=(
            "Group new clips in an inbox folder into one recap project per day"
            " after the inbox goes quiet."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/inbox.py --inbox <inbox> --projects <projects>\n"
            "  python scripts/inbox.py --inbox <inbox> --once --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--inbox", required=True, help="watched folder (read-only)")
    parser.add_argument(
        "--projects",
        default=None,
        help=(
            "folder holding one workspace per day; pass it here, set the "
            "YT_PIPELINE_PROJECTS environment variable, or set projects.path in the config"
        ),
    )
    parser.add_argument(
        "--quiet-minutes",
        type=float,
        default=5.0,
        help="process a day only after the inbox has been quiet this long (default: 5)",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=60.0,
        help="watch-mode poll interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--default-brief",
        default=None,
        help="brief used when no brief.txt applies",
    )
    parser.add_argument(
        "--preview-base",
        default=None,
        help="base URL of the serve.py server, forwarded to recap.py",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="forward --notify so recap.py posts the preview link to ntfy",
    )
    parser.add_argument(
        "--budget-usd",
        type=float,
        default=None,
        help="LLM budget forwarded to recap.py",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="forward --force and re-run days that have nothing new",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="scan once, run every ready group and exit",
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def is_within(path, directory):
    path = Path(path).expanduser().resolve()
    directory = Path(directory).expanduser().resolve()
    return path == directory or directory in path.parents


def parse_date_parts(year, month, day):
    try:
        return datetime(int(year), int(month), int(day)).date().isoformat()
    except ValueError:
        return None


def scan_videos(inbox):
    """Return the video files under ``inbox``, recursively, hidden paths skipped."""
    inbox = Path(inbox)
    found = []
    for current, dirs, files in os.walk(inbox):
        dirs[:] = sorted(name for name in dirs if not name.startswith("."))
        for name in sorted(files):
            if name.startswith("."):
                continue
            path = Path(current) / name
            if path.suffix.lower() in VIDEO_EXTENSIONS:
                found.append(path)
    return found


def parent_date(path, inbox):
    """Return the ISO date of a dated parent folder inside the inbox, or None."""
    inbox = Path(inbox)
    current = path.parent
    while current != inbox and inbox in current.parents:
        name = current.name
        if DATE_DIR_RE.match(name):
            parsed = parse_date_parts(*name.split("-"))
            if parsed:
                return parsed
        current = current.parent
    return None


def date_from_filename(name):
    """Return the ISO date found in a filename, or None."""
    for pattern in (DATE_DASH_RE, DATE_COMPACT_RE, DATE_UNDERSCORE_RE):
        match = pattern.search(name)
        if match:
            parsed = parse_date_parts(*match.groups())
            if parsed:
                return parsed
    return None


def date_from_mtime(path):
    return datetime.fromtimestamp(Path(path).stat().st_mtime).date().isoformat()


def clip_day(path, inbox):
    """Return the project day of a clip, parent folder then filename then mtime."""
    dated = parent_date(path, inbox)
    if dated:
        return dated
    named = date_from_filename(Path(path).name)
    if named:
        return named
    return date_from_mtime(path)


def build_groups(videos, inbox):
    """Group clips by project day, returning {day: [clip, ...]}."""
    groups = {}
    for clip in videos:
        day = clip_day(clip, inbox)
        groups.setdefault(day, []).append(clip)
    return groups


def plan_source(clips):
    """Return (mode, directory); mode is "direct" or "farm".

    A day whose clips all live in one directory that holds no other video file
    is passed to recap.py directly. Every other case needs a symlink farm.
    """
    parents = {clip.parent for clip in clips}
    if len(parents) == 1:
        parent = next(iter(parents))
        try:
            siblings = {
                path
                for path in parent.iterdir()
                if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
            }
        except OSError:
            siblings = set()
        if siblings <= set(clips):
            return "direct", parent
    return "farm", None


def unique_link_name(filename, used):
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    candidate = filename
    index = 2
    while candidate in used:
        candidate = f"{stem}_{index}{suffix}"
        index += 1
    used.add(candidate)
    return candidate


def build_symlink_farm(workspace, clips):
    """Rebuild ``<workspace>/source/`` with one symlink per clip and return it."""
    farm = Path(workspace) / FARM_NAME
    farm.mkdir(parents=True, exist_ok=True)
    for entry in farm.iterdir():
        if entry.is_symlink() or entry.is_file():
            try:
                entry.unlink()
            except OSError:
                continue
    used = set()
    for clip in clips:
        link = farm / unique_link_name(clip.name, used)
        target = Path(clip).resolve()
        if link.exists() or link.is_symlink():
            link.unlink()
        os.symlink(target, link)
    return farm


def read_brief(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[warn] could not read brief {path}: {exc}", file=sys.stderr)
        return None


def dated_parents(clips, inbox):
    """Return the distinct dated parent folders of the clips inside the inbox."""
    inbox = Path(inbox)
    found = set()
    for clip in clips:
        current = Path(clip).parent
        while current != inbox and inbox in current.parents:
            if DATE_DIR_RE.match(current.name):
                found.add(current)
            current = current.parent
    return found


def resolve_brief(inbox, folder_dir, clips, default_brief):
    """Return (brief_text, brief_source) with folder then inbox then default priority.

    The folder brief is the direct source directory when the group runs direct,
    or the group's single dated parent folder when the clips are scattered.
    """
    if folder_dir is not None:
        candidate = Path(folder_dir) / BRIEF_NAME
        if candidate.is_file():
            text = read_brief(candidate)
            if text is not None:
                return text, "folder"
    parents = dated_parents(clips, inbox)
    if len(parents) == 1:
        candidate = next(iter(parents)) / BRIEF_NAME
        if candidate.is_file():
            text = read_brief(candidate)
            if text is not None:
                return text, "folder"
    candidate = Path(inbox) / BRIEF_NAME
    if candidate.is_file():
        text = read_brief(candidate)
        if text is not None:
            return text, "inbox"
    return default_brief, "default"


def brief_sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def newest_inbox_mtime(inbox):
    """Return the newest mtime among videos and brief.txt files under the inbox."""
    newest = 0.0
    inbox = Path(inbox)
    for current, dirs, files in os.walk(inbox):
        dirs[:] = sorted(name for name in dirs if not name.startswith("."))
        for name in files:
            if name.startswith("."):
                continue
            if name != BRIEF_NAME and Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            try:
                newest = max(newest, (Path(current) / name).stat().st_mtime)
            except OSError:
                continue
    return newest


def inbox_is_quiet(inbox, quiet_minutes, now):
    if quiet_minutes <= 0:
        return True
    return (now - newest_inbox_mtime(inbox)) >= quiet_minutes * 60.0


def load_state(path):
    """Return (state, corrupt); a missing state starts empty, a bad one warns."""
    empty = {"version": STATE_VERSION, "files": {}, "runs": {}}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty, False
    except (OSError, ValueError):
        return empty, True
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("files"), dict)
        or not isinstance(data.get("runs"), dict)
    ):
        return empty, True
    data["version"] = STATE_VERSION
    return data, False


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def is_processed(state, clip):
    entry = state["files"].get(str(clip))
    if not isinstance(entry, dict) or entry.get("processed_at") is None:
        return False
    try:
        stat = Path(clip).stat()
    except OSError:
        return False
    return entry.get("size") == stat.st_size and entry.get("mtime") == stat.st_mtime


def write_text_atomic(path, text):
    """Write ``text`` atomically, only when its content differs."""
    path = Path(path)
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == text:
                return path
        except OSError:
            pass
    temporary = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
    return path


def build_recap_command(source, workspace, brief_path, args, python):
    """Return the recap.py argv with exactly the forwarded flags."""
    command = [
        python,
        "scripts/recap.py",
        "--source",
        str(source),
        "--workspace",
        str(workspace),
        "--brief-file",
        str(brief_path),
    ]
    if args.preview_base is not None:
        command += ["--preview-base", args.preview_base]
    if args.notify:
        command.append("--notify")
    if args.budget_usd is not None:
        command += ["--budget-usd", str(args.budget_usd)]
    if args.force:
        command.append("--force")
    command.append("--json")
    return command


def parse_child_json(stdout):
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


def run_recap(source, workspace, brief_path, args, python, repo_root):
    """Run recap.py as a subprocess and return its parsed summary."""
    command = build_recap_command(source, workspace, brief_path, args, python)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
    except OSError as exc:
        return {
            "status": "failed",
            "wall_time_s": 0.0,
            "llm_cost_usd": 0.0,
            "output": None,
            "returncode": 1,
            "stderr": str(exc),
        }
    data = parse_child_json(result.stdout)
    wall_time_s = 0.0
    llm_cost_usd = 0.0
    output = None
    if isinstance(data, dict):
        value = data.get("llm_cost_usd")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            llm_cost_usd = float(value)
        value = data.get("wall_time_s")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            wall_time_s = float(value)
        outputs = data.get("outputs")
        if isinstance(outputs, dict):
            preview = outputs.get("preview")
            if isinstance(preview, str) and preview:
                output = os.path.basename(preview)
    return {
        "status": "done" if result.returncode == 0 else "failed",
        "wall_time_s": wall_time_s,
        "llm_cost_usd": llm_cost_usd,
        "output": output,
        "returncode": result.returncode,
        "stderr": result.stderr or "",
    }


def format_group_line(record):
    day = record["day"]
    status = record["status"]
    if status == "done":
        output = record.get("output") or "recap_720p.mp4"
        return (
            f"[group] {day}  {'run':<8} {len(record['files'])} clips  "
            f"{output}  ${record['llm_cost_usd']:.4f}  {record['wall_time_s']:.1f}s"
        )
    if status == "skipped":
        return f"[group] {day}  skipped (nothing new)"
    if status == "waiting":
        return f"[group] {day}  waiting (inbox not quiet)"
    return f"[group] {day}  failed (exit {record.get('returncode', '?')})"


def print_group_line(record, json_mode):
    stream = sys.stderr if json_mode else sys.stdout
    print(format_group_line(record), file=stream)


def should_print_group(record, previous):
    """Return True when a watch cycle should print this group's progress line."""
    if record["status"] in ("done", "failed"):
        return True
    return previous.get(record["day"]) != record["status"]


def print_failure(day, result):
    print(
        f"[error] {day}: recap failed (exit {result.get('returncode', '?')})",
        file=sys.stderr,
    )
    for line in (result.get("stderr") or "").strip().splitlines()[-20:]:
        print(line, file=sys.stderr)


def process_group(day, clips, inbox, projects, state, settings, args, quiet):
    """Decide on one day group, run its recap when ready and update state."""
    workspace = Path(projects) / day
    mode, direct_dir = plan_source(clips)
    brief_text, brief_source = resolve_brief(
        inbox, direct_dir, clips, settings.default_brief
    )
    brief_digest = brief_sha256(brief_text)
    record = {
        "day": day,
        "files": [clip.name for clip in clips],
        "source": str(direct_dir if mode == "direct" else workspace / FARM_NAME),
        "workspace": str(workspace),
        "brief_source": brief_source,
        "status": "waiting",
        "wall_time_s": 0.0,
        "llm_cost_usd": 0.0,
    }
    if not quiet:
        return record
    has_new = any(not is_processed(state, clip) for clip in clips)
    changed_brief = state["runs"].get(day, {}).get("brief_sha256") != brief_digest
    if not (has_new or changed_brief or args.force):
        record["status"] = "skipped"
        return record
    workspace.mkdir(parents=True, exist_ok=True)
    if mode == "direct":
        source = direct_dir
    else:
        source = build_symlink_farm(workspace, clips)
    record["source"] = str(source)
    brief_path = write_text_atomic(workspace / RECAP_BRIEF_NAME, brief_text)
    result = run_recap(source, workspace, brief_path, args, settings.python, settings.repo_root)
    record["wall_time_s"] = result["wall_time_s"]
    record["llm_cost_usd"] = result["llm_cost_usd"]
    record["output"] = result["output"]
    record["status"] = result["status"]
    record["returncode"] = result["returncode"]
    stamp = time.time()
    state["runs"][day] = {
        "status": result["status"],
        "brief_sha256": brief_digest,
        "wall_time_s": result["wall_time_s"],
        "llm_cost_usd": result["llm_cost_usd"],
        "last_run": stamp,
    }
    if result["status"] == "done":
        for clip in clips:
            try:
                stat = Path(clip).stat()
            except OSError:
                continue
            state["files"][str(clip)] = {
                "day": day,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "processed_at": stamp,
            }
    else:
        print_failure(day, result)
    return record


def process_cycle(
    inbox, projects, state_path, state, settings, args, now, previous=None, print_all=True
):
    """Scan once, process every day group and return the group records."""
    videos = scan_videos(inbox)
    groups = build_groups(videos, inbox)
    quiet = inbox_is_quiet(inbox, settings.quiet_minutes, now)
    previous = previous or {}
    records = []
    for day in sorted(groups):
        clips = sorted(groups[day], key=lambda path: str(path))
        record = process_group(day, clips, inbox, projects, state, settings, args, quiet)
        records.append(record)
        if print_all or should_print_group(record, previous):
            print_group_line(record, args.json)
    save_state(state_path, state)
    return records


def build_summary(inbox, projects, records, total_cost, wall_time_s):
    """Return the flat JSON summary object expected by --json."""
    groups = [
        {key: record[key] for key in GROUP_JSON_FIELDS} for record in records
    ]
    failed = any(group["status"] == "failed" for group in groups)
    return {
        "tool": TOOL,
        "status": "failed" if failed else "done",
        "inbox": str(inbox),
        "projects": str(projects),
        "groups": groups,
        "wall_time_s": round(wall_time_s, 3),
        "llm_cost_usd": round(total_cost, 8),
    }


def print_human(summary):
    print(
        f"inbox: {summary['status']} in {summary['wall_time_s']:.1f}s,"
        f" llm cost ${summary['llm_cost_usd']:.4f}"
    )
    for group in summary["groups"]:
        status = group["status"]
        if status == "done":
            count = len(group["files"])
            print(f"  {group['day']}  {count} clips  {group['workspace']}")
        else:
            print(f"  {group['day']}  {status}")


def emit_summary(summary, json_mode):
    if json_mode:
        print(json.dumps(summary))
    else:
        print_human(summary)


def resolve_paths(args):
    inbox = Path(args.inbox).expanduser().resolve()
    if not inbox.exists():
        raise InboxError(f"inbox not found: {inbox}")
    if not inbox.is_dir():
        raise InboxError(f"inbox is not a directory: {inbox}")
    resolved = projects_path(args.projects)
    if resolved is None:
        raise InboxError(no_projects_message("--projects"))
    projects = resolved.resolve()
    if is_within(projects, inbox):
        raise InboxError(f"projects folder must not be inside the inbox: {projects}")
    projects.mkdir(parents=True, exist_ok=True)
    return inbox, projects, projects / STATE_NAME


def main(argv=None):
    args = build_parser().parse_args(argv)
    started = time.monotonic()
    try:
        inbox, projects, state_path = resolve_paths(args)
    except (InboxError, ConfigError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)
    default_brief = args.default_brief if args.default_brief is not None else DEFAULT_BRIEF
    settings = Settings(
        quiet_minutes=args.quiet_minutes,
        default_brief=default_brief,
        python=sys.executable,
        repo_root=REPO_ROOT,
    )
    state, corrupt = load_state(state_path)
    if corrupt:
        print("[warn] state file unreadable, starting fresh", file=sys.stderr)

    if args.once:
        records = process_cycle(
            inbox, projects, state_path, state, settings, args, time.time()
        )
        total_cost = sum(record["llm_cost_usd"] for record in records)
        summary = build_summary(
            inbox, projects, records, total_cost, time.monotonic() - started
        )
        emit_summary(summary, args.json)
        return EXIT_RUNTIME if summary["status"] == "failed" else EXIT_OK

    latest = {}
    total_cost = 0.0
    try:
        while True:
            previous = {day: record["status"] for day, record in latest.items()}
            records = process_cycle(
                inbox,
                projects,
                state_path,
                state,
                settings,
                args,
                time.time(),
                previous=previous,
                print_all=False,
            )
            for record in records:
                latest[record["day"]] = record
                total_cost += record["llm_cost_usd"]
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        stream = sys.stderr if args.json else sys.stdout
        print("[inbox] stopped", file=stream)
    summary = build_summary(
        inbox,
        projects,
        [latest[day] for day in sorted(latest)],
        total_cost,
        time.monotonic() - started,
    )
    emit_summary(summary, args.json)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
