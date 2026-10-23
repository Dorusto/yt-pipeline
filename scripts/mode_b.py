#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run the whole Mode B flow on a folder of OBS recordings in one command.

Usage:
    python3 scripts/mode_b.py FOLDER [--out-dir DIR] [--backend {whisper,mock}]
        [--config FILE] [--llm] [--force] [--check] [--json]

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

Every session with a screen file is also checked for a mistaken recording: when
the screen file looks like a camera recording (a face in most sampled frames, or
high frame similarity to the paired camera file) the run warns loudly and the
``--json`` summary lists it under ``screen_suspects``. When the face detector
cannot load the check is incomplete: the run warns
``screen check incomplete: face detector unavailable: ...`` and the ``--json``
summary sets ``screen_check_incomplete``. ``--check`` runs only these checks —
no transcription, nothing written — so it is fast enough to run right after
recording. Its exit codes are the contract: ``0`` all clear, ``1`` a suspect
screen file was found, ``2`` the check could not run completely (the face
detector could not load), ``3`` a usage error (missing folder, not a folder, no
camera files, bad ``--config``). A suspect found while the detector is
unavailable still exits ``2``. A normal run exits ``0`` on success, ``2`` when
a pipeline step failed and ``3`` on a usage error.
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
import numpy as np
import yaml

from common.faces import (
    DEFAULT_MODEL_PATH,
    FacesError,
    detect_faces,
    ensure_model,
    make_detector,
)
from common.frames import read_frame_pairs
from common.media import parse_stamp, stem_timestamp, video_files
from common.timeline_words import words_file_for

TOOL = "scripts/mode_b.py"
REPO_ROOT = Path(__file__).resolve().parents[1]
CAMERA_PREFIX = "camera "
BACKENDS = ("whisper", "mock")
SCREEN_DIR_CANDIDATES = ("ecran", "screen")
CHECK_SAMPLES = 8
FACE_FRAME_FRAC = 0.6
SIMILARITY_THRESHOLD = 0.85
SIMILARITY_MATCH_WINDOW_S = 2.0
DEFAULT_MODE_B_CONFIG = {
    "camera_dir": "camera",
    "screen_dir": None,
    "words_dir": "json",
    "pair_tolerance_s": 10.0,
}


class ModeBError(Exception):
    """A user-facing error: validation exits 3, a failing step exits 2."""


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
        and stem_timestamp(path.stem) is not None
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
        found = words_file_for(directory, stem)
        if found is not None:
            return found
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
        if words_file_for(words_dir, stem) is not None:
            continue
        words_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, words_dir / source.name)


def load_manifest(path):
    """Return the existing sessions manifest document, or ``None`` when there is none."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ModeBError(f"cannot read sessions manifest '{path}': {exc}")
    return document


def manifest_document(sessions, previous=None):
    """Build the sessions manifest document (validated by rough_cut.py).

    A ``sync_offset_s``/``sync_offset_confidence`` already in ``previous`` (hand-written
    or recorded by ``rough_cut.py``) survives the rewrite: the manifest value
    always wins and ``mode_b.py`` never computes an offset.
    """
    recorded = {}
    for entry in (previous or {}).get("sessions") or []:
        camera = entry.get("camera")
        if not camera:
            continue
        preserved = {
            key: entry[key]
            for key in ("sync_offset_s", "sync_offset_confidence")
            if key in entry
        }
        for key in (str(Path(camera)), str(Path(camera).expanduser().resolve())):
            recorded[key] = preserved
    entries = []
    for session in sessions:
        camera = str(session["camera"].resolve())
        entry = {"camera": camera}
        if session["screen"] is not None:
            entry["screen"] = str(session["screen"].resolve())
        entry["words"] = str(session["words"].resolve())
        preserved = recorded.get(camera) or recorded.get(str(session["camera"])) or {}
        entry.update(preserved)
        entries.append(entry)
    return {"sessions": entries}


def write_manifest(path, sessions, previous=None):
    """Write the sessions manifest atomically, keeping recorded sync offsets."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(manifest_document(sessions, previous), sort_keys=False, allow_unicode=True)
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


def media_duration_s(path):
    """Return the media duration in seconds from OpenCV, or None."""
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return None
    fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
    count = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
    capture.release()
    if fps <= 0 or count <= 0:
        return None
    return float(count) / float(fps)


