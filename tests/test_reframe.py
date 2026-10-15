import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

import reframe
from common.schema import validate

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reframe.py"
FOURCC = cv2.VideoWriter_fourcc(*"mp4v")


def write_video(path, seconds=6, fps=10, size=(640, 360)):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), FOURCC, fps, size)
    if not writer.isOpened():
        pytest.skip("OpenCV mp4v VideoWriter is unavailable on this build")
    for index in range(seconds * fps):
        frame = np.full((size[1], size[0], 3), 20, dtype=np.uint8)
        x = 40 + index * 4
        cv2.rectangle(frame, (x, 100), (x + 80, 220), (220, 220, 220), -1)
        writer.write(frame)
    writer.release()
    return path


class SequenceDetector:
    """Face detector stub: returns the next canned box list on every call."""

    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.index = 0

    def setInputSize(self, size):
        return None

    def detect(self, frame):
        boxes = self.sequence[min(self.index, len(self.sequence) - 1)]
        self.index += 1
        if not boxes:
            return None, None
        faces = np.array([[x, y, w, h, score] for (x, y, w, h, score) in boxes], dtype=np.float32)
        return None, faces


def install_detector(monkeypatch, sequence):
    detector = SequenceDetector(sequence)
    monkeypatch.setattr(reframe, "make_detector", lambda path: detector)
    monkeypatch.setattr(reframe, "ensure_model", lambda path, url=None: Path(path))
    return detector


def run_cli(argv):
    code = reframe.main(argv)
    return code


def test_crop_size_from_aspect():
    assert reframe.crop_size(1920, 1080, 9.0, 16.0) == (608, 1080)
    assert reframe.crop_size(1920, 1080, 1.0, 1.0) == (1080, 1080)
    crop_w, crop_h = reframe.crop_size(1920, 1080, 4.0, 5.0)
    assert crop_w % 2 == 0 and crop_h % 2 == 0
    assert crop_w == 864 and crop_h == 1080


def test_path_follows_a_moving_subject(tmp_path, monkeypatch, capsys):
    clip = write_video(tmp_path / "clip.mp4")
    out = tmp_path / "crop.json"
    sequence = [[(100.0, 120.0, 80.0, 80.0, 0.9)] for _ in range(64)]
    for index, boxes in enumerate(sequence):
        boxes[0] = (100.0 + index * 12.0, 120.0, 80.0, 80.0, 0.9)
    install_detector(monkeypatch, sequence)
    code = run_cli(["--video", str(clip), "--out", str(out), "--sample-fps", "4", "--json"])
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert validate(data, "reframe") == []
    xs = [point["x"] for point in data["points"]]
    assert xs == sorted(xs)
    assert xs[-1] > xs[0]
    assert abs((xs[-1] + data["crop_w"] / 2) - (100.0 + 12.0 * (len(data["points"]) - 1) + 40.0)) < 120.0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["ok"] is True
    assert summary["cost_usd"] == 0.0


def test_smoothing_limits_speed_and_never_jumps(tmp_path, monkeypatch):
    clip = write_video(tmp_path / "clip.mp4")
    out = tmp_path / "crop.json"
    sequence = [[(100.0, 120.0, 80.0, 80.0, 0.9)] for _ in range(64)]
    for index, boxes in enumerate(sequence):
        boxes[0] = (100.0 if index < 8 else 600.0, 120.0, 80.0, 80.0, 0.9)
    install_detector(monkeypatch, sequence)
    code = run_cli(
        ["--video", str(clip), "--out", str(out), "--sample-fps", "4", "--max-speed", "0.35", "--json"]
    )
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    width = data["width"]
    dt = 1.0 / 4.0
    limit = 0.35 * width * dt + 1
    points = data["points"]
    for first, second in zip(points, points[1:]):
        assert abs(second["x"] - first["x"]) <= limit
        assert abs(second["y"] - first["y"]) <= limit


def test_window_stays_inside_the_frame(tmp_path, monkeypatch):
    clip = write_video(tmp_path / "clip.mp4")
    out = tmp_path / "crop.json"
    sequence = [[(560.0, 260.0, 80.0, 80.0, 0.9)] for _ in range(64)]
    install_detector(monkeypatch, sequence)
    code = run_cli(["--video", str(clip), "--out", str(out), "--sample-fps", "4", "--json"])
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    for point in data["points"]:
        assert 0 <= point["x"] <= data["width"] - data["crop_w"]
        assert 0 <= point["y"] <= data["height"] - data["crop_h"]


def test_no_face_falls_back_and_holds(tmp_path, monkeypatch):
    clip = write_video(tmp_path / "clip.mp4")
    out = tmp_path / "crop.json"
    sequence = [[(100.0, 120.0, 80.0, 80.0, 0.9)]] + [[] for _ in range(63)]
    install_detector(monkeypatch, sequence)
    code = run_cli(["--video", str(clip), "--out", str(out), "--sample-fps", "4", "--json"])
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert validate(data, "reframe") == []
    sources = {point["source"] for point in data["points"]}
    assert sources <= {"face", "track", "saliency", "hold"}
    assert "face" in sources
    points = data["points"]
    for first, second in zip(points, points[1:]):
        assert abs(second["x"] - first["x"]) <= 0.35 * data["width"] * 0.25 + 1


def test_rerun_without_force_skips(tmp_path, monkeypatch, capsys):
    clip = write_video(tmp_path / "clip.mp4")
    out = tmp_path / "crop.json"
    sequence = [[(100.0, 120.0, 80.0, 80.0, 0.9)] for _ in range(64)]
    install_detector(monkeypatch, sequence)
    assert run_cli(["--video", str(clip), "--out", str(out), "--json"]) == 0
    capsys.readouterr()
    first_mtime = out.stat().st_mtime_ns
    time.sleep(0.02)
    assert run_cli(["--video", str(clip), "--out", str(out), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["skipped"] is True
    assert out.stat().st_mtime_ns == first_mtime
    assert run_cli(["--video", str(clip), "--out", str(out), "--force", "--json"]) == 0
    assert out.stat().st_mtime_ns != first_mtime


def test_json_summary_has_wall_time_and_zero_cost(tmp_path, monkeypatch, capsys):
    clip = write_video(tmp_path / "clip.mp4")
    out = tmp_path / "crop.json"
    sequence = [[(100.0, 120.0, 80.0, 80.0, 0.9)] for _ in range(64)]
    install_detector(monkeypatch, sequence)
    assert run_cli(["--video", str(clip), "--out", str(out), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["ok"] is True
    assert summary["out"] == str(out)
    assert summary["points"] >= 2
    assert summary["wall_time_s"] >= 0.0
    assert summary["cost_usd"] == 0.0
