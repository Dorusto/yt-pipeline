#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run the whole Mode B flow on a folder of OBS recordings in one command.

Usage:
    python3 scripts/mode_b.py FOLDER [--out-dir DIR] [--backend {whisper,mock}]
        [--config FILE] [--llm] [--force] [--json]

``mode_b.py`` is the single entry point for the Mode B flow: it scans a folder
of OBS recordings, transcribes every session that has no words JSON yet, writes
the sessions manifest, runs ``rough_cut.py``, writes ``chapters.json`` with
``chapters.py`` and exports the result with ``export_filmcraft.py``. It chains
the station CLIs as subprocesses and imports none of them. Every step is
skipped when its output already exists (``--force`` re-runs everything); a
failing step stops the run and the next run resumes at it. Originals are
read-only; everything is written under ``--out-dir`` (default
``<FOLDER>/mode_b/``).

Two folder layouts are accepted. The subfolder layout has a ``camera/`` folder
and an ``ecran/`` (or ``screen/``) folder; the flat layout keeps the camera and
screen files directly in ``FOLDER``. A camera file is paired with the screen
file whose name timestamp is nearest within ``pair_tolerance_s`` (default
10 s), so the screen recording that starts a second or two before the camera
still pairs. The camera-to-screen sync offset is computed in one place,
``rough_cut.py`` (sound measurement when the voice tracks correlate, else the
name timestamps plus the container start time); the manifest never carries a
name-only ``sync_offset_s``.
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

import yaml

from common.media import parse_stamp, stem_timestamp, video_files

TOOL = "scripts/mode_b.py"
REPO_ROOT = Path(__file__).resolve().parents[1]
CAMERA_PREFIX = "camera "
BACKENDS = ("whisper", "mock")
WORDS_SUFFIXES = ("_words.json", ".json")
SCREEN_DIR_CANDIDATES = ("ecran", "screen")
DEFAULT_MODE_B_CONFIG = {
    "camera_dir": "camera",
    "screen_dir": None,
    "words_dir": "json",
    "pair_tolerance_s": 10.0,
}


class ModeBError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


