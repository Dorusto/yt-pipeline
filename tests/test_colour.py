import json
import os
import stat
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import colour
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE

TICKS = 254016000000

STUB = """#!/usr/bin/env python3
import json
import os
import sys

data = sys.stdin.read()
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": sys.argv[1:], "stdin": data}) + "\\n")
for line in data.splitlines():
    if line.strip():
        print(json.dumps({"id": json.loads(line)["id"], "line": 1, "ok": True, "result": {}}))
"""


def gradient():
    row = np.linspace(0, 255, 256, dtype=np.uint8)
    grey = np.tile(row, (64, 1))
    return np.dstack([grey, grey, grey])


def solid(value):
    return np.full((64, 64, 3), value, dtype=np.uint8)


def cast(channel, value=180, base=128):
    frame = np.full((64, 64, 3), base, dtype=np.uint8)
    frame[:, :, channel] = value
    return frame


def make_video(path, color="blue"):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:size=320x180:rate=25:duration=1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def write_stub(tmp_path):
    stub = tmp_path / "filmcraft-cli"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def write_project(path, clips):
    video_items = []
    media_items = {}
    for index, spec in enumerate(clips):
        clip_id = spec.get("id", 29 + index)
        name = spec.get("name", Path(spec["path"]).name)
        video_items.append(
            {
                "id": clip_id,
                "item": spec["item"],
                "name": name,
                "start": index * TICKS,
                "duration": TICKS,
                "source_in": 0,
                "speed": 1.0,
                "enabled": spec.get("enabled", True),
                "effects": spec.get("effects", []),
            }
        )
        media_items[str(spec["item"])] = {
            "id": spec["item"],
            "name": name,
            "kind": {"Media": {"media": {"File": {"path": str(spec["path"])}}}},
        }
    media_items["10"] = {
        "id": 10,
        "name": "Main",
        "kind": {
            "Sequence": {
                "settings": {"width": 1920, "height": 1080, "frame_rate": {"num": 25, "den": 1}},
                "video_tracks": [{"id": 11, "kind": "Video", "name": "Camera 1", "items": video_items}],
                "audio_tracks": [],
                "markers": [],
                "caption_tracks": [],
            }
        },
    }
    project = {
        "format": "filmcraft-project",
        "schema_version": 1,
        "project": {"name": "Fixture", "settings": {}, "items": media_items, "next_id": 40},
        "view": {},
    }
    path.write_text(json.dumps(project), encoding="utf-8")
    return path


