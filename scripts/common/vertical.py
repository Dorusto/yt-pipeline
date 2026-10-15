"""Helpers for the vertical 9:16 render: crop path to ffmpeg expression, file names, frame sizes.

``scripts/reframe.py`` writes one crop path per clip (``t_s`` -> window top-left
``x``, ``y`` in source pixels). The render turns the part of that path a slot
uses into a piecewise-linear ``crop`` expression on source time, so the window
follows the subject through the slot.
"""

import json
from pathlib import Path

FORMATS = ("16x9", "9x16", "both")
VERTICAL_PREVIEW_FRAME = (720, 1280)
VERTICAL_FINAL_FRAME = (1080, 1920)
ASPECT_W = 9
ASPECT_H = 16
MAX_PATH_POINTS = 40
WINDOW_MARGIN_S = 0.5


CROP_CACHE_VERSION = "_v2"


def vertical_out_path(out_path):
    """``recap_720p.mp4`` -> ``recap_720p_9x16.mp4`` next to it."""
    out_path = Path(out_path)
    return out_path.with_name(f"{out_path.stem}_9x16{out_path.suffix}")


def formats_to_render(frame_format):
    """The list of formats a ``--format`` value stands for."""
    if frame_format == "both":
        return ["16x9", "9x16"]
    return [frame_format]


def crop_size(frame_w, frame_h):
    """Largest even 9:16 window inside the frame."""
    if frame_w * ASPECT_H >= frame_h * ASPECT_W:
        crop_h = frame_h
        crop_w = round(frame_h * ASPECT_W / ASPECT_H)
    else:
        crop_w = frame_w
        crop_h = round(frame_w * ASPECT_H / ASPECT_W)
    crop_w = max(2, min(frame_w, crop_w - crop_w % 2))
    crop_h = max(2, min(frame_h, crop_h - crop_h % 2))
    return crop_w, crop_h


def centred_path(frame_w, frame_h):
    """A fixed centred crop used when no reframe path is available."""
    crop_w, crop_h = crop_size(frame_w, frame_h)
    return {
        "crop_w": crop_w,
        "crop_h": crop_h,
        "points": [{"t_s": 0.0, "x": (frame_w - crop_w) // 2, "y": (frame_h - crop_h) // 2}],
    }


def scale_path(record, frame_w, frame_h):
    """The crop path of ``record`` rescaled to a file of ``frame_w`` x ``frame_h`` pixels."""
    source_w = float(record["width"])
    source_h = float(record["height"])
    if int(source_w) == frame_w and int(source_h) == frame_h:
        return {"crop_w": record["crop_w"], "crop_h": record["crop_h"], "points": record["points"]}
    scale_x = frame_w / source_w
    scale_y = frame_h / source_h
    crop_w, crop_h = crop_size(frame_w, frame_h)
    points = []
    for point in record["points"]:
        x = min(frame_w - crop_w, max(0, round(point["x"] * scale_x)))
        y = min(frame_h - crop_h, max(0, round(point["y"] * scale_y)))
        points.append({"t_s": point["t_s"], "x": x, "y": y})
    return {"crop_w": crop_w, "crop_h": crop_h, "points": points}


def load_crop_path(path, frame_w, frame_h):
    """Read a reframe JSON and rescale it to the file being rendered; None when unreadable."""
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        if not record.get("points"):
            return None
        return scale_path(record, frame_w, frame_h)
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError):
        return None


def window_points(points, in_s, out_s):
    """The points that bracket ``[in_s, out_s]``, decimated to at most ``MAX_PATH_POINTS``."""
    ordered = sorted(points, key=lambda point: point["t_s"])
    inside = [
        point
        for point in ordered
        if in_s - WINDOW_MARGIN_S <= point["t_s"] <= out_s + WINDOW_MARGIN_S
    ]
    if not inside:
        before = [point for point in ordered if point["t_s"] < in_s]
        inside = [before[-1] if before else ordered[0]]
    if len(inside) > MAX_PATH_POINTS:
        step = len(inside) / MAX_PATH_POINTS
        inside = [inside[int(index * step)] for index in range(MAX_PATH_POINTS)] + [inside[-1]]
    return inside


def _number(value):
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text or "0"


def piecewise_expression(times, values):
    """An ffmpeg expression in ``t``: linear between the points, flat outside them."""
    if len(times) == 1 or all(value == values[0] for value in values):
        return _number(values[0])
    expression = _number(values[-1])
    for index in range(len(times) - 2, -1, -1):
        span = max(times[index + 1] - times[index], 1e-3)
        expression = (
            f"if(lt(t,{_number(times[index + 1])}),"
            f"lerp({_number(values[index])},{_number(values[index + 1])},"
            f"(t-{_number(times[index])})/{_number(span)}),{expression})"
        )
    return f"if(lt(t,{_number(times[0])}),{_number(values[0])},{expression})"


def crop_filter(path, in_s, out_s):
    """The ``crop`` filter that follows ``path`` over source time ``in_s``..``out_s``."""
    points = window_points(path["points"], in_s, out_s)
    times = [float(point["t_s"]) for point in points]
    x_expression = piecewise_expression(times, [point["x"] for point in points])
    y_expression = piecewise_expression(times, [point["y"] for point in points])
    return (
        f"crop={path['crop_w']}:{path['crop_h']}:x='{x_expression}':y='{y_expression}'"
    )


def reframe_command(python, reframe_script, video, out_path):
    """The argv that runs ``reframe.py`` for one clip."""
    return [str(python), str(reframe_script), "--video", str(video), "--out", str(out_path), "--json"]


def cached_path_name(video_path):
    """The cache file name of a clip's crop path under ``<workspace>/reframe/``."""
    return f"{Path(video_path).stem}_reframe{CROP_CACHE_VERSION}.json"
