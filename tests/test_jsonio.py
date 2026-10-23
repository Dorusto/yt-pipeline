import json

import pytest

from common.jsonio import load_json, now_iso, parse_model_json, write_json


def test_load_json_returns_none_for_a_missing_file(tmp_path):
    assert load_json(tmp_path / "nope.json") is None


def test_load_json_reads_a_document(tmp_path):
    path = tmp_path / "doc.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    assert load_json(path) == {"a": 1}


def test_write_json_writes_and_leaves_no_tmp(tmp_path):
    path = tmp_path / "out.json"
    write_json(path, {"a": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert list(tmp_path.glob("*.tmp")) == []


def test_now_iso_ends_with_utc_offset():
    assert now_iso().endswith("+00:00")


def test_parse_model_json_strips_a_fence():
    assert parse_model_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_model_json_raises_on_bad_json():
    with pytest.raises(ValueError):
        parse_model_json("not json")