def sample_times(duration_s, count):
    """Return ``count`` evenly spaced sample times inside ``duration_s``."""
    if duration_s is None or duration_s <= 0:
        return []
    step = float(duration_s) / float(count)
    return [step * (index + 0.5) for index in range(count)]


def read_frames(path, times):
    """Read one frame per time; return ``(t_s, frame)`` pairs for the frames found."""
    return read_frame_pairs(path, times, skip_missing=True)


def frame_similarity(first, second):
    """Normalised correlation of two frames on a 64x36 grey thumbnail, in ``[-1, 1]``."""
    grey_a = cv2.cvtColor(first, cv2.COLOR_BGR2GRAY)
    grey_b = cv2.cvtColor(second, cv2.COLOR_BGR2GRAY)
    thumb_a = cv2.resize(grey_a, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float64)
    thumb_b = cv2.resize(grey_b, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float64)
    thumb_a -= thumb_a.mean()
    thumb_b -= thumb_b.mean()
    denominator = float(np.linalg.norm(thumb_a) * np.linalg.norm(thumb_b))
    if denominator <= 1e-9:
        return 0.0
    return float(np.dot(thumb_a.ravel(), thumb_b.ravel()) / denominator)


def screen_camera_similarity(screen_frames, camera_frames):
    """Mean best-match similarity of the screen frames against the camera frames."""
    scores = []
    for t_s, frame in screen_frames:
        candidates = [
            other
            for other_t, other in camera_frames
            if abs(other_t - t_s) <= SIMILARITY_MATCH_WINDOW_S
        ]
        if not candidates:
            continue
        scores.append(max(frame_similarity(frame, other) for other in candidates))
    if not scores:
        return None
    return sum(scores) / len(scores)


def screen_camera_check(session, get_detector):
    """Return a suspect record when the screen file looks like a camera recording, else None.

    ``get_detector`` is called only after readable screen frames were found, so
    an unreadable screen never loads the face detector.
    """
    screen = session.get("screen")
    if screen is None:
        return None
    screen_frames = read_frames(screen, sample_times(media_duration_s(screen), CHECK_SAMPLES))
    if not screen_frames:
        return None
    detector = get_detector()
    reasons = []
    detail = []
    if detector is not None:
        hits = sum(1 for _, frame in screen_frames if detect_faces(detector, frame))
        if hits / len(screen_frames) >= FACE_FRAME_FRAC:
            reasons.append("face_in_most_frames")
            detail.append(f"a face in {hits}/{len(screen_frames)} sampled frames")
    camera = session.get("camera")
    if camera is not None:
        camera_frames = read_frames(
            camera, sample_times(media_duration_s(camera), CHECK_SAMPLES)
        )
        similarity = screen_camera_similarity(screen_frames, camera_frames)
        if similarity is not None and similarity >= SIMILARITY_THRESHOLD:
            reasons.append("similar_to_camera")
            detail.append(f"frame similarity {similarity:.2f} to '{Path(camera).name}'")
    if not reasons:
        return None
    return {
        "screen": Path(screen).name,
        "screen_path": str(screen),
        "camera": Path(camera).name if camera is not None else None,
        "reason": "+".join(reasons),
        "detail": "; ".join(detail),
    }


def run_screen_checks(sessions, warnings, state=None):
    """Warn per session when its screen file looks like a camera recording.

    ``state`` (optional) receives ``detector`` and ``error``; ``error`` is set
    when the face detector could not load, so the caller can report an
    incomplete check.
    """
    if state is None:
        state = {}
    state.setdefault("detector", None)
    state.setdefault("error", None)

    def get_detector():
        if state["detector"] is None and state["error"] is None:
            try:
                state["detector"] = make_detector(ensure_model(DEFAULT_MODEL_PATH))
            except FacesError as exc:
                state["error"] = str(exc)
        return state["detector"]

    suspects = []
    for session in sessions:
        if session.get("screen") is None:
            continue
        suspect = screen_camera_check(session, get_detector)
        if suspect is not None:
            warnings.append(
                f"screen file '{suspect['screen']}' looks like a CAMERA recording: {suspect['detail']}"
            )
            suspects.append(suspect)
    if state["error"] is not None:
        warnings.append(
            f"screen check incomplete: face detector unavailable: {state['error']}"
        )
    return suspects


