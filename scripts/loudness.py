#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Apply the audio loudness recipe, in the editor or without one.

Two backends share one command:

- ``--video`` runs an ffmpeg two-pass ``loudnorm`` (I=-14 LUFS, TP=-1 dBTP,
  LRA 11 by default, the YouTube targets) on a video file, copying the video
  stream and re-encoding the audio as AAC 320k.
- ``--fcproj`` applies the Essential Sound recipe (Dialogue type, Auto-Match
  loudness, Clarity Dynamics medium) to the voice clips of one audio track
  through the headless ``filmcraft-cli``. The input project is never modified
  unless ``--in-place`` is given; without it the tool works on a copy.

No model is called; ``llm_cost_usd`` is always 0.0.

Usage:
    python3 scripts/loudness.py --video VIDEO [--out FILE] [--json]
    python3 scripts/loudness.py --fcproj PROJECT.fcproj [--sequence NAME]
        [--track "Audio 1"] [--target-lufs -14] [--out FILE | --in-place]
        [--dry-run] [--json]
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from common.constants import TICKS_PER_SECOND
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.ffmpeg_tools import ffmpeg_path
from common.filmcraft import cli_path

DEFAULT_TARGET_I = -14.0
DEFAULT_TARGET_TP = -1.0
DEFAULT_TARGET_LRA = 11.0
DEFAULT_TARGET_LUFS = -14.0
DEFAULT_TRACK = "Audio 1"
DEFAULT_CLARITY_AMOUNT = 5
VIDEO_SUFFIXES = (".mp4", ".mov")
LOUDNORM_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)
CLI_COMMAND_LABELS = ("essentialSound.setType", "essentialSound.autoMatch", "essentialSound.set")


class LoudnessError(Exception):
    """A user-facing error; ``code`` is the process exit code (EXIT_USAGE for user errors)."""

    def __init__(self, message, code=EXIT_USAGE):
        super().__init__(message)
        self.code = code


class LoudnessRuntimeError(LoudnessError):
    """A failure after the work started; exits with EXIT_RUNTIME."""

    def __init__(self, message):
        super().__init__(message, code=EXIT_RUNTIME)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="loudness.py",
        description=(
            "Apply the loudness recipe: an ffmpeg two-pass loudnorm on a video file, "
            "or the Essential Sound recipe on a FilmCraft project through filmcraft-cli."
        ),
        epilog=(
            "examples:\n"
            "  python3 scripts/loudness.py --video clip.mp4 --json\n"
            "  python3 scripts/loudness.py --fcproj project.fcproj --sequence Main --json\n"
            "  python3 scripts/loudness.py --fcproj project.fcproj --dry-run\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--video", default=None, help="video file for the ffmpeg two-pass loudnorm backend")
    parser.add_argument("--fcproj", default=None, help="FilmCraft project for the in-editor backend")
    parser.add_argument("--sequence", default=None, help="sequence name (required when the project has more than one)")
    parser.add_argument("--track", default=DEFAULT_TRACK, help=f"audio track name (default '{DEFAULT_TRACK}')")
    parser.add_argument("--target-i", type=float, default=DEFAULT_TARGET_I, help=f"loudnorm integrated target (default {DEFAULT_TARGET_I:g})")
    parser.add_argument("--target-tp", type=float, default=DEFAULT_TARGET_TP, help=f"loudnorm true-peak target (default {DEFAULT_TARGET_TP:g})")
    parser.add_argument("--target-lra", type=float, default=DEFAULT_TARGET_LRA, help=f"loudnorm LRA target (default {DEFAULT_TARGET_LRA:g})")
    parser.add_argument("--target-lufs", type=float, default=DEFAULT_TARGET_LUFS, help=f"Auto-Match target in LUFS (default {DEFAULT_TARGET_LUFS:g})")
    parser.add_argument("--out", default=None, help="output file (video backend) or project copy (fcproj backend)")
    parser.add_argument("--in-place", action="store_true", help="modify the input project (fcproj backend only)")
    parser.add_argument("--dry-run", action="store_true", help="print the commands without running them (fcproj backend only; works without filmcraft-cli)")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def format_number(value):
    """Format a float as an integer when it is whole, else as-is."""
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def load_json(path):
    """Read a UTF-8 JSON file, raising a named error when it is missing or unreadable."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise LoudnessError(f"cannot read '{path}': file not found")
    except json.JSONDecodeError as exc:
        raise LoudnessError(f"cannot read '{path}': not valid JSON ({exc})")
    except OSError as exc:
        raise LoudnessError(f"cannot read '{path}': {exc}")


def parse_loudnorm_json(text):
    """Extract the loudnorm measurement object ffmpeg prints to stderr."""
    match = LOUDNORM_JSON.search(text or "")
    if not match:
        raise LoudnessRuntimeError("ffmpeg did not print loudnorm measurements")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise LoudnessRuntimeError(f"could not parse loudnorm measurements: {exc}")


def video_output_path(video, out):
    """Return the output path: ``--out`` when given, else ``<stem>_loud`` with the input's suffix."""
    if out:
        return Path(out).expanduser()
    suffix = video.suffix if video.suffix.lower() in VIDEO_SUFFIXES else ".mp4"
    return video.with_name(video.stem + "_loud" + suffix)


