import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

import mode_b
from fs_probe import safe_is_dir

MOCK_CONFIG = Path(__file__).parent / "fixtures" / "mock_llm_config.yaml"
REFERENCE_DIR = Path("/root/media/eval/second-brain")
REFERENCE_FCPROJ = "Second Brain.fcproj"
REFERENCE_MEDIA = re.compile(r'"([^"]+\.(?:mp4|mov|mkv|m4v))"', re.IGNORECASE)


@pytest.fixture(autouse=True)
def mock_llm_config(monkeypatch):
    monkeypatch.setenv("YT_CONFIG", str(MOCK_CONFIG))


def make_camera(folder, name):
    path = folder / name
    path.write_bytes(b"")
    return path


def make_words(path, text="Bună ziua."):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "id": 0,
                        "start": 0.0,
                        "end": 1.0,
                        "text": text,
                        "words": [{"word": text, "start": 0.0, "end": 1.0}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def run_main(argv, capsys):
    code = mode_b.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_scan_pairs_camera_and_screen(tmp_path):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    make_camera(folder, "2026-10-17 22-25-44.mp4")
    warnings = []
    sessions = mode_b.scan_sessions(folder, tmp_path / "out", warnings)
    assert len(sessions) == 1
    assert sessions[0]["screen"].name == "2026-10-17 22-25-44.mp4"
    assert warnings == []


def test_scan_no_screen(tmp_path):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    warnings = []
    sessions = mode_b.scan_sessions(folder, tmp_path / "out", warnings)
    assert sessions[0]["screen"] is None
    assert warnings == []


def test_scan_several_screens_warns_and_takes_first(tmp_path):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    make_camera(folder, "2026-10-17 22-25-44.mp4")
    make_camera(folder, "2026-10-17 22-25-44.mov")
    warnings = []
    sessions = mode_b.scan_sessions(folder, tmp_path / "out", warnings)
    assert sessions[0]["screen"].name == "2026-10-17 22-25-44.mov"
    assert any("several screen files" in warning for warning in warnings)


def test_scan_ignores_non_camera_files(tmp_path):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    make_camera(folder, "notes.mp4")
    make_camera(folder, "camera 2026-10-17 22-25-44.txt")
    sessions = mode_b.scan_sessions(folder, tmp_path / "out", [])
    assert [session["stem"] for session in sessions] == ["camera 2026-10-17 22-25-44"]


def test_scan_sorts_chronologically_unstamped_last(tmp_path):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-18 09-00-00.mp4")
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    make_camera(folder, "camera no-stamp.mp4")
    sessions = mode_b.scan_sessions(folder, tmp_path / "out", [])
    assert [session["stem"] for session in sessions] == [
        "camera 2026-10-17 22-25-44",
        "camera 2026-10-18 09-00-00",
        "camera no-stamp",
    ]


def test_flat_layout_ignores_untimestamped_videos(tmp_path, capsys):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-09-29 20-58-37.mp4")
    make_camera(folder, "2026-09-29 20-58-35.mp4")
    make_camera(folder, "notes.mp4")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    summary = json.loads(out)
    assert summary["sessions"] == 1
    assert summary["pairing"] == {"paired": 1, "camera_only": 0, "screen_only": 0}
    assert not any("unpaired screen file" in warning for warning in summary["warnings"])
    assert "notes.mp4" not in out


def test_find_words_in_folder(tmp_path):
    folder = tmp_path / "obs"
    folder.mkdir()
    words_dir = tmp_path / "out" / "words"
    stem = "camera 2026-10-17 22-25-44"
    make_words(folder / f"{stem}.json")
    assert mode_b.find_words(folder, words_dir, stem) == folder / f"{stem}.json"


def test_find_words_suffix_in_folder(tmp_path):
    folder = tmp_path / "obs"
    folder.mkdir()
    words_dir = tmp_path / "out" / "words"
    stem = "camera 2026-10-17 22-25-44"
    make_words(folder / f"{stem}_words.json")
    assert mode_b.find_words(folder, words_dir, stem) == folder / f"{stem}_words.json"


def test_find_words_in_out_words_dir(tmp_path):
    folder = tmp_path / "obs"
    folder.mkdir()
    words_dir = tmp_path / "out" / "words"
    stem = "camera 2026-10-17 22-25-44"
    make_words(words_dir / f"{stem}_words.json")
    assert mode_b.find_words(folder, words_dir, stem) == words_dir / f"{stem}_words.json"


def test_find_words_missing(tmp_path):
    folder = tmp_path / "obs"
    folder.mkdir()
    assert mode_b.find_words(folder, tmp_path / "out" / "words", "camera x") is None


def test_find_words_prefers_words_suffix_and_folder_order(tmp_path):
    folder = tmp_path / "obs"
    folder.mkdir()
    words_dir = tmp_path / "out" / "words"
    stem = "camera 2026-10-17 22-25-44"
    make_words(folder / f"{stem}.json")
    make_words(folder / f"{stem}_words.json")
    make_words(words_dir / f"{stem}_words.json")
    assert mode_b.find_words(folder, words_dir, stem) == folder / f"{stem}_words.json"


def test_end_to_end_mock(tmp_path, capsys):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    make_camera(folder, "2026-10-17 22-25-44.mp4")
    make_camera(folder, "camera 2026-10-18 09-00-00.mp4")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    summary = json.loads(out)
    assert list(summary.keys())[0] == "ok"
    assert summary["ok"] is True
    assert summary["sessions"] == 2
    assert summary["transcribed"] == 2
    assert summary["skipped"] == {"transcribe": 0, "rough_cut": False, "chapters": 0, "export": False}
    assert isinstance(summary["wall_time_s"], float)
    assert summary["llm_cost_usd"] == 0.0
    out_dir = folder / "mode_b"
    assert (out_dir / "words" / "camera 2026-10-17 22-25-44_words.json").is_file()
    assert (out_dir / "sessions.yaml").is_file()
    assert (out_dir / "edit.json").is_file()
    assert (out_dir / "project.fcproj").is_file()
    assert summary["outputs"]["project"] == str(out_dir / "project.fcproj")


def test_manifest_content(tmp_path, capsys):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    make_camera(folder, "2026-10-17 22-25-44.mp4")
    make_camera(folder, "camera 2026-10-18 09-00-00.mp4")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    manifest = yaml.safe_load((folder / "mode_b" / "sessions.yaml").read_text(encoding="utf-8"))
    entries = manifest["sessions"]
    assert len(entries) == 2
    first, second = entries
    assert first["camera"] == str((folder / "camera 2026-10-17 22-25-44.mp4").resolve())
    assert first["screen"] == str((folder / "2026-10-17 22-25-44.mp4").resolve())
    assert first["words"] == str(
        (folder / "mode_b" / "words" / "camera 2026-10-17 22-25-44_words.json").resolve()
    )
    assert "screen" not in second
    assert second["camera"] == str((folder / "camera 2026-10-18 09-00-00.mp4").resolve())
    assert all(Path(entry["camera"]).is_absolute() for entry in entries)
    assert all(Path(entry["words"]).is_absolute() for entry in entries)
    assert all("sync_offset_s" not in entry for entry in entries)


def test_second_run_skips_everything(tmp_path, capsys):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    make_camera(folder, "2026-10-17 22-25-44.mp4")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    summary = json.loads(out)
    assert summary["transcribed"] == 0
    assert summary["skipped"] == {"transcribe": 1, "rough_cut": True, "chapters": 1, "export": True}


def test_force_reruns(tmp_path, capsys):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    code, out, err = run_main([str(folder), "--backend", "mock", "--force", "--json"], capsys)
    assert code == 0
    summary = json.loads(out)
    assert summary["transcribed"] == 1
    assert summary["skipped"] == {"transcribe": 0, "rough_cut": False, "chapters": 0, "export": False}


def test_transcribe_only_missing_words(tmp_path, capsys):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    make_camera(folder, "camera 2026-10-18 09-00-00.mp4")
    source = make_words(folder / "camera 2026-10-17 22-25-44_words.json")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    summary = json.loads(out)
    assert summary["transcribed"] == 1
    assert summary["skipped"]["transcribe"] == 1
    words_dir = folder / "mode_b" / "words"
    found_copy = words_dir / "camera 2026-10-17 22-25-44_words.json"
    assert found_copy.is_file()
    assert (words_dir / "camera 2026-10-18 09-00-00_words.json").is_file()
    assert found_copy.read_bytes() == source.read_bytes()


def test_mixed_found_and_transcribed_words_all_visible(tmp_path, capsys):
    folder = tmp_path / "obs"
    camera_dir, screen_dir = make_subfolders(folder)
    make_camera(camera_dir, "camera 2026-09-29 20-58-37.mp4")
    make_camera(camera_dir, "camera 2026-09-29 21-00-00.mp4")
    make_words(folder / "json" / "camera 2026-09-29 20-58-37.json")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    summary = json.loads(out)
    assert not any("no words file for slot" in warning for warning in summary["warnings"])
    words_dir = folder / "mode_b" / "words"
    assert (words_dir / "camera 2026-09-29 20-58-37.json").is_file()
    assert (words_dir / "camera 2026-09-29 21-00-00_words.json").is_file()


def test_printed_project_path(tmp_path, capsys):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    code, out, err = run_main([str(folder), "--backend", "mock"], capsys)
    assert code == 0
    lines = [line for line in out.splitlines() if line.strip()]
    assert f"project: {folder / 'mode_b' / 'project.fcproj'}" in lines
    assert any(line.startswith("[warn]") for line in lines)


def test_missing_folder_exit_2(tmp_path, capsys):
    code, out, err = run_main([str(tmp_path / "nope"), "--json"], capsys)
    assert code == 2
    assert "[error]" in err
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "folder not found" in payload["error"]


def test_no_camera_files_exit_2(tmp_path, capsys):
    folder = tmp_path / "obs"
    folder.mkdir()
    (folder / "notes.mp4").write_bytes(b"")
    code, out, err = run_main([str(folder), "--json"], capsys)
    assert code == 2
    assert "no camera files" in err


def test_failing_step_exit_2(tmp_path, capsys):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    (folder / "camera 2026-10-17 22-25-44_words.json").write_text("not json", encoding="utf-8")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 2
    assert "[error]" in err
    assert "rough_cut failed" in err
    assert json.loads(out)["ok"] is False


def test_chapters_step_runs_and_writes_file(tmp_path, capsys):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    summary = json.loads(out)
    out_dir = folder / "mode_b"
    assert (out_dir / "chapters.json").is_file()
    assert summary["outputs"]["chapters"] == str(out_dir / "chapters.json")
    assert summary["skipped"]["chapters"] == 0


def test_second_run_skips_chapters(tmp_path, capsys):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    summary = json.loads(out)
    assert summary["skipped"]["chapters"] == 1


def test_force_reruns_chapters(tmp_path, capsys):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    code, out, err = run_main([str(folder), "--backend", "mock", "--force", "--json"], capsys)
    assert code == 0
    summary = json.loads(out)
    assert summary["skipped"]["chapters"] == 0


def test_project_receives_chapter_markers(tmp_path, capsys):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    project = json.loads((folder / "mode_b" / "project.fcproj").read_text(encoding="utf-8"))
    sequence = next(
        value for value in project["project"]["items"].values() if "Sequence" in value["kind"]
    )
    markers = sequence["kind"]["Sequence"]["markers"]
    sections = [marker for marker in markers if marker["name"] == "Intro"]
    assert len(sections) == 1
    assert sections[0]["color"] == "Green"
    assert sections[0]["comment"] == "Start."


def test_failing_chapters_step_exit_2(tmp_path, capsys, monkeypatch):
    config = {
        "models": {"metadata": "mock-model"},
        "llm_budget_usd": None,
        "llm_backend": "mock",
        "llm_mock_text": {"metadata": "not json"},
    }
    config_path = tmp_path / "bad_config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setenv("YT_CONFIG", str(config_path))
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 2
    assert "[error]" in err
    assert "chapters failed" in err
    assert json.loads(out)["ok"] is False


def make_subfolders(folder, camera_dir="camera", screen_dir="ecran"):
    (folder / camera_dir).mkdir(parents=True)
    (folder / screen_dir).mkdir()
    return folder / camera_dir, folder / screen_dir


def test_subfolder_layout_pairs_nearest_without_written_offset(tmp_path, capsys):
    folder = tmp_path / "obs"
    camera_dir, screen_dir = make_subfolders(folder)
    make_camera(camera_dir, "camera 2026-09-29 20-58-37.mp4")
    make_camera(screen_dir, "2026-09-29 20-58-35.mp4")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    manifest = yaml.safe_load((folder / "mode_b" / "sessions.yaml").read_text(encoding="utf-8"))
    entry = manifest["sessions"][0]
    assert entry["screen"].endswith("2026-09-29 20-58-35.mp4")
    assert "sync_offset_s" not in entry


def test_screen_folder_accepted_when_ecran_absent(tmp_path):
    folder = tmp_path / "obs"
    camera_dir, screen_dir = make_subfolders(folder, screen_dir="screen")
    make_camera(camera_dir, "camera 2026-09-29 20-58-37.mp4")
    make_camera(screen_dir, "2026-09-29 20-58-35.mp4")
    warnings = []
    sessions = mode_b.scan_sessions(folder, tmp_path / "out", warnings)
    assert sessions[0]["screen"].name == "2026-09-29 20-58-35.mp4"
    assert warnings == []


def test_subfolder_camera_without_screen_is_camera_only(tmp_path):
    folder = tmp_path / "obs"
    camera_dir, screen_dir = make_subfolders(folder)
    make_camera(camera_dir, "camera 2026-09-29 20-58-37.mp4")
    warnings = []
    sessions = mode_b.scan_sessions(folder, tmp_path / "out", warnings)
    assert sessions[0]["screen"] is None
    assert warnings == []


def test_orphan_screen_warns_and_is_not_a_session(tmp_path):
    folder = tmp_path / "obs"
    camera_dir, screen_dir = make_subfolders(folder)
    make_camera(camera_dir, "camera 2026-09-29 20-58-37.mp4")
    make_camera(screen_dir, "2026-09-29 21-30-00.mp4")
    warnings = []
    sessions = mode_b.scan_sessions(folder, tmp_path / "out", warnings)
    assert len(sessions) == 1
    assert sessions[0]["screen"] is None
    assert any("unpaired screen file" in warning for warning in warnings)


def test_greedy_one_to_one_nearest_takes_the_screen(tmp_path):
    folder = tmp_path / "obs"
    camera_dir, screen_dir = make_subfolders(folder)
    make_camera(camera_dir, "camera 2026-09-29 20-58-37.mp4")
    make_camera(camera_dir, "camera 2026-09-29 20-58-40.mp4")
    make_camera(screen_dir, "2026-09-29 20-58-35.mp4")
    warnings = []
    sessions = mode_b.scan_sessions(folder, tmp_path / "out", warnings)
    by_stem = {session["stem"]: session for session in sessions}
    assert by_stem["camera 2026-09-29 20-58-37"]["screen"] is not None
    assert by_stem["camera 2026-09-29 20-58-40"]["screen"] is None


def test_config_changes_folder_names_and_tolerance(tmp_path):
    folder = tmp_path / "obs"
    camera_dir, screen_dir = make_subfolders(folder, camera_dir="cam", screen_dir="scr")
    make_camera(camera_dir, "camera 2026-09-29 20-58-37.mp4")
    make_camera(screen_dir, "2026-09-29 20-58-22.mp4")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {"mode_b": {"camera_dir": "cam", "screen_dir": "scr", "pair_tolerance_s": 10.0}}
        ),
        encoding="utf-8",
    )
    sessions = mode_b.scan_sessions(
        folder, tmp_path / "out", [], mode_b.load_mode_b_config(config_path)
    )
    assert sessions[0]["screen"] is None
    config_path.write_text(
        yaml.safe_dump(
            {"mode_b": {"camera_dir": "cam", "screen_dir": "scr", "pair_tolerance_s": 20.0}}
        ),
        encoding="utf-8",
    )
    sessions = mode_b.scan_sessions(
        folder, tmp_path / "out", [], mode_b.load_mode_b_config(config_path)
    )
    assert sessions[0]["screen"] is not None


