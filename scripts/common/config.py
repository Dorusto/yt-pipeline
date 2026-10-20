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

    return data
