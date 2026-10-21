# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared length helpers for the directors and the web app.

``estimate_all_good`` answers "how long would the edit be with every good
shot in it?" from a workspace shotlog: eligible, not suppressed, not a near
duplicate of a better shot, score at least ``min_score``; each shot counted at
the length a long cut would give it (static holds short, moves longer, heroes
longest, photos 3 s).
"""

SKIP_ROLES = {"accidental", "setup", "teardown"}
STATIC_LABELS = {"static", "minor_motion"}
IMPORTANT_SEMANTIC = {"orbit", "reveal", "tracking", "pov", "travel"}
IMPORTANT_LABELS = {"orbit", "orbit_candidate", "reveal", "tracking", "pov"}
HOLD_S = 4.0
MOVE_S = 5.0
HERO_S = 6.0
PHOTO_S = 3.0
MIN_SCORE = 0.5


def shot_length_in_long_cut(shot):
    """The on-screen length a long cut gives one shot, capped by its own window."""
    if str(shot.get("media_type") or "video") == "photo":
        return PHOTO_S
    window = max(0.0, float(shot.get("end_s") or 0.0) - float(shot.get("start_s") or 0.0))
    label = shot.get("motion_label")
    if shot.get("hero") or shot.get("semantic_motion") in IMPORTANT_SEMANTIC or label in IMPORTANT_LABELS:
        cap = HERO_S
    elif label in STATIC_LABELS or shot.get("role") == "hold":
        cap = HOLD_S
    else:
        cap = MOVE_S
    return min(window, cap)


def estimate_all_good(shotlog, min_score=MIN_SCORE):
    """Return ``{"seconds", "shots"}`` for an edit holding every good shot once."""
    shots = (shotlog or {}).get("shots") or []
    good = [
        shot
        for shot in shots
        if shot.get("eligible")
        and not shot.get("suppressed_by")
        and shot.get("role") not in SKIP_ROLES
        and float(shot.get("score") or 0.0) >= min_score
    ]
    good.sort(key=lambda shot: float(shot.get("score") or 0.0), reverse=True)
    kept = set()
    total = 0.0
    count = 0
    for shot in good:
        key = shot.get("key")
        if any(other in kept for other in shot.get("similar_to") or []):
            continue
        length = shot_length_in_long_cut(shot)
        if length <= 0:
            continue
        kept.add(key)
        total += length
        count += 1
    return {"seconds": round(total, 1), "shots": count}
