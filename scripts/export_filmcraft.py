#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Export an ``edit.json`` edit list to an editable FilmCraft ``.fcproj`` project.

Usage:
    python3 scripts/export_filmcraft.py edit.json --out project.fcproj
        [--graphics-plan FILE] [--graphics-dir DIR]
        [--sequence NAME] [--width W] [--height H] [--fps N]
        [--screen FILE ...] [--json]

The tool writes the project JSON directly (route ``json``), using the exact
envelope, item, track and clip shapes a real FilmCraft save produces. Camera
and screen video sit on their own tracks, each video slot's own audio on
``Audio 1``, photo slots as still clips on ``Camera 1`` and approved graphics
overlays on ``Graphics 1`` above the picture. One marker per used slot carries
its reason (Green), replaced by a Rose marker when the slot is flagged for a
human check, plus one Mango marker per placed overlay. Media is referenced by
path, never embedded; a missing file is a warning, not an error. No model is
called, so ``llm_cost_usd`` is always 0.0.
"""

import argparse
import json
import os
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path

from common.schema import SchemaError, validate_or_raise

TICKS_PER_SECOND = 254016000000
SCHEMA_VERSION = 15
GENERATOR = "yt-pipeline export_filmcraft.py"
DEFAULT_SEQUENCE = "Main"
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FPS = 25.0
DEFAULT_STILL_DURATION_S = 5.0
MEDIA_LABEL = "Iris"
SEQUENCE_LABEL = "Forest"
CAMERA_TRACK = "Camera 1"
SCREEN_TRACK = "Screen 1"
AUDIO_TRACK = "Audio 1"
GRAPHICS_TRACK = "Graphics 1"
SCREEN_TOKEN = "screen"
GREEN = "Green"
ROSE = "Rose"
MANGO = "Mango"
VIDEO_CONTAINER = "MPEG-4"
STILL_CONTAINER = "Image"
OVERLAY_CONTAINER = "QuickTime"

PROJECT_SETTINGS = {
    "renderer": "FilmCraft GPU Acceleration (wgpu)",
    "video_display": "Timecode",
    "audio_display_samples": True,
    "default_still_duration": 1270080000000,
    "default_transition_duration_frames": 24,
    "default_audio_transition_duration": 254016000000,
    "ingest": {"enabled": False, "action": "createProxies", "destination": None, "preset": ""},
    "title_safe": [20.0, 20.0],
    "action_safe": [10.0, 10.0],
    "capture_format": "DV",
    "scratch": {"captured": None, "videoPreviews": None, "audioPreviews": None, "autoSave": None},
}


class ExportError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


def seconds_to_ticks(seconds):
    return int(round(float(seconds) * TICKS_PER_SECOND))


def frame_rate_fraction(fps):
    fraction = Fraction(float(fps)).limit_denominator(1001)
    return {"num": fraction.numerator, "den": fraction.denominator}


def load_json(path):
    try:
        return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ExportError(f"cannot read '{path}': file not found")
    except json.JSONDecodeError as exc:
        raise ExportError(f"cannot read '{path}': not valid JSON ({exc})")
    except OSError as exc:
        raise ExportError(f"cannot read '{path}': {exc}")


def load_edit(path):
    document = load_json(path)
    try:
        validate_or_raise(document, "edit")
    except SchemaError as exc:
        raise ExportError(f"invalid edit '{path}': {exc}")
    return document


def slot_length(slot):
    if slot.get("kind") == "photo":
        return float(slot.get("duration_s") or 0.0)
    return float(slot["out_s"]) - float(slot["in_s"])


def build_timeline(document):
    """Return groups of slots in timeline order with their real-time start and length.

    Consecutive video slots sharing the same ``id`` are two angles of one cut
    window: they form one group and share one timeline start.
    """
    groups = []
    current = None
    for slot in document.get("video") or []:
        kind = slot.get("kind")
        if kind not in (None, "video", "photo"):
            continue
        entry = {"slot": slot, "kind": "photo" if kind == "photo" else "video"}
        slot_id = slot.get("id")
        if (current is not None and entry["kind"] == "video" and current["kind"] == "video"
                and slot_id is not None and slot_id == current["slots"][0]["slot"].get("id")):
            current["slots"].append(entry)
        else:
            current = {"kind": entry["kind"], "slots": [entry]}
            groups.append(current)
    cursor = 0.0
    for group in groups:
        group["start_s"] = cursor
        group["length_s"] = slot_length(group["slots"][0]["slot"])
        cursor += group["length_s"]
    return groups


def is_screen_slot(slot, screen_names):
    clip = slot.get("clip") or ""
    if clip in screen_names:
        return True
    return SCREEN_TOKEN in clip.lower()


def slot_key(slot):
    return slot.get("shot_key") or slot.get("id")


def speed_flagged(slot):
    speed = float(slot.get("speed", 1.0) or 1.0)
    middle = slot.get("middle_speed")
    if abs(speed - 1.0) > 1e-9:
        return f"speed {speed:g}"
    if middle is not None and abs(float(middle) - 1.0) > 1e-9:
        return f"middle_speed {float(middle):g}"
    return None


def make_media_item(item_id, name, path, kind, duration_ticks, container):
    return {
        "id": item_id,
        "name": name,
        "label": MEDIA_LABEL,
        "kind": {
            "Media": {
                "media": {"File": {"path": str(path)}},
                "info": {"name": name, "kind": kind, "duration": duration_ticks, "container": container},
                "interpret": {"frame_rate": None, "par": None, "ignore_alpha": False, "invert_alpha": False},
                "markers": [],
                "offline": False,
            }
        },
        "metadata": {},
        "created": item_id,
    }


def make_track(track_id, kind, name):
    return {
        "id": track_id,
        "kind": kind,
        "name": name,
        "items": [],
        "transitions": [],
        "locked": False,
        "sync_lock": True,
        "enabled": True,
        "muted": False,
        "solo": False,
        "channels": "Stereo",
        "volume_db": 0.0,
        "pan": 0.0,
    }


def make_clip(clip_id, item_id, name, start_ticks, duration_ticks, source_in_ticks):
    return {
        "id": clip_id,
        "item": item_id,
        "name": name,
        "label": MEDIA_LABEL,
        "start": start_ticks,
        "duration": duration_ticks,
        "source_in": source_in_ticks,
        "speed": 1.0,
        "enabled": True,
        "effects": [],
    }


def make_marker(marker_id, start_ticks, name, comment, color):
    return {
        "id": marker_id,
        "start": start_ticks,
        "duration": 0,
        "name": name,
        "comment": comment,
        "kind": "Comment",
        "color": color,
    }


def sequence_settings(width, height, fps):
    return {
        "width": width,
        "height": height,
        "frame_rate": frame_rate_fraction(fps),
        "par": [1, 1],
        "sample_rate": 48000,
        "drop_frame": False,
        "preset": f"{width}x{height} {float(fps):g}",
        "audio_master": "Stereo",
        "preview_codec": "ProRes 422",
        "max_bit_depth": False,
        "max_render_quality": False,
        "working_space": "Rec. 709",
        "color": {"working": "Rec709", "wide_gamut": False, "auto_tone_map": True},
    }


def make_sequence_item(item_id, name, settings, video_tracks, audio_tracks, markers):
    return {
        "id": item_id,
        "name": name,
        "label": SEQUENCE_LABEL,
        "kind": {
            "Sequence": {
                "settings": settings,
                "video_tracks": video_tracks,
                "audio_tracks": audio_tracks,
                "markers": markers,
                "mark_in": None,
                "mark_out": None,
                "work_area": None,
                "start_timecode": 0,
                "master_volume_db": 0.0,
                "master_effects": [],
                "master_mixer": {"mode": "Read", "output": None, "record_arm": False,
                                 "solo_safe": False, "input_map": "Stereo"},
                "submix_tracks": [],
                "caption_tracks": [],
            }
        },
        "metadata": {},
        "created": item_id,
    }


def props_summary(props):
    parts = [f"{key}={value}" for key, value in props.items()]
    return ", ".join(parts)


def collect_overlays(document, timeline, graphics_plan_path, graphics_dir):
    """Return the overlays to place, each with id, template, reason, start_s and duration_s."""
    overlays = []
    if graphics_plan_path:
        plan = load_json(graphics_plan_path)
        for entry in plan.get("entries") or []:
            if not entry.get("approved"):
                continue
            overlays.append({
                "id": entry.get("id"),
                "template": entry.get("template"),
                "props": entry.get("props") or {},
                "reason": entry.get("reason") or "",
                "start_s": float(entry["time_s"]),
                "duration_s": float(entry["duration_s"]),
            })
        return overlays

    by_slot = {}
    for group in timeline:
        for entry in group["slots"]:
            by_slot[entry["slot"].get("id")] = {"start_s": group["start_s"], "length_s": group["length_s"]}
    for overlay in document.get("graphics") or []:
        slot_id = overlay.get("slot")
        if slot_id is not None:
            entry = by_slot.get(slot_id)
            if entry is None:
                overlays.append({"id": overlay.get("id"), "template": overlay.get("template"),
                                 "props": overlay.get("props") or {}, "reason": "",
                                 "start_s": None, "duration_s": None,
                                 "skip": f"slot '{slot_id}' not found"})
                continue
            start_s = entry["start_s"]
            duration_s = entry["length_s"]
        elif overlay.get("start_s") is not None and overlay.get("end_s") is not None:
            start_s = float(overlay["start_s"])
            duration_s = float(overlay["end_s"]) - float(overlay["start_s"])
        else:
            overlays.append({"id": overlay.get("id"), "template": overlay.get("template"),
                             "props": overlay.get("props") or {}, "reason": "",
                             "start_s": None, "duration_s": None,
                             "skip": "no slot binding and no start_s/end_s"})
            continue
        overlays.append({
            "id": overlay.get("id"),
            "template": overlay.get("template"),
            "props": overlay.get("props") or {},
            "reason": "",
            "start_s": start_s,
            "duration_s": duration_s,
        })
    return overlays


def build_project(document, options):
    """Build the FilmCraft project document and return ``(project, summary)``."""
    timeline = build_timeline(document)
    sources = {source["file"]: source for source in document.get("sources") or [] if source.get("file")}
    screen_names = set(options["screen"] or [])
    warnings = []
    missing_media = []

    counter = 1
    media_items = {}
    media_ids = {}
    for source in document.get("sources") or []:
        name = source.get("file")
        if not name:
            continue
        path = Path(source.get("path") or name).expanduser()
        is_photo = source.get("media_type") == "photo"
        duration_s = source.get("duration_s")
        if duration_s is None:
            duration_s = DEFAULT_STILL_DURATION_S if is_photo else 0.0
        item_id = counter
        counter += 1
        media_ids[name] = item_id
        media_items[str(item_id)] = make_media_item(
            item_id, name, path,
            "Still" if is_photo else "Movie",
            seconds_to_ticks(duration_s),
            STILL_CONTAINER if is_photo else VIDEO_CONTAINER,
        )
        if not path.is_file():
            missing_media.append(str(path))
            warnings.append(f"missing media: {path}")

    sequence_id = counter
    counter += 1
    graphics_track = make_track(counter, "Video", GRAPHICS_TRACK)
    counter += 1
    camera_track = make_track(counter, "Video", CAMERA_TRACK)
    counter += 1
    screen_track = make_track(counter, "Video", SCREEN_TRACK)
    counter += 1
    audio_track = make_track(counter, "Audio", AUDIO_TRACK)
    counter += 1

    markers = []
    slots_placed = 0
    photos_placed = 0

    for group in timeline:
        start_ticks = seconds_to_ticks(group["start_s"])
        duration_ticks = seconds_to_ticks(group["length_s"])
        first = group["slots"][0]
        for entry in group["slots"]:
            slot = entry["slot"]
            if entry["kind"] == "photo":
                name = slot.get("photo")
                item_id = media_ids.get(name)
                if item_id is None:
                    warnings.append(f"photo slot '{slot.get('id')}': source '{name}' is not in sources[]")
                    continue
                clip = make_clip(counter, item_id, name, start_ticks, duration_ticks, 0)
                counter += 1
                camera_track["items"].append(clip)
                photos_placed += 1
                warnings.append(f"photo slot '{slot.get('id')}': Ken Burns move is not exported")
            else:
                name = slot.get("clip")
                item_id = media_ids.get(name)
                if item_id is None:
                    warnings.append(f"video slot '{slot.get('id')}': source '{name}' is not in sources[]")
                    continue
                source_in_ticks = seconds_to_ticks(slot["in_s"])
                target = screen_track if is_screen_slot(slot, screen_names) else camera_track
                target["items"].append(make_clip(counter, item_id, name, start_ticks, duration_ticks, source_in_ticks))
                counter += 1
                slots_placed += 1
                flag = speed_flagged(slot)
                if flag:
                    warnings.append(f"slot '{slot.get('id')}': {flag} exported at real time")

        video_members = [entry for entry in group["slots"] if entry["kind"] == "video"]
        if video_members:
            audio_member = next(
                (entry for entry in video_members if not is_screen_slot(entry["slot"], screen_names)),
                video_members[0],
            )
            slot = audio_member["slot"]
            name = slot.get("clip")
            item_id = media_ids.get(name)
            if item_id is not None:
                source_in_ticks = seconds_to_ticks(slot["in_s"])
                audio_track["items"].append(make_clip(counter, item_id, name, start_ticks, duration_ticks, source_in_ticks))
                counter += 1

        for entry in group["slots"][1:]:
            if abs(slot_length(entry["slot"]) - group["length_s"]) > 1e-9:
                warnings.append(f"slot '{entry['slot'].get('id')}': length differs from its pair")

        reason = (first["slot"].get("reason") or "").strip()
        if not reason:
            continue
        key = slot_key(first["slot"])
        flag = None
        for entry in group["slots"]:
            if entry["kind"] != "video":
                continue
            member_flag = speed_flagged(entry["slot"])
            if member_flag:
                flag = member_flag
                break
            if entry["slot"].get("centred"):
                flag = "centred cut"
                break
        if flag:
            markers.append(make_marker(counter, start_ticks, f"redo {key}", flag, ROSE))
        else:
            markers.append(make_marker(counter, start_ticks, str(key), reason, GREEN))
        counter += 1

    overlays = collect_overlays(document, timeline, options["graphics_plan"], options["graphics_dir"])
    overlays_placed = 0
    for overlay in overlays:
        if overlay.get("skip"):
            warnings.append(f"overlay '{overlay.get('id')}': {overlay['skip']}")
            continue
        overlay_id = overlay.get("id")
        if not overlay_id:
            warnings.append("overlay without an id: cannot resolve its .mov, skipped")
            continue
        if not options["graphics_dir"]:
            warnings.append(f"overlay '{overlay_id}': no --graphics-dir, skipped")
            continue
        mov_path = Path(options["graphics_dir"]).expanduser() / f"{overlay_id}.mov"
        if not mov_path.is_file():
            warnings.append(f"overlay '{overlay_id}': missing {mov_path}, skipped")
            continue
        item_id = counter
        counter += 1
        media_items[str(item_id)] = make_media_item(
            item_id, mov_path.name, mov_path, "Movie",
            seconds_to_ticks(overlay["duration_s"]), OVERLAY_CONTAINER,
        )
        graphics_track["items"].append(make_clip(
            counter, item_id, mov_path.name,
            seconds_to_ticks(overlay["start_s"]), seconds_to_ticks(overlay["duration_s"]), 0,
        ))
        counter += 1
        comment = overlay.get("reason") or props_summary(overlay.get("props") or {})
        markers.append(make_marker(
            counter, seconds_to_ticks(overlay["start_s"]),
            f"{overlay.get('template')} {overlay_id}", comment, MANGO,
        ))
        counter += 1
        overlays_placed += 1

    sequence_item = make_sequence_item(
        sequence_id, options["sequence"],
        sequence_settings(options["width"], options["height"], options["fps"]),
        [graphics_track, camera_track, screen_track],
        [audio_track],
        markers,
    )
    items = dict(media_items)
    items[str(sequence_id)] = sequence_item
    children = [{"Item": int(key)} for key in sorted(items, key=int)]

    project = {
        "format": "filmcraft.project",
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "project": {
            "name": options["sequence"],
            "settings": PROJECT_SETTINGS,
            "root": {"id": 0, "name": options["sequence"], "children": children},
            "items": items,
            "next_id": counter,
        },
        "view": {"open_sequences": [sequence_id], "active_sequence": sequence_id, "sequences": {}},
    }

    summary = {
        "sequence": options["sequence"],
        "tracks": [GRAPHICS_TRACK, CAMERA_TRACK, SCREEN_TRACK, AUDIO_TRACK],
        "slots_placed": slots_placed,
        "photos_placed": photos_placed,
        "overlays_placed": overlays_placed,
        "markers": {
            "green": sum(1 for marker in markers if marker["color"] == GREEN),
            "rose": sum(1 for marker in markers if marker["color"] == ROSE),
            "mango": sum(1 for marker in markers if marker["color"] == MANGO),
        },
        "missing_media": missing_media,
        "warnings": warnings,
        "route": "json",
    }
    return project, summary


def check_out(out_path, document):
    out = Path(out_path).expanduser()
    if out.is_dir():
        raise ExportError(f"--out is a directory: {out}")
    for source in document.get("sources") or []:
        path = source.get("path")
        if path and Path(path).expanduser().resolve() == out.resolve():
            raise ExportError(f"--out would overwrite a source file: {out}")


def write_project(out_path, project):
    out = Path(out_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(out.parent), prefix=out.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(project, stream, ensure_ascii=False)
        os.replace(tmp_name, out)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def run(edit_path, out_path, options):
    started = time.monotonic()
    document = load_edit(edit_path)
    check_out(out_path, document)
    project, summary = build_project(document, options)
    write_project(out_path, project)
    summary["output"] = str(Path(out_path).expanduser())
    summary["wall_time_s"] = round(time.monotonic() - started, 3)
    summary["llm_cost_usd"] = 0.0
    return summary


def build_parser():
    parser = argparse.ArgumentParser(
        prog="export_filmcraft.py",
        description="Export an edit.json edit list to an editable FilmCraft .fcproj project.",
        epilog=(
            "examples:\n"
            "  python3 scripts/export_filmcraft.py edit.json --out project.fcproj --json\n"
            "  python3 scripts/export_filmcraft.py edit.json --out project.fcproj "
            "--graphics-plan graphics_plan.json --graphics-dir overlays/\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("edit", help="edit.json edit list (validated against schemas/edit.schema.json)")
    parser.add_argument("--out", required=True, help="the .fcproj project to write (atomic replace)")
    parser.add_argument("--graphics-plan", help="graphics_plan.json; approved entries are placed as overlays")
    parser.add_argument("--graphics-dir", help="folder holding <entry id>.mov rendered overlays")
    parser.add_argument("--sequence", default=DEFAULT_SEQUENCE, help=f"sequence name (default {DEFAULT_SEQUENCE})")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH, help=f"frame width (default {DEFAULT_WIDTH})")
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help=f"frame height (default {DEFAULT_HEIGHT})")
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS, help=f"frame rate (default {DEFAULT_FPS:g})")
    parser.add_argument("--screen", action="append", default=[], help="source file name to treat as screen (repeatable)")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    options = {
        "sequence": args.sequence,
        "width": args.width,
        "height": args.height,
        "fps": args.fps,
        "screen": args.screen,
        "graphics_plan": args.graphics_plan,
        "graphics_dir": args.graphics_dir,
    }
    try:
        summary = run(args.edit, args.out, options)
    except ExportError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        return 2

    if args.json:
        print(json.dumps({"ok": True, **summary}))
    else:
        print(
            f"written: {summary['output']} ({summary['slots_placed']} video, "
            f"{summary['photos_placed']} photo, {summary['overlays_placed']} overlay, "
            f"{summary['markers']['green']} Green, {summary['markers']['rose']} Rose, "
            f"{summary['markers']['mango']} Mango)"
        )
        for warning in summary["warnings"]:
            print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
