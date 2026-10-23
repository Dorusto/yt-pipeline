#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rank Shorts candidates from the kept transcript of an edit with one LLM pass.

Usage:
    python3 scripts/shorts_rank.py edit.json WORDS [--chapters chapters.json]
        [--out shorts_candidates.json] [--layout {face,split}]
        [--layout-policy {auto,on,off}] [--json]
    python3 scripts/shorts_rank.py --fcproj PROJ --sequence NAME --words-dir DIR
        [--chapters chapters.json] [--layout {face,split}]
        [--layout-policy {auto,on,off}] [--json]
    python3 scripts/shorts_rank.py --timeline-words FILE [--chapters chapters.json]
        [--layout {face,split}] [--layout-policy {auto,on,off}] [--json]

The tool maps the per-source Whisper word JSON files onto the cuts of an
``edit.json`` timeline (the same midpoint rule ``chapters.py`` uses), splits the
kept transcript into sentences on pauses over 0.6 s and builds deterministic
candidates: runs of consecutive sentences inside one chapter whose span is
20-60 s starting at or after 60 s of the edit (the intro block never becomes a
candidate). The same mapping is available for a final FilmCraft
project (``--fcproj``) or for the timeline ``_words.json`` that
``subs_from_edit.py`` already writes (``--timeline-words``). One ``director``
model pass scores every candidate on hook and payoff and writes a title and a
description; a description that is not exactly two sentences is a warning in the
output, never a rejection. The top 5 by ``(hook + payoff) / 2`` that do not
overlap by more than 20 % are written to ``shorts_candidates.json`` (validated
against ``schemas/shorts_candidates.schema.json``). The file is the input of
``shorts_generator.py --candidates`` and the top 3 become Mango markers in the
FilmCraft export.
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

from common.config import ConfigError, load_config
from common.llm import LLMError, make_client
from common.media import clip_kind
from common.overlap import overlap_fraction
from common.schema import SchemaError, validate_or_raise
from common.timeline_words import (
    EPSILON,
    PAUSE_SPLIT_S,
    TimelineWordsError,
    build_groups,
    build_words_map,
    collect_words,
    format_stamp,
    format_timeline,
    map_words,
    plan_from_fcproj,
    publish_folder_for,
    timeline_words_from_file,
    video_spans_from_fcproj,
)
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME
from common.errors import report_error

TOOL = "scripts/shorts_rank.py"
SCHEMA_VERSION = "0.2"
DEFAULT_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "shorts_rank.md"
MIN_DURATION_S = 20.0
MAX_DURATION_S = 60.0
MAX_CANDIDATES = 60
TOP_N = 5
MAX_REASON = 200
MAX_TITLE = 60
MAX_DESCRIPTION = 400
DESCRIPTION_SENTENCES = 2
MAX_OVERLAP_FRAC = 0.2
INTRO_END_S = 60.0
LAYOUT_POLICIES = ("auto", "on", "off")

_client = None


class ShortsRankError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


class ShortsRankRuntimeError(ShortsRankError):
    """Raised when the model answer cannot be used after the call."""

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


