import json
import os
import stat
import subprocess
from pathlib import Path

import loudness
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE

TICKS = 254016000000

STUB = """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")

if "essentialSound.inspect" in sys.argv:
    print(json.dumps({"ok": True, "result": {"loudness": {
        "measured_lufs": -14.2, "target_lufs": -14.0, "match_gain": 0.2}}}))
else:
    print(json.dumps({"ok": True}))
"""


def make_clip(path, duration=2.0):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=320x180:rate=25:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=f=440:duration={duration}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def probe_video(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)["streams"][0]


def write_stub(tmp_path):
    stub = tmp_path / "filmcraft-cli"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def write_project(path, clips):
    audio_items = []
    for index, (item_id, enabled) in enumerate(clips):
        audio_items.append(
            {
                "id": 20 + index,
                "item": item_id,
                "name": f"clip {index}",
                "start": index * TICKS,
                "duration": TICKS,
                "source_in": 0,
                "speed": 1.0,
                "reverse": False,
                "enabled": enabled,
                "markers": [],
            }
        )
    project = {
        "format": "filmcraft-project",
        "schema_version": 1,
        "project": {
            "name": "Fixture",
            "settings": {},
            "items": {
                "1": {"id": 1, "name": "a.mp4", "kind": {"Media": {"media": {"File": {"path": "/a.mp4"}}}}},
                "2": {"id": 2, "name": "b.mp4", "kind": {"Media": {"media": {"File": {"path": "/b.mp4"}}}}},
                "10": {
                    "id": 10,
                    "name": "Main",
                    "kind": {
                        "Sequence": {
                            "settings": {"width": 1920, "height": 1080, "frame_rate": {"num": 25, "den": 1}},
                            "video_tracks": [{"id": 11, "kind": "Video", "name": "Video 1", "items": []}],
                            "audio_tracks": [{"id": 12, "kind": "Audio", "name": "Audio 1", "items": audio_items}],
                            "markers": [],
                            "caption_tracks": [],
                        }
                    },
                },
            },
            "next_id": 30,
        },
        "view": {},
    }
    path.write_text(json.dumps(project), encoding="utf-8")
    return path


