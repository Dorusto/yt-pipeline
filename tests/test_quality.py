import json
import math
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

import quality
from common.schema import validate

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "quality.py"
FOURCC = cv2.VideoWriter_fourcc(*"mp4v")


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
    )


def read_quality(workspace, stem):
    return json.loads((workspace / "quality" / f"{stem}.json").read_text(encoding="utf-8"))


def write_video(path, frames, size, fps):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), FOURCC, fps, size)
    if not writer.isOpened():
        pytest.skip("OpenCV mp4v VideoWriter is unavailable on this build")
    for frame in frames:
        writer.write(frame)
    writer.release()
    return path


def noisy_gray_frame(width, height, seed=0):
    rng = np.random.default_rng(seed)
    gray = rng.integers(0, 256, (height, width), dtype=np.uint8)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def make_noisy_still_clip(path, seconds=3, width=64, height=48, fps=25):
    frame = noisy_gray_frame(width, height)
    return write_video(path, [frame for _ in range(seconds * fps)], (width, height), fps)


def make_black_clip(path, seconds=3, width=64, height=48, fps=25):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    return write_video(path, [frame for _ in range(seconds * fps)], (width, height), fps)


def horizon_lines_frame(width=320, height=240, angle_deg=0.0, count=3, thickness=3):
    gray = np.zeros((height, width), dtype=np.uint8)
    segment_length = int(width * 0.28)
    gap = int(width * 0.08)
    total = count * segment_length + (count - 1) * gap
    start_x = (width - total) // 2
    slope = math.tan(math.radians(angle_deg))
    center_y = height // 2
    for index in range(count):
        x0 = start_x + index * (segment_length + gap)
        x1 = x0 + segment_length
        y0 = int(round(center_y - slope * (x0 - width / 2)))
        y1 = int(round(center_y - slope * (x1 - width / 2)))
        cv2.line(gray, (x0, y0), (x1, y1), 255, thickness)
    return gray


def steep_lines_frame(width=320, height=240, angles=(45.0, 60.0, 30.0), thickness=3):
    gray = np.zeros((height, width), dtype=np.uint8)
    for index, angle_deg in enumerate(angles):
        angle = math.radians(angle_deg)
        length = int(height / math.sin(angle))
        x0 = int(width * (index + 1) / (len(angles) + 1))
        x1 = int(round(x0 + math.cos(angle) * length))
        y1 = int(round(math.sin(angle) * length))
        cv2.line(gray, (x0, 0), (x1, y1), 255, thickness)
    return gray


def short_dashes_frame(width=320, height=240, angle_deg=0.0, spacing=20, length_frac=0.1, thickness=3):
    gray = np.zeros((height, width), dtype=np.uint8)
    length = int(width * length_frac)
    angle = math.radians(angle_deg)
    for y0 in range(20, height, spacing):
        for x0 in range(0, width, length * 3):
            x1 = x0 + length
            y1 = int(round(y0 - math.tan(angle) * length))
            cv2.line(gray, (x0, y0), (x1, y1), 255, thickness)
    return gray


def scattered_lines_frame(width=320, height=240, angles=(-12.0, 0.0, 12.0), thickness=3):
    gray = np.zeros((height, width), dtype=np.uint8)
    length = int(width * 0.8)
    for index, angle_deg in enumerate(angles):
        angle = math.radians(angle_deg)
        y0 = int(height * (index + 1) / (len(angles) + 1))
        x0 = (width - length) // 2
        x1 = x0 + length
        y1 = int(round(y0 - math.tan(angle) * length))
        cv2.line(gray, (x0, y0), (x1, y1), 255, thickness)
    return gray


def clean_metrics(**overrides):
    base = {
        "laplacian_rel": 1.0,
        "clip_high_pct": 0.0,
        "clip_low_pct": 0.0,
        "mean_luma": 128.0,
        "shake": 0.0,
        "obstruction": 0.0,
        "horizon_deg": 0.0,
    }
    base.update(overrides)
    return base


def role_facts(**overrides):
    base = {
        "position": "middle",
        "duration_s": 5.0,
        "phase_type": "steady_move",
        "label": "pan_right",
        "speed": "regular",
        "steadiness": "no_shaking",
        "metrics": clean_metrics(),
        "move": None,
        "frame_diff_mean": 1.0,
    }
    base.update(overrides)
    return base


