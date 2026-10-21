import json
from pathlib import Path

import yaml

import mode_b


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
    assert summary["skipped"] == {"transcribe": 0, "rough_cut": False, "export": False}
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
    assert summary["skipped"] == {"transcribe": 1, "rough_cut": True, "export": True}


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
    assert summary["skipped"] == {"transcribe": 0, "rough_cut": False, "export": False}


def test_transcribe_only_missing_words(tmp_path, capsys):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    make_camera(folder, "camera 2026-10-18 09-00-00.mp4")
    make_words(folder / "camera 2026-10-17 22-25-44_words.json")
    code, out, err = run_main([str(folder), "--backend", "mock", "--json"], capsys)
    assert code == 0
    summary = json.loads(out)
    assert summary["transcribed"] == 1
    assert summary["skipped"]["transcribe"] == 1
    assert not (folder / "mode_b" / "words" / "camera 2026-10-17 22-25-44_words.json").is_file()
    assert (folder / "mode_b" / "words" / "camera 2026-10-18 09-00-00_words.json").is_file()


def test_printed_project_path(tmp_path, capsys):
    folder = tmp_path / "obs"
    folder.mkdir()
    make_camera(folder, "camera 2026-10-17 22-25-44.mp4")
    code, out, err = run_main([str(folder), "--backend", "mock"], capsys)
    assert code == 0
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines[-1] == f"project: {folder / 'mode_b' / 'project.fcproj'}"


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
