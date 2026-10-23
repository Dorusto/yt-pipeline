#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Matte the camera ranges over the screen spans and write the FilmCraft PiP steps.

Usage:
    python3 scripts/finish_pip.py --sequence SEQ.json --camera-dir DIR [options]

The tool reads a FilmCraft sequence inspect JSON, finds the spans where the
screen track has video content, lists and mattes the camera ranges that cover
them (padded and merged) and writes the ``filmcraft-cli`` steps that put the
matted person as a picture-in-picture bottom-right over each span with a quick
eased entry and a short anticipation exit, snapped to the screen span edges. The
place steps go to ``finish_pip.place.jsonl``; the motion keyframes go to
``finish_pip.animate.jsonl`` and are generated from a re-inspect after the place
run, because the engine assigns clip ids at run time. The place and animate
phases run against a live FilmCraft with ``--bridge HOST:PORT`` or against a
headless project file with ``--project FILE`` (the two are mutually exclusive);
with neither the tool only mattes and plans, because the ``timeline.place`` item
ids come from the import into that same project. No model is called, so
``llm_cost_usd`` is always 0.0.
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

from common.constants import TICKS_PER_SECOND
from common.filmcraft import cli_path, parse_frame_rate
from common.media import clip_kind, is_matte_export, is_video_name

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_PAD_S = 0.5
DEFAULT_MERGE_GAP_S = 2.0
DEFAULT_SCALE = 45.0
DEFAULT_SCALE_SMALL = 36.0
DEFAULT_SMALL_OVER = r"(?i)(explainer|explica|grafic|anim)"
SPAN_GAP_S = 0.05
EDGE_TOLERANCE_S = 0.05
EDGE_MAX_OVERLAP_S = 1.0
LEFTOVER_MIN_OVERLAP_S = 0.5
MARGIN_FRACTION = 0.05
ENTRY_TOTAL_S = 0.30
ENTRY_OVERSHOOT_S = 0.18
ENTRY_START_SCALE = 1.15
ENTRY_OVERSHOOT_SCALE = 0.96
ENTRY_START_OFFSET = 0.06
ENTRY_OVERSHOOT_OFFSET = 0.01
EXIT_TOTAL_S = 0.25
EXIT_ANTICIPATION_S = 0.10
EXIT_ANTICIPATION_SCALE = 1.03
EXIT_ANTICIPATION_OFFSET = 0.02
EXIT_OUT_SCALE = 0.5
PLACE_FILE = "finish_pip.place.jsonl"
ANIMATE_FILE = "finish_pip.animate.jsonl"
APPLIED_FILE = "finish_pip.animate.applied"


class FinishPipError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


class FinishPipRuntimeError(Exception):
    """An unexpected failure after validation that exits with code 1."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="finish_pip.py",
        description=(
            "Matte the camera ranges over the screen spans of a FilmCraft sequence and "
            "write the filmcraft-cli steps that place them as a bottom-right picture-in-picture."
        ),
        epilog=(
            "examples:\n"
            "  python3 scripts/finish_pip.py --sequence seq.json --camera-dir camera/ --dry-run\n"
            "  python3 scripts/finish_pip.py --sequence seq.json --camera-dir camera/ --json\n"
            "  python3 scripts/finish_pip.py --sequence seq.json --camera-dir camera/ "
            "--bridge 127.0.0.1:7714\n"
            "  python3 scripts/finish_pip.py --sequence seq.json --camera-dir camera/ "
            "--project project.fcproj\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--sequence", default=None, help="FilmCraft sequence inspect JSON (required, read-only)")
    parser.add_argument("--camera-dir", default=None, help="folder holding the camera source files (required, read-only)")
    parser.add_argument("--matte-dir", default="matte", help="where the matte .mov files are written (default matte/)")
    parser.add_argument("--out-dir", default=".", help="where the steps files are written (default .)")
    parser.add_argument("--bridge", default=None, help="HOST:PORT of the live FilmCraft to run the steps against")
    parser.add_argument("--project", default=None, help=".fcproj file to run the steps against headless (mutually exclusive with --bridge)")
    parser.add_argument("--pad-s", type=float, default=DEFAULT_PAD_S, help=f"padding per side in seconds (default {DEFAULT_PAD_S})")
    parser.add_argument("--merge-gap-s", type=float, default=DEFAULT_MERGE_GAP_S, help=f"merge camera ranges under this gap (default {DEFAULT_MERGE_GAP_S})")
    parser.add_argument("--scale", type=float, default=DEFAULT_SCALE, help=f"PiP scale in percent (default {DEFAULT_SCALE:g})")
    parser.add_argument("--scale-small", type=float, default=DEFAULT_SCALE_SMALL, help=f"PiP scale over explainer scenes (default {DEFAULT_SCALE_SMALL:g})")
    parser.add_argument("--small-over", default=DEFAULT_SMALL_OVER, help="regex matching a full-screen explainer screen item name")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and write nothing")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    parser.add_argument("--force", action="store_true", help="re-run matte.py even when the output exists")
    return parser


def target_flags(bridge, project, save=False):
    """Return the filmcraft-cli target flags for a live bridge or a headless project file."""
    if bridge:
        return ["--bridge", bridge]
    if project:
        flags = ["--project", str(project)]
        if save:
            flags.append("--save")
        return flags
    return []


def load_sequence(path):
    """Read the sequence inspect JSON, raising a named error when it is missing or unreadable."""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FinishPipError(f"cannot read '{path}': file not found")
    except json.JSONDecodeError as exc:
        raise FinishPipError(f"cannot read '{path}': not valid JSON ({exc})")
    except OSError as exc:
        raise FinishPipError(f"cannot read '{path}': {exc}")
    if not isinstance(data, dict) or "video" not in data:
        raise FinishPipError(f"'{path}' is not a FilmCraft sequence inspect JSON")
    return data


def sequence_settings(sequence):
    """Return the frame size and rate from the sequence settings, with sane defaults."""
    settings = sequence.get("settings") or {}
    width = settings.get("width") or DEFAULT_WIDTH
    height = settings.get("height") or DEFAULT_HEIGHT
    return {"width": int(width), "height": int(height), "fps": parse_frame_rate(settings)}


def classify_track(track):
    """Return ``camera``, ``screen`` or None for a video track's enabled, non-matte video items."""
    kinds = set()
    for item in track.get("items") or []:
        if not item.get("enabled", True):
            continue
        name = item.get("name") or ""
        if is_matte_export(name) or not is_video_name(name):
            continue
        kind = clip_kind(name)
        if kind:
            kinds.add(kind)
    if len(kinds) == 1:
        return next(iter(kinds))
    return None


