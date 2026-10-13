import json
import os

import agent_watchdog as watchdog


def test_spend_alert_once_per_day():
    state = {}
    assert watchdog.check_spend(1.0, 3.0, state, "2026-10-13") is None
    assert "4.50" in watchdog.check_spend(4.5, 3.0, state, "2026-10-13")
    assert watchdog.check_spend(5.0, 3.0, state, "2026-10-13") is None
    assert watchdog.check_spend(5.0, 3.0, state, "2026-10-14") is not None


def test_spend_unknown_is_silent():
    assert watchdog.check_spend(None, 3.0, {}, "2026-10-13") is None


def test_stall_needs_running_agent():
    assert watchdog.check_stall(False, 100.0, 100.0 + 9999, 3600, {}) is None


def test_stall_alert_once_per_episode():
    state = {}
    assert watchdog.check_stall(True, 1000.0, 1000.0 + 1800, 3600, state) is None
    assert watchdog.check_stall(True, 1000.0, 1000.0 + 7300, 3600, state) is not None
    assert watchdog.check_stall(True, 1000.0, 1000.0 + 9000, 3600, state) is None
    assert watchdog.check_stall(True, 5000.0, 5000.0 + 7300, 3600, state) is not None


def test_newest_mtime_skips_venv(tmp_path):
    kept = tmp_path / "a.txt"
    kept.write_text("x")
    os.utime(kept, (1000, 1000))
    venv = tmp_path / ".venv"
    venv.mkdir()
    ignored = venv / "b.txt"
    ignored.write_text("y")
    os.utime(ignored, (9000, 9000))
    assert watchdog.newest_mtime([tmp_path]) == 1000


def test_read_api_key_prefers_env(tmp_path, monkeypatch):
    key_file = tmp_path / "k"
    key_file.write_text("from-file\n")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert watchdog.read_api_key(str(key_file)) == "from-file"
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    assert watchdog.read_api_key(str(key_file)) == "from-env"


def test_main_dry_run_sends_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(watchdog, "fetch_daily_usage", lambda key: 7.0)
    monkeypatch.setattr(watchdog, "agent_running", lambda: True)
    monkeypatch.setattr(watchdog, "newest_mtime", lambda paths: 1.0)
    monkeypatch.setattr(watchdog, "send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent")))
    state_file = tmp_path / "state.json"
    code = watchdog.main(["--repo", str(tmp_path), "--state-file", str(state_file), "--dry-run", "--json"])
    summary = json.loads(capsys.readouterr().out)
    assert code == 0
    assert len(summary["alerts"]) == 2
    assert summary["sent"] == 0
    assert not state_file.exists()
    assert "test" not in json.dumps(summary["errors"])


def test_main_sends_and_remembers(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(watchdog, "fetch_daily_usage", lambda key: 7.0)
    monkeypatch.setattr(watchdog, "agent_running", lambda: False)
    monkeypatch.setattr(watchdog, "newest_mtime", lambda paths: 1.0)
    sent = []
    monkeypatch.setattr(watchdog, "send", lambda message, **k: sent.append(message) or 200)
    state_file = tmp_path / "state.json"
    args = ["--repo", str(tmp_path), "--state-file", str(state_file), "--json"]
    watchdog.main(args)
    watchdog.main(args)
    capsys.readouterr()
    assert len(sent) == 1
    assert json.loads(state_file.read_text())["spend_alerted_on"]
