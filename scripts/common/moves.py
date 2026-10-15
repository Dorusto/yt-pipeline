"""Shot length policy for camera moves versus static shots.

A static shot respects the pacing table's ``slot_max``. A camera move (pan,
tilt, orbit, tracking, reveal, ...) keeps as much of the move as the length
rule allows: the middle half of its source window may play up to
``MIDDLE_SPEED_MAX`` times faster, and a small share of the slots (at most
``ceil(15%)``, at least one) may run past ``slot_max`` on screen, up to the
hero cap. The first and last quarters of a slot always play in real time.

``edit.json`` stores the ramp as the optional slot field ``middle_speed``; the
on-screen length of a slot is ``0.25 L + 0.5 L / s + 0.25 L`` for a source
window of ``L`` seconds. Every tool that needs a slot's on-screen length goes
through :func:`slot_screen_length`.
"""

import math

MIDDLE_SPEED_MAX = 1.2
MIDDLE_SPEED_SCHEMA_MAX = 1.25
EXCEPTION_SHARE = 0.15
MOVE_ROLES = {"move", "transition_in", "transition_out"}
STATIC_LABELS = {"static", "minor_motion"}
MIN_GAIN_S = 0.05
EPSILON = 1e-9


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


def _fit(slot, low, high, budget):
    """The ``(in_s, out_s, middle_speed)`` that keeps most of ``[low, high]`` within ``budget`` on screen."""
    span = high - low
    if span <= EPSILON:
        return float(slot["in_s"]), float(slot["out_s"]), 1.0
    if span <= budget + EPSILON:
        return low, high, 1.0
    if span <= source_length_for(budget) + EPSILON:
        needed = 0.5 / (budget / span - 0.5)
        speed = min(MIDDLE_SPEED_MAX, math.ceil(needed * 100 - 1e-9) / 100)
        return low, high, max(1.0, speed)
    length = math.floor(source_length_for(budget) * 1000 + 1e-6) / 1000
    floor = slot.get("landing_floor_s")
    if slot.get("important") or floor is not None:
        return high - length, high, MIDDLE_SPEED_MAX
    return low, low + length, MIDDLE_SPEED_MAX


def _clamp_static(slot, slot_max):
    """Trim a non-move video slot to ``slot_max`` of source at real time."""
    in_s = float(slot["in_s"])
    out_s = float(slot["out_s"])
    if out_s - in_s > slot_max + EPSILON:
        if slot.get("important"):
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


def apply_move_policy(slots, pacing, exception_share=EXCEPTION_SHARE):
    """Apply the move-versus-static length policy to ``slots`` in place; return warnings.

    ``slots`` are internal slot dicts with ``in_s``/``out_s``, the candidate
    window ``window_in_s``/``window_out_s``, ``role``, ``motion_label``,
    ``important``, ``score`` and ``clip`` (photos carry ``duration_s``). A move
    keeps as much of its window as fits its on-screen budget, using
    ``middle_speed`` up to 1.2 only when that lets more of the move in. At most
    ``ceil(exception_share * len(slots))`` (minimum 1; none when the share is 0) moves, the best by score
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
    allowed = max(1, math.ceil(round(exception_share * len(slots), 9))) if exception_share > 0 else 0

    wanting = []
    for index, slot in enumerate(slots):
        if not is_move_slot(slot):
            continue
        low, high = _free_range(slots, index)
        in_s, out_s, speed = _fit(slot, low, high, hero_max)
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
            _clamp_static(slot, slot_max)
            slot["max_screen_s"] = slot_max
            continue
        low, high = _free_range(slots, index)
        if index in exceptions:
            in_s, out_s, speed = _fit(slot, low, high, hero_max)
            _apply(slot, in_s, out_s, speed)
            slot["max_screen_s"] = hero_max
            warnings.append(
                f"exception: {slot.get('shot_key') or slot.get('id') or slot.get('clip')} runs "
                f"{slot_screen_length(slot):.1f} s on screen (slot_max {slot_max:.1f} s)"
                + (f", middle at {speed:.2f}x" if speed > 1.0 else "")
            )
            continue
        budget = min(slot_screen_length(slot), slot_max)
        in_s, out_s, speed = _fit(slot, low, high, budget)
        _apply(slot, in_s, out_s, speed)
        slot["max_screen_s"] = slot_max
    return warnings
