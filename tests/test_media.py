from datetime import datetime

from common.media import is_matte_export, parse_stamp, stem_timestamp, video_files


def test_parse_stamp_from_stem_and_path():
    assert parse_stamp("camera 2026-09-29 20-58-37") == datetime(2026, 9, 29, 20, 58, 37)
    assert parse_stamp("/rec/camera 2026-09-29 20-58-37.mp4") == datetime(2026, 9, 29, 20, 58, 37)
    assert parse_stamp("/rec/2026-09-29_20-58-35.mkv") == datetime(2026, 9, 29, 20, 58, 35)
    assert parse_stamp("camera no-stamp.mp4") is None
    assert parse_stamp("camera 2026-13-40 99-99-99.mp4") is None


def test_parse_stamp_ignores_directory_timestamps():
    assert parse_stamp("/rec/2026-09-29 20-58-35/camera.mp4") is None


def test_stem_timestamp_sortable():
    assert stem_timestamp("camera 2026-09-29 20-58-37.mp4") == "2026-09-29 20-58-37"
    assert stem_timestamp("camera no-stamp.mp4") is None


def test_video_files_sorted_and_matte_filter(tmp_path):
    (tmp_path / "b.mp4").write_bytes(b"")
    (tmp_path / "a.mov").write_bytes(b"")
    (tmp_path / "clip-Alpha.mov").write_bytes(b"")
    (tmp_path / "clip_matte.mov").write_bytes(b"")
    (tmp_path / "notes.txt").write_bytes(b"")
    names = [path.name for path in video_files(tmp_path)]
    assert names == ["a.mov", "b.mp4", "clip-Alpha.mov", "clip_matte.mov"]
    names = [path.name for path in video_files(tmp_path, exclude_matte=True)]
    assert names == ["a.mov", "b.mp4"]
    assert is_matte_export(tmp_path / "clip-Alpha.mov")
    assert not is_matte_export(tmp_path / "b.mp4")


def test_video_files_missing_directory(tmp_path):
    assert video_files(tmp_path / "absent") == []
    assert video_files(None) == []
