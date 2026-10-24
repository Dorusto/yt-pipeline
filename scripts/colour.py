#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Apply a basic colour recipe to the camera clips of a FilmCraft project.

Measures each recording session's average white balance, exposure and light
contrast on a few frames of the camera clips and applies one Lumetri Basic
Correction per session (temperature, tint, exposure, contrast) to every camera
clip of that session, through the headless ``filmcraft-cli``. The input project
is never modified unless ``--in-place`` is given; without it the tool works on a
copy.

No model is called; ``llm_cost_usd`` is always 0.0.

Usage:
    python3 scripts/colour.py --fcproj PROJECT.fcproj [--sequence NAME]
        [--track "Camera 1"] [--session-gap-min 180] [--frames-per-clip 3]
        [--target-luma 0.5] [--target-contrast 0.18]
        [--out FILE | --in-place] [--dry-run] [--json]
"""

import argparse
import json
import math
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from common.constants import TICKS_PER_SECOND
from common.errors import report_error
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.filmcraft import cli_path
from common.frames import read_frame_pairs
from common.media import parse_stamp
from common.summary import summary_fields

TARGET_LUMA = 0.5
TARGET_CONTRAST = 0.18
WB_GAIN = 150.0
CONTRAST_GAIN = 100.0
EXPOSURE_MIN, EXPOSURE_MAX = -5.0, 5.0
PARAM_MIN, PARAM_MAX = -100.0, 100.0
DEFAULT_TRACK = "Camera 1"
DEFAULT_SESSION_GAP_MIN = 180.0
DEFAULT_FRAMES_PER_CLIP = 3
LUMA_WEIGHTS = (0.2126, 0.7152, 0.0722)
PARAMS = ("temperature", "tint", "exposure", "contrast")


class ColourError(Exception):
    """A user-facing error; ``code`` is the process exit code (EXIT_USAGE for user errors)."""

    def __init__(self, message, code=EXIT_USAGE):
        super().__init__(message)
        self.code = code


class ColourRuntimeError(ColourError):
    """A failure after the work started; exits with EXIT_RUNTIME."""

    def __init__(self, message):
        super().__init__(message, code=EXIT_RUNTIME)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="colour.py",
        description=(
            "Apply a basic colour recipe to the camera clips of a FilmCraft project: "
            "one Lumetri Basic Correction per recording session, through filmcraft-cli."
        ),
        epilog=(
            "examples:\n"
            "  python3 scripts/colour.py --fcproj project.fcproj --sequence Main --json\n"
            "  python3 scripts/colour.py --fcproj project.fcproj --dry-run\n"
            "  python3 scripts/colour.py --fcproj project.fcproj --in-place\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--fcproj", required=True, help="FilmCraft project to grade")
    parser.add_argument("--sequence", default=None, help="sequence name (required when the project has more than one)")
    parser.add_argument("--track", default=DEFAULT_TRACK, help=f"video track holding the camera clips (default '{DEFAULT_TRACK}')")
    parser.add_argument("--session-gap-min", type=float, default=DEFAULT_SESSION_GAP_MIN, help=f"minutes between clips that start a new session (default {DEFAULT_SESSION_GAP_MIN:g})")
    parser.add_argument("--frames-per-clip", type=int, default=DEFAULT_FRAMES_PER_CLIP, help=f"frames sampled per clip (default {DEFAULT_FRAMES_PER_CLIP})")
    parser.add_argument("--target-luma", type=float, default=TARGET_LUMA, help=f"target average luma, 0..1 (default {TARGET_LUMA:g})")
    parser.add_argument("--target-contrast", type=float, default=TARGET_CONTRAST, help=f"target luma standard deviation, 0..1 (default {TARGET_CONTRAST:g})")
    parser.add_argument("--out", default=None, help="output project copy (default <stem>_colour.fcproj)")
    parser.add_argument("--in-place", action="store_true", help="modify the input project")
    parser.add_argument("--dry-run", action="store_true", help="print the JSONL commands without running them (works without filmcraft-cli)")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def clamp(value, low, high):
    """Return ``value`` limited to the ``[low, high]`` range."""
    return max(low, min(high, value))


def load_json(path):
    """Read a UTF-8 JSON file, raising a named error when it is missing or unreadable."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise ColourError(f"cannot read '{path}': file not found")
    except json.JSONDecodeError as exc:
        raise ColourError(f"cannot read '{path}': not valid JSON ({exc})")
    except OSError as exc:
        raise ColourError(f"cannot read '{path}': {exc}")


