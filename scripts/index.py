#!/usr/bin/env python3
"""Join the workspace analyses into ``shotlog.json``, one scored record per shot.

Usage:
    python scripts/index.py --workspace DIR
    python scripts/index.py --workspace DIR --json
    python scripts/index.py --workspace DIR --clips 0072,0081 --force --json

Reads ``quality/<stem>.json`` (required) plus ``motion/<stem>.json``,
``describe/<stem>.json`` and ``similarity.json`` (all optional) from the
workspace and writes one ``shotlog.json`` validated against
``schemas/shotlog.schema.json``. Every quality segment becomes a scored
candidate with a hook and a reason; the vision verdict supplies the effective
role and motion label on uncertain segments. No model is called. Sources are
read-only; the output is written atomically.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

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
HOOK_WORDS = 8


class ShotlogError(RuntimeError):
    """Raised when a workspace cannot be turned into a shotlog."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="index.py",
        description=(
            "Join quality/, motion/, describe/ and similarity.json into shotlog.json: "
            "every quality segment as a scored candidate the director ranks."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/index.py --workspace ~/Videos/workspace\n"
            "  python scripts/index.py --workspace ~/Videos/workspace --json\n"
            "  python scripts/index.py --workspace ~/Videos/workspace \\\n"
            "      --clips 0072,0081 --force --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--workspace", required=True, help="workspace holding quality/; shotlog.json is written here")
    parser.add_argument("--weight-vision", type=float, default=DEFAULT_WEIGHT_VISION, help="fusion weight of the vision component (default 0.55)")
    parser.add_argument("--weight-labels", type=float, default=DEFAULT_WEIGHT_LABELS, help="fusion weight of the label component (default 0.45)")
    parser.add_argument("--clips", default=None, help="comma-separated 4-digit counters or stem substrings to include")
    parser.add_argument("--force", action="store_true", help="recompute even when shotlog.json is up to date")
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


def overlap_seconds(a_start, a_end, b_start, b_end):
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def run(
    workspace,
    weight_vision=DEFAULT_WEIGHT_VISION,
    weight_labels=DEFAULT_WEIGHT_LABELS,
    clips=None,
    force=False,
):
    """Build ``workspace/shotlog.json``; return the summary and the exit code."""
    started = time.monotonic()
    if weight_vision < 0 or weight_labels < 0:
        raise ShotlogError("fusion weights must be non-negative")
    if weight_vision + weight_labels <= 0:
        raise ShotlogError("weight vision + weight labels must be positive")
    workspace_path = Path(workspace).expanduser().resolve()
    quality_dir = workspace_path / "quality"
    if not quality_dir.is_dir():
        raise ShotlogError(f"no quality folder in workspace: {quality_dir}")
    selected = _select_quality_files(quality_dir, clips)
    output_path = workspace_path / "shotlog.json"
    inputs = _input_files(workspace_path, selected)
    if not force and _output_is_up_to_date(output_path, inputs):
        return _skipped_summary(output_path, started), 0
    similarity_map = _similarity_map(_load_json(workspace_path / "similarity.json"))
    shots = []
    for quality_file in selected:
        record = _load_quality(quality_file)
        counter = clip_counter(quality_file.name)
        motion = _load_json(workspace_path / "motion" / f"{quality_file.stem}.json")
        describe_segments = _segments_by_id(_load_json(workspace_path / "describe" / f"{quality_file.stem}.json"))
        for segment in record["segments"]:
            shots.append(
                _build_shot(
                    counter,
                    record,
                    segment,
                    motion,
                    describe_segments.get(segment.get("id")),
                    similarity_map,
                    weight_vision,
                    weight_labels,
                )
            )
    _apply_suppression(shots)
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
    _atomic_write_json(output_path, record)
    summary = _summary(shots, weights, wall_time_s, output_path)
    return summary, 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary, exit_code = run(
            args.workspace,
            weight_vision=args.weight_vision,
            weight_labels=args.weight_labels,
            clips=args.clips,
            force=args.force,
        )
    except (ShotlogError, SchemaError, OSError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
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


def _build_shot(counter, record, segment, motion, describe_segment, similarity_map, weight_vision, weight_labels):
    role, role_source, motion_label, label_source, confidence = _effective_labels(segment, describe_segment)
    uncertainty = bool(segment.get("uncertain"))
    vision = _vision_fields(describe_segment)
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
    key = segment_key(counter, segment.get("id"))
    return {
        "key": key,
        "clip": record.get("file", ""),
        "segment_id": segment.get("id"),
        "start_s": segment["start_s"],
        "end_s": segment["end_s"],
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
        "score": score,
        "scores": {"vision": vision_score, "labels": labels},
        "hook": _hook(vision, role, motion_label),
        "reason": _reason(vision, motion_label, label_source, role, defects, eligible),
        "similar_to": list(similarity_map.get(key, [])),
        "defects": [defect.get("name") for defect in defects],
        "suppressed_by": None,
    }


def _effective_labels(segment, describe_segment):
    if _is_described(describe_segment) and describe_segment.get("verdict"):
        verdict = describe_segment["verdict"]
        return (
            verdict.get("role"),
            "verdict",
            verdict.get("motion_label"),
            "verdict",
            verdict.get("confidence"),
        )
    motion_label = segment.get("motion_label")
    if motion_label is None:
        motion_label = "static"
    return segment.get("role"), "geometry", motion_label, "geometry", segment.get("confidence")


def _is_described(describe_segment):
    return isinstance(describe_segment, dict) and describe_segment.get("status") == "described"


def _vision_fields(describe_segment):
    if not _is_described(describe_segment):
        return None
    return {
        "description": describe_segment.get("description"),
        "summary": describe_segment.get("summary"),
        "tags": list(describe_segment.get("tags") or []),
        "subject": describe_segment.get("subject"),
        "scenery": describe_segment.get("scenery"),
        "semantic_motion": describe_segment.get("semantic_motion"),
        "interest": describe_segment.get("interest"),
        "hero": describe_segment.get("hero"),
    }


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


def _reason(vision, motion_label, label_source, role, defects, eligible):
    parts = []
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
    overlap = overlap_seconds(first["start_s"], first["end_s"], second["start_s"], second["end_s"])
    shorter = min(first["end_s"] - first["start_s"], second["end_s"] - second["start_s"])
    return shorter > 0 and overlap > SUPPRESS_OVERLAP_RATIO * shorter


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


def _input_files(workspace_path, quality_files):
    files = list(quality_files)
    similarity = workspace_path / "similarity.json"
    if similarity.exists():
        files.append(similarity)
    for quality_file in quality_files:
        for folder in ("motion", "describe"):
            path = workspace_path / folder / f"{quality_file.stem}.json"
            if path.exists():
                files.append(path)
    return files


def _select_quality_files(quality_dir, clips):
    tokens = _clip_tokens(clips)
    files = sorted(quality_dir.glob("*.json"))
    selected = [path for path in files if _matches(path.stem, tokens)]
    if not selected:
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


def _summary(shots, weights, wall_time_s, output_path):
    return {
        "shots": len(shots),
        "eligible": sum(1 for shot in shots if shot["eligible"]),
        "with_vision": sum(1 for shot in shots if shot.get("description") is not None),
        "suppressed": sum(1 for shot in shots if shot.get("suppressed_by")),
        "weights": weights,
        "wall_time_s": wall_time_s,
        "llm_cost_usd": 0.0,
        "output": str(output_path),
        "skipped": False,
    }


def _skipped_summary(output_path, started):
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
        "skipped": True,
    }


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