def find_track(sequence, kind):
    """Return the first video track whose items classify as ``kind``, or None."""
    for track in sequence.get("video") or []:
        if classify_track(track) == kind:
            return track
    return None


def screen_spans(sequence, small_over):
    """Return the maximal runs of enabled screen video items as spans with an explainer flag.

    Only video clips make a span: a still image, a graphic or a nameless generator
    item on the screen track is ignored and never classifies the track.
    """
    track = find_track(sequence, "screen")
    if track is None:
        return []
    items = [
        item
        for item in track.get("items") or []
        if item.get("enabled", True)
        and not is_matte_export(item.get("name") or "")
        and is_video_name(item.get("name") or "")
    ]
    items.sort(key=lambda item: int(item.get("start", 0)))
    gap = int(round(SPAN_GAP_S * TICKS_PER_SECOND))
    spans = []
    for item in items:
        start = int(item.get("start", 0))
        end = int(item.get("end", 0))
        name = item.get("name") or ""
        if spans and start - spans[-1]["end"] < gap:
            spans[-1]["end"] = max(spans[-1]["end"], end)
            spans[-1]["names"].append(name)
        else:
            spans.append({"start": start, "end": end, "names": [name]})
    for span in spans:
        span["explainer"] = any(small_over.search(name) for name in span["names"])
    return spans


def camera_pieces(sequence, spans, pad_s):
    """Return the camera source pieces covering the spans, padded per side and clamped at 0."""
    track = find_track(sequence, "camera")
    if track is None:
        return []
    items = [
        item
        for item in track.get("items") or []
        if item.get("enabled", True)
        and not is_matte_export(item.get("name") or "")
        and is_video_name(item.get("name") or "")
    ]
    pad = int(round(pad_s * TICKS_PER_SECOND))
    pieces = []
    for span in spans:
        for item in items:
            item_start = int(item.get("start", 0))
            item_end = int(item.get("end", 0))
            overlap_start = max(item_start, span["start"])
            overlap_end = min(item_end, span["end"])
            if overlap_end <= overlap_start:
                continue
            source_in = int(item.get("sourceIn", 0))
            raw_start = source_in + (overlap_start - item_start)
            raw_end = source_in + (overlap_end - item_start)
            pieces.append({
                "span": span,
                "source": item.get("name") or "",
                "timeline_start": overlap_start,
                "timeline_end": overlap_end,
                "raw_source_start": raw_start,
                "padded_start": max(0, raw_start - pad),
                "padded_end": raw_end + pad,
                "clip_start": item_start,
                "clip_end": item_end,
                "overlap": overlap_end - overlap_start,
                "clip_length": item_end - item_start,
            })
    return pieces


