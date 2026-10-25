# SPDX-License-Identifier: AGPL-3.0-or-later
"""Load and validate the project-wide AI Director configuration.

The config file lists the OpenRouter model id per role and the per-run LLM
budget. It holds no secrets: API keys stay in environment variables.
"""

import math
import os
from pathlib import Path

import yaml

from common.channel import CHANNEL_FIXED_PARTS, CHANNEL_STYLE_PARTS, parse_description_order

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


def _check_brand_logo(value):
    """Raise a ConfigError when ``value`` is not one of the brand options."""
    if value is not None and value not in BRAND_OPTIONS:
        raise ConfigError(f"brand.logo must be one of: {', '.join(BRAND_OPTIONS)}")


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

    beats = data.get("beats")
    if beats is not None:
        if not isinstance(beats, dict):
            raise ConfigError("beats must be a mapping")
        engine = beats.get("engine")
        if engine is not None and engine not in BEATS_ENGINES:
            raise ConfigError(f"beats.engine must be one of: {', '.join(BEATS_ENGINES)}")

    transcribe = data.get("transcribe")
    if transcribe is not None:
        if not isinstance(transcribe, dict):
            raise ConfigError("transcribe must be a mapping")
        model = transcribe.get("model")
        if model is not None and (not isinstance(model, str) or not model):
            raise ConfigError("transcribe.model must be a non-empty string")

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
        for key in ("upkeep_percent", "usd_to_eur", "owner_hours", "owner_rate_eur"):
            value = costs.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
                raise ConfigError(f"costs.{key} must be a number of zero or more")
        totals_url = costs.get("project_totals_url")
        if totals_url is not None and (not isinstance(totals_url, str) or not totals_url):
            raise ConfigError("costs.project_totals_url must be a non-empty string")

    limits = data.get("limits")
    if limits is not None:
        if not isinstance(limits, dict):
            raise ConfigError("limits must be a mapping")
        storage = limits.get("storage_gb")
        if storage is not None:
            if (
                isinstance(storage, bool)
                or not isinstance(storage, (int, float))
                or not math.isfinite(storage)
                or storage <= 0
            ):
                raise ConfigError("limits.storage_gb must be a positive number")
        warn = limits.get("credit_warn_usd")
        if warn is not None:
            if (
                isinstance(warn, bool)
                or not isinstance(warn, (int, float))
                or not math.isfinite(warn)
                or warn < 0
            ):
                raise ConfigError("limits.credit_warn_usd must be a non-negative number")

    requests = data.get("requests")
    if requests is not None:
        if not isinstance(requests, dict):
            raise ConfigError("requests must be a mapping")
        path = requests.get("path")
        if path is not None and (not isinstance(path, str) or not path):
            raise ConfigError("requests.path must be a non-empty string")

    notebook = data.get("notebook")
    if notebook is not None:
        if not isinstance(notebook, dict):
            raise ConfigError("notebook must be a mapping")
        path = notebook.get("path")
        if path is not None and (not isinstance(path, str) or not path):
            raise ConfigError("notebook.path must be a non-empty string")

    whip = data.get("whip")
    if whip is not None:
        if not isinstance(whip, dict):
            raise ConfigError("whip must be a mapping")
        min_hold = whip.get("min_hold_s")
        if min_hold is not None:
            if (
                isinstance(min_hold, bool)
                or not isinstance(min_hold, (int, float))
                or not math.isfinite(min_hold)
                or min_hold <= 0
            ):
                raise ConfigError("whip.min_hold_s must be a positive number")

    director = data.get("director")
    if director is not None:
        if not isinstance(director, dict):
            raise ConfigError("director must be a mapping")
        effort = director.get("reasoning_effort")
        if effort is not None and effort not in DIRECTOR_REASONING_EFFORTS:
            raise ConfigError(
                "director.reasoning_effort must be one of: "
                f"{', '.join(DIRECTOR_REASONING_EFFORTS)}"
            )

    web = data.get("web")
    if web is not None:
        if not isinstance(web, dict):
            raise ConfigError("web must be a mapping")
        consent = web.get("consent_text")
        if consent is not None and (not isinstance(consent, str) or not consent):
            raise ConfigError("web.consent_text must be a non-empty string")
        features = web.get("features")
        if features is not None:
            if not isinstance(features, list):
                raise ConfigError("web.features must be a list")
            for name in features:
                if name not in WEB_FEATURES:
                    raise ConfigError(
                        f"web.features entries must be one of: {', '.join(WEB_FEATURES)}"
                    )

    brand = data.get("brand")
    if brand is not None:
        if not isinstance(brand, dict):
            raise ConfigError("brand must be a mapping")
        logo = brand.get("logo")
        _check_brand_logo(logo)

    channel = data.get("channel")
    if channel is not None:
        if not isinstance(channel, dict):
            raise ConfigError("channel must be a mapping")
        for key in ("members", "github_url"):
            value = channel.get(key)
            if value is not None and (not isinstance(value, str) or not value):
                raise ConfigError(f"channel.{key} must be a non-empty string")
        fixed = channel.get("fixed")
        if fixed is not None:
            if not isinstance(fixed, dict):
                raise ConfigError("channel.fixed must be a mapping")
            for part, languages in fixed.items():
                if part not in CHANNEL_FIXED_PARTS:
                    raise ConfigError(
                        f"channel.fixed.{part} is not a known part; valid parts: "
                        f"{', '.join(CHANNEL_FIXED_PARTS)}"
                    )
                if not isinstance(languages, dict):
                    raise ConfigError(
                        f"channel.fixed.{part} must be a mapping of language codes to strings"
                    )
                for lang, text in languages.items():
                    if (
                        not isinstance(lang, str)
                        or not lang
                        or not isinstance(text, str)
                        or not text
                    ):
                        raise ConfigError(
                            f"channel.fixed.{part} must map non-empty language codes "
                            "to non-empty strings"
                        )
        style = channel.get("style")
        if style is not None:
            if not isinstance(style, dict):
                raise ConfigError("channel.style must be a mapping")
            for part, text in style.items():
                if part not in CHANNEL_STYLE_PARTS:
                    raise ConfigError(
                        f"channel.style.{part} is not a known part; valid parts: "
                        f"{', '.join(CHANNEL_STYLE_PARTS)}"
                    )
                if not isinstance(text, str) or not text:
                    raise ConfigError(f"channel.style.{part} must be a non-empty string")
                if part == "description_order":
                    try:
                        parse_description_order(text)
                    except ValueError as exc:
                        raise ConfigError(f"channel.style.description_order: {exc}") from exc

    return data


