#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turn the kept transcript of an edit into topic chapters with one LLM pass.

Usage:
    python3 scripts/chapters.py edit.json WORDS [--out chapters.json] [--json]
    python3 scripts/chapters.py --fcproj PROJ --sequence NAME --words-dir DIR [--json]
    python3 scripts/chapters.py --timeline-words FILE [--json]

The tool maps the per-source Whisper word JSON files onto the cuts of an
``edit.json`` timeline (the same midpoint rule ``subs_from_edit.py`` uses),
builds the kept transcript in timeline time and asks the ``metadata`` model
role for topic chapters. The same mapping is available for a final FilmCraft
project (``--fcproj``) or for the timeline ``_words.json`` that
``subs_from_edit.py`` already writes (``--timeline-words``). The chapter rules
of ``prompts/metadata.md`` apply: the first chapter starts at 0, starts never
decrease, titles are at most 40 characters and mark real topic changes. The
result is written to ``chapters.json`` (validated against
``schemas/chapters.schema.json``) and is the chapter source of truth for the
publish kit and the FilmCraft export.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from common.config import ConfigError
from common.jsonio import now_iso
from common.llm import LLMError, make_client
from common.schema import SchemaError, validate_or_raise
from common.timeline_words import (
    TimelineWordsError,
    build_groups,
    build_words_map,
    collect_words,
    format_timeline,
    map_words,
    plan_from_fcproj,
    publish_folder_for,
    timeline_words_from_file,
)
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME
from common.errors import report_error

TOOL = "scripts/chapters.py"
SCHEMA_VERSION = "0.1"
DEFAULT_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "chapters.md"
MAX_TITLE = 40
MAX_SUMMARY = 200

_client = None


class ChaptersError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


class ChaptersRuntimeError(ChaptersError):
    """Raised when the model answer cannot be used after the call."""

def get_client():
    global _client
    if _client is None:
        _client = make_client()
    return _client


def load_json(path):
    try:
        return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ChaptersError(f"cannot read '{path}': file not found")
    except json.JSONDecodeError as exc:
        raise ChaptersError(f"cannot read '{path}': not valid JSON ({exc})")
    except OSError as exc:
        raise ChaptersError(f"cannot read '{path}': {exc}")


def load_edit(path):
    document = load_json(path)
    try:
        validate_or_raise(document, "edit")
    except SchemaError as exc:
        raise ChaptersError(f"invalid edit '{path}': {exc}")
    return document


def resolve_inputs(args):
    """Resolve the three input modes into ``(mapped, warnings, primary_input)``."""
    has_positionals = args.edit is not None or args.words is not None
    has_fcproj = args.fcproj is not None
    has_timeline = args.timeline_words is not None
    if has_positionals and (has_fcproj or has_timeline):
        raise ChaptersError(
            "positional EDIT/WORDS cannot be combined with --fcproj or --timeline-words"
        )
    if has_fcproj and has_timeline:
        raise ChaptersError("--fcproj and --timeline-words are mutually exclusive")
    if not has_fcproj and (args.sequence or args.words_dir or args.track is not None):
        raise ChaptersError("--sequence, --words-dir and --track require --fcproj")
    if has_fcproj:
        if not args.sequence:
            raise ChaptersError("--fcproj requires --sequence")
        if not args.words_dir:
            raise ChaptersError("--fcproj requires --words-dir")
        plan = plan_from_fcproj(args.fcproj, args.sequence, args.track or "Audio 1")
        mapped, _dropped = collect_words(plan, args.words_dir)
        return mapped, [], Path(args.fcproj)
    if has_timeline:
        return timeline_words_from_file(args.timeline_words), [], Path(args.timeline_words)
    if not has_positionals:
        raise ChaptersError(
            "no input given: pass EDIT WORDS, --fcproj PROJ --sequence NAME --words-dir DIR "
            "or --timeline-words FILE"
        )
    if args.edit is None or args.words is None:
        raise ChaptersError("both EDIT and WORDS are required")
    document = load_edit(args.edit)
    groups = build_groups(document)
    words_map, single_file = build_words_map(args.words)
    mapped, warnings, _dropped = map_words(groups, words_map, single_file)
    return mapped, warnings, Path(args.edit)


def parse_model_json(text):
    raw = text.strip()
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ChaptersRuntimeError(f"model response is not valid JSON: {exc}")


def parse_chapters(text):
    """Parse the model output into a list of chapter objects of the expected shape."""
    data = parse_model_json(text)
    if not isinstance(data, dict):
        raise ChaptersRuntimeError("model response is not a JSON object")
    chapters = data.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise ChaptersRuntimeError("model JSON key 'chapters' must be a non-empty list")
    for index, chapter in enumerate(chapters):
        if not isinstance(chapter, dict):
            raise ChaptersRuntimeError(f"model JSON key 'chapters[{index}]' must be an object")
        start = chapter.get("start_s")
        if isinstance(start, bool) or not isinstance(start, (int, float)):
            raise ChaptersRuntimeError(f"model JSON key 'chapters[{index}].start_s' must be a number")
        if not isinstance(chapter.get("title"), str):
            raise ChaptersRuntimeError(f"model JSON key 'chapters[{index}].title' must be a string")
        if not isinstance(chapter.get("summary"), str):
            raise ChaptersRuntimeError(f"model JSON key 'chapters[{index}].summary' must be a string")
    return chapters


def request_chapters(client, message):
    """Ask the model once, retry once on unparseable output, then give up."""
    last_error = None
    for _ in range(2):
        response = client.complete(
            "metadata", [{"role": "user", "content": message}], temperature=0.3
        )
        try:
            return parse_chapters(response.text)
        except ChaptersError as exc:
            last_error = exc
    raise ChaptersRuntimeError(f"model output is not parseable after one retry: {last_error}")


