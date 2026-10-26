#!/usr/bin/env python3
"""Remove shots from a finished edit or change a shot's length, and render it again as a new version.

Takes the edit of one version (the latest with a video by default), removes the
shots named by their number on the edit timeline (1 is the first shot) or changes
a shot's length by a signed number of seconds, renders the preview again from the
proxies and keeps the result as the next version. Nothing else changes: no model
call, the order and cuts of the other shots stay.

Examples:
  python scripts/edit_drop.py --workspace <workspaces>/<project> --drop 7
  python scripts/edit_drop.py --workspace <workspaces>/<project> --drop 2 --drop 7 --version v002 --json
  python scripts/edit_drop.py --workspace <workspaces>/<project> --drop 7 --dry-run
  python scripts/edit_drop.py --workspace <workspaces>/<project> --trim 3:+1
  python scripts/edit_drop.py --workspace <workspaces>/<project> --trim 3:-1 --version v002 --json

Exit codes: 0 done, 2 usage or input error (nothing written), 3 the render failed.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import versions  # noqa: E402
from common.constants import MIN_SHOT_LENGTH_S  # noqa: E402
from common.errors import report_error  # noqa: E402
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE  # noqa: E402
from common.summary import summary_fields  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
PREVIEW_NAME = "recap_720p.mp4"


class DropError(Exception):
    """An input problem: nothing was written."""


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Remove shots from a finished edit or change a shot's length, and render it again as a new version.",
        epilog=__doc__.split("Examples:", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--workspace", required=True, help="project workspace with versions/")
    parser.add_argument("--drop", type=int, action="append",
                        help="shot number on the edit timeline to remove (1 = first); repeat for more")
    parser.add_argument("--trim", action="append", metavar="N:DELTA",
                        help="change shot N's length by DELTA seconds, e.g. 3:+1 or 3:-1; repeat for more")
    parser.add_argument("--version", help="version to start from, e.g. v002 (default: the latest with a video)")
    parser.add_argument("--dry-run", action="store_true", help="say what would change, write nothing")
    parser.add_argument("--json", action="store_true", help="print one JSON summary on stdout")
    return parser.parse_args(argv)


def parse_trim(value):
    """Parse a ``N:DELTA`` trim spec into a shot number and a signed change in seconds."""
    number, sep, delta = value.partition(":")
    if not sep or not number.isdigit() or not delta:
        raise DropError(f"bad --trim {value!r}: use N:DELTA, e.g. 3:+1 or 3:-1")
    try:
        change = float(delta)
    except ValueError:
        raise DropError(f"bad --trim {value!r}: DELTA must be a number, e.g. 3:+1 or 3:-1")
    if change == 0:
        raise DropError(f"bad --trim {value!r}: DELTA must not be zero")
    return int(number), change


def pick_base(workspace, wanted):
    found = versions.list_versions(workspace)
    if not found:
        raise DropError("this project has no versions yet")
    if wanted:
        for item in found:
            if item["name"] == wanted:
                return item
        raise DropError(f"version {wanted} not found")
    with_video = [item for item in found if any(name.endswith(".mp4") for name in item["files"])]
    if not with_video:
        raise DropError("no version with a video to start from")
    return with_video[-1]


def drop_shots(edit, numbers):
    shots = edit.get("video") or []
    bad = [n for n in numbers if n < 1 or n > len(shots)]
    if bad:
        raise DropError(f"shot {bad[0]} does not exist: the edit has {len(shots)} shots")
    if len(set(numbers)) >= len(shots):
        raise DropError("cannot remove every shot")
    removed = [shots[n - 1] for n in sorted(set(numbers))]
    keep = [shot for i, shot in enumerate(shots, 1) if i not in set(numbers)]
    new_edit = dict(edit)
    new_edit["video"] = keep
    return new_edit, removed


def describe(numbers, removed):
    bits = []
    for number, shot in zip(sorted(set(numbers)), removed):
        bits.append(f"{number} ({shot.get('clip')} {float(shot.get('in_s', 0)):.1f}-{float(shot.get('out_s', 0)):.1f} s)")
    return "removed shot " + ", ".join(bits)


def source_duration(edit, clip):
    """Return the source clip's duration in seconds, or None when it is unknown."""
    for source in edit.get("sources") or []:
        if source.get("file") == clip:
            value = source.get("duration_s")
            return float(value) if value is not None else None
    return None