MUSIC_LIBRARY_ENV = "YT_PIPELINE_MUSIC_LIBRARY"
BEATS_ENGINE_ENV = "YT_PIPELINE_BEATS_ENGINE"
BEATS_ENGINES = ("numpy", "beat_this")
DEFAULT_BEATS_ENGINE = "numpy"
INBOX_ENV = "YT_PIPELINE_INBOX"
PROJECTS_ENV = "YT_PIPELINE_PROJECTS"
COSTS_DB_ENV = "YT_PIPELINE_COSTS_DB"
DONATION_URL_ENV = "YT_PIPELINE_DONATION_URL"
UPKEEP_PERCENT_ENV = "YT_PIPELINE_UPKEEP_PERCENT"
USD_TO_EUR_ENV = "YT_PIPELINE_USD_TO_EUR"
REQUESTS_ENV = "YT_PIPELINE_REQUESTS"
NOTEBOOK_ENV = "YT_PIPELINE_NOTEBOOK"
STORAGE_GB_ENV = "YT_PIPELINE_STORAGE_GB"
PROJECT_TOTALS_URL_ENV = "YT_PIPELINE_PROJECT_TOTALS_URL"
DEFAULT_DONATION_LABEL = "Support the project"
DEFAULT_CREDIT_WARN_USD = 1.0
WHIP_MIN_HOLD_S = 3.0
DIRECTOR_REASONING_EFFORTS = ("low", "medium", "high", "off")
DEFAULT_DIRECTOR_REASONING_EFFORT = "medium"
WEB_FEATURES = ("upload", "recap", "why", "shots", "shorts", "costs", "requests")
CONSENT_TEXT_ENV = "YT_PIPELINE_CONSENT_TEXT"
FEATURES_ENV = "YT_PIPELINE_FEATURES"
BRAND_OPTIONS = ("option-1", "option-2", "option-3")
BRAND_LOGO_ENV = "YT_PIPELINE_BRAND_LOGO"
DEFAULT_BRAND_LOGO = "option-1"


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


def no_notebook_message(flag_name):
    """The one usage error for an unconfigured notebook, naming the flag."""
    return (
        f"no notebook configured: pass {flag_name} PATH, set the "
        f"{NOTEBOOK_ENV} environment variable, or set notebook.path in the config"
    )


