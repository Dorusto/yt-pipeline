import json
import math
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

import motion
from common.schema import validate

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "motion.py"
FOURCC = cv2.VideoWriter_fourcc(*"mp4v")


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
    )


def read_motion(workspace, stem):
    return json.loads((workspace / "motion" / f"{stem}.json").read_text(encoding="utf-8"))


def noise_frame(width, height, seed=0):
    rng = np.random.default_rng(seed)
    image = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
    return cv2.GaussianBlur(image, (3, 3), 0)


def write_video(path, frames, size, fps):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), FOURCC, fps, size)
    if not writer.isOpened():
        pytest.skip("OpenCV mp4v VideoWriter is unavailable on this build")
    for frame in frames:
        writer.write(frame)
    writer.release()
    return path


def make_static_clip(path, seconds=3, width=64, height=48, fps=25):
    frame = noise_frame(width, height)
    return write_video(path, [frame for _ in range(seconds * fps)], (width, height), fps)


def make_pan_clip(path, seconds=5, width=320, height=180, fps=25, pan_px_per_s=60.0):
    big = noise_frame(640, 360, seed=1)
    frames = []
    for index in range(seconds * fps):
        moment = index / fps
        if moment < 0.5:
            offset = 0.0
        elif moment < 3.5:
            offset = (moment - 0.5) * pan_px_per_s
        else:
            offset = 3.0 * pan_px_per_s
        left = int(round(offset))
        frames.append(big[0:height, left : left + width].copy())
    return write_video(path, frames, (width, height), fps)


def fabricated_rows(dx=0.0, dy=0.0, scale=1.0, roll_deg=0.0, count=10, sample_fps=10.0):
    return [
        {
            "t": (index + 0.5) / sample_fps,
            "dx": dx,
            "dy": dy,
            "scale": scale,
            "roll_deg": roll_deg,
            "inlier_ratio": 0.9,
        }
        for index in range(count)
    ]


def assert_tiles(phases, duration):
    assert phases[0]["start_s"] == pytest.approx(0.0)
    assert phases[-1]["end_s"] == pytest.approx(duration)
    for previous, following in zip(phases, phases[1:]):
        assert previous["end_s"] == pytest.approx(following["start_s"])


def test_help_lists_flags():
    result = run_cli("--help")
    assert result.returncode == 0
    for flag in ("--workspace", "--force", "--sample-fps", "--json"):
        assert flag in result.stdout


def test_label_pan_right():
    assert motion.label_window(fabricated_rows(dx=-0.05), 10.0) == ("pan_right", False)


def test_label_pan_left():
    assert motion.label_window(fabricated_rows(dx=0.05), 10.0) == ("pan_left", False)


def test_label_tilt_up():
    assert motion.label_window(fabricated_rows(dy=0.05), 10.0) == ("tilt_up", False)


def test_label_tilt_down():
    assert motion.label_window(fabricated_rows(dy=-0.05), 10.0) == ("tilt_down", False)


def test_label_roll_cw():
    assert motion.label_window(fabricated_rows(roll_deg=-10.0), 10.0) == ("roll_cw", False)


def test_label_roll_ccw():
    assert motion.label_window(fabricated_rows(roll_deg=10.0), 10.0) == ("roll_ccw", False)


def test_label_dolly_in():
    assert motion.label_window(fabricated_rows(scale=1.5), 10.0) == ("dolly_in", True)


def test_label_static():
    assert motion.label_window(fabricated_rows(dx=0.001), 10.0) == ("static", False)


def test_label_complex_motion():
    assert motion.label_window(fabricated_rows(dx=0.05, dy=0.05), 10.0) == ("complex_motion", True)


def test_label_scale_noise_is_static():
    sample_fps = 10.0
    rows = [
        {
            "t": (index + 0.5) / sample_fps,
            "dx": 0.0,
            "dy": 0.0,
            "scale": math.exp(0.01 if index % 2 == 0 else -0.01),
            "roll_deg": 0.0,
            "inlier_ratio": 0.9,
        }
        for index in range(100)
    ]
    assert motion.label_window(rows, sample_fps) == ("static", False)


def test_label_consistent_scale_is_dolly_in():
    sample_fps = 10.0
    rows = [
        {
            "t": (index + 0.5) / sample_fps,
            "dx": 0.0,
            "dy": 0.0,
            "scale": math.exp(0.01),
            "roll_deg": 0.0,
            "inlier_ratio": 0.9,
        }
        for index in range(100)
    ]
    assert motion.label_window(rows, sample_fps) == ("dolly_in", True)