def trim_shots(edit, specs):
    """Change the length of the named shots and return the new edit and the changes."""
    shots = edit.get("video") or []
    new_shots = [dict(shot) for shot in shots]
    trimmed = []
    for number, delta in specs:
        if number < 1 or number > len(shots):
            raise DropError(f"shot {number} does not exist: the edit has {len(shots)} shots")
        shot = new_shots[number - 1]
        if shot.get("kind") == "photo" or "photo" in shot:
            raise DropError(f"shot {number} is a photo and cannot be trimmed")
        in_s = float(shot.get("in_s", 0))
        out_s = float(shot.get("out_s", 0))
        if delta > 0:
            duration = source_duration(edit, shot.get("clip"))
            if duration is None:
                raise DropError(f"the source clip duration of shot {number} is unknown")
            if in_s <= 0 and out_s >= duration:
                raise DropError(f"shot {number} already uses the whole clip")
            new_out = min(out_s + delta, duration)
            new_in = max(0.0, in_s - (delta - (new_out - out_s)))
        else:
            floor = in_s + MIN_SHOT_LENGTH_S
            if out_s <= floor:
                raise DropError(f"shot {number} is already at its shortest")
            new_out = max(out_s - abs(delta), floor)
            new_in = in_s
        shot["in_s"] = round(new_in, 3)
        shot["out_s"] = round(new_out, 3)
        trimmed.append({"shot": number, "delta": float(delta), "in_s": shot["in_s"], "out_s": shot["out_s"]})
    new_edit = dict(edit)
    new_edit["video"] = new_shots
    return new_edit, trimmed


def describe_trim(trimmed):
    bits = []
    for item in trimmed:
        word = "longer" if item["delta"] > 0 else "shorter"
        bits.append(f"{item['shot']} ({abs(item['delta']):.1f} s {word}, now {item['in_s']:.1f}-{item['out_s']:.1f} s)")
    return "made shot " + ", ".join(bits)


def main(argv=None):
    args = parse_args(argv)
    started = time.monotonic()
    workspace = Path(args.workspace).expanduser()
    try:
        if bool(args.drop) == bool(args.trim):
            raise DropError("give either --drop or --trim, not both")
        if not workspace.is_dir():
            raise DropError(f"workspace not found: {workspace}")
        base = pick_base(workspace, args.version)
        edit = json.loads((base["path"] / versions.EDIT_NAME).read_text(encoding="utf-8"))
        if args.trim:
            new_edit, trimmed = trim_shots(edit, [parse_trim(value) for value in args.trim])
            removed = []
        else:
            new_edit, removed = drop_shots(edit, args.drop)
            trimmed = []
    except DropError as exc:
        return report_error(exc, EXIT_USAGE, args.json)
    except (OSError, ValueError) as exc:
        return report_error(f"cannot read the edit of the base version: {exc}", EXIT_USAGE, args.json)
    note = f"{describe_trim(trimmed) if args.trim else describe(args.drop, removed)} of {base['name']}"
    summary = {
        "tool": "scripts/edit_drop.py",
        "ok": True,
        "base": base["name"],
        "removed": [shot.get("id") for shot in removed],
        "trimmed": trimmed,
        "note": note,
        "shots_left": len(new_edit["video"]),
        "version": None,
        "dry_run": args.dry_run,
        **summary_fields(started),
    }
    if args.dry_run:
        print(json.dumps(summary) if args.json else f"would make a new version: {note}")
        return EXIT_OK
    temporary = workspace / (versions.EDIT_NAME + ".tmp")
    temporary.write_text(json.dumps(new_edit, indent=2) + "\n", encoding="utf-8")
    render = subprocess.run(
        [sys.executable, str(SCRIPTS / "render.py"), "--edit", str(temporary),
         "--out", str(workspace / PREVIEW_NAME), "--force", "--json"],
        capture_output=True, text=True,
    )
    if render.returncode != 0:
        temporary.unlink(missing_ok=True)
        tail = (render.stderr or render.stdout or "").strip().splitlines()[-1:] or ["no output"]
        return report_error(f"render failed: {tail[0]}", EXIT_RUNTIME, args.json, base=base["name"])
    os.replace(temporary, workspace / versions.EDIT_NAME)
    thumbs = subprocess.run(
        [sys.executable, str(SCRIPTS / "thumbs.py"), "--workspace", str(workspace),
         "--force", "--json"],
        capture_output=True, text=True,
    )
    if thumbs.returncode != 0:
        summary["warnings"] = ["the thumbnails could not be written"]
    meta = dict(base["meta"])
    for key in ("created_at", "files"):
        meta.pop(key, None)
    meta.update({"feedback": [], "note": note, "status": "done", "failed_at": None})
    meta["cost_usd"] = summary["llm_cost_usd"]
    directory = versions.snapshot(workspace, meta)
    summary["version"] = directory.name if directory else None
    print(json.dumps(summary) if args.json else f"new version {summary['version']}: {note}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
