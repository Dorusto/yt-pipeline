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