def test_speed_word_thresholds():
    assert motion.speed_word(0.0) == "slow"
    assert motion.speed_word(0.149) == "slow"
    assert motion.speed_word(0.15) == "regular"
    assert motion.speed_word(0.6) == "regular"
    assert motion.speed_word(0.61) == "fast"


def test_steadiness_word_thresholds():
    assert motion.steadiness_word(0.005) == "no_shaking"
    assert motion.steadiness_word(0.005, "static") == "static"
    assert motion.steadiness_word(0.02) == "minimal_shaking"
    assert motion.steadiness_word(0.05) == "unsteady"
    assert motion.steadiness_word(0.1) == "very_unsteady"


def test_phases_split_a_still_move_still_take():
    sample_fps = 10.0
    rows = []
    for index in range(100):
        if index < 10:
            dx = 0.0
        elif index < 20:
            dx = -0.005 - 0.005 * (index - 10)
        elif index < 70:
            dx = -0.05
        elif index < 80:
            dx = -0.05 + 0.005 * (index - 70)
        else:
            dx = 0.0
        rows.append(
            {
                "t": (index + 0.5) / sample_fps,
                "dx": dx,
                "dy": 0.0,
                "scale": 1.0,
                "roll_deg": 0.0,
                "inlier_ratio": 0.9,
            }
        )

    phases = motion.segment_phases(rows, 10.0, sample_fps)
    types = [phase["type"] for phase in phases]

    assert types[0] == "still"
    assert types[-1] == "still"
    assert "steady_move" in types
    assert set(types) <= {"still", "ramp_up", "steady_move", "ramp_down", "irregular"}
    assert_tiles(phases, 10.0)


def test_short_blip_does_not_become_a_phase():
    sample_fps = 10.0
    rows = []
    for index in range(100):
        if index < 10 or index >= 90:
            dx = 0.0
        elif 50 <= index <= 52:
            dx = 0.0
        else:
            dx = -0.01
        rows.append(
            {
                "t": (index + 0.5) / sample_fps,
                "dx": dx,
                "dy": 0.0,
                "scale": 1.0,
                "roll_deg": 0.0,
                "inlier_ratio": 0.9,
            }
        )

    phases = motion.segment_phases(rows, 10.0, sample_fps)
    stills = [phase for phase in phases if phase["type"] == "still"]

    assert len(stills) == 2
    assert stills[0]["end_s"] <= 1.5
    assert stills[1]["start_s"] >= 8.5
    assert_tiles(phases, 10.0)


def test_dominant_change_splits_into_separate_moves():
    sample_fps = 10.0
    rows = []
    for index in range(120):
        moment = (index + 0.5) / sample_fps
        if moment < 2.0:
            dx, scale = 0.0, 1.0
        elif moment < 5.0:
            dx, scale = -0.05, 1.0
        elif moment < 9.0:
            dx, scale = 0.0, math.exp(0.01)
        else:
            dx, scale = 0.0, 1.0
        rows.append(
            {
                "t": moment,
                "dx": dx,
                "dy": 0.0,
                "scale": scale,
                "roll_deg": 0.0,
                "inlier_ratio": 0.9,
            }
        )

    phases = motion.segment_phases(rows, 12.0, sample_fps)
    phase_labels = [phase["dominant_motion"] for phase in phases]
    moves = motion.build_moves(phases, rows, sample_fps)
    move_labels = [move["label"] for move in moves]

    assert "pan_right" in phase_labels
    assert "dolly_in" in phase_labels
    assert "pan_right" in move_labels
    assert "dolly_in" in move_labels


def test_hysteresis_one_sample_blip_does_not_split():
    sample_fps = 10.0
    rows = []
    for index in range(100):
        dx, dy = (0.0, 0.05) if index == 50 else (-0.05, 0.0)
        rows.append(
            {
                "t": (index + 0.5) / sample_fps,
                "dx": dx,
                "dy": dy,
                "scale": 1.0,
                "roll_deg": 0.0,
                "inlier_ratio": 0.9,
            }
        )

    phases = motion.segment_phases(rows, 10.0, sample_fps)

    assert not any(phase["dominant_motion"] == "tilt_up" for phase in phases)


