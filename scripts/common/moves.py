# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shot length policy for camera moves versus static shots.

A static shot respects the pacing table's ``slot_max``. A camera move (pan,
tilt, orbit, tracking, reveal, ...) keeps as much of the move as the length
rule allows, and a small share of the slots (at most 15 % rounded, at least
one) may run past ``slot_max`` on screen, up to the hero cap. Playback is real
time by default. The optional speed ramp (``speed_ramp=True``) lets the middle
half of a move's source window play up to ``MIDDLE_SPEED_MAX`` times faster so
more of the move fits; the first and last quarters always play in real time.

When a window has to be shortened, the part kept is centred on the slot's
``peak_s`` (see ``common/peak.py``), inside the free part of its window and
never ending before a landing floor. A slot without ``peak_s`` keeps the
legacy anchoring: the end for important shots and landings, else the start.

``edit.json`` stores the ramp as the optional slot field ``middle_speed``; the
on-screen length of a slot is ``0.25 L + 0.5 L / s + 0.25 L`` for a source
window of ``L`` seconds. Every tool that needs a slot's on-screen length goes
through :func:`slot_screen_length`.
"""

import math

from common.peak import centre_window

MIDDLE_SPEED_MAX = 1.2
MIDDLE_SPEED_SCHEMA_MAX = 1.25
EXCEPTION_SHARE = 0.15
STAR_SHARE = 0.4
STAR_MIN_SCORE = 0.85
MOVE_ROLES = {"move", "transition_in", "transition_out"}
STATIC_LABELS = {"static", "minor_motion"}
MIN_GAIN_S = 0.05
EPSILON = 1e-9


def is_star(candidate):
    """True for a shot the edit should not lose to time coverage: a hero or a very high score."""
    return bool(candidate.get("hero")) or float(candidate.get("score") or 0.0) >= STAR_MIN_SCORE


def middle_speed_of(slot):
    """The slot's ``middle_speed``, 1.0 when it has none."""
    value = slot.get("middle_speed")
    if value is None:
        return 1.0
    return max(1.0, float(value))


def screen_factor(middle_speed):
    """On-screen seconds per source second: ``0.5 + 0.5 / s``."""
    return 0.5 + 0.5 / max(1.0, float(middle_speed))


def screen_length(source_length_s, middle_speed=1.0):
    """On-screen length of a source window whose middle half plays at ``middle_speed``."""
    length = max(0.0, float(source_length_s))
    return 0.25 * length + 0.5 * length / max(1.0, float(middle_speed)) + 0.25 * length


def source_length_for(screen_budget_s, middle_speed=MIDDLE_SPEED_MAX):
    """The longest source window that fits ``screen_budget_s`` on screen at ``middle_speed``."""
    return max(0.0, float(screen_budget_s)) / screen_factor(middle_speed)


def is_photo_slot(slot):
    return slot.get("kind") == "photo" or slot.get("media_type") == "photo"


def slot_screen_length(slot):
    """The on-screen length of a slot, photo or video, ramp included."""
    if is_photo_slot(slot):
        return float(slot.get("duration_s") or 0.0)
    return screen_length(float(slot["out_s"]) - float(slot["in_s"]), middle_speed_of(slot))


def is_move_slot(slot):
    """True for a slot that holds a camera move: a move role or an important shot, never a static label."""
    if is_photo_slot(slot):
        return False
    if slot.get("motion_label") in STATIC_LABELS:
        return False
    return slot.get("role") in MOVE_ROLES or bool(slot.get("important"))


def _set_speed(slot, speed):
    if speed > 1.0 + EPSILON:
        slot["middle_speed"] = round(speed, 2)
    else:
        slot.pop("middle_speed", None)


def _free_range(slots, index):
    """The part of the slot's window that no other slot of the same take uses."""
    slot = slots[index]
    in_s = float(slot["in_s"])
    out_s = float(slot["out_s"])
    low = min(float(slot.get("window_in_s", in_s)), in_s)
    high = max(float(slot.get("window_out_s", out_s)), out_s)
    if slot.get("landing_floor_s") is not None:
        high = max(float(slot["landing_floor_s"]), out_s)
    for other_index, other in enumerate(slots):
        if other_index == index or is_photo_slot(other) or other.get("clip") != slot.get("clip"):
            continue
        other_in = float(other["in_s"])
        other_out = float(other["out_s"])
        if other_out <= in_s + EPSILON:
            low = max(low, other_out)
        elif other_in >= out_s - EPSILON:
            high = min(high, other_in)
    return low, high


def _place(slot, low, high, length):
    """The ``(in_s, out_s)`` of a ``length``-second cut inside ``[low, high]``.

    Centred on the slot's ``peak_s`` when it has one (a landing floor still
    forces the end), else anchored at the end for important shots and landings
    and at the start for ordinary moves.
    """
    floor = slot.get("landing_floor_s")
    peak = slot.get("peak_s")
    if peak is not None:
        return centre_window(low, high, length, peak, min_out_s=floor)
    if slot.get("important") or floor is not None:
        return high - length, high
    return low, low + length


def _fit(slot, low, high, budget, speed_ramp):
    """The ``(in_s, out_s, middle_speed)`` that keeps most of ``[low, high]`` within ``budget`` on screen."""
    span = high - low
    if span <= EPSILON:
        return float(slot["in_s"]), float(slot["out_s"]), 1.0
    if span <= budget + EPSILON:
        return low, high, 1.0
    top_speed = MIDDLE_SPEED_MAX if speed_ramp else 1.0
    if speed_ramp and span <= source_length_for(budget, top_speed) + EPSILON:
        needed = 0.5 / (budget / span - 0.5)
        speed = min(MIDDLE_SPEED_MAX, math.ceil(needed * 100 - 1e-9) / 100)
        return low, high, max(1.0, speed)
    length = math.floor(source_length_for(budget, top_speed) * 1000 + 1e-6) / 1000
    in_s, out_s = _place(slot, low, high, length)
    return in_s, out_s, top_speed