def load_mode_b_config(path=None):
    """Return the ``mode_b:`` settings from the config YAML, or the defaults."""
    config = dict(DEFAULT_MODE_B_CONFIG)
    if path is None:
        return config
    path = Path(path)
    if not path.is_file():
        raise ModeBError(f"config not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ModeBError(f"config '{path}': {exc}") from exc
    if data is None:
        return config
    if not isinstance(data, dict):
        raise ModeBError(f"config '{path}': root must be a mapping")
    section = data.get("mode_b")
    if section is None:
        return config
    if not isinstance(section, dict):
        raise ModeBError(f"config '{path}': mode_b must be a mapping")
    for key in ("camera_dir", "screen_dir", "words_dir"):
        if key in section:
            value = section[key]
            if not isinstance(value, str) or not value:
                raise ModeBError(f"config '{path}': mode_b.{key} must be a non-empty string")
            config[key] = value
    if "pair_tolerance_s" in section:
        value = section["pair_tolerance_s"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ModeBError(
                f"config '{path}': mode_b.pair_tolerance_s must be a non-negative number"
            )
        config["pair_tolerance_s"] = float(value)
    return config


def resolve_layout(folder, config):
    """Return ``(kind, camera_dir, screen_dir)`` for the folder layout."""
    camera_dir = folder / config["camera_dir"]
    if camera_dir.is_dir():
        screen_name = config["screen_dir"]
        if screen_name is None:
            for candidate in SCREEN_DIR_CANDIDATES:
                if (folder / candidate).is_dir():
                    screen_name = candidate
                    break
        screen_dir = folder / screen_name if screen_name else None
        return "subfolder", camera_dir, screen_dir
    return "flat", folder, folder


def layout_camera_files(kind, camera_dir, screen_dir):
    """Return the camera files for the resolved layout."""
    if kind == "subfolder":
        return video_files(camera_dir, exclude_matte=True)
    return [
        path
        for path in video_files(camera_dir, exclude_matte=True)
        if path.stem.startswith(CAMERA_PREFIX)
    ]


def layout_screen_files(kind, camera_dir, screen_dir):
    """Return the screen files for the resolved layout."""
    if kind == "subfolder":
        return video_files(screen_dir, exclude_matte=True)
    return [
        path
        for path in video_files(screen_dir, exclude_matte=True)
        if not path.stem.startswith(CAMERA_PREFIX)
    ]


def session_sort_key(session):
    """Chronological order by the stem timestamp, then by stem; no stamp sorts last."""
    stamp = stem_timestamp(session["stem"])
    if stamp is None:
        return (1, "", session["stem"])
    return (0, stamp, session["stem"])


def pair_screens(camera_files, screen_files, tolerance, warnings):
    """Pair cameras with the nearest screen by name timestamp; return ``(pairs, used)``."""
    camera_stamps = {camera: parse_stamp(camera.stem) for camera in camera_files}
    screen_stamps = {screen: parse_stamp(screen.stem) for screen in screen_files}
    candidates = []
    for camera in camera_files:
        camera_stamp = camera_stamps[camera]
        if camera_stamp is None:
            continue
        for screen in screen_files:
            screen_stamp = screen_stamps[screen]
            if screen_stamp is None:
                continue
            delta = abs((camera_stamp - screen_stamp).total_seconds())
            if delta <= tolerance:
                candidates.append(
                    (delta, camera_stamp, screen_stamp, camera.name, screen.name, camera, screen)
                )
    candidates.sort(key=lambda item: item[:5])
    pairs = {}
    used = set()
    for _, _, _, _, _, camera, screen in candidates:
        if camera in pairs or screen in used:
            continue
        pairs[camera] = screen
        used.add(screen)
    for camera, screen in pairs.items():
        camera_stamp = camera_stamps[camera]
        deltas = sorted(
            abs((camera_stamp - screen_stamps[other]).total_seconds())
            for other in screen_files
            if screen_stamps[other] is not None
            and abs((camera_stamp - screen_stamps[other]).total_seconds()) <= tolerance
        )
        if len(deltas) > 1 and deltas[0] == deltas[1]:
            warnings.append(
                f"several screen files match '{camera.name}': using '{screen.name}'"
            )
    return pairs, used


def exact_screen_for(camera, screen_files, used, warnings):
    """Return the screen whose stem equals the camera stem minus the prefix, or None."""
    if camera.stem.startswith(CAMERA_PREFIX):
        screen_stem = camera.stem[len(CAMERA_PREFIX):]
    else:
        screen_stem = camera.stem
    matches = sorted(
        (
            screen
            for screen in screen_files
            if screen not in used and screen.stem == screen_stem
        ),
        key=lambda path: path.name,
    )
    if not matches:
        return None
    if len(matches) > 1:
        warnings.append(
            f"several screen files match '{camera.name}': using '{matches[0].name}'"
        )
    return matches[0]


def find_words(folder, words_dir, stem, words_subdir="json"):
    """Return the session's words JSON from the folder, its words dir or the out dir."""
    directories = [folder]
    if words_subdir:
        directories.append(folder / words_subdir)
    directories.append(words_dir)
    for directory in directories:
        for suffix in WORDS_SUFFIXES:
            candidate = directory / f"{stem}{suffix}"
            if candidate.is_file():
                return candidate
    return None


def scan_sessions(folder, out_dir, warnings, config=None):
    """Return the sessions in the folder: camera, optional screen, words and offset."""
    config = config or dict(DEFAULT_MODE_B_CONFIG)
    kind, camera_dir, screen_dir = resolve_layout(folder, config)
    cameras = layout_camera_files(kind, camera_dir, screen_dir)
    screens = layout_screen_files(kind, camera_dir, screen_dir)
    pairs, used = pair_screens(cameras, screens, config["pair_tolerance_s"], warnings)
    words_dir = out_dir / "words"
    sessions = []
    for camera in cameras:
        screen = pairs.get(camera)
        if screen is None:
            screen = exact_screen_for(camera, screens, used, warnings)
            if screen is not None:
                used.add(screen)
        sessions.append(
            {
                "stem": camera.stem,
                "camera": camera,
                "screen": screen,
                "words": find_words(folder, words_dir, camera.stem, config["words_dir"]),
            }
        )
    for screen in screens:
        if screen not in used:
            warnings.append(f"unpaired screen file: '{screen.name}'")
    sessions.sort(key=session_sort_key)
    return sessions


def pairing_summary(sessions, folder, config):
    """Return the pairing counts after the scan."""
    kind, camera_dir, screen_dir = resolve_layout(folder, config)
    screens = layout_screen_files(kind, camera_dir, screen_dir)
    paired_screens = {
        session["screen"] for session in sessions if session["screen"] is not None
    }
    return {
        "paired": sum(1 for session in sessions if session["screen"] is not None),
        "camera_only": sum(1 for session in sessions if session["screen"] is None),
        "screen_only": sum(1 for screen in screens if screen not in paired_screens),
    }


def consolidate_words(sessions, words_dir):
    """Copy every session's words JSON into ``words_dir`` when it lives elsewhere."""
    for session in sessions:
        source = session["words"]
        if source is None or source.parent == words_dir:
            continue
        stem = session["stem"]
        if any((words_dir / f"{stem}{suffix}").is_file() for suffix in WORDS_SUFFIXES):
            continue
        words_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, words_dir / source.name)


def manifest_document(sessions):
    """Build the sessions manifest document (validated by rough_cut.py)."""
    entries = []
    for session in sessions:
        entry = {"camera": str(session["camera"].resolve())}
        if session["screen"] is not None:
            entry["screen"] = str(session["screen"].resolve())
        entry["words"] = str(session["words"].resolve())
        entries.append(entry)
    return {"sessions": entries}


def write_manifest(path, sessions):
    """Write the sessions manifest atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(manifest_document(sessions), sort_keys=False, allow_unicode=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def run_command(command):
    """Run one station CLI as a subprocess; return ``(returncode, stdout, stderr)``."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, cwd=str(REPO_ROOT)
        )
    except OSError as exc:
        return 1, "", str(exc)
    return result.returncode, result.stdout or "", result.stderr or ""


