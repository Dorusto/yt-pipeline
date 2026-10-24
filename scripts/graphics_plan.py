#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Propose where graphic overlays belong on a timeline, for human approval.

Usage:
    python3 scripts/graphics_plan.py WORDS.json [--markers markers.json] [--context FILE]
        [--out graphics_plan.json] [--max-entries N] [--json]

Reads a timeline words JSON (the flat shape ``subs_from_edit.py`` writes or the
Whisper result shape ``transcribe.py`` writes) and, optionally, a ``markers.json``
from FilmCraft. The ``graphics`` model role proposes at most ``--max-entries``
overlays (time, template, props, reason); the tool assigns ids, sets
``approved: false`` on every entry and writes ``graphics_plan.json`` validated
against ``schemas/graphics_plan.schema.json``. A human flips ``approved`` and
``graphics.py --plan`` renders only the approved entries. Nothing is rendered
here.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from common.config import ConfigError, load_config, model_for
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.errors import report_error
from common.llm import LLMError, make_client
from common.overlap import overlap_fraction, overlap_seconds
from common.schema import SchemaError, validate, validate_or_raise
from common.text import read_text

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = REPO_ROOT / "prompts" / "graphics_plan.md"
TOOL = "scripts/graphics_plan.py"
SCHEMA_VERSION = "0.1"
DEFAULT_MAX_ENTRIES = 4
MAX_DURATION_S = 15.0
MARKER_MATCH_S = 1.0
MANGO_COLOUR = "mango"
TRANSCRIPT_WINDOW_S = 5.0
WORDS_SHAPES = ("flat", "whisper")


