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


def test_yt_config_env_wins(tmp_path, monkeypatch):
    env_config = write_config(tmp_path, with_value("llm_backend", "mock"), name="env.yaml")
    local = write_config(tmp_path, VALID, name="config.yaml")
    monkeypatch.setattr(config, "CONFIG_PATH", local)
    monkeypatch.setenv("YT_CONFIG", str(env_config))
    assert config.resolve_config_path() == env_config
    assert config.load_config()["llm_backend"] == "mock"


def test_yt_config_env_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_CONFIG", str(tmp_path / "nope.yaml"))
    with pytest.raises(config.ConfigError) as exc:
        config.resolve_config_path()
    assert "nope.yaml" in str(exc.value)


def test_llm_mock_text_string_and_mapping_pass():
    assert config._validate(with_value("llm_mock_text", "canned"))["llm_mock_text"] == "canned"
    mapping = with_value("llm_mock_text", {"metadata": "{}", "translate": "[1] x"})
    assert config._validate(mapping)["llm_mock_text"]["metadata"] == "{}"


def test_llm_mock_text_wrong_type_raises():
    with pytest.raises(config.ConfigError):
        config._validate(with_value("llm_mock_text", 5))
    with pytest.raises(config.ConfigError):
        config._validate(with_value("llm_mock_text", {"metadata": 5}))


def test_music_library_flag_env_and_config(tmp_path, monkeypatch):
    monkeypatch.delenv(config.MUSIC_LIBRARY_ENV, raising=False)
    config_path = write_config(tmp_path, with_value("music", {"library": str(tmp_path / "from-config")}))
    monkeypatch.setenv("YT_CONFIG", str(config_path))
    assert config.music_library(str(tmp_path / "from-flag")) == (tmp_path / "from-flag").expanduser()
    assert config.music_library() == (tmp_path / "from-config").expanduser()
    monkeypatch.setenv(config.MUSIC_LIBRARY_ENV, str(tmp_path / "from-env"))
    assert config.music_library() == (tmp_path / "from-env").expanduser()
    assert config.music_library(str(tmp_path / "from-flag")) == (tmp_path / "from-flag").expanduser()


def test_music_library_config_argument_wins_over_config_file(tmp_path, monkeypatch):
    monkeypatch.delenv(config.MUSIC_LIBRARY_ENV, raising=False)
    explicit = {"music": {"library": str(tmp_path / "explicit")}}
    assert config.music_library(config=explicit) == (tmp_path / "explicit").expanduser()


def test_music_library_none_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv(config.MUSIC_LIBRARY_ENV, raising=False)
    monkeypatch.setenv("YT_CONFIG", str(write_config(tmp_path, VALID)))
    assert config.music_library() is None


def test_music_section_validated():
    assert config._validate(with_value("music", {"library": "/tracks"}))["music"]["library"] == "/tracks"
    assert config._validate(with_value("music", {}))["music"] == {}
    for bad in ("nope", {"library": ""}, {"library": 5}):
        with pytest.raises(config.ConfigError):
            config._validate(with_value("music", bad))


def test_transcribe_section_validated():
    assert config._validate(with_value("transcribe", {"model": "medium"}))["transcribe"]["model"] == "medium"
    assert config._validate(with_value("transcribe", {}))["transcribe"] == {}
    with pytest.raises(config.ConfigError):
        config._validate(with_value("transcribe", "nope"))
    for bad in ("", 5):
        with pytest.raises(config.ConfigError) as exc:
            config._validate(with_value("transcribe", {"model": bad}))
        assert "transcribe.model" in str(exc.value)


def test_no_music_library_message_names_the_flag():
    for flag in ("--library", "--music-dir"):
        message = config.no_music_library_message(flag)
        assert "no music library configured" in message
        assert flag in message
        assert config.MUSIC_LIBRARY_ENV in message
        assert "music.library" in message


def test_no_inbox_message_names_the_flag():
    message = config.no_inbox_message("--inbox")
    assert "no inbox configured" in message
    assert "--inbox" in message
    assert config.INBOX_ENV in message
    assert "inbox.path" in message


