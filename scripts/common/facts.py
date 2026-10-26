# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shot facts shared by the directors: the workspace read and the candidate windows.

A shot fact is one shotlog candidate as the editor needs it: the usable window
(the steady range, a landing hold included), the peak, the role and motion
label, the target length and the subject events inside the window.
``build_shot_facts`` reads ``ingest.json``, ``quality/``, ``motion/`` and the
shotlog and returns one fact per eligible shot key. Moved here from
``llm_director.py`` so the code editor and the LLM director share one
implementation (VISION: no tool imports another tool's code).
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common.constants import MIN_SHOT_LENGTH_S
from common.jsonio import load_json
from common.moves import MOVE_ROLES
from common.overlap import overlap_seconds
from common.peak import centre_window, choose_peak

MAX_SLOT_S = 12.0
DEFAULT_IMPORTANT_MAX_S = 6.0
TOLERANCE_S = 0.05
PHOTO_DEFAULT_S = 3.0
LANDING_FLOOR_S = 0.75

SKIP_ROLES = {"accidental", "setup", "teardown"}
LANDING_GAP_S = 0.75
IMPORTANT_SEMANTIC_MOTIONS = {"orbit", "reveal", "tracking", "pov", "travel"}
IMPORTANT_MOTION_LABELS = {"orbit", "orbit_candidate", "reveal", "tracking", "pov"}
NON_MOVE_MOTION_LABELS = {"static", "minor_motion"}

CLIP_COUNTER = re.compile(r"_(\d{4})_")


class FactsError(RuntimeError):
    """Raised when the workspace cannot be read into shot facts."""


def clamp(value, low, high):
    return max(low, min(high, value))


def parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def first_not_none(*values):
    for value in values:
        if value is not None:
            return value
    return None


def clip_counter(name):
    match = CLIP_COUNTER.search(name)
    return match.group(1) if match else Path(name).stem


def candidate_key(candidate):
    return f"{clip_counter(candidate['clip'])}/{candidate['segment'].get('id')}"


def load_shotlog(workspace):
    path = workspace / "shotlog.json"
    if not path.exists():
        return None
    data = load_json(path)
    if not isinstance(data, dict):
        return None
    shots = data.get("shots") or []
    return {shot["key"]: shot for shot in shots if shot.get("key")}


def load_ingest(workspace):
    path = workspace / "ingest.json"
    if not path.exists():
        raise FactsError(f"missing ingest.json in {workspace}")
    data = load_json(path)
    if data is None:
        raise FactsError(f"unreadable ingest.json in {workspace}")
    clips = data.get("clips") or []
    if not clips:
        raise FactsError("ingest.json has no clips")
    return clips


def load_ingest_photos(workspace):
    """The still photos listed in ``ingest.json``, or [] when there are none."""
    data = load_json(workspace / "ingest.json")
    if not isinstance(data, dict):
        return []
    photos = data.get("photos") or []
    return [
        photo
        for photo in photos
        if isinstance(photo, dict) and (photo.get("file") or photo.get("path"))
    ]


def order_clips(clips):
    parsed = [(clip, parse_time(clip.get("creation_time"))) for clip in clips]
    known = [moment for _, moment in parsed if moment is not None]
    fallback = min(known) if known else datetime.min.replace(tzinfo=timezone.utc)
    ordered = sorted(parsed, key=lambda item: (item[1] or fallback, item[0]["file"]))
    return [(clip, moment or fallback) for clip, moment in ordered]


def clip_position(moment, first_moment, span_seconds):
    return clamp((moment - first_moment).total_seconds() / span_seconds, 0.0, 1.0)


def best_overlapping_move(segment, moves):
    best = None
    best_overlap = 0.0
    for move in moves:
        overlap = overlap_seconds(segment["start_s"], segment["end_s"], move["start_s"], move["end_s"])
        if overlap > best_overlap:
            best_overlap = overlap
            best = move
    return best


def build_candidates(clip, clip_dt, position, content_offset, quality, motion, min_slot_s, beats_by_segment=None):
    moves = (motion or {}).get("moves") or []
    beats_by_segment = beats_by_segment or {}
    candidates = []
    for segment in quality.get("segments") or []:
        role = segment.get("role")
        if role in SKIP_ROLES:
            continue
        subranges = segment_subranges(segment, beats_by_segment, clip.get("file"))
        for sub_start, sub_end, shot_key in subranges:
            move = None
            if role == "hold":
                in_s, out_s = sub_start, sub_end
            else:
                move = best_overlapping_move({"start_s": sub_start, "end_s": sub_end}, moves)
                if move is not None:
                    in_s = first_not_none(move.get("handle_in_s"), move.get("steady_start_s"), move.get("start_s"))
                    out_s = first_not_none(move.get("steady_end_s"), move.get("handle_out_s"), move.get("end_s"))
                else:
                    in_s, out_s = sub_start, sub_end
                in_s = max(in_s, sub_start)
                out_s = min(out_s, sub_end)
            if in_s is None or out_s is None or out_s - in_s < min_slot_s:
                continue
            candidates.append(
                {
                    "clip": clip["file"],
                    "clip_dt": clip_dt,
                    "position": position,
                    "content_offset": content_offset,
                    "role": role,
                    "segment": segment,
                    "move": move,
                    "in_s": float(in_s),
                    "out_s": float(out_s),
                    "motion_label": move.get("label") if move else segment.get("motion_label"),
                    "steadiness": move.get("steadiness") if move else ("static" if role == "hold" else None),
                    "hero": None,
                    "semantic_motion": None,
                    "shot_key": shot_key,
                }
            )
    return candidates


def segment_subranges(segment, beats_by_segment, clip_file=None):
    """The ``(start, end, shot key)`` units of a segment: its beats or itself."""
    beats = beats_by_segment.get((clip_file, segment.get("id"))) or beats_by_segment.get(segment.get("id")) or []
    if not beats:
        return [(float(segment["start_s"]), float(segment["end_s"]), None)]
    subranges = []
    for beat in beats:
        start_s = max(float(segment["start_s"]), float(beat.get("start_s", segment["start_s"])))
        end_s = min(float(segment["end_s"]), float(beat.get("end_s", segment["end_s"])))
        if end_s - start_s <= 0:
            continue
        subranges.append((start_s, end_s, beat.get("key")))
    return subranges or [(float(segment["start_s"]), float(segment["end_s"]), None)]


def beats_by_segment_from_shotlog(shotlog):
    """Group the shotlog's content-beat shots by their clip and parent segment id."""
    grouped = {}
    if not shotlog:
        return grouped
    for shot in shotlog.values():
        beat_of = shot.get("beat_of")
        if not beat_of:
            continue
        grouped.setdefault((shot.get("clip"), beat_of), []).append(shot)
    for beats in grouped.values():
        beats.sort(key=lambda item: (item.get("start_s") or 0.0, item.get("key") or ""))
    return grouped


def landing_pairs(quality):
    segments = quality.get("segments") or []
    pairs = []
    for first, second in zip(segments, segments[1:]):
        if first.get("role") in MOVE_ROLES and second.get("role") == "hold":
            if second["start_s"] - first["end_s"] <= LANDING_GAP_S:
                pairs.append((first, second))
    return pairs


def make_phrase_candidate(move_candidate, hold_segment):
    phrase = dict(move_candidate)
    phrase["out_s"] = float(hold_segment["end_s"])
    phrase["score"] = move_candidate["score"]
    phrase["phrase"] = True
    phrase["landing_start_s"] = float(hold_segment["start_s"])
    phrase["landing_end_s"] = float(hold_segment["end_s"])
    phrase["landing_hold_key"] = f"{clip_counter(move_candidate['clip'])}/{hold_segment.get('id')}"
    return phrase


def build_phrases(scored, quality):
    pairs = landing_pairs(quality)
    if not pairs:
        return scored
    removed = set()
    phrases = []
    for move_segment, hold_segment in pairs:
        siblings = [c for c in scored if c.get("segment") is move_segment]
        move_candidate = _phrase_move(siblings)
        if move_candidate is None:
            continue
        removed.add(id(move_candidate))
        phrases.append(make_phrase_candidate(move_candidate, hold_segment))
    kept = [candidate for candidate in scored if id(candidate) not in removed]
    return kept + phrases


def _phrase_move(candidates):
    """The candidate that lands on the hold: the one latest in source time."""
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item["out_s"], item.get("score") or 0.0))


