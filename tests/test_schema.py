import json
from pathlib import Path

import pytest

import common.schema as schema

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_valid_fixture_passes():
    assert schema.validate(load("fragments_valid.json"), "fragments") == []


def test_valid_edit_fixture_passes():
    assert schema.validate(load("edit_valid.json"), "edit") == []


def test_edit_without_video_is_rejected():
    data = load("edit_valid.json")
    del data["video"]
    assert any("video" in e for e in schema.validate(data, "edit"))


def test_edit_slot_requires_reason():
    data = load("edit_valid.json")
    del data["video"][0]["reason"]
    assert any("reason" in e for e in schema.validate(data, "edit"))


def test_edit_rejects_unknown_top_level_field():
    data = load("edit_valid.json")
    data["render_cache"] = {}
    assert schema.validate(data, "edit")


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


def test_every_schema_file_is_valid_draft202012():
    import jsonschema

    for path in sorted(schema.SCHEMA_DIR.glob("*.schema.json")):
        jsonschema.Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("name", ["ingest", "motion", "quality", "describe", "similarity"])
def test_analysis_fixtures_validate(name):
    assert schema.validate(load(f"{name}_valid.json"), name) == []


@pytest.mark.parametrize("name", ["ingest", "motion", "quality", "describe", "similarity"])
def test_analysis_schemas_reject_missing_required(name):
    assert schema.validate({}, name)


def test_motion_rejects_unknown_label():
    data = load("motion_valid.json")
    data["moves"][0]["label"] = "whip_something"
    assert schema.validate(data, "motion")


def test_quality_rejects_unknown_role():
    data = load("quality_valid.json")
    data["segments"][0]["role"] = "junk"
    assert schema.validate(data, "quality")


def test_quality_rejects_unknown_defect():
    data = load("quality_valid.json")
    data["segments"][1]["defects"][0]["name"] = "wind"
    assert schema.validate(data, "quality")


def test_analysis_schemas_allow_extra_fields():
    for name in ("ingest", "motion", "quality", "describe", "similarity"):
        data = load(f"{name}_valid.json")
        data["director_note"] = "anything goes"
        assert schema.validate(data, name) == []


def test_describe_described_segment_requires_model_output():
    data = load("describe_valid.json")
    del data["segments"][0]["interest"]
    assert any("interest" in e for e in schema.validate(data, "describe"))


def test_describe_skipped_segment_requires_skip_reason():
    data = load("describe_valid.json")
    del data["segments"][1]["skip_reason"]
    assert schema.validate(data, "describe")


def test_describe_rejects_unknown_verdict_role():
    data = load("describe_valid.json")
    data["segments"][0]["verdict"]["role"] = "junk"
    assert schema.validate(data, "describe")


def test_similarity_fixture_passes():
    assert schema.validate(load("similarity_valid.json"), "similarity") == []


def test_similarity_requires_pairs_shape():
    data = load("similarity_valid.json")
    data["pairs"][0] = {"a": "0081/s5", "similarity": 0.5}
    assert any("b" in e for e in schema.validate(data, "similarity"))


def test_similarity_rejects_unknown_backend():
    data = load("similarity_valid.json")
    data["backend"] = "clip"
    assert schema.validate(data, "similarity")