def test_hysteresis_persistent_change_splits():
    sample_fps = 10.0
    rows = []
    for index in range(100):
        dx, dy = (0.0, 0.05) if 40 <= index < 46 else (-0.05, 0.0)
        rows.append(
            {
                "t": (index + 0.5) / sample_fps,
                "dx": dx,
                "dy": dy,
                "scale": 1.0,
                "roll_deg": 0.0,
                "inlier_ratio": 0.9,
            }
        )

    phases = motion.segment_phases(rows, 10.0, sample_fps)

    assert any(phase["dominant_motion"] == "tilt_up" for phase in phases)


def _steady_rows(dx=-0.05, count=100, sample_fps=10.0):
    rows = []
    for index in range(count):
        moment = (index + 0.5) / sample_fps
        if moment < 7.0:
            dx_value, dy_value = dx, 0.0
        else:
            dx_value, dy_value = 0.0, 0.05
        rows.append(
            {
                "t": moment,
                "dx": dx_value,
                "dy": dy_value,
                "scale": 1.0,
                "roll_deg": 0.0,
                "inlier_ratio": 0.9,
            }
        )
    return rows


def test_move_from_a_long_steady_phase():
    phases = [
        {"type": "ramp_up", "start_s": 0.0, "end_s": 0.5, "dominant_motion": "pan_right"},
        {"type": "steady_move", "start_s": 0.5, "end_s": 4.0, "dominant_motion": "pan_right"},
        {"type": "ramp_down", "start_s": 4.0, "end_s": 4.5, "dominant_motion": "pan_right"},
    ]

    moves = motion.build_moves(phases, _steady_rows(), 10.0)

    assert len(moves) == 1
    move = moves[0]
    assert move["id"] == "m1"
    assert move["label"] == "pan_right"
    assert move["start_s"] <= move["steady_start_s"] < move["steady_end_s"] <= move["end_s"]
    assert move["handle_in_s"] < move["steady_start_s"] < move["steady_end_s"] < move["handle_out_s"]


def test_short_steady_phase_yields_no_move():
    phases = [{"type": "steady_move", "start_s": 1.0, "end_s": 2.0, "dominant_motion": "pan_right"}]
    assert motion.build_moves(phases, _steady_rows(), 10.0) == []


def test_moves_are_numbered_in_time_order():
    phases = [
        {"type": "ramp_up", "start_s": 0.0, "end_s": 0.5, "dominant_motion": "pan_right"},
        {"type": "steady_move", "start_s": 0.5, "end_s": 4.0, "dominant_motion": "pan_right"},
        {"type": "ramp_down", "start_s": 4.0, "end_s": 4.5, "dominant_motion": "pan_right"},
        {"type": "still", "start_s": 4.5, "end_s": 7.0, "dominant_motion": "static"},
        {"type": "steady_move", "start_s": 7.0, "end_s": 10.0, "dominant_motion": "tilt_up"},
    ]

    moves = motion.build_moves(phases, _steady_rows(), 10.0)

    assert [move["id"] for move in moves] == ["m1", "m2"]
    assert moves[0]["start_s"] < moves[1]["start_s"]
    assert moves[0]["label"] == "pan_right"
    assert moves[1]["label"] == "tilt_up"


def test_end_to_end_static_clip(tmp_path):
    source = tmp_path / "clips"
    make_static_clip(source / "still.mp4")
    workspace = tmp_path / "workspace"

    result = run_cli(source, "--workspace", workspace, "--json")

    assert result.returncode == 0, result.stderr
    data = read_motion(workspace, "still")
    assert validate(data, "motion") == []
    assert data["moves"] == []
    assert "static" in [phase["dominant_motion"] for phase in data["phases"]]
    assert_tiles(data["phases"], data["duration_s"])


def test_end_to_end_synthetic_pan(tmp_path):
    source = tmp_path / "clips"
    make_pan_clip(source / "pan.mp4")
    workspace = tmp_path / "workspace"

    result = run_cli(source, "--workspace", workspace, "--json")

    assert result.returncode == 0, result.stderr
    data = read_motion(workspace, "pan")
    assert validate(data, "motion") == []
    assert_tiles(data["phases"], data["duration_s"])
    assert data["moves"], "the constant pan should yield a move"
    move = data["moves"][0]
    assert move["label"] == "pan_right"
    assert 0.3 <= move["steady_start_s"] <= 3.5


