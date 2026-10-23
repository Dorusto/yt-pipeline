# SPDX-License-Identifier: AGPL-3.0-or-later
"""Load and validate the project-wide AI Director configuration.

The config file lists the OpenRouter model id per role and the per-run LLM
budget. It holds no secrets: API keys stay in environment variables.
"""

import math
import os
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.yaml"
EXAMPLE_PATH = REPO_ROOT / "config.yaml.example"

VALID_BACKENDS = ("openrouter", "mock")


class ConfigError(RuntimeError):
    """Raised when the config file is missing, unreadable or invalid."""


def resolve_config_path(path=None):
    if path is not None:
        candidate = Path(path)
        if candidate.exists():
            return candidate
        raise ConfigError(f"config file not found: {candidate}")
    from_env = os.environ.get("YT_CONFIG")
    if from_env:
        candidate = Path(from_env)
        if candidate.exists():
            return candidate
        raise ConfigError(f"config file not found: {candidate}")
    if CONFIG_PATH.exists():
        return CONFIG_PATH
    if EXAMPLE_PATH.exists():
        return EXAMPLE_PATH
    raise ConfigError(f"no config file found at {CONFIG_PATH} or {EXAMPLE_PATH}")


def load_config(path=None):
    resolved = resolve_config_path(path)
    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not read config file {resolved}: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {resolved}: {exc}") from exc
    return _validate(data)


def model_for(config, role):
    models = config.get("models", {})
    if role not in models:
        known = ", ".join(sorted(models)) or "none"
        raise ConfigError(f"unknown model role '{role}'; known roles: {known}")
    return models[role]


def _validate(data):
    if not isinstance(data, dict):
        raise ConfigError("config root must be a mapping")

    models = data.get("models")
    if not isinstance(models, dict):
        raise ConfigError("models must be a mapping")
    if not models:
        raise ConfigError("models must not be empty")
    for key, value in models.items():
        if not isinstance(key, str) or not key:
            raise ConfigError("models keys must be non-empty strings")
        if not isinstance(value, str) or not value:
            raise ConfigError(f"models.{key} must be a non-empty string")

    if "llm_budget_usd" not in data:
        raise ConfigError("llm_budget_usd is required")
    budget = data["llm_budget_usd"]
    if budget is not None:
        if isinstance(budget, bool) or not isinstance(budget, (int, float)):
            raise ConfigError("llm_budget_usd must be null or a non-negative number")
        if not math.isfinite(budget) or budget < 0:
            raise ConfigError("llm_budget_usd must be null or a non-negative finite number")

    backend = data.get("llm_backend")
    if backend not in VALID_BACKENDS:
        raise ConfigError("llm_backend must be 'openrouter' or 'mock'")

    mock_text = data.get("llm_mock_text")
    if mock_text is not None:
        if isinstance(mock_text, str):
            pass
        elif isinstance(mock_text, dict):
            for key, value in mock_text.items():
                if not isinstance(key, str) or not key or not isinstance(value, str):
                    raise ConfigError(
                        "llm_mock_text must map non-empty role names to non-empty strings"
                    )
        else:
            raise ConfigError("llm_mock_text must be a string or a mapping of role to string")

    music = data.get("music")
    if music is not None:
        if not isinstance(music, dict):
            raise ConfigError("music must be a mapping")
        library = music.get("library")
        if library is not None and (not isinstance(library, str) or not library):
            raise ConfigError("music.library must be a non-empty string")

    inbox = data.get("inbox")
    if inbox is not None:
        if not isinstance(inbox, dict):
            raise ConfigError("inbox must be a mapping")
        folder = inbox.get("path")
        if folder is not None and (not isinstance(folder, str) or not folder):
            raise ConfigError("inbox.path must be a non-empty string")

    projects = data.get("projects")
    if projects is not None:
        if not isinstance(projects, dict):
            raise ConfigError("projects must be a mapping")
        folder = projects.get("path")
        if folder is not None and (not isinstance(folder, str) or not folder):
            raise ConfigError("projects.path must be a non-empty string")

    costs = data.get("costs")
    if costs is not None:
        if not isinstance(costs, dict):
            raise ConfigError("costs must be a mapping")
        ledger = costs.get("path")
        if ledger is not None and (not isinstance(ledger, str) or not ledger):
            raise ConfigError("costs.path must be a non-empty string")
        donation = costs.get("donation_url")
        if donation is not None and (not isinstance(donation, str) or not donation):
            raise ConfigError("costs.donation_url must be a non-empty string")
        links = costs.get("donation_links")
        if links is not None:
            if not isinstance(links, list):
                raise ConfigError("costs.donation_links must be a list")
            for item in links:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("label"), str)
                    or not item["label"]
                    or not isinstance(item.get("url"), str)
                    or not item["url"]
                ):
                    raise ConfigError(
                        "costs.donation_links entries must map a non-empty label and url"
                    )

    requests = data.get("requests")
    if requests is not None:
        if not isinstance(requests, dict):
            raise ConfigError("requests must be a mapping")
        path = requests.get("path")
        if path is not None and (not isinstance(path, str) or not path):
            raise ConfigError("requests.path must be a non-empty string")

    return data


MUSIC_LIBRARY_ENV = "YT_PIPELINE_MUSIC_LIBRARY"
INBOX_ENV = "YT_PIPELINE_INBOX"
PROJECTS_ENV = "YT_PIPELINE_PROJECTS"
COSTS_DB_ENV = "YT_PIPELINE_COSTS_DB"
DONATION_URL_ENV = "YT_PIPELINE_DONATION_URL"
REQUESTS_ENV = "YT_PIPELINE_REQUESTS"
DEFAULT_DONATION_LABEL = "Support the project"


