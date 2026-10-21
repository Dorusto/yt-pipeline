#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rank Shorts candidates from the kept transcript of an edit with one LLM pass.

Usage:
    python3 scripts/shorts_rank.py edit.json WORDS [--chapters chapters.json]
        [--out shorts_candidates.json] [--json]

The tool maps the per-source Whisper word JSON files onto the cuts of an
``edit.json`` timeline (the same midpoint rule ``chapters.py`` uses), splits the
kept transcript into sentences on pauses over 0.6 s and builds deterministic
candidates: runs of consecutive sentences inside one chapter whose span is
20-60 s. One ``director`` model pass scores every candidate on hook and payoff;
the top 5 by ``(hook + payoff) / 2`` are written to ``shorts_candidates.json``
(validated against ``schemas/shorts_candidates.schema.json``). The file is the
input of ``shorts_generator.py --candidates`` and the top 3 become Mango markers
in the FilmCraft export.
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

TOOL = "scripts/shorts_rank.py"
SCHEMA_VERSION = "0.1"
DEFAULT_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "shorts_rank.md"
PAUSE_SPLIT_S = 0.6
EPSILON = 1e-9
MIN_DURATION_S = 20.0
MAX_DURATION_S = 60.0
MAX_CANDIDATES = 60
TOP_N = 5
MAX_REASON = 200

_client = None


class ShortsRankError(Exception):
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
        raise ShortsRankError(f"cannot read '{path}': file not found")
    except json.JSONDecodeError as exc:
        raise ShortsRankError(f"cannot read '{path}': not valid JSON ({exc})")
    except OSError as exc:
        raise ShortsRankError(f"cannot read '{path}': {exc}")


def load_edit(path):
    document = load_json(path)
    try:
        validate_or_raise(document, "edit")
    except SchemaError as exc:
        raise ShortsRankError(f"invalid edit '{path}': {exc}")
    return document


def load_chapters(path):
    """Load and validate a chapters.json; return its chapter list."""
    document = load_json(path)
    try:
        validate_or_raise(document, "chapters")
    except SchemaError as exc:
        raise ShortsRankError(f"invalid chapters '{path}': {exc}")
    return document["chapters"]


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
    raise ShortsRankError(f"words path not found: {path}")


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
                raise ShortsRankError(f"'{path}': a word entry has no numeric start/end")
            words.append({"word": text, "start": start, "end": end})
    return words


