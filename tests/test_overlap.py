from common import overlap


def test_overlap_seconds_clamps_at_zero():
    assert overlap.overlap_seconds(0.0, 1.0, 2.0, 3.0) == 0.0
    assert overlap.overlap_seconds(0.0, 2.0, 1.0, 3.0) == 1.0
    assert overlap.overlap_seconds(1.0, 1.0, 0.0, 3.0) == 0.0


def test_overlap_fraction_over_the_shorter():
    assert overlap.overlap_fraction(0.0, 2.0, 1.0, 3.0) == 0.5
    assert overlap.overlap_fraction(0.0, 10.0, 2.0, 4.0) == 1.0
    assert overlap.overlap_fraction(0.0, 1.0, 2.0, 3.0) == 0.0


def test_overlap_fraction_zero_length_span_is_zero():
    assert overlap.overlap_fraction(1.0, 1.0, 0.0, 3.0) == 0.0
    assert overlap.overlap_fraction(0.0, 3.0, 1.0, 1.0) == 0.0


def test_overlap_bounds_intersection():
    assert overlap.overlap_bounds(0, 10, 4, 6) == (4, 6)
    assert overlap.overlap_bounds(0, 5, 5, 10) == (5, 5)


def test_overlap_bounds_disjoint_stays_disjoint():
    start, end = overlap.overlap_bounds(0, 5, 6, 10)
    assert end <= start
