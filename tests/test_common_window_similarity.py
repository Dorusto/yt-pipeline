import cv2
import numpy as np
import pytest

from common import embed, window_similarity
from common.window_similarity import WindowEmbedder, window_key

FOURCC = cv2.VideoWriter_fourcc(*"mp4v")


def write_two_tone_video(path, first_color, second_color, seconds=4, fps=10, size=(64, 48)):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), FOURCC, fps, size)
    if not writer.isOpened():
        pytest.skip("OpenCV mp4v VideoWriter is unavailable on this build")
    first = np.full((size[1], size[0], 3), first_color, dtype=np.uint8)
    second = np.full((size[1], size[0], 3), second_color, dtype=np.uint8)
    total = seconds * fps
    for index in range(total):
        writer.write(first if index < total // 2 else second)
    writer.release()
    return path


def test_window_key_rounds_to_three_decimals():
    assert window_key("clip.mp4", 1.23456, 5.67891) == "clip.mp4|1.235|5.679"


def test_windows_that_differ_at_the_end_score_below_the_threshold(tmp_path):
    write_two_tone_video(tmp_path / "clip.mp4", (200, 40, 40), (40, 40, 200))
    embedder = WindowEmbedder(tmp_path, backend="hist", threshold=0.90)
    first = {"clip": "clip.mp4", "in_s": 0.0, "out_s": 2.0}
    second = {"clip": "clip.mp4", "in_s": 0.0, "out_s": 4.0}
    third = {"clip": "clip.mp4", "in_s": 0.0, "out_s": 1.8}
    assert embedder.similarity(first, second) < 0.90
    assert embedder.similarity(first, third) >= 0.90
    assert embedder.similar(first, second) is False
    assert embedder.similar(first, third) is True


def test_cache_is_written_once_and_read_back(tmp_path, monkeypatch):
    write_two_tone_video(tmp_path / "clip.mp4", (200, 40, 40), (40, 40, 200))
    calls = {"count": 0}
    original = embed.read_frames

    def counting_read_frames(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(embed, "read_frames", counting_read_frames)
    monkeypatch.setattr(window_similarity, "read_frames", counting_read_frames)

    first = WindowEmbedder(tmp_path, backend="hist")
    first.embedding("clip.mp4", 0.0, 2.0)
    first.save()
    assert calls["count"] == 1
    assert (tmp_path / "similarity_windows.json").exists()

    second = WindowEmbedder(tmp_path, backend="hist")
    second.embedding("clip.mp4", 0.0, 2.0)
    assert calls["count"] == 1


def test_photo_slot_without_a_window_is_not_similar(tmp_path):
    write_two_tone_video(tmp_path / "clip.mp4", (200, 40, 40), (40, 40, 200))
    embedder = WindowEmbedder(tmp_path, backend="hist")
    photo = {"clip": "photo.jpg", "duration_s": 3.0}
    video = {"clip": "clip.mp4", "in_s": 0.0, "out_s": 2.0}
    assert embedder.similar(photo, video) is False
    assert embedder.similar(photo, photo) is False