def assert_tiles(segments, duration):
    assert segments[0]["start_s"] == pytest.approx(0.0)
    assert segments[-1]["end_s"] == pytest.approx(duration)
    for segment in segments:
        assert segment["end_s"] > segment["start_s"]
    for previous, following in zip(segments, segments[1:]):
        assert previous["end_s"] == pytest.approx(following["start_s"])


def test_help_lists_flags():
    result = run_cli("--help")
    assert result.returncode == 0
    for flag in ("--workspace", "--motion-dir", "--force", "--json"):
        assert flag in result.stdout


def test_assign_role_transition_in():
    role, confidence, evidence = quality.assign_role(
        role_facts(
            position="head",
            duration_s=2.0,
            phase_type="steady_move",
            label="roll_cw",
            speed="fast",
            steadiness="no_shaking",
        )
    )
    assert role == "transition_in"
    assert confidence == pytest.approx(0.6)
    assert evidence


def test_assign_role_transition_out():
    role, confidence, _ = quality.assign_role(
        role_facts(
            position="tail",
            duration_s=2.0,
            phase_type="steady_move",
            label="pan_left",
            speed="fast",
            steadiness="minimal_shaking",
        )
    )
    assert role == "transition_out"
    assert confidence == pytest.approx(0.6)


def test_assign_role_setup():
    role, _, evidence = quality.assign_role(
        role_facts(
            position="head",
            duration_s=2.0,
            phase_type="irregular",
            label="complex_motion",
            speed="regular",
            steadiness="unsteady",
        )
    )
    assert role == "setup"
    assert evidence


def test_assign_role_teardown():
    role, _, _ = quality.assign_role(
        role_facts(
            position="tail",
            duration_s=2.0,
            phase_type="irregular",
            label="complex_motion",
            speed="regular",
            steadiness="unsteady",
        )
    )
    assert role == "teardown"


def test_assign_role_move_overlapping_move():
    role, confidence, evidence = quality.assign_role(
        role_facts(position="middle", move={"confidence": 0.7, "label": "pan_right"})
    )
    assert role == "move"
    assert confidence == pytest.approx(0.7)
    assert evidence


def test_assign_role_hold_clean_middle():
    role, confidence, evidence = quality.assign_role(
        role_facts(
            position="middle",
            duration_s=3.0,
            phase_type="still",
            label="static",
            speed="slow",
            steadiness="static",
        )
    )
    assert role == "hold"
    assert confidence == pytest.approx(0.7)
    assert evidence


def test_assign_role_accidental_obstruction():
    role, confidence, evidence = quality.assign_role(
        role_facts(duration_s=3.0, metrics=clean_metrics(obstruction=0.8))
    )
    assert role == "accidental"
    assert confidence == pytest.approx(0.7)
    assert evidence


def test_assign_role_fallback_move():
    role, confidence, evidence = quality.assign_role(
        role_facts(
            position="middle",
            duration_s=0.5,
            phase_type="still",
            label=None,
            speed="slow",
            steadiness="static",
        )
    )
    assert role == "move"
    assert confidence == pytest.approx(0.4)
    assert evidence == ["unclassified motion"]


def test_assign_role_static_label_never_move():
    role, confidence, evidence = quality.assign_role(
        role_facts(
            position="middle",
            duration_s=0.5,
            phase_type="steady_move",
            label="static",
            speed="slow",
            steadiness="static",
        )
    )
    assert role == "hold"
    assert confidence == pytest.approx(0.4)
    assert evidence == ["no significant camera motion"]


def test_assign_role_static_label_with_overlapping_move_stays_hold():
    role, _, _ = quality.assign_role(
        role_facts(position="middle", label="static", move={"confidence": 0.7, "label": "static"})
    )
    assert role != "move"


def _motion(phases, moves=(), duration_s=0.0):
    return {
        "duration_s": duration_s,
        "phases": [
            {
                "start_s": start_s,
                "end_s": end_s,
                "type": phase_type,
                "dominant_motion": label,
                "speed": speed,
                "steadiness": steadiness,
            }
            for start_s, end_s, phase_type, label, speed, steadiness in phases
        ],
        "moves": list(moves),
        "trajectory": [],
    }


def _move(start_s, end_s, label):
    return {"id": f"m{start_s}", "start_s": start_s, "end_s": end_s, "label": label, "confidence": 0.7}


def _frames(duration_s, sample_fps=2):
    steps = max(1, int(duration_s * sample_fps))
    return [
        {
            "t": (index + 0.5) / sample_fps,
            "laplacian": 200.0,
            "mean_luma": 128.0,
            "clip_high_pct": 0.0,
            "clip_low_pct": 0.0,
            "obstruction": 0.0,
            "frame_diff": 10.0,
            "horizon_deg": None,
        }
        for index in range(steps)
    ]