def run(folder, out_dir, backend, config, use_llm, force, mode_b_config=None):
    """Run the Mode B chain and return the ``--json`` summary object."""
    started = time.monotonic()
    warnings = []
    mode_b_config = mode_b_config or dict(DEFAULT_MODE_B_CONFIG)
    sessions = scan_sessions(folder, out_dir, warnings, mode_b_config)
    pairing = pairing_summary(sessions, folder, mode_b_config)
    screen_state = {}
    suspects = run_screen_checks(sessions, warnings, screen_state)
    screen_check_incomplete = screen_state["error"] is not None
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
    write_manifest(sessions_path, sessions, load_manifest(sessions_path))

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
        "screen_suspects": suspects,
        "screen_check_incomplete": screen_check_incomplete,
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


def check(folder, out_dir, mode_b_config=None):
    """Run only the screen-file checks and return the ``--json`` summary object."""
    started = time.monotonic()
    warnings = []
    mode_b_config = mode_b_config or dict(DEFAULT_MODE_B_CONFIG)
    sessions = scan_sessions(folder, out_dir, warnings, mode_b_config)
    pairing = pairing_summary(sessions, folder, mode_b_config)
    screen_state = {}
    suspects = run_screen_checks(sessions, warnings, screen_state)
    screen_check_incomplete = screen_state["error"] is not None
    return {
        "ok": not suspects and not screen_check_incomplete,
        "check": True,
        "sessions": len(sessions),
        "pairing": pairing,
        "screen_suspects": suspects,
        "screen_check_incomplete": screen_check_incomplete,
        "warnings": warnings,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }


def print_human(summary, stream):
    print(
        f"mode_b: {summary['sessions']} session(s), {summary['transcribed']} transcribed",
        file=stream,
    )
    print(f"sessions: {summary['outputs']['sessions']}", file=stream)
    print(f"edit: {summary['outputs']['edit']}", file=stream)
    print(f"project: {summary['outputs']['project']}", file=stream)
    for warning in summary.get("warnings") or []:
        print(f"[warn] {warning}", file=stream)


def print_check(summary, stream):
    print(f"mode_b --check: {summary['sessions']} session(s)", file=stream)
    print(f"pairing: {summary['pairing']}", file=stream)
    for suspect in summary["screen_suspects"]:
        print(
            f"[warn] screen file '{suspect['screen']}' looks like a CAMERA recording: "
            f"{suspect['detail']}",
            file=stream,
        )
    for warning in summary.get("warnings") or []:
        print(f"[warn] {warning}", file=stream)


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
            "  python3 scripts/mode_b.py <trip-folder> --json\n"
            "  python3 scripts/mode_b.py <trip-folder> --backend mock --force --json\n"
            "  python3 scripts/mode_b.py <trip-folder> --config config.yaml --json\n"
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
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "only run the screen-file checks (a screen file that looks like a camera "
            "recording: a face in most sampled frames, or high frame similarity to the "
            "paired camera file); no transcription, nothing written; exit codes: 0 all "
            "clear, 1 a suspect was found, 2 the check could not run completely (face "
            "detector unavailable), 3 a usage error (missing folder, not a folder, no "
            "camera files, bad --config)"
        ),
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def report_error(exc, json_mode, code):
    """Print a user-facing error and return its exit code."""
    print(f"[error] {exc}", file=sys.stderr)
    if json_mode:
        print(json.dumps({"ok": False, "error": str(exc)}))
    return code


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
    except ModeBError as exc:
        return report_error(exc, args.json, 3)

    try:
        if args.check:
            summary = check(folder, out_dir, mode_b_config)
        else:
            summary = run(folder, out_dir, args.backend, config, args.llm, args.force, mode_b_config)
    except ModeBError as exc:
        return report_error(exc, args.json, 2)

    stream = sys.stderr if args.json else sys.stdout
    if args.json:
        print(json.dumps(summary))
    if args.check:
        print_check(summary, stream)
        if summary["screen_check_incomplete"]:
            return 2
        return 1 if summary["screen_suspects"] else 0
    print_human(summary, stream)
    return 0


if __name__ == "__main__":
    sys.exit(main())
