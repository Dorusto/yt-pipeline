#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Code editor: turn a director plan into the exact cut windows and a short report.

Usage:
    python scripts/director_editor.py --workspace DIR --plan director_plan.json
    python scripts/director_editor.py --workspace DIR --plan director_plan.json --json
    python scripts/director_editor.py --workspace DIR --plan director_plan.json --beats beats.json

Reads ``director_plan.json`` (validated against
``schemas/director_plan.schema.json``), the workspace ``shotlog.json``,
``ingest.json``, ``quality/`` and ``motion/`` and writes ``edit.json``
(``schemas/edit.schema.json``) and ``editor_report.json``
(``schemas/editor_report.schema.json``). The editor executes the plan: it never
changes, swaps, drops or reorders the shots; what it cannot do goes in the
report. No model call.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from common.beatsnap import PHOTO_MAX_S, PHOTO_MIN_S, BeatsError, load_beats, slot_bounds, snap_slots
from common.constants import MIN_SHOT_LENGTH_S
from common.embed import DEFAULT_THRESHOLD, EmbedError
from common.errors import report_error
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.facts import (
    DEFAULT_IMPORTANT_MAX_S,
    MAX_SLOT_S,
    NON_MOVE_MOTION_LABELS,
    FactsError,
    build_shot_facts,
    clamp,
    fact_peak,
    landing_floor,
    load_ingest_photos,
    load_shotlog,
)
from common.jsonio import load_json, now_iso, write_json
from common.moves import slot_duration
from common.peak import centre_window, is_centred
from common.photos import ken_burns_for
from common.schema import SchemaError, validate_or_raise
from common.summary import summary_fields
from common.window_similarity import WindowEmbedder

TOOL = "scripts/director_editor.py"
SCHEMA_VERSION = "0.1"
SLOT_MIN_S = 2.0
SLOT_MAX_S = 5.0
OPENING_MAX_S = 3.0
JERKY_PIECE_S = 0.6
JERKY_ENTRY_MIN_S = 1.0
COMPRESSION_PIECE_MIN_S = 0.5
ENTRY_SETTLE_S = 0.5
EXIT_AFTER_S = 0.3
DEFAULT_PIECES = 3


class EditorError(RuntimeError):
    """Raised when the editor cannot run (usage or input error)."""


class EditorRuntimeError(RuntimeError):
    """Raised when the editor fails after the run started (a write failure)."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="director_editor.py",
        description=(
            "Turn a validated director plan into edit.json and a short editor report: "
            "the exact cut windows, on the beat, without changing the shots. No model call."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/director_editor.py --workspace <workspace> --plan director_plan.json\n"
            "  python scripts/director_editor.py --workspace <workspace> --plan director_plan.json --json\n"
            "  python scripts/director_editor.py --workspace <workspace> --plan director_plan.json --beats beats.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--workspace",
        required=True,
        help="workspace holding shotlog.json, ingest.json, quality/ and motion/",
    )
    parser.add_argument("--plan", required=True, help="director plan JSON (schemas/director_plan.schema.json)")
    parser.add_argument("--out", default=None, help="edit list to write (default WORKSPACE/edit.json)")
    parser.add_argument("--report", default=None, help="editor report to write (default WORKSPACE/editor_report.json)")
    parser.add_argument(
        "--beats",
        default=None,
        help="beat grid JSON from beats.py (default WORKSPACE/beats.json when it exists)",
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def _fallback_fact(shot):
    """A minimal fact from the shotlog entry when the built facts have none for the key."""
    return {
        "shot_key": shot.get("key"),
        "clip": shot.get("clip"),
        "window_in_s": float(shot.get("start_s") or 0.0),
        "window_out_s": float(shot.get("end_s") or 0.0),
        "peak_s": shot.get("peak_s"),
        "peak_source": shot.get("peak_source"),
        "landing_at_s": None,
        "landing_hold_key": None,
        "keep_whole": False,
        "target_s": None,
        "score": shot.get("score"),
        "summary": shot.get("summary"),
        "role": shot.get("role"),
        "motion_label": shot.get("motion_label"),
        "hero": shot.get("hero"),
        "similar_to": list(shot.get("similar_to") or []),
        "media_type": shot.get("media_type"),
        "subject_position": shot.get("subject_position"),
        "tags": list(shot.get("tags") or []),
        "subject_enter_s": None,
        "subject_leave_s": None,
    }


def _moment(shot, fact, window_in, window_out):
    """The moment the cut is built around: the plan's at_s, else the peak, else the middle."""
    moment = shot.get("moment") or {}
    if moment.get("at_s") is not None:
        return clamp(float(moment["at_s"]), window_in, window_out)
    if moment.get("hint"):
        return fact_peak(fact)
    return (window_in + window_out) / 2.0