def measure_frames(frames):
    """Average the RGB means, luma and luma standard deviation over BGR frames."""
    if not frames:
        raise ColourError("no frames to measure")
    mean_r = mean_g = mean_b = luma = contrast = 0.0
    for frame in frames:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(np.float64) / 255.0
        mean_r += float(rgb[:, :, 0].mean())
        mean_g += float(rgb[:, :, 1].mean())
        mean_b += float(rgb[:, :, 2].mean())
        luma_frame = LUMA_WEIGHTS[0] * rgb[:, :, 0] + LUMA_WEIGHTS[1] * rgb[:, :, 1] + LUMA_WEIGHTS[2] * rgb[:, :, 2]
        luma += float(luma_frame.mean())
        contrast += float(luma_frame.std())
    count = len(frames)
    return {
        "mean_r": mean_r / count,
        "mean_g": mean_g / count,
        "mean_b": mean_b / count,
        "luma": luma / count,
        "contrast": contrast / count,
    }


def compute_correction(measured, target_luma, target_contrast):
    """Grey-world white balance plus exposure and contrast toward the targets."""
    mean_r = measured["mean_r"]
    mean_g = measured["mean_g"]
    mean_b = measured["mean_b"]
    grey = (mean_r + mean_g + mean_b) / 3
    if mean_r <= 0 or mean_g <= 0 or mean_b <= 0:
        temperature = tint = 0.0
    else:
        r_gain, g_gain, b_gain = grey / mean_r, grey / mean_g, grey / mean_b
        temperature = clamp((r_gain / b_gain - 1.0) * WB_GAIN, PARAM_MIN, PARAM_MAX)
        tint = clamp((math.sqrt(r_gain * b_gain) / g_gain - 1.0) * WB_GAIN, PARAM_MIN, PARAM_MAX)
    exposure = clamp(math.log2(target_luma / max(measured["luma"], 1e-6)), EXPOSURE_MIN, EXPOSURE_MAX)
    contrast = clamp((target_contrast - measured["contrast"]) / target_contrast * CONTRAST_GAIN, PARAM_MIN, PARAM_MAX)
    return {
        "temperature": round(temperature, 2),
        "tint": round(tint, 2),
        "exposure": round(exposure, 2),
        "contrast": round(contrast, 2),
    }


def capture_time(path, name):
    """Return the clip's capture time from the file-name stamp, else the file mtime, else None."""
    path = Path(path)
    if not path.exists():
        return None
    stamp = parse_stamp(name)
    if stamp is not None:
        return stamp
    return datetime.fromtimestamp(path.stat().st_mtime)


def group_sessions(clips, gap_min):
    """Group clips sorted by capture time into sessions split on a gap over ``gap_min`` minutes."""
    ordered = sorted(clips, key=lambda clip: clip["capture_time"])
    sessions = []
    for clip in ordered:
        if sessions and (clip["capture_time"] - sessions[-1][-1]["capture_time"]).total_seconds() <= gap_min * 60:
            sessions[-1].append(clip)
        else:
            sessions.append([clip])
    return sessions


def sample_times(start_s, end_s, count):
    """Return ``count`` evenly spaced sample times inside ``[start_s, end_s]``."""
    if count <= 0 or end_s <= start_s:
        return []
    step = (end_s - start_s) / count
    return [start_s + step * (index + 0.5) for index in range(count)]


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
        raise ColourError("no sequence found in the project")
    if requested:
        if requested not in sequences:
            found = ", ".join(sorted(sequences)) or "none"
            raise ColourError(f"unknown sequence '{requested}' (sequences found: {found})")
        return requested, sequences[requested]
    if len(sequences) > 1:
        found = ", ".join(sorted(sequences))
        raise ColourError(f"the project has more than one sequence, pass --sequence (found: {found})")
    name = next(iter(sequences))
    return name, sequences[name]