def classify_edge_piece(piece):
    """Return ``(verdict, reason)`` for a short piece at a span edge, or ``(None, None)``.

    A piece is an edge piece when its clip straddles the span edge (tolerance
    0.05 s) or its clip start/end sits within 0.05 s of that edge and its overlap
    with the span is at most 1.0 s. A leftover is a fragment of the surrounding
    full-screen stretch and is dropped; a seam is a real camera cut at the edge
    and is kept.
    """
    tolerance = int(round(EDGE_TOLERANCE_S * TICKS_PER_SECOND))
    max_overlap = int(round(EDGE_MAX_OVERLAP_S * TICKS_PER_SECOND))
    min_overlap = int(round(LEFTOVER_MIN_OVERLAP_S * TICKS_PER_SECOND))
    span = piece["span"]
    straddles = piece["clip_start"] < span["start"] - tolerance or piece["clip_end"] > span["end"] + tolerance
    near_edge = (
        abs(piece["clip_start"] - span["start"]) <= tolerance
        or abs(piece["clip_end"] - span["end"]) <= tolerance
    )
    if not (straddles or near_edge) or piece["overlap"] > max_overlap:
        return None, None
    if straddles and piece["overlap"] * 2 < piece["clip_length"]:
        return "leftover", "straddles the span edge, overlap under half the clip"
    if piece["overlap"] < min_overlap:
        return "leftover", "overlap under 0.5 s (split artifact)"
    return "seam", "camera cut at the span edge"


def split_edge_pieces(pieces):
    """Return ``(edge_checks, kept_pieces)``: the reported edge pieces and the pieces to matte."""
    checks = []
    kept = []
    for piece in pieces:
        verdict, reason = classify_edge_piece(piece)
        if verdict is None:
            kept.append(piece)
            continue
        checks.append({
            "source": piece["source"],
            "padded_start": piece["padded_start"],
            "padded_end": piece["padded_end"],
            "overlap": piece["overlap"],
            "clip_length": piece["clip_length"],
            "verdict": verdict,
            "reason": reason,
        })
        if verdict == "seam":
            kept.append(piece)
    checks.sort(key=lambda check: (check["padded_start"], check["source"]))
    return checks, kept


def merge_ranges(pieces, merge_gap_s):
    """Merge the padded pieces of one source under the gap into one matte range each."""
    gap = int(round(merge_gap_s * TICKS_PER_SECOND))
    by_source = {}
    for piece in pieces:
        by_source.setdefault(piece["source"], []).append(piece)
    ranges = []
    for source, group in by_source.items():
        group.sort(key=lambda piece: piece["padded_start"])
        merged = []
        for piece in group:
            if merged and piece["padded_start"] - merged[-1]["end"] < gap:
                merged[-1]["end"] = max(merged[-1]["end"], piece["padded_end"])
                merged[-1]["pieces"].append(piece)
            else:
                merged.append({
                    "source": source,
                    "start": piece["padded_start"],
                    "end": piece["padded_end"],
                    "pieces": [piece],
                })
        ranges.extend(merged)
    ranges.sort(key=lambda entry: (entry["source"], entry["start"]))
    return ranges


def matte_output(matte_dir, source, start_ticks, end_ticks):
    """Return the matte .mov path for a source range (``<stem>_<A:07.2f>-<B:07.2f>.mov``)."""
    stem = Path(source).stem
    start_s = start_ticks / TICKS_PER_SECOND
    end_s = end_ticks / TICKS_PER_SECOND
    return Path(matte_dir) / f"{stem}_{start_s:07.2f}-{end_s:07.2f}.mov"


def build_placements(ranges):
    """Return one placement per (span, camera piece), trimmed to the span."""
    placements = []
    for entry in ranges:
        for piece in entry["pieces"]:
            placements.append({
                "span": piece["span"],
                "source": piece["source"],
                "matte_out": entry["out"],
                "timeline_start": piece["timeline_start"],
                "timeline_end": piece["timeline_end"],
                "duration": piece["timeline_end"] - piece["timeline_start"],
                "source_in": piece["raw_source_start"] - entry["start"],
                "range": entry,
            })
    placements.sort(key=lambda placement: (placement["timeline_start"], placement["source"]))
    return placements


def find_pip_track(sequence):
    """Return ``(index, track)`` of the video track holding only matte items, or ``(None, None)``."""
    for index, track in enumerate(sequence.get("video") or [], start=1):
        items = track.get("items") or []
        if items and all(is_matte_export(item.get("name") or "") for item in items):
            return index, track
    return None, None


