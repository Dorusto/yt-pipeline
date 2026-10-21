"""Map per-source Whisper words onto an edit timeline.

Shared by ``subs_from_edit.py`` (FilmCraft project or ``edit.json`` -> timeline
words), ``chapters.py`` and ``shorts_rank.py`` (the same kept words in timeline
time from a rough-cut ``edit.json``, a final FilmCraft project or an already
mapped timeline word file). The midpoint rule is the source of truth: a word
belongs to the window that contains the middle of its source span, and its
timeline time is the source time shifted by the window's timeline start.
"""

import json
from pathlib import Path

from common.constants import EPSILON, PAUSE_SPLIT_S, TICKS_PER_SECOND

XFADE_DEFAULT_S = 0.3
WORDS_SUFFIXES = ("_words.json", ".json")


class TimelineWordsError(Exception):
    """Bad input: missing words, unreadable JSON or a timeline that cannot map linearly."""


def load_json(path):
    try:
        return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise TimelineWordsError(f"cannot read '{path}': file not found")
    except json.JSONDecodeError as exc:
        raise TimelineWordsError(f"cannot read '{path}': not valid JSON ({exc})")
    except OSError as exc:
        raise TimelineWordsError(f"cannot read '{path}': {exc}")


def word_in_window(word, start, end):
    """True when the word's midpoint lies in ``[start, end)`` — the shared keep rule."""
    midpoint = (word["start"] + word["end"]) / 2
    return start <= midpoint < end


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
                raise TimelineWordsError(f"'{path}': a word entry has no numeric start/end")
            words.append({"word": text, "start": start, "end": end})
    return words


def timeline_words_from_file(path):
    """Return the flat ``[{word, start, end}]`` list of a timeline ``_words.json``."""
    data = load_json(path)
    if not isinstance(data, list):
        raise TimelineWordsError(f"'{path}': a timeline word file must be a JSON list")
    words = []
    for index, word in enumerate(data):
        if not isinstance(word, dict):
            raise TimelineWordsError(f"'{path}': word {index} is not an object")
        text = str(word.get("word", "")).strip()
        if not text:
            continue
        try:
            start = float(word["start"])
            end = float(word["end"])
        except (KeyError, TypeError, ValueError):
            raise TimelineWordsError(f"'{path}': a word entry has no numeric start/end")
        words.append({"word": text, "start": start, "end": end})
    words.sort(key=lambda item: (item["start"], item["end"]))
    return words


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
    raise TimelineWordsError(f"words path not found: {path}")


def words_file_for(words_dir, stem):
    """Return ``<words_dir>/<stem>_words.json`` or ``<words_dir>/<stem>.json``, or None."""
    directory = Path(words_dir)
    for suffix in WORDS_SUFFIXES:
        candidate = directory / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def publish_folder_for(words_dir):
    """Return the clip's publish folder for a words dir: its parent.

    The words JSONs live in the clip's ``json/`` subfolder and the publish chain
    builds ``publish-kit/`` in the same clip folder, so the parent is where the
    publish kit's siblings (``chapters.json``, ``shorts_candidates.json``) go.
    """
    return Path(words_dir).expanduser().resolve().parent


def media_item_path(item):
    media = (((item or {}).get("kind") or {}).get("Media") or {}).get("media") or {}
    return (media.get("File") or {}).get("path")


def plan_from_fcproj(path, sequence, track):
    """Build the timeline plan from a FilmCraft project's audio track."""
    data = load_json(path)
    items = (data.get("project") or {}).get("items") or {}
    sequences = {}
    for value in items.values():
        kind = value.get("kind") or {}
        if "Sequence" in kind and value.get("name"):
            sequences[value["name"]] = value
    if sequence not in sequences:
        found = ", ".join(sorted(sequences)) or "none"
        raise TimelineWordsError(f"unknown sequence '{sequence}' (sequences found: {found})")
    sequence_item = sequences[sequence]
    audio_tracks = ((sequence_item.get("kind") or {}).get("Sequence") or {}).get("audio_tracks") or []
    tracks = {entry.get("name"): entry for entry in audio_tracks}
    if track not in tracks:
        found = ", ".join(sorted(str(name) for name in tracks)) or "none"
        raise TimelineWordsError(f"unknown track '{track}' (tracks found: {found})")

    timeline_items = sorted(tracks[track].get("items") or [], key=lambda clip: clip.get("start", 0))
    plan = []
    for clip in timeline_items:
        media_path = media_item_path(items.get(str(clip.get("item"))))
        name = clip.get("name") or (Path(media_path).name if media_path else str(clip.get("item")))
        if not clip.get("enabled", True):
            plan.append({"name": name, "enabled": False})
            continue
        if media_path is None:
            raise TimelineWordsError(f"clip '{name}': source media is missing from the project")
        speed = float(clip.get("speed", 1.0) or 1.0)
        if abs(speed - 1.0) > EPSILON:
            raise TimelineWordsError(
                f"clip '{name}': speed {speed:g} is not 1.0, word times would not map linearly"
            )
        if clip.get("reverse", False):
            raise TimelineWordsError(
                f"clip '{name}': reverse playback is not supported, word times would not map linearly"
            )
        start = float(clip["start"]) / TICKS_PER_SECOND
        duration = float(clip["duration"]) / TICKS_PER_SECOND
        source_in = float(clip["source_in"]) / TICKS_PER_SECOND
        plan.append({
            "name": name,
            "source_stem": Path(media_path).stem,
            "enabled": True,
            "window_start": source_in,
            "window_end": source_in + duration,
            "timeline_start": start,
            "timeline_end": start + duration,
            "kept_words": 0,
        })
    return plan


