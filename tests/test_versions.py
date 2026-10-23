import json
import os
import time

from common import versions


def touch(path, text, stamp):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (stamp, stamp))


def test_snapshot_keeps_the_edit_and_its_renders(tmp_path):
    now = time.time()
    touch(tmp_path / "edit.json", "{}", now - 10)
    touch(tmp_path / "recap_720p.mp4", "video", now)
    kept = versions.snapshot(tmp_path, {"feedback": ["x"]})
    assert kept.name == "v001"
    assert sorted(os.listdir(kept)) == ["edit.json", "recap_720p.mp4", "version.json"]
    assert json.loads((kept / "version.json").read_text(encoding="utf-8"))["feedback"] == ["x"]


def test_snapshot_leaves_out_a_render_older_than_the_edit(tmp_path):
    now = time.time()
    touch(tmp_path / "recap_720p.mp4", "old video", now - 10)
    touch(tmp_path / "edit.json", "{}", now)
    kept = versions.snapshot(tmp_path)
    assert (kept / "edit.json").is_file()
    assert not (kept / "recap_720p.mp4").exists()


def test_snapshot_keeps_recap_meta_when_newer_than_the_edit(tmp_path):
    now = time.time()
    touch(tmp_path / "edit.json", "{}", now - 10)
    touch(tmp_path / "recap_meta.json", "{}", now)
    kept = versions.snapshot(tmp_path)
    assert (kept / "recap_meta.json").is_file()


def test_snapshot_leaves_out_recap_meta_older_than_the_edit(tmp_path):
    now = time.time()
    touch(tmp_path / "recap_meta.json", "{}", now - 10)
    touch(tmp_path / "edit.json", "{}", now)
    kept = versions.snapshot(tmp_path)
    assert not (kept / "recap_meta.json").exists()


def test_first_snapshot_of_an_old_workspace_keeps_the_video(tmp_path):
    now = time.time()
    touch(tmp_path / "recap_720p.mp4", "old video", now - 10)
    touch(tmp_path / "edit.json", "{}", now)
    kept = versions.snapshot(tmp_path, keep_renders=True)
    assert (kept / "recap_720p.mp4").is_file()
    assert not (kept / "edit.json").exists()


def test_snapshot_skips_when_nothing_changed(tmp_path):
    now = time.time()
    touch(tmp_path / "edit.json", "{}", now - 10)
    touch(tmp_path / "recap_720p.mp4", "video", now)
    assert versions.snapshot(tmp_path) is not None
    assert versions.snapshot(tmp_path) is None
    touch(tmp_path / "edit.json", "{\"new\": 1}", now + 5)
    os.replace(tmp_path / "edit.json", tmp_path / "edit.tmp")
    os.replace(tmp_path / "edit.tmp", tmp_path / "edit.json")
    assert versions.snapshot(tmp_path).name == "v002"


def test_a_kept_version_survives_an_atomic_rewrite(tmp_path):
    now = time.time()
    touch(tmp_path / "edit.json", "{}", now - 10)
    touch(tmp_path / "recap_720p.mp4", "first", now)
    kept = versions.snapshot(tmp_path)
    touch(tmp_path / "next.mp4", "second", now + 10)
    os.replace(tmp_path / "next.mp4", tmp_path / "recap_720p.mp4")
    assert (kept / "recap_720p.mp4").read_text(encoding="utf-8") == "first"


def test_list_versions_orders_by_number(tmp_path):
    for number in (2, 10, 1):
        directory = tmp_path / "versions" / f"v{number:03d}"
        directory.mkdir(parents=True)
    (tmp_path / "versions" / "notes").mkdir()
    assert [version["number"] for version in versions.list_versions(tmp_path)] == [1, 2, 10]


def test_snapshot_keeps_thumbs(tmp_path):
    now = time.time()
    touch(tmp_path / "edit.json", "{}", now - 10)
    touch(tmp_path / "recap_720p.mp4", "video", now)
    touch(tmp_path / "thumbs" / "poster.jpg", "jpg", now)
    touch(tmp_path / "thumbs" / "shot-1.jpg", "jpg", now)
    kept = versions.snapshot(tmp_path)
    assert (kept / "thumbs" / "poster.jpg").is_file()
    assert (kept / "thumbs" / "shot-1.jpg").is_file()
    listed = versions.list_versions(tmp_path)[0]
    assert "thumbs" in listed["files"]
