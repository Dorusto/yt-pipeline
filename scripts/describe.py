#!/usr/bin/env python3
"""Describe every kept quality segment with the vision model.

Usage:
    python scripts/describe.py --workspace DIR
    python scripts/describe.py --workspace DIR --json
    python scripts/describe.py --workspace DIR --clips 0067,0081 --backend mock --force

Reads ``quality/<stem>.json`` from the workspace, samples three frames per kept
segment, sends them with the segment facts to the vision model and writes
``describe/<stem>.json`` validated against ``schemas/describe.schema.json``.
Confident accidental/setup/teardown segments are skipped without a model call;
every uncertain segment carries a verdict whose label wins in the shotlog.
Frames live in memory only and are never written to disk. Sources are read-only;
outputs are written atomically.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2

from common.config import ConfigError, load_config
from common.llm import BudgetExceeded, LLMError
from common.schema import SchemaError, validate, validate_or_raise
from common.vision import VisionFrame, VisionRequest, make_vision_model

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = REPO_ROOT / "prompts" / "describe_segment.md"
SKIP_ROLES = {"accidental", "setup", "teardown"}
FRAME_COUNT = 3
JPEG_QUALITY = 85
SEMANTIC_MOTIONS = {"orbit", "tracking", "reveal", "travel", "none"}
ROLES = {
    "move",
    "hold",
    "transition_in",
    "transition_out",
    "setup",
    "teardown",
    "accidental",
}
MOTION_LABELS = {
    "static",
    "minor_motion",
    "pan_left",
    "pan_right",
    "tilt_up",
    "tilt_down",
    "roll_cw",
    "roll_ccw",
    "dolly_in",
    "dolly_out",
    "zoom_in",
    "zoom_out",
    "truck_left",
    "truck_right",
    "pedestal_up",
    "pedestal_down",
    "complex_motion",
    "travel_forward",
    "travel_backward",
    "orbit_candidate",
    "orbit",
    "tracking",
    "reveal",
    "pov",
}


class DescribeError(RuntimeError):
    """Raised when a clip cannot be described."""


class RunState:
    """Model calls, spend and the reason a run stopped early."""

    def __init__(self, max_calls=None):
        self.calls = 0
        self.max_calls = max_calls
        self.cost_usd = 0.0
        self.stop_reason = None


def build_parser():
    parser = argparse.ArgumentParser(
        prog="describe.py",
        description=(
            "Describe every kept quality segment with the vision model: three frames "
            "in time order plus the motion label, one structured answer per segment "
            "and a verdict for every uncertain segment."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/describe.py --workspace ~/Videos/workspace\n"
            "  python scripts/describe.py --workspace ~/Videos/workspace --json\n"
            "  python scripts/describe.py --workspace ~/Videos/workspace \\\n"
            "      --clips 0067,0081 --backend mock --force --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--workspace", required=True, help="workspace holding quality/; describe/ is written here")
    parser.add_argument("--clips", default=None, help="comma-separated 4-digit counters or stem substrings to include")
    parser.add_argument("--model", default=None, help="vision model id override (default models.vision from config)")
    parser.add_argument("--backend", choices=["mock", "openrouter"], default=None, help="vision backend override")
    parser.add_argument("--config", default=None, help="config file path (default repo config.yaml)")
    parser.add_argument("--prompt-file", default=None, help="prompt path (default prompts/describe_segment.md)")
    parser.add_argument("--max-calls", type=int, default=None, help="stop after this many model calls")
    parser.add_argument("--budget-usd", type=float, default=None, help="refuse calls past this spend (default llm_budget_usd)")
    parser.add_argument("--spend-ledger", default=None, help="spend ledger path (default WORKSPACE/llm_spend.json)")
    parser.add_argument("--frame-width", type=int, default=640, help="width frames are resized to before sending (default 640)")
    parser.add_argument("--force", action="store_true", help="re-describe clips whose output already exists")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def frame_times(start_s, end_s):
    """Three evenly spaced timestamps inside the segment interior, in time order."""
    span = end_s - start_s
    return [start_s + (index + 0.5) / FRAME_COUNT * span for index in range(FRAME_COUNT)]


def read_frames(path, times, width):
    """Read one JPEG frame per timestamp from a clip; frames stay in memory."""
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise DescribeError(f"cannot read video: {path}")
    frames = []
    try:
        for t_s in times:
            capture.set(cv2.CAP_PROP_POS_MSEC, t_s * 1000.0)
            ok, frame = capture.read()
            if not ok:
                raise DescribeError(f"cannot read frame at {t_s:.2f}s from {path}")
            frames.append(VisionFrame(t_s=t_s, jpeg_bytes=_encode_jpeg(frame, width)))
    finally:
        capture.release()
    return frames


def build_prompt(base_prompt, clip_file, segment, times):
    """Append the segment facts block to the model prompt."""
    uncertain = bool(segment.get("uncertain"))
    reasons = segment.get("uncertain_reasons") or []
    if uncertain:
        uncertain_text = "yes (" + ", ".join(reasons) + ")" if reasons else "yes"
    else:
        uncertain_text = "no"
    lines = [
        f"- clip: {clip_file}",
        f"- time: {segment['start_s']} s to {segment['end_s']} s",
        f"- geometric role: {segment.get('role')} (confidence {segment.get('confidence')})",
        f"- geometric motion label: {segment.get('motion_label')} "
        f"(confidence {segment.get('motion_label_confidence')})",
        f"- uncertain: {uncertain_text}",
        "- frames (in order): " + ", ".join(f"{t} s" for t in times),
    ]
    return base_prompt + "\n\nSegment facts:\n" + "\n".join(lines)


def parse_answer(text):
    """Parse the model text as a JSON object, tolerating markdown fences."""
    data = _loads(_strip_fences(text))
    if data is None:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            data = _loads(text[start : end + 1])
    return data


def normalize_answer(data):
    """Coerce a parsed answer into the fields the schema accepts."""
    return {
        "description": _as_string(data.get("description")),
        "subject": _as_string(data.get("subject")),
        "scenery": _as_string(data.get("scenery")),
        "semantic_motion": data.get("semantic_motion")
        if data.get("semantic_motion") in SEMANTIC_MOTIONS
        else "none",
        "interest": _clamp_interest(data.get("interest")),
        "hero": data.get("hero") if isinstance(data.get("hero"), bool) else False,
        "verdict": normalize_verdict(data.get("verdict")),
    }


def normalize_verdict(value):
    """Return a schema-valid verdict object, or None when the value is invalid."""
    if not isinstance(value, dict):
        return None
    role = value.get("role")
    label = value.get("motion_label")
    confidence = value.get("confidence")
    note = value.get("note")
    if role not in ROLES or label not in MOTION_LABELS:
        return None
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    if not isinstance(note, str):
        return None
    return {
        "role": role,
        "motion_label": label,
        "confidence": min(1.0, max(0.0, float(confidence))),
        "note": note,
    }


def describe_segments(record, clip_path, model, model_id, base_prompt, frame_width, state):
    """Describe or skip every segment of one clip, in time order."""
    warnings = []
    segments = []
    for segment in record["segments"]:
        role = segment.get("role")
        uncertain = bool(segment.get("uncertain"))
        if role in SKIP_ROLES and not uncertain:
            segments.append(_skipped_segment(segment, f"confident {role}"))
            continue
        if state.stop_reason is not None:
            segments.append(_skipped_segment(segment, state.stop_reason))
            continue
        if not _calls_available(state):
            state.stop_reason = "max_calls"
            segments.append(_skipped_segment(segment, "max_calls"))
            continue
        times = frame_times(segment["start_s"], segment["end_s"])
        frames = read_frames(clip_path, times, frame_width)
        prompt = build_prompt(base_prompt, record["file"], segment, times)
        request = VisionRequest(
            prompt=prompt,
            frames=frames,
            context=_context(segment),
            model=model_id,
        )
        try:
            response = _call_model(model, request, state)
        except BudgetExceeded:
            state.stop_reason = "budget_exceeded"
            segments.append(_skipped_segment(segment, "budget_exceeded"))
            continue
        segment_cost = response.cost_usd
        answer = parse_answer(response.text)
        if answer is None:
            warnings.append(f"{record['file']} {segment['id']}: unparseable model output")
            segments.append(_skipped_segment(segment, "unparseable model output"))
            continue
        normalized = normalize_answer(answer)
        verdict = normalized["verdict"]
        if uncertain and verdict is None:
            segment_cost += _retry_verdict(model, request, state, normalized, warnings, record, segment)
            verdict = normalized["verdict"]
        if not uncertain:
            verdict = None
        segments.append(_described_segment(segment, times, normalized, verdict, segment_cost))
    return segments, warnings


def describe_clip(record, clip_path, model, model_id, base_prompt, frame_width, state):
    """Describe one clip and return its full describe record."""
    started = time.monotonic()
    segments, warnings = describe_segments(
        record, clip_path, model, model_id, base_prompt, frame_width, state
    )
    return {
        "file": record["file"],
        "path": str(clip_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_s": record["duration_s"],
        "model": model_id,
        "llm_cost_usd": round(sum(segment["cost_usd"] or 0.0 for segment in segments), 8),
        "wall_time_s": round(time.monotonic() - started, 2),
        "segments": segments,
        "warnings": warnings,
    }


def run(
    workspace,
    clips=None,
    model=None,
    model_id=None,
    prompt_path=None,
    max_calls=None,
    budget_usd=None,
    spend_ledger=None,
    frame_width=640,
    force=False,
    config=None,
    backend=None,
):
    """Describe the selected clips into ``workspace/describe``; return summary and exit code."""
    started = time.monotonic()
    if frame_width <= 0:
        raise DescribeError("--frame-width must be positive")
    workspace_path = Path(workspace).expanduser().resolve()
    quality_dir = workspace_path / "quality"
    if not quality_dir.is_dir():
        raise DescribeError(f"no quality folder in workspace: {quality_dir}")
    if config is None:
        config = load_config()
    effective = dict(config)
    if backend is not None:
        effective["llm_backend"] = backend
    if budget_usd is not None:
        effective["llm_budget_usd"] = budget_usd
    if model_id is None:
        model_id = (effective.get("models") or {}).get("vision")
    if not model_id:
        raise DescribeError("no vision model id: set models.vision in the config or pass --model")
    if prompt_path is None:
        prompt_path = DEFAULT_PROMPT
    prompt_path = Path(prompt_path)
    if not prompt_path.exists():
        raise DescribeError(f"prompt file not found: {prompt_path}")
    base_prompt = prompt_path.read_text(encoding="utf-8")
    if spend_ledger is None:
        spend_ledger = workspace_path / "llm_spend.json"
    if model is None:
        model = make_vision_model(effective, ledger_path=spend_ledger)
    selected = _select_quality_files(quality_dir, clips)
    output_dir = workspace_path / "describe"
    output_dir.mkdir(parents=True, exist_ok=True)
    state = RunState(max_calls=max_calls)
    described = 0
    skipped = 0
    segments_described = 0
    segments_skipped = 0
    for quality_file in selected:
        output_path = output_dir / quality_file.name
        if not force and _output_is_valid(output_path):
            skipped += 1
            continue
        record = _load_quality(quality_file)
        clip_path = _clip_path(record, quality_file)
        clip_record = describe_clip(
            record, clip_path, model, model_id, base_prompt, frame_width, state
        )
        validate_or_raise(clip_record, "describe")
        _atomic_write_json(output_path, clip_record)
        described += 1
        segments_described += sum(
            1 for segment in clip_record["segments"] if segment["status"] == "described"
        )
        segments_skipped += sum(
            1 for segment in clip_record["segments"] if segment["status"] == "skipped"
        )
        if state.stop_reason is not None:
            break
    summary = {
        "clips": len(selected),
        "described": described,
        "skipped": skipped,
        "segments_described": segments_described,
        "segments_skipped": segments_skipped,
        "model": model_id,
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": round(state.cost_usd, 8),
    }
    return summary, (2 if state.stop_reason is not None else 0)


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary, exit_code = run(
            args.workspace,
            clips=args.clips,
            model_id=args.model,
            prompt_path=args.prompt_file,
            max_calls=args.max_calls,
            budget_usd=args.budget_usd,
            spend_ledger=args.spend_ledger,
            frame_width=args.frame_width,
            force=args.force,
            config=load_config(args.config),
            backend=args.backend,
        )
    except (DescribeError, ConfigError, SchemaError, LLMError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary))
    else:
        print(
            f"described {summary['described']} clips, skipped {summary['skipped']} "
            f"(of {summary['clips']})"
        )
        print(
            f"segments: {summary['segments_described']} described, "
            f"{summary['segments_skipped']} skipped"
        )
        print(
            f"model {summary['model']}, cost ${summary['llm_cost_usd']:.4f}, "
            f"{summary['wall_time_s']}s"
        )
    return exit_code


def _retry_verdict(model, request, state, normalized, warnings, record, segment):
    """Retry once for a missing verdict; keep the description and fall back if it fails."""
    cost = 0.0
    if not _calls_available(state):
        state.stop_reason = "max_calls"
    else:
        try:
            retry = _call_model(model, request, state)
            cost = retry.cost_usd
            retry_answer = parse_answer(retry.text)
            if retry_answer is not None:
                retry_normalized = normalize_answer(retry_answer)
                if retry_normalized["verdict"] is not None:
                    normalized.update(retry_normalized)
                    return cost
        except BudgetExceeded:
            state.stop_reason = "budget_exceeded"
    normalized["verdict"] = _fallback_verdict(segment)
    warnings.append(f"{record['file']} {segment['id']}: model gave no verdict; geometry kept")
    return cost


def _fallback_verdict(segment):
    label = segment.get("motion_label")
    if label not in MOTION_LABELS:
        label = "static"
    return {
        "role": segment.get("role"),
        "motion_label": label,
        "confidence": 0.0,
        "note": "model gave no verdict; geometry kept",
    }


def _call_model(model, request, state):
    response = model.describe(request)
    state.calls += 1
    state.cost_usd += response.cost_usd
    return response


def _calls_available(state):
    return state.max_calls is None or state.calls < state.max_calls


def _context(segment):
    return {
        "id": segment.get("id"),
        "start_s": segment.get("start_s"),
        "end_s": segment.get("end_s"),
        "role": segment.get("role"),
        "motion_label": segment.get("motion_label"),
        "motion_label_confidence": segment.get("motion_label_confidence"),
        "uncertain": bool(segment.get("uncertain")),
        "uncertain_reasons": list(segment.get("uncertain_reasons") or []),
    }


def _described_segment(segment, times, normalized, verdict, cost_usd):
    return {
        "id": segment["id"],
        "start_s": segment["start_s"],
        "end_s": segment["end_s"],
        "status": "described",
        "role": segment["role"],
        "motion_label": segment.get("motion_label"),
        "uncertain": bool(segment.get("uncertain")),
        "uncertain_reasons": list(segment.get("uncertain_reasons") or []),
        "frames": [{"t_s": t_s} for t_s in times],
        "description": normalized["description"],
        "subject": normalized["subject"],
        "scenery": normalized["scenery"],
        "semantic_motion": normalized["semantic_motion"],
        "interest": normalized["interest"],
        "hero": normalized["hero"],
        "verdict": verdict,
        "skip_reason": None,
        "cost_usd": cost_usd,
    }


def _skipped_segment(segment, reason):
    return {
        "id": segment["id"],
        "start_s": segment["start_s"],
        "end_s": segment["end_s"],
        "status": "skipped",
        "role": segment["role"],
        "motion_label": segment.get("motion_label"),
        "uncertain": bool(segment.get("uncertain")),
        "uncertain_reasons": list(segment.get("uncertain_reasons") or []),
        "skip_reason": reason,
        "cost_usd": None,
    }


def _encode_jpeg(frame, width):
    height, frame_width = frame.shape[:2]
    if frame_width != width:
        scaled_height = max(1, int(round(height * width / frame_width)))
        frame = cv2.resize(frame, (width, scaled_height), interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise DescribeError("could not encode a frame as JPEG")
    return buffer.tobytes()


def _strip_fences(text):
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _loads(text):
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _as_string(value):
    return value if isinstance(value, str) else ""


def _clamp_interest(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return min(100, max(0, value))


def _select_quality_files(quality_dir, clips):
    tokens = _clip_tokens(clips)
    files = sorted(quality_dir.glob("*.json"))
    selected = [path for path in files if _matches(path.stem, tokens)]
    if not selected:
        raise DescribeError(f"no quality files matched in {quality_dir}")
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
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DescribeError(f"unreadable quality file {path}: {exc}") from exc
    if not isinstance(data, dict) or not data.get("segments"):
        raise DescribeError(f"quality file {path} has no segments")
    return data


def _clip_path(record, quality_file):
    raw = record.get("path")
    if not raw:
        raise DescribeError(f"quality file {quality_file.name} has no clip path")
    path = Path(raw)
    if not path.exists():
        raise DescribeError(f"clip not found: {path}")
    return path


def _output_is_valid(path):
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return validate(data, "describe") == []


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