def resolve_inputs(args):
    """Resolve the three input modes into ``(mapped, warnings, primary_input)``."""
    has_positionals = args.edit is not None or args.words is not None
    has_fcproj = args.fcproj is not None
    has_timeline = args.timeline_words is not None
    if has_positionals and (has_fcproj or has_timeline):
        raise ShortsRankError(
            "positional EDIT/WORDS cannot be combined with --fcproj or --timeline-words"
        )
    if has_fcproj and has_timeline:
        raise ShortsRankError("--fcproj and --timeline-words are mutually exclusive")
    if not has_fcproj and (args.sequence or args.words_dir or args.track is not None):
        raise ShortsRankError("--sequence, --words-dir and --track require --fcproj")
    if has_fcproj:
        if not args.sequence:
            raise ShortsRankError("--fcproj requires --sequence")
        if not args.words_dir:
            raise ShortsRankError("--fcproj requires --words-dir")
        plan = plan_from_fcproj(args.fcproj, args.sequence, args.track or "Audio 1")
        mapped, _dropped = collect_words(plan, args.words_dir)
        return mapped, [], Path(args.fcproj)
    if has_timeline:
        return timeline_words_from_file(args.timeline_words), [], Path(args.timeline_words)
    if not has_positionals:
        raise ShortsRankError(
            "no input given: pass EDIT WORDS, --fcproj PROJ --sequence NAME --words-dir DIR "
            "or --timeline-words FILE"
        )
    if args.edit is None or args.words is None:
        raise ShortsRankError("both EDIT and WORDS are required")
    document = load_edit(args.edit)
    groups = build_groups(document)
    words_map, single_file = build_words_map(args.words)
    mapped, warnings, _dropped = map_words(groups, words_map, single_file)
    return mapped, warnings, Path(args.edit)


def resolve_layout_policy(args):
    """Return the layout policy: the flag, else the config key, else ``auto``.

    YAML reads an unquoted ``on``/``off`` as a boolean, so those are mapped back
    to the policy names before validation.
    """
    if args.layout_policy is not None:
        value = args.layout_policy
    else:
        value = load_config().get("shorts_layout", "auto")
    if value is True:
        value = "on"
    elif value is False:
        value = "off"
    if value not in LAYOUT_POLICIES:
        raise ShortsRankError(
            f"invalid layout policy '{value}': must be one of auto, on, off"
        )
    return value


def collect_spans(args):
    """Return the input timeline's spans ``[{name, timeline_start, timeline_end}]``.

    The rough cut reads the video groups of the edit (a Mode B pair is one
    window with both clip names); ``--fcproj`` reads every video-track clip;
    ``--timeline-words`` has no spans.
    """
    if args.fcproj:
        return video_spans_from_fcproj(args.fcproj, args.sequence)
    if args.edit:
        document = load_edit(args.edit)
        spans = []
        for group in build_groups(document):
            if group["kind"] != "video":
                continue
            start = group["start_s"]
            end = start + group["length_s"]
            for entry in group["slots"]:
                clip = entry["slot"].get("clip")
                if clip:
                    spans.append({
                        "name": clip,
                        "timeline_start": start,
                        "timeline_end": end,
                    })
        return spans
    return []


def has_both_angles(spans):
    """True when the timeline has at least one camera clip and one screen clip."""
    kinds = {clip_kind(span["name"]) for span in spans}
    return "camera" in kinds and "screen" in kinds


def screen_during(spans, start_s, end_s):
    """True when a screen clip's span overlaps ``[start_s, end_s]``."""
    for span in spans:
        if clip_kind(span["name"]) != "screen":
            continue
        if span["timeline_start"] < end_s and span["timeline_end"] > start_s:
            return True
    return False


def candidate_layout(policy, has_both, screen_overlap):
    """Return the layout for one candidate under the policy."""
    if policy == "off":
        return "face"
    if policy == "on":
        return "split" if has_both else "face"
    return "split" if has_both and screen_overlap else "face"


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


def sentence_count(text):
    """Sentences in a short text, split on the sentence-ending punctuation."""
    return len([part for part in re.split(r"[.!?]+", text) if part.strip()])


def chapter_index_for(start_s, chapter_starts):
    """Return the index of the chapter whose start is the last one at or before ``start_s``."""
    index = 0
    for position, chapter_start in enumerate(chapter_starts):
        if chapter_start <= start_s + EPSILON:
            index = position
        else:
            break
    return index


def is_standalone(start_s):
    """True when a span starts at or after ``INTRO_END_S`` of the edit.

    The intro block (the first 60 s) is the edit's own opening material and
    never becomes a candidate, whatever the span's end.
    """
    return start_s >= INTRO_END_S


