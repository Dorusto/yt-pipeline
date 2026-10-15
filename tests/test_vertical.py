import json
import sys

import pytest

from common import vertical


def test_vertical_out_path_adds_the_suffix_next_to_the_file(tmp_path):
    assert vertical.vertical_out_path(tmp_path / "recap_720p.mp4") == tmp_path / "recap_720p_9x16.mp4"


def test_formats_to_render():
    assert vertical.formats_to_render("16x9") == ["16x9"]
    assert vertical.formats_to_render("9x16") == ["9x16"]
    assert vertical.formats_to_render("both") == ["16x9", "9x16"]


@pytest.mark.parametrize(
    "frame,expected",
    [((1280, 720), (404, 720)), ((1920, 1080), (608, 1080)), ((720, 1280), (720, 1280)), ((640, 360), (202, 360))],
)
def test_crop_size_is_the_largest_even_9x16_window(frame, expected):
    assert vertical.crop_size(*frame) == expected


def test_piecewise_expression_is_flat_for_constant_values():
    assert vertical.piecewise_expression([0.0, 1.0], [100, 100]) == "100"


def test_piecewise_expression_interpolates_between_points():
    expression = vertical.piecewise_expression([0.0, 1.0, 2.0], [0, 100, 100])
    assert expression.startswith("if(lt(t,0),0,")
    assert "lerp(0,100,(t-0)/1)" in expression
    assert "lerp(100,100,(t-1)/1)" in expression


def test_window_points_bracket_the_slot_and_are_capped():
    points = [{"t_s": index * 0.25, "x": index, "y": 0} for index in range(400)]
    window = vertical.window_points(points, 10.0, 12.0)
    assert window[0]["t_s"] >= 9.5 - 1e-9
    assert window[-1]["t_s"] <= 12.5 + 1e-9
    assert len(window) <= 14
    wide = vertical.window_points(points, 0.0, 99.0)
    assert len(wide) <= vertical.MAX_PATH_POINTS + 1


def test_window_points_fall_back_to_the_nearest_earlier_point():
    points = [{"t_s": 0.0, "x": 5, "y": 6}, {"t_s": 1.0, "x": 7, "y": 8}]
    assert vertical.window_points(points, 50.0, 52.0) == [points[-1]] or len(vertical.window_points(points, 50.0, 52.0)) == 1


def test_scale_path_rescales_to_a_bigger_file():
    record = {
        "width": 1280, "height": 720, "crop_w": 404, "crop_h": 720,
        "points": [{"t_s": 0.0, "x": 100, "y": 0}, {"t_s": 1.0, "x": 300, "y": 0}],
    }
    scaled = vertical.scale_path(record, 1920, 1080)
    assert (scaled["crop_w"], scaled["crop_h"]) == (608, 1080)
    assert [point["x"] for point in scaled["points"]] == [150, 450]


def test_scale_path_keeps_the_path_for_the_same_size():
    record = {"width": 640, "height": 360, "crop_w": 202, "crop_h": 360, "points": [{"t_s": 0.0, "x": 10, "y": 0}]}
    assert vertical.scale_path(record, 640, 360)["points"] == record["points"]


def test_load_crop_path_returns_none_for_a_broken_file(tmp_path):
    broken = tmp_path / "x.json"
    broken.write_text("{not json", encoding="utf-8")
    assert vertical.load_crop_path(broken, 640, 360) is None
    assert vertical.load_crop_path(tmp_path / "missing.json", 640, 360) is None


def test_centred_path_sits_in_the_middle():
    path = vertical.centred_path(640, 360)
    assert path["points"][0]["x"] == (640 - 202) // 2


def test_crop_filter_names_the_window_and_follows_the_path():
    path = {
        "crop_w": 202, "crop_h": 360,
        "points": [{"t_s": 0.0, "x": 0, "y": 0}, {"t_s": 2.0, "x": 400, "y": 0}],
    }
    filter_text = vertical.crop_filter(path, 0.0, 2.0)
    assert filter_text.startswith("crop=202:360:x='")
    assert "lerp(0,400,(t-0)/2)" in filter_text
    assert filter_text.endswith(":y='0'")


def test_reframe_command_uses_one_video_and_the_cache_path(tmp_path):
    command = vertical.reframe_command(sys.executable, "scripts/reframe.py", tmp_path / "a.mp4", tmp_path / "c.json")
    assert command == [sys.executable, "scripts/reframe.py", "--video", str(tmp_path / "a.mp4"),
                       "--out", str(tmp_path / "c.json"), "--json"]
    assert vertical.cached_path_name(tmp_path / "clip_a.mp4") == "clip_a_reframe.json"
    json.dumps(command)
