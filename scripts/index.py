#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Join the workspace analyses into ``shotlog.json``, one scored record per shot.

Usage:
    python scripts/index.py --workspace DIR
    python scripts/index.py --workspace DIR --json
    python scripts/index.py --workspace DIR --clips 0072,0081 --force --json
    python scripts/index.py --workspace DIR --html /path/to/out.html
    python scripts/index.py --workspace DIR --no-html

Reads ``quality/<stem>.json`` (required unless photos exist) plus
``motion/<stem>.json``, ``describe/<stem>.json`` and ``similarity.json`` (all
optional) from the workspace and writes one ``shotlog.json`` validated against
``schemas/shotlog.schema.json``. Every quality segment becomes a scored
candidate with a hook and a reason — one candidate per content beat when
describe split a long segment into beats — and the vision verdict supplies the
effective role and motion label on uncertain segments, except a geometry
``accidental`` whose role is final. Every described still photo becomes one
candidate (``media_type: "photo"``, pseudo-segment ``p1``) with the fixed
clean-still label prior, and photos linked by similarity and captured within
30 s of each other form a burst whose best member stays eligible. A
self-contained ``shotlog.html`` contact sheet is written next to it, with one
base64 thumbnail per shot. No model is called. Sources are read-only; the
outputs are written atomically.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from common.excluded import is_excluded, load_excluded
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.errors import report_error
from common.frames import thumbnail_data_uri
from common.overlap import overlap_fraction, overlap_seconds
from common.photos import BLOCKING_PHOTO_FLAWS
from common.peak import valid_vision_peak
from common.schema import SchemaError, validate_or_raise

CLIP_COUNTER = re.compile(r"_(\d{4})_")
DEFAULT_WEIGHT_VISION = 0.55
DEFAULT_WEIGHT_LABELS = 0.45
ROLE_BASE = {"hold": 0.55, "move": 0.50, "transition_in": 0.35, "transition_out": 0.35}
STEADINESS_BONUS = {
    "no_shaking": 0.15,
    "minimal_shaking": 0.08,
    "static": 0.10,
    "unsteady": -0.10,
    "very_unsteady": -0.20,
}
DEFECT_WEIGHTS = {"horizon": 0.5}
DEFAULT_DEFECT_WEIGHT = 1.0
INELIGIBLE_ROLES = {"accidental", "setup", "teardown"}
NO_VISION_PRIOR = 0.5
SUPPRESS_OVERLAP_RATIO = 0.5
PHOTO_SEGMENT_ID = "p1"
PHOTO_START_S = 0.0
PHOTO_END_S = 0.04
PHOTO_LABEL_SCORE = 0.7
PHOTO_CONFIDENCE = 0.7
BURST_WINDOW_S = 30.0
BURST_SHARPNESS_FLOOR = 0.7
HOOK_WORDS = 8
HTML_NAME = "shotlog.html"


class ShotlogError(RuntimeError):
    """Raised when a workspace cannot be turned into a shotlog."""