def test_inbox_path_flag_env_and_config(tmp_path, monkeypatch):
    monkeypatch.delenv(config.INBOX_ENV, raising=False)
    config_path = write_config(tmp_path, with_value("inbox", {"path": str(tmp_path / "from-config")}))
    monkeypatch.setenv("YT_CONFIG", str(config_path))
    assert config.inbox_path(str(tmp_path / "from-flag")) == (tmp_path / "from-flag").expanduser()
    assert config.inbox_path() == (tmp_path / "from-config").expanduser()
    monkeypatch.setenv(config.INBOX_ENV, str(tmp_path / "from-env"))
    assert config.inbox_path() == (tmp_path / "from-env").expanduser()
    assert config.inbox_path(str(tmp_path / "from-flag")) == (tmp_path / "from-flag").expanduser()


def test_inbox_path_none_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv(config.INBOX_ENV, raising=False)
    monkeypatch.setenv("YT_CONFIG", str(write_config(tmp_path, VALID)))
    assert config.inbox_path() is None


def test_inbox_section_validated():
    assert config._validate(with_value("inbox", {"path": "/drop"}))["inbox"]["path"] == "/drop"
    assert config._validate(with_value("inbox", {}))["inbox"] == {}
    for bad in ("nope", {"path": ""}, {"path": 5}):
        with pytest.raises(config.ConfigError):
            config._validate(with_value("inbox", bad))


def test_no_projects_message_names_the_flag():
    message = config.no_projects_message("--projects")
    assert "no projects folder configured" in message
    assert "--projects" in message
    assert config.PROJECTS_ENV in message
    assert "projects.path" in message


def test_projects_path_flag_env_and_config(tmp_path, monkeypatch):
    monkeypatch.delenv(config.PROJECTS_ENV, raising=False)
    config_path = write_config(tmp_path, with_value("projects", {"path": str(tmp_path / "from-config")}))
    monkeypatch.setenv("YT_CONFIG", str(config_path))
    assert config.projects_path(str(tmp_path / "from-flag")) == (tmp_path / "from-flag").expanduser()
    assert config.projects_path() == (tmp_path / "from-config").expanduser()
    monkeypatch.setenv(config.PROJECTS_ENV, str(tmp_path / "from-env"))
    assert config.projects_path() == (tmp_path / "from-env").expanduser()
    assert config.projects_path(str(tmp_path / "from-flag")) == (tmp_path / "from-flag").expanduser()


def test_projects_path_config_argument_wins_over_config_file(tmp_path, monkeypatch):
    monkeypatch.delenv(config.PROJECTS_ENV, raising=False)
    explicit = {"projects": {"path": str(tmp_path / "explicit")}}
    assert config.projects_path(config=explicit) == (tmp_path / "explicit").expanduser()


def test_projects_path_none_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv(config.PROJECTS_ENV, raising=False)
    monkeypatch.setenv("YT_CONFIG", str(write_config(tmp_path, VALID)))
    assert config.projects_path() is None


def test_projects_section_validated():
    assert config._validate(with_value("projects", {"path": "/days"}))["projects"]["path"] == "/days"
    assert config._validate(with_value("projects", {}))["projects"] == {}
    for bad in ("nope", {"path": ""}, {"path": 5}):
        with pytest.raises(config.ConfigError):
            config._validate(with_value("projects", bad))


def test_donation_links_flag_env_and_config(tmp_path, monkeypatch):
    monkeypatch.delenv(config.DONATION_URL_ENV, raising=False)
    assert config.donation_links("https://example.org/a") == [
        {"label": config.DEFAULT_DONATION_LABEL, "url": "https://example.org/a"}
    ]
    monkeypatch.setenv(config.DONATION_URL_ENV, "https://example.org/b")
    assert config.donation_links() == [
        {"label": config.DEFAULT_DONATION_LABEL, "url": "https://example.org/b"}
    ]
    monkeypatch.delenv(config.DONATION_URL_ENV, raising=False)
    data = {"costs": {"donation_links": [{"label": "Ko-fi", "url": "https://example.org/k"}]}}
    assert config.donation_links(None, data) == [
        {"label": "Ko-fi", "url": "https://example.org/k"}
    ]
    assert config.donation_links(None, {"costs": {"donation_url": "https://example.org/one"}}) == [
        {"label": config.DEFAULT_DONATION_LABEL, "url": "https://example.org/one"}
    ]
    assert config.donation_links(None, {}) is None


def test_donation_links_section_validated():
    with pytest.raises(config.ConfigError):
        config._validate(
            {"models": {"coder": "x"}, "llm_budget_usd": 1.0, "llm_backend": "mock",
             "costs": {"donation_links": [{"label": "", "url": "https://example.org"}]}}
        )