def read_log(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_video_backend_writes_output(tmp_path, capsys):
    clip = make_clip(tmp_path / "clip.mp4")
    rc = loudness.main(["--video", str(clip), "--json"])
    assert rc == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["backend"] == "ffmpeg"
    assert summary["output"] == str(tmp_path / "clip_loud.mp4")
    assert Path(summary["output"]).is_file()
    assert summary["target"] == {"i": -14.0, "tp": -1.0, "lra": 11.0}
    for key in ("input_i", "input_tp", "input_lra", "input_thresh"):
        assert isinstance(summary["measured"][key], float)
    assert summary["llm_cost_usd"] == 0.0
    assert summary["wall_time_s"] >= 0.0


def test_video_backend_copies_video_stream(tmp_path, capsys):
    clip = make_clip(tmp_path / "clip.mp4")
    before = probe_video(clip)
    rc = loudness.main(["--video", str(clip), "--json"])
    assert rc == EXIT_OK
    capsys.readouterr()
    after = probe_video(tmp_path / "clip_loud.mp4")
    assert after["codec_name"] == before["codec_name"]
    assert after["width"] == before["width"]
    assert after["height"] == before["height"]


def test_video_backend_refuses_input_overwrite(tmp_path, capsys):
    clip = make_clip(tmp_path / "clip.mp4")
    rc = loudness.main(["--video", str(clip), "--out", str(clip), "--json"])
    assert rc == EXIT_USAGE
    assert "refusing to overwrite" in capsys.readouterr().err


def test_video_backend_missing_file(tmp_path, capsys):
    rc = loudness.main(["--video", str(tmp_path / "nope.mp4"), "--json"])
    assert rc == EXIT_USAGE
    assert "not found" in capsys.readouterr().err


def test_fcproj_runs_three_commands_and_inspect(tmp_path, capsys, monkeypatch):
    stub = write_stub(tmp_path)
    log = tmp_path / "stub.log"
    monkeypatch.setenv("FILMCRAFT_CLI", str(stub))
    monkeypatch.setenv("STUB_LOG", str(log))
    project = write_project(tmp_path / "proj.fcproj", [(1, True), (2, True), (2, False)])
    original = project.read_bytes()

    rc = loudness.main(["--fcproj", str(project), "--json"])
    assert rc == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["backend"] == "filmcraft"
    assert summary["sequence"] == "Main"
    assert summary["track"] == "Audio 1"
    assert summary["clips"] == [1, 2]
    assert summary["target_lufs"] == -14.0
    assert summary["measured"] == {"measured_lufs": -14.2, "target_lufs": -14.0, "match_gain": 0.2}
    assert summary["dry_run"] is False

    copy = tmp_path / "proj_loudness.fcproj"
    assert copy.is_file()
    assert project.read_bytes() == original

    entries = read_log(log)
    assert len(entries) == 4
    set_type, auto_match, set_values, inspect = entries
    assert set_type[1] == "essentialSound.setType"
    assert "type=dialogue" in set_type
    assert "clips=[1,2]" in set_type
    assert auto_match[1] == "essentialSound.autoMatch"
    assert "target=-14" in auto_match
    assert "clips=[1,2]" in auto_match
    assert set_values[1] == "essentialSound.set"
    assert any("clarity.dynamics.amount" in arg for arg in set_values)
    assert any("clarity.dynamics.on" in arg for arg in set_values)
    assert inspect[1] == "essentialSound.inspect"
    for entry in entries:
        assert entry[entry.index("--project") + 1] == str(copy)
        assert "--save" in entry
        assert "--compact" in entry


def test_fcproj_in_place_runs_against_original(tmp_path, capsys, monkeypatch):
    stub = write_stub(tmp_path)
    log = tmp_path / "stub.log"
    monkeypatch.setenv("FILMCRAFT_CLI", str(stub))
    monkeypatch.setenv("STUB_LOG", str(log))
    project = write_project(tmp_path / "proj.fcproj", [(1, True)])
    original = project.read_bytes()

    rc = loudness.main(["--fcproj", str(project), "--in-place", "--json"])
    assert rc == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["output"] == str(project)
    assert not (tmp_path / "proj_loudness.fcproj").exists()
    assert project.read_bytes() == original
    for entry in read_log(log):
        assert entry[entry.index("--project") + 1] == str(project)


def test_fcproj_dry_run_does_not_invoke_stub(tmp_path, capsys, monkeypatch):
    stub = write_stub(tmp_path)
    log = tmp_path / "stub.log"
    monkeypatch.setenv("FILMCRAFT_CLI", str(stub))
    monkeypatch.setenv("STUB_LOG", str(log))
    project = write_project(tmp_path / "proj.fcproj", [(1, True), (2, True)])

    rc = loudness.main(["--fcproj", str(project), "--dry-run", "--json"])
    assert rc == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["dry_run"] is True
    assert summary["measured"] is None
    assert len(summary["commands"]) == 3
    assert "essentialSound.setType" in summary["commands"][0]
    assert "type=dialogue" in summary["commands"][0]
    assert "target=-14" in summary["commands"][1]
    assert "clarity.dynamics.amount" in summary["commands"][2]
    assert not log.exists()
    assert not (tmp_path / "proj_loudness.fcproj").exists()


def test_fcproj_dry_run_prints_commands(tmp_path, capsys, monkeypatch):
    stub = write_stub(tmp_path)
    monkeypatch.setenv("FILMCRAFT_CLI", str(stub))
    monkeypatch.setenv("STUB_LOG", str(tmp_path / "stub.log"))
    project = write_project(tmp_path / "proj.fcproj", [(1, True)])
    rc = loudness.main(["--fcproj", str(project), "--dry-run"])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert out.count("essentialSound.") == 3


def test_fcproj_missing_binary(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FILMCRAFT_CLI", raising=False)
    monkeypatch.setenv("PATH", "")
    project = write_project(tmp_path / "proj.fcproj", [(1, True)])
    rc = loudness.main(["--fcproj", str(project), "--json"])
    assert rc == EXIT_RUNTIME
    err = capsys.readouterr().err
    assert "FILMCRAFT_CLI" in err and "filmcraft-cli" in err


def test_fcproj_dry_run_without_binary(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FILMCRAFT_CLI", raising=False)
    monkeypatch.setenv("PATH", "")
    project = write_project(tmp_path / "proj.fcproj", [(1, True)])
    rc = loudness.main(["--fcproj", str(project), "--dry-run", "--json"])
    assert rc == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["dry_run"] is True
    assert len(summary["commands"]) == 3
    assert "filmcraft-cli" in summary["commands"][0]


def test_fcproj_unknown_track(tmp_path, capsys, monkeypatch):
    stub = write_stub(tmp_path)
    monkeypatch.setenv("FILMCRAFT_CLI", str(stub))
    monkeypatch.setenv("STUB_LOG", str(tmp_path / "stub.log"))
    project = write_project(tmp_path / "proj.fcproj", [(1, True)])
    rc = loudness.main(["--fcproj", str(project), "--track", "Nope", "--json"])
    assert rc == EXIT_USAGE
    err = capsys.readouterr().err
    assert "Nope" in err and "Audio 1" in err


def test_fcproj_no_enabled_clip(tmp_path, capsys, monkeypatch):
    stub = write_stub(tmp_path)
    monkeypatch.setenv("FILMCRAFT_CLI", str(stub))
    monkeypatch.setenv("STUB_LOG", str(tmp_path / "stub.log"))
    project = write_project(tmp_path / "proj.fcproj", [(1, False)])
    rc = loudness.main(["--fcproj", str(project), "--json"])
    assert rc == EXIT_USAGE
    assert "no enabled clip" in capsys.readouterr().err


def test_fcproj_cli_failure_exits_one(tmp_path, capsys, monkeypatch):
    failing = tmp_path / "filmcraft-cli"
    failing.write_text("#!/bin/sh\necho 'boom' >&2\nexit 3\n", encoding="utf-8")
    failing.chmod(failing.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("FILMCRAFT_CLI", str(failing))
    project = write_project(tmp_path / "proj.fcproj", [(1, True)])
    rc = loudness.main(["--fcproj", str(project), "--json"])
    assert rc == EXIT_RUNTIME
    err = capsys.readouterr().err
    assert "essentialSound.setType" in err and "boom" in err


def test_both_backends_is_an_error(tmp_path, capsys):
    rc = loudness.main(["--video", "a.mp4", "--fcproj", "b.fcproj", "--json"])
    assert rc == EXIT_USAGE
    assert "exactly one" in capsys.readouterr().err


def test_neither_backend_is_an_error(capsys):
    rc = loudness.main(["--json"])
    assert rc == EXIT_USAGE
    assert "exactly one" in capsys.readouterr().err


def test_out_and_in_place_are_mutually_exclusive(tmp_path, capsys):
    rc = loudness.main(["--fcproj", "b.fcproj", "--out", "c.fcproj", "--in-place", "--json"])
    assert rc == EXIT_USAGE
    assert "mutually exclusive" in capsys.readouterr().err
