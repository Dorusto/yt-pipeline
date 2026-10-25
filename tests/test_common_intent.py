import common.intent as intent


def test_is_deliberate_shot_for_a_deliberate_move():
    assert intent.is_deliberate_shot("pan_left", None) is True


def test_is_deliberate_shot_for_a_semantic_orbit():
    assert intent.is_deliberate_shot("static", "orbit") is True


def test_is_deliberate_shot_false_for_a_static_hold():
    assert intent.is_deliberate_shot("static", None) is False