def test_storage_gb_flag_env_and_config(tmp_path, monkeypatch):
    monkeypatch.delenv(config.STORAGE_GB_ENV, raising=False)
    assert config.storage_gb(25, {}) == 25.0
    monkeypatch.setenv(config.STORAGE_GB_ENV, "40")
    assert config.storage_gb() == 40.0
    assert config.storage_gb(25, {}) == 25.0
    monkeypatch.delenv(config.STORAGE_GB_ENV, raising=False)
    assert config.storage_gb(None, {"limits": {"storage_gb": 100}}) == 100.0
    assert config.storage_gb(None, {}) is None


def test_storage_gb_section_validated(tmp_path):
    for bad in (0, -1, "x", True):
        with pytest.raises(config.ConfigError):
            config.load_config(write_config(tmp_path, with_value("limits", {"storage_gb": bad})))


def test_storage_gb_flag_and_env_reject_non_positive(monkeypatch):
    monkeypatch.delenv(config.STORAGE_GB_ENV, raising=False)
    for bad in (0, -1):
        with pytest.raises(config.ConfigError):
            config.storage_gb(bad, {})
    monkeypatch.setenv(config.STORAGE_GB_ENV, "0")
    with pytest.raises(config.ConfigError):
        config.storage_gb()
    monkeypatch.setenv(config.STORAGE_GB_ENV, "-5")
    with pytest.raises(config.ConfigError):
        config.storage_gb()


def test_credit_warn_usd_default_and_config():
    assert config.credit_warn_usd(config={}) == 1.0
    assert config.credit_warn_usd(config={"limits": {"credit_warn_usd": 2.5}}) == 2.5


def test_credit_warn_usd_section_validated():
    with pytest.raises(config.ConfigError):
        config._validate(with_value("limits", {"credit_warn_usd": -1}))


def test_project_totals_url_flag_env_and_config(tmp_path, monkeypatch):
    monkeypatch.delenv(config.PROJECT_TOTALS_URL_ENV, raising=False)
    assert config.project_totals_url("https://flag.example/costs.json", {}) == (
        "https://flag.example/costs.json"
    )
    monkeypatch.setenv(config.PROJECT_TOTALS_URL_ENV, "https://env.example/costs.json")
    assert config.project_totals_url() == "https://env.example/costs.json"
    assert config.project_totals_url("https://flag.example/costs.json", {}) == (
        "https://flag.example/costs.json"
    )
    monkeypatch.delenv(config.PROJECT_TOTALS_URL_ENV, raising=False)
    assert config.project_totals_url(None, {"costs": {"project_totals_url": "https://cfg.example/costs.json"}}) == (
        "https://cfg.example/costs.json"
    )
    assert config.project_totals_url(None, {}) is None


def test_project_totals_url_section_validated():
    with pytest.raises(config.ConfigError):
        config._validate(with_value("costs", {"project_totals_url": ""}))


def test_no_storage_message_names_the_flag():
    message = config.no_storage_message("--storage-gb")
    assert "no storage limit configured" in message
    assert "--storage-gb" in message
    assert config.STORAGE_GB_ENV in message
    assert "limits.storage_gb" in message


def test_no_project_totals_message_names_the_flag():
    message = config.no_project_totals_message("--project-totals-url")
    assert "no project totals URL configured" in message
    assert "--project-totals-url" in message
    assert config.PROJECT_TOTALS_URL_ENV in message
    assert "costs.project_totals_url" in message


def test_brand_logo_defaults_to_option_1(monkeypatch):
    monkeypatch.delenv(config.BRAND_LOGO_ENV, raising=False)
    assert config.brand_logo(config={}) == "option-1"


def test_brand_logo_reads_flag_env_and_config(monkeypatch):
    monkeypatch.delenv(config.BRAND_LOGO_ENV, raising=False)
    assert config.brand_logo("option-2", {"brand": {"logo": "option-3"}}) == "option-2"
    monkeypatch.setenv(config.BRAND_LOGO_ENV, "option-2")
    assert config.brand_logo(None, {"brand": {"logo": "option-3"}}) == "option-2"
    assert config.brand_logo("option-1", {"brand": {"logo": "option-3"}}) == "option-1"
    monkeypatch.delenv(config.BRAND_LOGO_ENV, raising=False)
    assert config.brand_logo(None, {"brand": {"logo": "option-3"}}) == "option-3"