def _clamp_static(slot, slot_max, low, high):
    """Trim a non-move video slot to ``slot_max`` of source at real time, around its peak."""
    in_s = float(slot["in_s"])
    out_s = float(slot["out_s"])
    if out_s - in_s > slot_max + EPSILON:
        if slot.get("peak_s") is not None:
            in_s, out_s = centre_window(
                min(low, in_s), max(high, out_s), slot_max, slot["peak_s"], slot.get("landing_floor_s")
            )
        elif slot.get("important"):
            in_s = out_s - slot_max
        else:
            out_s = in_s + slot_max
        slot["in_s"] = round(in_s, 3)
        slot["out_s"] = round(out_s, 3)
    _set_speed(slot, 1.0)


def _apply(slot, in_s, out_s, speed):
    slot["in_s"] = round(in_s, 3)
    slot["out_s"] = round(out_s, 3)
    _set_speed(slot, speed)


def apply_move_policy(slots, pacing, exception_share=EXCEPTION_SHARE, speed_ramp=False):
    """Apply the move-versus-static length policy to ``slots`` in place; return warnings.

    ``slots`` are internal slot dicts with ``in_s``/``out_s``, the candidate
    window ``window_in_s``/``window_out_s``, ``role``, ``motion_label``,
    ``important``, ``score`` and ``clip`` (photos carry ``duration_s``). A move
    keeps as much of its window as fits its on-screen budget, centred on its
    ``peak_s`` when it has one; with ``speed_ramp`` it may also use
    ``middle_speed`` up to 1.2 when that lets more of the move in, without it
    every ``middle_speed`` stays 1.0. At most
    ``exception_share * len(slots)`` rounded (minimum 1; none when the share is 0) moves, the best by score
    among those that want more than ``slot_max``, may run up to ``hero_max`` on
    screen. Every other slot, every static or hold slot and every photo stays
    within ``slot_max``. Each slot gets ``max_screen_s``, the cap beat snapping
    must keep.
    """
    warnings = []
    if not slots:
        return warnings
    slot_max = float(pacing["slot_max"])
    hero_max = max(float(pacing["hero_max"]), slot_max)
    allowed = max(1, int(exception_share * len(slots) + 0.5)) if exception_share > 0 else 0

    wanting = []
    for index, slot in enumerate(slots):
        if not is_move_slot(slot):
            continue
        low, high = _free_range(slots, index)
        in_s, out_s, speed = _fit(slot, low, high, hero_max, speed_ramp)
        if screen_length(out_s - in_s, speed) > slot_max + MIN_GAIN_S:
            wanting.append((float(slot.get("score") or 0.0), -index, index))
    wanting.sort(reverse=True)
    exceptions = {index for _, _, index in wanting[:allowed]}

    for index, slot in enumerate(slots):
        if is_photo_slot(slot):
            duration = float(slot.get("duration_s") or 0.0)
            if duration > slot_max + EPSILON:
                slot["duration_s"] = round(slot_max, 3)
                slot["out_s"] = slot["duration_s"]
            slot["max_screen_s"] = slot_max
            continue
        if not is_move_slot(slot):
            low, high = _free_range(slots, index)
            _clamp_static(slot, slot_max, low, high)
            slot["max_screen_s"] = slot_max
            continue
        low, high = _free_range(slots, index)
        if index in exceptions:
            in_s, out_s, speed = _fit(slot, low, high, hero_max, speed_ramp)
            _apply(slot, in_s, out_s, speed)
            slot["max_screen_s"] = hero_max
            warnings.append(
                f"exception: {slot.get('shot_key') or slot.get('id') or slot.get('clip')} runs "
                f"{slot_screen_length(slot):.1f} s on screen (slot_max {slot_max:.1f} s)"
                + (f", middle at {speed:.2f}x" if speed > 1.0 else "")
            )
            continue
        budget = min(slot_screen_length(slot), slot_max)
        in_s, out_s, speed = _fit(slot, low, high, budget, speed_ramp)
        _apply(slot, in_s, out_s, speed)
        slot["max_screen_s"] = slot_max
    return warnings


SAME_TAKE_CONTIGUOUS_S = 0.5
SAME_TAKE_MIN_GAP_S = 8.0


def is_moving(role, motion_label):
    """True for a camera move: a move role with a label that is not static."""
    return role in MOVE_ROLES and motion_label not in STATIC_LABELS


def same_take_gap(first, second):
    """Seconds of source skipped between two cuts of one take; 0 when they overlap or go back."""
    gap = float(second["in_s"]) - float(first["out_s"])
    return max(0.0, gap)


def is_jump_cut(first, second, first_moving, second_moving):
    """True when cutting from ``first`` to ``second`` reads as a jump cut.

    Two cuts of the same take are fine when they touch in source time (one
    continuous shot) or when both are camera moves at least
    ``SAME_TAKE_MIN_GAP_S`` apart, forward in time: the moving camera has
    reached a different place, so the cut reads as an ellipsis (a boat's run
    into a tunnel, then its exit). Static framings of one take always jump.
    """
    if first.get("clip") is None or first.get("clip") != second.get("clip"):
        return False
    if abs(float(first["out_s"]) - float(second["in_s"])) <= SAME_TAKE_CONTIGUOUS_S:
        return False
    if not (first_moving and second_moving):
        return True
    return same_take_gap(first, second) < SAME_TAKE_MIN_GAP_S