def select_track(sequence_item, track_name):
    """Return the video track named ``track_name`` of the sequence."""
    block = (sequence_item.get("kind") or {}).get("Sequence") or {}
    tracks = {entry.get("name"): entry for entry in block.get("video_tracks") or []}
    if track_name not in tracks:
        found = ", ".join(sorted(str(name) for name in tracks)) or "none"
        raise ColourError(f"unknown track '{track_name}' (tracks found: {found})")
    return tracks[track_name]


def media_path(items, clip, project_dir):
    """Resolve a timeline clip's media file path, relative to the project's folder."""
    item = items.get(str(clip.get("item")))
    if not isinstance(item, dict):
        return None
    media = ((item.get("kind") or {}).get("Media") or {}).get("media") or {}
    raw = (media.get("File") or {}).get("path")
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = project_dir / path
    return path


def collect_clips(items, track, project_dir, track_name, warnings):
    """Return the enabled clips on the track with a readable media file and a capture time."""
    enabled = [clip for clip in track.get("items") or [] if clip.get("enabled", True)]
    if not enabled:
        raise ColourError(f"no enabled clip on track '{track_name}'")
    clips = []
    for clip in enabled:
        path = media_path(items, clip, project_dir)
        if path is None or not path.is_file():
            warnings.append(f"clip {clip.get('id')}: media file missing ({path})")
            continue
        when = capture_time(path, path.name)
        if when is None:
            warnings.append(f"clip {clip.get('id')}: no capture time ({path})")
            continue
        source_in = clip.get("source_in") or 0
        duration = clip.get("duration") or 0
        clips.append(
            {
                "id": clip.get("id"),
                "path": path,
                "capture_time": when,
                "clip": clip,
                "source_in_s": source_in / TICKS_PER_SECOND,
                "source_end_s": (source_in + duration) / TICKS_PER_SECOND,
            }
        )
    return clips


def read_session_frames(session, frames_per_clip):
    """Read the sampled frames of every clip in a session; return the BGR frames found."""
    frames = []
    for entry in session:
        times = sample_times(entry["source_in_s"], entry["source_end_s"], frames_per_clip)
        try:
            pairs = read_frame_pairs(entry["path"], times, skip_missing=True)
        except Exception as exc:
            raise ColourRuntimeError(f"could not read frames from {entry['path']}: {exc}")
        frames.extend(frame for _t_s, frame in pairs)
    return frames


def has_lumetri(clip):
    """Return True when the clip already carries a ``lumetri`` effect."""
    for effect in clip.get("effects") or []:
        if isinstance(effect, dict) and effect.get("effect") == "lumetri":
            return True
    return False


def build_commands(sessions):
    """Build the JSONL command lines: one apply when needed, then four setParam per clip."""
    lines = []
    for session in sessions:
        correction = session["correction"]
        for entry in session["clips"]:
            clip_id = entry["id"]
            if not has_lumetri(entry["clip"]):
                lines.append(json.dumps({"id": "effects.apply", "params": {"clips": [clip_id], "effect": "lumetri"}}))
            for param in PARAMS:
                lines.append(
                    json.dumps(
                        {
                            "id": "effects.setParam",
                            "params": {"clip": clip_id, "effect": "lumetri", "param": param, "value": correction[param]},
                        }
                    )
                )
    return lines


def resolve_cli(required=True):
    """Return the filmcraft-cli binary from ``$FILMCRAFT_CLI`` or PATH.

    With ``required=False`` (``--dry-run``) a missing binary is not an error.
    """
    found = cli_path()
    if found:
        return found
    if not required:
        return "filmcraft-cli"
    raise ColourRuntimeError("filmcraft-cli not found: set FILMCRAFT_CLI or put filmcraft-cli on PATH")


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


def run_script(cli, target, lines):
    """Run the JSONL script through ``filmcraft-cli run`` and raise on a failed command."""
    command = [cli, "run", "-", "--project", str(target), "--save", "--compact"]
    try:
        result = subprocess.run(command, input="\n".join(lines) + "\n", capture_output=True, text=True)
    except OSError as exc:
        raise ColourRuntimeError(f"could not run {cli}: {exc}")
    replies = parse_replies(result.stdout)
    for reply in replies:
        if isinstance(reply, dict) and reply.get("ok") is False:
            raise ColourRuntimeError(f"command '{reply.get('id')}' failed: {json.dumps(reply)}")
    if result.returncode != 0:
        if replies and isinstance(replies[-1], dict):
            last_id = replies[-1].get("id")
        elif lines:
            last_id = json.loads(lines[0]).get("id")
        else:
            last_id = "unknown"
        tail = (result.stderr or "").strip().splitlines()
        detail = tail[-1] if tail else "unknown error"
        raise ColourRuntimeError(f"filmcraft-cli run failed (exit {result.returncode}) at command '{last_id}': {detail}")


