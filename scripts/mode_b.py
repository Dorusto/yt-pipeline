#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run the whole Mode B flow on a folder of OBS recordings in one command.

Usage:
    python3 scripts/mode_b.py FOLDER [--out-dir DIR] [--backend {whisper,mock}]
        [--config FILE] [--llm] [--force] [--json]

``mode_b.py`` is the single entry point for the Mode B flow: it scans a folder
of OBS recordings (a ``camera ...`` file plus an optional sibling screen file),
transcribes every session that has no words JSON yet, writes the sessions
manifest, runs ``rough_cut.py`` and exports the result with
``export_filmcraft.py``. It chains the three station CLIs as subprocesses and
imports none of them. Every step is skipped when its output already exists
(``--force`` re-runs everything); a failing step stops the run and the next run
resumes at it. Originals are read-only; everything is written under ``--out-dir``
(default ``<FOLDER>/mode_b/``).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

TOOL = "scripts/mode_b.py"
REPO_ROOT = Path(__file__).resolve().parents[1]
CAMERA_PREFIX = "camera "
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v"}
BACKENDS = ("whisper", "mock")
FILENAME_TIMESTAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _](\d{2})-(\d{2})-(\d{2})")
WORDS_SUFFIXES = ("_words.json", ".json")


class ModeBError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


def camera_files(folder):
    """Return the camera files directly inside ``folder``, sorted by name."""
    return sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file()
            and path.stem.startswith(CAMERA_PREFIX)
            and path.suffix.lower() in VIDEO_EXTENSIONS
        ),
        key=lambda path: path.name,
    )


def screen_for(camera, folder, warnings):
    """Return the sibling screen file for ``camera``, or None when there is none."""
    screen_stem = camera.stem[len(CAMERA_PREFIX):]
    matches = sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file()
            and path.stem == screen_stem
            and path.suffix.lower() in VIDEO_EXTENSIONS
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


def find_words(folder, words_dir, stem):
    """Return the session's words JSON from the folder or the words dir, or None."""
    for directory in (folder, words_dir):
        for suffix in WORDS_SUFFIXES:
            candidate = directory / f"{stem}{suffix}"
            if candidate.is_file():
                return candidate
    return None


def stem_timestamp(stem):
    """Return the first ``YYYY-MM-DD HH-MM-SS`` in ``stem`` as a sortable string."""
    match = FILENAME_TIMESTAMP.search(stem)
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    return f"{year}-{month}-{day} {hour}-{minute}-{second}"


def session_sort_key(session):
    """Chronological order by the stem timestamp, then by stem; no stamp sorts last."""
    stamp = stem_timestamp(session["stem"])
    if stamp is None:
        return (1, "", session["stem"])
    return (0, stamp, session["stem"])


def scan_sessions(folder, out_dir, warnings):
    """Return the sessions in the folder: camera, optional screen and words JSON."""
    words_dir = out_dir / "words"
    sessions = []
    for camera in camera_files(folder):
        stem = camera.stem
        sessions.append(
            {
                "stem": stem,
                "camera": camera,
                "screen": screen_for(camera, folder, warnings),
                "words": find_words(folder, words_dir, stem),
            }
        )
    sessions.sort(key=session_sort_key)
    return sessions


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


def export_command(edit_path, project_path, sessions):
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
    command.append("--json")
    return command


def run(folder, out_dir, backend, config, use_llm, force):
    """Run the Mode B chain and return the ``--json`` summary object."""
    started = time.monotonic()
    warnings = []
    sessions = scan_sessions(folder, out_dir, warnings)
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
    export_skipped = project_path.is_file() and not force
    if not export_skipped:
        code, stdout, stderr = run_command(export_command(edit_path, project_path, sessions))
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
            "project": str(project_path),
        },
        "sessions": len(sessions),
        "transcribed": transcribed,
        "skipped": {
            "transcribe": skipped_transcribe,
            "rough_cut": rough_cut_skipped,
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
            "words JSON yet, rough-cut it and export a FilmCraft project."
        ),
        epilog=(
            "examples:\n"
            "  python3 scripts/mode_b.py ~/media/obs --json\n"
            "  python3 scripts/mode_b.py ~/media/obs --backend mock --force --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("folder", help="folder with the OBS recordings (camera + optional screen files)")
    parser.add_argument("--out-dir", default=None, help="output folder (default: <FOLDER>/mode_b)")
    parser.add_argument(
        "--backend",
        default="whisper",
        metavar="{whisper,mock}",
        help="transcribe.py backend: whisper (real model) or mock (deterministic, offline); default: whisper",
    )
    parser.add_argument("--config", default=None, help="cut-rules YAML passed to rough_cut.py")
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
        if not camera_files(folder):
            raise ModeBError(f"no camera files in {folder}")
        out_dir = (
            Path(args.out_dir).expanduser().resolve() if args.out_dir else folder / "mode_b"
        )
        config = Path(args.config).expanduser() if args.config else None
        summary = run(folder, out_dir, args.backend, config, args.llm, args.force)
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
