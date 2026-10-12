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


def tilted_dashes_frame(width=320, height=240, angle_deg=10.0, spacing=10, length=40, thickness=3):
    gray = np.zeros((height, width), dtype=np.uint8)
    angle = math.radians(angle_deg)
    dx, dy = math.cos(angle), math.sin(angle)
    for y0 in range(-20, height + 20, spacing):
        for x0 in range(-length, width + length, length * 2):
            start = (int(round(x0)), int(round(y0)))
            end = (int(round(x0 + dx * length)), int(round(y0 + dy * length)))
            cv2.line(gray, start, end, 255, thickness)
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
            label="static",
            speed="slow",
            steadiness="static",
        )
    )
    assert role == "move"
    assert confidence == pytest.approx(0.4)
    assert evidence == ["unclassified motion"]


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


def test_build_defects_horizon_on_hold():
    defects = quality.build_defects(clean_metrics(horizon_deg=3.0), role="hold")
    horizon = [defect for defect in defects if defect["name"] == "horizon"]
    assert len(horizon) == 1
    assert horizon[0]["value"] == pytest.approx(0.6)


def test_measure_frame_edge_angle_on_tilted_dashes():
    for drawn in (0.0, 5.0, 10.0, -8.0):
        result = quality.measure_frame(tilted_dashes_frame(angle_deg=drawn))
        assert result["edge_angle_deg"] is not None
        assert result["edge_line_count"] >= 30
        assert result["edge_angle_deg"] == pytest.approx(drawn, abs=2.0)


def test_measure_frame_edge_angle_none_without_lines():
    result = quality.measure_frame(np.zeros((240, 320), dtype=np.uint8))
    assert result["edge_angle_deg"] is None
    assert result["edge_line_count"] < 30


def test_segment_horizon_relative_to_clip_median():
    frames = [
        {"edge_angle_deg": 2.0},
        {"edge_angle_deg": 4.0},
        {"edge_angle_deg": 6.0},
    ]
    clip_median = quality.clip_edge_reference(frames)
    assert clip_median == pytest.approx(4.0)
    assert quality.segment_horizon(frames[:1], clip_median) == pytest.approx(-2.0)
    assert quality.segment_horizon(frames[2:], clip_median) == pytest.approx(2.0)


def test_segment_horizon_without_measurement_is_zero():
    frames = [{"edge_angle_deg": None}, {"edge_angle_deg": None}]
    assert quality.clip_edge_reference(frames) is None
    assert quality.segment_horizon(frames, None) == 0.0


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