def session_summary(entry, dry_run):
    """Build one session's summary block."""
    return {
        "key": entry["clips"][0]["capture_time"].isoformat(timespec="minutes"),
        "clips": [clip["id"] for clip in entry["clips"]],
        "frames": entry["frames"],
        "measured": None if dry_run else {key: round(value, 4) for key, value in entry["measured"].items()},
        "correction": entry["correction"],
    }


def run_fcproj(project_path, sequence_name, track_name, session_gap_min, frames_per_clip, target_luma, target_contrast, out_path, in_place, dry_run):
    """Apply the colour recipe through filmcraft-cli and return the summary."""
    started = time.monotonic()
    project = Path(project_path).expanduser()
    if not project.is_file():
        raise ColourError(f"project not found: {project}")
    data = load_json(project)
    items = (data.get("project") or {}).get("items")
    if not isinstance(items, dict):
        raise ColourError(f"'{project}' is not a FilmCraft project (no project.items)")
    name, sequence_item = select_sequence(items, sequence_name)
    track = select_track(sequence_item, track_name)

    warnings = []
    clips = collect_clips(items, track, project.parent, track_name, warnings)
    sessions = group_sessions(clips, session_gap_min)

    measured_sessions = []
    for session in sessions:
        frames = read_session_frames(session, frames_per_clip)
        if not frames:
            warnings.append(f"session {session[0]['capture_time'].isoformat(timespec='minutes')}: no readable frame")
            continue
        measured = measure_frames(frames)
        correction = compute_correction(measured, target_luma, target_contrast)
        measured_sessions.append({"clips": session, "measured": measured, "correction": correction, "frames": len(frames)})
    if not measured_sessions:
        raise ColourError("no frames could be read from the camera clips")

    if in_place:
        target = project
    else:
        target = Path(out_path).expanduser() if out_path else project.with_name(project.stem + "_colour.fcproj")
        if target.resolve() == project.resolve():
            raise ColourError(f"refusing to overwrite the input project: {project}")

    commands = build_commands(measured_sessions)
    corrected = [clip["id"] for entry in measured_sessions for clip in entry["clips"]]

    summary = {
        "output": str(target),
        "backend": "filmcraft",
        "sequence": name,
        "track": track_name,
        "sessions": [session_summary(entry, dry_run) for entry in measured_sessions],
        "clips": corrected,
        "commands": commands,
        "dry_run": dry_run,
        "warnings": warnings,
    }

    if dry_run:
        summary.update(summary_fields(started))
        return summary

    cli = resolve_cli(required=True)
    if not in_place:
        shutil.copy2(project, target)
    run_script(cli, target, commands)

    summary.update(summary_fields(started))
    return summary


def print_summary(summary):
    if summary.get("dry_run"):
        for command in summary["commands"]:
            print(command)
        return
    for session in summary["sessions"]:
        correction = session["correction"]
        print(
            f"{session['key']}: {len(session['clips'])} clips, "
            f"temperature {correction['temperature']}, tint {correction['tint']}, "
            f"exposure {correction['exposure']}, contrast {correction['contrast']}"
        )
    print(f"written: {summary['output']}")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.out and args.in_place:
            raise ColourError("--out and --in-place are mutually exclusive")
        if not (0 < args.target_luma <= 1):
            raise ColourError("--target-luma must be in (0, 1]")
        if not (0 < args.target_contrast <= 1):
            raise ColourError("--target-contrast must be in (0, 1]")
        summary = run_fcproj(
            args.fcproj,
            args.sequence,
            args.track,
            args.session_gap_min,
            args.frames_per_clip,
            args.target_luma,
            args.target_contrast,
            args.out,
            args.in_place,
            args.dry_run,
        )
    except ColourError as exc:
        return report_error(exc, exc.code, args.json)

    if args.json:
        print(json.dumps(summary))
    else:
        print_summary(summary)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