def measure_video(video, target_i, target_tp, target_lra):
    """Run loudnorm pass 1 and return the measured values."""
    filter_spec = f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:print_format=json"
    command = [ffmpeg_path(), "-hide_banner", "-i", str(video), "-af", filter_spec, "-f", "null", "-"]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise LoudnessRuntimeError(f"could not run ffmpeg: {exc}")
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()
        raise LoudnessRuntimeError(f"ffmpeg loudnorm pass 1 failed: {tail[-1] if tail else 'unknown error'}")
    return parse_loudnorm_json(result.stderr)


def encode_video(video, out_path, measured, target_i, target_tp, target_lra):
    """Run loudnorm pass 2 with the measured values, copying the video stream."""
    filter_spec = (
        f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}"
        f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        f":offset={measured.get('target_offset', 0.0)}:linear=true:print_format=summary"
    )
    command = [
        ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-i",
        str(video),
        "-af",
        filter_spec,
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "320k",
        str(out_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise LoudnessRuntimeError(f"could not run ffmpeg: {exc}")
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()
        raise LoudnessRuntimeError(f"ffmpeg loudnorm pass 2 failed: {tail[-1] if tail else 'unknown error'}")


def run_video(video_path, out_path, target_i, target_tp, target_lra):
    """Run the ffmpeg two-pass loudnorm backend and return the summary."""
    started = time.monotonic()
    video = Path(video_path).expanduser()
    if not video.is_file():
        raise LoudnessError(f"video not found: {video}")
    out = video_output_path(video, out_path)
    if out.resolve() == video.resolve():
        raise LoudnessError(f"refusing to overwrite the input video: {video}")
    measured = measure_video(video, target_i, target_tp, target_lra)
    out.parent.mkdir(parents=True, exist_ok=True)
    encode_video(video, out, measured, target_i, target_tp, target_lra)
    return {
        "output": str(out),
        "backend": "ffmpeg",
        "measured": {
            "input_i": float(measured["input_i"]),
            "input_tp": float(measured["input_tp"]),
            "input_lra": float(measured["input_lra"]),
            "input_thresh": float(measured["input_thresh"]),
        },
        "target": {"i": target_i, "tp": target_tp, "lra": target_lra},
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }


def resolve_cli(required=True):
    """Return the filmcraft-cli binary from ``$FILMCRAFT_CLI`` or PATH.

    With ``required=False`` (``--dry-run``) a missing binary is not an error: the
    command lines print with the plain binary name.
    """
    found = cli_path()
    if found:
        return found
    if not required:
        return "filmcraft-cli"
    raise LoudnessRuntimeError("filmcraft-cli not found: set FILMCRAFT_CLI or put filmcraft-cli on PATH")


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
        raise LoudnessError("no sequence found in the project")
    if requested:
        if requested not in sequences:
            found = ", ".join(sorted(sequences)) or "none"
            raise LoudnessError(f"unknown sequence '{requested}' (sequences found: {found})")
        return requested, sequences[requested]
    if len(sequences) > 1:
        found = ", ".join(sorted(sequences))
        raise LoudnessError(f"the project has more than one sequence, pass --sequence (found: {found})")
    name = next(iter(sequences))
    return name, sequences[name]


def select_track(sequence_item, track_name):
    """Return the audio track named ``track_name`` of the sequence."""
    block = (sequence_item.get("kind") or {}).get("Sequence") or {}
    tracks = {entry.get("name"): entry for entry in block.get("audio_tracks") or []}
    if track_name not in tracks:
        found = ", ".join(sorted(str(name) for name in tracks)) or "none"
        raise LoudnessError(f"unknown track '{track_name}' (tracks found: {found})")
    return tracks[track_name]


def voice_clip_ids(track, track_name):
    """Return the item ids of the enabled timeline clips on the track."""
    ids = []
    for clip in track.get("items") or []:
        if not clip.get("enabled", True):
            continue
        item_id = clip.get("item")
        if item_id is None:
            continue
        ids.append(item_id)
    if not ids:
        raise LoudnessError(f"no enabled clip on track '{track_name}'")
    return ids


def clips_argument(clips):
    return "clips=" + json.dumps(clips, separators=(",", ":"))


def build_commands(cli, project, clips, target_lufs):
    """Build the three Essential Sound command lines for the voice clips."""
    clips_arg = clips_argument(clips)
    values = json.dumps(
        {
            "clarity.enabled": True,
            "clarity.dynamics.on": True,
            "clarity.dynamics.amount": DEFAULT_CLARITY_AMOUNT,
        }
    )
    suffix = ["--project", str(project), "--save", "--compact"]
    return [
        [cli, "exec", "essentialSound.setType", "type=dialogue", clips_arg, *suffix],
        [cli, "exec", "essentialSound.autoMatch", f"target={format_number(target_lufs)}", clips_arg, *suffix],
        [cli, "exec", "essentialSound.set", f"values={values}", clips_arg, *suffix],
    ]


def build_inspect_command(cli, project, clips):
    return [cli, "exec", "essentialSound.inspect", clips_argument(clips), "--project", str(project), "--save", "--compact"]


def format_command(command):
    return " ".join(command)


def run_command(command, label):
    """Run one filmcraft-cli command and return its stdout, raising on a non-zero exit."""
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise LoudnessRuntimeError(f"could not run {command[0]}: {exc}")
    if result.returncode != 0:
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        tail = "\n".join(output.splitlines()[-5:])
        raise LoudnessRuntimeError(f"{label} failed (exit {result.returncode}):\n{tail}")
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
            raise LoudnessRuntimeError(f"{label} returned no JSON: {raw[-200:]}")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LoudnessRuntimeError(f"{label} returned invalid JSON: {exc}")
    if isinstance(data, dict) and data.get("ok") is False:
        raise LoudnessRuntimeError(f"{label} returned ok=false: {raw[-200:]}")
    return data


def extract_measured(reply):
    """Pull the Loudness block fields out of an inspect reply."""
    result = reply.get("result") if isinstance(reply, dict) else None
    if not isinstance(result, dict):
        result = reply if isinstance(reply, dict) else {}
    loudness = result.get("loudness") if isinstance(result.get("loudness"), dict) else result
    return {
        "measured_lufs": loudness.get("measured_lufs"),
        "target_lufs": loudness.get("target_lufs"),
        "match_gain": loudness.get("match_gain"),
    }


def run_fcproj(project_path, sequence_name, track_name, target_lufs, out_path, in_place, dry_run):
    """Apply the Essential Sound recipe through filmcraft-cli and return the summary."""
    started = time.monotonic()
    project = Path(project_path).expanduser()
    if not project.is_file():
        raise LoudnessError(f"project not found: {project}")
    data = load_json(project)
    items = (data.get("project") or {}).get("items")
    if not isinstance(items, dict):
        raise LoudnessError(f"'{project}' is not a FilmCraft project (no project.items)")
    name, sequence_item = select_sequence(items, sequence_name)
    track = select_track(sequence_item, track_name)
    clips = voice_clip_ids(track, track_name)

    if in_place:
        target = project
    else:
        target = Path(out_path).expanduser() if out_path else project.with_name(project.stem + "_loudness.fcproj")

    cli = resolve_cli(required=not dry_run)
    commands = build_commands(cli, target, clips, target_lufs)
    inspect_command = build_inspect_command(cli, target, clips)

    if dry_run:
        return {
            "output": str(target),
            "backend": "filmcraft",
            "sequence": name,
            "track": track_name,
            "clips": clips,
            "measured": None,
            "target_lufs": target_lufs,
            "commands": [format_command(command) for command in commands],
            "dry_run": True,
            "wall_time_s": round(time.monotonic() - started, 3),
            "llm_cost_usd": 0.0,
        }

    if not in_place:
        shutil.copy2(project, target)

    for command, label in zip(commands, CLI_COMMAND_LABELS):
        parse_reply(run_command(command, label), label)

    inspect_reply = parse_reply(run_command(inspect_command, "essentialSound.inspect"), "essentialSound.inspect")
    measured = extract_measured(inspect_reply)

    return {
        "output": str(target),
        "backend": "filmcraft",
        "sequence": name,
        "track": track_name,
        "clips": clips,
        "measured": measured,
        "target_lufs": target_lufs,
        "commands": [format_command(command) for command in commands],
        "dry_run": False,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }


def print_summary(summary):
    if summary["backend"] == "ffmpeg":
        measured = summary["measured"]
        target = summary["target"]
        print(f"measured: I={measured['input_i']} LUFS, TP={measured['input_tp']} dBTP, LRA={measured['input_lra']} LU")
        print(f"target: I={target['i']} LUFS, TP={target['tp']} dBTP, LRA={target['lra']} LU")
        print(f"written: {summary['output']}")
        return
    if summary.get("dry_run"):
        for command in summary["commands"]:
            print(command)
        return
    measured = summary["measured"] or {}
    print(
        f"measured: {measured.get('measured_lufs')} LUFS, "
        f"target {measured.get('target_lufs')} LUFS, gain {measured.get('match_gain')} dB"
    )
    print(f"written: {summary['output']}")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if bool(args.video) == bool(args.fcproj):
            raise LoudnessError("exactly one of --video and --fcproj is required")
        if args.video:
            if args.in_place:
                raise LoudnessError("--in-place is only valid with --fcproj")
            if args.dry_run:
                raise LoudnessError("--dry-run is only valid with --fcproj")
            summary = run_video(args.video, args.out, args.target_i, args.target_tp, args.target_lra)
        else:
            if args.out and args.in_place:
                raise LoudnessError("--out and --in-place are mutually exclusive")
            summary = run_fcproj(
                args.fcproj,
                args.sequence,
                args.track,
                args.target_lufs,
                args.out,
                args.in_place,
                args.dry_run,
            )
    except LoudnessError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return exc.code

    if args.json:
        print(json.dumps(summary))
    else:
        print_summary(summary)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