def collect_words(plan, words_dir):
    """Map every source word onto the timeline; return ``(placements, words_dropped)``."""
    by_source = {}
    for entry in plan:
        if entry.get("enabled") and entry.get("source_stem"):
            by_source.setdefault(entry["source_stem"], []).append(entry)
    placements = []
    words_dropped = 0
    for stem, entries in by_source.items():
        words_path = words_file_for(words_dir, stem)
        if words_path is None:
            raise TimelineWordsError(
                f"no word JSON for clip '{entries[0]['name']}' "
                f"(looked for '{stem}.json' in {words_dir})"
            )
        for word in load_source_words(words_path):
            matched = False
            for entry in entries:
                if word_in_window(word, entry["window_start"], entry["window_end"]):
                    matched = True
                    shift = entry["timeline_start"] - entry["window_start"]
                    placements.append({
                        "word": word["word"],
                        "start": round(word["start"] + shift, 3),
                        "end": round(word["end"] + shift, 3),
                    })
                    entry["kept_words"] += 1
            if not matched:
                words_dropped += 1
    placements.sort(key=lambda item: (item["start"], item["end"]))
    return placements, words_dropped


def slot_length(slot):
    if slot.get("kind") == "photo":
        return float(slot.get("duration_s") or 0.0)
    return float(slot["out_s"]) - float(slot["in_s"])


def fade_for_join(slot, dur_prev, dur_next, xfade_s=XFADE_DEFAULT_S):
    """The overlap at the join into ``slot``; 0 means a hard cut.

    The one crossfade rule: ``render.py`` joins with it and ``serve.py``
    overlaps its timeline blocks with it. A ``crossfade`` transition takes
    its ``duration_s`` (or ``xfade_s`` when absent or not positive), capped
    at half of either neighbour.
    """
    transition = slot.get("transition") or {}
    if transition.get("type") != "crossfade":
        return 0.0
    try:
        requested = float(transition.get("duration_s") or 0.0)
    except (TypeError, ValueError):
        requested = 0.0
    if requested <= 0:
        try:
            requested = float(xfade_s)
        except (TypeError, ValueError):
            requested = 0.0
    if requested <= 0:
        return 0.0
    return max(0.0, min(requested, float(dur_prev) / 2, float(dur_next) / 2))


def build_groups(document):
    """Group consecutive video slots sharing one id; each group is one timeline window.

    A Mode B pair (two video slots with the same ``id``) is one group. A
    ``crossfade`` into a group overlaps it with the previous one by the same
    rule ``render.py`` uses. Every group carries a ``kept_words`` counter filled
    in by ``map_words``.
    """
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
    previous_length = 0.0
    has_previous = False
    for group in groups:
        group["length_s"] = slot_length(group["slots"][0]["slot"])
        overlap = 0.0
        if has_previous:
            overlap = fade_for_join(group["slots"][0]["slot"], previous_length, group["length_s"])
        group["start_s"] = cursor - overlap
        cursor = group["start_s"] + group["length_s"]
        previous_length = group["length_s"]
        has_previous = True
        group["kept_words"] = 0
    return groups


def check_linear(slot):
    """Refuse a slot whose word times would not map linearly onto the timeline."""
    speed = float(slot.get("speed", 1.0) or 1.0)
    if abs(speed - 1.0) > EPSILON:
        raise TimelineWordsError(
            f"slot '{slot.get('id')}' ({slot.get('clip')}): speed {speed:g} is not 1.0, "
            "word times would not map linearly"
        )
    middle = slot.get("middle_speed")
    if middle is not None and abs(float(middle) - 1.0) > EPSILON:
        raise TimelineWordsError(
            f"slot '{slot.get('id')}' ({slot.get('clip')}): middle_speed {float(middle):g} "
            "is not 1.0, word times would not map linearly"
        )
    if slot.get("reverse", False):
        raise TimelineWordsError(
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


def map_words(groups, words_map, single_file, strict=False):
    """Map every kept word onto timeline time; return ``(mapped, warnings, words_dropped)``.

    A Mode B pair is one window and its words are counted once, from the member
    whose clip stem matches a words file. A word that falls in no window counts
    as dropped. ``strict`` raises instead of warning when a group has no words
    file.
    """
    video_groups = [group for group in groups if group["kind"] == "video"]
    warnings = []
    resolved = []
    for group in groups:
        group["kept_words"] = 0
        if group["kind"] != "video":
            continue
        for entry in group["slots"]:
            check_linear(entry["slot"])
        member, words_file = words_member(group, words_map, single_file, len(video_groups))
        if member is None:
            slot_id = group["slots"][0]["slot"].get("id")
            if strict:
                raise TimelineWordsError(
                    f"slot '{slot_id}' ({group['slots'][0]['slot'].get('clip')}): no words file"
                )
            warnings.append(f"no words file for slot '{slot_id}', skipped")
            continue
        resolved.append((group, member, Path(words_file)))
    by_file = {}
    for group, member, words_file in resolved:
        by_file.setdefault(words_file, []).append((group, member))
    mapped = []
    words_dropped = 0
    for words_file, entries in by_file.items():
        for word in load_source_words(words_file):
            matched = False
            for group, member in entries:
                in_s = float(member["slot"]["in_s"])
                out_s = float(member["slot"]["out_s"])
                if word_in_window(word, in_s, out_s):
                    matched = True
                    shift = group["start_s"] - in_s
                    mapped.append({
                        "word": word["word"],
                        "start": round(word["start"] + shift, 3),
                        "end": round(word["end"] + shift, 3),
                    })
                    group["kept_words"] += 1
            if not matched:
                words_dropped += 1
    mapped.sort(key=lambda item: (item["start"], item["end"]))
    return mapped, warnings, words_dropped


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
