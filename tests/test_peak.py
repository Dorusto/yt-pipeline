import pytest

from common.peak import (
    PEAK_MIDDLE,
    PEAK_MOTION,
    PEAK_VISION,
    centre_window,
    choose_peak,
    is_centred,
    is_trimmed,
    motion_peak,
    peak_in_cut,
    valid_vision_peak,
)


def activity(values, start=0.5):
    return [{"t_s": start + index, "activity": value} for index, value in enumerate(values)]


FLAT = activity([0.002] * 20)
SPIKE = activity([0.002] * 6 + [0.004, 0.012, 0.004] + [0.002] * 11)


def test_valid_vision_peak_accepts_values_inside_the_segment_only():
    assert valid_vision_peak(12.4, 10.0, 20.0) == 12.4
    assert valid_vision_peak(10.0, 10.0, 20.0) == 10.0
    assert valid_vision_peak(20.0, 10.0, 20.0) == 20.0
    assert valid_vision_peak(9.9, 10.0, 20.0) is None
    assert valid_vision_peak(20.1, 10.0, 20.0) is None
    assert valid_vision_peak("12", 10.0, 20.0) is None
    assert valid_vision_peak(None, 10.0, 20.0) is None
    assert valid_vision_peak(True, 0.0, 20.0) is None


def test_vision_peak_wins_over_motion_and_middle():
    peak, source = choose_peak(0.0, 20.0, vision_peak_s=4.0, scene_activity=SPIKE)
    assert (peak, source) == (4.0, PEAK_VISION)


def test_motion_peak_is_the_second_of_maximum_scene_activity():
    peak, source = choose_peak(0.0, 20.0, scene_activity=SPIKE)
    assert source == PEAK_MOTION
    assert peak == 7.5


def test_motion_peak_only_looks_inside_the_window():
    peak, source = choose_peak(0.0, 5.0, scene_activity=SPIKE)
    assert (peak, source) == (2.5, PEAK_MIDDLE)
    peak, source = choose_peak(6.0, 10.0, scene_activity=SPIKE)
    assert (peak, source) == (7.5, PEAK_MOTION)


def test_flat_activity_falls_back_to_the_middle():
    assert choose_peak(2.0, 10.0, scene_activity=FLAT) == (6.0, PEAK_MIDDLE)
    assert choose_peak(2.0, 10.0, scene_activity=[]) == (6.0, PEAK_MIDDLE)
    assert choose_peak(2.0, 10.0, scene_activity=None) == (6.0, PEAK_MIDDLE)


def test_motion_ties_go_to_the_bin_closest_to_the_window_middle():
    series = activity([0.002] * 3 + [0.02] * 7 + [0.002] * 10)
    peak = motion_peak(series, 0.0, 20.0)
    assert peak is not None and 4.5 <= peak <= 8.5
    narrow = motion_peak(series, 3.0, 9.0)
    assert narrow is not None and abs(narrow - 6.0) <= 1.0


def test_motion_peak_rejects_malformed_rows():
    rows = [{"t_s": "a", "activity": 1}, {"t_s": 1.5}, "x"] + activity([0.001, 0.001, 0.05, 0.001, 0.001, 0.001])
    assert motion_peak(rows, 0.0, 10.0) == 2.5


def test_vision_peak_outside_the_window_is_clamped_to_the_nearest_edge():
    assert choose_peak(10.0, 14.0, vision_peak_s=3.0) == (10.0, PEAK_VISION)
    assert choose_peak(10.0, 14.0, vision_peak_s=30.0) == (14.0, PEAK_VISION)


def test_centre_window_centres_on_the_peak():
    assert centre_window(0.0, 20.0, 4.0, 10.0) == (8.0, 12.0)
    assert centre_window(0.0, 20.0, 3.0, 7.3) == (5.8, 8.8)


def test_centre_window_stays_inside_the_window_at_both_edges():
    assert centre_window(5.0, 20.0, 4.0, 5.5) == (5.0, 9.0)
    assert centre_window(5.0, 20.0, 4.0, 19.8) == (16.0, 20.0)


def test_centre_window_never_longer_than_the_window():
    assert centre_window(4.0, 6.0, 9.0, 5.0) == (4.0, 6.0)


def test_landing_floor_wins_over_the_peak():
    in_s, out_s = centre_window(0.0, 20.0, 2.0, 5.0, min_out_s=10.75)
    assert out_s == 10.75 and in_s == 8.75
    assert not peak_in_cut(in_s, out_s, 5.0)


def test_landing_floor_keeps_the_peak_when_it_is_close_enough():
    in_s, out_s = centre_window(0.0, 20.0, 2.0, 9.5, min_out_s=10.75)
    assert out_s == 10.75
    assert peak_in_cut(in_s, out_s, 9.5)


def test_landing_floor_beyond_the_window_is_limited_to_the_window_end():
    assert centre_window(0.0, 10.0, 2.0, 1.0, min_out_s=12.0) == (8.0, 10.0)


def test_floor_is_ignored_when_the_centred_cut_already_ends_after_it():
    assert centre_window(0.0, 20.0, 2.0, 15.0, min_out_s=10.75) == (14.0, 16.0)


def test_is_centred_needs_a_trimmed_cut_that_holds_the_peak():
    assert is_centred(8.0, 12.0, 0.0, 20.0, 10.0)
    assert not is_centred(0.0, 20.0, 0.0, 20.0, 10.0)
    assert not is_centred(0.0, 4.0, 0.0, 20.0, 10.0)
    assert is_trimmed(0.0, 4.0, 0.0, 20.0)
    assert not is_trimmed(0.0, 19.98, 0.0, 20.0)
