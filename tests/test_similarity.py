import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

import similarity
from common.schema import validate

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "similarity.py"
FOURCC = cv2.VideoWriter_fourcc(*"mp4v")
MODEL_PATH = Path.home() / ".cache" / "yt-pipeline" / "models" / "mobilenetv2-12.onnx"


def write_solid_video(path, color, seconds=4, fps=10, size=(64, 48)):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), FOURCC, fps, size)
    if not writer.isOpened():
        pytest.skip("OpenCV mp4v VideoWriter is unavailable on this build")
    frame = np.full((size[1], size[0], 3), color, dtype=np.uint8)
    for _ in range(seconds * fps):
        writer.write(frame)
    writer.release()
    return path


def segment(id, start_s, end_s):
    return {
        "id": id,
        "start_s": start_s,
        "end_s": end_s,
        "role": "hold",
        "confidence": 0.7,
        "motion_label": "static",
        "motion_label_confidence": 0.8,
        "uncertain": False,
        "uncertain_reasons": [],
    }


def write_quality(workspace, stem, clip_path, segments, duration_s=4.0):
    quality_dir = workspace / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "file": clip_path.name,
        "path": str(clip_path),
        "duration_s": duration_s,
        "segments": segments,
    }
    (quality_dir / f"{stem}.json").write_text(json.dumps(record), encoding="utf-8")
    return record


def read_similarity(workspace):
    return json.loads((workspace / "similarity.json").read_text(encoding="utf-8"))


def test_frame_times_match_formula():
    assert similarity.frame_times(1.0, 7.0) == pytest.approx([2.0, 4.0, 6.0])
    assert similarity.frame_times(0.0, 3.0) == pytest.approx([0.5, 1.5, 2.5])


def test_segment_key_and_counter_format():
    assert similarity.segment_key("0081", "s7") == "0081/s7"
    assert (
        similarity.clip_counter("dji_mimo_20261012_124338_0081_1791817211091_video.json")
        == "0081"
    )
    assert similarity.clip_counter("clip.json") == "clip"


def test_hist_identical_frames_produce_pair(tmp_path):
    clip = write_solid_video(tmp_path / "clip.mp4", (128, 128, 128))
    write_quality(
        tmp_path,
        "dji_20261012_000000_0081_0000000000000_video",
        clip,
        [segment("s1", 0.0, 2.0), segment("s2", 2.0, 4.0)],
    )
    summary, code = similarity.run(tmp_path, backend="hist")
    assert code == 0
    assert summary["segments"] == 2
    assert summary["pairs"] == 1
    data = read_similarity(tmp_path)
    pair = data["pairs"][0]
    assert pair["a"] == "0081/s1"
    assert pair["b"] == "0081/s2"
    assert pair["similarity"] == pytest.approx(1.0, abs=0.01)


def test_hist_dark_vs_bright_stays_below_threshold(tmp_path):
    dark = write_solid_video(tmp_path / "dark.mp4", (0, 0, 0))
    bright = write_solid_video(tmp_path / "bright.mp4", (0, 0, 255))
    write_quality(
        tmp_path,
        "dji_20261012_000000_0051_0000000000000_video",
        dark,
        [segment("s1", 0.0, 4.0)],
    )
    write_quality(
        tmp_path,
        "dji_20261012_000000_0052_0000000000000_video",
        bright,
        [segment("s1", 0.0, 4.0)],
    )
    summary, code = similarity.run(tmp_path, backend="hist")
    assert code == 0
    assert summary["segments"] == 2
    assert summary["pairs"] == 0
    data = read_similarity(tmp_path)
    assert data["pairs"] == []


def test_output_validates_against_schema(tmp_path):
    clip = write_solid_video(tmp_path / "clip.mp4", (60, 120, 180))
    write_quality(
        tmp_path,
        "dji_20261012_000000_0081_0000000000000_video",
        clip,
        [segment("s1", 0.0, 2.0), segment("s2", 2.0, 4.0)],
    )
    similarity.run(tmp_path, backend="hist")
    data = read_similarity(tmp_path)
    assert validate(data, "similarity") == []
    assert data["backend"] == "hist"
    assert data["threshold"] == 0.90
    assert data["llm_cost_usd"] == 0.0


def test_json_summary_has_wall_time_and_zero_cost(tmp_path):
    clip = write_solid_video(tmp_path / "clip.mp4", (10, 200, 10))
    write_quality(
        tmp_path,
        "dji_20261012_000000_0081_0000000000000_video",
        clip,
        [segment("s1", 0.0, 4.0)],
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workspace",
            str(tmp_path),
            "--backend",
            "hist",
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert "wall_time_s" in summary
    assert summary["llm_cost_usd"] == 0.0
    assert summary["segments"] == 1
    assert summary["backend"] == "hist"


def test_rerun_without_force_is_noop_and_force_recomputes(tmp_path):
    clip = write_solid_video(tmp_path / "clip.mp4", (90, 90, 90))
    write_quality(
        tmp_path,
        "dji_20261012_000000_0081_0000000000000_video",
        clip,
        [segment("s1", 0.0, 2.0), segment("s2", 2.0, 4.0)],
    )
    first, code = similarity.run(tmp_path, backend="hist")
    assert code == 0
    assert first["skipped"] is False
    second, code = similarity.run(tmp_path, backend="hist")
    assert code == 0
    assert second["skipped"] is True
    forced, code = similarity.run(tmp_path, backend="hist", force=True)
    assert code == 0
    assert forced["skipped"] is False


def test_clips_filter_selects_substrings(tmp_path):
    first = write_solid_video(tmp_path / "first.mp4", (30, 30, 30))
    second = write_solid_video(tmp_path / "second.mp4", (200, 200, 200))
    write_quality(
        tmp_path,
        "dji_20261012_000000_0067_0000000000000_video",
        first,
        [segment("s1", 0.0, 4.0)],
    )
    write_quality(
        tmp_path,
        "dji_20261012_000000_0081_0000000000000_video",
        second,
        [segment("s1", 0.0, 4.0)],
    )
    summary, code = similarity.run(tmp_path, backend="hist", clips="0067")
    assert code == 0
    assert summary["segments"] == 1
    data = read_similarity(tmp_path)
    assert [entry["key"] for entry in data["segments"]] == ["0067/s1"]


def test_missing_workspace_is_an_error(tmp_path):
    with pytest.raises(similarity.SimilarityError):
        similarity.run(tmp_path, backend="hist")


@pytest.mark.skipif(not MODEL_PATH.exists(), reason="mobilenet ONNX model not in the cache")
def test_mobilenet_embeddings_are_finite_and_normalised(tmp_path):
    clip = write_solid_video(tmp_path / "clip.mp4", (40, 80, 160))
    write_quality(
        tmp_path,
        "dji_20261012_000000_0081_0000000000000_video",
        clip,
        [segment("s1", 0.0, 2.0), segment("s2", 2.0, 4.0)],
    )
    summary, code = similarity.run(tmp_path, backend="mobilenet")
    assert code == 0
    assert summary["model"] == "mobilenetv2-12.onnx"
    data = read_similarity(tmp_path)
    assert validate(data, "similarity") == []
    for entry in data["segments"]:
        embedding = np.asarray(entry["embedding"], dtype=np.float64)
        assert embedding.shape == (1000,)
        assert np.all(np.isfinite(embedding))
        assert np.linalg.norm(embedding) == pytest.approx(1.0, abs=0.01)
