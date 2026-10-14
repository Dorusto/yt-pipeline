import json
import os
import re
import subprocess
import sys
import time

import pytest

import recap


def make_clip(path, duration=1.0, size="160x120", rate=10, audio=True):
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration}:size={size}:rate={rate}",
    ]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
    command += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        command += ["-c:a", "aac", "-shortest"]
    command += [str(path)]
    subprocess.run(command, check=True, capture_output=True)


def make_source(tmp_path, names=("clip_a.mp4", "clip_b.mp4")):
    source = tmp_path / "raw"
    source.mkdir()
    for name in names:
        make_clip(source / name)
    return source


class Recorder:
    def __init__(self, fail_at=None, costs=None, ledger=None):
        self.fail_at = fail_at
        self.costs = costs or {}
        self.ledger = ledger
        self.calls = []
        self.argvs = []
        self.clock = time.time() + 1000.0
        self.ledger_written = False

    def tick(self):
        self.clock += 10.0
        return self.clock

    def touch(self, path, content=""):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        stamp = self.tick()
        os.utime(path, (stamp, stamp))

    def create_outputs(self, name, ctx):
        ws = ctx.workspace
        clips = recap.video_files(ctx.source)
        brief_text = (ws / "brief.txt").read_text(encoding="utf-8")
        if name == "proxy":
            for clip in clips:
                self.touch(ws / "proxy" / clip.name, "proxy")
        elif name == "ingest":
            self.touch(ws / "ingest.json", "{}")
        elif name == "motion":
            for clip in clips:
                self.touch(ws / "motion" / f"{clip.stem}.json", "{}")
        elif name == "quality":
            for clip in clips:
                self.touch(ws / "quality" / f"{clip.stem}.json", "{}")
        elif name == "describe":
            for clip in clips:
                self.touch(ws / "describe" / f"{clip.stem}.json", "{}")
        elif name == "similarity":
            self.touch(ws / "similarity.json", "{}")
        elif name == "index":
            self.touch(ws / "shotlog.json", "{}")
        elif name == "router":
            self.touch(ws / "route.json", json.dumps({"brief": {"text": brief_text}}))
        elif name == "llm_director":
            self.touch(ws / "edit.json", json.dumps({"brief": {"text": brief_text}}))
        elif name == "render":
            self.touch(ws / "recap_720p.mp4", "video")
        elif name == "render_final":
            self.touch(ws / "recap_final.mp4", "video")
        elif name == "export_kdenlive":
            self.touch(ws / "recap.kdenlive", "project")

    def __call__(self, station, argv, ctx):
        name = station["name"]
        self.calls.append(name)
        self.argvs.append((name, argv))
        if self.ledger is not None and not self.ledger_written:
            self.touch(ctx.workspace / "llm_spend.json", json.dumps(self.ledger))
            self.ledger_written = True
        if name == self.fail_at:
            return {
                "status": "FAIL",
                "wall_time_s": 1.0,
                "llm_cost_usd": 0.0,
                "detail": "exit 1",
                "returncode": 1,
                "stderr": "boom",
            }
        self.create_outputs(name, ctx)
        return {
            "status": "done",
            "wall_time_s": 1.0,
            "llm_cost_usd": self.costs.get(name, 0.0),
            "detail": "written",
            "returncode": 0,
            "stderr": "",
        }


def run_recap(source, workspace, brief="a calm recap", extra=None):
    argv = [
        "--source",
        str(source),
        "--brief",
        brief,
        "--workspace",
        str(workspace),
    ]
    if extra:
        argv += extra
    return recap.main(argv)


def test_station_order_without_final(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, workspace, extra=["--json"]) == 0
    assert recorder.calls == [
        "proxy",
        "ingest",
        "motion",
        "quality",
        "describe",
        "similarity",
        "index",
        "router",
        "llm_director",
        "render",
    ]


def test_station_order_with_final(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, workspace, extra=["--final", "--json"]) == 0
    assert recorder.calls == [
        "proxy",
        "ingest",
        "motion",
        "quality",
        "describe",
        "similarity",
        "index",
        "router",
        "llm_director",
        "render",
        "render_final",
        "export_kdenlive",
    ]