def test_brand_logo_rejects_an_unknown_value(monkeypatch):
    monkeypatch.delenv(config.BRAND_LOGO_ENV, raising=False)
    with pytest.raises(config.ConfigError):
        config.brand_logo("nope", {})
    with pytest.raises(config.ConfigError):
        config.brand_logo(None, {"brand": {"logo": "nope"}})


def test_brand_logo_invalid_in_config_is_rejected(tmp_path):
    with pytest.raises(config.ConfigError):
        config.load_config(write_config(tmp_path, with_value("brand", {"logo": "nope"})))


def test_upkeep_percent_rejects_non_numeric(monkeypatch):
    monkeypatch.setenv(config.UPKEEP_PERCENT_ENV, "abc")
    with pytest.raises(config.ConfigError):
        config.upkeep_percent()
    monkeypatch.delenv(config.UPKEEP_PERCENT_ENV, raising=False)
    with pytest.raises(config.ConfigError):
        config.upkeep_percent(config={"costs": {"upkeep_percent": "abc"}})


def test_usd_to_eur_rejects_non_numeric(monkeypatch):
    monkeypatch.setenv(config.USD_TO_EUR_ENV, "abc")
    with pytest.raises(config.ConfigError):
        config.usd_to_eur()
    monkeypatch.delenv(config.USD_TO_EUR_ENV, raising=False)
    with pytest.raises(config.ConfigError):
        config.usd_to_eur(config={"costs": {"usd_to_eur": "abc"}})


def test_channel_fixed_loads(tmp_path):
    data = copy.deepcopy(VALID)
    data["channel"] = {
        "members": "A, B",
        "github_url": "https://example.invalid/c",
        "fixed": {
            "members_sentence": {"ro": "Mulțumesc {members}.", "en": "Thanks {members}."},
            "closing": {"ro": "Pe curând.", "en": "See you."},
        },
    }
    loaded = config.load_config(write_config(tmp_path, data))
    assert loaded["channel"]["fixed"]["closing"]["ro"] == "Pe curând."


def test_channel_invalid_raises(tmp_path):
    for bad in (
        "nope",
        {"members": ""},
        {"github_url": 5},
        {"fixed": "nope"},
        {"fixed": {"closing": "nope"}},
        {"fixed": {"closing": {"ro": ""}}},
        {"fixed": {"closing": {"": "x"}}},
        {"fixed": {"closing": {"ro": 5}}},
    ):
        data = copy.deepcopy(VALID)
        data["channel"] = bad
        with pytest.raises(config.ConfigError):
            config.load_config(write_config(tmp_path, data))


def test_channel_fixed_unknown_key_raises(tmp_path):
    data = copy.deepcopy(VALID)
    data["channel"] = {"fixed": {"nope": {"ro": "x"}}}
    with pytest.raises(config.ConfigError) as exc:
        config.load_config(write_config(tmp_path, data))
    assert "nope" in str(exc.value)


def test_channel_style_loads(tmp_path):
    data = copy.deepcopy(VALID)
    data["channel"] = {
        "style": {
            "title_style": "short and concrete",
            "pinned_comment_voice": "plain first person",
        },
    }
    loaded = config.load_config(write_config(tmp_path, data))
    assert loaded["channel"]["style"]["title_style"] == "short and concrete"
    assert loaded["channel"]["style"]["pinned_comment_voice"] == "plain first person"


def test_channel_style_invalid_raises(tmp_path):
    for bad in (
        "nope",
        {"title_style": ""},
        {"title_style": 5},
        {"nope": "x"},
    ):
        data = copy.deepcopy(VALID)
        data["channel"] = {"style": bad}
        with pytest.raises(config.ConfigError):
            config.load_config(write_config(tmp_path, data))


def test_channel_style_description_order_loads(tmp_path):
    data = copy.deepcopy(VALID)
    data["channel"] = {"style": {"description_order": "closing, intro, subscribe"}}
    loaded = config.load_config(write_config(tmp_path, data))
    assert loaded["channel"]["style"]["description_order"] == "closing, intro, subscribe"


def test_channel_style_description_order_unknown_part_raises(tmp_path):
    data = copy.deepcopy(VALID)
    data["channel"] = {"style": {"description_order": "intro, nope"}}
    with pytest.raises(config.ConfigError) as exc:
        config.load_config(write_config(tmp_path, data))
    assert "nope" in str(exc.value)
