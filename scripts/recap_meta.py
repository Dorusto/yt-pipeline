#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Write ``recap_meta.json`` in a workspace: title, description and hashtags.

Usage:
    python scripts/recap_meta.py --workspace DIR
    python scripts/recap_meta.py --workspace DIR --json
    python scripts/recap_meta.py --workspace DIR --dry-run
    python scripts/recap_meta.py --workspace DIR --out /tmp/recap_meta.json --force

Reads ``edit.json`` (required, validated against ``schemas/edit.schema.json``)
and ``ingest.json`` (optional, for the date), builds the deterministic facts
(the brief text, the four facts from ``brief_facts.json`` — place, when, who,
occasion — winning over the brief syntax and the ingest date, and the shot
descriptions), asks the ``metadata`` model role for one title, a 2-3 sentence
description and 5-10 hashtags, and writes ``recap_meta.json`` validated against
``schemas/recap_meta.schema.json``. The fact fields the brief and
``brief_facts.json`` did not carry are listed by field name in ``missing`` so
the web app can hint at them. Sources are read-only; the output is written
atomically and skipped when it is newer than ``edit.json``, unless ``--force``.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from common.config import ConfigError, load_config
from common.brief_facts import FACT_LABELS, load_brief_facts
from common.jsonio import load_json, now_iso, parse_model_json, write_json
from common.llm import LLMError, make_client
from common.media import earliest_creation_date
from common.schema import SchemaError, validate_or_raise
from common.summary import summary_fields
from common.exit_codes import EXIT_OK, EXIT_USAGE, EXIT_RUNTIME
from common.errors import report_error

TOOL = "scripts/recap_meta.py"
SCHEMA_VERSION = "0.1"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = REPO_ROOT / "prompts" / "recap_meta.md"
SCHEMA_PATH = REPO_ROOT / "schemas" / "recap_meta.schema.json"

_client = None
_limits_cache = None


class RecapMetaError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


class RecapMetaRuntimeError(RecapMetaError):
    """Raised when the model answer cannot be used after the call."""


def get_client(config=None):
    global _client
    if _client is None:
        _client = make_client(config)
    return _client


def load_edit(path):
    if not Path(path).is_file():
        raise RecapMetaError(f"no edit.json in workspace: {path}")
    document = load_json(path)
    if document is None:
        raise RecapMetaError(f"unreadable edit.json: {path}")
    try:
        validate_or_raise(document, "edit")
    except SchemaError as exc:
        raise RecapMetaError(f"invalid edit '{path}': {exc}")
    return document


def read_prompt(prompt_file):
    try:
        return Path(prompt_file).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RecapMetaError(f"prompt file not found: {prompt_file}")
    except OSError as exc:
        raise RecapMetaError(f"cannot read prompt file '{prompt_file}': {exc}")


def schema_limits():
    """The title/description length and hashtag count limits from the schema file."""
    global _limits_cache
    if _limits_cache is None:
        try:
            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RecapMetaError(f"cannot read the recap_meta schema: {exc}")
        properties = schema.get("properties") or {}
        title = properties.get("title") or {}
        description = properties.get("description") or {}
        hashtags = properties.get("hashtags") or {}
        _limits_cache = (
            int(title["maxLength"]),
            int(description["maxLength"]),
            int(hashtags["minItems"]),
            int(hashtags["maxItems"]),
        )
    return _limits_cache


def shot_descriptions(edit):
    """The non-empty slot descriptions in timeline order, deduplicated."""
    seen = set()
    descriptions = []
    for slot in edit.get("video") or []:
        if not isinstance(slot, dict):
            continue
        value = slot.get("description")
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        descriptions.append(text)
    return descriptions


def earliest_date(workspace_path):
    """The date part of the earliest clip or photo creation_time in ingest.json, or None."""
    return earliest_creation_date(workspace_path)


def build_facts(edit, workspace_path):
    """The deterministic facts the message carries and the missing list."""
    facts = load_brief_facts(workspace_path)
    brief_text = (edit.get("brief") or {}).get("text") or ""
    place = facts["place"]
    when = facts["when"] or earliest_date(workspace_path)
    who = facts["who"]
    occasion = facts["occasion"]
    missing = [
        name
        for name, value in (("place", place), ("when", when), ("who", who), ("occasion", occasion))
        if not value
    ]
    return {
        "brief_text": brief_text,
        "place": place,
        "when": when,
        "who": who,
        "occasion": occasion,
        "descriptions": shot_descriptions(edit),
        "missing": missing,
    }