def find_placed_item(placement, pip_track):
    """Return the PiP track item holding this placement's matte at the same time, or None."""
    if pip_track is None:
        return None
    name = Path(placement["matte_out"]).name
    tolerance = TICKS_PER_SECOND // 10
    best = None
    for item in pip_track.get("items") or []:
        if (item.get("name") or "") != name:
            continue
        distance = abs(int(item.get("start", 0)) - placement["timeline_start"])
        if distance <= tolerance and (best is None or distance < best[0]):
            best = (distance, item)
    return best[1] if best is not None else None


def already_placed(placement, pip_track):
    """Return True when the PiP track already holds this matte at the same time."""
    return find_placed_item(placement, pip_track) is not None


def animate_steps_cover_clip(out_dir, clip):
    """Return True when an applied animate steps file holds a setParam step for this clip id."""
    if not (Path(out_dir) / APPLIED_FILE).is_file():
        return False
    path = Path(out_dir) / ANIMATE_FILE
    if not path.is_file():
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            step = json.loads(line)
        except json.JSONDecodeError:
            continue
        if step.get("id") == "effects.setParam" and (step.get("params") or {}).get("clip") == clip:
            return True
    return False


def keyframes_in_place(item, out_dir):
    """Return True when the placed PiP item shows the animate keyframes.

    When the item exposes no ``effects`` key at all, fall back to the animate
    steps file: it counts as keyframed when it was applied and holds a
    ``effects.setParam`` step for the item's clip id. A placed item with no clip
    id is never keyframed.
    """
    if item is None:
        return False
    if "effects" in item:
        for effect in item.get("effects") or []:
            if effect.get("effect") != "motion":
                continue
            params = effect.get("params") or {}
            scale = (params.get("scale") or {}).get("keyframes") or []
            position = (params.get("position") or {}).get("keyframes") or []
            if scale and position:
                return True
        return False
    clip = item.get("clip")
    if clip is None:
        return False
    return animate_steps_cover_clip(out_dir, clip)


def attach_clips(sequence, placements):
    """Attach the placed clip id and start to every placement found on the PiP track.

    Returns a warning for every placement whose matte item carries no clip id, so
    the animate phase skips its keyframes instead of crashing.
    """
    warnings = []
    _, pip_track = find_pip_track(sequence)
    if pip_track is None:
        return warnings
    for placement in placements:
        item = find_placed_item(placement, pip_track)
        if item is None:
            continue
        clip = item.get("clip")
        if clip is None:
            warnings.append(f"placed matte has no clip id, keyframes skipped: {Path(placement['matte_out']).name}")
            continue
        placement["clip"] = int(clip)
        placement["clip_start"] = int(item.get("start", 0))
    return warnings


def final_pose(settings, scale):
    """Return the bottom-right final pose (percent scale, frame-pixel centre)."""
    width = settings["width"]
    height = settings["height"]
    margin = MARGIN_FRACTION * height
    scaled_w = width * scale / 100.0
    scaled_h = height * scale / 100.0
    return {
        "scale": scale,
        "position": [width - margin - scaled_w / 2.0, height - margin - scaled_h / 2.0],
    }


def span_scale(span, options):
    return options["scale_small"] if span["explainer"] else options["scale"]


def set_param(clip, param, value, time_ticks):
    return {
        "id": "effects.setParam",
        "params": {
            "clip": clip,
            "effect": "motion",
            "param": param,
            "value": value,
            "time": time_ticks,
            "keyframe": True,
        },
    }


def set_interpolation(clip, param, media_time, interpolation):
    return {
        "id": "effects.setInterpolation",
        "params": {
            "clip": clip,
            "effect": "motion",
            "param": param,
            "mediaTime": media_time,
            "interpolation": interpolation,
        },
    }


def entry_keyframes(span, pose, settings):
    """Return the entry keyframes as ``(time, scale, position)`` (quick ease-out with a slight overshoot)."""
    width = settings["width"]
    height = settings["height"]
    return [
        (
            span["start"],
            pose["scale"] * ENTRY_START_SCALE,
            [
                pose["position"][0] + ENTRY_START_OFFSET * width,
                pose["position"][1] + ENTRY_START_OFFSET * height,
            ],
        ),
        (
            span["start"] + int(round(ENTRY_OVERSHOOT_S * TICKS_PER_SECOND)),
            pose["scale"] * ENTRY_OVERSHOOT_SCALE,
            [
                pose["position"][0] + ENTRY_OVERSHOOT_OFFSET * width,
                pose["position"][1] + ENTRY_OVERSHOOT_OFFSET * height,
            ],
        ),
        (span["start"] + int(round(ENTRY_TOTAL_S * TICKS_PER_SECOND)), pose["scale"], pose["position"]),
    ]