def check_linear(slot):
    """Refuse a slot whose word times would not map linearly onto the timeline."""
    speed = float(slot.get("speed", 1.0) or 1.0)
    if abs(speed - 1.0) > EPSILON:
        raise ShortsRankError(
            f"slot '{slot.get('id')}' ({slot.get('clip')}): speed {speed:g} is not 1.0, "
            "word times would not map linearly"
        )
    middle = slot.get("middle_speed")
    if middle is not None and abs(float(middle) - 1.0) > EPSILON:
        raise ShortsRankError(
            f"slot '{slot.get('id')}' ({slot.get('clip')}): middle_speed {float(middle):g} "
            "is not 1.0, word times would not map linearly"
        )
    if slot.get("reverse", False):
        raise ShortsRankError(
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


def split_sentences(words):
    """Split the kept words into sentences on pauses over ``PAUSE_SPLIT_S``."""
    sentences = []
    current = []
    for word in words:
        if current and word["start"] - current[-1]["end"] > PAUSE_SPLIT_S:
            sentences.append(current)
            current = []
        current.append(word)
    if current:
        sentences.append(current)
    return sentences


def chapter_index_for(start_s, chapter_starts):
    """Return the index of the chapter whose start is the last one at or before ``start_s``."""
    index = 0
    for position, chapter_start in enumerate(chapter_starts):
        if chapter_start <= start_s + EPSILON:
            index = position
        else:
            break
    return index


def build_candidates(mapped, chapters):
    """Build the deterministic sentence-aligned 20-60 s candidates inside one chapter."""
    sentences = split_sentences(mapped)
    if not sentences:
        return []
    chapter_starts = [float(chapter["start_s"]) for chapter in chapters]
    sentence_chapter = [
        chapter_index_for(sentence[0]["start"], chapter_starts) for sentence in sentences
    ]
    candidates = []
    for start_index in range(len(sentences)):
        chapter = sentence_chapter[start_index]
        chapter_end = (
            chapter_starts[chapter + 1]
            if chapter + 1 < len(chapter_starts)
            else float("inf")
        )
        start_s = sentences[start_index][0]["start"]
        best = None
        for end_index in range(start_index, len(sentences)):
            if sentence_chapter[end_index] != chapter:
                break
            if sentences[end_index][-1]["end"] > chapter_end + EPSILON:
                break
            span = sentences[end_index][-1]["end"] - start_s
            if span > MAX_DURATION_S + EPSILON:
                break
            best = end_index
        if best is None:
            continue
        end_s = sentences[best][-1]["end"]
        if end_s - start_s < MIN_DURATION_S - EPSILON:
            continue
        text = " ".join(
            word["word"]
            for sentence in sentences[start_index:best + 1]
            for word in sentence
        )
        candidates.append({
            "start_s": round(start_s, 3),
            "end_s": round(end_s, 3),
            "chapter_start_s": round(chapter_starts[chapter], 3),
            "chapter_title": chapters[chapter]["title"],
            "text": text,
        })
    candidates.sort(key=lambda candidate: candidate["start_s"])
    return candidates[:MAX_CANDIDATES]


def parse_model_json(text):
    raw = text.strip()
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ShortsRankError(f"model response is not valid JSON: {exc}")


def parse_scores(text):
    """Parse the model output into the raw score rows; raise when the shape is wrong."""
    data = parse_model_json(text)
    if not isinstance(data, dict):
        raise ShortsRankError("model response is not a JSON object")
    scores = data.get("scores")
    if not isinstance(scores, list):
        raise ShortsRankError("model JSON key 'scores' must be a list")
    return scores


def request_scores(client, message):
    """Ask the model once, retry once on unparseable output, then give up."""
    last_error = None
    for _ in range(2):
        response = client.complete(
            "director", [{"role": "user", "content": message}], temperature=0.3
        )
        try:
            return parse_scores(response.text)
        except ShortsRankError as exc:
            last_error = exc
    raise ShortsRankError(f"model output is not parseable after one retry: {last_error}")


def validate_scores(scores, candidate_ids):
    """Validate one score row per candidate id; return ``{id: {hook, payoff, reason}}``.

    Rows for ids the run never proposed are ignored as model noise; a missing
    row, an out-of-range score or a bad reason is an error naming the id.
    """
    by_id = {}
    for row in scores:
        if not isinstance(row, dict):
            raise ShortsRankError("model JSON 'scores' entries must be objects")
        candidate_id = row.get("id")
        if isinstance(candidate_id, bool) or not isinstance(candidate_id, int):
            raise ShortsRankError("model JSON 'scores[].id' must be an integer")
        if candidate_id not in candidate_ids:
            continue
        hook = row.get("hook")
        payoff = row.get("payoff")
        for name, value in (("hook", hook), ("payoff", payoff)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ShortsRankError(f"candidate {candidate_id}: '{name}' must be a number")
            if not 0 <= value <= 100:
                raise ShortsRankError(
                    f"candidate {candidate_id}: '{name}' {value} is outside 0-100"
                )
        reason = row.get("reason")
        if not isinstance(reason, str):
            raise ShortsRankError(f"candidate {candidate_id}: 'reason' must be a string")
        reason = reason.strip()
        if not reason:
            raise ShortsRankError(f"candidate {candidate_id}: 'reason' is empty")
        by_id[candidate_id] = {
            "hook": hook,
            "payoff": payoff,
            "reason": reason[:MAX_REASON],
        }
    for candidate_id in candidate_ids:
        if candidate_id not in by_id:
            raise ShortsRankError(
                f"model JSON is missing a score row for candidate id {candidate_id}"
            )
    return by_id


def rank_candidates(candidates, scores):
    """Score, rank and keep the top candidates; ties break on the earlier start."""
    ranked = []
    for candidate_id, candidate in enumerate(candidates, 1):
        row = scores[candidate_id]
        score = (row["hook"] + row["payoff"]) / 2
        ranked.append({
            "start_s": candidate["start_s"],
            "end_s": candidate["end_s"],
            "duration_s": round(candidate["end_s"] - candidate["start_s"], 3),
            "score": score,
            "hook": row["hook"],
            "payoff": row["payoff"],
            "reason": row["reason"],
            "chapter_start_s": candidate["chapter_start_s"],
            "chapter_title": candidate["chapter_title"],
        })
    ranked.sort(key=lambda item: (-item["score"], item["start_s"]))
    return ranked[:TOP_N]


def build_message(prompt_text, transcript_text, candidate_lines):
    return "\n\n".join([prompt_text, "## Transcript", transcript_text, "## Candidates", candidate_lines])


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


def run(edit_path, words_path, chapters_path=None, out_path=None, prompt_path=None):
    """Rank the Shorts candidates and return the ``--json`` summary."""
    started = time.monotonic()
    document = load_edit(edit_path)
    groups = build_groups(document)
    words_map, single_file = build_words_map(words_path)
    mapped, warnings = map_words(groups, words_map, single_file)
    if not mapped:
        raise ShortsRankError("no words mapped: the kept transcript is empty")

    if chapters_path:
        chapters = load_chapters(chapters_path)
        has_chapters = True
    else:
        chapters = [{"start_s": 0.0, "title": None}]
        has_chapters = False

    candidates = build_candidates(mapped, chapters)
    if not candidates:
        raise ShortsRankError(
            "no Shorts candidates found: no 20-60 s sentence-aligned span inside a chapter"
        )

    prompt_file = Path(prompt_path).expanduser() if prompt_path else DEFAULT_PROMPT
    try:
        prompt_text = prompt_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ShortsRankError(f"prompt file not found: {prompt_file}")
    except OSError as exc:
        raise ShortsRankError(f"cannot read prompt file '{prompt_file}': {exc}")

    candidate_lines = "\n".join(
        f"{candidate_id}. [{format_stamp(candidate['start_s'])}-{format_stamp(candidate['end_s'])}] "
        f"{candidate['text']}"
        for candidate_id, candidate in enumerate(candidates, 1)
    )
    message = build_message(prompt_text, format_timeline(mapped), candidate_lines)

    client = get_client()
    scores = request_scores(client, message)
    by_id = validate_scores(scores, list(range(1, len(candidates) + 1)))
    ranked = rank_candidates(candidates, by_id)

    output_candidates = []
    for rank, item in enumerate(ranked, 1):
        output_candidates.append({
            "rank": rank,
            "start_s": item["start_s"],
            "end_s": item["end_s"],
            "duration_s": item["duration_s"],
            "score": item["score"],
            "hook": item["hook"],
            "payoff": item["payoff"],
            "reason": item["reason"],
            "chapter_start_s": item["chapter_start_s"] if has_chapters else None,
            "chapter_title": item["chapter_title"] if has_chapters else None,
        })

    out = (
        Path(out_path).expanduser()
        if out_path
        else Path(edit_path).expanduser().parent / "shorts_candidates.json"
    )
    output = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "tool": TOOL,
        "candidates": output_candidates,
    }
    try:
        validate_or_raise(output, "shorts_candidates")
    except SchemaError as exc:
        raise ShortsRankError(f"shorts candidates do not match the schema: {exc}")
    write_json(out, output)

    return {
        "outputs": {"candidates": str(out)},
        "candidates_scored": len(candidates),
        "written": len(output_candidates),
        "warnings": warnings,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": round(client.spent_usd, 8),
    }


def build_parser():
    parser = argparse.ArgumentParser(
        prog="shorts_rank.py",
        usage="shorts_rank.py edit.json WORDS [--chapters chapters.json] [--out shorts_candidates.json] [--json]",
        description=(
            "Rank Shorts candidates from the kept transcript of an edit with one LLM pass "
            "and write shorts_candidates.json."
        ),
        epilog=(
            "examples:\n"
            "  python3 scripts/shorts_rank.py edit.json words/ --json\n"
            "  python3 scripts/shorts_rank.py edit.json words/ --chapters chapters.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("edit", help="edit.json edit list (validated against schemas/edit.schema.json)")
    parser.add_argument("words", help="a Whisper word JSON or a folder of them (X.json or X_words.json)")
    parser.add_argument("--chapters", default=None, help="chapters.json; every candidate stays inside one chapter")
    parser.add_argument("--out", default=None, help="the shorts_candidates.json to write (default: next to the edit)")
    parser.add_argument("--prompt", default=None, help="alternative prompt file")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = run(args.edit, args.words, args.chapters, args.out, args.prompt)
    except (ShortsRankError, ConfigError, LLMError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        return 2

    if args.json:
        print(json.dumps({"ok": True, **summary}))
    else:
        print(
            f"written: {summary['outputs']['candidates']} "
            f"({summary['written']} of {summary['candidates_scored']} candidates)"
        )
        for warning in summary["warnings"]:
            print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
