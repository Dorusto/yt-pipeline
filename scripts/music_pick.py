#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pick a music track from a library by the brief and the shot tags.

Usage:
    python scripts/music_pick.py --library <music-library> --brief "about 60 seconds" --workspace DIR
    python scripts/music_pick.py --library <music-library> --brief-file brief.txt --json
    python scripts/music_pick.py --list --json

The library folder comes from ``--library``, else the
``YT_PIPELINE_MUSIC_LIBRARY`` environment variable, else ``music.library`` in
the config; there is no personal default and nothing is ever downloaded. Every
track in the library is measured with ``beats.py`` (BPM and energy, cached under
``<library>/.yt-beats``) and scored against the brief's pace words, the shot tags
of the workspace shotlog and the target duration. The winner is written to
``music_choice.json``; with ``--workspace`` the chosen track's beat grid is also
written to ``workspace/beats.json``. ``--list`` reports each track with its
duration, BPM and licence file status. The library is read only; no model is
called.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from common.config import music_library

TOOL = "scripts/music_pick.py"
NO_LIBRARY_MESSAGE = (
    "no music library configured: pass --library DIR, set the "
    "YT_PIPELINE_MUSIC_LIBRARY environment variable, or set music.library in the config"
)
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}
LICENCE_SIDECAR_SUFFIXES = (".license", ".licence")
LICENCE_LIBRARY_NAMES = ("license", "licence", "license.txt", "licence.txt", "copying")
CACHE_DIR_NAME = ".yt-beats"
BEATS_SCRIPT = Path(__file__).resolve().parent / "beats.py"
CALM_WORDS = ("calm", "slow", "relax", "relaxing", "quiet", "chill", "soft", "ambient", "gentle")
ENERGETIC_WORDS = ("energetic", "fast", "upbeat", "party", "epic", "driving", "action", "dance", "fun")
CALM_BAND = (70.0, 100.0)
ENERGETIC_BAND = (110.0, 150.0)
NEUTRAL_BAND = (85.0, 135.0)
BAND_FALLOFF_BPM = 30.0
CALM_ENERGY_DBFS = -24.0
ENERGETIC_ENERGY_DBFS = -10.0
NEUTRAL_ENERGY_DBFS = -16.0
ENERGY_FALLOFF_DB = 12.0
WEIGHTS = {"bpm": 0.35, "energy": 0.25, "tag": 0.25, "duration": 0.15}


class MusicPickError(RuntimeError):
    """Raised when no track can be chosen."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="music_pick.py",
        description=(
            "Pick a music track from a library by the brief and the shot tags, "
            "using beats.py energy and BPM."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/music_pick.py --library <music-library> --brief \"about 60 seconds\" --workspace ~/ws\n"
            "  python scripts/music_pick.py --library <music-library> --brief-file brief.txt --json\n"
            "  python scripts/music_pick.py --list --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--library",
        default=None,
        help=(
            "folder of audio tracks (or YT_PIPELINE_MUSIC_LIBRARY, or music.library "
            "in the config; no personal default)"
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list the library's tracks with duration, BPM and licence file status, then exit",
    )
    parser.add_argument("--brief", default=None, help="the brief in plain language")
    parser.add_argument("--brief-file", default=None, help="path to a file holding the brief")
    parser.add_argument("--workspace", default=None, help="workspace holding shotlog.json; also gets beats.json")
    parser.add_argument("--target-duration-s", type=float, default=None, help="edit length the track should cover")
    parser.add_argument("--out", default=None, help="choice file to write (default WORKSPACE/music_choice.json)")
    parser.add_argument("--force", action="store_true", help="ignore the measurement cache")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def read_brief(brief, brief_file):
    if brief is not None and brief_file is not None:
        raise MusicPickError("give either --brief or --brief-file, not both")
    if brief is not None:
        return brief
    if brief_file is not None:
        path = Path(brief_file).expanduser().resolve()
        if not path.is_file():
            raise MusicPickError(f"brief file not found: {path}")
        return path.read_text(encoding="utf-8")
    raise MusicPickError("provide --brief or --brief-file")


def tokenize(text):
    return [token for token in re.split(r"[^a-z0-9]+", str(text).lower()) if token]


def resolve_library(cli_value):
    """The library folder from the flag, the environment or the config."""
    resolved = music_library(cli_value)
    if resolved is None:
        raise MusicPickError(NO_LIBRARY_MESSAGE)
    return resolved


def library_tracks(library):
    root = Path(library).expanduser().resolve()
    if not root.is_dir():
        raise MusicPickError(f"library not found: {root}")
    tracks = sorted(
        (
            entry
            for entry in root.iterdir()
            if entry.is_file() and entry.suffix.lower() in AUDIO_EXTENSIONS
        ),
        key=lambda entry: entry.name,
    )
    return root, tracks


def library_entries(root):
    return {entry.name.lower(): entry for entry in root.iterdir() if entry.is_file()}


def licence_for(track, entries):
    """The licence file of one track: a sidecar, else the library's licence file."""
    names = {f"{track.stem}{suffix}" for suffix in LICENCE_SIDECAR_SUFFIXES}
    names |= {f"{track.name}{suffix}" for suffix in LICENCE_SIDECAR_SUFFIXES}
    for name in sorted(names):
        found = entries.get(name.lower())
        if found is not None:
            return "sidecar", found
    for name in LICENCE_LIBRARY_NAMES:
        found = entries.get(name)
        if found is not None:
            return "library", found
    return "missing", None


