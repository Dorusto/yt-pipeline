import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml

import inbox
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME
from test_no_personal_setup import BANNED as BANNED_STRINGS


def make_clip(path, content=b"clip"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def set_mtime(path, moment):
    stamp = moment.timestamp() if isinstance(moment, datetime) else float(moment)
    os.utime(path, (stamp, stamp))


class Recorder:
    def __init__(self, cost=0.0812, output="recap_720p.mp4", wall=42.1, fail_for=()):
        self.cost = cost
        self.output = output
        self.wall = wall
        self.fail_for = set(fail_for)
        self.calls = []
        self.commands = []

    def __call__(
        self,
        source,
        workspace,
        brief_path,
        args,
        python=sys.executable,
        repo_root=inbox.REPO_ROOT,
    ):
        workspace = Path(workspace)
        self.calls.append(
            {
                "source": Path(source),
                "workspace": workspace,
                "brief_path": Path(brief_path),
                "args": args,
            }
        )
        self.commands.append(
            inbox.build_recap_command(source, workspace, brief_path, args, python)
        )
        if workspace.name in self.fail_for:
            return {
                "status": "failed",
                "wall_time_s": 1.0,
                "llm_cost_usd": 0.0,
                "output": None,
                "returncode": 1,
                "stderr": "boom",
            }
        return {
            "status": "done",
            "wall_time_s": self.wall,
            "llm_cost_usd": self.cost,
            "output": self.output,
            "returncode": 0,
            "stderr": "",
        }


def run_inbox(inbox_dir, projects, extra=None, quiet="0"):
    argv = [
        "--inbox",
        str(inbox_dir),
        "--projects",
        str(projects),
        "--once",
        "--quiet-minutes",
        quiet,
    ]
    if extra:
        argv += extra
    return inbox.main(argv)


def test_help_documents_every_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        inbox.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in (
        "--inbox",
        "--projects",
        "--quiet-minutes",
        "--poll-seconds",
        "--default-brief",
        "--preview-base",
        "--notify",
        "--budget-usd",
        "--force",
        "--once",
        "--json",
    ):
        assert flag in out


def test_parent_date_wins_over_filename(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_mimo_20261012_113618.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    projects = tmp_path / "projects"
    assert run_inbox(inbox_dir, projects, extra=["--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert [group["day"] for group in summary["groups"]] == ["2026-10-14"]
    assert summary["groups"][0]["source"] == str(inbox_dir / "2026-10-14")
    assert recorder.calls[0]["workspace"] == projects / "2026-10-14"


def test_day_from_filename(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "dji_mimo_20261012_113618.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    assert run_inbox(inbox_dir, tmp_path / "projects", extra=["--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["groups"][0]["day"] == "2026-10-12"


def test_date_from_filename_variants():
    assert inbox.date_from_filename("clip_2026-10-12.mp4") == "2026-10-12"
    assert inbox.date_from_filename("dji_mimo_20261012_113618.mp4") == "2026-10-12"
    assert inbox.date_from_filename("clip_2026_10_12.mp4") == "2026-10-12"
    assert inbox.date_from_filename("clip.mp4") is None


def test_day_from_mtime(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    clip = make_clip(inbox_dir / "random.mp4")
    moment = datetime(2026, 10, 9, 12, 0, 0)
    set_mtime(clip, moment)
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    assert run_inbox(inbox_dir, tmp_path / "projects", extra=["--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["groups"][0]["day"] == "2026-10-09"


def test_fresh_file_waits_and_does_not_run(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    assert run_inbox(inbox_dir, tmp_path / "projects", quiet="5") == 0
    assert recorder.calls == []
    out = capsys.readouterr().out
    assert "waiting (inbox not quiet)" in out


def test_quiet_zero_runs(tmp_path, monkeypatch):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    assert run_inbox(inbox_dir, tmp_path / "projects", quiet="0") == 0
    assert len(recorder.calls) == 1


def test_pure_day_folder_passed_directly(tmp_path, monkeypatch):
    inbox_dir = tmp_path / "inbox"
    day_dir = inbox_dir / "2026-10-14"
    make_clip(day_dir / "dji_20261012_113618.mp4")
    make_clip(day_dir / "dji_20261012_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    projects = tmp_path / "projects"
    assert run_inbox(inbox_dir, projects) == 0
    assert recorder.calls[0]["source"] == day_dir
    assert not (projects / "2026-10-14" / "source").exists()


def test_mixed_directories_use_symlink_farm(tmp_path, monkeypatch):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "a" / "dji_20261014_120000.mp4")
    make_clip(inbox_dir / "b" / "dji_20261014_130000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    projects = tmp_path / "projects"
    assert run_inbox(inbox_dir, projects) == 0
    farm = projects / "2026-10-14" / "source"
    assert recorder.calls[0]["source"] == farm
    assert farm.is_dir()
    assert sorted(path.name for path in farm.iterdir()) == [
        "dji_20261014_120000.mp4",
        "dji_20261014_130000.mp4",
    ]
    assert all(path.is_symlink() for path in farm.iterdir())


def test_farm_link_name_collisions(tmp_path, monkeypatch):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "a" / "dji_20261014_120000.mp4")
    make_clip(inbox_dir / "b" / "dji_20261014_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    projects = tmp_path / "projects"
    assert run_inbox(inbox_dir, projects) == 0
    farm = projects / "2026-10-14" / "source"
    assert sorted(path.name for path in farm.iterdir()) == [
        "dji_20261014_120000.mp4",
        "dji_20261014_120000_2.mp4",
    ]
    assert all(path.is_symlink() for path in farm.iterdir())


def test_other_day_sibling_forces_farm(tmp_path, monkeypatch):
    inbox_dir = tmp_path / "inbox"
    day_dir = inbox_dir / "trip"
    make_clip(day_dir / "dji_20261014_120000.mp4")
    make_clip(day_dir / "dji_20261015_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    projects = tmp_path / "projects"
    assert run_inbox(inbox_dir, projects) == 0
    sources = {call["workspace"].name: call["source"] for call in recorder.calls}
    assert sources["2026-10-14"] == projects / "2026-10-14" / "source"
    assert sources["2026-10-15"] == projects / "2026-10-15" / "source"


def test_brief_folder_beats_inbox(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    day_dir = inbox_dir / "2026-10-14"
    make_clip(day_dir / "dji_20261012_120000.mp4")
    (day_dir / "brief.txt").write_text("folder brief", encoding="utf-8")
    (inbox_dir / "brief.txt").write_text("inbox brief", encoding="utf-8")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    projects = tmp_path / "projects"
    assert run_inbox(inbox_dir, projects, extra=["--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["groups"][0]["brief_source"] == "folder"
    brief_path = projects / "2026-10-14" / "inbox_brief.txt"
    assert recorder.calls[0]["brief_path"] == brief_path
    assert brief_path.read_text(encoding="utf-8") == "folder brief"


def test_brief_from_inbox(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "sub" / "dji_20261014_120000.mp4")
    (inbox_dir / "brief.txt").write_text("inbox brief", encoding="utf-8")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    projects = tmp_path / "projects"
    assert run_inbox(inbox_dir, projects, extra=["--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["groups"][0]["brief_source"] == "inbox"
    brief_path = projects / "2026-10-14" / "inbox_brief.txt"
    assert brief_path.read_text(encoding="utf-8") == "inbox brief"


def test_brief_from_dated_parent_in_farm(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "a" / "dji_20261014_120000.mp4")
    make_clip(inbox_dir / "2026-10-14" / "b" / "dji_20261014_130000.mp4")
    (inbox_dir / "2026-10-14" / "brief.txt").write_text("parent brief", encoding="utf-8")
    (inbox_dir / "brief.txt").write_text("inbox brief", encoding="utf-8")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    projects = tmp_path / "projects"
    assert run_inbox(inbox_dir, projects, extra=["--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["groups"][0]["brief_source"] == "folder"
    brief_path = projects / "2026-10-14" / "inbox_brief.txt"
    assert brief_path.read_text(encoding="utf-8") == "parent brief"


def test_brief_direct_folder_beats_dated_parent(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    day_dir = inbox_dir / "2026-10-14"
    sub = day_dir / "sub"
    make_clip(sub / "dji_20261014_120000.mp4")
    (sub / "brief.txt").write_text("sub brief", encoding="utf-8")
    (day_dir / "brief.txt").write_text("parent brief", encoding="utf-8")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    projects = tmp_path / "projects"
    assert run_inbox(inbox_dir, projects, extra=["--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["groups"][0]["brief_source"] == "folder"
    brief_path = projects / "2026-10-14" / "inbox_brief.txt"
    assert brief_path.read_text(encoding="utf-8") == "sub brief"


def test_two_dated_parents_fall_through_to_inbox(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "tree1" / "2026-10-14" / "dji_20261014_120000.mp4")
    make_clip(inbox_dir / "tree2" / "2026-10-14" / "dji_20261014_130000.mp4")
    (inbox_dir / "tree1" / "2026-10-14" / "brief.txt").write_text("one", encoding="utf-8")
    (inbox_dir / "tree2" / "2026-10-14" / "brief.txt").write_text("two", encoding="utf-8")
    (inbox_dir / "brief.txt").write_text("inbox brief", encoding="utf-8")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    projects = tmp_path / "projects"
    assert run_inbox(inbox_dir, projects, extra=["--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["groups"][0]["brief_source"] == "inbox"
    brief_path = projects / "2026-10-14" / "inbox_brief.txt"
    assert brief_path.read_text(encoding="utf-8") == "inbox brief"


def test_brief_default_and_flag(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "sub" / "dji_20261014_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    projects = tmp_path / "projects"
    assert run_inbox(inbox_dir, projects, extra=["--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["groups"][0]["brief_source"] == "default"
    brief_path = projects / "2026-10-14" / "inbox_brief.txt"
    assert brief_path.read_text(encoding="utf-8") == inbox.DEFAULT_BRIEF
    capsys.readouterr()
    assert run_inbox(inbox_dir, projects, extra=["--json", "--default-brief", "custom"]) == 0
    assert brief_path.read_text(encoding="utf-8") == "custom"


def test_second_run_skips_nothing_new(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    projects = tmp_path / "projects"
    assert run_inbox(inbox_dir, projects) == 0
    assert len(recorder.calls) == 1
    capsys.readouterr()
    assert run_inbox(inbox_dir, projects) == 0
    assert len(recorder.calls) == 1
    assert "skipped (nothing new)" in capsys.readouterr().out


def test_new_clip_same_day_reruns(tmp_path, monkeypatch):
    inbox_dir = tmp_path / "inbox"
    day_dir = inbox_dir / "2026-10-14"
    make_clip(day_dir / "dji_20261014_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    projects = tmp_path / "projects"
    assert run_inbox(inbox_dir, projects) == 0
    make_clip(day_dir / "dji_20261014_130000.mp4")
    assert run_inbox(inbox_dir, projects) == 0
    assert len(recorder.calls) == 2


def test_changed_brief_reruns(tmp_path, monkeypatch):
    inbox_dir = tmp_path / "inbox"
    day_dir = inbox_dir / "2026-10-14"
    make_clip(day_dir / "dji_20261014_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    projects = tmp_path / "projects"
    assert run_inbox(inbox_dir, projects) == 0
    (day_dir / "brief.txt").write_text("a new brief", encoding="utf-8")
    assert run_inbox(inbox_dir, projects) == 0
    assert len(recorder.calls) == 2


def test_projects_inside_inbox_refused(tmp_path, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    projects = inbox_dir / "projects"
    assert run_inbox(inbox_dir, projects) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "[error]" in err
    assert "inside the inbox" in err
    assert not projects.exists()


def test_recap_flags_forwarded(tmp_path, monkeypatch):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    extra = [
        "--notify",
        "--preview-base",
        "http://127.0.0.1:8091",
        "--budget-usd",
        "2.5",
        "--force",
    ]
    assert run_inbox(inbox_dir, tmp_path / "projects", extra=extra) == 0
    command = recorder.commands[0]
    assert "--notify" in command
    assert "--preview-base" in command
    assert "http://127.0.0.1:8091" in command
    assert "--budget-usd" in command
    assert "2.5" in command
    assert "--force" in command
    assert "--brief-file" in command
    assert command[-1] == "--json"


def test_json_summary_sums_child_costs(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    make_clip(inbox_dir / "2026-10-15" / "dji_20261015_120000.mp4")
    recorder = Recorder(cost=0.0812)
    monkeypatch.setattr(inbox, "run_recap", recorder)
    assert run_inbox(inbox_dir, tmp_path / "projects", extra=["--json"]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["tool"] == "scripts/inbox.py"
    assert summary["status"] == "done"
    assert isinstance(summary["wall_time_s"], (int, float))
    assert summary["llm_cost_usd"] == pytest.approx(0.1624)
    assert captured.out.strip().count("\n") == 0
    assert captured.err.count("[group]") == 2


def test_failing_child_marks_group_and_others_run(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    make_clip(inbox_dir / "2026-10-15" / "dji_20261015_120000.mp4")
    recorder = Recorder(fail_for=("2026-10-15",))
    monkeypatch.setattr(inbox, "run_recap", recorder)
    assert run_inbox(inbox_dir, tmp_path / "projects", extra=["--json"]) == EXIT_RUNTIME
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["status"] == "failed"
    by_day = {group["day"]: group for group in summary["groups"]}
    assert by_day["2026-10-14"]["status"] == "done"
    assert by_day["2026-10-15"]["status"] == "failed"
    assert len(recorder.calls) == 2
    assert "[error] 2026-10-15" in captured.err


def test_failed_group_retries_next_run(tmp_path, monkeypatch):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    recorder = Recorder(fail_for=("2026-10-14",))
    monkeypatch.setattr(inbox, "run_recap", recorder)
    projects = tmp_path / "projects"
    assert run_inbox(inbox_dir, projects) == EXIT_RUNTIME
    assert len(recorder.calls) == 1
    assert run_inbox(inbox_dir, projects) == EXIT_RUNTIME
    assert len(recorder.calls) == 2


def test_corrupt_state_warns_and_starts_fresh(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "inbox_state.json").write_text("{not json", encoding="utf-8")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    assert run_inbox(inbox_dir, projects) == 0
    captured = capsys.readouterr()
    assert "[warn]" in captured.err
    assert len(recorder.calls) == 1


def test_state_file_shape(tmp_path, monkeypatch):
    inbox_dir = tmp_path / "inbox"
    clip = make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    projects = tmp_path / "projects"
    assert run_inbox(inbox_dir, projects) == 0
    state = json.loads((projects / "inbox_state.json").read_text(encoding="utf-8"))
    assert state["version"] == 1
    assert set(state["runs"]["2026-10-14"]) == {
        "status",
        "brief_sha256",
        "wall_time_s",
        "llm_cost_usd",
        "last_run",
    }
    entry = state["files"][str(clip)]
    assert set(entry) == {"day", "size", "mtime", "processed_at"}
    assert entry["day"] == "2026-10-14"


def test_hidden_paths_skipped(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / ".hidden" / "dji_20261014_120000.mp4")
    make_clip(inbox_dir / ".dji_20261016_120000.mp4")
    make_clip(inbox_dir / "dji_20261015_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    assert run_inbox(inbox_dir, tmp_path / "projects", extra=["--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert [group["day"] for group in summary["groups"]] == ["2026-10-15"]


def test_watch_mode_stops_on_keyboard_interrupt(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)

    def stop(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(inbox.time, "sleep", stop)
    argv = [
        "--inbox",
        str(inbox_dir),
        "--projects",
        str(tmp_path / "projects"),
        "--quiet-minutes",
        "0",
    ]
    assert inbox.main(argv) == 0
    captured = capsys.readouterr()
    assert "[inbox] stopped" in captured.out
    assert len(recorder.calls) == 1


def test_watch_mode_json_prints_single_summary(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)

    def stop(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(inbox.time, "sleep", stop)
    argv = [
        "--inbox",
        str(inbox_dir),
        "--projects",
        str(tmp_path / "projects"),
        "--quiet-minutes",
        "0",
        "--json",
    ]
    assert inbox.main(argv) == 0
    captured = capsys.readouterr()
    assert captured.out.strip().count("\n") == 0
    summary = json.loads(captured.out)
    assert summary["tool"] == "scripts/inbox.py"
    assert summary["status"] == "done"
    assert "[inbox] stopped" in captured.err


def test_watch_mode_does_not_reprint_unchanged_waiting(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    cycles = {"n": 0}

    def stop(_seconds):
        cycles["n"] += 1
        if cycles["n"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(inbox.time, "sleep", stop)
    argv = [
        "--inbox",
        str(inbox_dir),
        "--projects",
        str(tmp_path / "projects"),
        "--quiet-minutes",
        "5",
    ]
    assert inbox.main(argv) == 0
    out = capsys.readouterr().out
    assert out.count("waiting (inbox not quiet)") == 1
    assert recorder.calls == []


def test_watch_mode_prints_when_group_runs(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    clip = make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    cycles = {"n": 0}

    def stop(_seconds):
        cycles["n"] += 1
        if cycles["n"] == 1:
            set_mtime(clip, 0)
            return
        raise KeyboardInterrupt

    monkeypatch.setattr(inbox.time, "sleep", stop)
    argv = [
        "--inbox",
        str(inbox_dir),
        "--projects",
        str(tmp_path / "projects"),
        "--quiet-minutes",
        "5",
    ]
    assert inbox.main(argv) == 0
    out = capsys.readouterr().out
    assert out.count("waiting (inbox not quiet)") == 1
    assert out.count("[group] 2026-10-14") == 2
    assert len(recorder.calls) == 1


def test_watch_mode_skipped_prints_on_change_only(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    cycles = {"n": 0}

    def stop(_seconds):
        cycles["n"] += 1
        if cycles["n"] >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(inbox.time, "sleep", stop)
    argv = [
        "--inbox",
        str(inbox_dir),
        "--projects",
        str(tmp_path / "projects"),
        "--quiet-minutes",
        "0",
    ]
    assert inbox.main(argv) == 0
    out = capsys.readouterr().out
    assert out.count("[group] 2026-10-14") == 2
    assert out.count("skipped (nothing new)") == 1
    assert len(recorder.calls) == 1


def test_once_prints_group_line_every_run(tmp_path, monkeypatch, capsys):
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    projects = tmp_path / "projects"
    assert run_inbox(inbox_dir, projects) == 0
    first = capsys.readouterr().out
    assert first.count("[group] 2026-10-14") == 1
    assert run_inbox(inbox_dir, projects) == 0
    second = capsys.readouterr().out
    assert second.count("[group] 2026-10-14") == 1
    assert "skipped (nothing new)" in second


VALID_CONFIG = {
    "models": {"metadata": "deepseek/deepseek-v4.1-flash"},
    "llm_budget_usd": None,
    "llm_backend": "mock",
}


def write_config(tmp_path, data):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def run_inbox_without_projects(inbox_dir):
    return inbox.main(
        ["--inbox", str(inbox_dir), "--once", "--quiet-minutes", "0"]
    )


def test_missing_projects_exits_1_with_three_ways(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("YT_PIPELINE_PROJECTS", raising=False)
    monkeypatch.setenv("YT_CONFIG", str(write_config(tmp_path, VALID_CONFIG)))
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    assert run_inbox_without_projects(inbox_dir) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "[error]" in err
    assert "no projects folder configured" in err
    assert "--projects" in err
    assert "YT_PIPELINE_PROJECTS" in err
    assert "projects.path" in err


def test_projects_from_env(tmp_path, monkeypatch):
    monkeypatch.delenv("YT_CONFIG", raising=False)
    projects = tmp_path / "projects"
    monkeypatch.setenv("YT_PIPELINE_PROJECTS", str(projects))
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    assert run_inbox_without_projects(inbox_dir) == 0
    assert recorder.calls[0]["workspace"] == projects / "2026-10-14"


def test_projects_from_config(tmp_path, monkeypatch):
    monkeypatch.delenv("YT_PIPELINE_PROJECTS", raising=False)
    projects = tmp_path / "projects"
    config = dict(VALID_CONFIG, projects={"path": str(projects)})
    monkeypatch.setenv("YT_CONFIG", str(write_config(tmp_path, config)))
    inbox_dir = tmp_path / "inbox"
    make_clip(inbox_dir / "2026-10-14" / "dji_20261014_120000.mp4")
    recorder = Recorder()
    monkeypatch.setattr(inbox, "run_recap", recorder)
    assert run_inbox_without_projects(inbox_dir) == 0
    assert recorder.calls[0]["workspace"] == projects / "2026-10-14"


def test_help_has_no_personal_path(capsys):
    with pytest.raises(SystemExit) as excinfo:
        inbox.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for banned in BANNED_STRINGS:
        assert banned not in out