class GraphicsPlanError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="graphics_plan.py",
        description=(
            "Read a timeline words JSON (and optionally a markers.json) and have the "
            "graphics model propose where graphic overlays belong, written to "
            "graphics_plan.json for human approval. Nothing is rendered here."
        ),
        epilog=(
            "examples:\n"
            "  python3 scripts/graphics_plan.py words.json --json\n"
            "  python3 scripts/graphics_plan.py words.json --markers markers.json "
            "--context context.md --max-entries 3\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("words", help="timeline words JSON (flat list or Whisper result shape)")
    parser.add_argument("--markers", default=None, help="markers.json from FilmCraft; only Mango markers become candidates")
    parser.add_argument("--context", default=None, help="markdown note with the clip's angle, passed to the model")
    parser.add_argument("--out", default=None, help="output plan path (default: <words dir>/graphics_plan.json)")
    parser.add_argument(
        "--max-entries",
        type=int,
        default=DEFAULT_MAX_ENTRIES,
        help=f"cap on proposed entries, >= 0 (default {DEFAULT_MAX_ENTRIES})",
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def read_json(path, label):
    """Read a JSON file, raising a named error when it is missing or unreadable."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise GraphicsPlanError(f"{label} file not found: {path}")
    except OSError as exc:
        raise GraphicsPlanError(f"cannot read {label} file {path}: {exc}")
    except json.JSONDecodeError as exc:
        raise GraphicsPlanError(f"{label} file {path} is not valid JSON: {exc}")


def as_number(value):
    """Return ``value`` as a float when it is a real number, else ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def as_optional_string(value):
    """Return ``value`` when it is a string, else ``None``."""
    return value if isinstance(value, str) else None


def load_words(path):
    """Load a timeline words JSON and return ``(words, shape)`` with shape ``flat`` or ``whisper``."""
    data = read_json(path, "words")
    if isinstance(data, list):
        shape = "flat"
        raw = data
    elif isinstance(data, dict) and isinstance(data.get("segments"), list):
        shape = "whisper"
        raw = [
            word
            for segment in data["segments"]
            if isinstance(segment, dict)
            for word in (segment.get("words") or [])
        ]
    else:
        raise GraphicsPlanError(
            f"words JSON {path} is neither a flat word list nor a Whisper result with segments"
        )
    words = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise GraphicsPlanError(f"words JSON {path}: word {index} is not an object")
        text = str(item.get("word", "")).strip()
        if not text:
            raise GraphicsPlanError(f"words JSON {path}: word {index} has no 'word' text")
        start = as_number(item.get("start"))
        end = as_number(item.get("end"))
        if start is None or end is None:
            raise GraphicsPlanError(
                f"words JSON {path}: word {index} ('{text}') has no numeric start/end"
            )
        words.append({"word": text, "start": start, "end": end})
    if not words:
        raise GraphicsPlanError(f"words JSON {path} has no words")
    words.sort(key=lambda word: (word["start"], word["end"]))
    return words, shape


def load_markers(path):
    """Load markers.json and return the Mango candidates as marker dicts, in time order."""
    data = read_json(path, "markers")
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict) and isinstance(data.get("markers"), list):
        raw = data["markers"]
    else:
        raise GraphicsPlanError(
            f"markers JSON {path} is neither a marker list nor an object with a 'markers' list"
        )
    candidates = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise GraphicsPlanError(f"markers JSON {path}: marker {index} is not an object")
        time_s = as_number(item.get("time_s"))
        if time_s is None:
            raise GraphicsPlanError(f"markers JSON {path}: marker {index} has no numeric time_s")
        if time_s < 0:
            raise GraphicsPlanError(f"markers JSON {path}: marker {index} has a negative time_s")
        colour = item.get("colour")
        if not isinstance(colour, str) or colour.strip().lower() != MANGO_COLOUR:
            continue
        candidates.append(
            {
                "time_s": time_s,
                "name": as_optional_string(item.get("name")),
                "comment": as_optional_string(item.get("comment")),
                "colour": colour,
                "kind": as_optional_string(item.get("kind")),
            }
        )
    candidates.sort(key=lambda marker: marker["time_s"])
    return candidates


def format_clock(seconds):
    """Format seconds as ``MM:SS`` (minutes may exceed 59 on a long timeline)."""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


def build_transcript(words, window_s=TRANSCRIPT_WINDOW_S):
    """Chunk the words into one ``[MM:SS] text`` line per ~window_s seconds."""
    lines = []
    chunk_start = None
    chunk_words = []
    for word in words:
        if chunk_start is None:
            chunk_start = word["start"]
        elif word["start"] - chunk_start >= window_s:
            lines.append(f"[{format_clock(chunk_start)}] {' '.join(chunk_words)}")
            chunk_start = word["start"]
            chunk_words = []
        chunk_words.append(word["word"])
    if chunk_words:
        lines.append(f"[{format_clock(chunk_start)}] {' '.join(chunk_words)}")
    return "\n".join(lines)


def format_marker_candidates(candidates):
    """Render the Mango candidates as ``- MM:SS (time s): hint`` lines."""
    lines = []
    for marker in candidates:
        hint = marker["name"] or marker["comment"] or ""
        lines.append(f"- {format_clock(marker['time_s'])} ({marker['time_s']:.2f} s): {hint}")
    return "\n".join(lines)


def build_message(prompt_text, transcript, candidates, context_text, max_entries):
    """Assemble the model message: prompt, cap, transcript, marker candidates and context."""
    parts = [prompt_text, f"## Max entries\n{max_entries}", "## Transcript", transcript]
    if candidates:
        parts.append("## Marker candidates")
        parts.append(format_marker_candidates(candidates))
    if context_text:
        parts.append("## Context")
        parts.append(context_text)
    return "\n\n".join(parts)


def parse_model_json(text):
    """Parse the model text as JSON, tolerating markdown fences."""
    raw = text.strip()
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GraphicsPlanError(f"model response is not valid JSON: {exc}")


def normalize_entries(data, max_entries):
    """Turn the model's ``entries`` into schema-shaped entries, sorted by time and capped."""
    if not isinstance(data, dict):
        raise GraphicsPlanError("model response is not a JSON object")
    raw = data.get("entries")
    if not isinstance(raw, list):
        raise GraphicsPlanError("model JSON key 'entries' must be a list")
    entries = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise GraphicsPlanError(f"model entry {index} is not an object")
        time_s = as_number(item.get("time_s"))
        duration_s = as_number(item.get("duration_s"))
        template = item.get("template")
        props = item.get("props")
        reason = item.get("reason")
        if time_s is None:
            raise GraphicsPlanError(f"model entry {index} has no numeric time_s")
        if duration_s is None:
            raise GraphicsPlanError(f"model entry {index} has no numeric duration_s")
        if not isinstance(template, str) or not template:
            raise GraphicsPlanError(f"model entry {index} has no template")
        if not isinstance(props, dict):
            raise GraphicsPlanError(f"model entry {index} has no props object")
        if not isinstance(reason, str) or not reason.strip():
            raise GraphicsPlanError(f"model entry {index} has no reason")
        entry = {
            "time_s": time_s,
            "duration_s": duration_s,
            "template": template,
            "props": props,
            "reason": reason,
        }
        hint = item.get("hint")
        if isinstance(hint, str) and hint.strip():
            entry["hint"] = hint
        entries.append(entry)
    entries.sort(key=lambda entry: entry["time_s"])
    return entries[:max_entries]


def nearest_candidate(time_s, candidates):
    """Return the Mango candidate within MARKER_MATCH_S of ``time_s``, else ``None``."""
    best = None
    best_delta = None
    for candidate in candidates:
        delta = abs(candidate["time_s"] - time_s)
        if best_delta is None or delta < best_delta:
            best = candidate
            best_delta = delta
    if best is not None and best_delta <= MARKER_MATCH_S:
        return best
    return None


def build_plan(entries, candidates, words_path, markers_path):
    """Build the finished plan: ids in time order, ``approved: false`` and the origin per entry."""
    plan_entries = []
    for index, entry in enumerate(entries):
        candidate = nearest_candidate(entry["time_s"], candidates)
        built = {
            "id": f"g{index + 1}",
            "time_s": entry["time_s"],
            "duration_s": entry["duration_s"],
            "template": entry["template"],
            "props": entry["props"],
            "reason": entry["reason"],
            "approved": False,
        }
        if candidate is not None:
            built["origin"] = "marker"
            hint = candidate["name"] or candidate["comment"] or entry.get("hint")
            built["marker"] = {
                "time_s": candidate["time_s"],
                "name": candidate["name"],
                "comment": candidate["comment"],
                "colour": candidate["colour"],
                "kind": candidate["kind"],
            }
        else:
            built["origin"] = "model"
            hint = entry.get("hint")
        if hint:
            built["hint"] = hint
        plan_entries.append(built)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": TOOL,
        "source_words": str(words_path),
        "markers_path": str(markers_path) if markers_path else None,
        "entries": plan_entries,
    }


