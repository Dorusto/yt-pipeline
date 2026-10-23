#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Score a Mode B rough cut against the words a hand edit kept.

Usage:
    python3 scripts/eval_rough_cut.py --edit edit.json --sessions sessions.yaml \
        --fcproj REF.fcproj --sequence NAME --words-dir DIR \
        [--section-min-s 30.0] [--until-s S] [--out report.json] [--json]

The tool compares the words ``rough_cut.py`` kept with the words a human kept in
a FilmCraft project of the same material. Both sides are mapped with the same
midpoint rule over their windows: a word is kept when ``(start + end) / 2`` lies
in ``[in, out)``. It reports precision/recall, the same numbers excluding the
sessions and long sections the reference did not use at all, the ten biggest
disagreement runs with timecodes, and a cross-check against
``subs_from_edit.py`` run as a subprocess. No model is called, so
``llm_cost_usd`` is always 0.0. The eval is a measuring tool: it has no
pass/fail gate and exits 0 on success.

With ``--until-s S`` only the first S seconds of the reference timeline count:
the reference clips are trimmed at S, the kept set is built from the trimmed
windows, and a word counts on either side only when its stem has a counted clip
and its midpoint lies inside that stem's covered source span. The cross-check
against ``subs_from_edit.py`` is always full-sequence, whatever ``--until-s``
says, because it validates the FilmCraft parsing over the whole project.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

from common.constants import TICKS_PER_SECOND
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.errors import report_error
from common.schema import SchemaError, validate_or_raise
from common.timeline_words import media_item_path, word_in_window

TOOL = "scripts/eval_rough_cut.py"
DEFAULT_TRACK = "Audio 1"
DEFAULT_SECTION_MIN_S = 30.0
MAX_DISAGREEMENTS = 10
SAMPLE_WORDS = 5
SUBS_FROM_EDIT = Path(__file__).resolve().parent / "subs_from_edit.py"


class EvalError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


class EvalRuntimeError(Exception):
    """A failure after the work started, such as the subs_from_edit.py cross-check."""


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def load_json(path):
    try:
        return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EvalError(f"cannot read '{path}': file not found")
    except json.JSONDecodeError as exc:
        raise EvalError(f"cannot read '{path}': not valid JSON ({exc})")
    except OSError as exc:
        raise EvalError(f"cannot read '{path}': {exc}")