def dedupe_candidates(candidates, drop_reasons=None):
    kept = []
    ordered = sorted(
        candidates,
        key=lambda item: (-item["score"], 0 if item.get("phrase") else 1, item["in_s"]),
    )
    for candidate in ordered:
        clash = next(
            (
                other
                for other in kept
                if overlap_seconds(candidate["in_s"], candidate["out_s"], other["in_s"], other["out_s"]) > 0
            ),
            None,
        )
        if clash is not None:
            if drop_reasons is not None:
                key = candidate.get("shot_key") or candidate_key(candidate)
                drop_reasons.setdefault(
                    key, f"overlapping window of {clash.get('shot_key') or candidate_key(clash)}, lower score"
                )
            continue
        kept.append(candidate)
    return kept


def is_important(candidate):
    if candidate.get("hero"):
        return True
    motion_label = candidate.get("motion_label")
    if motion_label in NON_MOVE_MOTION_LABELS:
        return False
    if candidate.get("semantic_motion") in IMPORTANT_SEMANTIC_MOTIONS:
        return True
    return motion_label in IMPORTANT_MOTION_LABELS


def default_target_s(candidate, important_max_s, slot_min_s=MIN_SHOT_LENGTH_S, slot_max_s=MAX_SLOT_S):
    window = candidate["out_s"] - candidate["in_s"]
    if is_important(candidate):
        return round(min(window, important_max_s), 3)
    return round(clamp(window, slot_min_s, slot_max_s), 3)