def test_non_mapping_mode_b_config_is_user_error(tmp_path, capsys):
    folder = tmp_path / "obs"
    camera_dir, screen_dir = make_subfolders(folder)
    make_camera(camera_dir, "camera 2026-09-29 20-58-37.mp4")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"mode_b": "camera"}), encoding="utf-8")
    code, out, err = run_main(
        [str(folder), "--backend", "mock", "--config", str(config_path), "--json"], capsys
    )
    assert code == 2
    assert "mode_b must be a mapping" in err


def test_words_in_json_subfolder_skip_transcribe(tmp_path, capsys):
    folder = tmp_path / "obs"
    camera_dir, screen_dir = make_subfolders(folder)
    make_camera(camera_dir, "camera 2026-09-29 20-58-37.mp4")
    make_words(folder / "json" / "camera 2026-09-29 20-58-37.json")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    summary = json.loads(out)
    assert summary["transcribed"] == 0
    assert summary["skipped"]["transcribe"] == 1
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    summary = json.loads(out)
    assert summary["skipped"]["transcribe"] == 1


def test_matte_exports_are_never_sessions(tmp_path):
    folder = tmp_path / "obs"
    camera_dir, screen_dir = make_subfolders(folder)
    make_camera(camera_dir, "camera 2026-09-29 20-58-37.mp4")
    make_camera(camera_dir, "camera 2026-09-29 20-58-37-Claude-RVM-Alpha.mov")
    make_camera(camera_dir, "camera 2026-09-29 20-58-37_matte.mov")
    sessions = mode_b.scan_sessions(folder, tmp_path / "out", [])
    assert [session["stem"] for session in sessions] == ["camera 2026-09-29 20-58-37"]


