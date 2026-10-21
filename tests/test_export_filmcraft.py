import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import export_filmcraft as ef
import subs_from_edit as sfe

FIXTURES = Path(__file__).parent / "fixtures" / "filmcraft"
EDIT = FIXTURES / "edit.json"
PLAN = FIXTURES / "graphics_plan.json"
TICKS = 254016000000


def filmcraft_cli():
    env = os.environ.get("FILMCRAFT_CLI")
    if env:
        return env
    return shutil.which("filmcraft-cli")


needs_filmcraft = pytest.mark.skipif(filmcraft_cli() is None, reason="filmcraft-cli is not installed")


def make_overlay_dir(tmp_path, ids=("g1",)):
    directory = tmp_path / "overlays"
    directory.mkdir(exist_ok=True)
    for entry_id in ids:
        (directory / f"{entry_id}.mov").write_bytes(b"")
    return directory


def run_export(tmp_path, capsys, edit=EDIT, extra=None, out_name="out.fcproj"):
    out = tmp_path / out_name
    argv = [str(edit), "--out", str(out), "--json"] + (extra or [])
    rc = ef.main(argv)
    captured = capsys.readouterr()
    summary = json.loads(captured.out) if captured.out.strip() else None
    return rc, out, summary, captured.err


def sequence_of(project):
    return next(value for value in project["project"]["items"].values() if "Sequence" in value["kind"])


def tracks_of(project):
    block = sequence_of(project)["kind"]["Sequence"]
    return {track["name"]: track for track in block["video_tracks"] + block["audio_tracks"]}


def markers_of(project):
    return sequence_of(project)["kind"]["Sequence"]["markers"]


def test_export_summary(tmp_path, capsys):
    overlays = make_overlay_dir(tmp_path)
    rc, out, summary, err = run_export(tmp_path, capsys, extra=["--graphics-dir", str(overlays)])
    assert rc == 0
    assert summary["route"] == "json"
    assert summary["sequence"] == "Main"
    assert summary["tracks"] == ["Graphics 1", "Camera 1", "Screen 1", "Audio 1"]
    assert summary["slots_placed"] == 2
    assert summary["photos_placed"] == 1
    assert summary["overlays_placed"] == 1
    assert summary["markers"] == {"green": 3, "rose": 0, "mango": 1}
    assert summary["llm_cost_usd"] == 0.0
    assert summary["wall_time_s"] >= 0.0
    assert summary["output"] == str(out)
    assert len(summary["missing_media"]) == 3


def test_export_envelope_and_layout(tmp_path, capsys):
    overlays = make_overlay_dir(tmp_path)
    rc, out, summary, err = run_export(tmp_path, capsys, extra=["--graphics-dir", str(overlays)])
    assert rc == 0
    project = json.loads(out.read_text(encoding="utf-8"))
    assert project["format"] == "filmcraft.project"
    assert project["schema_version"] == ef.SCHEMA_VERSION
    assert project["view"]["active_sequence"] == sequence_of(project)["id"]
    tracks = tracks_of(project)
    assert [item["name"] for item in tracks["Camera 1"]["items"]] == ["camera A.mp4", "pic.jpg"]
    assert [item["name"] for item in tracks["Screen 1"]["items"]] == ["screen B.mp4"]
    assert [item["name"] for item in tracks["Audio 1"]["items"]] == ["camera A.mp4", "screen B.mp4"]
    assert [item["name"] for item in tracks["Graphics 1"]["items"]] == ["g1.mov"]
    camera = tracks["Camera 1"]["items"]
    assert camera[0]["start"] == 0 and camera[0]["duration"] == 2 * TICKS
    assert camera[0]["source_in"] == 1 * TICKS
    assert camera[1]["start"] == 2 * TICKS and camera[1]["duration"] == 3 * TICKS
    screen = tracks["Screen 1"]["items"][0]
    assert screen["start"] == 5 * TICKS and screen["source_in"] == 2 * TICKS
    overlay = tracks["Graphics 1"]["items"][0]
    assert overlay["start"] == 0 and overlay["duration"] == 2 * TICKS


def test_markers_carry_reasons(tmp_path, capsys):
    rc, out, summary, err = run_export(tmp_path, capsys)
    assert rc == 0
    markers = markers_of(json.loads(out.read_text(encoding="utf-8")))
    green = [marker for marker in markers if marker["color"] == "Green"]
    assert [marker["name"] for marker in green] == ["0071/s2", "0072/p1", "0073/s1"]
    assert green[0]["comment"] == "clean opening hold"
    assert green[0]["start"] == 0
    assert green[1]["start"] == 2 * TICKS
    assert green[2]["start"] == 5 * TICKS
    assert all(marker["kind"] == "Comment" and marker["duration"] == 0 for marker in markers)