def _analyse(motion, duration_s):
    return quality.segments_from_motion(motion, duration_s, _frames(duration_s), 1.0)


def test_segments_setup_cannot_follow_content_move():
    motion = _motion(
        [
            (0.0, 2.0, "still", "static", "slow", "static"),
            (2.0, 4.0, "steady_move", "pan_right", "regular", "no_shaking"),
            (4.0, 5.0, "irregular", "complex_motion", "fast", "very_unsteady"),
        ],
        [_move(2.0, 4.0, "pan_right")],
    )
    parts = _analyse(motion, 5.0)
    roles = [part["role"] for part in parts]
    assert "setup" not in roles
    assert_tiles(parts, 5.0)


def test_segments_setup_before_content_move_at_head():
    motion = _motion(
        [
            (0.0, 2.0, "irregular", "minor_motion", "regular", "unsteady"),
            (2.0, 4.0, "steady_move", "pan_right", "regular", "no_shaking"),
        ],
        [_move(2.0, 4.0, "pan_right")],
    )
    parts = _analyse(motion, 4.0)
    assert [part["role"] for part in parts] == ["setup", "move"]


def test_segments_teardown_only_after_last_content():
    motion = _motion(
        [
            (0.0, 2.0, "still", "static", "slow", "static"),
            (2.0, 3.0, "irregular", "minor_motion", "regular", "unsteady"),
            (3.0, 5.0, "steady_move", "pan_right", "regular", "no_shaking"),
        ],
        [_move(3.0, 5.0, "pan_right")],
    )
    parts = _analyse(motion, 5.0)
    roles = [part["role"] for part in parts]
    assert "teardown" not in roles
    assert roles[-1] == "move"


def test_segments_teardown_after_last_content():
    motion = _motion(
        [
            (0.0, 2.0, "still", "static", "slow", "static"),
            (2.0, 4.0, "irregular", "minor_motion", "regular", "unsteady"),
        ]
    )
    parts = _analyse(motion, 4.0)
    assert [part["role"] for part in parts] == ["hold", "teardown"]


def test_segments_degenerate_no_content_splits_at_midpoint():
    motion = _motion(
        [
            (0.0, 2.0, "irregular", "minor_motion", "regular", "unsteady"),
            (2.0, 4.0, "irregular", "minor_motion", "regular", "unsteady"),
            (4.0, 6.0, "irregular", "minor_motion", "regular", "unsteady"),
        ]
    )
    parts = _analyse(motion, 6.0)
    assert parts[0]["role"] == "setup"
    assert parts[-1]["role"] == "teardown"


def test_segments_from_motion_merges_slivers():
    motion = _motion(
        [
            (0.0, 0.2, "irregular", "minor_motion", "slow", "unsteady"),
            (0.2, 5.0, "steady_move", "pan_right", "slow", "no_shaking"),
            (5.0, 5.1, "ramp_down", "pan_right", "slow", "no_shaking"),
            (5.1, 10.0, "steady_move", "pan_left", "slow", "no_shaking"),
        ]
    )
    parts = _analyse(motion, 10.0)
    assert_tiles(parts, 10.0)
    for part in parts:
        assert part["end_s"] - part["start_s"] >= quality.MIN_RUN_SECONDS


def test_build_defects_blur():
    defects = quality.build_defects(clean_metrics(laplacian_rel=0.3))
    blur = [defect for defect in defects if defect["name"] == "blur"]
    assert len(blur) == 1
    assert blur[0]["value"] == pytest.approx(0.4)


def test_build_defects_exposure():
    defects = quality.build_defects(clean_metrics(clip_high_pct=30))
    assert any(defect["name"] == "exposure" for defect in defects)


def test_build_defects_shake():
    defects = quality.build_defects(clean_metrics(shake=0.05))
    assert any(defect["name"] == "shake" for defect in defects)


def test_build_defects_obstruction():
    defects = quality.build_defects(clean_metrics(obstruction=0.4))
    assert any(defect["name"] == "obstruction" for defect in defects)


def test_build_defects_horizon_above_threshold():
    defects = quality.build_defects(clean_metrics(horizon_deg=8.0))
    horizon = [defect for defect in defects if defect["name"] == "horizon"]
    assert len(horizon) == 1
    assert horizon[0]["value"] == pytest.approx(8.0 / 15.0)