def test_json_reports_pairing_counts(tmp_path, capsys):
    folder = tmp_path / "obs"
    camera_dir, screen_dir = make_subfolders(folder)
    make_camera(camera_dir, "camera 2026-09-29 20-58-37.mp4")
    make_camera(camera_dir, "camera 2026-09-29 21-00-00.mp4")
    make_camera(screen_dir, "2026-09-29 20-58-35.mp4")
    make_camera(screen_dir, "2026-09-29 22-00-00.mp4")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    summary = json.loads(out)
    assert summary["pairing"] == {"paired": 1, "camera_only": 1, "screen_only": 1}


def test_fixture_40_camera_32_screen(tmp_path, capsys):
    folder = tmp_path / "obs"
    camera_dir, screen_dir = make_subfolders(folder)
    base = datetime(2026, 9, 29, 20, 0, 0)
    for index in range(40):
        stamp = base + timedelta(minutes=index)
        make_camera(camera_dir, f"camera {stamp:%Y-%m-%d %H-%M-%S}.mp4")
    for index in range(31):
        stamp = base + timedelta(minutes=index, seconds=-1)
        make_camera(screen_dir, f"{stamp:%Y-%m-%d %H-%M-%S}.mp4")
    orphan = base + timedelta(minutes=100)
    make_camera(screen_dir, f"{orphan:%Y-%m-%d %H-%M-%S}.mp4")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    summary = json.loads(out)
    assert summary["sessions"] == 40
    assert summary["pairing"] == {"paired": 31, "camera_only": 9, "screen_only": 1}
    assert any("unpaired screen file" in warning for warning in summary["warnings"])