def no_music_library_message(flag_name):
    """The one usage error for an unconfigured music library, naming the flag."""
    return (
        f"no music library configured: pass {flag_name} DIR, set the "
        f"{MUSIC_LIBRARY_ENV} environment variable, or set music.library in the config"
    )


def no_inbox_message(flag_name):
    """The one usage error for an unconfigured inbox, naming the flag."""
    return (
        f"no inbox configured: pass {flag_name} DIR, set the "
        f"{INBOX_ENV} environment variable, or set inbox.path in the config"
    )


def no_projects_message(flag_name):
    """The one usage error for an unconfigured projects folder, naming the flag."""
    return (
        f"no projects folder configured: pass {flag_name} DIR, set the "
        f"{PROJECTS_ENV} environment variable, or set projects.path in the config"
    )


def no_costs_message(flag_name):
    """The one usage error for an unconfigured costs ledger, naming the flag."""
    return (
        f"no costs ledger configured: pass {flag_name} PATH, set the "
        f"{COSTS_DB_ENV} environment variable, or set costs.path in the config"
    )


def no_requests_message(flag_name):
    """The one usage error for an unconfigured requests file, naming the flag."""
    return (
        f"no requests file configured: pass {flag_name} PATH, set the "
        f"{REQUESTS_ENV} environment variable, or set requests.path in the config"
    )


def music_library(cli_value=None, config=None):
    """The music library folder: CLI flag, else MUSIC_LIBRARY_ENV, else music.library.

    Returns None when none of the three is set — the caller turns that into a
    usage error naming the three ways. There is no personal default path.
    """
    if cli_value:
        return Path(cli_value).expanduser()
    from_env = os.environ.get(MUSIC_LIBRARY_ENV)
    if from_env:
        return Path(from_env).expanduser()
    data = config if config is not None else load_config()
    section = data.get("music")
    if isinstance(section, dict):
        value = section.get("library")
        if value:
            return Path(value).expanduser()
    return None


def inbox_path(cli_value=None, config=None):
    """The inbox folder: CLI flag, else INBOX_ENV, else inbox.path.

    Returns None when none of the three is set — the caller turns that into a
    usage error naming the three ways. There is no personal default path.
    """
    if cli_value:
        return Path(cli_value).expanduser()
    from_env = os.environ.get(INBOX_ENV)
    if from_env:
        return Path(from_env).expanduser()
    data = config if config is not None else load_config()
    section = data.get("inbox")
    if isinstance(section, dict):
        value = section.get("path")
        if value:
            return Path(value).expanduser()
    return None


def projects_path(cli_value=None, config=None):
    """The projects folder: CLI flag, else PROJECTS_ENV, else projects.path.

    Returns None when none of the three is set — the caller turns that into a
    usage error naming the three ways. There is no personal default path.
    """
    if cli_value:
        return Path(cli_value).expanduser()
    from_env = os.environ.get(PROJECTS_ENV)
    if from_env:
        return Path(from_env).expanduser()
    data = config if config is not None else load_config()
    section = data.get("projects")
    if isinstance(section, dict):
        value = section.get("path")
        if value:
            return Path(value).expanduser()
    return None


def costs_db(cli_value=None, config=None):
    """The costs ledger file: CLI flag, else COSTS_DB_ENV, else costs.path.

    Returns None when none of the three is set — the caller turns that into a
    usage error naming the three ways. There is no personal default path.
    """
    if cli_value:
        return Path(cli_value).expanduser()
    from_env = os.environ.get(COSTS_DB_ENV)
    if from_env:
        return Path(from_env).expanduser()
    data = config if config is not None else load_config()
    section = data.get("costs")
    if isinstance(section, dict):
        value = section.get("path")
        if value:
            return Path(value).expanduser()
    return None


def donation_links(cli_value=None, config=None):
    """The donation links: CLI flag, else DONATION_URL_ENV, else costs.donation_links / costs.donation_url.

    Returns a list of ``{"label", "url"}`` dicts, or None when none is set — it is
    optional, so the caller shows no link rather than failing. There is no
    personal default.
    """
    if cli_value:
        return [{"label": DEFAULT_DONATION_LABEL, "url": cli_value}]
    from_env = os.environ.get(DONATION_URL_ENV)
    if from_env:
        return [{"label": DEFAULT_DONATION_LABEL, "url": from_env}]
    data = config if config is not None else load_config()
    section = data.get("costs")
    if isinstance(section, dict):
        links = section.get("donation_links")
        if links:
            return [{"label": item["label"], "url": item["url"]} for item in links]
        value = section.get("donation_url")
        if value:
            return [{"label": DEFAULT_DONATION_LABEL, "url": value}]
    return None


def requests_path(cli_value=None, config=None):
    """The feedback/requests file: CLI flag, else REQUESTS_ENV, else requests.path.

    Returns None when none of the three is set — it is optional, so the caller
    hides the form rather than failing. There is no personal default path.
    """
    if cli_value:
        return Path(cli_value).expanduser()
    from_env = os.environ.get(REQUESTS_ENV)
    if from_env:
        return Path(from_env).expanduser()
    data = config if config is not None else load_config()
    section = data.get("requests")
    if isinstance(section, dict):
        value = section.get("path")
        if value:
            return Path(value).expanduser()
    return None