def test_build_defects_level_horizon_is_clean():
    assert quality.build_defects(clean_metrics(horizon_deg=4.9)) == []


def test_build_defects_null_horizon_is_clean():
    assert quality.build_defects(clean_metrics(horizon_deg=None)) == []


def test_measure_frame_horizon_level():
    result = quality.measure_frame(horizon_lines_frame(angle_deg=0.0))
    assert result["horizon_deg"] == pytest.approx(0.0, abs=2.0)
    assert quality.build_defects(clean_metrics(horizon_deg=result["horizon_deg"])) == []


def test_measure_frame_horizon_rolled_reports_tilt_and_defect():
    result = quality.measure_frame(horizon_lines_frame(angle_deg=8.0))
    assert result["horizon_deg"] == pytest.approx(8.0, abs=2.0)
    defects = quality.build_defects(clean_metrics(horizon_deg=result["horizon_deg"]))
    horizon = [defect for defect in defects if defect["name"] == "horizon"]
    assert len(horizon) == 1
    assert horizon[0]["value"] > 0


def test_measure_frame_horizon_steep_diagonals_abstain():
    result = quality.measure_frame(steep_lines_frame())
    assert result["horizon_deg"] is None
    assert quality.build_defects(clean_metrics(horizon_deg=result["horizon_deg"])) == []


def test_measure_frame_horizon_short_dashes_abstain():
    result = quality.measure_frame(short_dashes_frame())
    assert result["horizon_deg"] is None


def test_measure_frame_horizon_scattered_angles_abstain():
    result = quality.measure_frame(scattered_lines_frame())
    assert result["horizon_deg"] is None


def test_measure_frame_horizon_none_without_lines():
    result = quality.measure_frame(np.zeros((240, 320), dtype=np.uint8))
    assert result["horizon_deg"] is None


def test_segment_horizon_all_missing_is_null():
    frames = _frames(1.0)
    horizon = quality.segment_horizon(frames)
    assert horizon is None
    metrics = quality.segment_metrics(frames, 1.0, 0.0, horizon)
    assert metrics["horizon_deg"] is None
    assert quality.build_defects(metrics) == []


def test_segment_horizon_means_measured_frames():
    frames = [{"horizon_deg": 2.0}, {"horizon_deg": None}, {"horizon_deg": 4.0}]
    assert quality.segment_horizon(frames) == pytest.approx(3.0)


def test_null_horizon_passes_schema():
    instance = {
        "file": "clip.mp4",
        "duration_s": 1.0,
        "segments": [
            {
                "id": "s1",
                "start_s": 0.0,
                "end_s": 1.0,
                "role": "hold",
                "confidence": 0.5,
                "defects": [],
                "metrics": {"horizon_deg": None},
            }
        ],
    }
    assert validate(instance, "quality") == []


def test_hold_with_obstruction_does_not_say_clean():
    role, confidence, evidence = quality.assign_role(
        role_facts(
            position="middle",
            duration_s=3.0,
            phase_type="still",
            label="static",
            speed="slow",
            steadiness="static",
            metrics=clean_metrics(obstruction=0.3),
        )
    )
    assert role == "hold"
    assert confidence == pytest.approx(0.7)
    assert evidence != ["still, clean"]
    assert any("obstruction" in item for item in evidence)


def test_build_defects_clean_segment():
    assert quality.build_defects(clean_metrics()) == []
    metrics = quality.segment_metrics([], 1.0)
    assert set(metrics) == {
        "laplacian_rel",
        "clip_high_pct",
        "clip_low_pct",
        "mean_luma",
        "shake",
        "obstruction",
        "horizon_deg",
    }


def test_intent_blur_on_fast_move():
    metrics = clean_metrics(laplacian_rel=0.3)
    defects = quality.build_defects(metrics, role="move", speed="fast")
    assert any(defect["name"] == "blur" for defect in defects)
    notes = quality.intent_evidence(defects, "move", "fast")
    assert "motion blur during a fast move (intent)" in notes


def test_end_to_end_noisy_still_is_hold(tmp_path):
    source = tmp_path / "clips"
    make_noisy_still_clip(source / "still.mp4")
    workspace = tmp_path / "workspace"

    result = run_cli(source, "--workspace", workspace, "--json")

    assert result.returncode == 0, result.stderr
    data = read_quality(workspace, "still")
    assert validate(data, "quality") == []
    assert_tiles(data["segments"], data["duration_s"])
    assert [segment["role"] for segment in data["segments"]] == ["hold"]
    assert data["motion_file"] is None
    assert any("no motion file" in warning for warning in data["warnings"])