def reference_camera_stems():
    json_dir = REFERENCE_DIR / "json"
    return sorted(
        path.stem
        for path in json_dir.glob("*.json")
        if not path.name.startswith("_")
    )


def reference_screen_names():
    text = (REFERENCE_DIR / REFERENCE_FCPROJ).read_text(encoding="utf-8")
    names = set()
    for match in REFERENCE_MEDIA.finditer(text):
        name = Path(match.group(1)).name
        if not name.startswith("camera "):
            names.add(name)
    return sorted(names)


@pytest.mark.skipif(not safe_is_dir(REFERENCE_DIR), reason="reference recordings not present")
def test_reference_names_pairing(tmp_path, capsys):
    folder = tmp_path / "second-brain"
    camera_dir, screen_dir = make_subfolders(folder)
    for stem in reference_camera_stems():
        (camera_dir / f"{stem}.mp4").write_bytes(b"")
    for name in reference_screen_names():
        (screen_dir / name).write_bytes(b"")
    code, out, err = run_main(
        [
            str(folder),
            "--out-dir",
            str(tmp_path / "out"),
            "--backend",
            "mock",
            "--config",
            str(MOCK_CONFIG),
            "--json",
        ],
        capsys,
    )
    assert code == 0
    summary = json.loads(out)
    assert summary["sessions"] == 31
    assert summary["pairing"] == {"paired": 27, "camera_only": 4, "screen_only": 0}
    manifest = yaml.safe_load((tmp_path / "out" / "sessions.yaml").read_text(encoding="utf-8"))
    assert all("sync_offset_s" not in entry for entry in manifest["sessions"])
    stems = {Path(entry["camera"]).stem for entry in manifest["sessions"]}
    assert "camera 2026-09-29 20-58-37" in stems
    assert "camera 2026-10-03 21-46-01" in stems
    assert "camera 2026-10-17 23-05-15" in stems