def test_photo_item_is_still(tmp_path, capsys):
    rc, out, summary, err = run_export(tmp_path, capsys)
    assert rc == 0
    items = json.loads(out.read_text(encoding="utf-8"))["project"]["items"]
    photo = next(value for value in items.values() if value["name"] == "pic.jpg")
    assert photo["kind"]["Media"]["info"]["kind"] == "Still"
    assert photo["kind"]["Media"]["info"]["container"] == "Image"
    assert any("Ken Burns" in warning for warning in summary["warnings"])


def test_missing_media_is_a_warning_not_an_error(tmp_path, capsys):
    rc, out, summary, err = run_export(tmp_path, capsys)
    assert rc == 0
    assert out.is_file()
    assert summary["missing_media"] == [
        "/footage/camera/camera A.mp4",
        "/footage/screen/screen B.mp4",
        "/footage/photos/pic.jpg",
    ]
    assert any("missing media" in warning for warning in summary["warnings"])


def test_round_trip_words_and_srt_equal(tmp_path, capsys):
    rc, out, summary, err = run_export(tmp_path, capsys)
    assert rc == 0
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    rc_a = sfe.main(["--from-edit", str(EDIT), "--words-dir", str(FIXTURES), "--out-dir", str(out_a), "--json"])
    rc_b = sfe.main(["--fcproj", str(out), "--sequence", "Main", "--words-dir", str(FIXTURES), "--out-dir", str(out_b), "--json"])
    assert rc_a == 0 and rc_b == 0
    words_a = json.loads((out_a / "edit_words.json").read_text(encoding="utf-8"))
    words_b = json.loads((out_b / "Main_words.json").read_text(encoding="utf-8"))
    assert words_a == words_b
    assert (out_a / "edit_RO.srt").read_text(encoding="utf-8") == (out_b / "Main_RO.srt").read_text(encoding="utf-8")


def test_speed_ramp_warns_and_marks_rose(tmp_path, capsys):
    edit = json.loads(EDIT.read_text(encoding="utf-8"))
    edit["video"][0]["speed"] = 1.2
    path = tmp_path / "edit.json"
    path.write_text(json.dumps(edit), encoding="utf-8")
    rc, out, summary, err = run_export(tmp_path, capsys, edit=path)
    assert rc == 0
    assert summary["markers"] == {"green": 2, "rose": 1, "mango": 0}
    assert any("speed 1.2" in warning for warning in summary["warnings"])
    rose = [marker for marker in markers_of(json.loads(out.read_text(encoding="utf-8"))) if marker["color"] == "Rose"]
    assert rose[0]["name"] == "redo 0071/s2"
    assert rose[0]["comment"] == "speed 1.2"


def test_centred_slot_marks_rose(tmp_path, capsys):
    edit = json.loads(EDIT.read_text(encoding="utf-8"))
    edit["video"][0]["centred"] = True
    path = tmp_path / "edit.json"
    path.write_text(json.dumps(edit), encoding="utf-8")
    rc, out, summary, err = run_export(tmp_path, capsys, edit=path)
    assert rc == 0
    rose = [marker for marker in markers_of(json.loads(out.read_text(encoding="utf-8"))) if marker["color"] == "Rose"]
    assert rose[0]["name"] == "redo 0071/s2"
    assert rose[0]["comment"] == "centred cut"


def test_graphics_plan_places_approved_only(tmp_path, capsys):
    overlays = make_overlay_dir(tmp_path, ids=("g1", "g2"))
    rc, out, summary, err = run_export(
        tmp_path, capsys, extra=["--graphics-plan", str(PLAN), "--graphics-dir", str(overlays)]
    )
    assert rc == 0
    assert summary["overlays_placed"] == 1
    assert summary["markers"]["mango"] == 1
    tracks = tracks_of(json.loads(out.read_text(encoding="utf-8")))
    assert [item["name"] for item in tracks["Graphics 1"]["items"]] == ["g1.mov"]
    mango = [marker for marker in markers_of(json.loads(out.read_text(encoding="utf-8"))) if marker["color"] == "Mango"]
    assert mango[0]["name"] == "lower_third g1"
    assert mango[0]["comment"] == "introduce the host"
    assert mango[0]["start"] == int(round(0.5 * TICKS))


