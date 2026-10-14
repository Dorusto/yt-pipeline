"""ffmpeg helpers: locate the binary, choose a usable H.264 encoder, build args.

The build machine has no GPU, so nothing may assume nvenc. Encoders are chosen
from what the local ffmpeg build reports *and* the platform: ``h264_nvenc`` when
an NVIDIA GPU is present, ``h264_videotoolbox`` on macOS, else ``libx264``.
A preferred encoder that is missing from the ffmpeg build falls back through
the known encoders, ``libx264`` last. An explicit override can be given via
``YT_VIDEO_ENCODER``.
"""

import functools
import shutil
import subprocess
import sys

H264_ENCODERS = ("h264_nvenc", "h264_videotoolbox", "libx264")

VIDEO_TOOLBOX_BITRATE = "8M"


def ffmpeg_path():
    return shutil.which("ffmpeg") or "ffmpeg"


def has_nvidia_gpu():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False
    try:
        return subprocess.run(
            [exe, "-L"], capture_output=True, timeout=5
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@functools.lru_cache(maxsize=1)
def available_video_encoders():
    try:
        result = subprocess.run(
            [ffmpeg_path(), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    encoders = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            encoders.add(parts[1])
    return frozenset(encoders)


def preferred_video_encoder():
    """First-choice H.264 encoder for this machine: nvenc with an NVIDIA GPU,
    videotoolbox on macOS, else libx264."""
    if has_nvidia_gpu():
        return "h264_nvenc"
    if sys.platform == "darwin":
        return "h264_videotoolbox"
    return "libx264"


def pick_video_encoder(available=None, prefer=None):
    """Pick an H.264 encoder from what the local ffmpeg build reports.

    ``prefer`` names the encoder to try first (default: ``preferred_video_encoder``);
    a missing encoder falls back through ``H264_ENCODERS``, ``libx264`` last.
    """
    available = available_video_encoders() if available is None else set(available)
    prefer = prefer or preferred_video_encoder()
    order = (prefer,) + tuple(name for name in H264_ENCODERS if name != prefer)
    for name in order:
        if name in available:
            return name
    raise RuntimeError("ffmpeg reports no usable H.264 encoder")


def video_encode_args(encoder, crf=23, preset="medium"):
    """Codec args for ``encoder``: nvenc constant-quality ``p4/cq<crf>``,
    videotoolbox basic ``-b:v`` (constant quality only exists on Apple Silicon),
    libx264 ``<preset>/crf<crf>``."""
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(crf)]
    if encoder == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-b:v", VIDEO_TOOLBOX_BITRATE]
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
    return ["-c:v", encoder]