def build_candidates(mapped, chapters, stats=None):
    """Build the deterministic sentence-aligned 20-60 s candidates inside one chapter.

    Candidates that start before the first 60 s of the edit are dropped;
    ``stats["dropped_intro"]`` counts them when a stats dict is given.
    """
    sentences = split_sentences(mapped)
    if not sentences:
        return []
    chapter_starts = [float(chapter["start_s"]) for chapter in chapters]
    sentence_chapter = [
        chapter_index_for(sentence[0]["start"], chapter_starts) for sentence in sentences
    ]
    generated = []
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
        generated.append({
            "start_s": round(start_s, 3),
            "end_s": round(end_s, 3),
            "chapter_start_s": round(chapter_starts[chapter], 3),
            "chapter_title": chapters[chapter]["title"],
            "text": text,
        })
    candidates = [
        candidate for candidate in generated
        if is_standalone(candidate["start_s"])
    ]
    if stats is not None:
        stats["dropped_intro"] = len(generated) - len(candidates)
    candidates.sort(key=lambda candidate: candidate["start_s"])
    return candidates[:MAX_CANDIDATES]


def parse_model_json(text):
    raw = text.strip()
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ShortsRankRuntimeError(f"model response is not valid JSON: {exc}")


def parse_scores(text):
    """Parse the model output into the raw score rows; raise when the shape is wrong."""
    data = parse_model_json(text)
    if not isinstance(data, dict):
        raise ShortsRankRuntimeError("model response is not a JSON object")
    scores = data.get("scores")
    if not isinstance(scores, list):
        raise ShortsRankRuntimeError("model JSON key 'scores' must be a list")
    return scores


def request_scores(client, message, candidate_ids):
    """Ask the model, retry once on unparseable output or invalid rows, then give up."""
    last_error = None
    for _ in range(2):
        response = client.complete(
            "director", [{"role": "user", "content": message}], temperature=0.3
        )
        try:
            return validate_scores(parse_scores(response.text), candidate_ids)
        except ShortsRankError as exc:
            last_error = exc
    raise ShortsRankRuntimeError(f"model output is invalid after one retry: {last_error}")


def validate_scores(scores, candidate_ids):
    """Validate one score row per candidate id; return ``{id: {hook, payoff, reason, title, description}}``.

    Rows for ids the run never proposed are ignored as model noise; a missing
    row, an out-of-range score, a bad reason, a bad title or a bad description
    is an error naming the id.
    """
    by_id = {}
    for row in scores:
        if not isinstance(row, dict):
            raise ShortsRankRuntimeError("model JSON 'scores' entries must be objects")
        candidate_id = row.get("id")
        if isinstance(candidate_id, bool) or not isinstance(candidate_id, int):
            raise ShortsRankRuntimeError("model JSON 'scores[].id' must be an integer")
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
        title = row.get("title")
        if not isinstance(title, str):
            raise ShortsRankError(f"candidate {candidate_id}: 'title' must be a string")
        title = title.strip()
        if not title:
            raise ShortsRankError(f"candidate {candidate_id}: 'title' is empty")
        if len(title) > MAX_TITLE:
            raise ShortsRankError(
                f"candidate {candidate_id}: 'title' is longer than {MAX_TITLE} characters"
            )
        description = row.get("description")
        if not isinstance(description, str):
            raise ShortsRankError(
                f"candidate {candidate_id}: 'description' must be a string"
            )
        description = description.strip()
        if not description:
            raise ShortsRankError(f"candidate {candidate_id}: 'description' is empty")
        if len(description) > MAX_DESCRIPTION:
            raise ShortsRankError(
                f"candidate {candidate_id}: 'description' is longer than "
                f"{MAX_DESCRIPTION} characters"
            )
        by_id[candidate_id] = {
            "hook": hook,
            "payoff": payoff,
            "reason": reason[:MAX_REASON],
            "title": title,
            "description": description,
        }
    for candidate_id in candidate_ids:
        if candidate_id not in by_id:
            raise ShortsRankError(
                f"model JSON is missing a score row for candidate id {candidate_id}"
            )
    return by_id


