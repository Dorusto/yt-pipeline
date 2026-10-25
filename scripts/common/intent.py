# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared camera-move intent vocabulary.

Intent and defect are separate fields (shot-grammar 1): motion blur during a
whip pan is intent, motion blur on a static shot is a defect. This module holds
the shared intent vocabulary the quality, index and director tools agree on.
"""

DELIBERATE_MOVE_LABELS = frozenset({
    "orbit_candidate", "pan_left", "pan_right", "tilt_up", "tilt_down",
    "dolly_in", "dolly_out", "travel_forward", "travel_backward",
    "truck_left", "truck_right",
})
WHIP_SHORT_HOLD_SCORE = 0.1
DELIBERATE_SHAKE_FACTOR = 2.0


def is_deliberate_move(label):
    """True for a camera label that is a deliberate move, not a static hold."""
    return label in DELIBERATE_MOVE_LABELS


def is_whip_short_hold(segment):
    """True when the segment carries the whip short-hold mark."""
    return bool(segment) and segment.get("whip") == "short_hold"
