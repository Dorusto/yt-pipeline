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
    assert any("video/0" in e for e in schema.validate(data, "edit"))


def test_edit_photo_slot_validates():
    data = load("edit_valid.json")
    photo = next(item for item in data["video"] if item.get("kind") == "photo")
    assert photo["duration_s"] >= 2
    assert photo["ken_burns"]["move"] in {"zoom_in", "zoom_out", "pan_left", "pan_right"}
    assert schema.validate(data, "edit") == []


def test_edit_photo_slot_requires_ken_burns():
    data = load("edit_valid.json")
    photo = next(item for item in data["video"] if item.get("kind") == "photo")
    del photo["ken_burns"]
    assert any("video/1" in e for e in schema.validate(data, "edit"))


def test_edit_photo_slot_duration_is_capped():
    data = load("edit_valid.json")
    photo = next(item for item in data["video"] if item.get("kind") == "photo")
    photo["duration_s"] = 8.0
    assert any("video/1" in e for e in schema.validate(data, "edit"))


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


@pytest.mark.parametrize("name", ["ingest", "motion", "quality", "describe", "similarity", "shotlog"])
def test_analysis_fixtures_validate(name):
    assert schema.validate(load(f"{name}_valid.json"), name) == []


@pytest.mark.parametrize("name", ["ingest", "motion", "quality", "describe", "similarity", "shotlog"])
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
    for name in ("ingest", "motion", "quality", "describe", "similarity", "shotlog"):
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


def test_shotlog_fixture_passes():
    assert schema.validate(load("shotlog_valid.json"), "shotlog") == []


def test_shotlog_rejects_unknown_label_source():
    data = load("shotlog_valid.json")
    data["shots"][0]["label_source"] = "guess"
    assert schema.validate(data, "shotlog")


def test_shotlog_rejects_score_out_of_range():
    data = load("shotlog_valid.json")
    data["shots"][0]["score"] = 1.5
    assert any("score" in e for e in schema.validate(data, "shotlog"))

def test_describe_described_segment_requires_summary_and_tags():
    data = load("describe_valid.json")
    del data["segments"][0]["summary"]
    assert any("summary" in e for e in schema.validate(data, "describe"))
    data = load("describe_valid.json")
    del data["segments"][0]["tags"]
    assert any("tags" in e for e in schema.validate(data, "describe"))


def test_route_fixture_passes():
    assert schema.validate(load("route_valid.json"), "route") == []


def test_route_rejects_unknown_station():
    data = load("route_valid.json")
    data["stations"][0]["name"] = "teleport"
    assert any("teleport" in e for e in schema.validate(data, "route"))


def test_route_rejects_unknown_content_type():
    data = load("route_valid.json")
    data["content_type"] = "cooking"
    assert any("cooking" in e for e in schema.validate(data, "route"))


def test_shot_plan_fixture_passes():
    assert schema.validate(load("shot_plan_valid.json"), "shot_plan") == []


def test_shot_plan_rejects_empty_structure():
    data = load("shot_plan_valid.json")
    data["structure"] = []
    assert any("structure" in e for e in schema.validate(data, "shot_plan"))


def test_shot_plan_requires_why():
    data = load("shot_plan_valid.json")
    del data["slot_plan"][0]["why"]
    assert any("why" in e for e in schema.validate(data, "shot_plan"))


def test_edit_middle_speed_is_optional_and_bounded():
    data = load("edit_valid.json")
    video = next(item for item in data["video"] if item.get("kind") != "photo")
    assert "middle_speed" not in video
    video["middle_speed"] = 1.2
    assert schema.validate(data, "edit") == []
    video["middle_speed"] = 1.25
    assert schema.validate(data, "edit") == []
    video["middle_speed"] = 1.3
    assert any("middle_speed" in e or "video/0" in e for e in schema.validate(data, "edit"))
    video["middle_speed"] = 0.9
    assert any("middle_speed" in e or "video/0" in e for e in schema.validate(data, "edit"))


def test_edit_director_by_accepts_agent():
    data = load("edit_valid.json")
    data["meta"] = {
        "tool": "scripts/llm_director.py",
        "wall_time_s": 1.0,
        "llm_cost_usd": 0.0,
        "director": {"by": "agent", "approved": True, "notes": []},
    }
    assert schema.validate(data, "edit") == []


def test_edit_director_by_rejects_unknown():
    data = load("edit_valid.json")
    data["meta"] = {
        "tool": "scripts/llm_director.py",
        "wall_time_s": 1.0,
        "llm_cost_usd": 0.0,
        "director": {"by": "robot", "approved": True, "notes": []},
    }
    assert schema.validate(data, "edit")