def rank_candidates(candidates, scores):
    """Score, rank and greedily keep up to ``TOP_N`` candidates under the overlap cap.

    Ties break on the earlier start. A candidate is accepted only when it does
    not overlap an already accepted one by more than ``MAX_OVERLAP_FRAC``;
    skipped candidates are counted in ``dropped_overlap``.
    """
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
            "title": row["title"],
            "description": row["description"],
            "chapter_start_s": candidate["chapter_start_s"],
            "chapter_title": candidate["chapter_title"],
        })
    ranked.sort(key=lambda item: (-item["score"], item["start_s"]))
    accepted = []
    dropped_overlap = 0
    for item in ranked:
        if any(
            overlap_fraction(
                item["start_s"], item["end_s"], kept["start_s"], kept["end_s"]
            ) > MAX_OVERLAP_FRAC
            for kept in accepted
        ):
            dropped_overlap += 1
            continue
        accepted.append(item)
        if len(accepted) == TOP_N:
            break
    return accepted, dropped_overlap


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


def run(mapped, warnings, primary_input, chapters_path=None, out_path=None, prompt_path=None, publish_dir=None, layout=None, spans=None, layout_policy="auto"):
    """Rank the Shorts candidates and return ``(summary, ranked)``."""
    started = time.monotonic()
    if not mapped:
        raise ShortsRankError("no words mapped: the kept transcript is empty")

    if chapters_path:
        chapters = load_chapters(chapters_path)
        has_chapters = True
    else:
        chapters = [{"start_s": 0.0, "title": None}]
        has_chapters = False

    stats = {}
    candidates = build_candidates(mapped, chapters, stats)
    if not candidates:
        if stats.get("dropped_intro"):
            raise ShortsRankError(
                "no standalone 20-60 s candidate found: every candidate started "
                "before 60 s of the edit"
            )
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
    by_id = request_scores(client, message, list(range(1, len(candidates) + 1)))
    ranked, dropped_overlap = rank_candidates(candidates, by_id)

    output_candidates = []
    description_warnings = []
    has_both = has_both_angles(spans or [])
    for rank, item in enumerate(ranked, 1):
        candidate = {
            "rank": rank,
            "start_s": item["start_s"],
            "end_s": item["end_s"],
            "duration_s": item["duration_s"],
            "score": item["score"],
            "hook": item["hook"],
            "payoff": item["payoff"],
            "reason": item["reason"],
            "title": item["title"],
            "description": item["description"],
            "chapter_start_s": item["chapter_start_s"] if has_chapters else None,
            "chapter_title": item["chapter_title"] if has_chapters else None,
        }
        if layout is not None:
            candidate["layout"] = layout
        else:
            candidate["layout"] = candidate_layout(
                layout_policy,
                has_both,
                screen_during(spans or [], item["start_s"], item["end_s"]),
            )
        output_candidates.append(candidate)
        sentences = sentence_count(item["description"])
        if sentences != DESCRIPTION_SENTENCES:
            description_warnings.append(
                f"candidate {rank}: description should be exactly "
                f"{DESCRIPTION_SENTENCES} sentences (found {sentences})"
            )

    if out_path:
        out = Path(out_path).expanduser()
    elif publish_dir is not None:
        out = Path(publish_dir).expanduser() / "shorts_candidates.json"
    else:
        out = Path(primary_input).expanduser().parent / "shorts_candidates.json"
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

    summary = {
        "outputs": {"candidates": str(out)},
        "candidates_scored": len(candidates),
        "written": len(output_candidates),
        "dropped_overlap": dropped_overlap,
        "dropped_intro": stats.get("dropped_intro", 0),
        "layout": layout,
        "layout_policy": layout_policy,
        "warnings": list(warnings) + description_warnings,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": round(client.spent_usd, 8),
    }
    return summary, ranked