def overlap_errors(entries):
    """Return an error for every pair of entries overlapping more than half of the shorter."""
    errors = []
    for left in range(len(entries)):
        for right in range(left + 1, len(entries)):
            first = entries[left]
            second = entries[right]
            values = (
                first.get("time_s"),
                first.get("duration_s"),
                second.get("time_s"),
                second.get("duration_s"),
            )
            if any(as_number(value) is None for value in values):
                continue
            first_start, first_duration, second_start, second_duration = (float(v) for v in values)
            first_end = first_start + first_duration
            second_end = second_start + second_duration
            overlap_s = overlap_seconds(first_start, first_end, second_start, second_end)
            if overlap_fraction(first_start, first_end, second_start, second_end) > 0.5:
                errors.append(
                    f"entries/{left} and entries/{right} overlap by {overlap_s:.2f}s, "
                    "more than half of the shorter"
                )
    return errors


def validate_plan(plan, span):
    """Return the schema and timing problems of a finished plan."""
    errors = list(validate(plan, "graphics_plan"))
    for index, entry in enumerate(plan["entries"]):
        time_s = as_number(entry.get("time_s"))
        if time_s is not None and time_s > span:
            errors.append(f"entries/{index}/time_s: {time_s:g} is outside 0..{span:g}")
    errors.extend(overlap_errors(plan["entries"]))
    return errors