def exit_keyframes(span, pose, settings):
    """Return the exit keyframes as ``(time, scale, position)`` (short anticipation)."""
    width = settings["width"]
    height = settings["height"]
    return [
        (span["end"] - int(round(EXIT_TOTAL_S * TICKS_PER_SECOND)), pose["scale"], pose["position"]),
        (
            span["end"] - int(round(EXIT_ANTICIPATION_S * TICKS_PER_SECOND)),
            pose["scale"] * EXIT_ANTICIPATION_SCALE,
            [
                pose["position"][0] + EXIT_ANTICIPATION_OFFSET * width,
                pose["position"][1] + EXIT_ANTICIPATION_OFFSET * height,
            ],
        ),
        (span["end"], pose["scale"] * EXIT_OUT_SCALE, pose["position"]),
    ]


def media_time(placement, timeline_time):
    """Return the source-relative media time of a timeline time on a placed clip."""
    return timeline_time - placement["clip_start"] + placement["source_in"]


def entry_steps(span, pose, placement, settings):
    """Return the entry keyframes (quick ease-out with a slight overshoot) for one piece."""
    clip = placement["clip"]
    media = media_time(placement, span["start"])
    steps = []
    for time_ticks, scale, position in entry_keyframes(span, pose, settings):
        steps.append(set_param(clip, "scale", scale, time_ticks))
        steps.append(set_param(clip, "position", position, time_ticks))
    steps.append(set_interpolation(clip, "scale", media, "easeOut"))
    steps.append(set_interpolation(clip, "position", media, "easeOut"))
    return steps


def exit_steps(span, pose, placement, settings):
    """Return the exit keyframes (short anticipation) for one piece."""
    clip = placement["clip"]
    media = media_time(placement, span["end"])
    steps = []
    for time_ticks, scale, position in exit_keyframes(span, pose, settings):
        steps.append(set_param(clip, "scale", scale, time_ticks))
        steps.append(set_param(clip, "position", position, time_ticks))
    steps.append(set_interpolation(clip, "scale", media, "easeIn"))
    steps.append(set_interpolation(clip, "position", media, "easeIn"))
    return steps


def build_animate_steps(spans, placements, options):
    """Return the motion keyframes for the placed clips, one pose per span."""
    settings = options["settings"]
    steps = []
    for span in spans:
        group = [placement for placement in placements if placement["span"] is span]
        if not group:
            continue
        pose = final_pose(settings, span_scale(span, options))
        entry = next(
            (p for p in group if p["timeline_start"] <= span["start"] < p["timeline_end"]),
            None,
        )
        exit_piece = next(
            (p for p in group if p["timeline_start"] < span["end"] <= p["timeline_end"]),
            None,
        )
        if entry is not None and "clip" in entry:
            steps.extend(entry_steps(span, pose, entry, settings))
        if exit_piece is not None and "clip" in exit_piece:
            steps.extend(exit_steps(span, pose, exit_piece, settings))
    return steps


def snap_source_in(ticks, fps):
    """Snap a source offset to the frame grid, like the by-hand placement pass."""
    seconds_value = ticks / TICKS_PER_SECOND
    return int(round(round(seconds_value * fps) / fps * TICKS_PER_SECOND))


def build_place_steps(placements, item_ids, pip_index, pip_exists):
    """Return the timeline.place steps for the new placements on one PiP track."""
    steps = []
    first = True
    for placement in placements:
        if pip_exists:
            track = f"V{pip_index}"
        elif first:
            track = "new"
        else:
            track = f"V{pip_index}"
        first = False
        steps.append({
            "id": "timeline.place",
            "params": {
                "item": item_ids[placement["matte_out"]],
                "track": track,
                "time": placement["timeline_start"],
                "insert": False,
                "audio": False,
                "sourceIn": placement["source_in"],
                "duration": placement["duration"],
            },
        })
    return steps


