import json
from datetime import datetime

from common.media import (
    earliest_creation_date,
    is_matte_export,
    is_video_name,
    parse_stamp,
    stem_timestamp,
    video_files,
)


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
    assert is_matte_export("camera 2026-09-29 21-14-12_0007.98-0028.82.mov")
    assert is_matte_export(tmp_path / "camera 2026-09-29 21-14-12_0007.98-0028.82.mov")
    assert not is_matte_export("camera 2026-09-29 21-14-12.mp4")
    assert not is_matte_export("2026-09-29 21-14-12.mp4")
    assert is_video_name("2026-09-29 21-14-12.mp4")
    assert is_video_name("camera 2026-09-29 21-14-12.mov")
    assert not is_video_name("fundal_intro.png")
    assert not is_video_name("fade_stanga.jpg")
    assert not is_video_name("Color Matte")


def test_video_files_missing_directory(tmp_path):
    assert video_files(tmp_path / "absent") == []
    assert video_files(None) == []


def test_earliest_creation_date_across_clips_and_photos(tmp_path):
    (tmp_path / "ingest.json").write_text(
        json.dumps(
            {
                "clips": [
                    {"creation_time": "2026-09-29T20:58:37"},
                    {"creation_time": "2026-09-28T10:00:00"},
                ],
                "photos": [{"creation_time": "2026-09-27T08:00:00"}],
            }
        ),
        encoding="utf-8",
    )
    assert earliest_creation_date(tmp_path) == "2026-09-27"


def test_earliest_creation_date_missing_and_empty(tmp_path):
    assert earliest_creation_date(tmp_path) is None
    (tmp_path / "ingest.json").write_text(
        json.dumps({"clips": [], "photos": []}), encoding="utf-8"
    )
    assert earliest_creation_date(tmp_path) is None
