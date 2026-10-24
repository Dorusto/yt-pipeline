#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Write 2-3 Romanian thumbnail concepts from a confirmed title and a corrected .srt.

The tool sends the prompt in ``prompts/thumbnail_concepts.md`` plus the context,
the confirmed title and the transcript to the ``metadata`` model role, validates
the answer and writes ``thumbnail_concepts.json`` (validated against
``schemas/thumbnail_concepts.schema.json``) and a Romanian
``thumbnail_concepts.md`` next to it. The chosen concept feeds ``thumbnail.py``
directly through ``--concepts``/``--pick``. A human reviews the concepts;
nothing is uploaded.

Usage:
    python3 scripts/thumbnail_concepts.py SRT --context CONTEXT.md --title "TITLE" \
        [--out-dir DIR] [--name NAME] [--prompt PATH] [--json]
"""

import argparse
import json
import sys
import time
from pathlib import Path

from common.channel import fixed_string, fixed_strings
from common.config import ConfigError, load_config
from common.jsonio import now_iso
from common.llm import LLMError, make_client
from common.schema import SchemaError, validate_or_raise
from common.srt import format_clock, format_transcript, parse_srt
from common.text import read_text
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME
from common.errors import report_error

TOOL = "scripts/thumbnail_concepts.py"
SCHEMA_VERSION = "0.1"
DEFAULT_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "thumbnail_concepts.md"

_client = None


class ThumbnailConceptsError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


class ThumbnailConceptsRuntimeError(ThumbnailConceptsError):
    """Raised when the model answer cannot be used after the call."""


def get_client():
    global _client
    if _client is None:
        _client = make_client()
    return _client


def build_message(prompt_text, context_text, title, transcript_text, channel_formula=None):
    parts = [prompt_text, "## Context", context_text, "## Title", title]
    if channel_formula is not None:
        parts.extend(["## Channel", channel_formula])
    parts.extend(["## Transcript", transcript_text])
    return "\n\n".join(parts)


def normalize_timestamp(value):
    parts = value.split(":")
    if len(parts) == 2:
        hours, minutes, seconds = "0", parts[0], parts[1]
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        return None
    if not (hours.isdigit() and minutes.isdigit() and seconds.isdigit()):
        return None
    if int(minutes) >= 60 or int(seconds) >= 60:
        return None
    return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"


def timestamp_seconds(normalized):
    hours, minutes, seconds = normalized.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)


def validate_model_json(data):
    """Validate the model contract and return the raw concept list."""
    if not isinstance(data, dict):
        raise ThumbnailConceptsRuntimeError("model response is not a JSON object")
    concepts = data.get("concepts")
    if not isinstance(concepts, list) or not 2 <= len(concepts) <= 3:
        raise ThumbnailConceptsRuntimeError(
            "model JSON key 'concepts' must be a list of 2 or 3 concepts"
        )
    for index, concept in enumerate(concepts):
        validate_concept(concept, index)
    return concepts


def validate_concept(concept, index):
    """Validate one concept shape (its timecode is checked later, against the transcript)."""
    label = f"model JSON key 'concepts[{index}]'"
    if not isinstance(concept, dict):
        raise ThumbnailConceptsRuntimeError(f"{label} must be an object")
    for field in ("name", "pose", "emotion"):
        if not isinstance(concept.get(field), str) or not concept[field].strip():
            raise ThumbnailConceptsRuntimeError(f"{label}.{field} must be a non-empty string")
    text = concept.get("text")
    if not isinstance(text, dict):
        raise ThumbnailConceptsRuntimeError(f"{label}.text must be an object with 'title', 'keyword' and 'kicker'")
    for field in ("title", "keyword", "kicker"):
        if not isinstance(text.get(field), str):
            raise ThumbnailConceptsRuntimeError(f"{label}.text.{field} must be a string")
    if not text["title"].strip() or not text["keyword"].strip():
        raise ThumbnailConceptsRuntimeError(f"{label}.text needs a non-empty title and keyword")
    if concept.get("theme") not in ("tech", "creator"):
        raise ThumbnailConceptsRuntimeError(f"{label}.theme must be 'tech' or 'creator'")
    source = concept.get("source")
    if not isinstance(source, dict):
        raise ThumbnailConceptsRuntimeError(f"{label}.source must be an object with 'kind'")
    kind = source.get("kind")
    if kind == "clip_frame":
        if not isinstance(source.get("timecode"), str):
            raise ThumbnailConceptsRuntimeError(
                f"{label}.source.timecode must be a string for kind 'clip_frame'"
            )
    elif kind != "new_photo":
        raise ThumbnailConceptsRuntimeError(f"{label}.source.kind must be 'clip_frame' or 'new_photo'")


def validate_concepts(concepts, srt_end):
    """Normalize clip-frame timecodes and check they lie inside the transcript; name the offending concept."""
    normalized = []
    for index, concept in enumerate(concepts):
        copy = dict(concept)
        source = dict(concept["source"])
        if source["kind"] == "clip_frame":
            raw = source["timecode"]
            stamp = normalize_timestamp(raw)
            if stamp is None:
                raise ThumbnailConceptsRuntimeError(
                    f"concept {index + 1} ('{concept['name']}') has an invalid timecode "
                    f"('{raw}'); use HH:MM:SS or MM:SS"
                )
            if timestamp_seconds(stamp) > srt_end:
                raise ThumbnailConceptsRuntimeError(
                    f"concept {index + 1} ('{concept['name']}') timecode is beyond the "
                    f"transcript end ({format_clock(srt_end)})"
                )
            source["timecode"] = stamp
        copy["source"] = source
        normalized.append(copy)
    return normalized


def build_markdown(name, title, concepts):
    lines = [
        f"# Concepte de thumbnail — {name}",
        "",
        f"Titlu confirmat: {title}",
        "",
    ]
    for index, concept in enumerate(concepts, 1):
        source = concept["source"]
        if source["kind"] == "clip_frame":
            source_line = f"cadru din clip {source['timecode']}"
        else:
            source_line = "poză nouă"
        text = concept["text"]
        lines.extend([
            f"## {index}. {concept['name']}",
            "",
            f"- Nume: {concept['name']}",
            f"- Poză: {concept['pose']}",
            f"- Emoție: {concept['emotion']}",
            f"- Text: titlu \"{text['title']}\" · cuvânt cheie \"{text['keyword']}\" · kicker \"{text['kicker']}\"",
            f"- Temă: {concept['theme']}",
            f"- Sursă: {source_line}",
            "",
            f"thumbnail.py --concepts thumbnail_concepts.json --pick {index} --photo <source> --out <dir>",
            "",
        ])
    return "\n".join(lines)


def run(srt_path, context_path, title, out_dir=None, name=None, prompt_path=None):
    """Build the concepts and return the ``--json`` summary; raise ``ThumbnailConceptsError`` on bad input."""
    started = time.monotonic()

    srt_path = Path(srt_path).expanduser()
    context_path = Path(context_path).expanduser()
    srt_text = read_text(srt_path, "SRT", ThumbnailConceptsError)
    context_text = read_text(context_path, "context", ThumbnailConceptsError)

    if not isinstance(title, str) or not title.strip():
        raise ThumbnailConceptsError("--title must be a non-empty string")

    config = load_config()
    fixed = fixed_strings(config)
    channel_formula = fixed_string(fixed, "thumbnail_formula", "ro")
    if channel_formula is None:
        channel_formula = fixed_string(fixed, "thumbnail_formula", "en")

    prompt_file = Path(prompt_path).expanduser() if prompt_path else DEFAULT_PROMPT
    prompt_text = read_text(prompt_file, "prompt", ThumbnailConceptsError)

    cues = parse_srt(srt_text)
    srt_end = max((cue["end"] for cue in cues), default=0.0)
    transcript_text = format_transcript(cues)

    message = build_message(prompt_text, context_text, title, transcript_text, channel_formula)
    answer = get_client().complete_json(
        "metadata",
        [{"role": "user", "content": message}],
        temperature=0.3,
    )

    concepts = validate_model_json(answer.data)
    concepts = validate_concepts(concepts, srt_end)

    output_name = name or srt_path.stem
    out_path = Path(out_dir).expanduser() if out_dir else srt_path.parent
    out_path.mkdir(parents=True, exist_ok=True)

    document = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "tool": TOOL,
        "title": title,
        "concepts": concepts,
    }
    try:
        validate_or_raise(document, "thumbnail_concepts")
    except SchemaError as exc:
        raise ThumbnailConceptsError(f"thumbnail concepts do not match the schema: {exc}")

    json_file = out_path / "thumbnail_concepts.json"
    json_file.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    md_file = out_path / "thumbnail_concepts.md"
    md_file.write_text(build_markdown(output_name, title, concepts), encoding="utf-8")

    return {
        "outputs": {"json": str(json_file), "markdown": str(md_file)},
        "title": title,
        "concepts": len(concepts),
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": get_client().spent_usd,
    }


def build_parser():
    parser = argparse.ArgumentParser(
        prog="thumbnail_concepts.py",
        description=(
            "Write 2-3 Romanian thumbnail concepts from a confirmed title, a context "
            "note and a corrected Romanian .srt; a human reviews them and nothing is uploaded."
        ),
        epilog=(
            "example:\n"
            "  python3 scripts/thumbnail_concepts.py clip_RO.srt --context context.md "
            "--title \"AI-UL MEU\" --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("srt", help="corrected Romanian .srt (timeline time)")
    parser.add_argument("--context", required=True, help="markdown note with the clip's angle")
    parser.add_argument("--title", required=True, help="the confirmed title the concepts start from")
    parser.add_argument("--out-dir", default=None, help="output folder (default: the SRT's parent)")
    parser.add_argument("--name", default=None, help="draft name (default: the SRT stem)")
    parser.add_argument("--prompt", default=None, help="alternative prompt file")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = run(
            args.srt,
            args.context,
            args.title,
            out_dir=args.out_dir,
            name=args.name,
            prompt_path=args.prompt,
        )
    except (ThumbnailConceptsRuntimeError, LLMError) as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except (ThumbnailConceptsError, ConfigError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)

    if args.json:
        print(json.dumps({"ok": True, **summary}))
    else:
        print(f"written: {summary['outputs']['json']} ({summary['concepts']} concepts)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
