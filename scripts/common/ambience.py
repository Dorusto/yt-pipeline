# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ambient-audio cleanup chain shared by ``audio_clean.py`` and ``render.py``.

Wind high-pass, RNNoise or afftdn noise reduction and loudnorm — the same chain
both tools build, so the render's ambient bus is cleaned exactly like a clip the
standalone tool cleans. No tool imports another tool; this module is the shared
code. Sources are read-only.
"""

from pathlib import Path

DEFAULT_MODEL = Path.home() / ".cache" / "yt-pipeline" / "models" / "arnndn.rnnn"
DEFAULT_WIND_HZ = 150.0
DEFAULT_TARGET_LUFS = -16.0
DENOISERS = ("arnndn", "afftdn", "none")


class AmbienceError(RuntimeError):
    """Raised when the cleanup chain cannot be resolved."""


def fmt(value):
    """Format a number for an ffmpeg filter argument without trailing zeros."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def escape_filter_value(value):
    """Escape a path for use inside an ffmpeg filter argument."""
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def resolve_denoiser(requested, model_path):
    """Return ``(denoiser, warnings)`` for the requested backend and model.

    ``requested`` is ``arnndn``, ``afftdn``, ``none`` or None for auto: arnndn
    when the model file exists, else afftdn with a warning.
    """
    model_path = Path(model_path)
    if requested == "none":
        return "none", []
    if requested == "afftdn":
        return "afftdn", []
    if requested == "arnndn":
        if model_path.is_file():
            return "arnndn", []
        raise AmbienceError(
            f"arnndn needs an RNNoise model file; pass --model PATH (a .rnnn file),"
            f" no model at {model_path}"
        )
    if model_path.is_file():
        return "arnndn", []
    return "afftdn", [
        f"no RNNoise model found at {model_path}, using afftdn (pass --model or --denoiser arnndn)"
    ]


def build_filters(denoiser, model_path, wind_hz, apply_highpass, apply_loudnorm, target_lufs):
    """Return ``(filter strings, applied filter names)`` for the cleanup pass."""
    filters = []
    applied = []
    if apply_highpass:
        filters.append(f"highpass=f={fmt(wind_hz)}")
        applied.append("highpass")
    if denoiser == "arnndn":
        filters.append(f"arnndn=m={escape_filter_value(str(model_path))}")
        applied.append("arnndn")
    elif denoiser == "afftdn":
        filters.append("afftdn=nr=12")
        applied.append("afftdn")
    if apply_loudnorm:
        filters.append(f"loudnorm=I={fmt(target_lufs)}:TP=-1.5:LRA=11")
        applied.append("loudnorm")
    return filters, applied
