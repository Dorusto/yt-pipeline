"""Shared FilmCraft helpers: the ``filmcraft-cli`` lookup and the sequence frame rate."""

import os
import shutil
from fractions import Fraction

DEFAULT_FRAME_RATE = 25.0


def cli_path():
    """Return the filmcraft-cli binary from ``$FILMCRAFT_CLI`` or PATH, or None when missing."""
    env = os.environ.get("FILMCRAFT_CLI")
    if env:
        return env
    return shutil.which("filmcraft-cli")


def parse_frame_rate(settings):
    """Return a sequence settings dict's frame rate as a float, defaulting to 25 when unset or unusable."""
    rate = (settings or {}).get("frame_rate") or {}
    num = rate.get("num")
    den = rate.get("den")
    if not num or not den:
        return DEFAULT_FRAME_RATE
    try:
        fps = float(num) / float(den)
    except (TypeError, ValueError, ZeroDivisionError):
        return DEFAULT_FRAME_RATE
    if fps <= 0:
        return DEFAULT_FRAME_RATE
    return fps


def frame_rate_fraction(fps):
    """Return a frame rate as the ``{"num", "den"}`` pair a sequence settings dict carries."""
    fraction = Fraction(float(fps)).limit_denominator(1001)
    return {"num": fraction.numerator, "den": fraction.denominator}
