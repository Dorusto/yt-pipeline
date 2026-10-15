"""Best moment ("peak") of a shot and the cut window centred on it.

When a shot is longer than the length it may keep on screen, the part to keep
is the one around its best moment, not its start or its end: a push through a
gate looks best while it passes the gate. Both directors choose the peak and
centre the window through this module.

Peak sources, in priority order:

1. ``vision``  - ``peak_s`` returned by ``describe`` (the model saw a
   timestamped frame mosaic of the segment) and carried by the shotlog;
2. ``motion``  - the second of maximum scene activity inside the window, from
   the ``scene_activity`` series of ``motion.py`` (independent movement and
   parallax left after the camera motion is removed);
3. ``middle``  - the middle of the window.

A peak always lies inside the candidate window. A window never leaves the
candidate window; a landing floor (``min_out``) still wins over the peak.
"""

PEAK_VISION = "vision"
PEAK_MOTION = "motion"
PEAK_MIDDLE = "middle"
PEAK_SOURCES = (PEAK_VISION, PEAK_MOTION, PEAK_MIDDLE)
MOTION_SMOOTH_BINS = 3
MOTION_MIN_CONTRAST = 0.001
TOLERANCE_S = 0.05
EPSILON = 1e-9


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def clamp_into(value, low, high):
    """``value`` limited to ``[low, high]``."""
    return max(low, min(high, value))


def valid_vision_peak(peak_s, start_s, end_s):
    """The vision peak when it lies in ``[start_s, end_s]``, else None."""
    peak = _number(peak_s)
    if peak is None:
        return None
    if peak < float(start_s) - EPSILON or peak > float(end_s) + EPSILON:
        return None
    return round(peak, 3)


def _smoothed(values):
    half = MOTION_SMOOTH_BINS // 2
    result = []
    for index in range(len(values)):
        chunk = values[max(0, index - half) : index + half + 1]
        result.append(sum(chunk) / len(chunk))
    return result


def _median(values):
    ordered = sorted(values)
    count = len(ordered)
    if not count:
        return 0.0
    middle = count // 2
    if count % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def motion_peak(scene_activity, window_in_s, window_out_s):
    """The time of maximum scene activity inside the window, or None when the series is flat.

    ``scene_activity`` is the ``[{t_s, activity}]`` series of ``motion.py``
    (one row per second, ``t_s`` the bin centre). The series is smoothed over
    three bins; the maximum inside the window must stand out from the clip's
    median activity by ``MOTION_MIN_CONTRAST``, otherwise there is no peak.
    Ties go to the larger raw value, then to the bin closest to the window middle.
    """
    rows = []
    for row in scene_activity or []:
        if not isinstance(row, dict):
            continue
        t_s = _number(row.get("t_s"))
        activity = _number(row.get("activity"))
        if t_s is None or activity is None:
            continue
        rows.append((t_s, activity))
    if not rows:
        return None
    rows.sort()
    smoothed = _smoothed([activity for _, activity in rows])
    baseline = _median(smoothed)
    middle = (float(window_in_s) + float(window_out_s)) / 2.0
    best = None
    for (t_s, raw), value in zip(rows, smoothed):
        if t_s < float(window_in_s) - EPSILON or t_s > float(window_out_s) + EPSILON:
            continue
        key = (round(value, 12), raw, -abs(t_s - middle))
        if best is None or key > best[0]:
            best = (key, t_s, value)
    if best is None or best[2] - baseline < MOTION_MIN_CONTRAST:
        return None
    return round(clamp_into(best[1], float(window_in_s), float(window_out_s)), 3)


def choose_peak(window_in_s, window_out_s, vision_peak_s=None, scene_activity=None):
    """The ``(peak_s, source)`` of a candidate window: vision, then motion, then the middle.

    A vision peak outside the window is clamped to its nearest edge: the best
    moment of the segment is not in the cut range, the nearest steady part is.
    """
    low = float(window_in_s)
    high = float(window_out_s)
    vision = _number(vision_peak_s)
    if vision is not None:
        return round(clamp_into(vision, low, high), 3), PEAK_VISION
    motion = motion_peak(scene_activity, low, high)
    if motion is not None:
        return motion, PEAK_MOTION
    return round((low + high) / 2.0, 3), PEAK_MIDDLE


def centre_window(window_in_s, window_out_s, length_s, peak_s, min_out_s=None):
    """A cut of ``length_s`` seconds centred on ``peak_s`` and kept inside the window.

    The window is pushed back inside ``[window_in_s, window_out_s]`` when the
    centred cut would leave it. ``min_out_s`` (a landing floor) forces the cut
    to end at or after it, which may move the peak off-centre or out of the
    cut; the landing rule wins over the peak. Returns ``(in_s, out_s)``.
    """
    low = float(window_in_s)
    high = float(window_out_s)
    length = max(0.0, min(float(length_s), high - low))
    start = float(peak_s) - length / 2.0
    start = clamp_into(start, low, high - length)
    end = start + length
    if min_out_s is not None:
        floor = min(float(min_out_s), high)
        if end < floor:
            end = floor
            start = max(low, end - length)
    return round(start, 3), round(end, 3)


def is_trimmed(in_s, out_s, window_in_s, window_out_s, tolerance=TOLERANCE_S):
    """True when the cut is shorter than its candidate window."""
    return (float(out_s) - float(in_s)) < (float(window_out_s) - float(window_in_s)) - tolerance


def peak_in_cut(in_s, out_s, peak_s, tolerance=TOLERANCE_S):
    """True when ``peak_s`` lies inside the cut."""
    peak = _number(peak_s)
    if peak is None:
        return False
    return float(in_s) - tolerance <= peak <= float(out_s) + tolerance


def is_centred(in_s, out_s, window_in_s, window_out_s, peak_s):
    """True when the cut was trimmed around its peak: shorter than the window and holding the peak."""
    return is_trimmed(in_s, out_s, window_in_s, window_out_s) and peak_in_cut(in_s, out_s, peak_s)
