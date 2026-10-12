import json
from pathlib import Path

import pytest

import common.schema as schema

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_valid_fixture_passes():
    assert schema.validate(load("fragments_valid.json"), "fragments") == []


def test_missing_out_is_reported():
    data = load("fragments_valid.json")
    del data["fragments"][0]["out"]
    errors = schema.validate(data, "fragments")
    assert any("out" in e for e in errors)


def test_bad_timecode_is_reported():
    data = load("fragments_valid.json")
    data["fragments"][0]["in"] = "00:00:05"
    assert any("in" in e for e in schema.validate(data, "fragments"))


def test_unknown_fragment_fields_are_allowed():
    data = load("fragments_valid.json")
    data["fragments"][0]["director_note"] = "anything goes"
    assert schema.validate(data, "fragments") == []


def test_empty_fragment_list_is_rejected():
    data = {"fragments": []}
    assert any("fragments" in e for e in schema.validate(data, "fragments"))


def test_missing_fragments_key_is_rejected():
    assert schema.validate({"source_dir": "/x"}, "fragments")


def test_validate_or_raise_raises_schema_error():
    with pytest.raises(schema.SchemaError):
        schema.validate_or_raise({"fragments": [{"file": "a.MP4"}]}, "fragments")


def test_schema_path_accepts_extensionless_name():
    assert schema.schema_path("fragments").name == "fragments.schema.json"