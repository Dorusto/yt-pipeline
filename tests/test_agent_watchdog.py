import json
import os
import sys

import agent_watchdog as watchdog


class _FakeProcess:
    def __init__(self, name):
        self.info = {"name": name}


class _FakePsutil:
    class Error(Exception):
        pass

    def __init__(self, names):
        self._names = list(names)

    def process_iter(self, attrs=None):
        for name in self._names:
            yield _FakeProcess(name)


class _FakeCompleted:
    def __init__(self, returncode=0, stdout=b""):
        self.returncode = returncode
        self.stdout = stdout


def test_agent_running_true_with_psutil(monkeypatch):
    monkeypatch.setattr(watchdog, "IS_WINDOWS", False)
    monkeypatch.setitem(sys.modules, "psutil", _FakePsutil(["bash", "opencode", "python"]))
    assert watchdog.agent_running() is True


def test_agent_running_accepts_opencode_exe_on_windows(monkeypatch):
    monkeypatch.setattr(watchdog, "IS_WINDOWS", True)
    monkeypatch.setitem(sys.modules, "psutil", _FakePsutil(["opencode.exe"]))
    assert watchdog.agent_running() is True


def test_agent_running_false_without_match(monkeypatch):
    monkeypatch.setattr(watchdog, "IS_WINDOWS", False)
    monkeypatch.setitem(sys.modules, "psutil", _FakePsutil(["bash", "python"]))
    assert watchdog.agent_running() is False


def test_agent_running_false_without_psutil_or_fallback(monkeypatch):
    monkeypatch.setattr(watchdog, "IS_WINDOWS", False)
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(watchdog.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        watchdog.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("run"))
    )
    assert watchdog.agent_running() is False


def test_agent_running_uses_pgrep_fallback(monkeypatch):
    monkeypatch.setattr(watchdog, "IS_WINDOWS", False)
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(watchdog.shutil, "which", lambda name: "/usr/bin/pgrep")
    monkeypatch.setattr(watchdog.subprocess, "run", lambda *a, **k: _FakeCompleted(0))
    assert watchdog.agent_running() is True


def test_agent_running_pgrep_fallback_no_match(monkeypatch):
    monkeypatch.setattr(watchdog, "IS_WINDOWS", False)
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(watchdog.shutil, "which", lambda name: "/usr/bin/pgrep")
    monkeypatch.setattr(watchdog.subprocess, "run", lambda *a, **k: _FakeCompleted(1))
    assert watchdog.agent_running() is False


def test_agent_running_uses_tasklist_fallback_on_windows(monkeypatch):
    monkeypatch.setattr(watchdog, "IS_WINDOWS", True)
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(watchdog.shutil, "which", lambda name: "C:/Windows/System32/tasklist.exe")
    monkeypatch.setattr(
        watchdog.subprocess, "run", lambda *a, **k: _FakeCompleted(0, b"opencode.exe 1234 Console")
    )
    assert watchdog.agent_running() is True


def test_agent_running_tasklist_fallback_no_match(monkeypatch):
    monkeypatch.setattr(watchdog, "IS_WINDOWS", True)
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(watchdog.shutil, "which", lambda name: "C:/Windows/System32/tasklist.exe")
    monkeypatch.setattr(
        watchdog.subprocess,
        "run",
        lambda *a, **k: _FakeCompleted(0, b"INFO: No tasks are running which match the criteria."),
    )
    assert watchdog.agent_running() is False


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


def test_newest_mtime_accepts_string_and_prunes_nested_skipped_dirs(tmp_path):
    nested = tmp_path / "sub" / "deep"
    nested.mkdir(parents=True)
    kept = nested / "a.txt"
    kept.write_text("x")
    os.utime(kept, (2000, 2000))
    skipped = tmp_path / "sub" / "node_modules"
    skipped.mkdir()
    ignored = skipped / "b.txt"
    ignored.write_text("y")
    os.utime(ignored, (9000, 9000))
    assert watchdog.newest_mtime([str(tmp_path)]) == 2000


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
    monkeypatch.setattr(watchdog, "last_commit_time", lambda repo: 0.0)
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
    monkeypatch.setattr(watchdog, "last_commit_time", lambda repo: 0.0)
    sent = []
    monkeypatch.setattr(watchdog, "send", lambda message, **k: sent.append(message) or 200)
    state_file = tmp_path / "state.json"
    args = ["--repo", str(tmp_path), "--state-file", str(state_file), "--json"]
    watchdog.main(args)
    watchdog.main(args)
    capsys.readouterr()
    assert len(sent) == 1
    assert json.loads(state_file.read_text())["spend_alerted_on"]


def test_no_progress_alert_once_per_episode():
    state = {}
    assert watchdog.check_no_progress(True, 1000.0, 1000.0 + 3000, 5400, state) is None
    assert "no commit" in watchdog.check_no_progress(True, 1000.0, 1000.0 + 6000, 5400, state)
    assert watchdog.check_no_progress(True, 1000.0, 1000.0 + 9000, 5400, state) is None
    assert watchdog.check_no_progress(False, 1.0, 99999.0, 5400, {}) is None


def test_last_commit_time_uses_ref_mtime(tmp_path):
    assert watchdog.last_commit_time(tmp_path) == 0.0
    ref = tmp_path / ".git" / "refs" / "heads" / "d2-2-fixes"
    ref.parent.mkdir(parents=True)
    ref.write_text("abc\n")
    os.utime(ref, (5000, 5000))
    assert watchdog.last_commit_time(tmp_path) == 5000


def test_kill_reason_is_off_by_default():
    assert watchdog.kill_reason(True, 9.0, None, 1.0, 100000.0, None) is None


def test_kill_reason_spend_cap():
    reason = watchdog.kill_reason(True, 2.5, 2.0, 0.0, 0.0, None)
    assert "hard cap" in reason
    assert watchdog.kill_reason(False, 2.5, 2.0, 0.0, 0.0, None) is None
    assert watchdog.kill_reason(True, None, 2.0, 0.0, 0.0, None) is None


def test_kill_reason_no_commit_limit():
    now = 10 * 3600.0
    assert "no commit" in watchdog.kill_reason(True, 0.1, None, now - 3 * 3600, now, 2.0)
    assert watchdog.kill_reason(True, 0.1, None, now - 1 * 3600, now, 2.0) is None


def test_main_stops_the_agent_over_the_cap(tmp_path, monkeypatch, capsys):
    import json

    stopped = []
    monkeypatch.setattr(watchdog, "read_api_key", lambda key_file: "key")
    monkeypatch.setattr(watchdog, "fetch_daily_usage", lambda key: 3.0)
    monkeypatch.setattr(watchdog, "agent_running", lambda: True)
    monkeypatch.setattr(watchdog, "stop_agent", lambda: stopped.append(1) or 1)
    monkeypatch.setattr(watchdog, "send", lambda *a, **k: None)
    repo = tmp_path / "repo"
    repo.mkdir()
    watchdog.main(["--repo", str(repo), "--kill-over-usd", "2", "--state-file", str(tmp_path / "s.json"),
                         "--watch", str(repo), "--json"])
    summary = json.loads(capsys.readouterr().out)
    assert stopped == [1]
    assert summary["stopped"] == 1
    assert any(alert.startswith("Stopped the agent") for alert in summary["alerts"])
