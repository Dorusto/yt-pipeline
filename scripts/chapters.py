#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turn the kept transcript of an edit into topic chapters with one LLM pass.

Usage:
    python3 scripts/chapters.py edit.json WORDS [--out chapters.json] [--json]

The tool maps the per-source Whisper word JSON files onto the cuts of an
``edit.json`` timeline (the same midpoint rule ``subs_from_edit.py`` uses),
builds the kept transcript in timeline time and asks the ``metadata`` model
role for topic chapters. The chapter rules of ``prompts/metadata.md`` apply:
the first chapter starts at 0, starts never decrease, titles are at most 40
characters and mark real topic changes. The result is written to
``chapters.json`` (validated against ``schemas/chapters.schema.json``) and is
the chapter source of truth for the publish kit and the FilmCraft export.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from common.config import ConfigError
from common.llm import LLMError, make_client
from common.schema import SchemaError, validate_or_raise

TOOL = "scripts/chapters.py"
SCHEMA_VERSION = "0.1"
DEFAULT_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "chapters.md"
PAUSE_SPLIT_S = 0.6
EPSILON = 1e-9
MAX_TITLE = 40
MAX_SUMMARY = 200

_client = None


class ChaptersError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


def get_client():
    global _client
    if _client is None:
        _client = make_client()
    return _client


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def slot_length(slot):
    if slot.get("kind") == "photo":
        return float(slot.get("duration_s") or 0.0)
    return float(slot["out_s"]) - float(slot["in_s"])


def build_groups(document):
    """Group consecutive video slots sharing one id; each group is one timeline window."""
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


def build_words_map(words_path):
    """Return ``(stem -> path, single_file)`` for a words file or a folder of them."""
    path = Path(words_path).expanduser()
    if path.is_dir():
        mapping = {}
        for candidate in sorted(path.iterdir()):
            if candidate.suffix != ".json" or not candidate.is_file():
                continue
            stem = candidate.stem
            if stem.endswith("_words"):
                mapping[stem[: -len("_words")]] = candidate
            else:
                mapping.setdefault(stem, candidate)
        return mapping, None
    if path.is_file():
        stem = path.stem
        if stem.endswith("_words"):
            stem = stem[: -len("_words")]
        return {stem: path}, path
    raise ChaptersError(f"words path not found: {path}")


def load_source_words(path):
    """Return ``[{word, start, end}]`` for every word of a Whisper word JSON file."""
    data = load_json(path)
    words = []
    for segment in data.get("segments") or []:
        for word in segment.get("words") or []:
            text = str(word.get("word", "")).strip()
            if not text:
                continue
            try:
                start = float(word["start"])
                end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                raise ChaptersError(f"'{path}': a word entry has no numeric start/end")
            words.append({"word": text, "start": start, "end": end})
    return words


def check_linear(slot):
    """Refuse a slot whose word times would not map linearly onto the timeline."""
    speed = float(slot.get("speed", 1.0) or 1.0)
    if abs(speed - 1.0) > EPSILON:
        raise ChaptersError(
            f"slot '{slot.get('id')}' ({slot.get('clip')}): speed {speed:g} is not 1.0, "
            "word times would not map linearly"
        )
    middle = slot.get("middle_speed")
    if middle is not None and abs(float(middle) - 1.0) > EPSILON:
        raise ChaptersError(
            f"slot '{slot.get('id')}' ({slot.get('clip')}): middle_speed {float(middle):g} "
            "is not 1.0, word times would not map linearly"
        )
    if slot.get("reverse", False):
        raise ChaptersError(
            f"slot '{slot.get('id')}' ({slot.get('clip')}): reverse playback is not supported, "
            "word times would not map linearly"
        )


def words_member(group, words_map, single_file, video_group_count):
    """Return ``(member, words_file)`` for the angle whose clip stem matches a words file."""
    for entry in group["slots"]:
        if entry["kind"] != "video":
            continue
        clip = entry["slot"].get("clip")
        if not clip:
            continue
        stem = Path(clip).stem
        if stem in words_map:
            return entry, words_map[stem]
    if single_file is not None and video_group_count == 1:
        return group["slots"][0], single_file
    return None, None


