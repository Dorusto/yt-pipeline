#!/usr/bin/env python3
"""Build 720p H.264 constant-frame-rate proxies with audio from originals.

Usage:
    python scripts/proxy.py SOURCE
    python scripts/proxy.py SOURCE --out DIR --json

``SOURCE`` is a folder of video clips (non-recursive) or one video file. Each
proxy keeps the source file name and is written to ``--out`` (default:
``<source parent>/proxy``). Existing proxies are skipped unless ``--force`` is
given. Originals are read-only; the tool refuses to write inside the source
folder. The encoder is chosen at runtime (``h264_nvenc`` with an NVIDIA GPU,
``h264_videotoolbox`` on macOS, else ``libx264``) with a retry to ``libx264``
when the chosen encoder fails.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from common.ffmpeg_tools import ffmpeg_path, pick_video_encoder, video_encode_args
from common.photos import PHOTO_EXTENSIONS, ensure_jpeg, is_photo

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".insv"}
DEFAULT_HEIGHT = 720
DEFAULT_CRF = 23
DEFAULT_FRAME_RATE = "25"
INVALID_FRAME_RATES = {"", "0/0", "N/A"}


class ProxyError(RuntimeError):
    """Raised when a proxy run cannot be completed."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="proxy.py",
        description="Build 720p H.264 constant-frame-rate proxies with audio from originals.",
        epilog=(
            "examples:\n"
            "  python scripts/proxy.py ~/media/raw\n"
            "  python scripts/proxy.py ~/media/raw --out ~/media/workspace/proxy --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "source",
        help="folder of video clips (non-recursive) or one video file",
    )
    parser.add_argument(
        "--out",
        help="output folder for the proxies (default: <source parent>/proxy)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=DEFAULT_HEIGHT,
        help=f"output height in pixels (default: {DEFAULT_HEIGHT})",
    )
    parser.add_argument(
        "--crf",
        type=int,
        default=DEFAULT_CRF,
        help=f"libx264 quality, lower is better (default: {DEFAULT_CRF})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rebuild even when the proxy exists",
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def is_within(path, directory):
    path = Path(path).expanduser().resolve()
    directory = Path(directory).expanduser().resolve()
    return path == directory or directory in path.parents


def find_clips(source):
    return sorted(
        (
            path
            for path in source.iterdir()
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        ),
        key=lambda path: path.name,
    )


def find_photos(source):
    return sorted(
        (path for path in source.iterdir() if path.is_file() and is_photo(path)),
        key=lambda path: path.name,
    )


def copy_photo(photo, out_dir):
    """Copy one still into ``out_dir`` and return its target name.

    JPEGs are copied byte-for-byte under the same name; a HEIC still becomes
    ``<stem>.jpg`` so everything downstream can decode it.
    """
    if photo.suffix.lower() == ".heic":
        converted = ensure_jpeg(photo, out_dir)
        target = out_dir / f"{photo.stem}.jpg"
        if converted != target:
            os.replace(converted, target)
        return target.name
    target = out_dir / photo.name
    if not target.exists():
        shutil.copy2(photo, target)
    return target.name


def run_ffprobe(path):
    command = ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise ProxyError(f"could not run ffprobe: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown error"
        raise ProxyError(f"ffprobe failed for {path.name}: {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProxyError(f"ffprobe returned invalid JSON for {path.name}: {exc}") from exc


def probe_frame_rate(path):
    """Return the source frame rate as an ffmpeg rate string such as ``25/1``."""
    data = run_ffprobe(path)
    video = next(
        (stream for stream in data.get("streams") or [] if stream.get("codec_type") == "video"),
        None,
    )
    if video is None:
        raise ProxyError(f"no video stream in {path.name}")
    for key in ("r_frame_rate", "avg_frame_rate"):
        rate = video.get(key)
        if rate and rate not in INVALID_FRAME_RATES:
            return rate
    return DEFAULT_FRAME_RATE


def build_command(source, tmp_path, height, crf, encoder, frame_rate):
    command = [
        ffmpeg_path(),
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-map_metadata",
        "0",
        "-vf",
        f"scale=-2:{height}",
        "-r",
        frame_rate,
        "-fps_mode",
        "cfr",
    ]
    command += video_encode_args(encoder, crf=crf, preset="veryfast")
    command += [
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
    return command


def run_ffmpeg(command):
    try:
        return subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise ProxyError(f"could not run ffmpeg: {exc}") from exc


def last_stderr_line(result):
    lines = (result.stderr or "").strip().splitlines()
    return lines[-1] if lines else "unknown error"


def encode_clip(source, out_path, height, crf, encoder):
    """Encode one proxy atomically and return the encoder actually used."""
    tmp_path = out_path.with_name(out_path.name + ".tmp.mp4")
    frame_rate = probe_frame_rate(source)
    active = encoder
    try:
        result = run_ffmpeg(build_command(source, tmp_path, height, crf, active, frame_rate))
        if result.returncode != 0 and active != "libx264":
            print(
                f"[warn] {source.name}: {active} failed, retrying with libx264",
                file=sys.stderr,
            )
            active = "libx264"
            result = run_ffmpeg(build_command(source, tmp_path, height, crf, active, frame_rate))
        if result.returncode != 0:
            raise ProxyError(f"{source.name}: {last_stderr_line(result)}")
        os.replace(tmp_path, out_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return active


def build_proxies(source, out, height, crf, force):
    """Build proxies for ``source`` into ``out`` and return the run summary."""
    started = time.monotonic()
    source = Path(source).expanduser().resolve()
    if not source.exists():
        raise ProxyError(f"source not found: {source}")
    photos = []
    if source.is_dir():
        clips = find_clips(source)
        photos = find_photos(source)
        if not clips and not photos:
            raise ProxyError(
                f"no video files ({', '.join(sorted(VIDEO_EXTENSIONS))}) or photos "
                f"({', '.join(sorted(PHOTO_EXTENSIONS))}) in {source}"
            )
    elif source.is_file():
        clips = [source]
    else:
        raise ProxyError(f"source is not a file or folder: {source}")

    if out is None:
        out = source.parent / "proxy"
    out = Path(out).expanduser().resolve()
    originals_root = source if source.is_dir() else source.parent
    out_is_original = is_within(out, source) or (
        not source.is_dir() and out == originals_root
    )
    if out_is_original:
        raise ProxyError(f"--out must not be inside the source: {out}")

    if shutil.which("ffmpeg") is None:
        raise ProxyError("ffmpeg not found on PATH")
    if shutil.which("ffprobe") is None:
        raise ProxyError("ffprobe not found on PATH")
    try:
        encoder = pick_video_encoder()
    except RuntimeError as exc:
        raise ProxyError(str(exc)) from exc

    out.mkdir(parents=True, exist_ok=True)

    written = []
    skipped = []
    failed = []
    photos_copied = 0
    active_encoder = encoder
    for clip in clips:
        target = out / clip.name
        if target.resolve() == clip.resolve():
            print(f"[error] refusing to overwrite original: {target}", file=sys.stderr)
            failed.append(clip.name)
            continue
        if target.exists() and not force:
            skipped.append(clip.name)
            continue
        try:
            active_encoder = encode_clip(clip, target, height, crf, encoder)
        except ProxyError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            failed.append(clip.name)
            continue
        written.append(clip.name)

    for photo in photos:
        target = out / photo.name
        if target.resolve() == photo.resolve():
            print(f"[error] refusing to overwrite original: {target}", file=sys.stderr)
            failed.append(photo.name)
            continue
        expected = photo.stem + ".jpg" if photo.suffix.lower() == ".heic" else photo.name
        if (out / expected).exists() and not force:
            skipped.append(photo.name)
            continue
        try:
            name = copy_photo(photo, out)
        except Exception as exc:
            print(f"[error] {exc}", file=sys.stderr)
            failed.append(photo.name)
            continue
        written.append(name)
        photos_copied += 1

    if failed:
        status = "failed"
    elif written:
        status = "written"
    else:
        status = "skipped"

    return {
        "tool": "scripts/proxy.py",
        "status": status,
        "source": str(source),
        "out": str(out),
        "width": None,
        "height": height,
        "encoder": active_encoder,
        "written": written,
        "skipped": skipped,
        "failed": failed,
        "counts": {
            "written": len(written),
            "skipped": len(skipped),
            "failed": len(failed),
            "photos": photos_copied,
        },
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = build_proxies(args.source, args.out, args.height, args.crf, args.force)
    except ProxyError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary))
    else:
        counts = summary["counts"]
        print(
            f"{summary['status']}: wrote {counts['written']},"
            f" skipped {counts['skipped']}, failed {counts['failed']}"
            f" -> {summary['out']}"
        )
    return 1 if summary["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
