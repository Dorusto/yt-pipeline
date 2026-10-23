import json

from common.excluded import (
    EXCLUDED_FILE,
    exclude_name,
    filter_excluded,
    include_name,
    is_excluded,
    load_excluded,
    load_excluded_path,
    write_excluded,
)


def test_load_excluded_missing_file_is_empty(tmp_path):
    assert load_excluded(tmp_path) == set()
    assert load_excluded_path(tmp_path / "nope.json") == set()


def test_load_excluded_unreadable_is_empty(tmp_path):
    path = tmp_path / EXCLUDED_FILE
    path.write_text("not json", encoding="utf-8")
    assert load_excluded(tmp_path) == set()
    path.write_text(json.dumps({"excluded": "nope"}), encoding="utf-8")
    assert load_excluded(tmp_path) == set()


def test_exclude_then_include_round_trips(tmp_path):
    assert exclude_name(tmp_path, "C1634.MP4") == {"C1634.MP4"}
    assert load_excluded(tmp_path) == {"C1634.MP4"}
    assert include_name(tmp_path, "C1634.MP4") == set()
    assert load_excluded(tmp_path) == set()


def test_is_excluded_matches_name_and_stem_both_ways():
    assert is_excluded("C1634.MP4", {"C1634"}) is True
    assert is_excluded("C1634", {"C1634.MP4"}) is True
    assert is_excluded("C1634.MP4", {"C1634.MP4"}) is True
    assert is_excluded("C1634", {"C1634"}) is True
    assert is_excluded("other.mp4", {"C1634"}) is False
    assert is_excluded("C1634.MP4", set()) is False


def test_filter_excluded_drops_the_right_paths(tmp_path):
    paths = [tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "c.jpg"]
    kept = filter_excluded(paths, {"b"})
    assert [path.name for path in kept] == ["a.mp4", "c.jpg"]
    assert filter_excluded(paths, set()) == paths


def test_write_excluded_is_atomic_and_sorted(tmp_path):
    write_excluded(tmp_path, {"b.mp4", "a.mp4"})
    path = tmp_path / EXCLUDED_FILE
    assert json.loads(path.read_text(encoding="utf-8")) == {"excluded": ["a.mp4", "b.mp4"]}
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert not (tmp_path / (EXCLUDED_FILE + ".tmp")).exists()