def build_message(prompt_text, facts):
    facts_block = (
        f"{FACT_LABELS['place']}: {facts['place'] or '(unknown)'}\n"
        f"{FACT_LABELS['when']}: {facts['when'] or '(unknown)'}\n"
        f"{FACT_LABELS['who']}: {facts['who'] or '(unknown)'}\n"
        f"{FACT_LABELS['occasion']}: {facts['occasion'] or '(unknown)'}\n"
    )
    if facts["descriptions"]:
        shots = "\n".join(f"- {text}" for text in facts["descriptions"])
    else:
        shots = "- (no shot descriptions)"
    return (
        prompt_text
        + "\n\n## Brief\n"
        + facts["brief_text"]
        + "\n\n## Facts\n"
        + facts_block
        + "\n## Shots\n"
        + shots
    )


def normalize_answer(text):
    """Parse and normalize the model answer; raise on any invalid shape or range."""
    max_title, max_description, min_hashtags, max_hashtags = schema_limits()
    try:
        data = parse_model_json(text)
    except ValueError as exc:
        raise RecapMetaRuntimeError(f"model response is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise RecapMetaRuntimeError("model response is not a JSON object")
    title = data.get("title")
    if not isinstance(title, str):
        raise RecapMetaRuntimeError("model JSON key 'title' must be a string")
    title = title.strip()
    if not 1 <= len(title) <= max_title:
        raise RecapMetaRuntimeError(
            f"title must be 1-{max_title} characters after strip (got {len(title)})"
        )
    description = data.get("description")
    if not isinstance(description, str):
        raise RecapMetaRuntimeError("model JSON key 'description' must be a string")
    description = description.strip()
    if not 1 <= len(description) <= max_description:
        raise RecapMetaRuntimeError(
            f"description must be 1-{max_description} characters after strip "
            f"(got {len(description)})"
        )
    sentences = [part for part in re.split(r"[.!?]+", description) if part.strip()]
    if not 2 <= len(sentences) <= 3:
        raise RecapMetaRuntimeError(
            f"description must be 2-3 sentences (got {len(sentences)})"
        )
    hashtags = data.get("hashtags")
    if not isinstance(hashtags, list):
        raise RecapMetaRuntimeError("model JSON key 'hashtags' must be a list")
    normalized = []
    seen = set()
    for item in hashtags:
        if not isinstance(item, str):
            raise RecapMetaRuntimeError("every hashtag must be a string")
        tag = item.strip().lstrip("#").strip()
        if not tag:
            continue
        if any(character.isspace() for character in tag):
            raise RecapMetaRuntimeError(f"hashtag must not contain spaces: {item!r}")
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append("#" + tag)
    if not min_hashtags <= len(normalized) <= max_hashtags:
        raise RecapMetaRuntimeError(
            f"hashtags must be {min_hashtags}-{max_hashtags} after normalisation "
            f"(got {len(normalized)})"
        )
    return {"title": title, "description": description, "hashtags": normalized}


def request_meta(client, message):
    """Ask the model once, retry once on an invalid answer, then give up."""
    last_error = None
    run_cost = 0.0
    for _ in range(2):
        response = client.complete(
            "metadata", [{"role": "user", "content": message}], temperature=0.4
        )
        run_cost += float(response.cost_usd)
        try:
            return normalize_answer(response.text), response, run_cost
        except RecapMetaRuntimeError as exc:
            last_error = exc
    raise RecapMetaRuntimeError(
        f"model output is not usable after one retry: {last_error}"
    )


def output_is_up_to_date(out_path, edit_path):
    if not Path(out_path).is_file() or not Path(edit_path).is_file():
        return False
    try:
        return Path(out_path).stat().st_mtime > Path(edit_path).stat().st_mtime
    except OSError:
        return False


def effective_config(budget_usd):
    config = load_config()
    if budget_usd is not None:
        config = dict(config)
        config["llm_budget_usd"] = budget_usd
    return config


def _skipped_summary(out_path, missing, started):
    summary = {
        "ok": True,
        "status": "skipped",
        "out": str(out_path),
        "missing": missing,
    }
    existing = load_json(out_path)
    if isinstance(existing, dict):
        if isinstance(existing.get("title"), str):
            summary["title"] = existing["title"]
        if isinstance(existing.get("hashtags"), list):
            summary["hashtags"] = len(existing["hashtags"])
        if isinstance(existing.get("model"), str):
            summary["model"] = existing["model"]
    summary.update(summary_fields(started, 0.0))
    return summary


def run(workspace, out=None, prompt_path=None, dry_run=False, force=False, budget_usd=None):
    """Build the recap metadata and return the ``--json`` summary."""
    started = time.monotonic()
    workspace_path = Path(workspace).expanduser().resolve()
    if not workspace_path.is_dir():
        raise RecapMetaError(f"workspace is not a directory: {workspace_path}")
    edit_path = workspace_path / "edit.json"
    edit = load_edit(edit_path)
    out_path = Path(out).expanduser() if out else workspace_path / "recap_meta.json"
    if not dry_run and not force and output_is_up_to_date(out_path, edit_path):
        facts = build_facts(edit, workspace_path)
        return _skipped_summary(out_path, facts["missing"], started)

    prompt_file = Path(prompt_path).expanduser() if prompt_path else DEFAULT_PROMPT
    prompt_text = read_prompt(prompt_file)
    facts = build_facts(edit, workspace_path)
    message = build_message(prompt_text, facts)

    if dry_run:
        summary = {
            "ok": True,
            "status": "dry_run",
            "out": str(out_path),
            "message": message,
            "missing": facts["missing"],
        }
        summary.update(summary_fields(started, 0.0))
        return summary

    client = get_client(effective_config(budget_usd))
    answer, response, run_cost = request_meta(client, message)
    document = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "tool": TOOL,
        "title": answer["title"],
        "description": answer["description"],
        "hashtags": answer["hashtags"],
        "missing": facts["missing"],
        "model": response.model,
        "llm_cost_usd": round(run_cost, 8),
    }
    try:
        validate_or_raise(document, "recap_meta")
    except SchemaError as exc:
        raise RecapMetaError(f"recap metadata does not match the schema: {exc}")
    write_json(out_path, document)

    summary = {
        "ok": True,
        "status": "written",
        "out": str(out_path),
        "title": answer["title"],
        "hashtags": len(answer["hashtags"]),
        "missing": facts["missing"],
        "model": response.model,
    }
    summary.update(summary_fields(started, run_cost))
    return summary