def validate_chapters(chapters, transcript_end):
    """Snap the first chapter to 0 and check order, range and text lengths."""
    normalized = []
    previous = None
    for index, chapter in enumerate(chapters):
        start = round(float(chapter["start_s"]), 3)
        if index == 0:
            start = 0.0
        title = chapter["title"].strip()
        summary = chapter["summary"].strip()
        if not 1 <= len(title) <= MAX_TITLE:
            raise ChaptersError(
                f"chapter {index + 1} title must be 1-{MAX_TITLE} characters after strip "
                f"(got {len(title)})"
            )
        if not 1 <= len(summary) <= MAX_SUMMARY:
            raise ChaptersError(
                f"chapter {index + 1} summary must be 1-{MAX_SUMMARY} characters after strip "
                f"(got {len(summary)})"
            )
        if start < 0 or start > transcript_end:
            raise ChaptersError(
                f"chapter {index + 1} start_s {start} is outside [0, {transcript_end}]"
            )
        if previous is not None and start < previous:
            raise ChaptersError(
                f"chapter {index + 1} start_s {start} is before the previous chapter ({previous})"
            )
        normalized.append({"start_s": start, "title": title, "summary": summary})
        previous = start
    return normalized


def build_message(prompt_text, transcript_text):
    return "\n\n".join([prompt_text, "## Transcript", transcript_text])


def write_json(path, document):
    """Write the document atomically, leaving no temporary file behind."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def run(mapped, warnings, primary_input, out_path=None, prompt_path=None, publish_dir=None):
    """Build the chapters and return the ``--json`` summary; raise ``ChaptersError`` on bad input."""
    started = time.monotonic()
    if not mapped:
        raise ChaptersError("no words mapped: the kept transcript is empty")
    transcript_end = max(item["end"] for item in mapped)

    prompt_file = Path(prompt_path).expanduser() if prompt_path else DEFAULT_PROMPT
    try:
        prompt_text = prompt_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ChaptersError(f"prompt file not found: {prompt_file}")
    except OSError as exc:
        raise ChaptersError(f"cannot read prompt file '{prompt_file}': {exc}")

    message = build_message(prompt_text, format_timeline(mapped))
    client = get_client()
    chapters = request_chapters(client, message)
    normalized = validate_chapters(chapters, transcript_end)

    if out_path:
        out = Path(out_path).expanduser()
    elif publish_dir is not None:
        out = Path(publish_dir).expanduser() / "chapters.json"
    else:
        out = Path(primary_input).expanduser().parent / "chapters.json"
    output = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "tool": TOOL,
        "chapters": normalized,
    }
    try:
        validate_or_raise(output, "chapters")
    except SchemaError as exc:
        raise ChaptersError(f"chapters do not match the schema: {exc}")
    write_json(out, output)

    return {
        "outputs": {"chapters": str(out)},
        "chapters": len(normalized),
        "words_mapped": len(mapped),
        "warnings": warnings,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": round(client.spent_usd, 8),
    }


def build_parser():
    parser = argparse.ArgumentParser(
        prog="chapters.py",
        usage=(
            "chapters.py [EDIT] [WORDS] [--out chapters.json] [--prompt FILE]\n"
            "            [--fcproj PROJ --sequence NAME [--track TRACK] --words-dir DIR]\n"
            "            [--timeline-words FILE] [--json]"
        ),
        description=(
            "Turn the kept transcript of an edit into topic chapters with one LLM pass "
            "and write chapters.json."
        ),
        epilog=(
            "input modes:\n"
            "  rough cut:      chapters.py edit.json words/ --json\n"
            "  final project:  chapters.py --fcproj project.fcproj --sequence Main "
            "--words-dir words/ --json\n"
            "  timeline words: chapters.py --timeline-words edit_words.json --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("edit", nargs="?", help="edit.json edit list (rough-cut mode)")
    parser.add_argument("words", nargs="?", help="a Whisper word JSON or a folder of them (rough-cut mode)")
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "the chapters.json to write (default: the clip's publish folder — the parent of "
            "--words-dir — in --fcproj mode, else next to the primary input)"
        ),
    )
    parser.add_argument("--prompt", default=None, help="alternative prompt file")
    parser.add_argument("--fcproj", default=None, help="FilmCraft project file (final-project mode)")
    parser.add_argument("--sequence", default=None, help="name of the sequence item to use (with --fcproj)")
    parser.add_argument("--track", default=None, help="audio track name inside the sequence (default 'Audio 1')")
    parser.add_argument("--words-dir", default=None, help="folder with one Whisper word JSON per source clip (with --fcproj)")
    parser.add_argument("--timeline-words", default=None, help="a timeline _words.json written by subs_from_edit.py (timeline-words mode)")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        mapped, warnings, primary_input = resolve_inputs(args)
        publish_dir = (
            publish_folder_for(args.words_dir)
            if args.fcproj and args.words_dir
            else None
        )
        summary = run(mapped, warnings, primary_input, args.out, args.prompt, publish_dir)
    except (ChaptersRuntimeError, LLMError) as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except (ChaptersError, ConfigError, TimelineWordsError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)

    if args.json:
        print(json.dumps({"ok": True, **summary}))
    else:
        print(
            f"written: {summary['outputs']['chapters']} "
            f"({summary['chapters']} chapters, {summary['words_mapped']} words)"
        )
        for warning in summary["warnings"]:
            print(f"warning: {warning}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