def test_second_run_skips_everything(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, workspace, extra=["--json"]) == 0
    capsys.readouterr()
    recorder.calls.clear()
    assert run_recap(source, workspace, extra=["--json"]) == 0
    assert recorder.calls == []
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert all(entry["status"] == "skip" for entry in summary["stations"])
    assert "up to date" in captured.err


def test_new_brief_redoes_only_the_edit(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, workspace, brief="brief A", extra=["--json"]) == 0
    capsys.readouterr()
    recorder.calls.clear()
    assert run_recap(source, workspace, brief="brief B", extra=["--json"]) == 0
    assert recorder.calls == ["router", "llm_director", "render"]


def test_failure_stops_the_chain(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder(fail_at="quality")
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, workspace, extra=["--json"]) == 1
    assert recorder.calls == ["proxy", "ingest", "motion", "quality"]
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["status"] == "failed"
    quality = next(entry for entry in summary["stations"] if entry["name"] == "quality")
    assert quality["status"] == "FAIL"
    assert quality["detail"] == "exit 1"
    assert "[error] quality failed (exit 1)" in captured.err


def test_json_summary_fields(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder(
        costs={"describe": 0.1, "llm_director": 0.2},
        ledger={"total_usd": 0.42, "entries": []},
    )
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, workspace, extra=["--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["tool"] == "scripts/recap.py"
    assert isinstance(summary["stations"], list)
    assert summary["outputs"]["preview"] == str(workspace / "recap_720p.mp4")
    assert summary["outputs"]["final"] is None
    assert isinstance(summary["wall_time_s"], (int, float))
    assert summary["llm_cost_usd"] == pytest.approx(0.3)
    assert summary["llm_spend_usd"] == pytest.approx(0.42)


def test_progress_lines_format(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, workspace) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    progress = [
        line
        for line in lines
        if re.match(r"^\[\s*\d+/\d+\] \S+\s+(done|skip|FAIL)\s", line)
    ]
    assert len(progress) == 10
    assert any(line.startswith("recap:") for line in lines)
    assert run_recap(source, workspace, extra=["--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["tool"] == "scripts/recap.py"
    assert captured.out.strip().count("\n") == 0
    assert re.search(r"^\[\s*\d+/\d+\]", captured.err, re.M)


def test_real_execute_parses_child_json(tmp_path):
    source = make_source(tmp_path, names=("clip.mp4",))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx = recap.Context(
        source=source,
        workspace=workspace,
        brief_text="brief",
        final=False,
        budget_usd=None,
        force=False,
        json_mode=True,
        python=sys.executable,
        repo_root=recap.REPO_ROOT,
    )
    proxy = next(station for station in recap.build_stations(ctx) if station["name"] == "proxy")
    result = recap.execute(proxy, proxy["argv"], ctx)
    assert result["status"] == "done"
    assert result["detail"] == "written"
    assert isinstance(result["wall_time_s"], (int, float))
    assert (workspace / "proxy" / "clip.mp4").is_file()


def test_refuses_workspace_inside_source(tmp_path, capsys):
    source = make_source(tmp_path, names=("clip.mp4",))
    workspace = source / "workspace"
    assert run_recap(source, workspace) == 1
    err = capsys.readouterr().err
    assert "[error]" in err
    assert "inside the source" in err
    assert not workspace.exists()


def test_brief_txt_written_once(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, workspace, brief="same brief", extra=["--json"]) == 0
    brief_path = workspace / "brief.txt"
    assert brief_path.read_text(encoding="utf-8") == "same brief"
    before = brief_path.stat().st_mtime_ns
    capsys.readouterr()
    assert run_recap(source, workspace, brief="same brief", extra=["--json"]) == 0
    assert brief_path.stat().st_mtime_ns == before


def test_force_and_budget_forwarded(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    assert (
        run_recap(
            source,
            workspace,
            extra=["--budget-usd", "2.5", "--force", "--json"],
        )
        == 0
    )
    commands = dict(recorder.argvs)
    assert "--budget-usd" in commands["describe"][0]
    assert "2.5" in commands["describe"][0]
    assert "--budget-usd" in commands["llm_director"][0]
    assert "--force" in commands["proxy"][0]
    assert "--force" not in commands["ingest"][0]
