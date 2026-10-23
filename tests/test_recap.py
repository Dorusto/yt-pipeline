import json
import os
import re
import subprocess
import sys
import time

import pytest

import common.notify as notify
import recap
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME


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
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
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
            self.touch(ws / "edit.json", json.dumps({"brief": {"text": brief_text}, "made": self.clock}))
        elif name == "explain":
            self.touch(ws / "explain.html", "<html></html>")
        elif name == "recap_meta":
            self.touch(ws / "recap_meta.json", "{}")
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
        "explain",
        "recap_meta",
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
        "explain",
        "recap_meta",
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
    assert recorder.calls == ["router", "llm_director", "explain", "recap_meta", "render"]


def test_failure_stops_the_chain(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder(fail_at="quality")
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, workspace, extra=["--json"]) == EXIT_RUNTIME
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
    assert len(progress) == 12
    assert any(line.startswith("recap:") for line in lines)
    assert run_recap(source, workspace, extra=["--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["tool"] == "scripts/recap.py"
    assert captured.out.strip().count("\n") == 0
    assert re.search(r"^\[\s*\d+/\d+\]", captured.err, re.M)


def test_start_line_before_each_running_station(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, workspace) == 0
    lines = capsys.readouterr().out.splitlines()
    for name in recorder.calls:
        starts = [
            index
            for index, line in enumerate(lines)
            if re.match(rf"^\[\s*\d+/\d+\] {re.escape(name)}\s+start$", line)
        ]
        finishes = [
            index
            for index, line in enumerate(lines)
            if re.match(rf"^\[\s*\d+/\d+\] {re.escape(name)}\s+(done|FAIL)\s", line)
        ]
        assert len(starts) == 1, name
        assert len(finishes) == 1, name
        assert starts[0] < finishes[0], name


def test_no_start_line_for_skipped_stations(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, workspace, extra=["--json"]) == 0
    capsys.readouterr()
    assert run_recap(source, workspace) == 0
    lines = capsys.readouterr().out.splitlines()
    start_pattern = re.compile(r"^\[\s*\d+/\d+\] \S+\s+start$")
    assert not [line for line in lines if start_pattern.match(line)]
    skips = [line for line in lines if re.match(r"^\[\s*\d+/\d+\] \S+\s+skip\s", line)]
    assert len(skips) == 12


def test_start_line_format(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, workspace) == 0
    pattern = re.compile(r"^\[\s*\d+/\d+\] \S+\s+start$")
    start_lines = [
        line for line in capsys.readouterr().out.splitlines() if line.endswith(" start")
    ]
    assert start_lines
    assert all(pattern.match(line) for line in start_lines)
    assert len(start_lines) == len(recorder.calls)


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
    assert run_recap(source, workspace) == EXIT_USAGE
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


def make_context(tmp_path, music=None, final=False, feedback=None):
    source = tmp_path / "raw"
    source.mkdir(exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return recap.Context(
        source=source,
        workspace=workspace,
        brief_text="brief",
        final=final,
        budget_usd=None,
        force=False,
        json_mode=True,
        python=sys.executable,
        repo_root=recap.REPO_ROOT,
        music=music,
        feedback=feedback,
    )


def test_music_threads_beats_and_music(tmp_path):
    music = tmp_path / "track.wav"
    music.write_bytes(b"")
    ctx = make_context(tmp_path, music=music, final=True)
    stations = recap.build_stations(ctx)
    names = [station["name"] for station in stations]
    assert names.index("beats") == names.index("ingest") + 1
    commands = {station["name"]: station["argv"][0] for station in stations}
    beats = commands["beats"]
    assert str(music) in beats
    assert "--out" in beats
    assert str(ctx.workspace / "beats.json") in beats
    director = commands["llm_director"]
    assert "--beats" in director
    assert str(ctx.workspace / "beats.json") in director
    for name in ("render", "render_final"):
        assert "--music" in commands[name]
        assert str(music) in commands[name]


def test_without_music_no_beats_or_flags(tmp_path):
    ctx = make_context(tmp_path)
    stations = recap.build_stations(ctx)
    names = [station["name"] for station in stations]
    assert "beats" not in names
    for station in stations:
        argv = station["argv"][0]
        assert "--beats" not in argv
        assert "--music" not in argv


def test_feedback_forwarded_to_director_station(tmp_path):
    ctx = make_context(tmp_path, feedback=["drop 0071/s2", "tunnel longer"])
    stations = recap.build_stations(ctx)
    command = next(s for s in stations if s["name"] == "llm_director")["argv"][0]
    assert command.count("--feedback") == 2
    assert "drop 0071/s2" in command
    assert "tunnel longer" in command
    for name in ("proxy", "render"):
        argv = next(s for s in stations if s["name"] == name)["argv"][0]
        assert "--feedback" not in argv


def test_feedback_reruns_only_edit_and_render(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, workspace, extra=["--json"]) == 0
    capsys.readouterr()
    recorder.calls.clear()
    assert (
        run_recap(source, workspace, extra=["--feedback", "drop 0071/s2", "--json"])
        == 0
    )
    assert recorder.calls == ["llm_director", "explain", "recap_meta", "render"]
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    statuses = {entry["name"]: entry["status"] for entry in summary["stations"]}
    assert statuses["llm_director"] == "done"
    assert statuses["render"] == "done"
    for name in ("proxy", "ingest", "motion", "quality", "describe", "similarity", "index", "router"):
        assert statuses[name] == "skip"
    commands = dict(recorder.argvs)
    assert "--feedback" in commands["llm_director"][0]
    assert "drop 0071/s2" in commands["llm_director"][0]


class NotifyRecorder:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def __call__(self, message, title=None, **kwargs):
        self.calls.append({"message": message, "title": title})
        if self.error is not None:
            raise self.error
        return 200


def test_notify_posts_preview_link(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    sent = NotifyRecorder()
    monkeypatch.setattr(notify, "send", sent)
    assert (
        run_recap(
            source,
            workspace,
            extra=["--notify", "--preview-base", "http://x:8091", "--json"],
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["notified"] is True
    assert summary["preview_url"] == "http://x:8091/workspace/recap_720p.mp4"
    assert isinstance(summary["wall_time_s"], (int, float))
    assert isinstance(summary["llm_cost_usd"], (int, float))
    assert len(sent.calls) == 1
    assert sent.calls[0]["title"] == "Recap ready (720p)"
    assert "http://x:8091/workspace/recap_720p.mp4" in sent.calls[0]["message"]
    assert str(workspace) in sent.calls[0]["message"]


def test_preview_base_trailing_slash_stripped(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    sent = NotifyRecorder()
    monkeypatch.setattr(notify, "send", sent)
    assert (
        run_recap(
            source,
            workspace,
            extra=["--notify", "--preview-base", "http://x:8091/", "--json"],
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["preview_url"] == "http://x:8091/workspace/recap_720p.mp4"


def test_preview_url_quotes_workspace_name(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "my ws"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    sent = NotifyRecorder()
    monkeypatch.setattr(notify, "send", sent)
    assert (
        run_recap(
            source,
            workspace,
            extra=["--notify", "--preview-base", "http://x:8091", "--json"],
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["preview_url"] == "http://x:8091/my%20ws/recap_720p.mp4"


def test_notify_without_preview_base_uses_workspace_path(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    sent = NotifyRecorder()
    monkeypatch.setattr(notify, "send", sent)
    assert run_recap(source, workspace, extra=["--notify", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["preview_url"] is None
    assert summary["notified"] is True
    assert len(sent.calls) == 1
    assert str(workspace) in sent.calls[0]["message"]
    assert "Preview:" not in sent.calls[0]["message"]


def test_without_notify_flag_no_message(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    sent = NotifyRecorder()
    monkeypatch.setattr(notify, "send", sent)
    assert run_recap(source, workspace, extra=["--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["notified"] is False
    assert sent.calls == []


def test_failed_run_does_not_notify(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder(fail_at="quality")
    monkeypatch.setattr(recap, "execute", recorder)
    sent = NotifyRecorder()
    monkeypatch.setattr(notify, "send", sent)
    assert run_recap(source, workspace, extra=["--notify", "--json"]) == EXIT_RUNTIME
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "failed"
    assert summary["notified"] is False
    assert sent.calls == []


def test_notify_failure_does_not_fail_recap(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    sent = NotifyRecorder(error=notify.NotifyError("boom"))
    monkeypatch.setattr(notify, "send", sent)
    assert run_recap(source, workspace, extra=["--notify", "--json"]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["notified"] is False
    assert "[warn] notify failed" in captured.err


def test_all_skipped_run_titles_cached(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    sent = NotifyRecorder()
    monkeypatch.setattr(notify, "send", sent)
    assert run_recap(source, workspace, extra=["--json"]) == 0
    capsys.readouterr()
    assert run_recap(source, workspace, extra=["--notify", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert all(entry["status"] == "skip" for entry in summary["stations"])
    assert summary["notified"] is True
    assert len(sent.calls) == 1
    assert sent.calls[0]["title"] == "Recap ready (cached)"


def test_music_auto_places_music_pick_after_index(tmp_path):
    ctx = make_context(tmp_path, music="auto")
    ctx.music_dir = tmp_path / "music"
    stations = recap.build_stations(ctx)
    names = [station["name"] for station in stations]
    assert "music_pick" in names
    assert "beats" not in names
    assert names.index("music_pick") == names.index("index") + 1
    pick = next(station for station in stations if station["name"] == "music_pick")
    argv = pick["argv"][0]
    assert "music_pick.py" in " ".join(argv)
    assert "--workspace" in argv
    for name in ("llm_director", "render"):
        command = next(station for station in stations if station["name"] == name)["argv"][0]
        assert recap.MUSIC_TOKEN in command


def test_resolve_argv_substitutes_the_music_token(tmp_path):
    ctx = make_context(tmp_path, music="auto")
    assert recap.resolve_argv(["--music", recap.MUSIC_TOKEN], ctx) == ["--music", recap.MUSIC_TOKEN]
    track = tmp_path / "track.mp3"
    track.write_bytes(b"")
    ctx.music = track
    assert recap.resolve_argv(["--music", recap.MUSIC_TOKEN], ctx) == ["--music", str(track)]


def test_resolve_auto_music_reads_the_choice(tmp_path):
    ctx = make_context(tmp_path, music="auto")
    assert recap.resolve_auto_music(ctx) is False
    track = tmp_path / "chosen.mp3"
    track.write_bytes(b"")
    (ctx.workspace / "music_choice.json").write_text(
        json.dumps({"path": str(track)}), encoding="utf-8"
    )
    assert recap.resolve_auto_music(ctx) is True
    assert ctx.music == track.resolve()


def test_resolve_argv_substitutes_inside_nested_commands(tmp_path):
    track = tmp_path / "track.mp3"
    track.write_bytes(b"")
    ctx = make_context(tmp_path, music=track)
    nested = [["python", "render.py", "--music", recap.MUSIC_TOKEN]]
    resolved = recap.resolve_argv(nested, ctx)
    assert resolved == [["python", "render.py", "--music", str(track)]]


def stations_of(ctx):
    return {station["name"]: station["argv"] for station in recap.build_stations(ctx)}


def test_default_format_has_no_vertical_station(tmp_path):
    ctx = make_context(tmp_path, final=True)
    names = list(stations_of(ctx))
    assert "render_vertical" not in names and "render_final_vertical" not in names
    assert all("--format" not in command for command in stations_of(ctx)["render"])


def test_format_both_adds_the_vertical_render_after_the_wide_one(tmp_path):
    ctx = make_context(tmp_path)
    ctx.frame_format = "both"
    stations = stations_of(ctx)
    names = list(stations)
    assert names.index("render_vertical") == names.index("render") + 1
    command = stations["render_vertical"][0]
    assert command[command.index("--out") + 1] == str(ctx.workspace / "recap_720p_9x16.mp4")
    assert command[command.index("--format") + 1] == "9x16"
    assert "--format" not in stations["render"][0]


def test_format_9x16_replaces_the_wide_render(tmp_path):
    ctx = make_context(tmp_path, final=True)
    ctx.frame_format = "9x16"
    names = list(stations_of(ctx))
    assert "render" not in names and "render_final" not in names
    assert "render_vertical" in names and "render_final_vertical" in names
    final = stations_of(ctx)["render_final_vertical"][0]
    assert final[final.index("--out") + 1] == str(ctx.workspace / "recap_final_9x16.mp4")
    assert "--originals-dir" in final and final[final.index("--format") + 1] == "9x16"


def test_vertical_station_skip_rule_follows_its_own_file(tmp_path):
    ctx = make_context(tmp_path)
    ctx.frame_format = "both"
    edit = ctx.workspace / "edit.json"
    edit.write_text("{}", encoding="utf-8")
    assert recap.is_up_to_date("render_vertical", ctx) is False
    out = ctx.workspace / "recap_720p_9x16.mp4"
    out.write_bytes(b"x")
    os.utime(out, (edit.stat().st_mtime + 5, edit.stat().st_mtime + 5))
    assert recap.is_up_to_date("render_vertical", ctx) is True


def test_recap_meta_station_skip_rule_follows_its_own_file(tmp_path):
    ctx = make_context(tmp_path)
    edit = ctx.workspace / "edit.json"
    edit.write_text("{}", encoding="utf-8")
    assert recap.is_up_to_date("recap_meta", ctx) is False
    out = ctx.workspace / "recap_meta.json"
    out.write_text("{}", encoding="utf-8")
    os.utime(out, (edit.stat().st_mtime + 5, edit.stat().st_mtime + 5))
    assert recap.is_up_to_date("recap_meta", ctx) is True


def test_format_flag_reaches_the_context_and_the_outputs(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, workspace, extra=["--format", "both", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert recorder.calls[-2:] == ["render", "render_vertical"]
    assert summary["outputs"]["preview_vertical"] == str(workspace / "recap_720p_9x16.mp4")


def test_feedback_reruns_the_vertical_render_too(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder()
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, workspace, extra=["--format", "both", "--feedback", "drop 0071/s2", "--json"]) == 0
    assert "render_vertical" in recorder.calls


def test_speed_ramp_is_off_by_default_in_the_director_argv(tmp_path):
    ctx = make_context(tmp_path)
    assert ctx.speed_ramp is False
    assert "--speed-ramp" not in recap.director_args(ctx)
    assert all("--speed-ramp" not in command for command in stations_of(ctx)["llm_director"])


def test_speed_ramp_reaches_the_llm_director_station(tmp_path):
    ctx = make_context(tmp_path)
    ctx.speed_ramp = True
    assert "--speed-ramp" in recap.director_args(ctx)
    assert any("--speed-ramp" in command for command in stations_of(ctx)["llm_director"])


def test_speed_ramp_flag_is_parsed_and_listed_in_help(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    seen = {}

    def spy(ctx):
        seen["ramp"] = ctx.speed_ramp
        return {"status": "ok", "stations": [], "outputs": {}}

    monkeypatch.setattr(recap, "run", spy)
    assert run_recap(source, workspace, extra=["--speed-ramp", "--json"]) in (0, 1)
    assert seen["ramp"] is True
    assert "--speed-ramp" in recap.build_parser().format_help()


def test_changing_the_speed_ramp_setting_redoes_the_edit(tmp_path):
    ctx = make_context(tmp_path)
    edit = ctx.workspace / "edit.json"
    edit.write_text(json.dumps({"brief": {"text": "brief"}, "meta": {"speed_ramp": False}}), encoding="utf-8")
    (ctx.workspace / "shotlog.json").write_text("{}", encoding="utf-8")
    os.utime(edit, (edit.stat().st_mtime + 5, edit.stat().st_mtime + 5))
    assert recap.is_up_to_date("llm_director", ctx) is True
    ctx.speed_ramp = True
    assert recap.is_up_to_date("llm_director", ctx) is False


def test_a_failed_vertical_render_does_not_stop_the_other_renders(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    recorder = Recorder(fail_at="render_vertical")
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, workspace, extra=["--format", "both", "--final", "--json"]) == EXIT_RUNTIME
    assert recorder.calls[-4:] == ["render_vertical", "render_final", "render_final_vertical", "export_kdenlive"]
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "failed"
    assert (workspace / "recap_720p.mp4").is_file()


def test_each_new_edit_is_kept_as_a_version(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(recap, "execute", Recorder())
    assert run_recap(source, workspace, extra=["--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["version"] == "v001"
    assert run_recap(source, workspace, extra=["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["version"] is None
    monkeypatch.setattr(recap, "execute", Recorder())
    assert run_recap(source, workspace, extra=["--feedback", "tunnel longer", "--json"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["version"] == "v002"
    meta = json.loads((workspace / "versions" / "v002" / "version.json").read_text(encoding="utf-8"))
    assert meta["feedback"] == ["tunnel longer"]
    assert meta["status"] == "done"
    assert (workspace / "versions" / "v001" / "recap_720p.mp4").is_file()


def test_a_failed_render_keeps_the_new_edit_without_a_video(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(recap, "execute", Recorder())
    assert run_recap(source, workspace, extra=["--json"]) == 0
    capsys.readouterr()
    later = Recorder(fail_at="render")
    later.clock += 10000.0
    monkeypatch.setattr(recap, "execute", later)
    assert run_recap(source, workspace, extra=["--feedback", "shorter", "--json"]) == EXIT_RUNTIME
    summary = json.loads(capsys.readouterr().out)
    kept = workspace / "versions" / summary["version"]
    assert (kept / "edit.json").is_file()
    assert not (kept / "recap_720p.mp4").exists()
    assert json.loads((kept / "version.json").read_text(encoding="utf-8"))["failed_at"] == "render"


def test_new_edit_reruns_the_edit_without_feedback(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path)
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(recap, "execute", Recorder())
    assert run_recap(source, workspace, extra=["--json"]) == 0
    capsys.readouterr()
    recorder = Recorder()
    recorder.clock += 10000.0
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, workspace, extra=["--new-edit", "--json"]) == 0
    assert recorder.calls == ["llm_director", "explain", "recap_meta", "render"]
    director_argv = dict(recorder.argvs)["llm_director"]
    flat = director_argv[0] if isinstance(director_argv[0], list) else director_argv
    assert "--force" in flat


def write_isolated_config(tmp_path, monkeypatch, library=None):
    text = (
        "models:\n  director: deepseek/deepseek-v4.1-flash\n"
        "llm_budget_usd: 5.0\nllm_backend: mock\n"
    )
    if library is not None:
        text += f"music:\n  library: {library}\n"
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.delenv("YT_PIPELINE_MUSIC_LIBRARY", raising=False)
    monkeypatch.setenv("YT_CONFIG", str(path))
    return path


def test_music_auto_omits_library_when_unset(tmp_path):
    ctx = make_context(tmp_path, music="auto")
    ctx.music_dir = None
    stations = recap.build_stations(ctx)
    pick = next(station for station in stations if station["name"] == "music_pick")
    argv = pick["argv"][0]
    assert "--library" not in argv
    assert "--workspace" in argv


def test_music_auto_without_library_is_a_usage_error(tmp_path, monkeypatch, capsys):
    write_isolated_config(tmp_path, monkeypatch)
    source = make_source(tmp_path, names=("clip_a.mp4",))
    assert run_recap(source, tmp_path / "ws", extra=["--music", "auto", "--json"]) == EXIT_USAGE
    error = capsys.readouterr().err
    assert "no music library configured" in error
    for way in ("--music-dir", "YT_PIPELINE_MUSIC_LIBRARY", "music.library"):
        assert way in error


class MusicRecorder(Recorder):
    def create_outputs(self, name, ctx):
        super().create_outputs(name, ctx)
        if name == "music_pick":
            self.touch(ctx.workspace / "music_choice.json", json.dumps({"path": "track.mp3"}))


def test_music_auto_reads_the_library_from_the_config(tmp_path, monkeypatch, capsys):
    library = tmp_path / "music"
    library.mkdir()
    write_isolated_config(tmp_path, monkeypatch, library=str(library))
    source = make_source(tmp_path, names=("clip_a.mp4",))
    recorder = MusicRecorder()
    monkeypatch.setattr(recap, "execute", recorder)
    assert run_recap(source, tmp_path / "ws", extra=["--music", "auto", "--json"]) == 0
    commands = dict(recorder.argvs)
    argv = commands["music_pick"][0]
    assert argv[argv.index("--library") + 1] == str(library)
    capsys.readouterr()


def test_main_resolves_the_music_library_path(tmp_path, monkeypatch):
    source = make_source(tmp_path, names=("clip_a.mp4",))
    real = tmp_path / "real-lib"
    real.mkdir()
    link = tmp_path / "link-lib"
    link.symlink_to(real)
    captured = {}

    def fake_run(ctx):
        captured["music_dir"] = ctx.music_dir
        return {"status": "done"}

    monkeypatch.setattr(recap, "run", fake_run)
    code = recap.main(
        [
            "--source",
            str(source),
            "--brief",
            "a calm recap",
            "--workspace",
            str(tmp_path / "ws"),
            "--music",
            "auto",
            "--music-dir",
            str(link),
            "--json",
        ]
    )
    assert code == 0
    assert captured["music_dir"] == real.resolve()


def test_music_help_names_the_three_ways():
    actions = {action.dest: action for action in recap.build_parser()._actions}
    help_text = actions["music"].help
    for way in ("--music-dir", "YT_PIPELINE_MUSIC_LIBRARY", "music.library"):
        assert way in help_text


def test_proxy_up_to_date_when_source_gone_and_proxies_remain(tmp_path):
    ctx = make_context(tmp_path)
    (ctx.workspace / "proxy").mkdir(parents=True, exist_ok=True)
    (ctx.workspace / "proxy" / "clip.mp4").write_bytes(b"proxy")
    assert recap.is_up_to_date("proxy", ctx) is True


def test_proxy_station_carries_the_exclude_file(tmp_path):
    ctx = make_context(tmp_path)
    stations = recap.build_stations(ctx)
    proxy = next(station for station in stations if station["name"] == "proxy")
    argv = proxy["argv"][0]
    assert "--exclude-file" in argv
    assert argv[argv.index("--exclude-file") + 1] == str(ctx.workspace / "excluded.json")
