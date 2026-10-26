# SPDX-License-Identifier: AGPL-3.0-or-later
"""Beat-grid helpers: load a beats.py grid and snap the timeline cuts onto it.

``snap_slots`` moves every cut to a downbeat, a half-bar beat or the nearest
beat, within the slot's window and length cap, absorbing the change into the
neighbouring slot when the end is pinned. Moved here from ``llm_director.py``
so the code editor and the LLM director share one implementation (VISION: no
tool imports another tool's code).
"""

from pathlib import Path

from common.constants import SNAP_MIN_SLOT_S
from common.facts import LANDING_FLOOR_S, keeps_whole
from common.jsonio import load_json
from common.moves import middle_speed_of, screen_factor, slot_duration

BEAT_SNAP_FRACTION = 0.5
PHOTO_MIN_S = 2.0
PHOTO_MAX_S = 4.0


class BeatsError(RuntimeError):
    """Raised when the beat grid cannot be read."""


def load_beats(path):
    """The beat grid from ``beats.py``: ``measurement`` nested or the keys at the top."""
    data = load_json(Path(path))
    if not isinstance(data, dict):
        raise BeatsError(f"unreadable beats file: {path}")
    measurement = data.get("measurement")
    source = measurement if isinstance(measurement, dict) else data
    beat_s = [float(value) for value in (source.get("beat_s") or [])]
    downbeat_s = [float(value) for value in (source.get("downbeat_s") or [])]
    period = source.get("beat_period_s")
    period = float(period) if period else derive_beat_period(beat_s)
    if not beat_s or period <= 0:
        raise BeatsError(f"beats file has no beat grid: {path}")
    return {"beat_s": beat_s, "downbeat_s": downbeat_s or list(beat_s), "beat_period_s": period}


