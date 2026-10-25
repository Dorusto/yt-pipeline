from common.scoring import counts_as_defect, label_score


def test_counts_as_defect_waives_shake_on_a_deliberate_move():
    assert counts_as_defect("shake", True) is False
    assert counts_as_defect("shake", False) is True
    assert counts_as_defect("blur", True) is True


def test_base_plus_confidence_only():
    assert label_score("hold", 0.7, "static", []) == 0.55 + 0.25 * 0.7


def test_unsteady_levels_subtract():
    base = label_score("hold", 0.7, None, [])
    assert label_score("hold", 0.7, "unsteady", []) == base - 0.10
    assert label_score("hold", 0.7, "very_unsteady", []) == base - 0.20


def test_stable_steadiness_adds_nothing():
    base = label_score("hold", 0.7, None, [])
    assert label_score("hold", 0.7, "no_shaking", []) == base
    assert label_score("hold", 0.7, "static", []) == base


def test_clean_segment_gets_no_bonus():
    assert label_score("hold", 0.7, "static", []) == 0.55 + 0.25 * 0.7


def test_defect_subtracts():
    assert label_score("hold", 0.7, "static", [{"name": "blur", "value": 0.3}]) == 0.55 + 0.25 * 0.7 - 0.3


def test_defect_penalty_is_capped():
    assert label_score("hold", 0.7, "static", [{"name": "blur", "value": 0.9}]) == 0.55 + 0.25 * 0.7 - 0.4


def test_horizon_defect_is_weighted_half():
    assert label_score("hold", 0.7, "static", [{"name": "horizon", "value": 0.4}]) == 0.55 + 0.25 * 0.7 - 0.2


def test_whip_short_hold_returns_the_low_score():
    assert label_score("move", 0.9, "no_shaking", [], whip="short_hold") == 0.1


def test_deliberate_drops_the_shake_defect():
    with_shake = label_score("move", 0.8, "no_shaking", [{"name": "shake", "value": 0.5}], deliberate=True)
    clean = label_score("move", 0.8, "no_shaking", [], deliberate=True)
    assert with_shake == clean


def test_deliberate_keeps_other_defects():
    with_blur = label_score("move", 0.8, "no_shaking", [{"name": "blur", "value": 0.3}], deliberate=True)
    assert with_blur == 0.50 + 0.25 * 0.8 - 0.3


def test_score_is_clamped_to_zero():
    assert label_score("transition_in", 0.0, "very_unsteady", [{"name": "blur", "value": 0.9}]) == 0.0