def build_parser():
    parser = argparse.ArgumentParser(
        prog="shorts_rank.py",
        usage=(
            "shorts_rank.py [EDIT] [WORDS] [--chapters chapters.json] "
            "[--out shorts_candidates.json] [--prompt FILE]\n"
            "               [--fcproj PROJ --sequence NAME [--track TRACK] --words-dir DIR]\n"
            "               [--timeline-words FILE] [--layout {face,split}] "
            "[--layout-policy {auto,on,off}] [--json]"
        ),
        description=(
            "Rank Shorts candidates from the kept transcript of an edit with one LLM pass "
            "and write shorts_candidates.json."
        ),
        epilog=(
            "input modes:\n"
            "  rough cut:      shorts_rank.py edit.json words/ --json\n"
            "  final project:  shorts_rank.py --fcproj project.fcproj --sequence Main "
            "--words-dir words/ --json\n"
            "  timeline words: shorts_rank.py --timeline-words edit_words.json --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("edit", nargs="?", help="edit.json edit list (rough-cut mode)")
    parser.add_argument("words", nargs="?", help="a Whisper word JSON or a folder of them (rough-cut mode)")
    parser.add_argument("--chapters", default=None, help="chapters.json; every candidate stays inside one chapter")
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "the shorts_candidates.json to write (default: the clip's publish folder — the "
            "parent of --words-dir — in --fcproj mode, else next to the primary input)"
        ),
    )
    parser.add_argument("--prompt", default=None, help="alternative prompt file")
    parser.add_argument("--fcproj", default=None, help="FilmCraft project file (final-project mode)")
    parser.add_argument("--sequence", default=None, help="name of the sequence item to use (with --fcproj)")
    parser.add_argument("--track", default=None, help="audio track name inside the sequence (default 'Audio 1')")
    parser.add_argument("--words-dir", default=None, help="folder with one Whisper word JSON per source clip (with --fcproj)")
    parser.add_argument("--timeline-words", default=None, help="a timeline _words.json written by subs_from_edit.py (timeline-words mode)")
    parser.add_argument(
        "--layout",
        choices=("face", "split"),
        default=None,
        help=(
            "write this layout on every candidate: 'face' is the reframe.py face crop, "
            "'split' stacks the face region over the screen region of the same 16:9 frame "
            "(default: the --layout-policy decides per candidate)"
        ),
    )
    parser.add_argument(
        "--layout-policy",
        default=None,
        metavar="{auto,on,off}",
        help=(
            "how the layout is chosen per candidate when --layout is not given: 'auto' "
            "(default) writes 'split' only when the session has a camera and a screen "
            "recording and the screen shows something during the candidate, else 'face'; "
            "'on' writes 'split' for every candidate of a camera+screen session; 'off' "
            "always writes 'face'. Read from the config key 'shorts_layout' when the flag "
            "is omitted. --layout overrides it."
        ),
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        mapped, warnings, primary_input = resolve_inputs(args)
        layout_policy = resolve_layout_policy(args)
        spans = collect_spans(args) if args.layout is None else []
        publish_dir = (
            publish_folder_for(args.words_dir)
            if args.fcproj and args.words_dir
            else None
        )
        summary, ranked = run(
            mapped, warnings, primary_input, args.chapters, args.out, args.prompt,
            publish_dir, args.layout, spans=spans, layout_policy=layout_policy,
        )
    except (ShortsRankRuntimeError, LLMError) as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except (ShortsRankError, ConfigError, TimelineWordsError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)

    if args.json:
        print(json.dumps({"ok": True, **summary}))
    else:
        for rank, item in enumerate(ranked, 1):
            print(
                f"{rank}. {item['score']:.1f}  "
                f"{format_stamp(item['start_s'])}-{format_stamp(item['end_s'])} "
                f"({item['duration_s']:.1f}s) {item['title']}"
            )
        print(
            f"written: {summary['outputs']['candidates']} "
            f"({summary['written']} of {summary['candidates_scored']} candidates)"
        )
        for warning in summary["warnings"]:
            print(f"warning: {warning}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
