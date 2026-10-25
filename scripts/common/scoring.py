# SPDX-License-Identifier: AGPL-3.0-or-later
"""The label score shared by the shotlog index and the rule-based director.

Steadiness and a defect-free segment no longer add points above the base; only
real defects and the two unsteady levels subtract (V030 lot c22, issue #119).
"""

from common.intent import WHIP_SHORT_HOLD_SCORE

ROLE_BASE = {"hold": 0.55, "move": 0.50, "transition_in": 0.35, "transition_out": 0.35}
STEADINESS_PENALTY = {"unsteady": 0.10, "very_unsteady": 0.20}
DEFECT_WEIGHTS = {"horizon": 0.5}
DEFAULT_DEFECT_WEIGHT = 1.0
MAX_DEFECT_PENALTY = 0.4


def label_score(role, confidence, steadiness, defects, whip=None, deliberate=False):
    """The label score for one segment: base + confidence, minus steadiness and defects.

    ``whip == "short_hold"`` returns ``WHIP_SHORT_HOLD_SCORE``. ``deliberate``
    drops the ``shake`` defect before the penalty (intent, not defect).
    """
    if whip == "short_hold":
        return WHIP_SHORT_HOLD_SCORE
    score = ROLE_BASE.get(role, 0.0)
    score += 0.25 * float(confidence or 0.0)
    score -= STEADINESS_PENALTY.get(steadiness, 0.0)
    scoring = [
        defect
        for defect in (defects or [])
        if not (deliberate and defect.get("name") == "shake")
    ]
    if scoring:
        penalty = sum(
            float(defect.get("value", 0.0))
            * DEFECT_WEIGHTS.get(defect.get("name"), DEFAULT_DEFECT_WEIGHT)
            for defect in scoring
        )
        score -= min(MAX_DEFECT_PENALTY, penalty)
    return max(0.0, min(1.0, score))