class ShotlogRuntimeError(RuntimeError):
    """Raised when the run fails after it started (disk write)."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="index.py",
        description=(
            "Join quality/, motion/, describe/ and similarity.json into shotlog.json: "
            "every quality segment as a scored candidate the director ranks."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/index.py --workspace <workspace>\n"
            "  python scripts/index.py --workspace <workspace> --json\n"
            "  python scripts/index.py --workspace <workspace> \\\n"
            "      --clips 0072,0081 --force --json\n"
            "  python scripts/index.py --workspace <workspace> \\\n"
            "      --html /tmp/shotlog.html\n"
            "  python scripts/index.py --workspace <workspace> --no-html\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--workspace", required=True, help="workspace holding quality/; shotlog.json is written here")
    parser.add_argument("--weight-vision", type=float, default=DEFAULT_WEIGHT_VISION, help="fusion weight of the vision component (default 0.55)")
    parser.add_argument("--weight-labels", type=float, default=DEFAULT_WEIGHT_LABELS, help="fusion weight of the label component (default 0.45)")
    parser.add_argument("--clips", default=None, help="comma-separated 4-digit counters or stem substrings to include")
    parser.add_argument("--force", action="store_true", help="recompute even when shotlog.json is up to date")
    parser.add_argument("--html", default=None, help="contact sheet path (default WORKSPACE/shotlog.html)")
    parser.add_argument("--no-html", action="store_true", help="skip writing the contact sheet")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def clamp(value, low, high):
    return max(low, min(high, value))


def clip_counter(name):
    """The 4-digit counter in a quality file name, or the stem when there is none."""
    match = CLIP_COUNTER.search(name)
    return match.group(1) if match else Path(name).stem


def segment_key(counter, segment_id):
    """The ``<clip counter>/<segment id>`` key used in the output."""
    return f"{counter}/{segment_id}"


def run(
    workspace,
    weight_vision=DEFAULT_WEIGHT_VISION,
    weight_labels=DEFAULT_WEIGHT_LABELS,
    clips=None,
    force=False,
    html=None,
    no_html=False,
):
    """Build ``workspace/shotlog.json`` and its contact sheet; return the summary and the exit code."""
    started = time.monotonic()
    if weight_vision < 0 or weight_labels < 0:
        raise ShotlogError("fusion weights must be non-negative")
    if weight_vision + weight_labels <= 0:
        raise ShotlogError("weight vision + weight labels must be positive")
    workspace_path = Path(workspace).expanduser().resolve()
    quality_dir = workspace_path / "quality"
    excluded = load_excluded(workspace_path)
    ingest_photos = _load_ingest_photos(workspace_path)
    photo_describes = _load_photo_describes(workspace_path, excluded)
    if quality_dir.is_dir():
        selected = _select_quality_files(quality_dir, clips, excluded, required=not photo_describes)
    else:
        selected = []
    if not selected and not photo_describes:
        raise ShotlogError(f"no quality folder in workspace: {quality_dir}")
    html_path = _html_path(workspace_path, html, no_html)
    output_path = workspace_path / "shotlog.json"
    inputs = _input_files(workspace_path, selected, photo_describes)
    if not force and _output_is_up_to_date(output_path, inputs):
        return _skipped_summary(output_path, started, html_path), EXIT_OK
    clip_paths = _clip_path_map(workspace_path, selected)
    similarity_map = _similarity_map(_load_json(workspace_path / "similarity.json"))
    shots = []
    for quality_file in selected:
        record = _load_quality(quality_file)
        counter = clip_counter(quality_file.name)
        motion = _load_json(workspace_path / "motion" / f"{quality_file.stem}.json")
        describe_segments = _segments_by_id(_load_json(workspace_path / "describe" / f"{quality_file.stem}.json"))
        for segment in record["segments"]:
            describe_segment = describe_segments.get(segment.get("id"))
            beats = _split_beats(describe_segment)
            if beats:
                for beat in beats:
                    shots.append(
                        _build_shot(
                            counter,
                            record,
                            segment,
                            motion,
                            describe_segment,
                            similarity_map,
                            weight_vision,
                            weight_labels,
                            beat=beat,
                        )
                    )
            else:
                shots.append(
                    _build_shot(
                        counter,
                        record,
                        segment,
                        motion,
                        describe_segment,
                        similarity_map,
                        weight_vision,
                        weight_labels,
                    )
                )
    photo_lookup = _photo_key_lookup(ingest_photos)
    photo_by_name = _ingest_photos_by_name(ingest_photos)
    for describe_path, describe_record in photo_describes:
        segment = _segments_by_id(describe_record).get(PHOTO_SEGMENT_ID)
        if segment is None:
            continue
        key = _photo_shot_key(describe_record, describe_path, photo_lookup)
        photo = photo_by_name.get(describe_record.get("file") or "")
        shots.append(
            _build_photo_shot(
                key,
                describe_record,
                segment,
                photo,
                similarity_map,
                weight_vision,
                weight_labels,
            )
        )
    _apply_suppression(shots)
    _apply_burst_suppression(shots)
    weights = {
        "vision": weight_vision,
        "labels": weight_labels,
        "audio_energy": 0.0,
        "text": 0.0,
    }
    wall_time_s = round(time.monotonic() - started, 2)
    record = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(workspace_path),
        "weights": weights,
        "shots": shots,
        "wall_time_s": wall_time_s,
        "llm_cost_usd": 0.0,
        "warnings": [],
    }
    validate_or_raise(record, "shotlog")
    try:
        _atomic_write_json(output_path, record)
        if html_path is not None:
            _write_html(html_path, record, clip_paths)
    except OSError as exc:
        raise ShotlogRuntimeError(str(exc)) from exc
    summary = _summary(shots, weights, wall_time_s, output_path, html_path)
    return summary, EXIT_OK


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary, exit_code = run(
            args.workspace,
            weight_vision=args.weight_vision,
            weight_labels=args.weight_labels,
            clips=args.clips,
            force=args.force,
            html=args.html,
            no_html=args.no_html,
        )
    except ShotlogRuntimeError as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except (ShotlogError, SchemaError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)
    except OSError as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    if args.json:
        print(json.dumps(summary))
    else:
        if summary["skipped"]:
            print(f"up to date: {summary['output']}")
        else:
            print(
                f"wrote {summary['shots']} shots "
                f"({summary['eligible']} eligible, {summary['with_vision']} with vision, "
                f"{summary['suppressed']} suppressed)"
            )
            print(
                f"weights vision {summary['weights']['vision']} "
                f"labels {summary['weights']['labels']}, {summary['wall_time_s']}s"
            )
    return exit_code


def _split_beats(describe_segment):
    """The content beats of a described segment, or [] when it is unsplit."""
    if not _is_described(describe_segment):
        return []
    beats = describe_segment.get("beats") or []
    return beats if len(beats) >= 2 else []


def _build_shot(
    counter,
    record,
    segment,
    motion,
    describe_segment,
    similarity_map,
    weight_vision,
    weight_labels,
    beat=None,
):
    role, role_source, motion_label, label_source, confidence = _effective_labels(segment, describe_segment)
    uncertainty = bool(segment.get("uncertain"))
    vision = _vision_fields(describe_segment, beat)
    steadiness = _steadiness(segment, motion, role)
    defects = list(segment.get("defects") or [])
    eligible = _is_eligible(role, uncertainty)
    labels = _label_score(role, confidence, steadiness, defects)
    vision_score = _vision_score(vision)
    if eligible:
        fusion_vision = vision_score if vision_score is not None else NO_VISION_PRIOR
        score = _fused_score(fusion_vision, labels, weight_vision, weight_labels)
    else:
        score = 0.0
        labels = 0.0
        vision_score = None
    segment_id = segment.get("id")
    start_s = segment["start_s"]
    end_s = segment["end_s"]
    beat_of = None
    beat_id = None
    beat_label = None
    if beat is not None:
        beat_of = segment_id
        beat_id = beat.get("id")
        beat_label = beat.get("label")
        segment_id = f"{segment_id}{beat_id}"
        start_s = beat["start_s"]
        end_s = beat["end_s"]
    key = segment_key(counter, segment_id)
    return {
        "key": key,
        "clip": record.get("file", ""),
        "segment_id": segment_id,
        "beat_of": beat_of,
        "beat_id": beat_id,
        "beat_label": beat_label,
        "start_s": start_s,
        "end_s": end_s,
        "eligible": eligible,
        "role": role,
        "role_source": role_source,
        "motion_label": motion_label,
        "label_source": label_source,
        "confidence": confidence,
        "uncertain": uncertainty,
        "uncertain_reasons": list(segment.get("uncertain_reasons") or []),
        "description": vision.get("description") if vision else None,
        "summary": vision.get("summary") if vision else None,
        "tags": list(vision.get("tags") or []) if vision else [],
        "subject": vision.get("subject") if vision else None,
        "scenery": vision.get("scenery") if vision else None,
        "semantic_motion": vision.get("semantic_motion") if vision else None,
        "interest": vision.get("interest") if vision else None,
        "hero": vision.get("hero") if vision else None,
        "peak_s": valid_vision_peak(vision.get("peak_s"), start_s, end_s) if vision else None,
        "score": score,
        "scores": {"vision": vision_score, "labels": labels},
        "hook": _hook(vision, role, motion_label),
        "reason": _reason(vision, motion_label, label_source, role, defects, eligible, beat_label),
        "similar_to": list(similarity_map.get(key, [])),
        "defects": [defect.get("name") for defect in defects],
        "suppressed_by": None,
    }


def _build_photo_shot(
    key,
    describe_record,
    describe_segment,
    photo,
    similarity_map,
    weight_vision,
    weight_labels,
):
    vision = _vision_fields(describe_segment)
    vision_score = _vision_score(vision)
    labels = PHOTO_LABEL_SCORE
    fusion_vision = vision_score if vision_score is not None else NO_VISION_PRIOR
    score = _fused_score(fusion_vision, labels, weight_vision, weight_labels)
    semantic_motion = (vision.get("semantic_motion") if vision else None) or "none"
    flaws = [str(flaw) for flaw in (describe_segment.get("photo_flaws") or [])] if _is_described(describe_segment) else []
    blocked = any(flaw in BLOCKING_PHOTO_FLAWS for flaw in flaws)
    sharp = describe_segment.get("sharpness") if _is_described(describe_segment) else None
    return {
        "key": key,
        "clip": describe_record.get("file", ""),
        "segment_id": PHOTO_SEGMENT_ID,
        "beat_of": None,
        "beat_id": None,
        "beat_label": None,
        "start_s": PHOTO_START_S,
        "end_s": PHOTO_END_S,
        "media_type": "photo",
        "capture_time": photo.get("creation_time") if photo else None,
        "subject_position": _subject_position(describe_segment),
        "sharpness": float(sharp) if isinstance(sharp, (int, float)) and not isinstance(sharp, bool) else None,
        "eligible": not blocked,
        "role": "hold",
        "role_source": "geometry",
        "motion_label": "static",
        "label_source": "geometry",
        "confidence": PHOTO_CONFIDENCE,
        "uncertain": False,
        "uncertain_reasons": [],
        "description": vision.get("description") if vision else None,
        "summary": vision.get("summary") if vision else None,
        "tags": list(vision.get("tags") or []) if vision else [],
        "subject": vision.get("subject") if vision else None,
        "scenery": vision.get("scenery") if vision else None,
        "semantic_motion": semantic_motion,
        "interest": vision.get("interest") if vision else None,
        "hero": vision.get("hero") if vision else None,
        "score": score,
        "scores": {"vision": vision_score, "labels": labels},
        "hook": _hook(vision, "hold", "static"),
        "reason": _reason(vision, "static", "geometry", "hold", [{"name": flaw} for flaw in flaws], not blocked),
        "similar_to": list(similarity_map.get(key, [])),
        "defects": flaws,
        "suppressed_by": None,
    }


def _subject_position(describe_segment):
    if not _is_described(describe_segment):
        return None
    position = describe_segment.get("subject_position")
    if not isinstance(position, dict):
        return None
    x = position.get("x")
    y = position.get("y")
    if isinstance(x, bool) or isinstance(y, bool):
        return None
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    return {"x": float(x), "y": float(y)}


def _effective_labels(segment, describe_segment):
    geometry_role = segment.get("role")
    geometry_motion = segment.get("motion_label")
    if geometry_motion is None:
        geometry_motion = "static"
    verdict = describe_segment.get("verdict") if _is_described(describe_segment) else None
    if verdict is None:
        return geometry_role, "geometry", geometry_motion, "geometry", segment.get("confidence")
    if geometry_role == "accidental":
        motion_label = verdict.get("motion_label")
        if motion_label is None:
            return geometry_role, "geometry", geometry_motion, "geometry", segment.get("confidence")
        return geometry_role, "geometry", motion_label, "verdict", verdict.get("confidence")
    return (
        verdict.get("role"),
        "verdict",
        verdict.get("motion_label"),
        "verdict",
        verdict.get("confidence"),
    )


def _is_described(describe_segment):
    return isinstance(describe_segment, dict) and describe_segment.get("status") == "described"


def _vision_fields(describe_segment, beat=None):
    if not _is_described(describe_segment):
        return None
    fields = {
        "description": describe_segment.get("description"),
        "summary": describe_segment.get("summary"),
        "tags": list(describe_segment.get("tags") or []),
        "subject": describe_segment.get("subject"),
        "scenery": describe_segment.get("scenery"),
        "semantic_motion": describe_segment.get("semantic_motion"),
        "interest": describe_segment.get("interest"),
        "hero": describe_segment.get("hero"),
        "peak_s": describe_segment.get("peak_s"),
    }
    if beat is not None:
        fields["peak_s"] = beat.get("peak_s") if beat.get("peak_s") is not None else fields["peak_s"]
        if beat.get("summary") is not None:
            fields["summary"] = beat.get("summary")
        if beat.get("interest") is not None:
            fields["interest"] = beat.get("interest")
        if beat.get("hero") is not None:
            fields["hero"] = beat.get("hero")
    return fields


def _vision_score(vision):
    if vision is None or vision.get("interest") is None:
        return None
    score = float(vision["interest"]) / 100.0
    if vision.get("hero"):
        score += 0.15
    return min(1.0, score)


def _steadiness(segment, motion, role):
    move = _best_overlapping_move(segment, (motion or {}).get("moves") or [])
    if move is not None:
        return move.get("steadiness")
    return "static" if role == "hold" else None


def _best_overlapping_move(segment, moves):
    best = None
    best_overlap = 0.0
    for move in moves:
        start_s = move.get("start_s")
        end_s = move.get("end_s")
        if start_s is None or end_s is None:
            continue
        overlap = overlap_seconds(segment["start_s"], segment["end_s"], start_s, end_s)
        if overlap > best_overlap:
            best_overlap = overlap
            best = move
    return best


def _label_score(role, confidence, steadiness, defects):
    score = ROLE_BASE.get(role, 0.0)
    score += 0.25 * float(confidence or 0.0)
    if steadiness in STEADINESS_BONUS:
        score += STEADINESS_BONUS[steadiness]
    if defects:
        penalty = sum(
            float(defect.get("value", 0.0)) * DEFECT_WEIGHTS.get(defect.get("name"), DEFAULT_DEFECT_WEIGHT)
            for defect in defects
        )
        score -= min(0.4, penalty)
    else:
        score += 0.10
    return clamp(score, 0.0, 1.0)


def _fused_score(vision, labels, weight_vision, weight_labels):
    fused = (weight_vision * vision + weight_labels * labels) / (weight_vision + weight_labels)
    return clamp(fused, 0.0, 1.0)


def _is_eligible(role, uncertain):
    if role == "accidental":
        return False
    return not (role in INELIGIBLE_ROLES and not uncertain)


def _hook(vision, role, motion_label):
    if vision is not None:
        subject = vision.get("subject")
        if isinstance(subject, str) and subject.strip() and subject.strip().lower() != "none":
            return subject
        description = vision.get("description")
        if isinstance(description, str) and description.strip():
            return " ".join(description.split()[:HOOK_WORDS])
    return f"{role} · {motion_label}"


def _reason(vision, motion_label, label_source, role, defects, eligible, beat_label=None):
    parts = []
    if beat_label:
        parts.append(str(beat_label))
    interest = vision.get("interest") if vision is not None else None
    parts.append(f"interest {interest}" if interest is not None else "no description")
    if vision is not None and vision.get("hero"):
        parts.append("hero")
    parts.append(f"{motion_label} by {label_source}")
    parts.append(role)
    parts.append(",".join(defect.get("name") for defect in defects) if defects else "clean")
    if not eligible:
        parts.append("not eligible")
    return " · ".join(parts)


def _apply_suppression(shots):
    suppressors = {}
    by_clip = {}
    for shot in shots:
        by_clip.setdefault(shot["clip"], []).append(shot)
    for group in by_clip.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                first, second = group[i], group[j]
                if not _suppressing_overlap(first, second):
                    continue
                if first["score"] >= second["score"]:
                    winner, loser = first, second
                else:
                    winner, loser = second, first
                current = suppressors.get(loser["key"])
                if current is None or winner["score"] > current["score"]:
                    suppressors[loser["key"]] = winner
    for shot in shots:
        winner = suppressors.get(shot["key"])
        if winner is None:
            continue
        shot["suppressed_by"] = winner["key"]
        shot["eligible"] = False
        shot["score"] = 0.0
        shot["scores"]["labels"] = 0.0
        shot["scores"]["vision"] = None


def _suppressing_overlap(first, second):
    return (
        overlap_fraction(
            first["start_s"], first["end_s"], second["start_s"], second["end_s"]
        ) > SUPPRESS_OVERLAP_RATIO
    )


def _apply_burst_suppression(shots):
    photos = [shot for shot in shots if shot.get("media_type") == "photo"]
    if len(photos) < 2:
        return
    by_key = {shot["key"]: shot for shot in photos}
    similar = {key: set(shot.get("similar_to") or []) for key, shot in by_key.items()}
    moments = {key: _parse_capture_time(shot.get("capture_time")) for key, shot in by_key.items()}
    neighbours = {key: set() for key in by_key}
    keys = list(by_key)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            first, second = keys[i], keys[j]
            if second not in similar[first] or first not in similar[second]:
                continue
            if moments[first] is None or moments[second] is None:
                continue
            if abs((moments[first] - moments[second]).total_seconds()) > BURST_WINDOW_S:
                continue
            neighbours[first].add(second)
            neighbours[second].add(first)
    for component in _connected_components(neighbours):
        if len(component) < 2:
            continue
        winner_key = min(component, key=lambda key: _burst_rank(by_key, component, key))
        for key in component:
            if key == winner_key:
                continue
            shot = by_key[key]
            shot["suppressed_by"] = winner_key
            shot["eligible"] = False
            shot["score"] = 0.0
            shot["scores"]["labels"] = 0.0
            shot["scores"]["vision"] = None


def _burst_rank(by_key, component, key):
    """Sort key of a burst member: no spoiling flaw first, then score weighted by relative sharpness."""
    shot = by_key[key]
    blocked = any(flaw in BLOCKING_PHOTO_FLAWS for flaw in shot.get("defects") or [])
    values = [by_key[other].get("sharpness") for other in component]
    values = [value for value in values if isinstance(value, (int, float)) and value > 0]
    factor = 1.0
    if values and isinstance(shot.get("sharpness"), (int, float)):
        factor = BURST_SHARPNESS_FLOOR + (1 - BURST_SHARPNESS_FLOOR) * shot["sharpness"] / max(values)
    return (blocked, -float(shot.get("score") or 0.0) * factor, key)


def _connected_components(neighbours):
    components = []
    seen = set()
    for node in neighbours:
        if node in seen:
            continue
        seen.add(node)
        stack = [node]
        component = set()
        while stack:
            current = stack.pop()
            component.add(current)
            for neighbour in neighbours[current]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        components.append(component)
    return components


def _parse_capture_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _similarity_map(similarity):
    result = {}
    if not isinstance(similarity, dict):
        return result
    for pair in similarity.get("pairs") or []:
        a = pair.get("a")
        b = pair.get("b")
        if not a or not b:
            continue
        result.setdefault(a, []).append(b)
        result.setdefault(b, []).append(a)
    return result


def _segments_by_id(describe):
    if not isinstance(describe, dict):
        return {}
    return {segment.get("id"): segment for segment in describe.get("segments") or []}


def _load_ingest_photos(workspace_path):
    ingest = _load_json(workspace_path / "ingest.json")
    if not isinstance(ingest, dict):
        return []
    photos = ingest.get("photos")
    if not isinstance(photos, list):
        return []
    valid = [
        photo
        for photo in photos
        if isinstance(photo, dict) and (photo.get("file") or photo.get("path"))
    ]
    return sorted(valid, key=lambda photo: photo.get("file") or Path(photo["path"]).name)


def _ingest_photos_by_name(photos):
    by_name = {}
    for photo in photos:
        name = photo.get("file") or Path(photo["path"]).name
        by_name[name] = photo
    return by_name


def _photo_key_lookup(photos):
    """Map every ingest photo (by file name and path) to its deterministic ``<counter>/p1`` key."""
    lookup = {}
    used = set()
    for photo in photos:
        name = photo.get("file") or Path(photo["path"]).name
        counter = clip_counter(name)
        if counter in used:
            counter = Path(name).stem
        used.add(counter)
        key = segment_key(counter, PHOTO_SEGMENT_ID)
        lookup[name] = key
        if photo.get("path"):
            lookup[str(photo["path"])] = key
    return lookup


def _photo_shot_key(describe_record, describe_path, lookup):
    name = describe_record.get("file")
    if name and name in lookup:
        return lookup[name]
    raw_path = describe_record.get("path")
    if raw_path and str(raw_path) in lookup:
        return lookup[str(raw_path)]
    if not name:
        name = describe_path.stem
    return segment_key(clip_counter(name), PHOTO_SEGMENT_ID)


def _load_photo_describes(workspace_path, excluded=None):
    describe_dir = workspace_path / "describe"
    if not describe_dir.is_dir():
        return []
    records = []
    for path in sorted(describe_dir.glob("*.json")):
        if is_excluded(path.stem, excluded):
            continue
        data = _load_json(path)
        if isinstance(data, dict) and data.get("media_type") == "photo":
            records.append((path, data))
    return records


def _input_files(workspace_path, quality_files, photo_describes=None):
    files = list(quality_files)
    similarity = workspace_path / "similarity.json"
    if similarity.exists():
        files.append(similarity)
    for quality_file in quality_files:
        for folder in ("motion", "describe"):
            path = workspace_path / folder / f"{quality_file.stem}.json"
            if path.exists():
                files.append(path)
    for path, _ in photo_describes or []:
        files.append(path)
    return files


def _select_quality_files(quality_dir, clips, excluded=None, required=True):
    tokens = _clip_tokens(clips)
    files = sorted(quality_dir.glob("*.json"))
    selected = [
        path for path in files if _matches(path.stem, tokens) and not is_excluded(path.stem, excluded)
    ]
    if not selected and required:
        raise ShotlogError(f"no quality files matched in {quality_dir}")
    return selected


def _clip_tokens(clips):
    if not clips:
        return None
    return [token.strip() for token in clips.split(",") if token.strip()]


def _matches(stem, tokens):
    if not tokens:
        return True
    return any(token in stem for token in tokens)


def _load_quality(path):
    data = _load_json(path)
    if not isinstance(data, dict) or not data.get("segments"):
        raise ShotlogError(f"quality file {path} has no segments")
    return data


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _output_is_up_to_date(output_path, inputs):
    if not output_path.exists() or _load_json(output_path) is None:
        return False
    try:
        output_mtime = output_path.stat().st_mtime
        newest_input = max(path.stat().st_mtime for path in inputs)
    except (OSError, ValueError):
        return False
    return output_mtime >= newest_input


def _summary(shots, weights, wall_time_s, output_path, html_path):
    return {
        "shots": len(shots),
        "eligible": sum(1 for shot in shots if shot["eligible"]),
        "with_vision": sum(1 for shot in shots if shot.get("description") is not None),
        "suppressed": sum(1 for shot in shots if shot.get("suppressed_by")),
        "weights": weights,
        "wall_time_s": wall_time_s,
        "llm_cost_usd": 0.0,
        "output": str(output_path),
        "html": str(html_path) if html_path is not None else None,
        "skipped": False,
    }


def _skipped_summary(output_path, started, html_path):
    data = _load_json(output_path) or {}
    shots = data.get("shots") or []
    return {
        "shots": len(shots),
        "eligible": sum(1 for shot in shots if shot.get("eligible")),
        "with_vision": sum(1 for shot in shots if shot.get("description") is not None),
        "suppressed": sum(1 for shot in shots if shot.get("suppressed_by")),
        "weights": data.get("weights") or {},
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": 0.0,
        "output": str(output_path),
        "html": str(html_path) if html_path is not None and html_path.exists() else None,
        "skipped": True,
    }


def _html_path(workspace_path, html, no_html):
    if no_html:
        return None
    path = Path(html).expanduser().resolve() if html else workspace_path / HTML_NAME
    if _is_inside_repo(path):
        raise ShotlogError(f"refusing to write the contact sheet inside the repository: {path}")
    return path


def _is_inside_repo(path):
    repo_root = Path(__file__).resolve().parents[1]
    resolved = Path(path).resolve()
    return resolved == repo_root or repo_root in resolved.parents


def _clip_path_map(workspace_path, quality_files):
    paths = {}
    ingest = _load_json(workspace_path / "ingest.json")
    if isinstance(ingest, dict):
        for clip in ingest.get("clips") or []:
            name = clip.get("file")
            path = clip.get("path")
            if name and path:
                paths.setdefault(name, path)
        for photo in ingest.get("photos") or []:
            name = photo.get("file")
            path = photo.get("path")
            if name and path:
                paths.setdefault(name, path)
    for quality_file in quality_files:
        record = _load_json(quality_file)
        if not isinstance(record, dict):
            continue
        name = record.get("file")
        path = record.get("path")
        if name and path:
            paths[name] = path
    proxy_dir = Path(workspace_path) / "proxy"
    for name, path in list(paths.items()):
        if not Path(path).exists() and (proxy_dir / name).is_file():
            paths[name] = str(proxy_dir / name)
    return paths


def _write_html(html_path, record, clip_paths):
    shots = sorted(record.get("shots") or [], key=lambda shot: shot.get("score") or 0.0, reverse=True)
    workspace_name = Path(record.get("workspace") or "").name
    used_keys = _used_shot_keys(record)
    sections = []
    for verdict, title in (
        ("used", "Used in the edit"),
        ("kept", "Kept but not used"),
        ("unusable", "Not usable"),
    ):
        rows = [shot for shot in shots if _verdict_of(shot, used_keys) == verdict]
        sections.append(_shots_section(verdict, title, rows, clip_paths))
    document = _html_document(
        workspace_name,
        _summary_line(shots),
        record.get("generated_at"),
        "\n".join(sections),
    )
    html_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = html_path.with_name(html_path.name + ".tmp")
    try:
        temporary.write_text(document, encoding="utf-8")
        os.replace(temporary, html_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _used_shot_keys(record):
    """The shot keys the workspace's edit.json marks as used, or an empty set."""
    workspace = record.get("workspace")
    if not workspace:
        return set()
    edit = _load_json(Path(workspace) / "edit.json")
    if not isinstance(edit, dict) or not isinstance(edit.get("decisions"), list):
        return set()
    return {
        str(row["id"])
        for row in edit["decisions"]
        if isinstance(row, dict) and row.get("status") == "used" and row.get("id")
    }


