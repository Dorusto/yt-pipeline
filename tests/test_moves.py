import math

import pytest

from common.moves import (
    MIDDLE_SPEED_MAX,
    apply_move_policy,
    is_move_slot,
    screen_length,
    slot_screen_length,
    source_length_for,
)

PACING = {"slot_min": 2.0, "slot_target": 3.6, "slot_max": 5.0, "hero_max": 6.0}


def video_slot(name, in_s, out_s, window=None, role="move", label="pan_right", score=0.5, important=False, **extra):
    window_in, window_out = window or (in_s, out_s)
    slot = {
        "id": name,
        "clip": f"{name}.mp4",
        "in_s": in_s,
        "out_s": out_s,
        "window_in_s": window_in,
        "window_out_s": window_out,
        "role": role,
        "motion_label": label,
        "score": score,
        "important": important,
    }
    slot.update(extra)
    return slot


def photo_slot(name, duration):
    return {"id": name, "clip": f"{name}.jpg", "media_type": "photo", "duration_s": duration, "in_s": 0.0, "out_s": duration}


def test_screen_length_without_ramp_is_the_source_length():
    assert screen_length(4.0) == pytest.approx(4.0)
    assert screen_length(4.0, 1.0) == pytest.approx(4.0)


def test_screen_length_ramp_formula():
    assert screen_length(4.0, 1.2) == pytest.approx(0.25 * 4 + 0.5 * 4 / 1.2 + 0.25 * 4)
    assert screen_length(10.0, 1.25) == pytest.approx(9.0)


def test_source_length_for_is_the_inverse_of_screen_length():
    for budget in (1.5, 3.0, 5.0, 6.0):
        length = source_length_for(budget)
        assert screen_length(length, MIDDLE_SPEED_MAX) == pytest.approx(budget)
        assert length > budget


def test_slot_screen_length_reads_middle_speed_and_photos():
    assert slot_screen_length({"in_s": 1.0, "out_s": 5.0}) == pytest.approx(4.0)
    assert slot_screen_length({"in_s": 1.0, "out_s": 5.0, "middle_speed": 1.2}) == pytest.approx(screen_length(4.0, 1.2))
    assert slot_screen_length({"kind": "photo", "duration_s": 3.0}) == 3.0


def test_is_move_slot_by_role_label_and_importance():
    assert is_move_slot(video_slot("a", 0, 3))
    assert is_move_slot(video_slot("a", 0, 3, role="hold", label="orbit", important=True))
    assert not is_move_slot(video_slot("a", 0, 3, role="hold", label="static"))
    assert not is_move_slot(video_slot("a", 0, 3, label="static", important=True))
    assert not is_move_slot(video_slot("a", 0, 3, label="minor_motion"))
    assert not is_move_slot(photo_slot("p", 3.0))


def test_static_hold_is_clamped_to_slot_max():
    slots = [video_slot("a", 0.0, 8.0, role="hold", label="static")]
    apply_move_policy(slots, PACING)
    assert slot_screen_length(slots[0]) == pytest.approx(5.0)
    assert "middle_speed" not in slots[0]


def test_important_static_hold_keeps_the_end_and_respects_slot_max():
    slots = [video_slot("a", 0.0, 6.0, role="hold", label="static", important=True)]
    apply_move_policy(slots, PACING)
    assert slots[0]["out_s"] == 6.0
    assert slot_screen_length(slots[0]) == pytest.approx(5.0)


def test_photo_is_clamped_to_slot_max():
    slots = [photo_slot("p", 4.0)]
    apply_move_policy(slots, {"slot_max": 2.5, "hero_max": 3.5})
    assert slots[0]["duration_s"] == 2.5


def test_long_move_is_extended_to_the_whole_window_with_a_ramp():
    slots = [video_slot("a", 2.0, 5.6, window=(0.0, 6.2), important=True)]
    warnings = apply_move_policy(slots, PACING, speed_ramp=True)
    assert slots[0]["in_s"] == 0.0 and slots[0]["out_s"] == 6.2
    assert 1.0 < slots[0]["middle_speed"] <= MIDDLE_SPEED_MAX
    assert slot_screen_length(slots[0]) <= PACING["hero_max"] + 1e-6
    assert warnings and "exception" in warnings[0] and "middle at" in warnings[0]


def test_move_that_fits_the_hero_cap_at_real_time_needs_no_ramp():
    slots = [video_slot("a", 0.0, 4.0, window=(0.0, 5.8))]
    apply_move_policy(slots, PACING)
    assert slots[0]["in_s"] == 0.0
    assert slots[0]["out_s"] == pytest.approx(5.8)
    assert "middle_speed" not in slots[0]