FOURCC = cv2.VideoWriter_fourcc(*"mp4v")


def write_video(path, frames, fps=10, size=(640, 360)):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), FOURCC, fps, size)
    if not writer.isOpened():
        pytest.skip("OpenCV mp4v VideoWriter is unavailable on this build")
    for frame in frames:
        writer.write(frame)
    writer.release()
    return path


def rect_frames(count=30, size=(640, 360)):
    frames = []
    for index in range(count):
        frame = np.full((size[1], size[0], 3), 20, dtype=np.uint8)
        x = 40 + index * 6
        cv2.rectangle(frame, (x, 100), (x + 80, 220), (220, 220, 220), -1)
        frames.append(frame)
    return frames


def noise_frames(count=30, size=(640, 360), seed=5):
    rng = np.random.default_rng(seed)
    return [rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8) for _ in range(count)]


class SequenceDetector:
    """Face detector stub: returns the next canned box list on every call."""

    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.index = 0

    def setInputSize(self, size):
        return None

    def detect(self, frame):
        boxes = self.sequence[min(self.index, len(self.sequence) - 1)]
        self.index += 1
        if not boxes:
            return None, None
        faces = np.array(
            [[x, y, w, h, score] for (x, y, w, h, score) in boxes], dtype=np.float32
        )
        return None, faces


