import io
import json

import pytest

import common.notify as notify
import notify as notify_cli


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Recorder:
    def __init__(self, status=200):
        self.status = status
        self.request = None

    def __call__(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        return FakeResponse(self.status)


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(notify.urllib.request, "urlopen", rec)
    monkeypatch.setenv("NTFY_URL", "http://example.test/topic")
    return rec


def test_send_uses_config_url(recorder, monkeypatch, tmp_path):
    monkeypatch.delenv("NTFY_URL", raising=False)
    config = tmp_path / "config.yaml"
    config.write_text(
        "models:\n  director: mock-model\nllm_budget_usd: null\nllm_backend: mock\n"
        "ntfy_url: http://example.test/from-config\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("YT_CONFIG", str(config))
    assert notify.send("hello") == 200
    assert recorder.request.full_url == "http://example.test/from-config"


def test_send_requires_a_configured_url(recorder, monkeypatch, tmp_path):
    monkeypatch.delenv("NTFY_URL", raising=False)
    monkeypatch.setenv("YT_CONFIG", str(tmp_path / "missing.yaml"))
    with pytest.raises(notify.NotifyError):
        notify.send("hello")


def test_send_uses_env_url(recorder, monkeypatch):
    monkeypatch.setenv("NTFY_URL", "http://example.test/topic")
    notify.send("hello")
    assert recorder.request.full_url == "http://example.test/topic"


def test_send_explicit_url_wins(recorder, monkeypatch):
    monkeypatch.setenv("NTFY_URL", "http://example.test/topic")
    notify.send("hello", url="http://other.test/t")
    assert recorder.request.full_url == "http://other.test/t"


def test_send_sets_headers_and_body(recorder):
    notify.send("hi", title="D0", priority="high", tags=["robot", "rocket"])
    headers = {k.lower(): v for k, v in recorder.request.header_items()}
    assert headers["title"] == "D0"
    assert headers["priority"] == "high"
    assert headers["tags"] == "robot,rocket"
    assert recorder.request.data == b"hi"


def test_send_empty_message_raises(recorder):
    with pytest.raises(notify.NotifyError):
        notify.send("   ")


def test_unreachable_server_raises_notify_error(monkeypatch):
    def boom(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setenv("NTFY_URL", "http://example.test/topic")
    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    with pytest.raises(notify.NotifyError):
        notify.send("hello")


def test_cli_dry_run_json(monkeypatch, capsys):
    monkeypatch.setenv("NTFY_URL", "http://example.test/topic")
    assert notify_cli.main(["hi", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sent"] is False
    assert payload["url"] == "http://example.test/topic"
    assert payload["wall_time_s"] >= 0.0
    assert payload["llm_cost_usd"] == 0.0


def test_cli_without_a_configured_url_returns_1(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("NTFY_URL", raising=False)
    monkeypatch.setenv("YT_CONFIG", str(tmp_path / "missing.yaml"))
    assert notify_cli.main(["hi", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["sent"] is False


def test_cli_success_json(monkeypatch, capsys):
    monkeypatch.setenv("NTFY_URL", "http://example.test/topic")
    monkeypatch.setattr(notify_cli, "send", lambda *a, **k: 200)
    assert notify_cli.main(["hi", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sent"] is True
    assert payload["wall_time_s"] >= 0.0
    assert payload["llm_cost_usd"] == 0.0


def test_cli_failure_returns_1(monkeypatch, capsys):
    monkeypatch.setenv("NTFY_URL", "http://example.test/topic")

    def boom(*a, **k):
        raise notify.NotifyError("nope")

    monkeypatch.setattr(notify_cli, "send", boom)
    assert notify_cli.main(["hi", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["sent"] is False
    assert payload["wall_time_s"] >= 0.0
    assert payload["llm_cost_usd"] == 0.0


def test_cli_reads_message_from_stdin(monkeypatch, capsys):
    monkeypatch.setenv("NTFY_URL", "http://example.test/topic")
    monkeypatch.setattr("sys.stdin", io.StringIO("from stdin"))
    monkeypatch.setattr(notify_cli, "send", lambda *a, **k: 200)
    assert notify_cli.main(["-", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["sent"] is True