def test_move_longer_than_the_ramp_can_save_keeps_the_end_and_caps_the_speed():
    slots = [video_slot("a", 14.0, 20.0, window=(0.0, 20.0), important=True)]
    apply_move_policy(slots, PACING, speed_ramp=True)
    assert slots[0]["middle_speed"] == MIDDLE_SPEED_MAX
    assert slots[0]["out_s"] == 20.0
    assert slot_screen_length(slots[0]) == pytest.approx(PACING["hero_max"], abs=0.01)


def test_extension_never_leaves_the_candidate_window():
    slots = [video_slot("a", 3.0, 5.0, window=(2.0, 5.5))]
    apply_move_policy(slots, PACING)
    assert slots[0]["in_s"] >= 2.0 - 1e-9
    assert slots[0]["out_s"] <= 5.5 + 1e-9


def test_extension_stays_clear_of_another_slot_of_the_same_take():
    slots = [
        video_slot("a", 2.0, 4.0, window=(0.0, 8.0), clip="take.mp4"),
        video_slot("b", 6.0, 8.0, window=(0.0, 8.0), clip="take.mp4", score=0.1),
    ]
    apply_move_policy(slots, PACING)
    assert slots[0]["out_s"] <= slots[1]["in_s"] + 1e-9


def over_slot_max(slots):
    return [slot for slot in slots if slot_screen_length(slot) > PACING["slot_max"] + 1e-6]


def test_exception_share_is_fifteen_percent_rounded_with_a_minimum_of_one():
    def run(count):
        slots = [
            video_slot(f"m{index}", 0.0, 3.0, window=(0.0, 12.0), important=True, score=0.9 - index * 0.01)
            for index in range(count)
        ]
        apply_move_policy(slots, PACING)
        return over_slot_max(slots)

    assert len(run(1)) == 1
    assert len(run(4)) == 1
    assert len(run(7)) == 1
    assert len(run(14)) == 2
    assert len(run(20)) == 3


def test_exceptions_go_to_the_best_scores():
    slots = [
        video_slot("low", 0.0, 3.0, window=(0.0, 12.0), important=True, score=0.2),
        video_slot("high", 0.0, 3.0, window=(0.0, 12.0), important=True, score=0.95),
        video_slot("mid", 0.0, 3.0, window=(0.0, 12.0), important=True, score=0.6),
        video_slot("x", 0.0, 3.0, role="hold", label="static"),
    ]
    apply_move_policy(slots, PACING)
    assert [slot["id"] for slot in over_slot_max(slots)] == ["high"]


def test_zero_share_disables_exceptions():
    slots = [video_slot("a", 0.0, 3.0, window=(0.0, 12.0), important=True)]
    apply_move_policy(slots, PACING, exception_share=0.0)
    assert slot_screen_length(slots[0]) <= PACING["slot_max"] + 1e-6


def test_ordinary_move_keeps_its_on_screen_budget_and_gains_source_from_the_ramp():
    slots = [video_slot("a", 0.0, 3.0, window=(0.0, 12.0), score=0.1), video_slot("h", 0.0, 3.0, role="hold", label="static")]
    slots += [video_slot(f"m{index}", 0.0, 3.0, window=(0.0, 12.0), score=0.9) for index in range(10)]
    apply_move_policy(slots, PACING, speed_ramp=True)
    ordinary = slots[0]
    assert slot_screen_length(ordinary) == pytest.approx(3.0, abs=0.01)
    assert ordinary["out_s"] - ordinary["in_s"] > 3.0
    assert ordinary["middle_speed"] == MIDDLE_SPEED_MAX


def test_every_slot_reports_its_cap():
    slots = [
        video_slot("a", 0.0, 3.0, window=(0.0, 12.0), important=True, score=0.9),
        video_slot("b", 0.0, 3.0, role="hold", label="static"),
        photo_slot("p", 3.0),
    ]
    apply_move_policy(slots, PACING)
    assert slots[0]["max_screen_s"] == PACING["hero_max"]
    assert slots[1]["max_screen_s"] == PACING["slot_max"]
    assert slots[2]["max_screen_s"] == PACING["slot_max"]


def test_policy_is_idempotent():
    slots = [
        video_slot("a", 0.0, 3.0, window=(0.0, 12.0), important=True),
        video_slot("b", 0.0, 4.0, role="hold", label="static"),
    ]
    apply_move_policy(slots, PACING)
    first = [dict(slot) for slot in slots]
    apply_move_policy(slots, PACING)
    assert slots == first


def test_empty_slots_return_no_warnings():
    assert apply_move_policy([], PACING) == []


def peak_slot(name, in_s, out_s, window, peak, **extra):
    return video_slot(name, in_s, out_s, window=window, peak_s=peak, **extra)


