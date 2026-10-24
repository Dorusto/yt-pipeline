#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build a metadata.md publish draft from a corrected Romanian .srt and a context note.

The tool sends the prompt in ``prompts/metadata.md`` plus the context, the
transcript and the links to the ``metadata`` model role, then assembles the
description deterministically: the channel's fixed wording (``channel.fixed``)
is inserted verbatim and never taken from the model. A human reviews the draft;
nothing is uploaded. It also writes ``metadata.json`` next to ``metadata.md``
with the validated fields, so the publish kit is deterministic from the model
JSON. With ``--chapters`` the chapters come from a ``chapters.json`` (the
chapter source of truth) instead of the model answer.

Usage:
    python3 metadata.py SRT --context CONTEXT.md [--title TITLE]
        [--chapters chapters.json] [--previous-url URL] [--repo-url URL]
        [--out-dir DIR] [--name NAME] [--prompt PATH] [--json]
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from common.channel import fixed_string, fixed_strings
from common.config import ConfigError, load_config
from common.llm import LLMError, make_client
from common.schema import SchemaError, validate_or_raise
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME
from common.errors import report_error

DEFAULT_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "metadata.md"

_client = None


class MetadataError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


class MetadataRuntimeError(MetadataError):
    """Raised when the model answer cannot be used after the call."""

def get_client():
    global _client
    if _client is None:
        _client = make_client()
    return _client


