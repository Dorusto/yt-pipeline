import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

import agent_watchdog as watchdog

_REAL_TMUX_SESSION_FOR_AGENT = watchdog.tmux_session_for_agent


@pytest.fixture(autouse=True)
def _isolate_tmux(monkeypatch):
    monkeypatch.setattr(watchdog, "tmux_session_for_agent", lambda: None)


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
    running = {"value": True}

    def fake_agent_running():
        return running["value"]

    def fake_stop():
        stopped.append(1)
        running["value"] = False
        return 1

    monkeypatch.setattr(watchdog, "read_api_key", lambda key_file: "key")
    monkeypatch.setattr(watchdog, "fetch_daily_usage", lambda key: 3.0)
    monkeypatch.setattr(watchdog, "agent_running", fake_agent_running)
    monkeypatch.setattr(watchdog, "stop_agent", fake_stop)
    monkeypatch.setattr(watchdog, "send", lambda *a, **k: None)
    repo = tmp_path / "repo"
    repo.mkdir()
    watchdog.main(["--repo", str(repo), "--kill-over-usd", "2", "--state-file", str(tmp_path / "s.json"),
                         "--watch", str(repo), "--json"])
    summary = json.loads(capsys.readouterr().out)
    assert stopped == [1]
    assert summary["stopped"] == 1
    assert summary["stop_verified"] is True
    assert any(alert.startswith("Stopped the agent") for alert in summary["alerts"])


def _old_commit_repo(tmp_path, hours=3):
    repo = tmp_path / "repo"
    ref = repo / ".git" / "refs" / "heads" / "main"
    ref.parent.mkdir(parents=True)
    ref.write_text("abc\n")
    old = time.time() - hours * 3600
    os.utime(ref, (old, old))
    return repo


def test_no_hard_stop_without_kill_no_commit_hours(tmp_path, monkeypatch, capsys):
    stopped = []
    monkeypatch.setattr(watchdog, "read_api_key", lambda key_file: "key")
    monkeypatch.setattr(watchdog, "fetch_daily_usage", lambda key: 0.1)
    monkeypatch.setattr(watchdog, "agent_running", lambda: True)
    monkeypatch.setattr(watchdog, "stop_agent", lambda: stopped.append(1) or 1)
    monkeypatch.setattr(watchdog, "send", lambda *a, **k: None)
    repo = _old_commit_repo(tmp_path)
    watchdog.main(["--repo", str(repo), "--state-file", str(tmp_path / "s.json"),
                   "--watch", str(repo), "--json"])
    summary = json.loads(capsys.readouterr().out)
    assert summary["kill_reason"] is None
    assert summary["stopped"] == 0
    assert stopped == []


def test_hard_stop_with_kill_no_commit_hours(tmp_path, monkeypatch, capsys):
    stopped = []
    monkeypatch.setattr(watchdog, "read_api_key", lambda key_file: "key")
    monkeypatch.setattr(watchdog, "fetch_daily_usage", lambda key: 0.1)
    monkeypatch.setattr(watchdog, "agent_running", lambda: True)
    monkeypatch.setattr(watchdog, "stop_agent", lambda: stopped.append(1) or 1)
    monkeypatch.setattr(watchdog, "send", lambda *a, **k: None)
    repo = _old_commit_repo(tmp_path)
    state_file = tmp_path / "s.json"
    started = datetime.now(timezone.utc) - timedelta(hours=4)
    state_file.write_text(json.dumps({"run_started_at": started.isoformat()}))
    watchdog.main(["--repo", str(repo), "--state-file", str(state_file),
                   "--watch", str(repo), "--kill-no-commit-hours", "2", "--json"])
    summary = json.loads(capsys.readouterr().out)
    assert stopped == [1]
    assert summary["stopped"] == 1
    assert "no commit" in summary["kill_reason"]


