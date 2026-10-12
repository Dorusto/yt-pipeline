"""LLM client for OpenRouter with per-run spend tracking and a mock backend.

The backend is chosen by ``llm_backend`` in ``config.yaml`` and every model id
comes from the ``models`` map there; nothing here hard-codes a model. Cloud
calls go through the ``openai`` SDK pointed at OpenRouter, with the key read
from ``OPENROUTER_API_KEY``. The client accumulates the USD cost reported in
each response ``usage`` block and refuses further calls once the per-run
``llm_budget_usd`` is reached.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace

import openai

from common.config import load_config, model_for

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class LLMError(RuntimeError):
    """Base error for the LLM client."""


class BudgetExceeded(LLMError):
    """Raised before a call when the per-run budget is already spent."""


@dataclass
class LLMResponse:
    """One completion and the cost it added to the run."""

    text: str
    model: str
    role: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


def _value(source, name, default=None):
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _text_of(response):
    return response.choices[0].message.content


def _usage_of(response):
    usage = _value(response, "usage")
    prompt_tokens = _value(usage, "prompt_tokens", 0) or 0
    completion_tokens = _value(usage, "completion_tokens", 0) or 0
    cost = _value(usage, "cost")
    cost_usd = 0.0 if cost is None else float(cost)
    return int(prompt_tokens), int(completion_tokens), cost_usd


class LLMClient:
    """Base client: budget accounting and role-to-model resolution."""

    def __init__(self, budget_usd=None, config=None):
        self.budget_usd = budget_usd
        self.config = config
        self.spent_usd = 0.0

    @property
    def remaining_usd(self):
        """Budget left for this run, or ``None`` when there is no cap."""
        if self.budget_usd is None:
            return None
        return max(0.0, self.budget_usd - self.spent_usd)

    def complete(self, role, messages, model=None, **kwargs):
        """Run one completion, charge its cost and return the response."""
        self._check_budget()
        resolved = model if model is not None else self._resolve(role)
        response = self._create(resolved, messages, **kwargs)
        prompt_tokens, completion_tokens, cost_usd = _usage_of(response)
        self.spent_usd += cost_usd
        return LLMResponse(
            text=_text_of(response),
            model=resolved,
            role=role,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
        )

    def _check_budget(self):
        if self.budget_usd is not None and self.spent_usd >= self.budget_usd:
            raise BudgetExceeded(
                f"LLM budget reached: spent ${self.spent_usd:.2f} of "
                f"${self.budget_usd:.2f} (llm_budget_usd); refusing further calls"
            )

    def _resolve(self, role):
        if self.config is None:
            return role
        return model_for(self.config, role)

    def _create(self, model, messages, **kwargs):
        raise NotImplementedError


class OpenRouterClient(LLMClient):
    """LLM client backed by the OpenRouter chat completions API."""

    def __init__(
        self,
        budget_usd=None,
        config=None,
        api_key=None,
        base_url=DEFAULT_BASE_URL,
        client=None,
    ):
        super().__init__(budget_usd=budget_usd, config=config)
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
    ):
        super().__init__(budget_usd=budget_usd, config=config)
        self.cost_per_call = cost_per_call
        self.text = text

    def _create(self, model, messages, **kwargs):
        usage = SimpleNamespace(
            prompt_tokens=0, completion_tokens=0, cost=self.cost_per_call
        )
        message = SimpleNamespace(content=self.text)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def make_client(config=None):
    """Build the client selected by ``llm_backend`` in the config."""
    if config is None:
        config = load_config()
    budget = config.get("llm_budget_usd")
    backend = config.get("llm_backend")
    if backend == "mock":
        return MockClient(budget_usd=budget, config=config)
    if backend == "openrouter":
        return OpenRouterClient(budget_usd=budget, config=config)
    raise LLMError(f"unknown llm_backend '{backend}'")