def test_missing_overlay_mov_is_skipped(tmp_path, capsys):
    overlays = tmp_path / "overlays"
    overlays.mkdir()
    rc, out, summary, err = run_export(tmp_path, capsys, extra=["--graphics-dir", str(overlays)])
    assert rc == 0
    assert summary["overlays_placed"] == 0
    assert any("g1.mov" in warning for warning in summary["warnings"])


def test_overlay_without_graphics_dir_is_skipped(tmp_path, capsys):
    rc, out, summary, err = run_export(tmp_path, capsys)
    assert rc == 0
    assert summary["overlays_placed"] == 0
    assert any("no --graphics-dir" in warning for warning in summary["warnings"])


def test_screen_override_moves_a_source(tmp_path, capsys):
    rc, out, summary, err = run_export(tmp_path, capsys, extra=["--screen", "camera A.mp4"])
    assert rc == 0
    tracks = tracks_of(json.loads(out.read_text(encoding="utf-8")))
    assert [item["name"] for item in tracks["Screen 1"]["items"]] == ["camera A.mp4", "screen B.mp4"]
    assert [item["name"] for item in tracks["Camera 1"]["items"]] == ["pic.jpg"]


def test_sequence_settings_flags(tmp_path, capsys):
    rc, out, summary, err = run_export(
        tmp_path, capsys, extra=["--sequence", "Cut", "--width", "1280", "--height", "720", "--fps", "30"]
    )
    assert rc == 0
    sequence = sequence_of(json.loads(out.read_text(encoding="utf-8")))
    assert sequence["name"] == "Cut"
    settings = sequence["kind"]["Sequence"]["settings"]
    assert settings["width"] == 1280 and settings["height"] == 720
    assert settings["frame_rate"] == {"num": 30, "den": 1}


def test_rerun_replaces_output(tmp_path, capsys):
    rc1, out, summary1, err1 = run_export(tmp_path, capsys)
    first = out.read_text(encoding="utf-8")
    rc2, out2, summary2, err2 = run_export(tmp_path, capsys)
    assert rc1 == 0 and rc2 == 0
    assert out.read_text(encoding="utf-8") == first


def test_missing_edit_is_exit_2(tmp_path, capsys):
    rc = ef.main([str(tmp_path / "nope.json"), "--out", str(tmp_path / "o.fcproj"), "--json"])
    assert rc == 2
    assert "not found" in capsys.readouterr().err


def test_invalid_edit_is_exit_2(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version": "0.1"}', encoding="utf-8")
    rc = ef.main([str(bad), "--out", str(tmp_path / "o.fcproj"), "--json"])
    assert rc == 2
    err = capsys.readouterr().err
    assert str(bad) in err


def test_out_refuses_a_source_file(tmp_path, capsys):
    rc = ef.main([str(EDIT), "--out", "/footage/camera/camera A.mp4", "--json"])
    assert rc == 2
    assert "overwrite a source" in capsys.readouterr().err


def test_out_refuses_a_directory(tmp_path, capsys):
    rc = ef.main([str(EDIT), "--out", str(tmp_path), "--json"])
    assert rc == 2
    assert "directory" in capsys.readouterr().err


@needs_filmcraft
def test_headless_open(tmp_path, capsys):
    overlays = make_overlay_dir(tmp_path)
    rc, out, summary, err = run_export(tmp_path, capsys, extra=["--graphics-dir", str(overlays)])
    assert rc == 0
    project = subprocess.run(
        [filmcraft_cli(), "inspect", "project", "--project", str(out)],
        capture_output=True, text=True,
    )
    assert project.stdout.strip().startswith("{"), project.stdout + project.stderr
    data = json.loads(project.stdout)
    assert data["name"] == "Main"
    assert data["activeSequence"] is not None
    sequence = subprocess.run(
        [filmcraft_cli(), "inspect", "sequence", "--project", str(out)],
        capture_output=True, text=True,
    )
    assert sequence.stdout.strip().startswith("{"), sequence.stdout + sequence.stderr
    block = json.loads(sequence.stdout)
    assert [track["name"] for track in block["video"]] == ["Graphics 1", "Camera 1", "Screen 1"]
    assert [track["name"] for track in block["audio"]] == ["Audio 1"]
    assert [marker["color"] for marker in block["markers"]] == ["Green", "Green", "Green", "Mango"]
