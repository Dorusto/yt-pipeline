import copy
import json
import re
from pathlib import Path

import common.schema as schema

FIXTURES = Path(__file__).parent / "fixtures"
PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "director.md"


def plan():
    return copy.deepcopy(json.loads((FIXTURES / "director_plan_valid.json").read_text(encoding="utf-8")))


def errors(data):
    return schema.validate(data, "director_plan")


def test_valid_plan_passes():
    assert errors(plan()) == []


def test_plan_rejects_empty_shots():
    data = plan()
    data["shots"] = []
    assert any("shots" in e for e in errors(data))


def test_shot_requires_purpose_and_why():
    for key in ("purpose", "why"):
        data = plan()
        del data["shots"][0][key]
        assert any("shots/0" in e for e in errors(data)), key


def test_structure_needs_a_reason():
    data = plan()
    del data["structure"]["why"]
    assert any("structure" in e for e in errors(data))


def test_unknown_technique_and_structure_are_rejected():
    data = plan()
    data["shots"][0]["technique"] = "whip_pan_spam"
    assert any("shots/0/technique" in e for e in errors(data))
    data = plan()
    data["structure"]["kind"] = "random"
    assert any("structure/kind" in e for e in errors(data))


def test_moment_needs_a_time_or_a_hint():
    data = plan()
    data["shots"][0]["moment"] = {}
    assert any("shots/0/moment" in e for e in errors(data))
    data["shots"][0]["moment"] = {"at_s": 2.0}
    assert errors(data) == []


def test_moment_is_optional_for_a_photo():
    data = plan()
    data["shots"][0]["shot_key"] = "photo_01/p1"
    del data["shots"][0]["moment"]
    assert errors(data) == []


def test_pieces_is_bounded_and_extra_keys_are_rejected():
    data = plan()
    data["shots"][2]["pieces"] = 9
    assert any("shots/2/pieces" in e for e in errors(data))
    data = plan()
    data["shots"][0]["swap_with"] = "0013/s1"
    assert any("shots/0" in e for e in errors(data))


def test_prompt_names_every_technique_and_structure():
    text = PROMPT.read_text(encoding="utf-8")
    spec = schema.load_schema("director_plan")["properties"]
    for value in spec["shots"]["items"]["properties"]["technique"]["enum"]:
        assert f"`{value}`" in text, value
    for value in spec["structure"]["properties"]["kind"]["enum"]:
        assert f"`{value}`" in text, value


def test_prompt_example_matches_the_schema():
    text = PROMPT.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)```", text, re.S)
    assert blocks, "prompts/director.md needs a json example"
    example = json.loads(blocks[-1])
    assert errors(example) == []
