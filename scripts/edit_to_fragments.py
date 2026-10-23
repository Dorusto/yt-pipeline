#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Convert an edit.json edit list into the fragments.yaml that the Kdenlive adapter reads.

The optional Kdenlive export is one adapter of the edit list: this tool reads
``edit.json`` (validated against ``schemas/edit.schema.json``) and writes a
``fragments.yaml`` (validated against ``schemas/fragments.schema.json``) in the
shape ``kdenlive_from_fragments.py`` already consumes. One fragment is written
per video slot, in timeline order, and the generator can then build the project
with a single extra command. No model is called and Kdenlive need not be
installed.

Timecode convention: ``kdenlive_from_fragments.py`` treats each fragment's
``out`` timecode as frame-inclusive (``dur_frames = out_frames - in_frames +
1``), while ``edit.json``'s ``out_s`` is exclusive. To keep the fragment length
equal to the slot length, this tool writes ``out`` as
``timecode(out_s - 1 / FPS)`` at the consumer's frame rate (``FPS = 25``),
clamped so it never lands before ``in`` and rounded to milliseconds.

Usage:
    python3 edit_to_fragments.py --edit edit.json --out fragments.yaml
    python3 edit_to_fragments.py --edit edit.json --out fragments.yaml \\
        --source-dir <source-folder> \\
        --kdenlive-out <workspace>/recap.kdenlive
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.moves import middle_speed_of
from common.schema import SchemaError, validate_or_raise

FPS = 25
LABEL_MAX = 60


class EditToFragmentsError(RuntimeError):
    """Raised when an edit list cannot be converted to fragments."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="edit_to_fragments.py",
        description=(
            "Convert an edit.json edit list into the fragments.yaml read by "
            "kdenlive_from_fragments.py. The edit list stays edit.json; this is "
            "an optional export adapter and needs no Kdenlive install."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/edit_to_fragments.py --edit edit.json --out fragments.yaml\n"
            "  python scripts/edit_to_fragments.py --edit edit.json --out fragments.yaml \\\n"
            "      --source-dir <source-folder> \\\n"
            "      --kdenlive-out <workspace>/recap.kdenlive\n"
            "  python scripts/edit_to_fragments.py --edit edit.json --out fragments.yaml --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--edit", required=True, help="edit.json to convert")
    parser.add_argument("--out", required=True, help="fragments.yaml to write")
    parser.add_argument(
        "--source-dir",
        default=None,
        help="folder the fragments point at (default: directory of the first sources[] path)",
    )
    parser.add_argument(
        "--kdenlive-out",
        default=None,
        help="output: field of the YAML (default: --out with a .kdenlive suffix)",
    )
    parser.add_argument("--force", action="store_true", help="rewrite even when the output is up to date")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def sec_to_tc(seconds):
    """Format seconds as HH:MM:SS.mmm, clamping negatives to zero."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def short_label(slot):
    """Build the human label from the slot id, its score and the first words of its reason."""
    prefix = f"{slot['id']} · {slot['score']:.2f} · "
    remaining = LABEL_MAX - len(prefix)
    reason = " ".join(slot["reason"].split())
    if remaining <= 0:
        return prefix[:LABEL_MAX]
    if len(reason) > remaining:
        reason = reason[: remaining - 1].rstrip() + "…"
    return prefix + reason


def slot_to_fragment(slot):
    """Convert one video slot to the fragment dict the consumer reads."""
    in_tc = sec_to_tc(slot["in_s"])
    inclusive_out = max(slot["in_s"], slot["out_s"] - 1.0 / FPS)
    return {
        "file": slot["clip"],
        "in": in_tc,
        "out": sec_to_tc(inclusive_out),
        "label": short_label(slot),
        "score": slot["score"],
        "reason": slot["reason"],
        "slot": slot["id"],
    }


def default_source_dir(document):
    """Return the directory of the first sources[] path, falling back to the current directory."""
    parent = Path(document["sources"][0]["path"]).expanduser().parent
    return str(parent) if str(parent) else "."


def atomic_write_text(path, text):
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def run(args, started):
    """Convert the edit list and return the run summary."""
    edit_path = Path(args.edit).expanduser()
    out_path = Path(args.out).expanduser()
    if not edit_path.is_file():
        raise EditToFragmentsError(f"edit file not found: {edit_path}")
    try:
        document = json.loads(edit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EditToFragmentsError(f"could not read edit file: {exc}") from exc
    validate_or_raise(document, "edit")

    source_dir = (
        str(Path(args.source_dir).expanduser())
        if args.source_dir
        else default_source_dir(document)
    )
    kdenlive_out = (
        str(Path(args.kdenlive_out).expanduser())
        if args.kdenlive_out
        else str(out_path.with_suffix(".kdenlive"))
    )
    slots = document["video"]
    fragments = [slot_to_fragment(slot) for slot in slots]
    total_duration_s = round(sum(slot["out_s"] - slot["in_s"] for slot in slots), 3)
    ramped = [slot["id"] for slot in slots if middle_speed_of(slot) > 1.0]

    summary = {
        "status": "written",
        "out": str(out_path),
        "fragments": len(fragments),
        "source_dir": source_dir,
        "kdenlive_out": kdenlive_out,
        "total_duration_s": total_duration_s,
        "warnings": (
            [
                "Kdenlive cannot express the middle speed ramp of "
                + ", ".join(ramped)
                + ": the fragments keep real time, so the project is longer than the render"
            ]
            if ramped
            else []
        ),
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }

    if out_path.exists() and not args.force and out_path.stat().st_mtime > edit_path.stat().st_mtime:
        summary["status"] = "skipped"
        return summary

    output = {"source_dir": source_dir, "output": kdenlive_out, "fragments": fragments}
    validate_or_raise(output, "fragments")
    text = yaml.safe_dump(output, sort_keys=False, allow_unicode=True, default_flow_style=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out_path, text)
    return summary


def print_human(summary):
    if summary["status"] == "skipped":
        print(f"skipped {summary['out']} (up to date)")
    else:
        print(f"wrote {summary['out']}")
    print(
        f"{summary['fragments']} fragments, {summary['total_duration_s']:.1f}s, "
        f"source_dir {summary['source_dir']}, output {summary['kdenlive_out']}"
    )
    for warning in summary["warnings"]:
        print(f"warning: {warning}")


def main(argv=None):
    args = build_parser().parse_args(argv)
    started = time.monotonic()
    try:
        summary = run(args, started)
    except (EditToFragmentsError, SchemaError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_USAGE
    except OSError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    if args.json:
        print(json.dumps(summary))
    else:
        print_human(summary)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
