#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build a Romanian timeline .srt and word list from a FilmCraft project or an edit.json.

The tool maps the per-source Whisper word JSON files onto the cuts of an edit
timeline and writes a flat ``_words.json`` (the shape ``shorts_generator.py``
consumes) plus a standard ``_RO.srt``. It never transcribes: word times come
only from the given JSON files. A FilmCraft project and an ``edit.json`` edit
list are both accepted and use the same mapping rule.

Usage:
    python3 subs_from_edit.py --fcproj PROJ.fcproj --sequence Main --words-dir DIR --out-dir DIR
    python3 subs_from_edit.py --from-edit edit.json --words-dir DIR --out-dir DIR
"""

import argparse
import json
import sys
import time
from pathlib import Path

from common.timeline_words import (
    PAUSE_SPLIT_S,
    TimelineWordsError,
    build_groups,
    build_words_map,
    collect_words,
    map_words,
    plan_from_fcproj,
)

SENTENCE_END = (".", "!", "?", "\u2026")
MAX_LINES = 2
MAX_LINE_CHARS = 42


class SubsError(Exception):
    """A user-facing error that exits with code 2 and writes nothing."""


def load_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise SubsError(f"cannot read '{path}': file not found")
    except json.JSONDecodeError as exc:
        raise SubsError(f"cannot read '{path}': not valid JSON ({exc})")
    except OSError as exc:
        raise SubsError(f"cannot read '{path}': {exc}")


def clips_from_plan(plan):
    clips = []
    for entry in plan:
        if not entry.get("enabled"):
            clips.append({"name": entry["name"], "skipped": "disabled"})
            continue
        clips.append({
            "name": entry["name"],
            "kept_words": entry.get("kept_words", 0),
            "timeline_start_s": round(entry["timeline_start"], 3),
            "timeline_end_s": round(entry["timeline_end"], 3),
        })
    return clips


def clips_from_groups(groups):
    clips = []
    for group in groups:
        slot = group["slots"][0]["slot"]
        name = slot.get("photo") if group["kind"] == "photo" else slot.get("clip")
        clips.append({
            "name": name,
            "kept_words": group.get("kept_words", 0),
            "timeline_start_s": round(group["start_s"], 3),
            "timeline_end_s": round(group["start_s"] + group["length_s"], 3),
        })
    return clips


def attach_hyphen_suffixes(placements):
    """Glue Whisper's split clitics back on: "task" + "-uri" -> "task-uri", "m" + "-am" -> "m-am"."""
    merged = []
    for item in placements:
        if merged and item["word"].startswith("-") and len(item["word"]) > 1:
            previous = dict(merged[-1])
            previous["word"] = previous["word"] + item["word"]
            previous["end"] = max(previous["end"], item["end"])
            merged[-1] = previous
        else:
            merged.append(item)
    return merged


def layout_lines(words):
    """Greedily pack words into lines of at most ``MAX_LINE_CHARS`` characters."""
    lines = []
    current = []
    for word in words:
        if not current:
            current = [word]
        elif len(" ".join(current)) + 1 + len(word) <= MAX_LINE_CHARS:
            current.append(word)
        else:
            lines.append(current)
            current = [word]
    if current:
        lines.append(current)
    return lines


def cue_fits(words):
    """True when ``words`` fit a cue: at most 2 lines of at most 42 characters."""
    if len(words) <= 1:
        return True
    lines = layout_lines(words)
    if len(lines) > MAX_LINES:
        return False
    return all(len(" ".join(line)) <= MAX_LINE_CHARS or len(line) == 1 for line in lines)


def ends_sentence(word):
    return word.endswith(SENTENCE_END)