def _plain_cut(fact, length, moment):
    """A cut of ``length`` seconds around ``moment``, inside the window, landing floor included."""
    return centre_window(
        float(fact["window_in_s"]),
        float(fact["window_out_s"]),
        length,
        moment,
        min_out_s=landing_floor(fact),
    )


def _apply_floor(in_s, out_s, window_in, window_out, floor, length):
    """Push the cut's end to the landing floor, keeping it inside the window."""
    if floor is None:
        return in_s, out_s
    out_s = min(window_out, max(out_s, floor))
    in_s = max(window_in, out_s - length)
    return in_s, out_s


def _jerky_pieces(window_in, window_out, length, pieces):
    """The pieces of a jerky approach: short cuts spread over the window, then the entry."""
    entry_len = max(JERKY_ENTRY_MIN_S, length - (pieces - 1) * JERKY_PIECE_S)
    first_part = (window_out - window_in) - entry_len
    step = (first_part - JERKY_PIECE_S) / (pieces - 1)
    cuts = []
    for index in range(pieces - 1):
        start = window_in + index * step
        cuts.append((round(start, 3), round(start + JERKY_PIECE_S, 3)))
    cuts.append((round(window_out - entry_len, 3), round(window_out, 3)))
    return cuts


def _compression_pieces(window_in, window_out, length, pieces):
    """The slices of a time compression: equal short cuts spread over the window."""
    slice_len = max(COMPRESSION_PIECE_MIN_S, length / pieces)
    step = ((window_out - window_in) - slice_len) / (pieces - 1)
    cuts = []
    for index in range(pieces):
        start = window_in + index * step
        cuts.append((round(start, 3), round(start + slice_len, 3)))
    return cuts


def _video_cut(shot, fact, could_not_do):
    """The cut pieces of one video shot: ``(pieces, technique_done, why_not)``."""
    window_in = float(fact["window_in_s"])
    window_out = float(fact["window_out_s"])
    window_len = window_out - window_in
    technique = shot["technique"]
    target = float(shot["target_s"])
    calm_hold = fact.get("role") == "hold" and fact.get("motion_label") in NON_MOVE_MOTION_LABELS
    if calm_hold:
        length = min(max(target, SLOT_MIN_S), window_len)
    else:
        length = min(clamp(target, SLOT_MIN_S, SLOT_MAX_S), window_len)
    moment = _moment(shot, fact, window_in, window_out)
    floor = landing_floor(fact)

    if technique == "opening_shot":
        return [_plain_cut(fact, clamp(length, SLOT_MIN_S, OPENING_MAX_S), moment)], technique, None

    if technique == "clean_entry":
        enter = fact.get("subject_enter_s")
        if enter is None:
            why = "no subject-enters event inside the window"
            could_not_do.append(f"{shot['id']} {shot['shot_key']}: {why}")
            return [_plain_cut(fact, length, moment)], None, why
        in_s = max(window_in, float(enter) - ENTRY_SETTLE_S)
        out_s = min(window_out, in_s + length)
        in_s, out_s = _apply_floor(in_s, out_s, window_in, window_out, floor, length)
        return [(in_s, out_s)], technique, None

    if technique == "clean_exit":
        leave = fact.get("subject_leave_s")
        if leave is None:
            why = "no subject-leaves event inside the window"
            could_not_do.append(f"{shot['id']} {shot['shot_key']}: {why}")
            out_s = window_out
            in_s = max(window_in, out_s - length)
            return [(in_s, out_s)], None, why
        out_s = min(window_out, float(leave) + EXIT_AFTER_S)
        in_s = max(window_in, out_s - length)
        in_s, out_s = _apply_floor(in_s, out_s, window_in, window_out, floor, length)
        return [(in_s, out_s)], technique, None

    if technique == "jerky_approach":
        pieces = int(clamp(int(shot.get("pieces") or DEFAULT_PIECES), 3, 4))
        needed = (pieces - 1) * JERKY_PIECE_S + JERKY_ENTRY_MIN_S
        if window_len < needed:
            why = f"the window is too short for {pieces} pieces (needs {needed:.1f} s, has {window_len:.1f} s)"
            could_not_do.append(f"{shot['id']} {shot['shot_key']}: {why}")
            return [_plain_cut(fact, length, moment)], None, why
        return _jerky_pieces(window_in, window_out, length, pieces), technique, None

    if technique == "time_compression":
        pieces = int(clamp(int(shot.get("pieces") or DEFAULT_PIECES), 2, 5))
        needed = pieces * COMPRESSION_PIECE_MIN_S
        if window_len < needed:
            why = f"the window is too short for {pieces} slices (needs {needed:.1f} s, has {window_len:.1f} s)"
            could_not_do.append(f"{shot['id']} {shot['shot_key']}: {why}")
            return [_plain_cut(fact, length, moment)], None, why
        return _compression_pieces(window_in, window_out, length, pieces), technique, None

    return [_plain_cut(fact, length, moment)], technique, None