def test_run_spend_ignores_old_entries(tmp_path):
    ledger = tmp_path / "run-spend.json"
    ledger.write_text(json.dumps({"entries": [
        {"ts": "2026-10-20T10:00:00+00:00", "cost_usd": 5.0, "note": "old"},
        {"ts": "2026-10-20T12:00:00+00:00", "cost_usd": 0.4, "note": "new"},
    ]}))
    entries = watchdog.read_spend_ledger(ledger)
    total = watchdog.run_spend_usd(entries, "2026-10-20T11:00:00+00:00", ledger)
    assert total == 0.4


def test_run_spend_ledger_appends_openrouter_delta(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(watchdog, "read_api_key", lambda key_file: "key")
    holder = {"usage": 0.5}
    monkeypatch.setattr(watchdog, "fetch_daily_usage", lambda key: holder["usage"])
    monkeypatch.setattr(watchdog, "agent_running", lambda: True)
    monkeypatch.setattr(watchdog, "newest_mtime", lambda paths: 1.0)
    monkeypatch.setattr(watchdog, "last_commit_time", lambda repo: 0.0)
    stopped = []
    monkeypatch.setattr(watchdog, "stop_agent", lambda: stopped.append(1) or 1)
    monkeypatch.setattr(watchdog, "send", lambda *a, **k: None)
    state_file = tmp_path / "state.json"
    ledger = tmp_path / "run-spend.json"
    base = [
        "--repo", str(tmp_path), "--state-file", str(state_file), "--ledger", str(ledger),
        "--watch", str(tmp_path), "--json",
    ]
    first = watchdog.main(base + ["--start-run"])
    capsys.readouterr()
    assert first == 0
    holder["usage"] = 2.0
    watchdog.main(base)
    summary = json.loads(capsys.readouterr().out)
    entries = json.loads(ledger.read_text())["entries"]
    assert stopped == []
    assert [entry["note"] for entry in entries] == ["openrouter-delta"]
    assert entries[0]["cost_usd"] == 1.5
    assert summary["run_spend_usd"] == 1.5


class _FakeParentProcess:
    def __init__(self, pid, name, parent=None):
        self.pid = pid
        self.info = {"name": name}
        self._parent = parent

    def parent(self):
        return self._parent


class _FakeTreePsutil:
    class Error(Exception):
        pass

    def __init__(self, processes):
        self._processes = list(processes)

    def process_iter(self, attrs=None):
        return iter(self._processes)


def test_no_progress_clock_starts_at_agent_start():
    now = 100000.0
    last_commit = now - 10 * 3600
    agent_start = now - 60
    state = {}
    assert watchdog.check_no_progress(True, last_commit, now, 5400, state, agent_start) is None
    assert watchdog.kill_reason(True, 0.1, None, last_commit, now, 2.0, agent_start=agent_start) is None


def test_no_commit_age_never_negative():
    now = 100000.0
    last_commit = now + 3600
    state = {}
    assert watchdog.check_no_progress(True, last_commit, now, 5400, state, 0.0) is None
    assert watchdog.kill_reason(True, 0.1, None, last_commit, now, 2.0) is None


def test_no_commit_clock_falls_back_to_last_commit():
    now = 100000.0
    last_commit = now - 3 * 3600
    state = {}
    assert "no commit" in watchdog.check_no_progress(True, last_commit, now, 5400, state, 0.0)
    assert "no commit" in watchdog.kill_reason(True, 0.1, None, last_commit, now, 2.0, agent_start=0.0)


def test_summary_age_clamped_when_commit_in_future(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(watchdog, "read_api_key", lambda key_file: "key")
    monkeypatch.setattr(watchdog, "fetch_daily_usage", lambda key: 0.1)
    monkeypatch.setattr(watchdog, "agent_running", lambda: True)
    monkeypatch.setattr(watchdog, "newest_mtime", lambda paths: 1.0)
    monkeypatch.setattr(watchdog, "last_commit_time", lambda repo: time.time() + 3600)
    monkeypatch.setattr(watchdog, "send", lambda *a, **k: None)
    repo = tmp_path / "repo"
    repo.mkdir()
    watchdog.main(["--repo", str(repo), "--state-file", str(tmp_path / "s.json"),
                   "--watch", str(repo), "--json"])
    summary = json.loads(capsys.readouterr().out)
    assert summary["hours_since_commit"] == 0.0
    assert summary["idle_hours"] >= 0.0
    assert summary["kill_reason"] is None


def test_stop_not_verified_when_agent_still_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(watchdog, "read_api_key", lambda key_file: "key")
    monkeypatch.setattr(watchdog, "fetch_daily_usage", lambda key: 3.0)
    monkeypatch.setattr(watchdog, "agent_running", lambda: True)
    monkeypatch.setattr(watchdog, "stop_agent", lambda: 1)
    monkeypatch.setattr(watchdog, "send", lambda *a, **k: None)
    repo = tmp_path / "repo"
    repo.mkdir()
    watchdog.main(["--repo", str(repo), "--kill-over-usd", "2", "--state-file", str(tmp_path / "s.json"),
                   "--watch", str(repo), "--json"])
    summary = json.loads(capsys.readouterr().out)
    assert summary["stopped"] == 1
    assert summary["stop_verified"] is False
    assert any(
        alert.startswith("Tried to stop the agent but it is still running")
        for alert in summary["alerts"]
    )


def test_alerts_labelled_with_active_task_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(watchdog, "read_api_key", lambda key_file: "key")
    monkeypatch.setattr(watchdog, "fetch_daily_usage", lambda key: 7.0)
    monkeypatch.setattr(watchdog, "agent_running", lambda: False)
    monkeypatch.setattr(watchdog, "newest_mtime", lambda paths: 1.0)
    monkeypatch.setattr(watchdog, "last_commit_time", lambda repo: 0.0)
    monkeypatch.setattr(watchdog, "send", lambda *a, **k: None)
    repo = tmp_path / "repo"
    tasks = repo / ".opencode" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "V020N-lot-n4.md").write_text("brief")
    watchdog.main(["--repo", str(repo), "--state-file", str(tmp_path / "s.json"),
                   "--watch", str(repo), "--json"])
    summary = json.loads(capsys.readouterr().out)
    assert summary["label"] == "V020N-lot-n4"
    assert summary["alerts"]
    assert all(alert.startswith("V020N-lot-n4: ") for alert in summary["alerts"])


def test_alerts_labelled_with_tmux_session(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(watchdog, "tmux_session_for_agent", lambda: "s60")
    monkeypatch.setattr(watchdog, "read_api_key", lambda key_file: "key")
    monkeypatch.setattr(watchdog, "fetch_daily_usage", lambda key: 7.0)
    monkeypatch.setattr(watchdog, "agent_running", lambda: False)
    monkeypatch.setattr(watchdog, "newest_mtime", lambda paths: 1.0)
    monkeypatch.setattr(watchdog, "last_commit_time", lambda repo: 0.0)
    monkeypatch.setattr(watchdog, "send", lambda *a, **k: None)
    repo = tmp_path / "repo"
    repo.mkdir()
    watchdog.main(["--repo", str(repo), "--state-file", str(tmp_path / "s.json"),
                   "--watch", str(repo), "--json"])
    summary = json.loads(capsys.readouterr().out)
    assert summary["label"] == "s60"
    assert summary["alerts"]
    assert all(alert.startswith("s60: ") for alert in summary["alerts"])


def test_alerts_unlabelled_without_lot_or_tmux(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(watchdog, "read_api_key", lambda key_file: "key")
    monkeypatch.setattr(watchdog, "fetch_daily_usage", lambda key: 7.0)
    monkeypatch.setattr(watchdog, "agent_running", lambda: False)
    monkeypatch.setattr(watchdog, "newest_mtime", lambda paths: 1.0)
    monkeypatch.setattr(watchdog, "last_commit_time", lambda repo: 0.0)
    monkeypatch.setattr(watchdog, "send", lambda *a, **k: None)
    repo = tmp_path / "repo"
    repo.mkdir()
    watchdog.main(["--repo", str(repo), "--state-file", str(tmp_path / "s.json"),
                   "--watch", str(repo), "--json"])
    summary = json.loads(capsys.readouterr().out)
    assert summary["label"] is None
    assert summary["alerts"]
    assert all(not alert.startswith("s60: ") for alert in summary["alerts"])


def test_agent_start_time_prefers_run_marker():
    state = {"run_started_at": "2026-10-22T10:00:00+00:00"}
    assert watchdog.agent_start_time(state, True) == watchdog.parse_ts("2026-10-22T10:00:00+00:00")


def test_main_accepts_explicit_now(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(watchdog, "read_api_key", lambda key_file: "key")
    monkeypatch.setattr(watchdog, "fetch_daily_usage", lambda key: 0.1)
    monkeypatch.setattr(watchdog, "agent_running", lambda: True)
    monkeypatch.setattr(watchdog, "newest_mtime", lambda paths: 1.0)
    monkeypatch.setattr(watchdog, "last_commit_time", lambda repo: 0.0)
    monkeypatch.setattr(watchdog, "send", lambda *a, **k: None)
    repo = tmp_path / "repo"
    repo.mkdir()
    fixed = 1_800_000_000.0
    watchdog.main(["--repo", str(repo), "--state-file", str(tmp_path / "s.json"),
                   "--watch", str(repo), "--json"], now=fixed)
    summary = json.loads(capsys.readouterr().out)
    expected = datetime.fromtimestamp(fixed, tz=timezone.utc).isoformat(timespec="seconds")
    assert summary["run_started_at"] == expected
    assert summary["hours_since_commit"] == 0.0


def test_agent_start_time_falls_back_to_process(monkeypatch):
    monkeypatch.setattr(watchdog, "opencode_process_start", lambda: 5000.0)
    assert watchdog.agent_start_time({}, True) == 5000.0
    assert watchdog.agent_start_time({}, False) == 0.0


def test_opencode_process_start_uses_create_time(monkeypatch):
    class _Process:
        info = {"name": "opencode"}

        def create_time(self):
            return 1234.5

    monkeypatch.setitem(sys.modules, "psutil", _FakeTreePsutil([_Process()]))
    assert watchdog.opencode_process_start() == 1234.5


def test_tmux_session_for_agent_walks_parents(monkeypatch):
    monkeypatch.setattr(watchdog, "tmux_session_for_agent", _REAL_TMUX_SESSION_FOR_AGENT)
    pane = _FakeParentProcess(100, "bash")
    agent = _FakeParentProcess(200, "opencode", parent=pane)
    monkeypatch.setattr(watchdog.shutil, "which", lambda name: "/usr/bin/tmux")
    monkeypatch.setitem(sys.modules, "psutil", _FakeTreePsutil([agent]))
    monkeypatch.setattr(
        watchdog.subprocess, "run", lambda *a, **k: _FakeCompleted(0, b"s60 100\nweb 300\n")
    )
    assert watchdog.tmux_session_for_agent() == "s60"


def test_tmux_session_for_agent_none_without_tmux(monkeypatch):
    monkeypatch.setattr(watchdog, "tmux_session_for_agent", _REAL_TMUX_SESSION_FOR_AGENT)
    monkeypatch.setattr(watchdog.shutil, "which", lambda name: None)
    assert watchdog.tmux_session_for_agent() is None


def test_tmux_session_for_agent_none_without_match(monkeypatch):
    monkeypatch.setattr(watchdog, "tmux_session_for_agent", _REAL_TMUX_SESSION_FOR_AGENT)
    agent = _FakeParentProcess(200, "opencode")
    monkeypatch.setattr(watchdog.shutil, "which", lambda name: "/usr/bin/tmux")
    monkeypatch.setitem(sys.modules, "psutil", _FakeTreePsutil([agent]))
    monkeypatch.setattr(
        watchdog.subprocess, "run", lambda *a, **k: _FakeCompleted(0, b"s60 100\n")
    )
    assert watchdog.tmux_session_for_agent() is None


def test_active_task_label_picks_newest_md(tmp_path):
    tasks = tmp_path / ".opencode" / "tasks"
    tasks.mkdir(parents=True)
    older = tasks / "V020N-lot-n3.md"
    older.write_text("old")
    os.utime(older, (1000, 1000))
    newer = tasks / "V020N-lot-n4.md"
    newer.write_text("new")
    os.utime(newer, (2000, 2000))
    assert watchdog.active_task_label(tmp_path) == "V020N-lot-n4"


def test_active_task_label_skips_review_and_audit(tmp_path):
    tasks = tmp_path / ".opencode" / "tasks"
    tasks.mkdir(parents=True)
    brief = tasks / "V020N-lot-n4.md"
    brief.write_text("brief")
    os.utime(brief, (1000, 1000))
    review = tasks / "review-x.md"
    review.write_text("review")
    os.utime(review, (3000, 3000))
    audit = tasks / "AUDIT-x.md"
    audit.write_text("audit")
    os.utime(audit, (4000, 4000))
    assert watchdog.active_task_label(tmp_path) == "V020N-lot-n4"


def test_active_task_label_none_without_folder(tmp_path):
    assert watchdog.active_task_label(tmp_path) is None


def test_active_task_label_ignores_non_md(tmp_path):
    tasks = tmp_path / ".opencode" / "tasks"
    tasks.mkdir(parents=True)
    brief = tasks / "V020N-lot-n4.md"
    brief.write_text("brief")
    os.utime(brief, (1000, 1000))
    other = tasks / "notes.txt"
    other.write_text("notes")
    os.utime(other, (5000, 5000))
    assert watchdog.active_task_label(tmp_path) == "V020N-lot-n4"


def test_stop_notification_reports_lot_cost(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(watchdog, "read_api_key", lambda key_file: "key")
    monkeypatch.setattr(watchdog, "fetch_daily_usage", lambda key: 3.0)
    running = {"value": True}

    def fake_agent_running():
        return running["value"]

    def fake_stop():
        running["value"] = False
        return 1

    monkeypatch.setattr(watchdog, "agent_running", fake_agent_running)
    monkeypatch.setattr(watchdog, "stop_agent", fake_stop)
    monkeypatch.setattr(watchdog, "newest_mtime", lambda paths: 1.0)
    monkeypatch.setattr(watchdog, "last_commit_time", lambda repo: 0.0)
    sent = []
    monkeypatch.setattr(
        watchdog, "send", lambda message, **k: sent.append((k.get("title"), message)) or 200
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    state_file = tmp_path / "state.json"
    ledger = tmp_path / "run-spend.json"
    started = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=10)
    state_file.write_text(json.dumps({
        "run_started_at": started.isoformat(),
        "usage_daily_last_seen": 3.0,
    }))
    ledger.write_text(json.dumps({"entries": [
        {"ts": started.isoformat(), "cost_usd": 0.42, "note": "fake"},
    ]}))
    watchdog.main(["--repo", str(repo), "--state-file", str(state_file), "--ledger", str(ledger),
                   "--kill-over-usd", "2", "--watch", str(repo), "--json"])
    summary = json.loads(capsys.readouterr().out)
    assert summary["run_spend_usd"] == 0.42
    blocked = [message for title, message in sent if title == "Blocked"]
    assert len(blocked) == 1
    assert "This lot has cost $0.42." in blocked[0]
