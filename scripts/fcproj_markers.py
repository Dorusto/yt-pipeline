#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read a FilmCraft project's timeline markers into markers.json and todo.md.

The tool reads the sequence-level markers of a ``.fcproj`` project (read-only)
and writes two files next to it: ``markers.json`` in the shape
``graphics_plan.py --markers`` already consumes, and ``todo.md`` holding only the
Rose markers, the redo list outside the editor. Clip-level markers are counted
and reported, never mapped. No model is called; ``llm_cost_usd`` is always 0.0.

Usage:
    python3 scripts/fcproj_markers.py PROJECT.fcproj [--sequence NAME]
        [--out markers.json] [--todo todo.md] [--json]
"""

import argparse
import json
import sys
import time
from pathlib import Path

from common.constants import TICKS_PER_SECOND
from common.filmcraft import parse_frame_rate
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME
from common.errors import report_error
ROSE = "rose"
SCHEMA_VERSION = "1"


class MarkersError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="fcproj_markers.py",
        description=(
            "Read a FilmCraft project's sequence markers into markers.json "
            "(the graphics_plan.py contract) and the Rose markers into todo.md."
        ),
        epilog=(
            "examples:\n"
            "  python3 scripts/fcproj_markers.py project.fcproj --json\n"
            "  python3 scripts/fcproj_markers.py project.fcproj --sequence Main "
            "--out markers.json --todo todo.md\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project", help="FilmCraft project file (JSON, read-only)")
    parser.add_argument("--sequence", default=None, help="sequence name (required when the project has more than one)")
    parser.add_argument("--out", default=None, help="markers JSON path (default: <project dir>/markers.json)")
    parser.add_argument("--todo", default=None, help="todo markdown path (default: <project dir>/todo.md)")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def load_json(path):
    """Read a UTF-8 JSON file, raising a named error when it is missing or unreadable."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise MarkersError(f"cannot read '{path}': file not found")
    except json.JSONDecodeError as exc:
        raise MarkersError(f"cannot read '{path}': not valid JSON ({exc})")
    except OSError as exc:
        raise MarkersError(f"cannot read '{path}': {exc}")


def find_sequences(items):
    """Return ``{name: sequence item}`` for every named sequence item."""
    sequences = {}
    for value in items.values():
        kind = value.get("kind") or {}
        if "Sequence" in kind and value.get("name"):
            sequences[value["name"]] = value
    return sequences


def select_sequence(items, requested):
    """Return ``(name, sequence item)`` for the requested or only sequence."""
    sequences = find_sequences(items)
    if not sequences:
        raise MarkersError("no sequence found in the project")
    if requested:
        if requested not in sequences:
            found = ", ".join(sorted(sequences)) or "none"
            raise MarkersError(f"unknown sequence '{requested}' (sequences found: {found})")
        return requested, sequences[requested]
    if len(sequences) > 1:
        found = ", ".join(sorted(sequences))
        raise MarkersError(f"the project has more than one sequence, pass --sequence (found: {found})")
    name = next(iter(sequences))
    return name, sequences[name]


def sequence_block(sequence_item):
    return (sequence_item.get("kind") or {}).get("Sequence") or {}


def frame_rate(sequence_item):
    """Return the sequence frame rate as a float, defaulting to 25 when unset."""
    return parse_frame_rate(sequence_block(sequence_item).get("settings") or {})


def format_timecode(seconds, fps):
    """Format seconds as ``HH:MM:SS:FF`` at ``fps`` frames per second."""
    total = max(0.0, float(seconds))
    whole = int(total)
    frame = int((total - whole) * fps)
    frame_count = max(1, int(round(fps)))
    if frame >= frame_count:
        frame = frame_count - 1
    hours = whole // 3600
    minutes = (whole % 3600) // 60
    secs = whole % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frame:02d}"


def parse_markers(sequence_item, fps):
    """Return the sequence markers as dicts sorted by time, or raise on a bad shape."""
    raw = sequence_block(sequence_item).get("markers") or []
    markers = []
    for index, marker in enumerate(raw):
        if not isinstance(marker, dict):
            raise MarkersError(f"marker {index} is not an object")
        start = marker.get("start")
        if isinstance(start, bool) or not isinstance(start, (int, float)):
            raise MarkersError(f"marker {index} has no numeric start")
        seconds = float(start) / TICKS_PER_SECOND
        markers.append(
            {
                "time_s": round(seconds, 3),
                "timecode": format_timecode(seconds, fps),
                "name": marker.get("name") or "",
                "comment": marker.get("comment") or "",
                "colour": marker.get("color") or "",
                "kind": marker.get("kind") or "Comment",
            }
        )
    markers.sort(key=lambda item: item["time_s"])
    return markers


def count_clip_markers(sequence_item):
    """Count the markers on every timeline clip of the sequence's tracks."""
    block = sequence_block(sequence_item)
    total = 0
    for track_key in ("video_tracks", "audio_tracks"):
        for track in block.get(track_key) or []:
            for clip in track.get("items") or []:
                total += len(clip.get("markers") or [])
    return total


def is_rose(marker):
    return str(marker.get("colour", "")).strip().lower() == ROSE


def build_todo(markers):
    """Render the Rose markers as a markdown checklist, or a one-line note when there are none."""
    rose = [marker for marker in markers if is_rose(marker)]
    if not rose:
        return "No Rose markers in this project.\n"
    lines = []
    for marker in rose:
        text = marker["name"]
        if marker["comment"]:
            text = f"{text}: {marker['comment']}" if text else marker["comment"]
        lines.append(f"- [ ] {marker['timecode']} ({marker['time_s']:.1f} s) \u2014 {text}")
    return "\n".join(lines) + "\n"


def run(project_path, sequence_name=None, out_path=None, todo_path=None):
    """Read the project markers, write markers.json and todo.md, and return the summary."""
    started = time.monotonic()
    project = Path(project_path).expanduser()
    data = load_json(project)
    project_block = data.get("project")
    if not isinstance(project_block, dict) or not isinstance(project_block.get("items"), dict):
        raise MarkersError(f"'{project}' is not a FilmCraft project (no project.items)")
    name, sequence_item = select_sequence(project_block["items"], sequence_name)
    fps = frame_rate(sequence_item)
    markers = parse_markers(sequence_item, fps)
    ignored = count_clip_markers(sequence_item)

    out = Path(out_path).expanduser() if out_path else project.parent / "markers.json"
    todo = Path(todo_path).expanduser() if todo_path else project.parent / "todo.md"
    document = {
        "schema_version": SCHEMA_VERSION,
        "source": str(project),
        "sequence": name,
        "markers": markers,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    todo.parent.mkdir(parents=True, exist_ok=True)
    todo.write_text(build_todo(markers), encoding="utf-8")

    return {
        "output": str(out),
        "todo": str(todo),
        "sequence": name,
        "markers": len(markers),
        "rose": sum(1 for marker in markers if is_rose(marker)),
        "clip_markers_ignored": ignored,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = run(args.project, args.sequence, args.out, args.todo)
    except MarkersError as exc:
        return report_error(exc, EXIT_USAGE, args.json)

    if args.json:
        print(json.dumps(summary))
    else:
        print(f"written: {summary['output']} ({summary['markers']} markers, {summary['rose']} Rose)")
        print(f"written: {summary['todo']}")
        if summary["clip_markers_ignored"]:
            print(f"warning: ignored {summary['clip_markers_ignored']} clip-level markers")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