def write_steps(path, steps):
    """Write the steps as one JSON object per line, replacing the file atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            for step in steps:
                stream.write(json.dumps(step) + "\n")
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def write_animate_steps(out_dir, steps):
    """Write the animate steps and clear the applied marker: the file is not applied yet."""
    out_dir = Path(out_dir)
    write_steps(out_dir / ANIMATE_FILE, steps)
    (out_dir / APPLIED_FILE).unlink(missing_ok=True)


def mark_animate_applied(out_dir):
    """Mark the animate steps file as applied, so it counts as keyframe evidence."""
    (Path(out_dir) / APPLIED_FILE).write_text("", encoding="utf-8")


def run_matte(source, start_ticks, end_ticks, out):
    """Run matte.py over the source range and write the matte .mov."""
    command = [
        sys.executable,
        str(Path(__file__).resolve().parent / "matte.py"),
        str(source),
        "--start",
        f"{start_ticks / TICKS_PER_SECOND:.3f}",
        "--end",
        f"{end_ticks / TICKS_PER_SECOND:.3f}",
        "--out",
        str(out),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise FinishPipRuntimeError(f"could not run matte.py: {exc}")
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise FinishPipRuntimeError(f"matte.py failed for {source}: {detail[-1] if detail else 'unknown error'}")


def import_mattes(paths, bridge, project):
    """Import the matte files and return ``{path: item id}`` from the CLI reply."""
    if not paths:
        return {}
    binary = cli_path()
    if binary is None:
        raise FinishPipRuntimeError("filmcraft-cli not found on PATH (set $FILMCRAFT_CLI)")
    command = [binary] + target_flags(bridge, project, save=True)
    command += ["--compact", "import"] + [str(path) for path in paths]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise FinishPipRuntimeError(f"could not run filmcraft-cli: {exc}")
    if result.returncode != 0:
        raise FinishPipRuntimeError(f"filmcraft-cli import failed: {(result.stderr or '').strip()}")
    try:
        data = json.loads((result.stdout or "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise FinishPipRuntimeError(f"filmcraft-cli import returned invalid JSON: {exc}")
    items = data.get("items") or []
    if len(items) != len(paths):
        raise FinishPipRuntimeError(f"filmcraft-cli import returned {len(items)} items for {len(paths)} files")
    return {Path(path): item for path, item in zip(paths, items)}


def run_steps(path, bridge, project):
    """Run a steps file against the live FilmCraft through the bridge or a headless project file."""
    binary = cli_path()
    if binary is None:
        raise FinishPipRuntimeError("filmcraft-cli not found on PATH (set $FILMCRAFT_CLI)")
    command = [binary] + target_flags(bridge, project, save=True)
    command += ["run", str(path)]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise FinishPipRuntimeError(f"could not run filmcraft-cli: {exc}")
    if result.returncode != 0:
        raise FinishPipRuntimeError(f"filmcraft-cli run failed: {(result.stderr or '').strip()}")


def inspect_sequence(bridge, project):
    """Re-inspect the sequence through the bridge or a headless project file and return the JSON."""
    binary = cli_path()
    if binary is None:
        raise FinishPipRuntimeError("filmcraft-cli not found on PATH (set $FILMCRAFT_CLI)")
    command = [binary] + target_flags(bridge, project)
    command += ["inspect", "sequence"]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise FinishPipRuntimeError(f"could not run filmcraft-cli: {exc}")
    if result.returncode != 0:
        raise FinishPipRuntimeError(f"filmcraft-cli inspect failed: {(result.stderr or '').strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FinishPipRuntimeError(f"filmcraft-cli inspect returned invalid JSON: {exc}")


def seconds(ticks):
    return round(ticks / TICKS_PER_SECOND, 3)


def print_plan(spans, ranges, placements, edge_checks, options, nothing_to_do):
    """Print the human plan: spans, edge checks, camera ranges, placements and keyframe poses."""
    settings = options["settings"]
    print(f"spans: {len(spans)}")
    for span in spans:
        print(f"  [{seconds(span['start']):.3f}-{seconds(span['end']):.3f}] explainer={span['explainer']}")
    print("edge checks:")
    for check in edge_checks:
        print(
            f"  {check['source']} [{seconds(check['padded_start']):.3f}-{seconds(check['padded_end']):.3f}] "
            f"overlap={seconds(check['overlap']):.3f}s clip={seconds(check['clip_length']):.3f}s "
            f"-> {check['verdict']} ({check['reason']})"
        )
    print(f"ranges: {len(ranges)}")
    for entry in ranges:
        print(
            f"  {entry['source']} [{seconds(entry['start']):.3f}-{seconds(entry['end']):.3f}] "
            f"-> {entry['out']} matted={'yes' if entry.get('matted') else 'no'} "
            f"placed={'yes' if entry.get('placed') else 'no'} "
            f"keyframed={'yes' if entry.get('keyframed') else 'no'}"
        )
    if nothing_to_do:
        print("nothing to do")
        return
    print(f"placements: {len(placements)}")
    for placement in placements:
        span = placement["span"]
        pose = final_pose(settings, span_scale(span, options))
        print(
            f"  span [{seconds(span['start']):.3f}-{seconds(span['end']):.3f}] "
            f"{placement['source']} [{seconds(placement['timeline_start']):.3f}-"
            f"{seconds(placement['timeline_end']):.3f}] sourceIn={placement['source_in']} "
            f"duration={placement['duration']}"
        )
        print(f"    final scale={pose['scale']:g} position=[{pose['position'][0]:.1f}, {pose['position'][1]:.1f}]")
        for label, keyframes in (("entry", entry_keyframes(span, pose, settings)), ("exit", exit_keyframes(span, pose, settings))):
            for time_ticks, scale, position in keyframes:
                print(
                    f"    {label} t={seconds(time_ticks):.3f} scale={scale:.2f} "
                    f"position=[{position[0]:.1f}, {position[1]:.1f}]"
                )


def apply_run_state(ranges, placements, animate_applied):
    """Set each range's placed/keyframed from what the run left behind.

    A range is placed when every placement of it was already on the PiP track
    before the run or was placed by this run. It is keyframed when it is placed
    and every placement was already keyframed before the run or this run's
    animate phase applied and the placement carries a clip id.
    """
    for entry in ranges:
        group = [placement for placement in placements if placement["range"] is entry]
        placed = all(
            placement.get("placed_before") or placement.get("placed_this_run")
            for placement in group
        )
        keyframed = placed and all(
            placement.get("keyframed_before") or (animate_applied and "clip" in placement)
            for placement in group
        )
        entry["placed"] = placed
        entry["keyframed"] = keyframed


def run(sequence_path, camera_dir, options):
    """Build the plan, matte the ranges, write the steps and return the summary."""
    started = time.monotonic()
    if options["bridge"] and options["project"]:
        raise FinishPipError("--bridge and --project are mutually exclusive")
    if not sequence_path:
        raise FinishPipError("--sequence is required")
    if not camera_dir:
        raise FinishPipError("--camera-dir is required")
    sequence = load_sequence(sequence_path)
    settings = sequence_settings(sequence)
    options["settings"] = settings
    try:
        small_over = re.compile(options["small_over"])
    except re.error as exc:
        raise FinishPipError(f"--small-over is not a valid regex: {exc}")

    camera = Path(camera_dir).expanduser()
    if not camera.is_dir():
        raise FinishPipError(f"--camera-dir is not a directory: {camera}")
    camera_resolved = camera.resolve()
    for label, value in (("--matte-dir", options["matte_dir"]), ("--out-dir", options["out_dir"])):
        resolved = Path(value).expanduser().resolve()
        if resolved == camera_resolved or camera_resolved in resolved.parents:
            raise FinishPipError(f"{label} must not be inside the camera folder: {value}")

    spans = screen_spans(sequence, small_over)
    pieces = camera_pieces(sequence, spans, options["pad_s"])
    edge_checks, kept_pieces = split_edge_pieces(pieces)
    ranges = merge_ranges(kept_pieces, options["merge_gap_s"])
    for entry in ranges:
        entry["out"] = matte_output(options["matte_dir"], entry["source"], entry["start"], entry["end"])
    placements = build_placements(ranges)
    for placement in placements:
        placement["source_in"] = snap_source_in(placement["source_in"], settings["fps"])

    video_count = len(sequence.get("video") or [])
    pip_index, pip_track = find_pip_track(sequence)
    if pip_index is None:
        pip_index = video_count + 1
    new_placements = [placement for placement in placements if not already_placed(placement, pip_track)]
    for placement in placements:
        item = find_placed_item(placement, pip_track)
        placement["placed_before"] = item is not None
        placement["keyframed_before"] = item is not None and keyframes_in_place(item, options["out_dir"])
    for entry in ranges:
        entry["matted"] = entry["out"].exists()
    apply_run_state(ranges, placements, animate_applied=False)
    nothing_to_do = not new_placements and all(
        entry["matted"] and entry["keyframed"] for entry in ranges
    )

    if options["dry_run"]:
        if not options["json"]:
            print_plan(spans, ranges, new_placements, edge_checks, options, nothing_to_do)
        return build_summary(spans, ranges, new_placements, edge_checks, [], [], started, nothing_to_do)

    if nothing_to_do:
        return build_summary(spans, ranges, new_placements, edge_checks, [], [], started, True)

    warnings = []
    for entry in ranges:
        out = entry["out"]
        if out.exists() and not options["force"]:
            entry["matted"] = True
            continue
        source_path = camera / entry["source"]
        if not source_path.is_file():
            warnings.append(f"missing camera source: {source_path}")
            entry["matted"] = False
            continue
        run_matte(source_path, entry["start"], entry["end"], out)
        entry["matted"] = out.exists()

    needed = {placement["matte_out"] for placement in new_placements}
    matte_paths = [entry["out"] for entry in ranges if entry.get("matted") and entry["out"] in needed]

    out_dir = Path(options["out_dir"]).expanduser()
    steps_files = []
    animate_applied = False

    if options["bridge"] or options["project"]:
        if new_placements:
            item_ids = import_mattes(matte_paths, options["bridge"], options["project"])
            placeable = [placement for placement in new_placements if placement["matte_out"] in item_ids]
            warned = set()
            for placement in new_placements:
                if placement["matte_out"] not in item_ids and placement["matte_out"] not in warned:
                    warned.add(placement["matte_out"])
                    warnings.append(f"matte not imported, placement skipped: {placement['matte_out']}")

            place_steps = build_place_steps(placeable, item_ids, pip_index, pip_track is not None)
            write_steps(out_dir / PLACE_FILE, place_steps)
            steps_files.append(PLACE_FILE)
            run_steps(out_dir / PLACE_FILE, options["bridge"], options["project"])
            for placement in placeable:
                placement["placed_this_run"] = True

        fresh = inspect_sequence(options["bridge"], options["project"])
        warnings.extend(attach_clips(fresh, placements))
        animate_steps = build_animate_steps(spans, placements, options)
        write_animate_steps(out_dir, animate_steps)
        steps_files.append(ANIMATE_FILE)
        try:
            run_steps(out_dir / ANIMATE_FILE, options["bridge"], options["project"])
            mark_animate_applied(out_dir)
            animate_applied = True
        except Exception:
            (out_dir / ANIMATE_FILE).unlink(missing_ok=True)
            (out_dir / APPLIED_FILE).unlink(missing_ok=True)
            raise
    elif not new_placements:
        warnings.extend(attach_clips(sequence, placements))
        animate_steps = build_animate_steps(spans, placements, options)
        write_animate_steps(out_dir, animate_steps)
        steps_files.append(ANIMATE_FILE)
    else:
        warnings.append("place steps need --bridge or --project")
        if not options["json"]:
            print_next_steps(out_dir, ranges, options)

    apply_run_state(ranges, placements, animate_applied)
    return build_summary(spans, ranges, new_placements, edge_checks, steps_files, warnings, started, False)


def print_next_steps(out_dir, ranges, options):
    """Print the commands to run the place phase and the re-inspect for the animate phase."""
    matte_paths = [str(entry["out"]) for entry in ranges if entry.get("matted")]
    if options["bridge"]:
        target = f"--bridge {options['bridge']}"
    elif options["project"]:
        target = f"--project {options['project']} --save"
    else:
        target = "--bridge HOST:PORT"
    print("next:")
    if not options["bridge"] and not options["project"]:
        print("  (add --bridge HOST:PORT or --project FILE to run the place phase)")
    if matte_paths:
        print(f"  filmcraft-cli {target} --compact import {' '.join(matte_paths)}")
    print(f"  filmcraft-cli {target} run {out_dir / PLACE_FILE}")
    print(f"  filmcraft-cli {target} inspect sequence > sequence.json")
    print(
        "  python3 scripts/finish_pip.py --sequence sequence.json "
        f"--camera-dir {options['camera_dir']} --out-dir {out_dir}"
    )


def build_summary(spans, ranges, placements, edge_checks, steps_files, warnings, started, nothing_to_do):
    """Return the machine-readable summary object."""
    return {
        "ok": True,
        "spans": [
            {"start_s": seconds(span["start"]), "end_s": seconds(span["end"]), "explainer": span["explainer"]}
            for span in spans
        ],
        "ranges": [
            {
                "source": entry["source"],
                "start_s": seconds(entry["start"]),
                "end_s": seconds(entry["end"]),
                "out": str(entry["out"]),
                "matted": bool(entry.get("matted", False)),
                "placed": bool(entry.get("placed", False)),
                "keyframed": bool(entry.get("keyframed", False)),
            }
            for entry in ranges
        ],
        "placements": len(placements),
        "edge_checks": [
            {
                "source": check["source"],
                "start_s": seconds(check["padded_start"]),
                "end_s": seconds(check["padded_end"]),
                "overlap_s": seconds(check["overlap"]),
                "clip_s": seconds(check["clip_length"]),
                "verdict": check["verdict"],
                "reason": check["reason"],
            }
            for check in edge_checks
        ],
        "nothing_to_do": nothing_to_do,
        "steps_files": steps_files,
        "warnings": warnings,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    options = {
        "matte_dir": args.matte_dir,
        "out_dir": args.out_dir,
        "bridge": args.bridge,
        "project": args.project,
        "pad_s": args.pad_s,
        "merge_gap_s": args.merge_gap_s,
        "scale": args.scale,
        "scale_small": args.scale_small,
        "small_over": args.small_over,
        "dry_run": args.dry_run,
        "json": args.json,
        "force": args.force,
        "camera_dir": args.camera_dir,
    }
    try:
        summary = run(args.sequence, args.camera_dir, options)
    except FinishPipError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    if args.json:
        print(json.dumps(summary))
    elif args.dry_run:
        pass
    elif summary["nothing_to_do"]:
        print("nothing to do")
    else:
        print(f"written: {', '.join(summary['steps_files']) or 'nothing'} ({summary['placements']} placements)")
        for warning in summary["warnings"]:
            print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