def no_storage_message(flag_name):
    """The one usage error for an unconfigured storage limit, naming the flag."""
    return (
        f"no storage limit configured: pass {flag_name} GB, set the "
        f"{STORAGE_GB_ENV} environment variable, or set limits.storage_gb in the config"
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


def upkeep_percent(config=None):
    """The owner's upkeep share in percent: UPKEEP_PERCENT_ENV, else costs.upkeep_percent, else 0.

    Optional: without it the costs page shows no upkeep line.
    """
    from_env = os.environ.get(UPKEEP_PERCENT_ENV)
    if from_env:
        try:
            value = float(from_env)
        except ValueError as exc:
            raise ConfigError(f"{UPKEEP_PERCENT_ENV} must be a number") from exc
        return max(0.0, value)
    data = config if config is not None else load_config()
    section = data.get("costs")
    if isinstance(section, dict) and section.get("upkeep_percent") is not None:
        try:
            value = float(section["upkeep_percent"])
        except ValueError as exc:
            raise ConfigError("costs.upkeep_percent must be a number") from exc
        return max(0.0, value)
    return 0.0


def usd_to_eur(config=None):
    """The display rate from US dollars to euros: USD_TO_EUR_ENV, else costs.usd_to_eur, else None.

    The ledger stays in US dollars (the model provider bills in dollars); with a rate
    the web app shows euros, without one it shows dollars.
    """
    from_env = os.environ.get(USD_TO_EUR_ENV)
    if from_env:
        try:
            return float(from_env)
        except ValueError as exc:
            raise ConfigError(f"{USD_TO_EUR_ENV} must be a number") from exc
    data = config if config is not None else load_config()
    section = data.get("costs")
    if isinstance(section, dict) and section.get("usd_to_eur"):
        try:
            return float(section["usd_to_eur"])
        except ValueError as exc:
            raise ConfigError("costs.usd_to_eur must be a number") from exc
    return None


def owner_time(config=None):
    """The owner's own time for the Costs page: costs.owner_hours, costs.owner_rate_eur, costs.owner_hours_note.

    Shown only on the owner's instance, never in the balance and never in /costs.json.
    Returns None when no hours are set.
    """
    data = config if config is not None else load_config()
    section = data.get("costs")
    if not isinstance(section, dict) or not section.get("owner_hours"):
        return None
    return {
        "hours": float(section["owner_hours"]),
        "rate_eur": float(section["owner_rate_eur"]) if section.get("owner_rate_eur") else None,
        "note": str(section.get("owner_hours_note") or ""),
    }


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


def notebook_path(cli_value=None, config=None):
    """The instance notebook file: CLI flag, else NOTEBOOK_ENV, else notebook.path.

    Returns None when none of the three is set — the notebook is optional, so the
    director reads nothing and the app shows no section. There is no personal default.
    """
    if cli_value:
        return Path(cli_value).expanduser()
    from_env = os.environ.get(NOTEBOOK_ENV)
    if from_env:
        return Path(from_env).expanduser()
    data = config if config is not None else load_config()
    section = data.get("notebook")
    if isinstance(section, dict):
        value = section.get("path")
        if value:
            return Path(value).expanduser()
    return None


def _positive_storage(value, source):
    """Return ``value`` as a float, or raise a ConfigError when it is not positive."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{source} must be a positive number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ConfigError(f"{source} must be a positive number")
    return number


def storage_gb(cli_value=None, config=None):
    """The storage limit in GB: CLI flag, else STORAGE_GB_ENV, else limits.storage_gb.

    Returns None when none of the three is set — the caller shows no limit. A
    non-numeric or non-positive value is a ConfigError. There is no personal default.
    """
    if cli_value is not None:
        return _positive_storage(cli_value, "storage limit")
    from_env = os.environ.get(STORAGE_GB_ENV)
    if from_env:
        try:
            value = float(from_env)
        except ValueError as exc:
            raise ConfigError(f"{STORAGE_GB_ENV} must be a number") from exc
        return _positive_storage(value, STORAGE_GB_ENV)
    data = config if config is not None else load_config()
    section = data.get("limits")
    if isinstance(section, dict):
        value = section.get("storage_gb")
        if value is not None:
            return float(value)
    return None


def credit_warn_usd(config=None):
    """The credit warning threshold in USD: limits.credit_warn_usd, default 1.0."""
    data = config if config is not None else load_config()
    section = data.get("limits")
    if isinstance(section, dict):
        value = section.get("credit_warn_usd")
        if value is not None:
            return float(value)
    return DEFAULT_CREDIT_WARN_USD


def whip_min_hold_s(config=None):
    """The whip short-hold threshold: ``whip.min_hold_s`` from the config, else 3.0."""
    data = config if config is not None else load_config()
    section = data.get("whip")
    if isinstance(section, dict):
        value = section.get("min_hold_s")
        if value is not None:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ConfigError("whip.min_hold_s must be a positive number")
            return float(value)
    return WHIP_MIN_HOLD_S


def director_reasoning_effort(cli_value=None, config=None):
    """The director's reasoning effort: CLI flag, else director.reasoning_effort, else medium.

    An unknown value is a ConfigError naming the valid values. There is no
    environment variable.
    """
    value = cli_value
    if not value:
        data = config if config is not None else load_config()
        section = data.get("director")
        if isinstance(section, dict):
            value = section.get("reasoning_effort")
    if not value:
        value = DEFAULT_DIRECTOR_REASONING_EFFORT
    if value not in DIRECTOR_REASONING_EFFORTS:
        raise ConfigError(
            "director.reasoning_effort must be one of: "
            f"{', '.join(DIRECTOR_REASONING_EFFORTS)}"
        )
    return value


def project_totals_url(cli_value=None, config=None):
    """The project totals URL: CLI flag, else PROJECT_TOTALS_URL_ENV, else costs.project_totals_url.

    Setting it marks a friend's instance: its Costs page shows only its own
    costs and no longer fetches the owner's totals. Returns None when none of
    the three is set. There is no personal default.
    """
    if cli_value:
        return cli_value
    from_env = os.environ.get(PROJECT_TOTALS_URL_ENV)
    if from_env:
        return from_env
    data = config if config is not None else load_config()
    section = data.get("costs")
    if isinstance(section, dict):
        value = section.get("project_totals_url")
        if value:
            return value
    return None


def consent_text(cli_value=None, config=None):
    """The upload-page consent text: CLI flag, else CONSENT_TEXT_ENV, else web.consent_text.

    Returns None when none of the three is set — it is optional, so the caller
    shows no checkbox rather than failing. There is no personal default.
    """
    if cli_value:
        return cli_value
    from_env = os.environ.get(CONSENT_TEXT_ENV)
    if from_env:
        return from_env
    data = config if config is not None else load_config()
    section = data.get("web")
    if isinstance(section, dict):
        value = section.get("consent_text")
        if value:
            return value
    return None


def web_features(cli_value=None, config=None):
    """The enabled web parts: CLI flag (comma-separated), else FEATURES_ENV, else web.features.

    Returns a tuple in WEB_FEATURES order. Defaults to every part when none of
    the three is set. An unknown name is a ConfigError.
    """
    names = None
    if cli_value:
        names = [part.strip() for part in cli_value.split(",") if part.strip()]
    else:
        from_env = os.environ.get(FEATURES_ENV)
        if from_env:
            names = [part.strip() for part in from_env.split(",") if part.strip()]
    if names is None:
        data = config if config is not None else load_config()
        section = data.get("web")
        if isinstance(section, dict) and section.get("features") is not None:
            names = list(section["features"])
    if names is None:
        return WEB_FEATURES
    for name in names:
        if name not in WEB_FEATURES:
            raise ConfigError(
                f"web.features entries must be one of: {', '.join(WEB_FEATURES)}"
            )
    return tuple(name for name in WEB_FEATURES if name in names)


def brand_logo(cli_value=None, config=None):
    """The app logo mark: CLI flag, else BRAND_LOGO_ENV, else brand.logo, else option-1.

    An unknown value is a ConfigError naming the three options. There is a
    default, so this is not a three-ways error.
    """
    value = cli_value
    if not value:
        value = os.environ.get(BRAND_LOGO_ENV)
    if not value:
        data = config if config is not None else load_config()
        section = data.get("brand")
        if isinstance(section, dict):
            value = section.get("logo")
    if not value:
        return DEFAULT_BRAND_LOGO
    _check_brand_logo(value)
    return value


def beats_engine(cli_value=None, config=None):
    """The beat/downbeat engine: CLI flag, else BEATS_ENGINE_ENV, else beats.engine, else numpy.

    An unknown value is a ConfigError naming the valid engines. There is a
    default, so this is not a three-ways error.
    """
    value = cli_value
    if not value:
        value = os.environ.get(BEATS_ENGINE_ENV)
    if not value:
        data = config if config is not None else load_config()
        section = data.get("beats")
        if isinstance(section, dict):
            value = section.get("engine")
    if not value:
        value = DEFAULT_BEATS_ENGINE
    if value not in BEATS_ENGINES:
        raise ConfigError(f"beats.engine must be one of: {', '.join(BEATS_ENGINES)}")
    return value
