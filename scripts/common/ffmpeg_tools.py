"""ffmpeg helpers: locate the binary, choose a usable H.264 encoder, build args.

The build machine has no GPU, so nothing may assume nvenc. Encoders are chosen
from what the local ffmpeg build reports *and* whether an NVIDIA GPU is
actually present; an explicit override can be given via ``YT_VIDEO_ENCODER``.
"""

import functools
import shutil
import subprocess

H264_ENCODERS = ("h264_nvenc", "libx264")


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


def pick_video_encoder(available=None, prefer_nvenc=None):
    available = available_video_encoders() if available is None else set(available)
    if prefer_nvenc is None:
        prefer_nvenc = has_nvidia_gpu()
    order = H264_ENCODERS if prefer_nvenc else tuple(reversed(H264_ENCODERS))
    for name in order:
        if name in available:
            return name
    if available:
        return sorted(available)[0]
    raise RuntimeError("ffmpeg reports no usable video encoder")


def video_encode_args(encoder):
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"]
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", "medium", "-crf", "23"]
    return ["-c:v", encoder]