def parse_child_json(stdout):
    """Return the JSON object a child printed, or None."""
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def step_error(step, name, code, stderr):
    """Build the user-facing message for a failed station."""
    lines = (stderr or "").strip().splitlines()
    tail = lines[-1] if lines else ""
    message = f"{step} failed on '{name}' (exit {code})"
    if tail:
        message += f": {tail}"
    return message


def transcribe_command(session, words_dir, backend):
    return [
        sys.executable,
        "scripts/transcribe.py",
        str(session["camera"]),
        "--out-dir",
        str(words_dir),
        "--name",
        session["stem"],
        "--backend",
        backend,
        "--json",
    ]


def rough_cut_command(sessions_path, edit_path, config, use_llm):
    command = [
        sys.executable,
        "scripts/rough_cut.py",
        str(sessions_path),
        "--out",
        str(edit_path),
    ]
    if config:
        command += ["--config", str(config)]
    if use_llm:
        command.append("--llm")
    command.append("--json")
    return command


def export_command(edit_path, project_path, sessions, chapters_path=None):
    command = [
        sys.executable,
        "scripts/export_filmcraft.py",
        str(edit_path),
        "--out",
        str(project_path),
    ]
    for session in sessions:
        if session["screen"] is not None:
            command += ["--screen", session["screen"].name]
    if chapters_path is not None and Path(chapters_path).is_file():
        command += ["--chapters", str(chapters_path)]
    command.append("--json")
    return command


def chapters_command(edit_path, words_dir, chapters_path):
    return [
        sys.executable,
        "scripts/chapters.py",
        str(edit_path),
        str(words_dir),
        "--out",
        str(chapters_path),
        "--json",
    ]


def run(folder, out_dir, backend, config, use_llm, force, mode_b_config=None):
    """Run the Mode B chain and return the ``--json`` summary object."""
    started = time.monotonic()
    warnings = []
    mode_b_config = mode_b_config or dict(DEFAULT_MODE_B_CONFIG)
    sessions = scan_sessions(folder, out_dir, warnings, mode_b_config)
    pairing = pairing_summary(sessions, folder, mode_b_config)
    words_dir = out_dir / "words"
    transcribed = 0
    skipped_transcribe = 0

    for session in sessions:
        if force or session["words"] is None:
            code, stdout, stderr = run_command(transcribe_command(session, words_dir, backend))
            if code != 0:
                raise ModeBError(step_error("transcribe", session["camera"].name, code, stderr))
            session["words"] = words_dir / f"{session['stem']}_words.json"
            transcribed += 1
        else:
            skipped_transcribe += 1

    consolidate_words(sessions, words_dir)

    sessions_path = out_dir / "sessions.yaml"
    write_manifest(sessions_path, sessions)

    edit_path = out_dir / "edit.json"
    rough_cut_skipped = edit_path.is_file() and not force
    llm_cost = 0.0
    if not rough_cut_skipped:
        code, stdout, stderr = run_command(
            rough_cut_command(sessions_path, edit_path, config, use_llm)
        )
        if code != 0:
            raise ModeBError(step_error("rough_cut", sessions_path.name, code, stderr))
        data = parse_child_json(stdout)
        if isinstance(data, dict):
            value = data.get("llm_cost_usd")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                llm_cost = float(value)
            warnings.extend(data.get("warnings") or [])

    project_path = out_dir / "project.fcproj"
    chapters_path = out_dir / "chapters.json"
    chapters_skipped = int(chapters_path.is_file() and not force)
    if not chapters_skipped:
        code, stdout, stderr = run_command(
            chapters_command(edit_path, words_dir, chapters_path)
        )
        if code != 0:
            raise ModeBError(step_error("chapters", edit_path.name, code, stderr))
        data = parse_child_json(stdout)
        if isinstance(data, dict):
            value = data.get("llm_cost_usd")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                llm_cost += float(value)
            warnings.extend(data.get("warnings") or [])

    export_skipped = project_path.is_file() and not force
    if not export_skipped:
        code, stdout, stderr = run_command(
            export_command(edit_path, project_path, sessions, chapters_path)
        )
        if code != 0:
            raise ModeBError(step_error("export", edit_path.name, code, stderr))
        data = parse_child_json(stdout)
        if isinstance(data, dict):
            warnings.extend(data.get("warnings") or [])

    wall_time_s = round(time.monotonic() - started, 3)
    return {
        "ok": True,
        "outputs": {
            "words_dir": str(words_dir),
            "sessions": str(sessions_path),
            "edit": str(edit_path),
            "chapters": str(chapters_path),
            "project": str(project_path),
        },
        "sessions": len(sessions),
        "pairing": pairing,
        "transcribed": transcribed,
        "skipped": {
            "transcribe": skipped_transcribe,
            "rough_cut": rough_cut_skipped,
            "chapters": chapters_skipped,
            "export": export_skipped,
        },
        "warnings": warnings,
        "wall_time_s": wall_time_s,
        "llm_cost_usd": round(llm_cost, 8),
    }