def test_end_to_end_black_clip_is_accidental_or_exposed(tmp_path):
    source = tmp_path / "clips"
    make_black_clip(source / "black.mp4")
    workspace = tmp_path / "workspace"

    result = run_cli(source, "--workspace", workspace, "--json")

    assert result.returncode == 0, result.stderr
    data = read_quality(workspace, "black")
    assert validate(data, "quality") == []
    assert_tiles(data["segments"], data["duration_s"])
    roles = [segment["role"] for segment in data["segments"]]
    defect_names = {defect["name"] for segment in data["segments"] for defect in segment["defects"]}
    assert "accidental" in roles or "exposure" in defect_names


def test_end_to_end_planted_motion_roles(tmp_path):
    source = tmp_path / "clips"
    make_noisy_still_clip(source / "planted.mp4", seconds=6)
    workspace = tmp_path / "workspace"
    motion_dir = workspace / "motion"
    motion_dir.mkdir(parents=True)
    motion = {
        "file": "planted.mp4",
        "duration_s": 6.0,
        "phases": [
            {"start_s": 0.0, "end_s": 2.0, "type": "still", "dominant_motion": "static", "speed": "slow", "steadiness": "static"},
            {"start_s": 2.0, "end_s": 4.0, "type": "steady_move", "dominant_motion": "pan_right", "speed": "regular", "steadiness": "no_shaking"},
            {"start_s": 4.0, "end_s": 6.0, "type": "still", "dominant_motion": "static", "speed": "slow", "steadiness": "static"},
        ],
        "moves": [
            {
                "id": "m1",
                "start_s": 2.0,
                "end_s": 4.0,
                "label": "pan_right",
                "speed": "regular",
                "steadiness": "no_shaking",
                "steady_start_s": 2.0,
                "steady_end_s": 4.0,
                "confidence": 0.7,
            }
        ],
        "trajectory": [
            {"t": 0.5, "dx": 0.0, "dy": 0.0, "scale": 1.0, "roll_deg": 0.0, "inlier_ratio": 0.9, "shake": 0.0},
            {"t": 2.5, "dx": -0.05, "dy": 0.0, "scale": 1.0, "roll_deg": 0.0, "inlier_ratio": 0.9, "shake": 0.0},
            {"t": 4.5, "dx": 0.0, "dy": 0.0, "scale": 1.0, "roll_deg": 0.0, "inlier_ratio": 0.9, "shake": 0.0},
        ],
    }
    (motion_dir / "planted.json").write_text(json.dumps(motion), encoding="utf-8")

    result = run_cli(source, "--workspace", workspace, "--json")

    assert result.returncode == 0, result.stderr
    data = read_quality(workspace, "planted")
    assert validate(data, "quality") == []
    assert data["motion_file"] == str((motion_dir / "planted.json").resolve())
    assert [segment["role"] for segment in data["segments"]] == ["hold", "move", "hold"]
    assert_tiles(data["segments"], data["duration_s"])


def test_folder_skip_and_force(tmp_path):
    source = tmp_path / "clips"
    make_noisy_still_clip(source / "one.mp4", seconds=1)
    make_noisy_still_clip(source / "two.mp4", seconds=1)
    workspace = tmp_path / "workspace"

    first = run_cli(source, "--workspace", workspace, "--json")
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout) == {
        "clips": 2,
        "analyzed": 2,
        "skipped": 0,
        "output_dir": str((workspace / "quality").resolve()),
    }
    assert (workspace / "quality" / "one.json").exists()
    assert (workspace / "quality" / "two.json").exists()

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
    assert not (tmp_path / "workspace" / "quality").exists()


def test_unreadable_motion_json_falls_back(tmp_path):
    source = tmp_path / "clips"
    make_noisy_still_clip(source / "broken.mp4", seconds=3)
    workspace = tmp_path / "workspace"
    motion_dir = workspace / "motion"
    motion_dir.mkdir(parents=True)
    (motion_dir / "broken.json").write_text("not json at all", encoding="utf-8")

    result = run_cli(source, "--workspace", workspace, "--json")

    assert result.returncode == 0, result.stderr
    data = read_quality(workspace, "broken")
    assert validate(data, "quality") == []
    assert data["motion_file"] is None
    assert any("unreadable motion file" in warning for warning in data["warnings"])
    assert_tiles(data["segments"], data["duration_s"])