def map_words(groups, words_map, single_file):
    """Map every kept word onto timeline time; return ``(mapped, warnings)``."""
    video_groups = [group for group in groups if group["kind"] == "video"]
    mapped = []
    warnings = []
    for group in groups:
        if group["kind"] != "video":
            continue
        for entry in group["slots"]:
            check_linear(entry["slot"])
        member, words_file = words_member(group, words_map, single_file, len(video_groups))
        if member is None:
            warnings.append(
                f"no words file for slot '{group['slots'][0]['slot'].get('id')}', skipped"
            )
            continue
        in_s = float(member["slot"]["in_s"])
        out_s = float(member["slot"]["out_s"])
        shift = group["start_s"] - in_s
        for word in load_source_words(words_file):
            midpoint = (word["start"] + word["end"]) / 2
            if in_s <= midpoint < out_s:
                mapped.append({
                    "word": word["word"],
                    "start": round(word["start"] + shift, 3),
                    "end": round(word["end"] + shift, 3),
                })
    mapped.sort(key=lambda item: (item["start"], item["end"]))
    return mapped, warnings


def format_stamp(seconds):
    tenths = int(round(max(0.0, float(seconds)) * 10))
    minutes, remainder = divmod(tenths, 600)
    return f"{minutes:02d}:{remainder / 10:04.1f}"


def format_timeline(words):
    """Render the kept transcript as ``[MM:SS.d] words…`` lines split on pauses."""
    lines = []
    current = []
    for word in words:
        if current and word["start"] - current[-1]["end"] > PAUSE_SPLIT_S:
            lines.append(current)
            current = []
        current.append(word)
    if current:
        lines.append(current)
    return "\n".join(
        f"[{format_stamp(line[0]['start'])}] " + " ".join(item["word"] for item in line)
        for line in lines
    )


def parse_model_json(text):
    raw = text.strip()
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ChaptersError(f"model response is not valid JSON: {exc}")


def parse_chapters(text):
    """Parse the model output into a list of chapter objects of the expected shape."""
    data = parse_model_json(text)
    if not isinstance(data, dict):
        raise ChaptersError("model response is not a JSON object")
    chapters = data.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise ChaptersError("model JSON key 'chapters' must be a non-empty list")
    for index, chapter in enumerate(chapters):
        if not isinstance(chapter, dict):
            raise ChaptersError(f"model JSON key 'chapters[{index}]' must be an object")
        start = chapter.get("start_s")
        if isinstance(start, bool) or not isinstance(start, (int, float)):
            raise ChaptersError(f"model JSON key 'chapters[{index}].start_s' must be a number")
        if not isinstance(chapter.get("title"), str):
            raise ChaptersError(f"model JSON key 'chapters[{index}].title' must be a string")
        if not isinstance(chapter.get("summary"), str):
            raise ChaptersError(f"model JSON key 'chapters[{index}].summary' must be a string")
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
    raise ChaptersError(f"model output is not parseable after one retry: {last_error}")


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


def run(edit_path, words_path, out_path=None, prompt_path=None):
    """Build the chapters and return the ``--json`` summary; raise ``ChaptersError`` on bad input."""
    started = time.monotonic()
    document = load_edit(edit_path)
    groups = build_groups(document)
    words_map, single_file = build_words_map(words_path)
    mapped, warnings = map_words(groups, words_map, single_file)
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

    out = (
        Path(out_path).expanduser()
        if out_path
        else Path(edit_path).expanduser().parent / "chapters.json"
    )
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
        usage="chapters.py edit.json WORDS [--out chapters.json] [--json]",
        description=(
            "Turn the kept transcript of an edit into topic chapters with one LLM pass "
            "and write chapters.json."
        ),
        epilog=(
            "examples:\n"
            "  python3 scripts/chapters.py edit.json words/ --json\n"
            "  python3 scripts/chapters.py edit.json words/ --out chapters.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("edit", help="edit.json edit list (validated against schemas/edit.schema.json)")
    parser.add_argument("words", help="a Whisper word JSON or a folder of them (X.json or X_words.json)")
    parser.add_argument("--out", default=None, help="the chapters.json to write (default: next to the edit)")
    parser.add_argument("--prompt", default=None, help="alternative prompt file")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = run(args.edit, args.words, args.out, args.prompt)
    except (ChaptersError, ConfigError, LLMError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        return 2

    if args.json:
        print(json.dumps({"ok": True, **summary}))
    else:
        print(
            f"written: {summary['outputs']['chapters']} "
            f"({summary['chapters']} chapters, {summary['words_mapped']} words)"
        )
        for warning in summary["warnings"]:
            print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