def keeps_whole(fact):
    """True when the shot keeps its window up to the hero cap: a hero or semantic move, never a static hold."""
    return bool(fact.get("keep_whole")) and fact.get("motion_label") not in NON_MOVE_MOTION_LABELS


def _subject_event_s(events, event_type, window_in_s, window_out_s, first):
    """The first or last event of ``event_type`` inside the window, or None."""
    times = [
        float(event["t_s"])
        for event in events or []
        if isinstance(event, dict)
        and event.get("type") == event_type
        and event.get("t_s") is not None
        and float(window_in_s) <= float(event["t_s"]) <= float(window_out_s)
    ]
    if not times:
        return None
    return round(min(times) if first else max(times), 3)


def make_fact(candidate, important_max_s, slot_min_s=MIN_SHOT_LENGTH_S, slot_max_s=MAX_SLOT_S, events=None):
    return {
        "shot_key": candidate.get("shot_key"),
        "clip": candidate["clip"],
        "segment_id": (candidate.get("segment") or {}).get("id"),
        "window_in_s": round(float(candidate["in_s"]), 3),
        "window_out_s": round(float(candidate["out_s"]), 3),
        "peak_s": candidate.get("peak_s"),
        "peak_source": candidate.get("peak_source"),
        "landing_at_s": round(float(candidate["landing_start_s"]), 3) if candidate.get("phrase") else None,
        "landing_hold_key": candidate.get("landing_hold_key"),
        "keep_whole": is_important(candidate),
        "target_s": default_target_s(candidate, important_max_s, slot_min_s, slot_max_s),
        "score": candidate.get("score"),
        "summary": candidate.get("summary"),
        "role": candidate.get("role"),
        "motion_label": candidate.get("motion_label"),
        "hero": candidate.get("hero"),
        "faces_max": (candidate.get("segment") or {}).get("faces_max"),
        "face_size_max": (candidate.get("segment") or {}).get("face_size_max"),
        "similar_to": list(candidate.get("similar_to") or []),
        "subject_enter_s": _subject_event_s(events, "subject_enters", candidate["in_s"], candidate["out_s"], True),
        "subject_leave_s": _subject_event_s(events, "subject_leaves", candidate["in_s"], candidate["out_s"], False),
    }


def make_photo_fact(shot, photo):
    """One photo shot as a shot fact: no window, no landing, a fixed 3 s target."""
    return {
        "shot_key": shot.get("key"),
        "media_type": "photo",
        "photo": shot.get("clip") or photo.get("file"),
        "photo_path": photo.get("path") or shot.get("clip"),
        "capture_time": shot.get("capture_time") or photo.get("creation_time"),
        "subject_position": shot.get("subject_position"),
        "tags": list(shot.get("tags") or []),
        "score": shot.get("score"),
        "summary": shot.get("summary"),
        "hero": shot.get("hero"),
        "similar_to": list(shot.get("similar_to") or []),
        "window_in_s": 0.0,
        "window_out_s": 0.04,
        "landing_at_s": None,
        "landing_hold_key": None,
        "keep_whole": False,
        "target_s": PHOTO_DEFAULT_S,
        "role": shot.get("role") or "hold",
        "motion_label": shot.get("motion_label") or "static",
        "description": shot.get("description"),
    }


