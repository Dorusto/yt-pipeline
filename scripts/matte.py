#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Remove the background of a clip with RVM and write a ProRes 4444 .mov with alpha.

Usage:
    python3 scripts/matte.py VIDEO [--start SEC] [--end SEC] [--ratio R] [--out FILE] [--json]

RobustVideoMatting (RVM) is loaded through ``torch.hub`` (never vendored here) and
runs on CUDA when available, else on CPU. Only the ``--start``/``--end`` range is
processed. The per-frame foreground and alpha are written as two PNG sequences and
merged into a ProRes 4444 ``.mov`` with alpha (video only; the editor keeps the
original audio). Sources are read-only; the output is written atomically. No model
service is called, so ``llm_cost_usd`` is always 0.0.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cv2

from common.ffmpeg_tools import ffmpeg_path
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME

RATIO_4K = 0.125
RATIO_1080P = 0.25
RATIO_720P = 0.4
RATIO_4K_MIN_HEIGHT = 1600
RATIO_1080P_MIN_HEIGHT = 900
HUB_REPO = "PeterL1n/RobustVideoMatting"
HUB_MODEL = "resnet50"
MISSING_TORCH = (
    "torch is not installed (pip install torch torchvision); matte.py needs it for RVM"
)
INVALID_FRAME_RATES = {"", "0/0", "N/A"}


class MatteError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


