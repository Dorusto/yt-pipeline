import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from common.config import ConfigError
from common.llm import (
    BudgetExceeded,
    InvalidModelJSON,
    JSONResponse,
    LLMClient,
    LLMError,
    LLMResponse,
    MissingUsageCost,
    MockClient,
    OpenRouterClient,
    ledger_lock,
    make_client,
    read_ledger,
    resolve_ledger_path,
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


def test_usage_without_cost_raises_on_openrouter():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4),
    )
    client = OpenRouterClient(client=FakeOpenAI(response))
    with pytest.raises(MissingUsageCost):
        client.complete("vision", MESSAGES, model="vision/model")
    assert client.spent_usd == 0.0


def test_missing_usage_raises_on_openrouter():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))],
        usage=None,
    )
    client = OpenRouterClient(client=FakeOpenAI(response))
    with pytest.raises(MissingUsageCost):
        client.complete("vision", MESSAGES, model="vision/model")
    assert client.spent_usd == 0.0


def test_missing_cost_error_does_not_leak_keys():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))],
        usage=SimpleNamespace(prompt_tokens=1),
    )
    client = OpenRouterClient(client=FakeOpenAI(response))
    with pytest.raises(MissingUsageCost) as exc:
        client.complete("vision", MESSAGES, model="vision/model")
    message = str(exc.value)
    assert "usage.cost" in message
    assert "sk-" not in message


def test_no_ledger_path_means_no_ledger_file():
    client = MockClient(cost_per_call=0.01)
    client.complete("vision", MESSAGES)
    assert client.ledger_path is None
    assert client.spent_usd == pytest.approx(0.01)


def test_ledger_records_entries_and_total(tmp_path):
    ledger = tmp_path / "llm_spend.json"
    client = MockClient(cost_per_call=0.01, ledger_path=ledger)
    client.complete("vision", MESSAGES)
    client.complete("coder", MESSAGES)
    data = read_ledger(ledger)
    assert data["total_usd"] == pytest.approx(0.02)
    assert len(data["entries"]) == 2
    assert data["entries"][0]["role"] == "vision"
    assert data["entries"][0]["cost_usd"] == pytest.approx(0.01)
    assert data["entries"][1]["role"] == "coder"
    assert data["entries"][0]["ts"]


def test_ledger_caps_the_job_across_processes(tmp_path):
    ledger = tmp_path / "llm_spend.json"
    first = MockClient(budget_usd=0.05, cost_per_call=0.02, ledger_path=ledger)
    first.complete("vision", MESSAGES)
    first.complete("vision", MESSAGES)
    assert first.spent_usd == pytest.approx(0.04)
    second = MockClient(budget_usd=0.05, cost_per_call=0.02, ledger_path=ledger)
    assert second.spent_usd == pytest.approx(0.04)
    assert second.remaining_usd == pytest.approx(0.01)
    second.complete("vision", MESSAGES)
    assert second.spent_usd == pytest.approx(0.06)
    third = MockClient(budget_usd=0.05, cost_per_call=0.02, ledger_path=ledger)
    with pytest.raises(BudgetExceeded):
        third.complete("vision", MESSAGES)


def test_ledger_message_names_the_ledger(tmp_path):
    ledger = tmp_path / "llm_spend.json"
    client = MockClient(budget_usd=0.01, cost_per_call=0.02, ledger_path=ledger)
    client.complete("vision", MESSAGES)
    with pytest.raises(BudgetExceeded) as exc:
        client.complete("vision", MESSAGES)
    assert str(ledger) in str(exc.value)


def test_ledger_created_in_missing_directory(tmp_path):
    ledger = tmp_path / "workspace" / "llm_spend.json"
    client = MockClient(cost_per_call=0.01, ledger_path=ledger)
    client.complete("vision", MESSAGES)
    assert read_ledger(ledger)["total_usd"] == pytest.approx(0.01)


def test_corrupt_ledger_raises(tmp_path):
    ledger = tmp_path / "llm_spend.json"
    ledger.write_text("{not json", encoding="utf-8")
    with pytest.raises(LLMError):
        MockClient(ledger_path=ledger)


def test_ledger_entry_without_cost_raises(tmp_path):
    ledger = tmp_path / "llm_spend.json"
    ledger.write_text(
        json.dumps({"total_usd": 0.0, "entries": [{"role": "vision"}]}),
        encoding="utf-8",
    )
    with pytest.raises(LLMError):
        MockClient(ledger_path=ledger)