def build_shot_facts(
    workspace,
    shotlog,
    important_max_s=DEFAULT_IMPORTANT_MAX_S,
    drop_reasons=None,
    slot_min_s=MIN_SHOT_LENGTH_S,
    slot_max_s=MAX_SLOT_S,
):
    clips = order_clips(load_ingest(workspace))
    first_moment = clips[0][1]
    last_clip, last_moment = clips[-1]
    span_end = last_moment + timedelta(seconds=last_clip["duration_s"])
    span_seconds = max(1e-9, (span_end - first_moment).total_seconds())
    content_offsets = []
    running = 0.0
    for clip, _ in clips:
        content_offsets.append(running)
        running += clip.get("duration_s") or 0.0
    facts = {}
    warnings = []
    beats_by_segment = beats_by_segment_from_shotlog(shotlog)
    for (clip, moment), content_offset in zip(clips, content_offsets):
        stem = Path(clip["file"]).stem
        quality = load_json(workspace / "quality" / f"{stem}.json")
        if quality is None:
            warnings.append(f"missing quality document for {clip['file']}")
            continue
        motion = load_json(workspace / "motion" / f"{stem}.json")
        position = clip_position(moment, first_moment, span_seconds)
        candidates = build_candidates(
            clip, moment, position, content_offset, quality, motion, MIN_SHOT_LENGTH_S,
            beats_by_segment=beats_by_segment,
        )
        scored = []
        for candidate in candidates:
            lookup_key = candidate.get("shot_key") or candidate_key(candidate)
            shot = shotlog.get(lookup_key)
            if shot is None or not shot.get("eligible", True):
                continue
            candidate["score"] = shot["score"]
            candidate["role"] = shot.get("role") or candidate["role"]
            candidate["motion_label"] = shot.get("motion_label") or candidate["motion_label"]
            candidate["description"] = shot.get("description")
            candidate["shot_reason"] = shot.get("reason")
            candidate["shot_key"] = shot.get("key")
            candidate["similar_to"] = list(shot.get("similar_to") or [])
            candidate["hero"] = shot.get("hero")
            candidate["semantic_motion"] = shot.get("semantic_motion")
            candidate["summary"] = shot.get("summary")
            candidate["peak_s"], candidate["peak_source"] = choose_peak(
                candidate["in_s"], candidate["out_s"], shot.get("peak_s"), (motion or {}).get("scene_activity"),
            )
            scored.append(candidate)
        scored = build_phrases(scored, quality)
        for candidate in dedupe_candidates(scored, drop_reasons):
            key = candidate.get("shot_key")
            if key:
                facts[key] = make_fact(
                    candidate, important_max_s, slot_min_s, slot_max_s, events=(motion or {}).get("events"),
                )
    photos_by_name = {
        photo.get("file") or Path(photo["path"]).name: photo
        for photo in load_ingest_photos(workspace)
    }
    for shot in shotlog.values():
        if shot.get("media_type") != "photo":
            continue
        if not shot.get("eligible", True):
            continue
        if (shot.get("role") or "hold") in SKIP_ROLES:
            continue
        key = shot.get("key")
        if not key:
            continue
        name = shot.get("clip") or ""
        facts[key] = make_photo_fact(shot, photos_by_name.get(name) or {})
    return facts, clips, warnings


def fact_peak(fact):
    """The shot's best moment: its ``peak_s``, else the middle of its window."""
    if fact.get("peak_s") is not None:
        return float(fact["peak_s"])
    return (float(fact["window_in_s"]) + float(fact["window_out_s"])) / 2.0


def landing_floor(fact):
    """The earliest end of a cut that lands on a hold, or None."""
    landing = fact.get("landing_at_s")
    return None if landing is None else float(landing) + LANDING_FLOOR_S


def expected_cut(fact, length_s):
    """The ``(in_s, out_s)`` of a ``length_s`` cut centred on the shot's peak, inside its window."""
    window_in = float(fact["window_in_s"])
    window_out = float(fact["window_out_s"])
    length = min(float(length_s), window_out - window_in)
    return centre_window(window_in, window_out, length, fact_peak(fact), landing_floor(fact))