def derive_beat_period(beat_s):
    """The median spacing of a beat list, for grids written without ``beat_period_s``."""
    if len(beat_s) < 2:
        return 0.0
    spacings = sorted(later - earlier for earlier, later in zip(beat_s, beat_s[1:]))
    return spacings[len(spacings) // 2]


def nearest_beat(value, times):
    if not times:
        return None
    return min(times, key=lambda moment: abs(moment - value))


def snap_warning(cut_number, end, target):
    if target is None:
        return f"cut {cut_number}: not snapped to a beat (no beat grid)"
    return f"cut {cut_number}: not snapped to a beat (nearest {abs(end - target):.1f} s away)"


def slot_bounds(slot, fact, cap=None):
    """The range a snap may move this slot's end within: photo length or video window.

    ``cap`` is the longest on-screen length the move policy allows the slot.
    """
    if fact is not None and fact.get("media_type") == "photo":
        return {"photo": True, "important": False, "cap": cap}
    if slot.get("kind") == "photo":
        return {"photo": True, "important": False, "cap": cap}
    low = float(slot["in_s"]) + SNAP_MIN_SLOT_S
    high = float(slot["out_s"])
    important = False
    window_in = float(slot["in_s"])
    if fact is not None:
        high = float(fact.get("window_out_s", slot["out_s"]))
        important = keeps_whole(fact)
        window_in = float(fact.get("window_in_s", slot["in_s"]))
        if fact.get("landing_at_s") is not None:
            low = max(low, float(fact["landing_at_s"]) + LANDING_FLOOR_S)
    return {"photo": False, "important": important, "low": low, "high": high, "window_in": window_in, "cap": cap}


def over_cap(slot, bounds, delta):
    """True when lengthening the slot on screen by ``delta`` would pass its policy cap."""
    cap = bounds.get("cap")
    if cap is None or delta <= 0:
        return False
    return slot_duration(slot) + delta > float(cap) + 1e-9


def source_delta(slot, delta):
    """The source-time change that moves the slot's on-screen length by ``delta``."""
    return delta / screen_factor(middle_speed_of(slot))


def _relaxed(bounds, slack):
    """The bounds of a slot with the beat-snap slack applied to a hero or semantic move."""
    low = float(bounds["low"])
    high = float(bounds["high"])
    window_in = float(bounds.get("window_in", low))
    if slack and bounds.get("important"):
        low -= slack
        high += slack
        window_in -= slack
    return low, high, window_in


def absorb_out(slot, bounds, delta, slack=0.0):
    """Move a slot's end by ``delta`` within its bounds; False when it cannot.

    ``slack`` lets a hero or semantic-move slot nudge its cut by up to half a
    beat past its window so the cut still lands on the music.
    """
    if over_cap(slot, bounds, delta):
        return False
    if bounds.get("photo"):
        target = float(slot["duration_s"]) + delta
        if not (PHOTO_MIN_S - 1e-9 <= target <= PHOTO_MAX_S + 1e-9):
            return False
        slot["duration_s"] = round(target, 3)
        return True
    low, high, _ = _relaxed(bounds, slack)
    target = float(slot["out_s"]) + source_delta(slot, delta)
    if not (low - 1e-9 <= target <= high + 1e-9):
        return False
    slot["out_s"] = round(target, 3)
    return True


def absorb_length(slot, bounds, delta, slack=0.0):
    """Change a slot's on-screen length by ``delta``, from its end or its head.

    The end first; when the end is pinned the head moves instead, never before
    the window's start. False when neither fits.
    """
    if absorb_out(slot, bounds, delta, slack):
        return True
    if bounds.get("photo"):
        return False
    low, high, window_in = _relaxed(bounds, slack)
    head_target = float(slot["in_s"]) - source_delta(slot, delta)
    if head_target < window_in - 1e-9:
        return False
    if float(slot["out_s"]) - head_target < SNAP_MIN_SLOT_S - 1e-9:
        return False
    slot["in_s"] = round(head_target, 3)
    return True


def absorb_head(slot, bounds, delta, slack=0.0):
    """Change a slot's length by ``delta`` from its head only; False when it cannot.

    The head move keeps the slot's end (and every cut after it) in place, which
    is what the pair absorb needs from the right slot.
    """
    if bounds.get("photo"):
        return absorb_out(slot, bounds, delta, slack)
    if over_cap(slot, bounds, delta):
        return False
    _, _, window_in = _relaxed(bounds, slack)
    head_target = float(slot["in_s"]) - source_delta(slot, delta)
    if head_target < window_in - 1e-9:
        return False
    if float(slot["out_s"]) - head_target < SNAP_MIN_SLOT_S - 1e-9:
        return False
    slot["in_s"] = round(head_target, 3)
    return True


def absorb_pair(left, left_bounds, right, right_bounds, delta, slack=0.0):
    """Move the cut between ``left`` and ``right`` by ``delta`` and nothing else.

    ``left`` grows by ``delta`` (from its end or its head) and ``right`` gives
    the same length back from its head, so only their shared boundary moves.
    Both must fit; on a partial fit the left change is restored exactly.
    """
    snapshot = {key: left[key] for key in ("in_s", "out_s", "duration_s") if key in left}
    if not absorb_length(left, left_bounds, delta, slack):
        return False
    if not absorb_head(right, right_bounds, -delta, slack):
        left.update(snapshot)
        return False
    return True


def snap_photo(slot, start, times, cap=None):
    """Put a photo's end on a beat that keeps its length in 2-4 s; False when none fits."""
    current = float(slot["duration_s"])
    longest = min(PHOTO_MAX_S, float(cap)) if cap is not None else PHOTO_MAX_S
    fitting = [
        moment
        for moment in times
        if PHOTO_MIN_S - 1e-9 <= moment - start <= longest + 1e-9
    ]
    if not fitting:
        return False
    target = min(fitting, key=lambda moment: abs(moment - (start + current)))
    slot["duration_s"] = round(target - start, 3)
    return True


def _cap_target(grid_name, times, end, start, cap, tolerance, beat_s):
    """The candidate target for one grid after the slot-cap fallback; returns grid, times, target, reach."""
    target = nearest_beat(end, times)
    reach = tolerance
    if target is not None and cap is not None and target - start > float(cap) + 1e-9:
        within = [moment for moment in times if moment - start <= float(cap) + 1e-9]
        if not within and times is not beat_s:
            grid_name = "beat"
            times = beat_s
            within = [moment for moment in beat_s if moment - start <= float(cap) + 1e-9]
        target = max(within) if within else None
        reach = 2 * tolerance
    return grid_name, times, target, reach


def snap_slots(slots, bounds, beat_grid, warnings, slot_min_s=None, downbeats_at=None):
    """Snap every timeline cut to a beat, preferring downbeats; return the summary counts.

    A downbeat or half-bar cut snaps when the target keeps the slot at least
    ``slot_min_s`` long (the pacing min); the slot's allowed length range is the
    reach, and the window and cap helpers still bound the move. The plain beat
    candidate keeps the half-beat tolerance. ``downbeats_at`` is an optional
    per-slot list: a slot whose entry is false tries only the beat grid.
    """
    beat_s = beat_grid["beat_s"]
    downbeat_s = beat_grid["downbeat_s"] or beat_s
    period = beat_grid["beat_period_s"]
    half_bar_s = [round(d + 2 * period, 4) for d in downbeat_s]
    tolerance = BEAT_SNAP_FRACTION * period
    snapped = 0
    unsnapped = 0
    downbeats = 0
    half_bar = 0
    start = 0.0
    for index, slot in enumerate(slots):
        if index > 0:
            slot["transition"] = {"type": "cut", "duration_s": 0}
        end = start + slot_duration(slot)
        cap = bounds[index].get("cap")
        chosen = None
        last_target = None
        grids = (("downbeat", downbeat_s), ("half_bar", half_bar_s), ("beat", beat_s))
        if downbeats_at is not None and not downbeats_at[index]:
            grids = (("beat", beat_s),)
        for grid_name, times in grids:
            used_grid, times, target, reach = _cap_target(grid_name, times, end, start, cap, tolerance, beat_s)
            last_target = target
            if target is None:
                continue
            if grid_name in ("downbeat", "half_bar"):
                if not bounds[index].get("photo") and slot_min_s is not None and target - start < slot_min_s - 1e-9:
                    continue
            elif abs(end - target) > reach:
                continue
            if bounds[index].get("photo"):
                if snap_photo(slot, start, times, cap):
                    chosen = used_grid
                    break
                continue
            delta = target - end
            if abs(delta) < 1e-9 or absorb_out(slot, bounds[index], delta, tolerance):
                chosen = used_grid
                break
            if index + 1 < len(slots):
                moved = absorb_pair(slot, bounds[index], slots[index + 1], bounds[index + 1], delta, tolerance)
            else:
                moved = absorb_length(slot, bounds[index], delta, tolerance)
            if moved:
                chosen = used_grid
                break
        if chosen is None:
            warnings.append(snap_warning(index + 1, end, last_target))
            unsnapped += 1
            slot["beat_grid"] = None
            start = end
            continue
        slot["beat_grid"] = chosen
        if chosen == "downbeat":
            downbeats += 1
        elif chosen == "half_bar":
            half_bar += 1
        snapped += 1
        start += slot_duration(slot)
    return {
        "beats": len(beat_s),
        "snapped": snapped,
        "unsnapped": unsnapped,
        "downbeats": downbeats,
        "half_bar": half_bar,
    }