def cache_path(library, track):
    return library / CACHE_DIR_NAME / f"{track.name}.json"


def load_cached(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("bpm") is not None else None


def write_cached(path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return


def measure_track(track, cache, force=False):
    """The beats.py measurement of one track, cached next to the library."""
    if not force and cache is not None:
        cached = load_cached(cache)
        if cached is not None:
            return cached
    result = subprocess.run(
        [sys.executable, str(BEATS_SCRIPT), str(track), "--json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        summary = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    measurement = summary.get("measurement") if isinstance(summary, dict) else None
    source = measurement if isinstance(measurement, dict) else summary
    if not isinstance(source, dict) or source.get("bpm") is None:
        return None
    data = {
        "bpm": float(source.get("bpm") or 0.0),
        "duration_s": float(source.get("duration_s") or 0.0),
        "energy_mean_dbfs": float(source.get("energy_mean_dbfs") or -60.0),
        "energy_peak_dbfs": float(source.get("energy_peak_dbfs") or -60.0),
    }
    if cache is not None:
        write_cached(cache, data)
    return data


def pace_words(brief, context_words):
    words = set(tokenize(brief)) | set(context_words)
    calm = any(word in CALM_WORDS for word in words)
    energetic = any(word in ENERGETIC_WORDS for word in words)
    if energetic and not calm:
        return "energetic"
    if calm and not energetic:
        return "calm"
    return "neutral"


def pace_targets(pace):
    if pace == "calm":
        return CALM_BAND, CALM_ENERGY_DBFS
    if pace == "energetic":
        return ENERGETIC_BAND, ENERGETIC_ENERGY_DBFS
    return NEUTRAL_BAND, NEUTRAL_ENERGY_DBFS


def bpm_score(bpm, band):
    low, high = band
    if low <= bpm <= high:
        return 1.0
    distance = low - bpm if bpm < low else bpm - high
    return max(0.0, 1.0 - distance / BAND_FALLOFF_BPM)


def energy_score(mean_dbfs, target):
    return max(0.0, 1.0 - abs(mean_dbfs - target) / ENERGY_FALLOFF_DB)


def tag_score(name, context_words):
    tokens = set(tokenize(name))
    matches = len(tokens & set(context_words))
    return min(1.0, matches / 2.0), matches


def duration_score(duration_s, target):
    if not target or target <= 0:
        return 0.5
    needed = 0.7 * float(target)
    if needed <= 0:
        return 0.5
    if duration_s >= needed:
        return 1.0
    return max(0.0, duration_s / needed)


def shot_context_words(workspace):
    if not workspace:
        return set()
    path = Path(workspace) / "shotlog.json"
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    words = set()
    for shot in data.get("shots") or []:
        for key in ("summary", "subject", "scenery"):
            words.update(tokenize(shot.get(key) or ""))
        for tag in shot.get("tags") or []:
            words.update(tokenize(tag))
    return words


def score_track(name, measurement, context_words, pace, target):
    band, energy_target = pace_targets(pace)
    bpm_value = measurement["bpm"]
    energy_mean = measurement["energy_mean_dbfs"]
    bpm = bpm_score(bpm_value, band)
    energy = energy_score(energy_mean, energy_target)
    tag, matches = tag_score(name, context_words)
    duration = duration_score(measurement["duration_s"], target)
    total = round(
        WEIGHTS["bpm"] * bpm + WEIGHTS["energy"] * energy + WEIGHTS["tag"] * tag + WEIGHTS["duration"] * duration,
        4,
    )
    return {
        "file": name,
        "total": total,
        "bpm": round(bpm, 4),
        "energy": round(energy, 4),
        "tag": round(tag, 4),
        "duration": round(duration, 4),
        "bpm_value": round(bpm_value, 2),
        "energy_mean_dbfs": round(energy_mean, 2),
        "duration_s": round(measurement["duration_s"], 2),
        "tag_matches": matches,
    }


def why_text(row, band, pace, target):
    parts = [
        f"bpm {row['bpm_value']:g} in band {band[0]:g}-{band[1]:g} ({pace})",
        f"energy {row['energy_mean_dbfs']:.1f} dBFS",
        f"{row['tag_matches']} tag match{'es' if row['tag_matches'] != 1 else ''}",
    ]
    if target:
        parts.append(f"{row['duration_s']:g} s vs {float(target):g} s target")
    else:
        parts.append(f"{row['duration_s']:g} s")
    return " · ".join(parts)


def write_beats(chosen_path, workspace):
    """Write the chosen track's beat grid to WORKSPACE/beats.json via beats.py."""
    out = Path(workspace) / "beats.json"
    result = subprocess.run(
        [sys.executable, str(BEATS_SCRIPT), str(chosen_path), "--out", str(out), "--force", "--json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise MusicPickError(f"beats.py failed on {chosen_path}: {result.stderr.strip()}")
    return out


def run_list(library, force=False):
    """One row per track: path, duration, BPM and the licence file status."""
    started = time.monotonic()
    root, tracks = library_tracks(library)
    entries = library_entries(root)
    rows = []
    for track in tracks:
        measurement = measure_track(track, cache_path(root, track), force=force)
        if measurement is None:
            print(f"[warn] could not measure {track.name}", file=sys.stderr)
        status, licence_file = licence_for(track, entries)
        rows.append(
            {
                "file": track.name,
                "path": str(track),
                "duration_s": round(measurement["duration_s"], 2) if measurement else None,
                "bpm": round(measurement["bpm"], 2) if measurement else None,
                "licence_status": status,
                "licence_file": str(licence_file) if licence_file else None,
            }
        )
    return {
        "tool": TOOL,
        "status": "listed",
        "library": str(root),
        "tracks": rows,
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": 0.0,
    }


def run(library, brief=None, brief_file=None, workspace=None, target_duration_s=None, out=None, force=False):
    started = time.monotonic()
    brief_text = read_brief(brief, brief_file)
    root, tracks = library_tracks(library)
    if not tracks:
        raise MusicPickError(f"no audio tracks in {root}")
    context_words = shot_context_words(Path(workspace).expanduser().resolve() if workspace else None)
    pace = pace_words(brief_text, context_words)
    rows = []
    chosen_path = None
    for track in tracks:
        measurement = measure_track(track, cache_path(root, track), force=force)
        if measurement is None:
            print(f"[warn] could not measure {track.name}", file=sys.stderr)
            continue
        row = score_track(track.name, measurement, context_words, pace, target_duration_s)
        row["path"] = str(track)
        rows.append(row)
    if not rows:
        raise MusicPickError(f"no track in {root} could be analysed")
    rows.sort(key=lambda row: (-row["total"], row["file"]))
    winner = rows[0]
    chosen_path = Path(winner["path"])
    band, _ = pace_targets(pace)
    summary = {
        "status": "written",
        "library": str(root),
        "tracks": len(tracks),
        "chosen": winner["file"],
        "path": str(chosen_path),
        "why": why_text(winner, band, pace, target_duration_s),
        "scores": [
            {key: row[key] for key in (
                "file", "total", "bpm", "energy", "tag", "duration", "bpm_value", "energy_mean_dbfs"
            )}
            for row in rows
        ],
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": 0.0,
    }
    if workspace:
        workspace_path = Path(workspace).expanduser().resolve()
        if not workspace_path.is_dir():
            raise MusicPickError(f"workspace not found: {workspace_path}")
        write_beats(chosen_path, workspace_path)
    out_path = Path(out).expanduser().resolve() if out else (
        Path(workspace).expanduser().resolve() / "music_choice.json" if workspace else Path("music_choice.json").resolve()
    )
    temporary = out_path.with_name(out_path.name + ".tmp")
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, out_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    summary["out"] = str(out_path)
    return summary


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        root = resolve_library(args.library)
        if args.list:
            summary = run_list(root, force=args.force)
        else:
            summary = run(
                root,
                brief=args.brief,
                brief_file=args.brief_file,
                workspace=args.workspace,
                target_duration_s=args.target_duration_s,
                out=args.out,
                force=args.force,
            )
    except (MusicPickError, OSError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary))
    elif args.list:
        print(f"library: {summary['library']}")
        for row in summary["tracks"]:
            duration = f"{row['duration_s']:g} s" if row["duration_s"] is not None else "? s"
            bpm = f"{row['bpm']:g} BPM" if row["bpm"] is not None else "? BPM"
            licence = row["licence_status"]
            if row["licence_file"]:
                licence = f"{licence} ({Path(row['licence_file']).name})"
            print(f"{row['file']}  {duration}  {bpm}  licence: {licence}")
        print(f"{len(summary['tracks'])} track{'s' if len(summary['tracks']) != 1 else ''}")
    else:
        print(f"chose {summary['chosen']} ({summary['path']})")
        print(f"why: {summary['why']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