def build_cues(placements):
    """Pack the timeline words into cues by punctuation, pause and line capacity."""
    cues = []
    current = []
    total = len(placements)
    for index, item in enumerate(placements):
        if current and not cue_fits([entry["word"] for entry in current] + [item["word"]]):
            cues.append(current)
            current = []
        current.append(item)
        if index + 1 < total:
            next_item = placements[index + 1]
            pause = round((next_item["start"] - item["end"]) * 1000)
            if ends_sentence(item["word"]) or pause > PAUSE_SPLIT_S * 1000:
                cues.append(current)
                current = []
    if current:
        cues.append(current)
    return cues


def format_ms(total_ms):
    total_ms = max(0, int(total_ms))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    seconds = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def build_srt(cues):
    blocks = []
    for index, cue in enumerate(cues, 1):
        start_ms = round(cue[0]["start"] * 1000)
        end_ms = round(cue[-1]["end"] * 1000)
        text = "\n".join(" ".join(line) for line in layout_lines([piece["word"] for piece in cue]))
        blocks.append(f"{index}\n{format_ms(start_ms)} --> {format_ms(end_ms)}\n{text}")
    return "\n\n".join(blocks) + "\n"


def run(fcproj, sequence, track, from_edit, words_dir, out_dir, name):
    started = time.monotonic()
    if from_edit:
        groups = build_groups(load_json(from_edit))
        words_map, single_file = build_words_map(words_dir)
        placements, _warnings, words_dropped = map_words(groups, words_map, single_file, strict=True)
        default_name = Path(from_edit).stem
        clips = clips_from_groups(groups)
    else:
        plan = plan_from_fcproj(fcproj, sequence, track)
        placements, words_dropped = collect_words(plan, words_dir)
        default_name = sequence
        clips = clips_from_plan(plan)
    output_name = name or default_name

    placements = attach_hyphen_suffixes(placements)
    cues = build_cues(placements)

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    srt_path = out_path / f"{output_name}_RO.srt"
    words_path = out_path / f"{output_name}_words.json"
    srt_path.write_text(build_srt(cues), encoding="utf-8")
    words_path.write_text(json.dumps(placements, ensure_ascii=False), encoding="utf-8")

    return {
        "outputs": {"srt": str(srt_path), "words_json": str(words_path)},
        "clips": clips,
        "words_kept": len(placements),
        "words_dropped": words_dropped,
        "cues": len(cues),
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }


def build_parser():
    parser = argparse.ArgumentParser(
        description="Build a Romanian .srt and a timeline word list from a FilmCraft project or an edit.json.",
        epilog=(
            "Example: python3 scripts/subs_from_edit.py --fcproj project.fcproj "
            "--sequence Main --words-dir words/ --out-dir out/"
        ),
    )
    parser.add_argument("--fcproj", help="FilmCraft project file (JSON)")
    parser.add_argument("--sequence", help="name of the sequence item to use")
    parser.add_argument("--track", help="audio track name inside the sequence (default 'Audio 1')")
    parser.add_argument("--from-edit", help="edit.json edit list to map instead of a FilmCraft project")
    parser.add_argument("--words-dir", required=True, help="folder with one Whisper word JSON per source clip")
    parser.add_argument("--out-dir", required=True, help="output folder (created if missing)")
    parser.add_argument("--name", help="output stem (default: the sequence name or edit file stem)")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if bool(args.fcproj) == bool(args.from_edit):
            raise SubsError("exactly one of --fcproj and --from-edit is required")
        if args.from_edit and (args.sequence or args.track is not None):
            raise SubsError("--from-edit cannot be combined with --sequence or --track")
        if args.fcproj and not args.sequence:
            raise SubsError("--fcproj requires --sequence")
        summary = run(
            args.fcproj,
            args.sequence,
            args.track or "Audio 1",
            args.from_edit,
            args.words_dir,
            args.out_dir,
            args.name,
        )
    except (SubsError, TimelineWordsError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        return 2

    if args.json:
        print(json.dumps({"ok": True, **summary}))
    else:
        print(
            f"written: {summary['outputs']['srt']} and {summary['outputs']['words_json']} "
            f"({summary['words_kept']} words, {summary['cues']} cues)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
