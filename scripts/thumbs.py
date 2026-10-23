#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Write the poster and per-shot thumbnails of a recap workspace.

Usage:
    python scripts/thumbs.py --workspace <workspace>
    python scripts/thumbs.py --workspace <workspace> --width 320 --json

``--workspace`` holds ``edit.json`` and the recap. The tool writes
``<workspace>/thumbs/poster.jpg`` (the frame at 30 % of the recap) and
``<workspace>/thumbs/shot-N.jpg`` (the middle frame of each timeline slot),
each scaled to ``--width`` pixels wide. Existing thumbnails newer than the
recap are skipped unless ``--force`` is given. The recap is read-only.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from common.edit_timeline import edit_summary
from common.errors import report_error
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.ffmpeg_tools import probe_duration
from common.frames import FramesError, extract_frame
from common.summary import summary_fields
from common.versions import POSTER_NAME, THUMBS_DIR, thumbs_are_fresh

TOOL = "thumbs.py"
DEFAULT_WIDTH = 320
RECAP_NAMES = ("recap_720p.mp4", "recap_720p_9x16.mp4")
POSTER_FRACTION = 0.30


class ThumbsError(RuntimeError):
    """Raised when the thumbnails cannot be written (usage or input error)."""


class ThumbsRuntimeError(ThumbsError):
    """Raised when ffprobe or ffmpeg fails after the run started."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="thumbs.py",
        description="Write the poster and per-shot thumbnails of a recap workspace.",
        epilog=(
            "examples:\n"
            "  python scripts/thumbs.py --workspace <workspace>\n"
            "  python scripts/thumbs.py --workspace <workspace> --width 320 --json\n"
            "  python scripts/thumbs.py --workspace <workspace> --force\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--workspace", required=True, help="workspace holding edit.json and the recap")
    parser.add_argument("--recap", help="the recap video (default: recap_720p.mp4, else recap_720p_9x16.mp4)")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH,
                        help=f"thumbnail width in pixels (default: {DEFAULT_WIDTH})")
    parser.add_argument("--force", action="store_true", help="rewrite even when the thumbs are newer than the recap")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def resolve_recap(workspace, recap):
    """The recap to read: the given file, else the wide preview, else the vertical one."""
    if recap:
        path = Path(recap)
        return path if path.is_absolute() else workspace / path
    for name in RECAP_NAMES:
        candidate = workspace / name
        if candidate.is_file():
            return candidate
    return None


def load_summary(workspace):
    """The timeline summary of ``workspace/edit.json``, or raise a usage error."""
    path = workspace / "edit.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ThumbsError(f"could not read {path}: {exc}") from exc
    summary = edit_summary(document)
    if summary is None:
        raise ThumbsError(f"{path} has no usable video list")
    return summary


def build_thumbs(workspace, recap, width, force):
    """Write the poster and shot thumbnails; return the summary dict."""
    started = time.monotonic()
    workspace = Path(workspace)
    if not workspace.is_dir():
        raise ThumbsError(f"workspace is not a folder: {workspace}")
    summary = load_summary(workspace)
    recap_path = resolve_recap(workspace, recap)
    if recap_path is None or not recap_path.is_file():
        raise ThumbsError("no recap found: pass --recap or render recap_720p.mp4 first")
    thumbs_dir = workspace / THUMBS_DIR
    shots = summary["shots"]
    if not force and thumbs_are_fresh(thumbs_dir, recap_path, shots):
        return {
            "tool": TOOL,
            "workspace": str(workspace),
            "recap": str(recap_path),
            "poster": str(thumbs_dir / POSTER_NAME),
            "shots": shots,
            "skipped": True,
            **summary_fields(started),
        }
    duration = probe_duration(recap_path)
    if duration is None:
        raise ThumbsRuntimeError(f"ffprobe returned no duration for {recap_path.name}")
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    extract_frame(recap_path, duration * POSTER_FRACTION, thumbs_dir / POSTER_NAME, width)
    for number, block in enumerate(summary["blocks"], start=1):
        middle = (block["start"] + block["end"]) / 2.0
        extract_frame(recap_path, middle, thumbs_dir / f"shot-{number}.jpg", width)
    return {
        "tool": TOOL,
        "workspace": str(workspace),
        "recap": str(recap_path),
        "poster": str(thumbs_dir / POSTER_NAME),
        "shots": shots,
        "skipped": False,
        **summary_fields(started),
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = build_thumbs(args.workspace, args.recap, args.width, args.force)
    except (ThumbsRuntimeError, FramesError) as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except ThumbsError as exc:
        return report_error(exc, EXIT_USAGE, args.json)
    if args.json:
        print(json.dumps(summary))
    else:
        state = "skipped" if summary["skipped"] else "written"
        print(f"{state}: {summary['shots']} shots -> {summary['poster']}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