def test_missing_ledger_file_is_empty(tmp_path):
    assert read_ledger(tmp_path / "absent.json") == {"total_usd": 0.0, "entries": []}


def test_resolve_ledger_path_explicit(tmp_path):
    assert resolve_ledger_path(tmp_path / "x.json") == tmp_path / "x.json"


def test_resolve_ledger_path_env(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_SPEND_LEDGER", str(tmp_path / "env.json"))
    assert resolve_ledger_path() == tmp_path / "env.json"


def test_resolve_ledger_path_config(tmp_path, monkeypatch):
    monkeypatch.delenv("YT_SPEND_LEDGER", raising=False)
    monkeypatch.delenv("YT_WORKSPACE", raising=False)
    config = {"spend_ledger": str(tmp_path / "cfg.json")}
    assert resolve_ledger_path(config=config) == tmp_path / "cfg.json"


def test_resolve_ledger_path_workspace_env(tmp_path, monkeypatch):
    monkeypatch.delenv("YT_SPEND_LEDGER", raising=False)
    monkeypatch.setenv("YT_WORKSPACE", str(tmp_path))
    assert resolve_ledger_path() == tmp_path / "llm_spend.json"


def test_resolve_ledger_path_none_without_clues(monkeypatch):
    monkeypatch.delenv("YT_SPEND_LEDGER", raising=False)
    monkeypatch.delenv("YT_WORKSPACE", raising=False)
    assert resolve_ledger_path() is None


def test_make_client_wires_ledger(tmp_path, monkeypatch):
    monkeypatch.delenv("YT_SPEND_LEDGER", raising=False)
    monkeypatch.delenv("YT_WORKSPACE", raising=False)
    ledger = tmp_path / "llm_spend.json"
    client = make_client(config_with(backend="mock", budget=3.0), ledger_path=ledger)
    assert client.ledger_path == ledger


def test_openrouter_records_ledger(tmp_path):
    ledger = tmp_path / "llm_spend.json"
    fake = FakeOpenAI(fake_response())
    client = OpenRouterClient(client=fake, ledger_path=ledger)
    client.complete("vision", MESSAGES, model="vision/model")
    data = read_ledger(ledger)
    assert data["total_usd"] == pytest.approx(0.0042)
    assert data["entries"][0]["model"] == "vision/model"


def test_missing_cost_is_not_written_to_ledger(tmp_path):
    ledger = tmp_path / "llm_spend.json"
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))],
        usage=SimpleNamespace(prompt_tokens=3),
    )
    client = OpenRouterClient(client=FakeOpenAI(response), ledger_path=ledger)
    with pytest.raises(MissingUsageCost):
        client.complete("vision", MESSAGES, model="vision/model")
    assert not ledger.exists()


def test_missing_api_key_raises_without_leaking(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ConfigError) as exc:
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


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"

CONCURRENT_WORKER = """
import sys
sys.path.insert(0, sys.argv[2])
from common.llm import MockClient
client = MockClient(cost_per_call=0.01, ledger_path=sys.argv[1])
for _ in range(int(sys.argv[3])):
    client.complete("vision", [{"role": "user", "content": "hi"}])
"""


def run_worker(ledger, calls):
    return subprocess.run(
        [sys.executable, "-c", CONCURRENT_WORKER, str(ledger), str(SCRIPTS_DIR), str(calls)],
        capture_output=True,
        text=True,
    )


def test_ledger_survives_concurrent_processes(tmp_path):
    ledger = tmp_path / "llm_spend.json"
    workers = [subprocess.Popen(
        [sys.executable, "-c", CONCURRENT_WORKER, str(ledger), str(SCRIPTS_DIR), "5"]
    ) for _ in range(4)]
    for worker in workers:
        assert worker.wait() == 0
    data = read_ledger(ledger)
    assert len(data["entries"]) == 20
    assert data["total_usd"] == pytest.approx(0.2)


def test_budget_check_sees_other_process_spend(tmp_path):
    ledger = tmp_path / "llm_spend.json"
    watcher = MockClient(budget_usd=0.05, cost_per_call=0.01, ledger_path=ledger)
    assert watcher.spent_usd == 0.0
    result = run_worker(ledger, 5)
    assert result.returncode == 0, result.stderr
    with pytest.raises(BudgetExceeded):
        watcher.complete("vision", MESSAGES)