def build_parser():
    parser = argparse.ArgumentParser(
        prog="recap_meta.py",
        description=(
            "Write recap_meta.json in a workspace: the YouTube title, a 2-3 "
            "sentence description and 5-10 hashtags for a recap, from edit.json "
            "and the deterministic facts the tool builds."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/recap_meta.py --workspace <workspace>\n"
            "  python scripts/recap_meta.py --workspace <workspace> --json\n"
            "  python scripts/recap_meta.py --workspace <workspace> --dry-run\n"
            "  python scripts/recap_meta.py --workspace <workspace> \\\n"
            "      --out /tmp/recap_meta.json --force\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--workspace",
        required=True,
        help="workspace holding edit.json; recap_meta.json is written here by default",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="output path (default WORKSPACE/recap_meta.json)",
    )
    parser.add_argument("--prompt", default=None, help="alternative prompt file")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build the message, print it and write nothing (no model call)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rewrite even when recap_meta.json is newer than edit.json",
    )
    parser.add_argument(
        "--budget-usd",
        type=float,
        default=None,
        help="refuse calls past this spend (default llm_budget_usd)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print one JSON summary on stdout; human output goes to stderr",
    )
    return parser


def print_human(summary):
    if summary["status"] == "dry_run":
        print(summary["message"])
    elif summary["status"] == "skipped":
        print(f"up to date: {summary['out']}")
    else:
        print(f"wrote {summary['out']}: {summary.get('title', '')}")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = run(
            args.workspace,
            out=args.out,
            prompt_path=args.prompt,
            dry_run=args.dry_run,
            force=args.force,
            budget_usd=args.budget_usd,
        )
    except (RecapMetaRuntimeError, LLMError) as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except OSError as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except (RecapMetaError, ConfigError, SchemaError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)

    if args.json:
        print(json.dumps(summary))
    else:
        print_human(summary)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