class MatteRuntimeError(Exception):
    """An unexpected failure after validation that exits with code 1."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="matte.py",
        description=(
            "Remove the background of a clip with RobustVideoMatting (RVM) and write a "
            "ProRes 4444 .mov with alpha, ready to place over screen capture."
        ),
        epilog=(
            "examples:\n"
            "  python3 scripts/matte.py clip.mp4\n"
            "  python3 scripts/matte.py clip.mp4 --start 10 --end 40 --ratio 0.25 --json\n"
            "  python3 scripts/matte.py clip.mp4 --out /tmp/clip_matte.mov\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("video", help="input clip (.mp4/.mov), read-only")
    parser.add_argument("--start", type=float, default=None, help="start of the range in seconds (default 0)")
    parser.add_argument("--end", type=float, default=None, help="end of the range in seconds, exclusive (default clip end)")
    parser.add_argument(
        "--ratio",
        type=float,
        default=None,
        help=(
            "RVM downsample_ratio, > 0 and <= 1; explicit override. "
            "Default auto from the input height: 0.125 at 4K (>=1600), "
            "0.25 at 1080p (>=900), 0.4 at 720p and smaller"
        ),
    )
    parser.add_argument("--out", default=None, help="output .mov path (default <video parent>/<stem>_matte.mov)")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def resolve_output(video, out):
    """Return the output path: ``--out`` or ``<video parent>/<stem>_matte.mov``."""
    if out:
        return Path(out).expanduser()
    return video.parent / f"{video.stem}_matte.mov"


def default_ratio_for_height(height):
    """Return the RVM downsample ratio for a video height (RVM guidance)."""
    if height >= RATIO_4K_MIN_HEIGHT:
        return RATIO_4K
    if height >= RATIO_1080P_MIN_HEIGHT:
        return RATIO_1080P
    return RATIO_720P


def parse_rate(value):
    """Parse an ffmpeg rate string such as ``25/1`` into a float, or None."""
    if not value or value in INVALID_FRAME_RATES:
        return None
    if "/" in value:
        numerator, _, denominator = value.partition("/")
        try:
            denominator_value = float(denominator)
            return float(numerator) / denominator_value if denominator_value else None
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def probe_video(path):
    """Return duration, fps, width and height of the first video stream."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate:format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise MatteRuntimeError(f"could not run ffprobe: {exc}")
    if result.returncode != 0:
        lines = (result.stderr or "").strip().splitlines()
        detail = lines[-1] if lines else "unknown error"
        raise MatteError(f"ffprobe could not read {path}: {detail}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MatteRuntimeError(f"ffprobe returned invalid JSON for {path}: {exc}")
    streams = data.get("streams") or []
    if not streams:
        raise MatteError(f"no video stream in {path}")
    stream = streams[0]
    fps_str = stream.get("r_frame_rate") or stream.get("avg_frame_rate")
    fps = parse_rate(fps_str)
    if fps is None:
        raise MatteError(f"could not read the frame rate of {path}")
    duration = (data.get("format") or {}).get("duration")
    if duration is None:
        raise MatteError(f"could not read the duration of {path}")
    return {
        "duration": float(duration),
        "fps": fps,
        "fps_str": fps_str,
        "width": int(stream["width"]),
        "height": int(stream["height"]),
    }


def load_model(torch, device):
    """Load RVM through torch.hub and return the model in eval mode on ``device``."""
    try:
        model = torch.hub.load(HUB_REPO, HUB_MODEL, trust_repo=True)
    except Exception as exc:
        raise MatteRuntimeError(f"could not load RVM via torch.hub: {exc}")
    return model.eval().to(device)


def write_foreground(torch, fgr, path):
    """Write the model's RGB foreground frame as a BGR PNG."""
    array = (
        fgr[0]
        .mul(255)
        .round()
        .clamp(0, 255)
        .to(torch.uint8)
        .permute(1, 2, 0)
        .cpu()
        .numpy()
    )
    if not cv2.imwrite(str(path), cv2.cvtColor(array, cv2.COLOR_RGB2BGR)):
        raise MatteRuntimeError(f"could not write {path}")


def write_alpha(torch, pha, path):
    """Write the model's single-channel alpha frame as a grayscale PNG."""
    array = (pha[0, 0] * 255).round().clamp(0, 255).to(torch.uint8).cpu().numpy()
    if not cv2.imwrite(str(path), array):
        raise MatteRuntimeError(f"could not write {path}")


def process_frames(torch, model, device, video, start_s, end_s, ratio, fgr_dir, pha_dir):
    """Run RVM over the range and write the foreground and alpha PNG sequences."""
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise MatteError(f"could not open video: {video}")
    count = 0
    try:
        if start_s > 0:
            capture.set(cv2.CAP_PROP_POS_MSEC, start_s * 1000.0)
        rec = [None] * 4
        with torch.no_grad():
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                frame_time = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                if frame_time < start_s:
                    continue
                if frame_time >= end_s:
                    break
                count += 1
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                src = (
                    torch.from_numpy(rgb)
                    .permute(2, 0, 1)
                    .unsqueeze(0)
                    .float()
                    .div_(255)
                    .to(device)
                )
                fgr, pha, *rec = model(src, *rec, ratio)
                write_foreground(torch, fgr, fgr_dir / f"{count:05d}.png")
                write_alpha(torch, pha, pha_dir / f"{count:05d}.png")
    finally:
        capture.release()
    return count


def encode_prores(fgr_dir, pha_dir, fps_str, output):
    """Merge the foreground and alpha PNG sequences into a ProRes 4444 .mov."""
    tmp_output = output.with_name(output.stem + ".tmp" + output.suffix)
    command = [
        ffmpeg_path(),
        "-y",
        "-v",
        "error",
        "-framerate",
        fps_str,
        "-i",
        str(fgr_dir / "%05d.png"),
        "-framerate",
        fps_str,
        "-i",
        str(pha_dir / "%05d.png"),
        "-filter_complex",
        "[0:v][1:v]alphamerge,format=yuva444p10le",
        "-c:v",
        "prores_ks",
        "-profile:v",
        "4444",
        "-pix_fmt",
        "yuva444p10le",
        str(tmp_output),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise MatteRuntimeError(f"could not run ffmpeg: {exc}")
    try:
        if result.returncode != 0:
            lines = (result.stderr or "").strip().splitlines()
            detail = "\n".join(lines[-5:]) if lines else "unknown error"
            raise MatteRuntimeError(f"ffmpeg failed to write {output}:\n{detail}")
        os.replace(tmp_output, output)
    finally:
        if tmp_output.exists():
            tmp_output.unlink()


def run(video, start=None, end=None, ratio=None, out=None):
    """Remove the background of ``video`` over the range and write the matte .mov."""
    started = time.monotonic()
    video = Path(video).expanduser()
    if not video.is_file():
        raise MatteError(f"input file not found: {video}")
    if not os.access(video, os.R_OK):
        raise MatteError(f"input file is not readable: {video}")

    output = resolve_output(video, out)
    if output.resolve() == video.resolve():
        raise MatteError(f"--out must not overwrite the input: {output}")

    if ratio is not None and not 0.0 < ratio <= 1.0:
        raise MatteError(f"--ratio must be > 0 and <= 1, got {ratio}")

    if start is not None and end is not None and start >= end:
        raise MatteError(f"--start {start} must be before --end {end}")

    if shutil.which("ffmpeg") is None:
        raise MatteRuntimeError("ffmpeg not found on PATH")
    if shutil.which("ffprobe") is None:
        raise MatteRuntimeError("ffprobe not found on PATH")

    info = probe_video(video)
    duration = info["duration"]
    start_s = 0.0 if start is None else float(start)
    end_s = duration if end is None else float(end)
    if start_s < 0 or start_s >= end_s or end_s > duration:
        raise MatteError(
            f"range start={start_s}s end={end_s}s is invalid for a clip of {duration}s "
            f"(need 0 <= start < end <= {duration})"
        )

    effective_ratio = ratio if ratio is not None else default_ratio_for_height(info["height"])

    try:
        import torch
    except ImportError:
        raise MatteRuntimeError(MISSING_TORCH)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = load_model(torch, device)

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="matte_") as temporary_dir:
        fgr_dir = Path(temporary_dir) / "fgr"
        pha_dir = Path(temporary_dir) / "pha"
        fgr_dir.mkdir()
        pha_dir.mkdir()
        frames = process_frames(
            torch, model, device, video, start_s, end_s, effective_ratio, fgr_dir, pha_dir
        )
        if frames == 0:
            raise MatteRuntimeError(f"no frames read from {video} in range {start_s}-{end_s}s")
        encode_prores(fgr_dir, pha_dir, info["fps_str"], output)

    return {
        "output": str(output),
        "frames": frames,
        "width": info["width"],
        "height": info["height"],
        "fps": info["fps"],
        "start_s": start_s,
        "end_s": end_s,
        "downsample_ratio": effective_ratio,
        "device": device,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = run(
            args.video,
            start=args.start,
            end=args.end,
            ratio=args.ratio,
            out=args.out,
        )
    except MatteError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    if args.json:
        print(json.dumps(summary))
    else:
        print(f"written: {summary['output']} ({summary['frames']} frames)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