def test_speed_ramp_is_off_by_default_and_never_sets_middle_speed():
    slots = [peak_slot("a", 2.0, 5.6, (0.0, 20.0), 10.0, important=True)]
    apply_move_policy(slots, PACING)
    assert "middle_speed" not in slots[0]
    assert slot_screen_length(slots[0]) == pytest.approx(PACING["hero_max"], abs=0.01)


def test_ramp_off_keeps_the_length_policy_for_every_kind_of_slot():
    slots = [
        peak_slot(f"m{index}", 0.0, 3.0, (0.0, 12.0), 6.0, important=True, score=0.9 - index * 0.01)
        for index in range(14)
    ]
    slots.append(video_slot("h", 0.0, 8.0, role="hold", label="static", peak_s=4.0))
    slots.append(photo_slot("p", 6.0))
    apply_move_policy(slots, PACING)
    assert all("middle_speed" not in slot for slot in slots)
    long_slots = [slot for slot in slots if slot_screen_length(slot) > PACING["slot_max"] + 1e-6]
    assert len(long_slots) == 2
    assert all(slot_screen_length(slot) <= PACING["hero_max"] + 1e-6 for slot in long_slots)
    assert slot_screen_length(slots[-2]) == pytest.approx(PACING["slot_max"])
    assert slots[-1]["duration_s"] == PACING["slot_max"]


def test_a_long_move_is_cut_around_its_peak_not_its_start_or_end():
    slots = [peak_slot("a", 0.0, 20.0, (0.0, 20.0), 9.0, important=True)]
    apply_move_policy(slots, PACING)
    assert slots[0]["in_s"] == pytest.approx(6.0) and slots[0]["out_s"] == pytest.approx(12.0)


def test_peak_close_to_an_edge_keeps_the_cut_inside_the_window():
    early = [peak_slot("a", 0.0, 20.0, (0.0, 20.0), 0.5, important=True)]
    late = [peak_slot("a", 0.0, 20.0, (0.0, 20.0), 19.8, important=True)]
    apply_move_policy(early, PACING)
    apply_move_policy(late, PACING)
    assert early[0]["in_s"] == 0.0 and early[0]["out_s"] == pytest.approx(6.0)
    assert late[0]["out_s"] == 20.0 and late[0]["in_s"] == pytest.approx(14.0)


def test_ramp_on_cuts_more_of_the_move_still_centred_on_the_peak():
    slots = [peak_slot("a", 0.0, 20.0, (0.0, 20.0), 9.0, important=True)]
    apply_move_policy(slots, PACING, speed_ramp=True)
    slot = slots[0]
    assert slot["middle_speed"] == MIDDLE_SPEED_MAX
    assert slot_screen_length(slot) == pytest.approx(PACING["hero_max"], abs=0.01)
    assert (slot["in_s"] + slot["out_s"]) / 2 == pytest.approx(9.0, abs=0.01)
    assert slot["out_s"] - slot["in_s"] > PACING["hero_max"]


def test_landing_floor_still_wins_over_the_peak_in_the_policy():
    slot = peak_slot("a", 4.0, 10.75, (0.0, 20.0), 2.0, landing_floor_s=10.75)
    apply_move_policy([slot], {"slot_max": 3.0, "hero_max": 3.0})
    assert slot["out_s"] >= 10.75 - 1e-9
    assert slot["out_s"] - slot["in_s"] <= 3.0 + 1e-9


def test_static_hold_is_trimmed_around_its_peak_inside_the_candidate_window():
    slots = [video_slot("a", 0.0, 9.0, window=(0.0, 20.0), role="hold", label="static", peak_s=10.0)]
    apply_move_policy(slots, PACING)
    assert slots[0]["in_s"] == pytest.approx(7.5) and slots[0]["out_s"] == pytest.approx(12.5)


def test_static_hold_trim_stays_clear_of_another_slot_of_the_same_take():
    slots = [
        video_slot("a", 0.0, 9.0, window=(0.0, 20.0), role="hold", label="static", peak_s=10.0, clip="take.mp4"),
        video_slot("b", 9.5, 12.0, window=(0.0, 20.0), role="hold", label="static", peak_s=11.0, clip="take.mp4"),
    ]
    apply_move_policy(slots, PACING)
    assert slots[0]["out_s"] <= slots[1]["in_s"] + 1e-9


def test_a_slot_without_a_peak_keeps_the_legacy_anchoring():
    important = [video_slot("a", 14.0, 20.0, window=(0.0, 20.0), important=True)]
    ordinary = [video_slot("a", 0.0, 3.0, window=(0.0, 20.0), score=0.5)]
    apply_move_policy(important, PACING)
    apply_move_policy(ordinary, PACING)
    assert important[0]["out_s"] == 20.0
    assert ordinary[0]["in_s"] == 0.0