def _photo_cut(shot, fact, could_not_do):
    """The duration and technique verdict of one photo shot: ``(duration, technique_done, why_not)``."""
    duration = clamp(float(shot["target_s"]), PHOTO_MIN_S, PHOTO_MAX_S)
    technique = shot["technique"]
    if technique == "none":
        return duration, technique, None
    why = "the shot is a still photo: no window to cut"
    could_not_do.append(f"{shot['id']} {shot['shot_key']}: {why}")
    return duration, None, why


def _video_slot(slot_id, shot, fact, entry, in_s, out_s):
    return {
        "id": slot_id,
        "clip": fact.get("clip") or entry.get("clip"),
        "in_s": round(float(in_s), 3),
        "out_s": round(float(out_s), 3),
        "score": float(entry.get("score") or 0.0),
        "reason": shot["why"],
        "transition": {"type": "cut", "duration_s": 0},
        "speed": 1.0,
        "role": fact.get("role"),
        "motion_label": fact.get("motion_label"),
        "description": entry.get("description"),
        "shot_key": shot["shot_key"],
    }


def _photo_slot(slot_id, shot, fact, entry, duration):
    return {
        "id": slot_id,
        "kind": "photo",
        "photo": fact.get("photo") or entry.get("clip"),
        "duration_s": round(float(duration), 3),
        "ken_burns": ken_burns_for(fact.get("subject_position"), fact.get("tags")),
        "score": float(entry.get("score") or 0.0),
        "reason": shot["why"],
        "transition": {"type": "cut", "duration_s": 0},
        "description": entry.get("description"),
        "capture_time": fact.get("capture_time") or entry.get("capture_time"),
        "shot_key": shot["shot_key"],
    }


def _cap_for(fact):
    """The longest on-screen length the snap may give one slot of this shot."""
    if fact.get("media_type") == "photo":
        return PHOTO_MAX_S
    window_len = float(fact["window_out_s"]) - float(fact["window_in_s"])
    if fact.get("role") == "hold" and fact.get("motion_label") in NON_MOVE_MOTION_LABELS:
        return window_len
    return min(SLOT_MAX_S, window_len)


def _similar_neighbours(slots, similar_fn, could_not_do):
    """The consecutive video-slot pairs whose window similarity reaches the threshold."""
    pairs = []
    for first, second in zip(slots, slots[1:]):
        if first.get("kind") == "photo" or second.get("kind") == "photo":
            continue
        try:
            score = float(similar_fn(first, second))
        except EmbedError as exc:
            could_not_do.append(f"the neighbour similarity check could not run: {exc}")
            break
        if score >= DEFAULT_THRESHOLD:
            pairs.append({"first": first["id"], "second": second["id"], "score": round(score, 4)})
    return pairs


def _summary_sentence(shot_count, total_length_s, target_length_s, could_not_do_count, similar_count):
    target = "no target" if target_length_s is None else f"target {target_length_s:g} s"
    things = f"{could_not_do_count} thing{'s' if could_not_do_count != 1 else ''} could not be done"
    pairs = (
        f"{similar_count} pair{'s' if similar_count != 1 else ''} of neighbours "
        f"{'look' if similar_count != 1 else 'looks'} alike"
    )
    return f"{shot_count} shots, {total_length_s:.1f} s on screen ({target}); {things}; {pairs}."