def attempt_plan(text, candidates, words_path, markers_path, span, max_entries):
    """Parse, normalise and validate one model answer; return ``(plan, errors)``."""
    try:
        data = parse_model_json(text)
        entries = normalize_entries(data, max_entries)
    except GraphicsPlanError as exc:
        return None, [str(exc)]
    plan = build_plan(entries, candidates, words_path, markers_path)
    return plan, validate_plan(plan, span)


def write_plan(path, plan):
    """Write the plan as pretty UTF-8 JSON with a stable key order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def summarize(output, plan, candidates, shape, model_id, started, cost_usd):
    """Build the ``--json`` summary for a finished plan."""
    return {
        "output": str(output),
        "entries": len(plan["entries"]),
        "approved": sum(1 for entry in plan["entries"] if entry["approved"]),
        "markers_used": len(candidates),
        "words_shape": shape,
        "model": model_id,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": round(cost_usd, 8),
    }


def run(words_path, markers_path=None, context_path=None, out_path=None, max_entries=DEFAULT_MAX_ENTRIES):
    """Propose the graphics plan and return the ``--json`` summary; raise ``GraphicsPlanError`` on bad input."""
    started = time.monotonic()
    if max_entries < 0:
        raise GraphicsPlanError(f"--max-entries must be >= 0, got {max_entries}")
    words_file = Path(words_path).expanduser()
    words, shape = load_words(words_file)
    span = max(word["end"] for word in words)
    candidates = load_markers(markers_path) if markers_path else []
    context_text = read_text(context_path, "context", GraphicsPlanError) if context_path else None
    prompt_text = read_text(DEFAULT_PROMPT, "prompt", GraphicsPlanError)
    transcript = build_transcript(words)
    message = build_message(prompt_text, transcript, candidates, context_text, max_entries)
    config = load_config()
    model_id = model_for(config, "graphics")
    output = Path(out_path).expanduser() if out_path else words_file.parent / "graphics_plan.json"

    if max_entries == 0:
        plan = build_plan([], candidates, words_file, markers_path)
        validate_or_raise(plan, "graphics_plan")
        write_plan(output, plan)
        return summarize(output, plan, candidates, shape, model_id, started, 0.0)

    client = make_client(config)
    response = client.complete("graphics", [{"role": "user", "content": message}], temperature=0.3)
    cost_usd = response.cost_usd
    plan, errors = attempt_plan(response.text, candidates, words_file, markers_path, span, max_entries)
    if errors:
        retry_message = message + "\n\n## Validation errors\n" + "\n".join(errors)
        retry = client.complete(
            "graphics", [{"role": "user", "content": retry_message}], temperature=0.3
        )
        cost_usd += retry.cost_usd
        plan, errors = attempt_plan(
            retry.text, candidates, words_file, markers_path, span, max_entries
        )
    if errors:
        raise GraphicsPlanError("model plan is invalid: " + "; ".join(errors))
    write_plan(output, plan)
    return summarize(output, plan, candidates, shape, model_id, started, cost_usd)


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = run(
            args.words,
            markers_path=args.markers,
            context_path=args.context,
            out_path=args.out,
            max_entries=args.max_entries,
        )
    except (GraphicsPlanError, ConfigError, SchemaError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)
    except LLMError as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)

    if args.json:
        print(json.dumps(summary))
    else:
        print(f"written: {summary['output']} ({summary['entries']} entries)")
        print("review the plan and flip approved to true on the entries you want, then run graphics.py --plan")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
