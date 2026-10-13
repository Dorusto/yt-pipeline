import copy

import pytest
import yaml

import common.config as config

VALID = {
    "models": {
        "architect": "xiaomi/mimo-v2.6-pro",
        "coder": "deepseek/deepseek-v4.1-flash",
        "coder_fallback": "z-ai/glm-5.3-flash",
        "vision": "z-ai/glm-5.3-flash",
        "vision_fallback": "deepseek/deepseek-v4.1-flash",
    },
    "llm_budget_usd": 5.0,
    "llm_backend": "openrouter",
}


def write_config(tmp_path, data, name="config.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def without(key):
    data = copy.deepcopy(VALID)
    del data[key]
    return data


def with_value(key, value):
    data = copy.deepcopy(VALID)
    data[key] = value
    return data


def test_valid_fixture_loads(tmp_path):
    loaded = config.load_config(write_config(tmp_path, VALID))
    assert config.model_for(loaded, "vision") == "z-ai/glm-5.3-flash"


def test_explicit_path_works(tmp_path):
    path = write_config(tmp_path, VALID)
    assert config.load_config(path)["llm_backend"] == "openrouter"


def test_explicit_missing_path_raises(tmp_path):
    with pytest.raises(config.ConfigError):
        config.load_config(tmp_path / "nope.yaml")


INVALID = [
    ("missing_models", without("models")),
    ("empty_models", with_value("models", {})),
    ("empty_model_value", with_value("models", {"vision": ""})),
    ("missing_budget", without("llm_budget_usd")),
    ("negative_budget", with_value("llm_budget_usd", -1)),
    ("bool_budget", with_value("llm_budget_usd", True)),
    ("missing_backend", without("llm_backend")),
    ("invalid_backend", with_value("llm_backend", "foo")),
]


@pytest.mark.parametrize("name,data", INVALID, ids=[case[0] for case in INVALID])
def test_invalid_config_raises(tmp_path, name, data):
    with pytest.raises(config.ConfigError):
        config.load_config(write_config(tmp_path, data))


def test_null_budget_loads(tmp_path):
    loaded = config.load_config(write_config(tmp_path, with_value("llm_budget_usd", None)))
    assert loaded["llm_budget_usd"] is None


def test_unknown_extra_key_ignored(tmp_path):
    data = copy.deepcopy(VALID)
    data["future_option"] = {"anything": True}
    loaded = config.load_config(write_config(tmp_path, data))
    assert loaded["future_option"] == {"anything": True}


def test_model_for_unknown_role_raises(tmp_path):
    loaded = config.load_config(write_config(tmp_path, VALID))
    with pytest.raises(config.ConfigError):
        config.model_for(loaded, "nope")


def test_fallback_to_example_when_config_missing(tmp_path, monkeypatch):
    example = write_config(tmp_path, VALID, name="config.yaml.example")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(config, "EXAMPLE_PATH", example)
    assert config.load_config()["llm_backend"] == "openrouter"


def test_config_wins_over_example(tmp_path, monkeypatch):
    local = write_config(tmp_path, with_value("llm_backend", "mock"))
    example = write_config(tmp_path, VALID, name="config.yaml.example")
    monkeypatch.setattr(config, "CONFIG_PATH", local)
    monkeypatch.setattr(config, "EXAMPLE_PATH", example)
    assert config.load_config()["llm_backend"] == "mock"


def test_missing_both_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(config, "EXAMPLE_PATH", tmp_path / "config.yaml.example")
    with pytest.raises(config.ConfigError):
        config.load_config()


def test_committed_example_loads():
    loaded = config.load_config(config.EXAMPLE_PATH)
    assert config.model_for(loaded, "architect") == "xiaomi/mimo-v2.6-pro"
    assert config.model_for(loaded, "coder") == "deepseek/deepseek-v4.1-flash"
    assert config.model_for(loaded, "coder_fallback") == "z-ai/glm-5.3-flash"
    assert config.model_for(loaded, "vision") == "z-ai/glm-5.3-flash"
    assert config.model_for(loaded, "vision_fallback") == "deepseek/deepseek-v4.1-flash"
    assert loaded["llm_budget_usd"] == 5.0
    assert loaded["llm_backend"] == "openrouter"
