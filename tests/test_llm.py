from types import SimpleNamespace

import pytest

from common.config import ConfigError
from common.llm import (
    BudgetExceeded,
    LLMClient,
    LLMError,
    LLMResponse,
    MockClient,
    OpenRouterClient,
    make_client,
)

MESSAGES = [{"role": "user", "content": "hi"}]

MODELS = {
    "architect": "arch/model",
    "coder": "coder/model",
    "coder_fallback": "coder/fallback",
    "vision": "vision/model",
    "vision_fallback": "vision/fallback",
}


def config_with(backend="mock", budget=None):
    return {"models": dict(MODELS), "llm_budget_usd": budget, "llm_backend": backend}


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, model, messages, **kwargs):
        self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
        return self.response


class FakeChat:
    def __init__(self, completions):
        self.completions = completions


class FakeOpenAI:
    def __init__(self, response):
        self.completions = FakeCompletions(response)
        self.chat = FakeChat(self.completions)


def fake_response(content="hello", cost=0.0042, prompt_tokens=11, completion_tokens=7):
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost=cost,
    )
    message = SimpleNamespace(content=content)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=usage,
    )


def test_mock_returns_response():
    response = MockClient().complete("vision", MESSAGES)
    assert isinstance(response, LLMResponse)
    assert response.text == "mock response"
    assert response.cost_usd == 0.0
    assert response.prompt_tokens == 0
    assert response.completion_tokens == 0
    assert response.role == "vision"


def test_mock_custom_text():
    response = MockClient(text="custom").complete("vision", MESSAGES)
    assert response.text == "custom"


def test_spend_accumulates_and_blocks():
    client = MockClient(budget_usd=0.05, cost_per_call=0.01)
    for _ in range(5):
        client.complete("vision", MESSAGES)
    assert client.spent_usd == pytest.approx(0.05)
    with pytest.raises(BudgetExceeded):
        client.complete("vision", MESSAGES)


def test_budget_message_names_spent_and_budget():
    client = MockClient(budget_usd=0.02, cost_per_call=0.03)
    client.complete("vision", MESSAGES)
    with pytest.raises(BudgetExceeded) as exc:
        client.complete("vision", MESSAGES)
    message = str(exc.value)
    assert "$0.03" in message
    assert "$0.02" in message
    assert "llm_budget_usd" in message


def test_zero_budget_blocks_first_call():
    client = MockClient(budget_usd=0, cost_per_call=0.01)
    with pytest.raises(BudgetExceeded):
        client.complete("vision", MESSAGES)
    assert client.spent_usd == 0.0


def test_no_budget_never_raises():
    client = MockClient(cost_per_call=1.0)
    for _ in range(10):
        client.complete("vision", MESSAGES)
    assert client.spent_usd == pytest.approx(10.0)


def test_remaining_usd_none_without_budget():
    assert MockClient().remaining_usd is None


def test_remaining_usd_zero_at_cap():
    client = MockClient(budget_usd=0.02, cost_per_call=0.01)
    client.complete("vision", MESSAGES)
    client.complete("vision", MESSAGES)
    assert client.remaining_usd == 0.0


def test_remaining_usd_never_negative_after_overshoot():
    client = MockClient(budget_usd=0.015, cost_per_call=0.02)
    client.complete("vision", MESSAGES)
    assert client.spent_usd == pytest.approx(0.02)
    assert client.remaining_usd == 0.0


def test_call_crossing_budget_still_returns():
    client = MockClient(budget_usd=0.015, cost_per_call=0.02)
    response = client.complete("vision", MESSAGES)
    assert response.cost_usd == pytest.approx(0.02)
    with pytest.raises(BudgetExceeded):
        client.complete("vision", MESSAGES)


def test_role_resolves_to_configured_model():
    client = MockClient(config=config_with())
    assert client.complete("vision", MESSAGES).model == "vision/model"


def test_unknown_role_raises_config_error():
    client = MockClient(config=config_with())
    with pytest.raises(ConfigError):
        client.complete("nope", MESSAGES)


def test_explicit_model_overrides_config():
    client = MockClient(config=config_with())
    assert client.complete("vision", MESSAGES, model="override").model == "override"


def test_make_client_mock_backend():
    client = make_client(config_with(backend="mock", budget=3.0))
    assert isinstance(client, MockClient)
    assert not isinstance(client, OpenRouterClient)
    assert client.budget_usd == 3.0


def test_make_client_openrouter_backend(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-key-not-used")
    client = make_client(config_with(backend="openrouter", budget=3.0))
    assert isinstance(client, OpenRouterClient)
    assert client.budget_usd == 3.0


def test_make_client_unknown_backend_raises():
    with pytest.raises(LLMError):
        make_client(config_with(backend="other"))


def test_openrouter_uses_injected_client():
    fake = FakeOpenAI(fake_response())
    client = OpenRouterClient(client=fake)
    response = client.complete(
        "vision", MESSAGES, model="vision/model", temperature=0.2
    )
    assert response.text == "hello"
    assert response.model == "vision/model"
    assert response.prompt_tokens == 11
    assert response.completion_tokens == 7
    assert response.cost_usd == pytest.approx(0.0042)
    assert client.spent_usd == pytest.approx(0.0042)
    call = fake.completions.calls[0]
    assert call["model"] == "vision/model"
    assert call["messages"] == MESSAGES
    assert call["kwargs"] == {"temperature": 0.2}


def test_openrouter_resolves_role_from_config():
    fake = FakeOpenAI(fake_response())
    client = OpenRouterClient(config=config_with(backend="openrouter"), client=fake)
    response = client.complete("coder", MESSAGES)
    assert response.model == "coder/model"
    assert fake.completions.calls[0]["model"] == "coder/model"


def test_usage_without_cost_charges_zero():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4),
    )
    client = OpenRouterClient(client=FakeOpenAI(response))
    result = client.complete("vision", MESSAGES, model="vision/model")
    assert result.cost_usd == 0.0
    assert result.prompt_tokens == 3
    assert result.completion_tokens == 4
    assert client.spent_usd == 0.0


def test_missing_usage_charges_zero():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))],
        usage=None,
    )
    client = OpenRouterClient(client=FakeOpenAI(response))
    result = client.complete("vision", MESSAGES, model="vision/model")
    assert result.cost_usd == 0.0
    assert result.prompt_tokens == 0


def test_missing_api_key_raises_without_leaking(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(LLMError) as exc:
        OpenRouterClient()
    message = str(exc.value)
    assert "OPENROUTER_API_KEY" in message
    assert "sk-" not in message


def test_injected_client_needs_no_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client = OpenRouterClient(client=FakeOpenAI(fake_response()))
    assert client.complete("vision", MESSAGES, model="vision/model").text == "hello"


def test_base_client_requires_backend():
    with pytest.raises(NotImplementedError):
        LLMClient().complete("vision", MESSAGES, model="vision/model")