def _verdict_of(shot, used_keys):
    if str(shot.get("key") or "") in used_keys:
        return "used"
    if shot.get("eligible"):
        return "kept"
    return "unusable"


def _summary_line(shots):
    """One plain line about the shots: pieces, clips, usable, shaky, moves and scenes."""
    pieces = len(shots)
    clips = len({str(shot.get("clip") or "") for shot in shots})
    usable = sum(1 for shot in shots if shot.get("eligible"))
    shaky = sum(1 for shot in shots if "shake" in (shot.get("defects") or []))
    parts = [f"{pieces} pieces from {clips} clips", f"{usable} usable", f"{shaky} shaky"]
    moves = _top_values(shots, "motion_label")
    if moves:
        parts.append("moves: " + ", ".join(moves))
    scenes = _top_values(shots, "scenery")
    if scenes:
        parts.append("scenes: " + ", ".join(scenes))
    return " · ".join(parts)


def _top_values(shots, field):
    counts = {}
    for shot in shots:
        value = shot.get(field)
        if value is None:
            continue
        counts[str(value)] = counts.get(str(value), 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [name for name, _ in ordered[:2]]


def _shots_section(verdict, title, rows, clip_paths):
    groups = _clip_groups(rows, clip_paths) if rows else '<p class="missing">None.</p>'
    return (
        f'<details class="shots-section" data-verdict="{verdict}" id="{verdict}" data-keep>'
        f'<summary>{escape(title)} (<span class="count">{len(rows)}</span>)</summary>'
        f'<div class="groups">{groups}</div>'
        f"</details>"
    )


def _clip_groups(rows, clip_paths):
    by_clip = {}
    for shot in rows:
        by_clip.setdefault(str(shot.get("clip") or ""), []).append(shot)
    parts = []
    for clip in sorted(by_clip):
        cards = "\n".join(_shot_card(shot, clip_paths) for shot in by_clip[clip])
        parts.append(
            f'<section class="clip-group" data-clip="{escape(clip)}">'
            f"<h3>{escape(clip)}</h3>{cards}</section>"
        )
    return "\n".join(parts)


def _shot_card(shot, clip_paths):
    eligible = bool(shot.get("eligible"))
    card_class = "card" if eligible else "card ineligible"
    clip = str(shot.get("clip") or "")
    key = str(shot.get("key") or "")
    start = _format_time(shot.get("start_s") or 0.0)
    end = _format_time(shot.get("end_s") or 0.0)
    summary = shot.get("summary")
    summary_text = escape(summary) if summary else "—"
    tags = "".join(f'<span class="tag">{escape(str(tag))}</span>' for tag in shot.get("tags") or [])
    tags_html = f'<div class="tags">{tags}</div>' if tags else ""
    score_text, components = _score_text(shot)
    badges = []
    if shot.get("hero"):
        badges.append('<span class="badge hero">HERO</span>')
    if eligible:
        badges.append('<span class="badge eligible">eligible</span>')
    else:
        badges.append('<span class="badge ineligible">not eligible</span>')
    if str(shot.get("media_type") or "video") == "photo":
        badges.append('<span class="badge media">photo</span>')
    role = escape(str(shot.get("role") or ""))
    motion = escape(str(shot.get("motion_label") or ""))
    hook = escape(str(shot.get("hook") or ""))
    return (
        f'<article class="{card_class}">'
        f"{_thumbnail_html(shot, clip_paths)}"
        f'<div class="body">'
        f'<div class="range">{escape(clip)} · {start}–{end} · {escape(key)}</div>'
        f'<p class="summary">{summary_text}</p>'
        f"{tags_html}"
        f'<div class="score">{score_text}<span class="components">{components}</span></div>'
        f'<div class="badges">{"".join(badges)}</div>'
        f'<div class="meta">{role} · {motion} · {hook}</div>'
        f"</div>"
        f"</article>"
    )


def _thumbnail_html(shot, clip_paths):
    clip = str(shot.get("clip") or "")
    path = clip_paths.get(clip)
    if str(shot.get("media_type") or "video") == "photo":
        t_s = 0.0
    else:
        start = float(shot.get("start_s") or 0.0)
        end = float(shot.get("end_s") or 0.0)
        t_s = start + (end - start) / 2
    data_uri = thumbnail_data_uri(path, t_s)
    if data_uri is None:
        return f'<div class="thumb placeholder">{escape(clip)}</div>'
    return f'<img class="thumb" src="{data_uri}" alt="{escape(clip)}">'


def _score_text(shot):
    score = shot.get("score")
    score_text = f"{float(score):.2f}" if score is not None else "–"
    scores = shot.get("scores") or {}
    vision = scores.get("vision")
    labels = scores.get("labels")
    vision_text = f"{float(vision):.2f}" if vision is not None else "–"
    labels_text = f"{float(labels):.2f}" if labels is not None else "–"
    return score_text, f"vision {vision_text} · labels {labels_text}"


def _format_time(seconds):
    total = max(0.0, float(seconds))
    minutes = int(total // 60)
    remainder = total - minutes * 60
    return f"{minutes:02d}:{remainder:04.1f}"


def _html_document(workspace_name, summary_line, generated_at, sections):
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>shotlog · {escape(workspace_name)}</title>\n"
        "<style>\n"
        ":root { color-scheme: dark light; --water:#13202b; --deck:#1b2a37; --rope:#2b3d4d; "
        "--stone:#ece5d5; --haze:#9badbb; --sea:#57b8c4; --keep:#a3cf7f; --left:#e2806f; --busy:#e8b54d; --hero:#e8b54d; "
        "--bg:var(--water); --surface:var(--deck); --line:var(--rope); --text:var(--stone); "
        "--muted:var(--haze); --accent:var(--sea); --ok:var(--keep); --warn:var(--busy); --danger:var(--left); "
        "--fs-xs:.78rem; --fs-s:.88rem; --fs-m:1rem; --fs-l:1.25rem; --fs-xl:1.6rem; --fs-2xl:2rem; "
        "--lh-tight:1.15; --lh-body:1.55; "
        "--font-display:ui-serif, Georgia, serif; --font-body:system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; "
        "--font-mono:ui-monospace, SFMono-Regular, Menlo, monospace; "
        "--sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px; --sp-5:24px; --sp-6:32px; "
        "--radius-s:6px; --radius-l:14px; --radius-pill:999px; --tap:44px; }\n"
        "@media (prefers-color-scheme: light) { :root { "
        "--water:#eef3f5; --deck:#ffffff; --rope:#d5dee3; --stone:#14232c; --haze:#5a6f7b; "
        "--sea:#17707c; --keep:#3d7a38; --left:#b0432e; --busy:#8a6410; } }\n"
        "* { box-sizing: border-box; }\n"
        "body { margin: 0; padding: 14px; font: 15px/1.5 system-ui, -apple-system, 'Segoe UI', sans-serif; "
        "background: var(--deck); color: var(--stone); }\n"
        "header { margin-bottom: 14px; }\n"
        "h1 { font-family: ui-serif, Georgia, serif; font-weight: 500; font-size: 1.35rem; margin: 0 0 2px; }\n"
        "header p { margin: 0; color: var(--haze); font-size: 0.88rem; }\n"
        "main { display: flex; flex-direction: column; gap: 18px; }\n"
        ".shots-section { border-top: 1px solid var(--rope); padding-top: 12px; }\n"
        ".shots-section summary { min-height: 44px; display: flex; align-items: center; cursor: pointer; "
        "font-family: ui-serif, Georgia, serif; font-weight: 500; font-size: 1.15rem; }\n"
        ".groups { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }\n"
        ".clip-group h3 { font-family: ui-serif, Georgia, serif; font-weight: 500; font-size: 1rem; "
        "margin: 0 0 8px; color: var(--haze); overflow-wrap: anywhere; }\n"
        ".card { background: var(--water); border-radius: 12px; overflow: hidden; }\n"
        ".card.ineligible { opacity: 0.55; }\n"
        ".thumb { display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: cover; background: var(--rope); }\n"
        ".thumb.placeholder { display: flex; align-items: center; justify-content: center; "
        "color: var(--haze); font-size: 0.8rem; padding: 24px 8px; text-align: center; }\n"
        ".body { padding: 10px 12px 14px; }\n"
        ".range { font-size: 0.8rem; color: var(--haze); margin-bottom: 6px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }\n"
        ".summary { font-size: 1rem; margin: 0 0 8px; }\n"
        ".tags { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 8px; }\n"
        ".tag { border: 1px solid var(--rope); border-radius: 999px; padding: 1px 8px; "
        "font-size: 0.75rem; color: var(--haze); }\n"
        ".score { font-size: 1.15rem; font-weight: 650; font-variant-numeric: tabular-nums; }\n"
        ".components { font-size: 0.78rem; color: var(--haze); margin-left: 6px; font-weight: 400; }\n"
        ".badges { display: flex; flex-wrap: wrap; gap: 10px; margin: 6px 0; }\n"
        ".badge { font-size: 0.78rem; font-weight: 600; display: inline-flex; align-items: center; gap: 5px; }\n"
        ".badge::before { content: ''; width: 8px; height: 8px; border-radius: 50%; background: currentColor; }\n"
        ".badge.hero { color: var(--hero); }\n"
        ".badge.eligible { color: var(--keep); }\n"
        ".badge.ineligible { color: var(--left); }\n"
        ".badge.media { color: var(--sea); }\n"
        ".meta { font-size: 0.78rem; color: var(--haze); overflow-wrap: anywhere; }\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        "<header>\n"
        f"<h1>{escape(workspace_name)}</h1>\n"
        f'<p class="summary">{escape(summary_line)}</p>\n'
        f'<p class="generated">generated {escape(str(generated_at or ""))}</p>\n'
        "</header>\n"
        "<main>\n"
        f"{sections}\n"
        "</main>\n"
        "<script>\n"
        "(function(){var ds=document.querySelectorAll('details[data-keep]');\n"
        "for(var i=0;i<ds.length;i++){(function(d){var k='shots:'+d.id;\n"
        "try{if(sessionStorage.getItem(k)==='open')d.open=true;}catch(e){}\n"
        "d.addEventListener('toggle',function(){try{sessionStorage.setItem(k,d.open?'open':'closed');}catch(e){}});})(ds[i]);}})();\n"
        "</script>\n"
        "</body>\n"
        "</html>\n"
    )


def _atomic_write_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    sys.exit(main())