def install_detector(monkeypatch, sequence):
    detector = SequenceDetector(sequence)
    monkeypatch.setattr(mode_b, "make_detector", lambda path: detector)
    monkeypatch.setattr(mode_b, "ensure_model", lambda path, url=None: Path(path))
    return detector


def test_check_warns_when_screen_shows_a_face(tmp_path, capsys, monkeypatch):
    folder = tmp_path / "obs"
    camera_dir, screen_dir = make_subfolders(folder)
    make_camera(camera_dir, "camera 2026-09-29 20-58-37.mp4")
    write_video(screen_dir / "2026-09-29 20-58-35.mp4", rect_frames())
    install_detector(monkeypatch, [[(100.0, 100.0, 80.0, 80.0, 0.9)]] * 8)
    code, out, err = run_main([str(folder), "--check", "--json"], capsys)
    assert code == 1
    summary = json.loads(out)
    assert summary["ok"] is False
    assert summary["check"] is True
    assert summary["llm_cost_usd"] == 0.0
    suspect = summary["screen_suspects"][0]
    assert suspect["reason"] == "face_in_most_frames"
    assert "CAMERA recording" in summary["warnings"][0]
    assert not (folder / "mode_b").exists()


def test_check_warns_when_screen_matches_the_camera(tmp_path, capsys, monkeypatch):
    folder = tmp_path / "obs"
    camera_dir, screen_dir = make_subfolders(folder)
    frames = rect_frames()
    write_video(camera_dir / "camera 2026-09-29 20-58-37.mp4", frames)
    write_video(screen_dir / "2026-09-29 20-58-35.mp4", frames)
    install_detector(monkeypatch, [[]] * 8)
    code, out, err = run_main([str(folder), "--check", "--json"], capsys)
    assert code == 1
    summary = json.loads(out)
    suspect = summary["screen_suspects"][0]
    assert suspect["reason"] == "similar_to_camera"
    assert suspect["camera"] == "camera 2026-09-29 20-58-37.mp4"
    assert "CAMERA recording" in summary["warnings"][0]


def test_check_clean_screen_exits_zero(tmp_path, capsys, monkeypatch):
    folder = tmp_path / "obs"
    camera_dir, screen_dir = make_subfolders(folder)
    write_video(camera_dir / "camera 2026-09-29 20-58-37.mp4", noise_frames(seed=2))
    write_video(screen_dir / "2026-09-29 20-58-35.mp4", rect_frames())
    install_detector(monkeypatch, [[]] * 8)
    code, out, err = run_main([str(folder), "--check", "--json"], capsys)
    assert code == 0
    summary = json.loads(out)
    assert summary["ok"] is True
    assert summary["screen_suspects"] == []
    assert summary["warnings"] == []


