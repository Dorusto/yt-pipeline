"""Shared overlap math for time spans (shots, Shorts candidates, graphics entries)."""


def overlap_seconds(a_start, a_end, b_start, b_end):
    """Seconds shared by two spans, 0.0 when they do not overlap."""
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def overlap_fraction(a_start, a_end, b_start, b_end):
    """Overlap of two spans as a fraction of the shorter one (0.0 for a zero-length span)."""
    overlap_s = overlap_seconds(a_start, a_end, b_start, b_end)
    shorter = min(a_end - a_start, b_end - b_start)
    return overlap_s / shorter if shorter > 0 else 0.0


def overlap_bounds(a_start, a_end, b_start, b_end):
    """Intersection (start, end) of two spans; end <= start when they do not overlap."""
    return max(a_start, b_start), min(a_end, b_end)
