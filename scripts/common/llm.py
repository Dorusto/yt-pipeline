"""LLM client for OpenRouter with job-level spend tracking and a mock backend.

The backend is chosen by ``llm_backend`` in ``config.yaml`` and every model id
comes from the ``models`` map there; nothing here hard-codes a model. Cloud
calls go through the ``openai`` SDK pointed at OpenRouter, with the key read
from ``OPENROUTER_API_KEY``. The client accumulates the USD cost reported in
each response ``usage`` block and refuses further calls once the per-run
``llm_budget_usd`` is reached.

Spend is recorded in a per-project ledger file (``llm_spend.json`` in the
workspace) so the budget caps a whole job across separate processes, not just
one process. An OpenRouter response without ``usage.cost`` raises instead of
being counted as free.
"""

import json
import os
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import openai
from filelock import FileLock

from common.config import load_config, model_for

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
LEDGER_NAME = "llm_spend.json"


class LLMError(RuntimeError):
    """Base error for the LLM client."""


class BudgetExceeded(LLMError):
    """Raised before a call when the job budget is already spent."""


class MissingUsageCost(LLMError):
    """Raised when an OpenRouter response does not report its USD cost."""


@dataclass
class LLMResponse:
    """One completion and the cost it added to the run."""

    text: str
    model: str
    role: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


def resolve_ledger_path(ledger_path=None, config=None):
    """Pick the spend ledger file: explicit path, env, config, workspace or none."""
    if ledger_path is not None:
        return Path(ledger_path)
    from_env = os.environ.get("YT_SPEND_LEDGER")
    if from_env:
        return Path(from_env)
    if config is not None:
        from_config = config.get("spend_ledger")
        if from_config:
            return Path(from_config)
    workspace = os.environ.get("YT_WORKSPACE")
    if workspace:
        return Path(workspace) / LEDGER_NAME
    return None


def read_ledger(path):
    """Load a spend ledger, or return an empty one when the file does not exist."""
    path = Path(path)
    if not path.exists():
        return {"total_usd": 0.0, "entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LLMError(f"spend ledger {path} is unreadable: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise LLMError(f"spend ledger {path} must be an object with an entries list")
    entries = data["entries"]
    total = 0.0
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("cost_usd"), (int, float)):
            raise LLMError(f"spend ledger {path} has an entry without a numeric cost_usd")
        total += float(entry["cost_usd"])
    return {"total_usd": round(total, 8), "entries": entries}