def print_human(summary, stream):
    print(
        f"mode_b: {summary['sessions']} session(s), {summary['transcribed']} transcribed",
        file=stream,
    )
    print(f"sessions: {summary['outputs']['sessions']}", file=stream)
    print(f"edit: {summary['outputs']['edit']}", file=stream)
    print(f"project: {summary['outputs']['project']}", file=stream)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mode_b.py",
        usage=(
            "mode_b.py FOLDER [--out-dir DIR] [--backend {whisper,mock}]"
            " [--config FILE] [--llm] [--force] [--json]"
        ),
        description=(
            "Run the whole Mode B flow on a folder of OBS recordings: transcribe what has no "
            "words JSON yet, rough-cut it and export a FilmCraft project. Two layouts are "
            "accepted: a camera/ folder plus an ecran/ (or screen/) folder, or the flat layout "
            "with the camera and screen files directly in FOLDER. A camera file is paired with "
            "the screen file whose name timestamp is nearest within pair_tolerance_s (default "
            "10 s), so a screen recording that starts a second or two before the camera still "
            "pairs; rough_cut.py computes the sync offset (by sound when possible, else the "
            "name timestamps plus the container start time)."
        ),
        epilog=(
            "examples:\n"
            "  python3 scripts/mode_b.py ~/media/obs --json\n"
            "  python3 scripts/mode_b.py ~/media/obs --backend mock --force --json\n"
            "  python3 scripts/mode_b.py ~/media/second-brain --config config.yaml --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "folder",
        help=(
            "folder with the OBS recordings: a camera/ folder plus an ecran/ (or screen/) "
            "folder, or the flat layout with the camera and screen files directly inside"
        ),
    )
    parser.add_argument("--out-dir", default=None, help="output folder (default: <FOLDER>/mode_b)")
    parser.add_argument(
        "--backend",
        default="whisper",
        metavar="{whisper,mock}",
        help="transcribe.py backend: whisper (real model) or mock (deterministic, offline); default: whisper",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="YAML with the mode_b: folder/pairing keys and the cut rules passed to rough_cut.py",
    )
    parser.add_argument("--llm", action="store_true", help="propose extra cuts in rough_cut.py (never applied)")
    parser.add_argument("--force", action="store_true", help="re-run every step even when its output exists")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        folder = Path(args.folder).expanduser().resolve()
        if not folder.exists():
            raise ModeBError(f"folder not found: {folder}")
        if not folder.is_dir():
            raise ModeBError(f"not a folder: {folder}")
        config = Path(args.config).expanduser() if args.config else None
        mode_b_config = load_mode_b_config(config)
        kind, camera_dir, screen_dir = resolve_layout(folder, mode_b_config)
        if not layout_camera_files(kind, camera_dir, screen_dir):
            raise ModeBError(f"no camera files in {folder}")
        out_dir = (
            Path(args.out_dir).expanduser().resolve() if args.out_dir else folder / "mode_b"
        )
        summary = run(folder, out_dir, args.backend, config, args.llm, args.force, mode_b_config)
    except ModeBError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        return 2

    if args.json:
        print(json.dumps(summary))
        print_human(summary, sys.stderr)
    else:
        print_human(summary, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