def _sources(slots, clips, workspace):
    """One source row per used clip or photo, deduplicated by file, in first-use order."""
    clips_by_file = {clip["file"]: clip for clip, _ in clips}
    photos_by_name = {
        photo.get("file") or Path(photo["path"]).name: photo
        for photo in load_ingest_photos(workspace)
    }
    sources = []
    seen = set()
    for slot in slots:
        if slot.get("kind") == "photo":
            name = slot["photo"]
            if name in seen:
                continue
            seen.add(name)
            photo = photos_by_name.get(name) or {}
            sources.append({"file": name, "path": photo.get("path") or name, "duration_s": None, "media_type": "photo"})
        else:
            name = slot["clip"]
            if name in seen:
                continue
            seen.add(name)
            clip = clips_by_file.get(name) or {}
            sources.append({"file": name, "path": clip.get("path") or name, "duration_s": clip.get("duration_s")})
    return sources


def run_editor(workspace, plan_path, out_path=None, report_path=None, beats_path=None, similar_fn=None):
    """Execute the plan and return ``(edit, report, summary)``."""
    started = time.monotonic()
    workspace = Path(workspace).expanduser()
    if not workspace.is_dir():
        raise EditorError(f"workspace is not a folder: {workspace}")
    plan_file = Path(plan_path).expanduser()
    plan = load_json(plan_file)
    if not isinstance(plan, dict):
        raise EditorError(f"missing or unreadable plan: {plan_file}")
    try:
        validate_or_raise(plan, "director_plan")
    except SchemaError as exc:
        raise EditorError(f"plan does not match director_plan: {exc}") from exc
    shotlog = load_shotlog(workspace)
    if shotlog is None:
        raise EditorError(f"missing shotlog.json in {workspace}")
    try:
        facts, clips, fact_warnings = build_shot_facts(
            workspace, shotlog, DEFAULT_IMPORTANT_MAX_S, slot_min_s=MIN_SHOT_LENGTH_S, slot_max_s=MAX_SLOT_S,
        )
    except FactsError as exc:
        raise EditorError(str(exc)) from exc

    could_not_do = list(fact_warnings)
    slots = []
    rows = []
    for shot in plan.get("shots") or []:
        shot_key = shot["shot_key"]
        entry = shotlog.get(shot_key)
        if entry is None:
            raise EditorError(f"unknown shot key {shot_key}: not in the shotlog")
        fact = facts.get(shot_key)
        if fact is None:
            fact = _fallback_fact(entry)
            could_not_do.append(
                f"{shot_key}: no usable window in the shotlog (ineligible or no steady range); "
                f"used the segment window {fact['window_in_s']:.1f}-{fact['window_out_s']:.1f} s"
            )
        if fact.get("media_type") == "photo":
            duration, technique_done, why_not = _photo_cut(shot, fact, could_not_do)
            slots.append(_photo_slot(shot["id"], shot, fact, entry, duration))
            rows.append(
                {"shot": shot, "fact": fact, "indices": [len(slots) - 1], "technique_done": technique_done, "why_not": why_not}
            )
            continue
        cuts, technique_done, why_not = _video_cut(shot, fact, could_not_do)
        indices = []
        for index, (in_s, out_s) in enumerate(cuts):
            slot_id = shot["id"] if index == 0 else f"{shot['id']}p{index + 1}"
            slots.append(_video_slot(slot_id, shot, fact, entry, in_s, out_s))
            indices.append(len(slots) - 1)
        rows.append(
            {"shot": shot, "fact": fact, "indices": indices, "technique_done": technique_done, "why_not": why_not}
        )

    grid_path = Path(beats_path).expanduser() if beats_path is not None else workspace / "beats.json"
    beat_grid = None
    if grid_path.is_file():
        try:
            beat_grid = load_beats(grid_path)
        except BeatsError as exc:
            raise EditorError(str(exc)) from exc
    if beat_grid is not None:
        bounds = []
        for row in rows:
            cap = _cap_for(row["fact"])
            for index in row["indices"]:
                bounds.append(slot_bounds(slots[index], row["fact"], cap))
        snap_warnings = []
        snap_slots(slots, bounds, beat_grid, snap_warnings, slot_min_s=MIN_SHOT_LENGTH_S)
        could_not_do.extend(snap_warnings)

    for row in rows:
        fact = row["fact"]
        if fact.get("media_type") == "photo":
            continue
        peak = round(fact_peak(fact), 3)
        for index in row["indices"]:
            slot = slots[index]
            slot["peak_s"] = peak
            slot["peak_source"] = fact.get("peak_source") or "middle"
            slot["centred"] = is_centred(
                slot["in_s"], slot["out_s"], fact["window_in_s"], fact["window_out_s"], peak,
            )

    report_shots = []
    for row in rows:
        shot = row["shot"]
        indices = row["indices"]
        if row["fact"].get("media_type") == "photo":
            report_shots.append(
                {
                    "id": shot["id"],
                    "shot_key": shot["shot_key"],
                    "in_s": None,
                    "out_s": None,
                    "length_s": round(slot_duration(slots[indices[0]]), 3),
                    "technique_asked": shot["technique"],
                    "technique_done": row["technique_done"],
                    "why_not": row["why_not"],
                }
            )
            continue
        report_shot = {
            "id": shot["id"],
            "shot_key": shot["shot_key"],
            "in_s": round(float(slots[indices[0]]["in_s"]), 3),
            "out_s": round(float(slots[indices[-1]]["out_s"]), 3),
            "length_s": round(sum(slot_duration(slots[index]) for index in indices), 3),
            "technique_asked": shot["technique"],
            "technique_done": row["technique_done"],
            "why_not": row["why_not"],
        }
        if len(indices) > 1:
            report_shot["pieces"] = [
                {"in_s": round(float(slots[index]["in_s"]), 3), "out_s": round(float(slots[index]["out_s"]), 3)}
                for index in indices
            ]
        report_shots.append(report_shot)

    embedder = None
    if similar_fn is None:
        embedder = WindowEmbedder(workspace)
        similar_fn = embedder.similarity
    similar_neighbours = _similar_neighbours(slots, similar_fn, could_not_do)
    if embedder is not None:
        embedder.save()

    target_length = plan["brief"].get("target_duration_s")
    total_length = sum(slot_duration(slot) for slot in slots)
    wall_time = time.monotonic() - started
    edit = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "tool": TOOL,
        "brief": {
            "text": plan["brief"]["text"],
            "target_duration_s": target_length,
            "order": plan["brief"].get("order"),
            "pacing": plan["brief"].get("pacing"),
        },
        "sources": _sources(slots, clips, workspace),
        "video": slots,
        "audio": {
            "ambience": {"enabled": True, "gain_db": 0, "source": "slot", "file": None},
            "music": None,
            "ambient_under_music_db": None,
            "sfx": [],
            "levels": {"target_lufs": None, "true_peak_db": None},
        },
        "captions": [],
        "graphics": [],
        "meta": {"tool": TOOL, "wall_time_s": round(wall_time, 3), "llm_cost_usd": 0.0},
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "tool": TOOL,
        "shots": report_shots,
        "total_length_s": round(total_length, 3),
        "target_length_s": target_length,
        "could_not_do": could_not_do,
        "similar_neighbours": similar_neighbours,
        "summary": _summary_sentence(
            len(report_shots), total_length, target_length, len(could_not_do), len(similar_neighbours),
        ),
        "wall_time_s": round(wall_time, 3),
        "llm_cost_usd": 0.0,
    }
    try:
        validate_or_raise(edit, "edit")
        validate_or_raise(report, "editor_report")
    except SchemaError as exc:
        raise EditorRuntimeError(f"the editor built an invalid record: {exc}") from exc
    out_file = Path(out_path).expanduser() if out_path else workspace / "edit.json"
    report_file = Path(report_path).expanduser() if report_path else workspace / "editor_report.json"
    try:
        write_json(out_file, edit)
        write_json(report_file, report)
    except OSError as exc:
        raise EditorRuntimeError(f"could not write the edit or the report: {exc}") from exc
    summary = {
        "ok": True,
        "workspace": str(workspace),
        "plan": str(plan_file),
        "edit": str(out_file),
        "report": str(report_file),
        "slots": len(slots),
        "total_length_s": round(total_length, 3),
        "target_length_s": target_length,
        "could_not_do": len(could_not_do),
        "similar_neighbours": len(similar_neighbours),
        **summary_fields(started),
    }
    return edit, report, summary


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        _, _, summary = run_editor(args.workspace, args.plan, args.out, args.report, args.beats)
    except EditorRuntimeError as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except EditorError as exc:
        return report_error(exc, EXIT_USAGE, args.json)
    if args.json:
        print(json.dumps(summary))
    else:
        print(f"{summary['slots']} slots, {summary['total_length_s']:.1f}s -> {summary['edit']}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
