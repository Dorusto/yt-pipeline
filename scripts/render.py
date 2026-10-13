#!/usr/bin/env python3
"""Render an ``edit.json`` edit list to a 720p MP4 with ffmpeg.

Usage:
    python scripts/render.py --edit edit.json --out recap_720p.mp4
    python scripts/render.py --edit edit.json --out recap_720p.mp4 --json

The tool cuts each video slot from the proxy clip named in ``sources[]``,
scales and pads it to the requested frame, joins the slots in timeline order
with crossfades or hard cuts and keeps each slot's own ambient audio. It is the
S1 preview adapter: captions, graphics, music and SFX are warned about and
ignored. Sources are read-only; the MP4 is written atomically to ``--out``.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from common.ffmpeg_tools import ffmpeg_path
from common.schema import SchemaError, validate_or_raise

FPS = 25
SAMPLE_RATE = 48000


class RenderError(RuntimeError):
    """Raised when an edit cannot be rendered."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="render.py",
        description="Render an edit.json edit list to a 720p MP4 with ffmpeg crossfades.",
        epilog=(
            "examples:\n"
            "  python scripts/render.py --edit edit.json --out recap_720p.mp4\n"
            "  python scripts/render.py --edit edit.json --out recap_720p.mp4 --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--edit", required=True, help="edit.json to render")
    parser.add_argument("--out", required=True, help="MP4 file to write")
    parser.add_argument(
        "--media-dir",
        help="fallback directory for clip names that are not in sources[]",
    )
    parser.add_argument("--width", type=int, default=1280, help="output width (default 1280)")
    parser.add_argument("--height", type=int, default=720, help="output height (default 720)")
    parser.add_argument(
        "--xfade-s",
        type=float,
        default=0.3,
        help="crossfade duration when the slot does not set one (default 0.3)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rewrite even when the output is up to date",
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def fmt(value):
    """Format a number for an ffmpeg filter argument without trailing zeros."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def run_ffprobe(path):
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise RenderError(f"could not run ffprobe: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown error"
        raise RenderError(f"ffprobe failed for {path}: {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RenderError(f"ffprobe returned invalid JSON for {path}: {exc}") from exc


def probe_output(path):
    """Return ``(duration_s, width, height)`` for a rendered file."""
    data = run_ffprobe(path)
    duration = float(data["format"]["duration"])
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise RenderError(f"no video stream in {path}")
    return duration, int(video["width"]), int(video["height"])


def probe_duration(path):
    try:
        return round(float(run_ffprobe(path)["format"]["duration"]), 3)
    except (RenderError, KeyError, TypeError, ValueError):
        return None


def has_audio_stream(path):
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise RenderError(f"could not run ffprobe: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown error"
        raise RenderError(f"ffprobe failed for {path}: {detail}")
    try:
        return bool(json.loads(result.stdout).get("streams"))
    except json.JSONDecodeError as exc:
        raise RenderError(f"ffprobe returned invalid JSON for {path}: {exc}") from exc


def resolve_clip(clip, sources, media_dir):
    """Resolve a slot's ``clip`` to a path via ``sources[]`` then ``--media-dir``."""
    for source in sources:
        if source.get("file") == clip:
            return Path(source["path"])
    if media_dir is not None:
        candidate = Path(media_dir).expanduser() / clip
        if candidate.is_file():
            return candidate
    return None


def fade_for_join(slot, dur_prev, dur_next, xfade_s):
    """Return the crossfade length used at the join into ``slot``; 0 means cut."""
    transition = slot.get("transition") or {}
    if transition.get("type") != "crossfade":
        return 0.0
    requested = transition.get("duration_s") or 0.0
    if requested <= 0:
        requested = xfade_s
    if requested <= 0:
        return 0.0
    return min(requested, dur_prev / 2, dur_next / 2)


def build_filter_graph(slots, durations, has_audio, ambience_enabled, width, height, xfade_s, warnings):
    """Build the ``filter_complex`` graph and return it with the final labels."""
    parts = []
    for index, slot in enumerate(slots):
        start = fmt(slot["in_s"])
        end = fmt(slot["out_s"])
        parts.append(
            f"[{index}:v]trim=start={start}:end={end},setpts=PTS-STARTPTS,fps={FPS},"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{index}]"
        )
        if ambience_enabled and has_audio[index]:
            parts.append(
                f"[{index}:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
                f"aresample={SAMPLE_RATE}[a{index}]"
            )
        else:
            parts.append(
                f"anullsrc=r={SAMPLE_RATE}:cl=stereo,"
                f"atrim=start=0:end={fmt(durations[index])},"
                f"asetpts=PTS-STARTPTS[a{index}]"
            )

    current_v = "v0"
    current_a = "a0"
    accumulated = durations[0]
    for join in range(len(slots) - 1):
        nxt = join + 1
        fade = fade_for_join(slots[nxt], durations[join], durations[nxt], xfade_s)
        if fade > 0:
            offset = accumulated - fade
            if offset < 0:
                offset = 0.0
                warnings.append(f"join {join}: crossfade offset clamped to 0")
            parts.append(
                f"[{current_v}][v{nxt}]xfade=transition=fade:duration={fmt(fade)}:"
                f"offset={fmt(offset)}[xv{join}]"
            )
            parts.append(f"[{current_a}][a{nxt}]acrossfade=d={fmt(fade)}[xa{join}]")
            current_v = f"xv{join}"
            current_a = f"xa{join}"
            accumulated = accumulated + durations[nxt] - fade
        else:
            parts.append(
                f"[{current_v}][{current_a}][v{nxt}][a{nxt}]concat=n=2:v=1:a=1"
                f"[cv{join}][ca{join}]"
            )
            parts.append(f"[cv{join}]settb=1/{FPS}[cvt{join}]")
            current_v = f"cvt{join}"
            current_a = f"ca{join}"
            accumulated = accumulated + durations[nxt]
    return ";".join(parts), current_v, current_a


