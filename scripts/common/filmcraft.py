"""Shared FilmCraft helpers: the ``filmcraft-cli`` lookup and resolution, the
project/sequence/track readers, the frame-rate helpers, the command runner and
the reply parsers used by the FilmCraft tools."""

import json
import os
import re
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

from common.exit_codes import EXIT_RUNTIME, EXIT_USAGE

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


class FilmCraftError(Exception):
    """A FilmCraft project or CLI problem; ``code`` is the process exit code."""

    def __init__(self, message, code=EXIT_USAGE):
        super().__init__(message)
        self.code = code


class FilmCraftRuntimeError(FilmCraftError):
    """A FilmCraft failure after the work started; exits with EXIT_RUNTIME."""

    def __init__(self, message):
        super().__init__(message, code=EXIT_RUNTIME)


def load_project_json(path):
    """Read a UTF-8 JSON file, raising FilmCraftError when it is missing or unreadable."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise FilmCraftError(f"cannot read '{path}': file not found")
    except json.JSONDecodeError as exc:
        raise FilmCraftError(f"cannot read '{path}': not valid JSON ({exc})")
    except OSError as exc:
        raise FilmCraftError(f"cannot read '{path}': {exc}")


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
        raise FilmCraftError("no sequence found in the project")
    if requested:
        if requested not in sequences:
            found = ", ".join(sorted(sequences)) or "none"
            raise FilmCraftError(f"unknown sequence '{requested}' (sequences found: {found})")
        return requested, sequences[requested]
    if len(sequences) > 1:
        found = ", ".join(sorted(sequences))
        raise FilmCraftError(f"the project has more than one sequence, pass --sequence (found: {found})")
    name = next(iter(sequences))
    return name, sequences[name]


def sequence_block(sequence_item):
    """Return the sequence's ``kind.Sequence`` block."""
    return (sequence_item.get("kind") or {}).get("Sequence") or {}


def select_track(sequence_item, track_name, track_key="audio_tracks"):
    """Return the track named ``track_name`` of the sequence."""
    block = sequence_block(sequence_item)
    tracks = {entry.get("name"): entry for entry in block.get(track_key) or []}
    if track_name not in tracks:
        found = ", ".join(sorted(str(name) for name in tracks)) or "none"
        raise FilmCraftError(f"unknown track '{track_name}' (tracks found: {found})")
    return tracks[track_name]


def frame_rate(sequence_item):
    """Return the sequence frame rate as a float, defaulting to 25 when unset."""
    return parse_frame_rate(sequence_block(sequence_item).get("settings") or {})


def resolve_cli(required=True):
    """Return the filmcraft-cli binary from ``$FILMCRAFT_CLI`` or PATH."""
    found = cli_path()
    if found:
        return found
    if not required:
        return "filmcraft-cli"
    raise FilmCraftRuntimeError("filmcraft-cli not found: set FILMCRAFT_CLI or put filmcraft-cli on PATH")


def format_command(command):
    """Join a command list into one line."""
    return " ".join(command)


def run_command(command, label):
    """Run one filmcraft-cli command and return its stdout, raising on a non-zero exit."""
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise FilmCraftRuntimeError(f"could not run {command[0]}: {exc}")
    if result.returncode != 0:
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        tail = "\n".join(output.splitlines()[-5:])
        raise FilmCraftRuntimeError(f"{label} failed (exit {result.returncode}):\n{tail}")
    return result.stdout or ""


def parse_reply(text, label):
    """Parse a filmcraft-cli JSON reply, raising when it reports ``ok: false``."""
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise FilmCraftRuntimeError(f"{label} returned no JSON: {raw[-200:]}")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise FilmCraftRuntimeError(f"{label} returned invalid JSON: {exc}")
    if isinstance(data, dict) and data.get("ok") is False:
        raise FilmCraftRuntimeError(f"{label} returned ok=false: {raw[-200:]}")
    return data


def parse_replies(text):
    """Parse the JSON reply lines ``filmcraft-cli run`` prints."""
    replies = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            replies.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return replies