def read_text(path, label):
    """Read a UTF-8 text file, raising a named error when it is missing or unreadable."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise MetadataError(f"{label} file not found: {path}")
    except OSError as exc:
        raise MetadataError(f"cannot read {label} file '{path}': {exc}")


def parse_srt_time(value):
    value = value.replace(",", ".")
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_srt(text):
    """Return ``[{start, end, text}]`` in seconds for every cue of an SRT body."""
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        match = re.match(
            r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})",
            lines[1],
        )
        if not match:
            continue
        cues.append({
            "start": parse_srt_time(match.group(1)),
            "end": parse_srt_time(match.group(2)),
            "text": " ".join(line.strip() for line in lines[2:]).strip(),
        })
    return cues


def format_clock(seconds):
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_transcript(cues):
    return "\n".join(f"[{format_clock(cue['start'])}] {cue['text']}" for cue in cues)


def build_message(
    prompt_text, context_text, transcript_text, channel_url, previous_url, repo_url, chosen_title=None
):
    parts = [prompt_text, "## Context", context_text]
    if chosen_title is not None:
        parts.extend(["## Chosen title", chosen_title])
    parts.extend(["## Transcript", transcript_text])
    links = [f"channel_url: {channel_url}"]
    if previous_url:
        links.append(f"previous_url: {previous_url}")
    if repo_url:
        links.append(f"repo_url: {repo_url}")
    parts.append("## Links")
    parts.extend(links)
    return "\n\n".join(parts)


def require_key(data, key):
    if key not in data:
        raise MetadataRuntimeError(f"model JSON is missing key '{key}'")
    return data[key]


def require_localized(value, key):
    if not isinstance(value, dict):
        raise MetadataRuntimeError(f"model JSON key '{key}' must be an object with 'ro' and 'en'")
    for lang in ("ro", "en"):
        if not isinstance(value.get(lang), str):
            raise MetadataRuntimeError(f"model JSON key '{key}.{lang}' must be a string")
    return value


def optional_localized(value, key):
    if value is None:
        return None
    return require_localized(value, key)


def validate_model_json(data, require_chapters=True):
    """Validate the model contract and return the normalized fields.

    When ``require_chapters`` is False the ``chapters`` key is optional and its
    value is ignored (the caller supplies the chapters from a file).
    """
    if not isinstance(data, dict):
        raise MetadataRuntimeError("model response is not a JSON object")

    titles = require_key(data, "titles")
    if not isinstance(titles, list) or len(titles) != 3 or not all(isinstance(t, str) for t in titles):
        raise MetadataRuntimeError("model JSON key 'titles' must be a list of exactly 3 strings")

    intro = require_localized(require_key(data, "intro"), "intro")
    github_line = optional_localized(require_key(data, "github_line"), "github_line")
    previous_line = optional_localized(require_key(data, "previous_line"), "previous_line")
    subscribe_line = require_localized(require_key(data, "subscribe_line"), "subscribe_line")

    promo_lines = require_key(data, "promo_lines")
    if not isinstance(promo_lines, list):
        raise MetadataRuntimeError("model JSON key 'promo_lines' must be a list")
    for index, promo in enumerate(promo_lines):
        if not isinstance(promo, dict):
            raise MetadataRuntimeError(f"model JSON key 'promo_lines[{index}]' must be an object")
        for field in ("ro", "en", "label"):
            if not isinstance(promo.get(field), str):
                raise MetadataRuntimeError(f"model JSON key 'promo_lines[{index}].{field}' must be a string")

    chapters = None
    if require_chapters:
        chapters = require_key(data, "chapters")
        if not isinstance(chapters, list):
            raise MetadataRuntimeError("model JSON key 'chapters' must be a list")
        for index, chapter in enumerate(chapters):
            if not isinstance(chapter, dict):
                raise MetadataRuntimeError(f"model JSON key 'chapters[{index}]' must be an object")
            if not isinstance(chapter.get("timestamp"), str):
                raise MetadataRuntimeError(f"model JSON key 'chapters[{index}].timestamp' must be a string")
            if not isinstance(chapter.get("title"), str):
                raise MetadataRuntimeError(f"model JSON key 'chapters[{index}].title' must be a string")
            if "title_en" in chapter and not isinstance(chapter["title_en"], str):
                raise MetadataRuntimeError(
                    f"model JSON key 'chapters[{index}].title_en' must be a string"
                )

    tags = require_key(data, "tags")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise MetadataRuntimeError("model JSON key 'tags' must be a list of strings")

    pinned_comment = require_key(data, "pinned_comment")
    if not isinstance(pinned_comment, str):
        raise MetadataRuntimeError("model JSON key 'pinned_comment' must be a string")

    return {
        "titles": titles,
        "intro": intro,
        "github_line": github_line,
        "previous_line": previous_line,
        "subscribe_line": subscribe_line,
        "promo_lines": promo_lines,
        "chapters": chapters,
        "tags": tags,
        "pinned_comment": pinned_comment,
    }


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


def validate_chapters(chapters, srt_end):
    """Normalize chapter timestamps and check order and range; name the offending chapter."""
    normalized = []
    previous = -1
    for index, chapter in enumerate(chapters):
        raw = chapter["timestamp"]
        stamp = normalize_timestamp(raw)
        if stamp is None:
            raise MetadataError(
                f"chapter {index + 1} ('{raw}') has an invalid timestamp; use HH:MM:SS or MM:SS"
            )
        seconds = timestamp_seconds(stamp)
        if seconds > srt_end:
            raise MetadataError(
                f"chapter {index + 1} ('{raw}') is beyond the transcript end ({format_clock(srt_end)})"
            )
        if seconds < previous:
            raise MetadataError(f"chapter {index + 1} ('{raw}') is before the previous chapter")
        if index == 0 and seconds != 0:
            raise MetadataError(f"chapter 1 ('{raw}') must start at 00:00:00")
        normalized_chapter = {"timestamp": stamp, "title": chapter["title"]}
        if "title_en" in chapter:
            normalized_chapter["title_en"] = chapter["title_en"]
        normalized.append(normalized_chapter)
        previous = seconds
    return normalized


def load_chapters_file(path):
    """Read a ``chapters.json``, validate it against the schema and return model-shaped chapters."""
    resolved = Path(path).expanduser()
    try:
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise MetadataError(f"chapters file not found: {resolved}")
    except OSError as exc:
        raise MetadataError(f"cannot read chapters file '{resolved}': {exc}")
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MetadataError(f"chapters file '{resolved}' is not valid JSON: {exc}")
    try:
        validate_or_raise(document, "chapters")
    except SchemaError as exc:
        raise MetadataError(f"chapters file '{resolved}' is not valid chapters.json: {exc}")
    return [
        {"timestamp": format_clock(chapter["start_s"]), "title": chapter["title"]}
        for chapter in document["chapters"]
    ]


def fill_members(fixed, members):
    """Return a copy of ``fixed`` with ``{members}`` filled in every members_sentence string."""
    filled = dict(fixed)
    sentence = fixed.get("members_sentence")
    if isinstance(sentence, dict):
        filled["members_sentence"] = {
            lang: (text.replace("{members}", members) if isinstance(text, str) else text)
            for lang, text in sentence.items()
        }
    return filled


def build_description(fields, lang, fixed, previous_url):
    paragraphs = [fields["intro"][lang]]
    if fields["github_line"] is not None:
        paragraphs.append(fields["github_line"][lang])
    members_sentence = fixed_string(fixed, "members_sentence", lang)
    if members_sentence is not None:
        paragraphs.append(members_sentence)
    if previous_url and fields["previous_line"] is not None:
        paragraphs.append(fields["previous_line"][lang])
    paragraphs.append(fields["subscribe_line"][lang])
    for promo in fields["promo_lines"]:
        paragraphs.append(f"[{promo['label']}] {promo[lang]}")
    closing = fixed_string(fixed, "closing", lang)
    if closing is not None:
        paragraphs.append(closing)
    return "\n\n".join(paragraphs)


def build_markdown(name, fields, chapters, ro_description, en_description):
    lines = [
        f"# Metadata draft — {name}",
        "",
        "## Titles",
    ]
    for index, title in enumerate(fields["titles"], 1):
        lines.append(f"{index}. {title}")
    lines.extend([
        "## Description RO",
        "",
        ro_description,
        "",
        "## Description EN",
        "",
        en_description,
        "",
        "## Chapters",
    ])
    for chapter in chapters:
        lines.append(f"{chapter['timestamp']} {chapter['title']}")
    if any("title_en" in chapter for chapter in chapters):
        lines.extend(["", "## Chapters (EN)"])
        for chapter in chapters:
            lines.append(f"{chapter['timestamp']} {chapter.get('title_en') or chapter['title']}")
    lines.extend([
        "",
        "## Tags",
        ", ".join(fields["tags"]),
        "",
        "## Pinned comment",
        fields["pinned_comment"],
        "",
    ])
    return "\n".join(lines)


def run(
    srt_path,
    context_path,
    previous_url=None,
    repo_url=None,
    out_dir=None,
    name=None,
    prompt_path=None,
    chapters_path=None,
    title=None,
):
    """Build the draft and return the ``--json`` summary; raise ``MetadataError`` on bad input."""
    started = time.monotonic()

    srt_path = Path(srt_path).expanduser()
    context_path = Path(context_path).expanduser()
    srt_text = read_text(srt_path, "SRT")
    context_text = read_text(context_path, "context")

    if title is not None and not title.strip():
        raise MetadataError("--title must be a non-empty string")

    config = load_config()
    members = (config.get("channel") or {}).get("members")
    if not isinstance(members, str) or not members.strip():
        raise MetadataError("config is missing channel.members (a non-empty string)")
    channel_url = (config.get("channel") or {}).get("github_url")
    if not isinstance(channel_url, str) or not channel_url.strip():
        raise MetadataError("config is missing channel.github_url (a non-empty string)")
    fixed = fill_members(fixed_strings(config), members)

    prompt_file = Path(prompt_path).expanduser() if prompt_path else DEFAULT_PROMPT
    prompt_text = read_text(prompt_file, "prompt")

    cues = parse_srt(srt_text)
    srt_end = max((cue["end"] for cue in cues), default=0.0)
    transcript_text = format_transcript(cues)

    file_chapters = load_chapters_file(chapters_path) if chapters_path else None

    message = build_message(
        prompt_text, context_text, transcript_text, channel_url, previous_url, repo_url, title
    )
    answer = get_client().complete_json(
        "metadata",
        [{"role": "user", "content": message}],
        temperature=0.3,
    )

    fields = validate_model_json(answer.data, require_chapters=chapters_path is None)
    chapters = validate_chapters(
        file_chapters if file_chapters is not None else fields["chapters"], srt_end
    )

    ro_description = build_description(fields, "ro", fixed, previous_url)
    en_description = build_description(fields, "en", fixed, previous_url)

    output_name = name or srt_path.stem
    out_path = Path(out_dir).expanduser() if out_dir else srt_path.parent
    out_path.mkdir(parents=True, exist_ok=True)
    output_file = out_path / "metadata.md"
    output_file.write_text(
        build_markdown(output_name, fields, chapters, ro_description, en_description),
        encoding="utf-8",
    )
    json_file = out_path / "metadata.json"
    json_file.write_text(
        json.dumps(
            {
                "name": output_name,
                "titles": fields["titles"],
                "description_ro": ro_description,
                "description_en": en_description,
                "chapters": chapters,
                "tags": fields["tags"],
                "pinned_comment": fields["pinned_comment"],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    return {
        "output": str(output_file),
        "titles": len(fields["titles"]),
        "chapters": len(chapters),
        "tags": len(fields["tags"]),
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": get_client().spent_usd,
    }


def build_parser():
    parser = argparse.ArgumentParser(
        prog="metadata.py",
        description=(
            "Build a metadata.md publish draft from a corrected Romanian .srt and a "
            "context note; a human reviews it and nothing is uploaded."
        ),
        epilog=(
            "example:\n"
            "  python3 scripts/metadata.py clip_RO.srt --context context.md --json\n"
            "  python3 scripts/metadata.py clip_RO.srt --context context.md "
            "--title \"Titlul ales\" --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("srt", help="corrected Romanian .srt (timeline time)")
    parser.add_argument("--context", required=True, help="markdown note with the clip's angle")
    parser.add_argument(
        "--title",
        default=None,
        help="the chosen title the model uses verbatim as the first title",
    )
    parser.add_argument(
        "--chapters",
        default=None,
        help="chapters.json to use instead of the model's chapters (schemas/chapters.schema.json)",
    )
    parser.add_argument("--previous-url", default=None, help="relevant earlier clip to link")
    parser.add_argument("--repo-url", default=None, help="the clip's own code repo")
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
            previous_url=args.previous_url,
            repo_url=args.repo_url,
            out_dir=args.out_dir,
            name=args.name,
            prompt_path=args.prompt,
            chapters_path=args.chapters,
            title=args.title,
        )
    except (MetadataRuntimeError, LLMError) as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except (MetadataError, ConfigError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)

    if args.json:
        print(json.dumps(summary))
    else:
        print(
            f"written: {summary['output']} "
            f"({summary['titles']} titles, {summary['chapters']} chapters, {summary['tags']} tags)"
        )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