def test_folder_skip_and_force(tmp_path):
    source = tmp_path / "clips"
    make_static_clip(source / "one.mp4", seconds=1)
    make_static_clip(source / "two.mp4", seconds=1)
    workspace = tmp_path / "workspace"

    first = run_cli(source, "--workspace", workspace, "--json")
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout) == {
        "clips": 2,
        "analyzed": 2,
        "skipped": 0,
        "output_dir": str((workspace / "motion").resolve()),
    }
    assert (workspace / "motion" / "one.json").exists()
    assert (workspace / "motion" / "two.json").exists()

    second = run_cli(source, "--workspace", workspace, "--json")
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["skipped"] == 2
    assert json.loads(second.stdout)["analyzed"] == 0

    third = run_cli(source, "--workspace", workspace, "--force", "--json")
    assert third.returncode == 0, third.stderr
    assert json.loads(third.stdout)["analyzed"] == 2


def test_missing_input_errors(tmp_path):
    result = run_cli(tmp_path / "missing.mp4", "--workspace", tmp_path / "workspace")
    assert result.returncode == 1
    assert "[error]" in result.stderr
    assert not (tmp_path / "workspace" / "motion").exists()


def test_non_video_file_errors(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("not a video", encoding="utf-8")
    result = run_cli(source, "--workspace", tmp_path / "workspace")
    assert result.returncode == 1
    assert "[error]" in result.stderr


def test_unreadable_video_errors(tmp_path):
    source = tmp_path / "broken.mp4"
    source.write_bytes(b"not really a video")
    result = run_cli(source, "--workspace", tmp_path / "workspace")
    assert result.returncode == 1
    assert "[error]" in result.stderr


def _bob_rows(sample_fps=10.0, seconds=20.0, period=3.0, amplitude=0.015, dx=0.0):
    rows = []
    for index in range(int(seconds * sample_fps)):
        moment = (index + 0.5) / sample_fps
        rows.append(
            {
                "t": moment,
                "dx": dx,
                "dy": amplitude * math.sin(2.0 * math.pi * moment / period),
                "scale": 1.0,
                "roll_deg": 0.0,
                "inlier_ratio": 0.9,
            }
        )
    return rows


def _scale_ramp_rows(sample_fps=10.0, seconds=20.0, per_frame=0.01):
    rows = []
    for index in range(int(seconds * sample_fps)):
        rows.append(
            {
                "t": (index + 0.5) / sample_fps,
                "dx": 0.0,
                "dy": 0.0,
                "scale": math.exp(per_frame),
                "roll_deg": 0.0,
                "inlier_ratio": 0.9,
            }
        )
    return rows


def test_bob_only_is_static_with_platform_bob():
    sample_fps = 10.0
    rows = _bob_rows(sample_fps=sample_fps)

    spans = motion.detect_platform_bob(rows, sample_fps)
    assert spans
    assert spans[0]["period_s"] == pytest.approx(3.0, abs=0.5)

    cleaned = motion._clean_bob(rows, spans, sample_fps)
    phases = motion.segment_phases(rows, 20.0, sample_fps, label_rows=cleaned)
    assert not any(phase["dominant_motion"] in ("tilt_up", "tilt_down") for phase in phases)

    moves = motion.build_moves(phases, cleaned, sample_fps, spans)
    assert moves
    assert not any(move["label"] in ("tilt_up", "tilt_down") for move in moves)
    assert moves[0]["label"] in ("static", "minor_motion")
    assert moves[0]["platform_bob"] is not None
    assert moves[0]["platform_bob"]["period_s"] == pytest.approx(3.0, abs=0.5)


def test_bob_on_pan_keeps_pan_label():
    sample_fps = 10.0
    rows = _bob_rows(sample_fps=sample_fps, dx=-0.05)

    spans = motion.detect_platform_bob(rows, sample_fps)
    assert spans

    cleaned = motion._clean_bob(rows, spans, sample_fps)
    phases = motion.segment_phases(rows, 20.0, sample_fps, label_rows=cleaned)
    moves = motion.build_moves(phases, cleaned, sample_fps, spans)

    assert moves
    assert any(move["label"] == "pan_right" for move in moves)
    assert all(move["platform_bob"] is not None for move in moves)


def test_long_smooth_scale_ramp_is_travel_forward():
    sample_fps = 10.0
    rows = _scale_ramp_rows(sample_fps=sample_fps)

    phases = motion.segment_phases(rows, 20.0, sample_fps)
    moves = motion.build_moves(phases, rows, sample_fps)

    assert len(moves) == 1
    move = moves[0]
    assert move["label"] == "travel_forward"
    assert move["label_geometric"] == "dolly_in"
    assert move["platform"] == "vehicle"
    assert move["semantic_check"] is True


def test_shrinking_scale_ramp_is_travel_backward():
    sample_fps = 10.0
    rows = _scale_ramp_rows(sample_fps=sample_fps, per_frame=-0.01)

    phases = motion.segment_phases(rows, 20.0, sample_fps)
    moves = motion.build_moves(phases, rows, sample_fps)

    assert len(moves) == 1
    move = moves[0]
    assert move["label"] == "travel_backward"
    assert move["label_geometric"] == "dolly_out"
    assert move["platform"] == "vehicle"
    assert move["semantic_check"] is True


def test_short_scale_blip_stays_dolly_in():
    sample_fps = 10.0
    rows = []
    for index in range(100):
        moment = (index + 0.5) / sample_fps
        scale = math.exp(0.01) if 4.0 <= moment < 6.0 else 1.0
        rows.append(
            {
                "t": moment,
                "dx": 0.0,
                "dy": 0.0,
                "scale": scale,
                "roll_deg": 0.0,
                "inlier_ratio": 0.9,
            }
        )

    phases = motion.segment_phases(rows, 10.0, sample_fps)
    moves = motion.build_moves(phases, rows, sample_fps)
    dolly_moves = [move for move in moves if move["label"] in ("dolly_in", "dolly_out")]

    assert dolly_moves
    move = dolly_moves[0]
    assert move["label"] == "dolly_in"
    assert move["label_geometric"] is None
    assert move["platform"] is None


def test_static_move_has_platform_static():
    phases = [{"type": "steady_move", "start_s": 0.5, "end_s": 4.0, "dominant_motion": "static"}]
    rows = fabricated_rows(dx=0.001, count=50)

    moves = motion.build_moves(phases, rows, 10.0)

    assert len(moves) == 1
    assert moves[0]["label"] == "static"
    assert moves[0]["platform"] == "static"
    assert moves[0]["label_geometric"] is None
    assert moves[0]["platform_bob"] is None


def test_moves_carry_new_fields_and_validate(tmp_path):
    source = tmp_path / "clips"
    make_pan_clip(source / "pan.mp4")
    workspace = tmp_path / "workspace"

    result = run_cli(source, "--workspace", workspace, "--json")

    assert result.returncode == 0, result.stderr
    data = read_motion(workspace, "pan")
    assert validate(data, "motion") == []
    assert data["moves"]
    for move in data["moves"]:
        assert "label_geometric" in move
        assert "platform" in move
        assert "platform_bob" in move


LATERAL_PHASE = [{"type": "steady_move", "start_s": 0.5, "end_s": 4.0, "dominant_motion": "pan_right"}]


def _lateral_rows(
    parallax,
    centre_sum=0.0,
    centre_count=0,
    periphery_sum=0.0,
    periphery_count=0,
    dx=-0.05,
    count=50,
    sample_fps=10.0,
):
    rows = []
    for index in range(count):
        rows.append(
            {
                "t": (index + 0.5) / sample_fps,
                "dx": dx,
                "dy": 0.0,
                "scale": 1.0,
                "roll_deg": 0.0,
                "inlier_ratio": 0.9,
                "parallax": parallax,
                "subject_centre_sum": centre_sum,
                "subject_centre_count": centre_count,
                "subject_periphery_sum": periphery_sum,
                "subject_periphery_count": periphery_count,
            }
        )
    return rows


def _orbit_field(slide_centre=False, seed=3):
    rng = np.random.default_rng(seed)
    width, height = 320, 180
    theta = math.radians(5)
    matrix = np.array(
        [[math.cos(theta), -math.sin(theta), 0.0], [math.sin(theta), math.cos(theta), 0.0]]
    )
    source = rng.uniform([0, 0], [width, height], size=(80, 2))
    centre = (
        (source[:, 0] >= 0.3 * width)
        & (source[:, 0] <= 0.7 * width)
        & (source[:, 1] >= 0.3 * height)
        & (source[:, 1] <= 0.7 * height)
    )
    target = source @ matrix[:, :2].T + matrix[:, 2]
    slide = 10.0 * rng.uniform(0, 1, size=len(source))
    if slide_centre:
        target[:, 0] += slide
    else:
        target[~centre, 0] += slide[~centre]
    return source, target, matrix, width, height


def test_parallax_pure_pan_is_low():
    rng = np.random.default_rng(0)
    source = rng.uniform([0, 0], [320, 180], size=(60, 2))
    target = source + np.array([-5.0, 0.0])

    parallax = motion._parallax_score(source, target, 320)

    assert parallax < 0.2
    moves = motion.build_moves(LATERAL_PHASE, _lateral_rows(parallax), 10.0)
    assert moves[0]["label"] == "pan_right"
    assert moves[0]["label_geometric"] is None


def test_parallax_depth_translation_is_truck():
    rng = np.random.default_rng(0)
    width, height = 320, 180
    source = rng.uniform([0, 0], [width, height], size=(80, 2))
    depth = 1.0 + 2.0 * (0.5 + 0.5 * np.sin(2.0 * np.pi * source[:, 0] / width))
    target = source + np.column_stack([-15.0 * depth, np.zeros(len(source))])

    parallax = motion._parallax_score(source, target, width)

    assert parallax >= 0.5
    moves = motion.build_moves(LATERAL_PHASE, _lateral_rows(parallax), 10.0)
    assert moves[0]["label"] == "truck_right"
    assert moves[0]["label_geometric"] == "pan_right"


def test_orbit_candidate_from_centred_subject():
    source, target, matrix, width, height = _orbit_field()
    parallax = motion._parallax_score(source, target, width)
    centre_sum, centre_count, periphery_sum, periphery_count = motion._subject_residuals(
        source, target, matrix, width, height
    )

    assert parallax >= 0.5
    rows = _lateral_rows(parallax, centre_sum, centre_count, periphery_sum, periphery_count)
    moves = motion.build_moves(LATERAL_PHASE, rows, 10.0)

    assert moves[0]["label"] == "orbit_candidate"
    assert moves[0]["label_geometric"] == "pan_right"
    assert moves[0]["semantic_check"] is True


def test_orbit_without_quiet_centre_is_truck():
    source, target, matrix, width, height = _orbit_field(slide_centre=True)
    parallax = motion._parallax_score(source, target, width)
    centre_sum, centre_count, periphery_sum, periphery_count = motion._subject_residuals(
        source, target, matrix, width, height
    )

    rows = _lateral_rows(parallax, centre_sum, centre_count, periphery_sum, periphery_count)
    moves = motion.build_moves(LATERAL_PHASE, rows, 10.0)

    assert moves[0]["label"] in ("truck_left", "truck_right")
    assert moves[0]["label_geometric"] == "pan_right"


def test_few_matches_parallax_zero():
    source = np.zeros((5, 2))
    target = np.zeros((5, 2))

    assert motion._parallax_score(source, target, 320) == 0.0

    moves = motion.build_moves(LATERAL_PHASE, _lateral_rows(0.0), 10.0)
    assert moves[0]["parallax"] == 0.0
    assert moves[0]["label"] == "pan_right"


def test_moves_carry_parallax_and_validate(tmp_path):
    source = tmp_path / "clips"
    make_pan_clip(source / "pan.mp4")
    workspace = tmp_path / "workspace"

    result = run_cli(source, "--workspace", workspace, "--json")

    assert result.returncode == 0, result.stderr
    data = read_motion(workspace, "pan")
    assert validate(data, "motion") == []
    assert data["moves"]
    for move in data["moves"]:
        assert 0.0 <= move["parallax"] <= 1.0


def test_scene_events_quiet_clip():
    assert motion.detect_scene_events([0.001] * 30) == []


def test_scene_events_rise_and_peak():
    activities = [0.001] * 60
    for index in range(10, 36):
        activities[index] = 0.001 + 0.01 * (index - 10) / 25
    for index in range(36, 50):
        activities[index] = 0.011 - 0.01 * (index - 35) / 15

    events = motion.detect_scene_events(activities)
    starts = [event for event in events if event["type"] in ("activity_start", "subject_enters")]
    peaks = [event for event in events if event["type"] == "activity_peak"]
    ends = [event for event in events if event["type"] in ("activity_end", "subject_leaves")]

    assert starts and abs(starts[0]["t_s"] - 10) <= 1
    assert peaks and abs(peaks[0]["t_s"] - 35) <= 2
    assert ends


def test_scene_events_two_spans():
    activities = [0.001] * 60
    for index in range(5, 15):
        activities[index] = 0.001 + 0.01 * (1 - abs(index - 10) / 5)
    for index in range(30, 45):
        activities[index] = 0.001 + 0.01 * (1 - abs(index - 37) / 7)

    events = motion.detect_scene_events(activities)
    starts = [event for event in events if event["type"] in ("activity_start", "subject_enters")]
    peaks = [event for event in events if event["type"] == "activity_peak"]

    assert len(starts) == 2
    assert len(peaks) == 2


def _dipped_span_series():
    activities = [0.001 + 0.001 * (index % 10) / 10 for index in range(100)]
    for index in range(10, 30):
        activities[index] = 0.006
    for index in (14, 15, 22):
        activities[index] = 0.002
    return activities


def test_scene_events_long_span_with_dips_is_one_span():
    events = motion.detect_scene_events(_dipped_span_series())
    starts = [event for event in events if event["type"] in ("activity_start", "subject_enters")]
    peaks = [event for event in events if event["type"] == "activity_peak"]
    ends = [event for event in events if event["type"] in ("activity_end", "subject_leaves")]

    assert len(starts) == 1
    assert len(peaks) == 1
    assert len(ends) == 1


def test_scene_events_have_no_duplicates():
    series = [
        [0.001] * 40,
        [0.001 + 0.0002 * (index % 3) for index in range(60)],
        [0.002] * 20 + [0.008] * 20 + [0.002] * 20,
        _dipped_span_series(),
    ]

    for activities in series:
        events = motion.detect_scene_events(activities)
        keys = [(event["t_s"], event["type"]) for event in events]
        assert len(keys) == len(set(keys))


def test_scene_activity_series_covers_every_second():
    rows = [
        {"t": 0.5, "residual_activity": 0.002},
        {"t": 1.5, "residual_activity": 0.004},
    ]

    series = motion.scene_activity_series(rows, 3.2)

    assert [row["t_s"] for row in series] == [0.5, 1.5, 2.5, 3.5]
    assert series[0]["activity"] == pytest.approx(0.002)
    assert series[1]["activity"] == pytest.approx(0.004)
    assert series[2]["activity"] == 0.0


def make_moving_object_clip(path, seconds=4, width=320, height=180, fps=25):
    background = noise_frame(width, height, seed=7)
    frames = []
    for index in range(seconds * fps):
        frame = background.copy()
        moment = index / fps
        left = int(20 + (width - 100) * moment / seconds)
        top = int(0.3 * height)
        cv2.rectangle(frame, (left, top), (left + 70, top + 45), (255, 255, 255), -1)
        for x in range(left, left + 70, 12):
            cv2.line(frame, (x, top), (x, top + 45), (0, 0, 0), 2)
        frames.append(frame)
    return write_video(path, frames, (width, height), fps)


def test_scene_activity_moving_object_beats_static(tmp_path):
    source = tmp_path / "clips"
    make_moving_object_clip(source / "moving.mp4")
    make_static_clip(source / "static.mp4", seconds=4, width=320, height=180)
    workspace = tmp_path / "workspace"

    result = run_cli(source, "--workspace", workspace, "--json")

    assert result.returncode == 0, result.stderr
    moving = read_motion(workspace, "moving")
    static = read_motion(workspace, "static")
    moving_peak = max(row["activity"] for row in moving["scene_activity"])
    static_peak = max(row["activity"] for row in static["scene_activity"])

    assert moving_peak > 0.001
    assert static_peak < 0.001


def test_motion_doc_validates_with_and_without_events(tmp_path):
    source = tmp_path / "clips"
    make_static_clip(source / "still.mp4")
    workspace = tmp_path / "workspace"

    result = run_cli(source, "--workspace", workspace, "--json")

    assert result.returncode == 0, result.stderr
    data = read_motion(workspace, "still")
    assert validate(data, "motion") == []
    assert data["events"] == []
    assert data["scene_activity"]

    data["events"] = [{"t_s": 1.0, "type": "activity_start", "note": "test"}]
    assert validate(data, "motion") == []