def test_ledger_lock_creates_sidecar_and_holds_exclusive(tmp_path):
    from filelock import FileLock, Timeout

    ledger = tmp_path / "llm_spend.json"
    lock_path = tmp_path / "llm_spend.json.lock"
    with ledger_lock(ledger):
        assert lock_path.exists()
        contender = FileLock(str(lock_path), timeout=0.2)
        with pytest.raises(Timeout):
            with contender:
                pass
    with FileLock(str(lock_path), timeout=0.2):
        pass


def test_ledger_lock_releases_on_body_exception(tmp_path):
    from filelock import FileLock, Timeout

    ledger = tmp_path / "llm_spend.json"
    lock_path = tmp_path / "llm_spend.json.lock"
    with pytest.raises(RuntimeError):
        with ledger_lock(ledger):
            raise RuntimeError("boom")
    with FileLock(str(lock_path), timeout=0.2):
        pass


def test_ledger_lock_creates_missing_parent_directory(tmp_path):
    ledger = tmp_path / "deep" / "workspace" / "llm_spend.json"
    lock_path = ledger.with_name(ledger.name + ".lock")
    with ledger_lock(ledger):
        assert lock_path.exists()
        assert lock_path.parent == ledger.parent


def test_mock_texts_per_role_with_default():
    client = MockClient(text="fallback", texts={"metadata": "{}", "translate": "[1] x"})
    assert client.complete("metadata", MESSAGES).text == "{}"
    assert client.complete("translate", MESSAGES).text == "[1] x"
    assert client.complete("vision", MESSAGES).text == "fallback"


def test_make_client_mock_reads_llm_mock_text_mapping():
    config = config_with(backend="mock")
    config["llm_mock_text"] = {"vision": '{"titles": []}'}
    client = make_client(config)
    assert client.complete("vision", MESSAGES).text == '{"titles": []}'
    assert client.complete("architect", MESSAGES).text == "mock response"


def test_make_client_mock_reads_llm_mock_text_string():
    config = config_with(backend="mock")
    config["llm_mock_text"] = "canned"
    client = make_client(config)
    assert client.complete("vision", MESSAGES).text == "canned"
    assert client.complete("architect", MESSAGES).text == "canned"


class SequenceClient(MockClient):
    def __init__(self, answers, **kwargs):
        super().__init__(**kwargs)
        self.answers = list(answers)
        self.calls = 0

    def complete(self, role, messages, model=None, **kwargs):
        self._default_text = self.answers[self.calls]
        self.calls += 1
        return super().complete(role, messages, model=model, **kwargs)


def test_complete_json_parses_valid_answer():
    client = MockClient(text='{"a": 1}', cost_per_call=0.01)
    answer = client.complete_json("metadata", MESSAGES)
    assert isinstance(answer, JSONResponse)
    assert answer.data == {"a": 1}
    assert answer.attempts == 1
    assert answer.cost_usd == pytest.approx(0.01)


def test_complete_json_retries_empty_answer():
    client = SequenceClient(["", '{"a": 1}'], cost_per_call=0.01)
    answer = client.complete_json("metadata", MESSAGES)
    assert answer.data == {"a": 1}
    assert answer.attempts == 2
    assert answer.cost_usd == pytest.approx(0.02)


def test_complete_json_retries_non_json_answer():
    client = SequenceClient(["not json", '{"a": 1}'], cost_per_call=0.01)
    answer = client.complete_json("metadata", MESSAGES)
    assert answer.data == {"a": 1}
    assert answer.attempts == 2
    assert answer.cost_usd == pytest.approx(0.02)


def test_complete_json_two_invalid_answers_raise():
    client = SequenceClient(["", "not json"], cost_per_call=0.01)
    with pytest.raises(InvalidModelJSON) as exc:
        client.complete_json("metadata", MESSAGES)
    assert "after one retry" in str(exc.value)


def test_complete_json_charges_both_attempts():
    client = SequenceClient(["", '{"a": 1}'], cost_per_call=0.01)
    client.complete_json("metadata", MESSAGES)
    assert client.spent_usd == pytest.approx(0.02)