def test_full_run_warns_on_camera_like_screen(tmp_path, capsys, monkeypatch):
    folder = tmp_path / "obs"
    camera_dir, screen_dir = make_subfolders(folder)
    make_camera(camera_dir, "camera 2026-09-29 20-58-37.mp4")
    write_video(screen_dir / "2026-09-29 20-58-35.mp4", rect_frames())
    make_words(folder / "json" / "camera 2026-09-29 20-58-37.json")
    install_detector(monkeypatch, [[(100.0, 100.0, 80.0, 80.0, 0.9)]] * 8)
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    summary = json.loads(out)
    assert summary["screen_suspects"][0]["reason"] == "face_in_most_frames"
    assert any("CAMERA recording" in warning for warning in summary["warnings"])


def test_manifest_rewrite_keeps_recorded_sync_offset(tmp_path):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    make_camera(folder, "2026-10-17 22-25-44.mp4")
    sessions = mode_b.scan_sessions(folder, tmp_path / "out", [])
    sessions[0]["words"] = make_words(
        tmp_path / "out" / "words" / "camera 2026-10-17 22-25-44_words.json"
    )
    sessions_path = tmp_path / "out" / "sessions.yaml"
    sessions_path.parent.mkdir(parents=True, exist_ok=True)
    sessions_path.write_text(
        yaml.safe_dump(
            {
                "sessions": [
                    {
                        "camera": str(sessions[0]["camera"].resolve()),
                        "sync_offset_s": -1.25,
                        "sync_offset_confidence": 0.8,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    mode_b.write_manifest(sessions_path, sessions, mode_b.load_manifest(sessions_path))
    entry = yaml.safe_load(sessions_path.read_text(encoding="utf-8"))["sessions"][0]
    assert entry["sync_offset_s"] == -1.25
    assert entry["sync_offset_confidence"] == 0.8
    assert entry["screen"] == str((folder / "2026-10-17 22-25-44.mp4").resolve())
    assert entry["words"]


def test_manifest_rewrite_keeps_offset_for_relative_camera(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "obs"
    folder.mkdir()
    camera = folder / "camera 2026-10-17 22-25-44.mp4"
    camera.write_bytes(b"")
    words = make_words(tmp_path / "out" / "words" / "camera 2026-10-17 22-25-44_words.json")
    sessions = [
        {
            "stem": "camera 2026-10-17 22-25-44",
            "camera": camera,
            "screen": None,
            "words": words,
        }
    ]
    previous = {
        "sessions": [
            {
                "camera": "obs/camera 2026-10-17 22-25-44.mp4",
                "sync_offset_s": -1.25,
                "sync_offset_confidence": 0.8,
            }
        ]
    }
    document = mode_b.manifest_document(sessions, previous)
    entry = document["sessions"][0]
    assert entry["camera"] == str(camera.resolve())
    assert entry["sync_offset_s"] == -1.25
    assert entry["sync_offset_confidence"] == 0.8


def test_second_run_keeps_hand_written_sync_offset(tmp_path, capsys):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    make_camera(folder, "2026-10-17 22-25-44.mp4")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    sessions_path = folder / "mode_b" / "sessions.yaml"
    document = yaml.safe_load(sessions_path.read_text(encoding="utf-8"))
    document["sessions"][0]["sync_offset_s"] = -1.5
    document["sessions"][0]["sync_offset_confidence"] = 0.9
    sessions_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    entry = yaml.safe_load(sessions_path.read_text(encoding="utf-8"))["sessions"][0]
    assert entry["sync_offset_s"] == -1.5
    assert entry["sync_offset_confidence"] == 0.9


def test_unreadable_screen_never_loads_the_face_detector(tmp_path, monkeypatch):
    folder = tmp_path / "obs"
    camera_dir, screen_dir = make_subfolders(folder)
    make_camera(camera_dir, "camera 2026-09-29 20-58-37.mp4")
    make_camera(screen_dir, "2026-09-29 20-58-35.mp4")

    def boom(path):
        raise AssertionError("the face detector must not load")

    monkeypatch.setattr(mode_b, "make_detector", boom)
    warnings = []
    sessions = mode_b.scan_sessions(folder, tmp_path / "out", warnings)
    suspects = mode_b.run_screen_checks(sessions, warnings)
    assert suspects == []
    assert warnings == []