def load_yaml(path):
    try:
        return yaml.safe_load(Path(path).expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EvalError(f"cannot read '{path}': file not found")
    except yaml.YAMLError as exc:
        raise EvalError(f"cannot read '{path}': invalid YAML ({exc})")
    except OSError as exc:
        raise EvalError(f"cannot read '{path}': {exc}")


def load_sessions(path):
    document = load_yaml(path)
    try:
        validate_or_raise(document, "sessions")
    except SchemaError as exc:
        raise EvalError(f"invalid sessions '{path}': {exc}")
    return document["sessions"]


def load_edit(path):
    document = load_json(path)
    try:
        validate_or_raise(document, "edit")
    except SchemaError as exc:
        raise EvalError(f"invalid edit '{path}': {exc}")
    return document


def load_review(path):
    """Load the phone cut review rows; a missing or unreadable file is a hard error."""
    document = load_yaml(path)
    if not isinstance(document, dict):
        raise EvalError(f"review '{path}': root must be a mapping")
    rows = document.get("review")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise EvalError(f"review '{path}': review must be a list")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cut_id = row.get("id")
        action = row.get("action")
        if not isinstance(cut_id, str) or action not in ("keep", "cut"):
            continue
        result.append({
            "id": cut_id,
            "action": action,
            "clip": row.get("clip"),
            "window_in_s": row.get("window_in_s"),
            "window_out_s": row.get("window_out_s"),
        })
    return result


def apply_review(rows, sessions, words_by_stem, doru, doru_kept_by_stem):
    """Apply the phone review rows as extra ground truth; return the number of rows applied."""
    clip_to_stem = {}
    for session in sessions:
        camera = Path(str(session["camera"]))
        clip_to_stem[camera.name] = camera.stem
        clip_to_stem[camera.stem] = camera.stem
    applied = 0
    for row in rows:
        clip = row.get("clip")
        if not isinstance(clip, str):
            continue
        stem = clip_to_stem.get(Path(clip).name) or clip_to_stem.get(Path(clip).stem)
        if stem is None or stem not in words_by_stem:
            continue
        start = row.get("window_in_s")
        end = row.get("window_out_s")
        if not is_number(start) or not is_number(end):
            continue
        words = words_by_stem[stem]
        indices = {
            index for index, word in enumerate(words)
            if float(start) <= (word["start"] + word["end"]) / 2.0 < float(end)
        }
        kept = doru_kept_by_stem.setdefault(stem, set())
        if row["action"] == "keep":
            for index in indices:
                doru.add((stem, index))
                kept.add(index)
        else:
            for index in indices:
                doru.discard((stem, index))
                kept.discard(index)
        applied += 1
    return applied


def flatten_words(path):
    """Return the flat ``[{word, start, end}]`` list of a Whisper word JSON, in file order."""
    data = load_json(path)
    words = []
    for segment in data.get("segments") or []:
        for word in segment.get("words") or []:
            text = str(word.get("word", "")).strip()
            if not text:
                continue
            start = word.get("start")
            end = word.get("end")
            if not is_number(start) or not is_number(end):
                raise EvalError(f"words '{path}': a word entry has no numeric start/end")
            words.append({"word": text, "start": float(start), "end": float(end)})
    return words


def our_windows(edit, sessions):
    """Map each session's camera stem to the edit.json windows in camera time.

    A session with a screen file produces paired slots sharing one id; the camera
    slot is the one whose ``clip`` is the camera basename, and each id is taken
    once.
    """
    windows = {}
    for session in sessions:
        camera_name = Path(session["camera"]).name
        stem = Path(camera_name).stem
        seen = set()
        for slot in edit.get("video") or []:
            if slot.get("clip") != camera_name:
                continue
            slot_id = slot.get("id")
            if slot_id in seen:
                continue
            seen.add(slot_id)
            windows.setdefault(stem, []).append((float(slot["in_s"]), float(slot["out_s"])))
    return windows


def doru_windows(fcproj, sequence, track):
    """Map each reference stem to its enabled audio-clip windows in source time.

    Returns ``(windows_by_stem, clips)`` where ``clips`` lists the enabled clips
    in timeline order with their stem, source window and timeline start.
    """
    data = load_json(fcproj)
    items = (data.get("project") or {}).get("items") or {}
    sequences = {}
    for value in items.values():
        kind = value.get("kind") or {}
        if "Sequence" in kind and value.get("name"):
            sequences[value["name"]] = value
    if sequence not in sequences:
        found = ", ".join(sorted(sequences)) or "none"
        raise EvalError(f"unknown sequence '{sequence}' (sequences found: {found})")
    sequence_item = sequences[sequence]
    audio_tracks = ((sequence_item.get("kind") or {}).get("Sequence") or {}).get("audio_tracks") or []
    tracks = {entry.get("name"): entry for entry in audio_tracks}
    if track not in tracks:
        found = ", ".join(sorted(str(name) for name in tracks)) or "none"
        raise EvalError(f"unknown track '{track}' (tracks found: {found})")

    windows = {}
    clips = []
    ordered = sorted(tracks[track].get("items") or [], key=lambda clip: float(clip.get("start", 0)))
    for clip in ordered:
        if not clip.get("enabled", True):
            continue
        media_path = media_item_path(items.get(str(clip.get("item"))))
        name = clip.get("name") or (Path(media_path).name if media_path else str(clip.get("item")))
        if media_path is None:
            raise EvalError(f"clip '{name}': source media is missing from the project")
        speed = float(clip.get("speed", 1.0) or 1.0)
        if abs(speed - 1.0) > 1e-9:
            raise EvalError(f"clip '{name}': speed {speed:g} is not 1.0, word times would not map linearly")
        if clip.get("reverse", False):
            raise EvalError(f"clip '{name}': reverse playback is not supported, word times would not map linearly")
        stem = Path(media_path).stem
        source_in = float(clip["source_in"]) / TICKS_PER_SECOND
        duration = float(clip["duration"]) / TICKS_PER_SECOND
        windows.setdefault(stem, []).append((source_in, source_in + duration))
        clips.append({
            "stem": stem,
            "start": source_in,
            "end": source_in + duration,
            "timeline_start": float(clip.get("start", 0)) / TICKS_PER_SECOND,
        })
    return windows, clips


def kept_indices(words, windows):
    """The flat indices whose midpoint lies in one of the windows."""
    kept = set()
    for index, word in enumerate(words):
        if any(word_in_window(word, start, end) for start, end in windows):
            kept.add(index)
    return kept


def trim_clips(clips, until_s):
    """The clips whose timeline interval intersects ``[0, until_s)``, trimmed at the boundary."""
    counted = []
    for clip in clips:
        timeline_start = clip["timeline_start"]
        if timeline_start >= until_s:
            continue
        timeline_end = timeline_start + (clip["end"] - clip["start"])
        if timeline_end <= 0:
            continue
        trim = min(timeline_end, until_s) - timeline_start
        counted.append({
            "stem": clip["stem"],
            "start": clip["start"],
            "end": clip["start"] + trim,
            "timeline_start": timeline_start,
        })
    return counted


def windows_by_stem(clips):
    """Group clips into ``{stem: [(source_in, source_out), ...]}``."""
    windows = {}
    for clip in clips:
        windows.setdefault(clip["stem"], []).append((clip["start"], clip["end"]))
    return windows


def covered_universe(windows_by_stem, words_by_stem):
    """The ``(stem, index)`` pairs whose midpoint lies inside their stem's covered source span."""
    universe = set()
    for stem, windows in windows_by_stem.items():
        words = words_by_stem.get(stem)
        if not words:
            continue
        span_start = min(start for start, _ in windows)
        span_end = max(end for _, end in windows)
        for index, word in enumerate(words):
            midpoint = (word["start"] + word["end"]) / 2.0
            if span_start <= midpoint < span_end:
                universe.add((stem, index))
    return universe


def merged_kept_count(clips, words_by_stem, doru_kept_by_stem):
    """Count the kept words the way ``subs_from_edit.py`` does: timeline order, hyphen clitics glued."""
    ordered = []
    for clip in sorted(clips, key=lambda entry: entry["timeline_start"]):
        words = words_by_stem.get(clip["stem"]) or []
        for index in sorted(doru_kept_by_stem.get(clip["stem"]) or ()):
            word = words[index]
            midpoint = (word["start"] + word["end"]) / 2.0
            if clip["start"] <= midpoint < clip["end"]:
                ordered.append(word["word"])
    merged = 0
    for position, text in enumerate(ordered):
        if position and text.startswith("-") and len(text) > 1:
            continue
        merged += 1
    return merged


def section_runs(words, dropped, section_min_s):
    """The dropped indices that belong to a consecutive run spanning ``section_min_s``."""
    members = set()
    run = []
    for index in dropped:
        if run and index == run[-1] + 1:
            run.append(index)
        else:
            if run:
                add_section_run(members, words, run, section_min_s)
            run = [index]
    if run:
        add_section_run(members, words, run, section_min_s)
    return members


def add_section_run(members, words, run, section_min_s):
    span = words[run[-1]]["end"] - words[run[0]]["start"]
    if span >= section_min_s:
        members.update(run)


def make_run(stem, words, side, first, last):
    return {
        "side": side,
        "stem": stem,
        "start_s": round(words[first]["start"], 3),
        "end_s": round(words[last]["end"], 3),
        "words": last - first + 1,
        "sample": [words[index]["word"] for index in range(first, min(first + SAMPLE_WORDS, last + 1))],
    }


def build_disagreements(words_by_stem, ours, doru):
    """Group consecutive differing words of the same side and keep the ten biggest runs."""
    runs = []
    for stem, words in words_by_stem.items():
        side = None
        start = 0
        for index in range(len(words)):
            in_ours = (stem, index) in ours
            in_doru = (stem, index) in doru
            if in_ours and not in_doru:
                current = "ours_only"
            elif in_doru and not in_ours:
                current = "doru_only"
            else:
                current = None
            if current != side:
                if side is not None:
                    runs.append(make_run(stem, words, side, start, index - 1))
                side = current
                start = index
        if side is not None:
            runs.append(make_run(stem, words, side, start, len(words) - 1))
    runs.sort(key=lambda run: (run["words"], run["end_s"] - run["start_s"]), reverse=True)
    return runs[:MAX_DISAGREEMENTS]


def cross_check(fcproj, sequence, words_dir, doru_kept_merged):
    """Run ``subs_from_edit.py`` and compare its ``words_kept`` with our merged ``|doru|``."""
    with tempfile.TemporaryDirectory() as tmp:
        command = [
            sys.executable, str(SUBS_FROM_EDIT),
            "--fcproj", str(fcproj), "--sequence", sequence,
            "--words-dir", str(words_dir), "--out-dir", tmp, "--json",
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
            raise EvalRuntimeError(f"subs_from_edit.py failed: {detail}")
        try:
            summary = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise EvalRuntimeError(f"subs_from_edit.py printed no JSON summary ({exc})")
        kept = int(summary["words_kept"])
    return {"subs_from_edit_words_kept": kept, "agrees": kept == doru_kept_merged}


def evaluate(edit, sessions, fcproj, sequence, track, words_dir, section_min_s, until_s=None, review_rows=None):
    """Compare the kept words of the edit and the reference and build the report object."""
    our_windows_by_stem = our_windows(edit, sessions)
    full_windows_by_stem, full_clips = doru_windows(fcproj, sequence, track)
    counted_clips = full_clips if until_s is None else trim_clips(full_clips, until_s)
    counted_windows_by_stem = windows_by_stem(counted_clips)

    words_paths = {}
    for session in sessions:
        words_paths[Path(session["camera"]).stem] = session["words"]
    for stem in full_windows_by_stem:
        if stem not in words_paths:
            words_paths[stem] = str(Path(words_dir) / f"{stem}.json")

    words_by_stem = {stem: flatten_words(path) for stem, path in words_paths.items()}

    universe = None
    if until_s is not None:
        universe = covered_universe(counted_windows_by_stem, words_by_stem)

    ours = set()
    for stem, windows in our_windows_by_stem.items():
        words = words_by_stem.get(stem)
        if words is None:
            continue
        for index in kept_indices(words, windows):
            if universe is None or (stem, index) in universe:
                ours.add((stem, index))

    doru = set()
    doru_kept_by_stem = {}
    for stem, windows in counted_windows_by_stem.items():
        kept = kept_indices(words_by_stem[stem], windows)
        doru_kept_by_stem[stem] = kept
        for index in kept:
            doru.add((stem, index))

    review_applied = apply_review(
        review_rows or [], sessions, words_by_stem, doru, doru_kept_by_stem
    )

    full_kept_by_stem = {}
    for stem, windows in full_windows_by_stem.items():
        full_kept_by_stem[stem] = kept_indices(words_by_stem[stem], windows)

    intersect = len(ours & doru)
    precision = intersect / len(ours) if ours else 0.0
    recall = intersect / len(doru) if doru else 0.0

    used_stems = set(counted_windows_by_stem)
    session_words = set()
    for stem, words in words_by_stem.items():
        if stem not in used_stems:
            session_words.update((stem, index) for index in range(len(words)))
    section_words = set()
    for stem in used_stems:
        words = words_by_stem[stem]
        if universe is None:
            candidates = range(len(words))
        else:
            candidates = [index for index in range(len(words)) if (stem, index) in universe]
        dropped = [index for index in candidates if index not in doru_kept_by_stem[stem]]
        section_words.update((stem, index) for index in section_runs(words, dropped, section_min_s))
    bucket = session_words | section_words

    ours_used = ours - bucket
    doru_used = doru - bucket
    intersect_used = len(ours_used & doru_used)
    precision_used = intersect_used / len(ours_used) if ours_used else 0.0
    recall_used = intersect_used / len(doru_used) if doru_used else 0.0

    report = {
        "ok": True,
        "until_s": until_s,
        "ours": {"kept": len(ours), "sessions": len(sessions)},
        "doru": {
            "kept": len(doru),
            "sessions_used": len(used_stems),
            "sessions_total": len(words_by_stem),
        },
        "intersect": intersect,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "precision_used": round(precision_used, 4),
        "recall_used": round(recall_used, 4),
        "bucket": {
            "session_words": len(session_words),
            "section_words": len(section_words),
            "total": len(bucket),
        },
        "disagreements": build_disagreements(words_by_stem, ours, doru),
        "cross_check": cross_check(
            fcproj, sequence, words_dir,
            merged_kept_count(full_clips, words_by_stem, full_kept_by_stem),
        ),
        "wall_time_s": 0.0,
        "llm_cost_usd": 0.0,
    }
    if review_rows is not None:
        report["review_rows"] = review_applied
    return report


def format_table(report):
    """Render the report as a short numbers table plus the biggest disagreements."""
    rows = []
    if report.get("until_s") is not None:
        rows.append(("until_s", f"{report['until_s']:g}"))
    rows.extend([
        ("ours kept", report["ours"]["kept"]),
        ("doru kept", report["doru"]["kept"]),
        ("intersect", report["intersect"]),
        ("precision", f"{report['precision']:.4f}"),
        ("recall", f"{report['recall']:.4f}"),
        ("precision_used", f"{report['precision_used']:.4f}"),
        ("recall_used", f"{report['recall_used']:.4f}"),
        ("bucket session_words", report["bucket"]["session_words"]),
        ("bucket section_words", report["bucket"]["section_words"]),
        ("bucket total", report["bucket"]["total"]),
        ("cross-check kept", report["cross_check"]["subs_from_edit_words_kept"]),
        ("cross-check agrees", "yes" if report["cross_check"]["agrees"] else "no"),
    ])
    lines = [f"{'metric':<24}{'value'}"]
    lines.extend(f"{name:<24}{value}" for name, value in rows)
    lines.append("")
    lines.append(f"{'side':<12}{'stem':<24}{'start_s':>9}{'end_s':>9}{'words':>7}")
    for run in report["disagreements"]:
        lines.append(
            f"{run['side']:<12}{run['stem']:<24}{run['start_s']:>9.3f}{run['end_s']:>9.3f}{run['words']:>7}"
        )
    return "\n".join(lines)


def atomic_write_json(path, data):
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(out.parent), prefix=out.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(tmp_name, out)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def build_parser():
    parser = argparse.ArgumentParser(
        prog="eval_rough_cut.py",
        description=(
            "Compare the words a Mode B rough cut keeps with the words a hand edit kept: "
            "precision/recall, the same numbers excluding the sessions and long sections the "
            "reference did not use, the biggest disagreements and a subs_from_edit.py cross-check."
        ),
        epilog=(
            "examples:\n"
            "  python3 scripts/eval_rough_cut.py --edit edit.json --sessions sessions.yaml "
            "--fcproj REF.fcproj --sequence 'Main' --words-dir words/ --json\n"
            "  python3 scripts/eval_rough_cut.py --edit edit.json --sessions sessions.yaml "
            "--fcproj REF.fcproj --sequence 'Main' --words-dir words/ --out report.json\n"
            "  python3 scripts/eval_rough_cut.py --edit edit.json --sessions sessions.yaml "
            "--fcproj REF.fcproj --sequence 'Main' --words-dir words/ --review cut_review.yaml --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--edit", required=True, help="the edit.json rough_cut.py wrote")
    parser.add_argument("--sessions", required=True, help="the sessions manifest rough_cut.py took")
    parser.add_argument("--fcproj", required=True, help="the reference FilmCraft project")
    parser.add_argument("--sequence", required=True, help="the sequence to read from the reference project")
    parser.add_argument("--words-dir", required=True, help="folder with one words JSON per source stem")
    parser.add_argument("--section-min-s", type=float, default=DEFAULT_SECTION_MIN_S,
                        help=f"a dropped run spanning at least this many seconds is a section (default {DEFAULT_SECTION_MIN_S:g})")
    parser.add_argument("--until-s", type=float, default=None,
                        help="only the first S seconds of the reference timeline count (default: no limit)")
    parser.add_argument("--review", help="cut_review.yaml from the phone review; its rows are extra ground truth")
    parser.add_argument("--out", help="also write the full report object to FILE (atomic replace)")
    parser.add_argument("--json", action="store_true", help="print the machine-readable report object")
    return parser


def main(argv=None):
    started = time.monotonic()
    args = build_parser().parse_args(argv)
    try:
        if args.until_s is not None and not (args.until_s > 0):
            raise EvalError(f"--until-s must be > 0 (got {args.until_s:g})")
        edit = load_edit(args.edit)
        sessions = load_sessions(args.sessions)
        review_rows = load_review(args.review) if args.review else None
        words_dir = Path(args.words_dir).expanduser()
        if not words_dir.is_dir():
            raise EvalError(f"words dir not found: {words_dir}")
        report = evaluate(edit, sessions, args.fcproj, args.sequence, DEFAULT_TRACK, words_dir,
                          args.section_min_s, args.until_s, review_rows)
        report["wall_time_s"] = round(time.monotonic() - started, 3)
    except EvalRuntimeError as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except (EvalError, SchemaError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)

    if args.out:
        try:
            atomic_write_json(args.out, report)
        except OSError as exc:
            return report_error(f"cannot write {args.out}: {exc}", EXIT_RUNTIME, args.json)

    if args.json:
        print(json.dumps(report))
    else:
        print(format_table(report))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