def collect_warnings(document, ambience):
    warnings = []
    audio = document.get("audio") or {}
    if ambience.get("enabled") and ambience.get("source") == "file":
        warnings.append("ambience file ignored in S1 render, using slot audio")
    if audio.get("music") is not None or audio.get("sfx"):
        warnings.append("music/sfx ignored in S1 render")
    if document.get("captions"):
        warnings.append("captions ignored in S1 render")
    if document.get("graphics"):
        warnings.append("graphics ignored in S1 render")
    return warnings


def render(edit_path, out_path, media_dir, width, height, xfade_s, force):
    """Render ``edit_path`` to ``out_path`` and return the run summary."""
    started = time.monotonic()
    edit_path = Path(edit_path).expanduser()
    out_path = Path(out_path).expanduser()
    if not edit_path.is_file():
        raise RenderError(f"edit file not found: {edit_path}")
    try:
        document = json.loads(edit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RenderError(f"could not read edit file: {exc}") from exc
    validate_or_raise(document, "edit")

    slots = document["video"]
    sources = document["sources"]
    audio = document.get("audio") or {}
    ambience = audio.get("ambience") or {}
    ambience_enabled = bool(ambience.get("enabled", False))
    warnings = collect_warnings(document, ambience)

    if out_path.exists() and not force and out_path.stat().st_mtime > edit_path.stat().st_mtime:
        return {
            "status": "skipped",
            "out": str(out_path),
            "duration_s": probe_duration(out_path),
            "slots": len(slots),
            "warnings": warnings,
            "wall_time_s": round(time.monotonic() - started, 3),
            "llm_cost_usd": 0.0,
        }

    clip_paths = []
    for slot in slots:
        resolved = resolve_clip(slot["clip"], sources, media_dir)
        if resolved is None:
            raise RenderError(f"could not resolve clip: {slot['clip']}")
        clip_paths.append(resolved)

    durations = [float(slot["out_s"]) - float(slot["in_s"]) for slot in slots]
    if ambience_enabled:
        has_audio = [has_audio_stream(path) for path in clip_paths]
    else:
        has_audio = [False] * len(slots)

    filter_complex, final_v, final_a = build_filter_graph(
        slots, durations, has_audio, ambience_enabled, width, height, xfade_s, warnings
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".tmp.mp4")
    command = [ffmpeg_path(), "-y"]
    for path in clip_paths:
        command += ["-i", str(path)]
    command += [
        "-filter_complex",
        filter_complex,
        "-map",
        f"[{final_v}]",
        "-map",
        f"[{final_a}]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(tmp_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            lines = (result.stderr or "").strip().splitlines()
            detail = lines[-1] if lines else "unknown error"
            raise RenderError(f"ffmpeg failed: {detail}")
        duration, out_width, out_height = probe_output(tmp_path)
        if (out_width, out_height) != (width, height):
            raise RenderError(f"unexpected output resolution: {out_width}x{out_height}")
        os.replace(tmp_path, out_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return {
        "status": "written",
        "out": str(out_path),
        "duration_s": round(duration, 3),
        "slots": len(slots),
        "warnings": warnings,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = render(
            args.edit,
            args.out,
            args.media_dir,
            args.width,
            args.height,
            args.xfade_s,
            args.force,
        )
    except (RenderError, SchemaError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary))
    else:
        if summary["status"] == "skipped":
            print(f"skipped {summary['out']} (up to date)")
        else:
            print(f"wrote {summary['out']}")
            print(f"{summary['duration_s']}s, {summary['slots']} slots, {args.width}x{args.height}")
        for warning in summary["warnings"]:
            print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