def write_ledger(path, entries):
    """Write the spend ledger atomically with the running total recomputed."""
    path = Path(path)
    total = round(sum(float(entry["cost_usd"]) for entry in entries), 8)
    payload = json.dumps({"total_usd": total, "entries": entries}, indent=2)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(payload + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        raise LLMError(f"could not write spend ledger {path}: {exc}") from exc


@contextmanager
def ledger_lock(path):
    """Hold an exclusive cross-process lock on a sidecar lock file for one ledger."""
    path = Path(path)
    lock = path.with_name(path.name + ".lock")
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LLMError(f"could not open spend ledger lock {lock}: {exc}") from exc
    try:
        with FileLock(str(lock)):
            yield
    except OSError as exc:
        raise LLMError(f"could not lock spend ledger {lock}: {exc}") from exc


def _value(source, name, default=None):
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _text_of(response):
    return response.choices[0].message.content


class LLMClient:
    """Base client: budget accounting, ledger and role-to-model resolution."""

    require_cost = False

    def __init__(self, budget_usd=None, config=None, ledger_path=None):
        self.budget_usd = budget_usd
        self.config = config
        self.ledger_path = resolve_ledger_path(ledger_path, config)
        self.spent_usd = 0.0
        self._entries = []
        if self.ledger_path is not None:
            ledger = read_ledger(self.ledger_path)
            self._entries = ledger["entries"]
            self.spent_usd = ledger["total_usd"]

    @property
    def remaining_usd(self):
        """Budget left for the job, or ``None`` when there is no cap."""
        if self.budget_usd is None:
            return None
        return max(0.0, self.budget_usd - self.spent_usd)

    def complete(self, role, messages, model=None, **kwargs):
        """Run one completion, charge its cost and return the response."""
        self._check_budget()
        resolved = model if model is not None else self._resolve(role)
        response = self._create(resolved, messages, **kwargs)
        prompt_tokens, completion_tokens, cost_usd = self._usage_of(response)
        self._record(
            role=role,
            model=resolved,
            cost_usd=cost_usd,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return LLMResponse(
            text=_text_of(response),
            model=resolved,
            role=role,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
        )

    def _usage_of(self, response):
        usage = _value(response, "usage")
        prompt_tokens = _value(usage, "prompt_tokens", 0) or 0
        completion_tokens = _value(usage, "completion_tokens", 0) or 0
        cost = _value(usage, "cost")
        if cost is None:
            if self.require_cost:
                raise MissingUsageCost(
                    "OpenRouter response has no usage.cost; refusing to treat the call "
                    "as free (set usage.cost or use the mock backend for offline runs)"
                )
            cost_usd = 0.0
        else:
            cost_usd = float(cost)
        return int(prompt_tokens), int(completion_tokens), cost_usd

    def _entry(self, role, model, cost_usd, prompt_tokens, completion_tokens):
        return {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "role": role,
            "model": model,
            "cost_usd": round(float(cost_usd), 8),
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
        }

    def _record(self, role, model, cost_usd, prompt_tokens, completion_tokens):
        entry = self._entry(role, model, cost_usd, prompt_tokens, completion_tokens)
        if self.ledger_path is None:
            self._entries.append(entry)
            self.spent_usd = round(self.spent_usd + float(cost_usd), 8)
            return
        with ledger_lock(self.ledger_path):
            ledger = read_ledger(self.ledger_path)
            entries = ledger["entries"] + [entry]
            write_ledger(self.ledger_path, entries)
            self._entries = entries
            self.spent_usd = round(ledger["total_usd"] + float(cost_usd), 8)

    def _check_budget(self):
        if self.budget_usd is None:
            return
        if self.ledger_path is not None:
            with ledger_lock(self.ledger_path):
                ledger = read_ledger(self.ledger_path)
            self._entries = ledger["entries"]
            self.spent_usd = ledger["total_usd"]
        if self.spent_usd >= self.budget_usd:
            ledger_note = (
                f"; spend ledger {self.ledger_path}" if self.ledger_path is not None else ""
            )
            raise BudgetExceeded(
                f"LLM budget reached: spent ${self.spent_usd:.2f} of "
                f"${self.budget_usd:.2f} (llm_budget_usd); refusing further calls"
                f"{ledger_note}"
            )

    def _resolve(self, role):
        if self.config is None:
            return role
        return model_for(self.config, role)

    def _create(self, model, messages, **kwargs):
        raise NotImplementedError


class OpenRouterClient(LLMClient):
    """LLM client backed by the OpenRouter chat completions API."""

    require_cost = True

    def __init__(
        self,
        budget_usd=None,
        config=None,
        api_key=None,
        base_url=DEFAULT_BASE_URL,
        client=None,
        ledger_path=None,
    ):
        super().__init__(budget_usd=budget_usd, config=config, ledger_path=ledger_path)
        self.base_url = base_url
        if client is not None:
            self._client = client
        else:
            if api_key is None:
                api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                raise LLMError("OPENROUTER_API_KEY is not set")
            self._client = openai.OpenAI(api_key=api_key, base_url=base_url)

    def _create(self, model, messages, **kwargs):
        return self._client.chat.completions.create(
            model=model, messages=messages, **kwargs
        )


class MockClient(LLMClient):
    """Offline client returning canned text and a fixed cost per call."""

    def __init__(
        self,
        budget_usd=None,
        config=None,
        cost_per_call=0.0,
        text="mock response",
        texts=None,
        ledger_path=None,
    ):
        super().__init__(budget_usd=budget_usd, config=config, ledger_path=ledger_path)
        self.cost_per_call = cost_per_call
        self._default_text = text
        self.text = text
        self.texts = dict(texts or {})

    def complete(self, role, messages, model=None, **kwargs):
        """Return the canned text for ``role`` (``texts[role]``, else the default)."""
        self.text = self.texts.get(role, self._default_text)
        return super().complete(role, messages, model=model, **kwargs)

    def _create(self, model, messages, **kwargs):
        usage = SimpleNamespace(
            prompt_tokens=0, completion_tokens=0, cost=self.cost_per_call
        )
        message = SimpleNamespace(content=self.text)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def make_client(config=None, ledger_path=None):
    """Build the client selected by ``llm_backend`` in the config."""
    if config is None:
        config = load_config()
    budget = config.get("llm_budget_usd")
    backend = config.get("llm_backend")
    if backend == "mock":
        mock_text = config.get("llm_mock_text")
        text = mock_text if isinstance(mock_text, str) else "mock response"
        texts = mock_text if isinstance(mock_text, dict) else None
        return MockClient(
            budget_usd=budget, config=config, ledger_path=ledger_path, text=text, texts=texts
        )
    if backend == "openrouter":
        return OpenRouterClient(budget_usd=budget, config=config, ledger_path=ledger_path)
    raise LLMError(f"unknown llm_backend '{backend}'")