def read_log(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def log_lines(entry):
    return [json.loads(line) for line in entry["stdin"].splitlines() if line.strip()]


def test_neutral_gradient_is_neutral():
    measured = colour.measure_frames([gradient()])
    correction = colour.compute_correction(measured, colour.TARGET_LUMA, colour.TARGET_CONTRAST)
    assert abs(correction["temperature"]) < 0.5
    assert abs(correction["tint"]) < 0.5
    assert abs(correction["exposure"]) < 0.05


def test_blue_cast_warms():
    measured = colour.measure_frames([cast(0)])
    correction = colour.compute_correction(measured, colour.TARGET_LUMA, colour.TARGET_CONTRAST)
    assert correction["temperature"] > 0


def test_green_cast_adds_magenta():
    measured = colour.measure_frames([cast(1)])
    correction = colour.compute_correction(measured, colour.TARGET_LUMA, colour.TARGET_CONTRAST)
    assert correction["tint"] > 0


def test_dark_frame_brightens():
    measured = colour.measure_frames([solid(40)])
    correction = colour.compute_correction(measured, colour.TARGET_LUMA, colour.TARGET_CONTRAST)
    assert correction["exposure"] > 0


def test_bright_frame_darkens():
    measured = colour.measure_frames([solid(220)])
    correction = colour.compute_correction(measured, colour.TARGET_LUMA, colour.TARGET_CONTRAST)
    assert correction["exposure"] < 0


def test_low_contrast_raises_contrast():
    frame = np.full((64, 64, 3), 128, dtype=np.uint8)
    frame[:32, :, :] = 120
    frame[32:, :, :] = 136
    measured = colour.measure_frames([frame])
    correction = colour.compute_correction(measured, colour.TARGET_LUMA, colour.TARGET_CONTRAST)
    assert correction["contrast"] > 0


def test_group_sessions_splits_on_gap():
    base = datetime(2026, 10, 20, 19, 0, 0)
    clips = [
        {"capture_time": base},
        {"capture_time": base + timedelta(minutes=30)},
        {"capture_time": base + timedelta(minutes=300)},
    ]
    sessions = colour.group_sessions(clips, 180)
    assert len(sessions) == 2
    assert len(sessions[0]) == 2
    assert len(sessions[1]) == 1


def test_capture_time_reads_stamp(tmp_path):
    path = tmp_path / "camera 2026-10-20 19-30-00.mp4"
    path.write_bytes(b"x")
    assert colour.capture_time(path, path.name) == datetime(2026, 10, 20, 19, 30, 0)


def test_capture_time_falls_back_to_mtime(tmp_path):
    path = tmp_path / "plain.mp4"
    path.write_bytes(b"x")
    stamp = 1_700_000_000
    os.utime(path, (stamp, stamp))
    assert colour.capture_time(path, path.name) == datetime.fromtimestamp(stamp)


def test_fcproj_end_to_end(tmp_path, capsys, monkeypatch):
    stub = write_stub(tmp_path)
    log = tmp_path / "stub.log"
    monkeypatch.setenv("FILMCRAFT_CLI", str(stub))
    monkeypatch.setenv("STUB_LOG", str(log))
    video_a = make_video(tmp_path / "camera 2026-10-20 19-30-00.mp4", "blue")
    video_b = make_video(tmp_path / "camera 2026-10-21 20-00-00.mp4", "green")
    project = write_project(
        tmp_path / "proj.fcproj",
        [
            {"item": 1, "path": video_a, "name": video_a.name},
            {"item": 2, "path": video_b, "name": video_b.name},
        ],
    )
    original = project.read_bytes()

    rc = colour.main(["--fcproj", str(project), "--json"])
    assert rc == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["backend"] == "filmcraft"
    assert summary["sequence"] == "Main"
    assert summary["track"] == "Camera 1"
    assert summary["dry_run"] is False
    assert summary["llm_cost_usd"] == 0.0
    assert summary["wall_time_s"] >= 0.0
    assert len(summary["sessions"]) == 2
    for session in summary["sessions"]:
        assert session["measured"] is not None
        assert set(session["correction"]) == {"temperature", "tint", "exposure", "contrast"}
    assert summary["clips"] == [29, 30]

    copy = tmp_path / "proj_colour.fcproj"
    assert copy.is_file()
    assert project.read_bytes() == original

    entries = read_log(log)
    assert len(entries) == 1
    assert entries[0]["argv"][0] == "run"
    assert entries[0]["argv"][1] == "-"
    assert entries[0]["argv"][entries[0]["argv"].index("--project") + 1] == str(copy)
    assert "--save" in entries[0]["argv"]
    assert "--compact" in entries[0]["argv"]
    lines = log_lines(entries[0])
    assert lines[0]["id"] == "effects.apply"
    assert lines[0]["params"]["clips"] == [29]
    assert lines[1]["id"] == "effects.setParam"
    assert lines[1]["params"]["param"] == "temperature"
    assert len(lines) == 10


def test_existing_lumetri_skips_apply(tmp_path, capsys, monkeypatch):
    stub = write_stub(tmp_path)
    log = tmp_path / "stub.log"
    monkeypatch.setenv("FILMCRAFT_CLI", str(stub))
    monkeypatch.setenv("STUB_LOG", str(log))
    video_a = make_video(tmp_path / "camera 2026-10-20 19-30-00.mp4", "blue")
    video_b = make_video(tmp_path / "camera 2026-10-20 19-40-00.mp4", "green")
    project = write_project(
        tmp_path / "proj.fcproj",
        [
            {"item": 1, "path": video_a, "name": video_a.name, "effects": [{"effect": "lumetri"}]},
            {"item": 2, "path": video_b, "name": video_b.name, "effects": []},
        ],
    )
    rc = colour.main(["--fcproj", str(project), "--json"])
    assert rc == EXIT_OK
    capsys.readouterr()
    lines = log_lines(read_log(log)[0])
    apply_lines = [line for line in lines if line["id"] == "effects.apply"]
    assert len(apply_lines) == 1
    assert apply_lines[0]["params"]["clips"] == [30]


def test_dry_run_does_not_invoke_or_copy(tmp_path, capsys, monkeypatch):
    stub = write_stub(tmp_path)
    log = tmp_path / "stub.log"
    monkeypatch.setenv("FILMCRAFT_CLI", str(stub))
    monkeypatch.setenv("STUB_LOG", str(log))
    video_a = make_video(tmp_path / "camera 2026-10-20 19-30-00.mp4", "blue")
    project = write_project(tmp_path / "proj.fcproj", [{"item": 1, "path": video_a, "name": video_a.name}])

    rc = colour.main(["--fcproj", str(project), "--dry-run", "--json"])
    assert rc == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["dry_run"] is True
    assert summary["sessions"][0]["measured"] is None
    assert len(summary["commands"]) == 5
    assert not log.exists()
    assert not (tmp_path / "proj_colour.fcproj").exists()


def test_dry_run_prints_jsonl(tmp_path, capsys, monkeypatch):
    stub = write_stub(tmp_path)
    monkeypatch.setenv("FILMCRAFT_CLI", str(stub))
    monkeypatch.setenv("STUB_LOG", str(tmp_path / "stub.log"))
    video_a = make_video(tmp_path / "camera 2026-10-20 19-30-00.mp4", "blue")
    project = write_project(tmp_path / "proj.fcproj", [{"item": 1, "path": video_a, "name": video_a.name}])
    rc = colour.main(["--fcproj", str(project), "--dry-run"])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert out.count("effects.setParam") == 4
    assert "effects.apply" in out


def test_in_place_runs_against_input(tmp_path, capsys, monkeypatch):
    stub = write_stub(tmp_path)
    log = tmp_path / "stub.log"
    monkeypatch.setenv("FILMCRAFT_CLI", str(stub))
    monkeypatch.setenv("STUB_LOG", str(log))
    video_a = make_video(tmp_path / "camera 2026-10-20 19-30-00.mp4", "blue")
    project = write_project(tmp_path / "proj.fcproj", [{"item": 1, "path": video_a, "name": video_a.name}])
    original = project.read_bytes()

    rc = colour.main(["--fcproj", str(project), "--in-place", "--json"])
    assert rc == EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["output"] == str(project)
    assert not (tmp_path / "proj_colour.fcproj").exists()
    assert project.read_bytes() == original
    entries = read_log(log)
    assert entries[0]["argv"][entries[0]["argv"].index("--project") + 1] == str(project)


def test_unknown_track(tmp_path, capsys, monkeypatch):
    stub = write_stub(tmp_path)
    monkeypatch.setenv("FILMCRAFT_CLI", str(stub))
    monkeypatch.setenv("STUB_LOG", str(tmp_path / "stub.log"))
    video_a = make_video(tmp_path / "camera 2026-10-20 19-30-00.mp4", "blue")
    project = write_project(tmp_path / "proj.fcproj", [{"item": 1, "path": video_a, "name": video_a.name}])
    rc = colour.main(["--fcproj", str(project), "--track", "Nope", "--json"])
    assert rc == EXIT_USAGE
    err = capsys.readouterr().err
    assert "Nope" in err and "Camera 1" in err


def test_no_enabled_clip(tmp_path, capsys, monkeypatch):
    stub = write_stub(tmp_path)
    monkeypatch.setenv("FILMCRAFT_CLI", str(stub))
    monkeypatch.setenv("STUB_LOG", str(tmp_path / "stub.log"))
    video_a = make_video(tmp_path / "camera 2026-10-20 19-30-00.mp4", "blue")
    project = write_project(
        tmp_path / "proj.fcproj",
        [{"item": 1, "path": video_a, "name": video_a.name, "enabled": False}],
    )
    rc = colour.main(["--fcproj", str(project), "--json"])
    assert rc == EXIT_USAGE
    assert "no enabled clip" in capsys.readouterr().err


def test_no_readable_frame(tmp_path, capsys, monkeypatch):
    stub = write_stub(tmp_path)
    monkeypatch.setenv("FILMCRAFT_CLI", str(stub))
    monkeypatch.setenv("STUB_LOG", str(tmp_path / "stub.log"))
    broken = tmp_path / "camera 2026-10-20 19-30-00.mp4"
    broken.write_text("not a video", encoding="utf-8")
    project = write_project(tmp_path / "proj.fcproj", [{"item": 1, "path": broken, "name": broken.name}])
    rc = colour.main(["--fcproj", str(project), "--json"])
    assert rc == EXIT_USAGE
    assert "no frames could be read" in capsys.readouterr().err


def test_missing_binary(tmp_path, capsys, monkeypatch):
    video_a = make_video(tmp_path / "camera 2026-10-20 19-30-00.mp4", "blue")
    project = write_project(tmp_path / "proj.fcproj", [{"item": 1, "path": video_a, "name": video_a.name}])
    monkeypatch.delenv("FILMCRAFT_CLI", raising=False)
    monkeypatch.setenv("PATH", "")
    rc = colour.main(["--fcproj", str(project), "--json"])
    assert rc == EXIT_RUNTIME
    err = capsys.readouterr().err
    assert "FILMCRAFT_CLI" in err and "filmcraft-cli" in err


def test_failing_stub(tmp_path, capsys, monkeypatch):
    failing = tmp_path / "filmcraft-cli"
    failing.write_text("#!/bin/sh\necho 'boom' >&2\nexit 3\n", encoding="utf-8")
    failing.chmod(failing.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("FILMCRAFT_CLI", str(failing))
    video_a = make_video(tmp_path / "camera 2026-10-20 19-30-00.mp4", "blue")
    project = write_project(tmp_path / "proj.fcproj", [{"item": 1, "path": video_a, "name": video_a.name}])
    rc = colour.main(["--fcproj", str(project), "--json"])
    assert rc == EXIT_RUNTIME
    err = capsys.readouterr().err
    assert "boom" in err and "effects.apply" in err


def test_out_and_in_place_are_mutually_exclusive(tmp_path, capsys):
    rc = colour.main(["--fcproj", str(tmp_path / "proj.fcproj"), "--out", "c.fcproj", "--in-place", "--json"])
    assert rc == EXIT_USAGE
    assert "mutually exclusive" in capsys.readouterr().err


def test_out_equal_to_input(tmp_path, capsys, monkeypatch):
    stub = write_stub(tmp_path)
    monkeypatch.setenv("FILMCRAFT_CLI", str(stub))
    monkeypatch.setenv("STUB_LOG", str(tmp_path / "stub.log"))
    video_a = make_video(tmp_path / "camera 2026-10-20 19-30-00.mp4", "blue")
    project = write_project(tmp_path / "proj.fcproj", [{"item": 1, "path": video_a, "name": video_a.name}])
    rc = colour.main(["--fcproj", str(project), "--out", str(project), "--json"])
    assert rc == EXIT_USAGE
    assert "refusing to overwrite" in capsys.readouterr().err


def test_target_luma_out_of_range_is_usage_error(tmp_path, capsys):
    for value in ("0", "1.5"):
        rc = colour.main(
            ["--fcproj", str(tmp_path / "proj.fcproj"), "--target-luma", value, "--json"]
        )
        assert rc == EXIT_USAGE
        err = capsys.readouterr().err
        assert "target-luma" in err
        assert "Traceback" not in err


def test_target_contrast_out_of_range_is_usage_error(tmp_path, capsys):
    for value in ("0", "2"):
        rc = colour.main(
            ["--fcproj", str(tmp_path / "proj.fcproj"), "--target-contrast", value, "--json"]
        )
        assert rc == EXIT_USAGE
        err = capsys.readouterr().err
        assert "target-contrast" in err
        assert "Traceback" not in err
