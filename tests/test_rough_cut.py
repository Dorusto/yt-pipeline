import json
from pathlib import Path

import yaml

import rough_cut as rc
from common.schema import validate_or_raise


def words_document(entries):
    return {
        "segments": [
            {
                "id": 0,
                "start": entries[0][1] if entries else 0.0,
                "end": entries[-1][2] if entries else 0.0,
                "text": " ".join(entry[0] for entry in entries),
                "words": [
                    {"word": text, "start": start, "end": end}
                    for text, start, end in entries
                ],
            }
        ]
    }


def run_case(tmp_path, capsys, sessions, config=None, extra=None):
    manifest = []
    for index, session in enumerate(sessions):
        words_path = tmp_path / f"words{index}.json"
        words_path.write_text(json.dumps(words_document(session["words"])), encoding="utf-8")
        entry = {"camera": session["camera"], "words": str(words_path)}
        if session.get("screen"):
            entry["screen"] = session["screen"]
        if session.get("sync_offset_s") is not None:
            entry["sync_offset_s"] = session["sync_offset_s"]
        manifest.append(entry)
    sessions_path = tmp_path / "sessions.yaml"
    sessions_path.write_text(yaml.safe_dump({"sessions": manifest}), encoding="utf-8")
    out = tmp_path / "edit.json"
    argv = [str(sessions_path), "--out", str(out), "--json"]
    if config is not None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        argv += ["--config", str(config_path)]
    argv += extra or []
    code = rc.main(argv)
    captured = capsys.readouterr()
    summary = json.loads(captured.out) if captured.out.strip() else None
    return code, out, summary


def windows_of(edit):
    return [(round(slot["in_s"], 6), round(slot["out_s"], 6)) for slot in edit["video"]]


def rejected_of(edit):
    return [decision for decision in edit["decisions"] if decision["status"] == "rejected"]


def test_pause_cut_keeps_handle(tmp_path, capsys):
    sessions = [{"camera": "/rec/cam.mp4", "words": [
        ("Bună", 0.0, 0.4), ("lume", 0.5, 0.9), ("Salut", 1.9, 2.3),
        ("din", 2.4, 2.6), ("nou", 2.7, 2.9),
    ]}]
    code, out, summary = run_case(tmp_path, capsys, sessions)
    assert code == 0
    edit = json.loads(out.read_text(encoding="utf-8"))
    assert windows_of(edit) == [(0.0, 1.02), (1.78, 3.02)]
    assert edit["video"][0]["reason"] == "start: session"
    assert edit["video"][1]["reason"] == "cut: pause 1.0 s"
    assert summary["cuts"]["pause"] == 1


def test_pause_below_threshold_kept(tmp_path, capsys):
    sessions = [{"camera": "/rec/cam.mp4", "words": [
        ("Bună", 0.0, 0.4), ("lume", 0.5, 0.9), ("Salut", 1.4, 1.8),
    ]}]
    code, out, summary = run_case(tmp_path, capsys, sessions)
    assert code == 0
    edit = json.loads(out.read_text(encoding="utf-8"))
    assert windows_of(edit) == [(0.0, 1.92)]
    assert summary["cuts"]["pause"] == 0


def test_filler_standalone_dropped(tmp_path, capsys):
    sessions = [{"camera": "/rec/cam.mp4", "words": [
        ("ă", 0.0, 0.2), ("Bună", 0.3, 0.7),
    ]}]
    code, out, summary = run_case(tmp_path, capsys, sessions)
    assert code == 0
    edit = json.loads(out.read_text(encoding="utf-8"))
    assert windows_of(edit) == [(0.2, 0.82)]
    assert rejected_of(edit)[0]["reason"] == "filler 'ă'"


def test_filler_single_deci_kept(tmp_path, capsys):
    sessions = [{"camera": "/rec/cam.mp4", "words": [
        ("Bună", 0.0, 0.4), ("deci", 0.5, 0.8), ("lume", 0.9, 1.3),
    ]}]
    code, out, summary = run_case(tmp_path, capsys, sessions)
    assert code == 0
    edit = json.loads(out.read_text(encoding="utf-8"))
    assert windows_of(edit) == [(0.0, 1.42)]
    assert summary["cuts"]["filler"] == 0


def test_filler_repeated_deci_dropped(tmp_path, capsys):
    sessions = [{"camera": "/rec/cam.mp4", "words": [
        ("Bună", 0.0, 0.4), ("deci", 0.5, 0.8), ("deci", 0.8, 1.1), ("lume", 1.2, 1.6),
    ]}]
    code, out, summary = run_case(tmp_path, capsys, sessions)
    assert code == 0
    edit = json.loads(out.read_text(encoding="utf-8"))
    assert windows_of(edit) == [(0.0, 0.5), (1.1, 1.72)]
    assert summary["cuts"]["filler"] == 1
    assert rejected_of(edit)[0]["reason"] == "filler 'deci'"


def test_config_overrides_load(tmp_path, capsys):
    sessions = [{"camera": "/rec/cam.mp4", "words": [
        ("Bună", 0.0, 0.4), ("lume", 0.5, 0.9), ("Salut", 1.4, 1.8),
    ]}]
    code, out, summary = run_case(tmp_path, capsys, sessions, config={"pause_gap_s": 0.3})
    assert code == 0
    edit = json.loads(out.read_text(encoding="utf-8"))
    assert windows_of(edit) == [(0.0, 1.02), (1.28, 1.92)]
    assert summary["cuts"]["pause"] == 1


def test_retake_last_take_kept(tmp_path, capsys):
    sessions = [{"camera": "/rec/cam.mp4", "words": [
        ("Bună", 0.0, 0.4), ("ziua", 0.5, 0.9), ("tuturor.", 1.0, 1.5),
        ("Bună", 2.0, 2.4), ("ziua", 2.5, 2.9), ("tuturor", 3.0, 3.5),
    ]}]
    code, out, summary = run_case(tmp_path, capsys, sessions)
    assert code == 0
    edit = json.loads(out.read_text(encoding="utf-8"))
    assert summary["cuts"]["retake"] == 1
    assert windows_of(edit) == [(1.5, 3.62)]
    rejected = rejected_of(edit)
    assert rejected[0]["reason"] == "retake of w001"
    assert rejected[0]["window_in_s"] == 0.0 and rejected[0]["window_out_s"] == 1.5


def test_retake_non_matching_untouched(tmp_path, capsys):
    sessions = [{"camera": "/rec/cam.mp4", "words": [
        ("Bună", 0.0, 0.4), ("ziua", 0.5, 0.9), ("tuturor.", 1.0, 1.5),
        ("Astăzi", 2.0, 2.4), ("vorbim", 2.5, 2.9), ("despre", 3.0, 3.4), ("proiect.", 3.5, 4.0),
    ]}]
    code, out, summary = run_case(tmp_path, capsys, sessions)
    assert code == 0
    assert summary["cuts"]["retake"] == 0


def test_retake_min_words_respected(tmp_path, capsys):
    sessions = [{"camera": "/rec/cam.mp4", "words": [
        ("Bună", 0.0, 0.4), ("ziua", 0.5, 0.9), ("tuturor.", 1.0, 1.5),
        ("Bună", 2.0, 2.4), ("ziua.", 2.5, 2.9),
    ]}]
    code, out, summary = run_case(tmp_path, capsys, sessions)
    assert code == 0
    assert summary["cuts"]["retake"] == 0


def test_two_sessions_global_ids_in_manifest_order(tmp_path, capsys):
    sessions = [
        {"camera": "/rec/cam1.mp4", "words": [("Bună.", 0.0, 0.4)]},
        {"camera": "/rec/cam2.mp4", "words": [("Salut.", 0.0, 0.4)]},
    ]
    code, out, summary = run_case(tmp_path, capsys, sessions)
    assert code == 0
    edit = json.loads(out.read_text(encoding="utf-8"))
    assert [slot["id"] for slot in edit["video"]] == ["w001", "w002"]
    assert [slot["clip"] for slot in edit["video"]] == ["cam1.mp4", "cam2.mp4"]
    assert [source["file"] for source in edit["sources"]] == ["cam1.mp4", "cam2.mp4"]
    assert summary["sessions"] == 2


def test_sync_offset_shifts_screen_and_shares_id(tmp_path, capsys):
    sessions = [{
        "camera": "/rec/camera 2026-10-17 22-25-44.mp4",
        "screen": "/rec/2026-10-17 22-25-44.mp4",
        "sync_offset_s": -0.35,
        "words": [("Bună.", 1.0, 1.4)],
    }]
    code, out, summary = run_case(tmp_path, capsys, sessions)
    assert code == 0
    edit = json.loads(out.read_text(encoding="utf-8"))
    screen_slot, camera_slot = edit["video"]
    assert screen_slot["id"] == camera_slot["id"] == "w001"
    assert screen_slot["clip"] == "2026-10-17 22-25-44.mp4"
    assert camera_slot["clip"] == "camera 2026-10-17 22-25-44.mp4"
    assert screen_slot["in_s"] == camera_slot["in_s"] - 0.35
    assert screen_slot["out_s"] == camera_slot["out_s"] - 0.35


def test_computed_offset_missing_media_is_zero_with_warning(tmp_path, capsys):
    sessions = [{
        "camera": "/rec/camera 2026-10-17 22-25-44.mp4",
        "screen": "/rec/2026-10-17 22-25-44.mp4",
        "words": [("Bună.", 1.0, 1.4)],
    }]
    code, out, summary = run_case(tmp_path, capsys, sessions)
    assert code == 0
    edit = json.loads(out.read_text(encoding="utf-8"))
    screen_slot, camera_slot = edit["video"]
    assert screen_slot["in_s"] == camera_slot["in_s"]
    assert screen_slot["out_s"] == camera_slot["out_s"]
    assert any("missing media" in warning for warning in summary["warnings"])


def test_computed_offset_without_manifest_value_includes_container_start(tmp_path, capsys, monkeypatch):
    starts = {
        "camera 2026-10-17 22-25-45.mp4": 0.7,
        "2026-10-17 22-25-44.mp4": 0.0,
    }
    monkeypatch.setattr(
        rc,
        "probe_media",
        lambda path: (None, starts.get(Path(path).name, 0.0)),
    )
    sessions = [{
        "camera": "/rec/camera 2026-10-17 22-25-45.mp4",
        "screen": "/rec/2026-10-17 22-25-44.mp4",
        "words": [("Bună.", 1.0, 1.4)],
    }]
    code, out, summary = run_case(tmp_path, capsys, sessions)
    assert code == 0
    edit = json.loads(out.read_text(encoding="utf-8"))
    screen_slot, camera_slot = edit["video"]
    assert screen_slot["id"] == camera_slot["id"] == "w001"
    assert screen_slot["in_s"] == camera_slot["in_s"] + 1.7
    assert screen_slot["out_s"] == camera_slot["out_s"] + 1.7


def test_written_edit_validates_against_schema(tmp_path, capsys):
    sessions = [{"camera": "/rec/cam.mp4", "words": [("Bună.", 0.0, 0.4)]}]
    code, out, summary = run_case(tmp_path, capsys, sessions)
    assert code == 0
    validate_or_raise(json.loads(out.read_text(encoding="utf-8")), "edit")


def test_llm_proposals_written_but_not_applied(tmp_path, capsys, monkeypatch):
    config = {
        "models": {"director": "mock-model"},
        "llm_budget_usd": 1.0,
        "llm_backend": "mock",
        "llm_mock_text": {
            "director": '{"proposals": [{"start_s": 1.0, "end_s": 2.0, "reason": "off-topic"}]}'
        },
    }
    config_path = tmp_path / "llm_config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setenv("YT_CONFIG", str(config_path))
    sessions = [{"camera": "/rec/cam.mp4", "words": [("Bună.", 0.0, 0.4)]}]
    code, out, summary = run_case(tmp_path, capsys, sessions, extra=["--llm"])
    assert code == 0
    assert summary["proposals"] == [{"start_s": 1.0, "end_s": 2.0, "reason": "off-topic"}]
    proposals_path = Path(summary["outputs"]["proposals"])
    assert proposals_path.is_file()
    stored = json.loads(proposals_path.read_text(encoding="utf-8"))
    assert stored["proposals"][0]["reason"] == "off-topic"
    edit = json.loads(out.read_text(encoding="utf-8"))
    assert all(slot["reason"] != "off-topic" for slot in edit["video"])


def test_missing_words_file_is_exit_2(tmp_path, capsys):
    sessions_path = tmp_path / "sessions.yaml"
    sessions_path.write_text(yaml.safe_dump({"sessions": [
        {"camera": "/rec/cam.mp4", "words": str(tmp_path / "nope.json")}
    ]}), encoding="utf-8")
    code = rc.main([str(sessions_path), "--out", str(tmp_path / "edit.json"), "--json"])
    assert code == 2
    assert "not found" in capsys.readouterr().err


def write_review(tmp_path, rows):
    path = tmp_path / "cut_review.yaml"
    path.write_text(yaml.safe_dump({"review": rows}), encoding="utf-8")
    return path


def pause_session():
    return [{"camera": "/rec/cam.mp4", "words": [
        ("Bună", 0.0, 0.4), ("lume", 0.5, 0.9), ("Salut", 1.9, 2.3),
        ("din", 2.4, 2.6), ("nou", 2.7, 2.9),
    ]}]


def test_review_keep_keeps_the_pause(tmp_path, capsys):
    review = write_review(tmp_path, [
        {"id": "c001", "action": "keep", "clip": "cam.mp4",
         "window_in_s": 1.0, "window_out_s": 1.8},
    ])
    code, out, summary = run_case(tmp_path, capsys, pause_session(),
                                  extra=["--review", str(review)])
    assert code == 0
    edit = json.loads(out.read_text(encoding="utf-8"))
    assert windows_of(edit) == [(0.0, 3.02)]
    assert edit["video"][0]["reason"] == "kept by review"
    decision = next(row for row in edit["decisions"] if row["id"] == "c001")
    assert decision["status"] == "used"
    assert decision["reason"] == "kept by review"
    assert decision["slot"] == "w001"
    assert summary["review"] == {"keep": 1, "cut": 0, "ignored": 0}


def test_review_cut_changes_nothing(tmp_path, capsys):
    review = write_review(tmp_path, [
        {"id": "c001", "action": "cut", "clip": "cam.mp4",
         "window_in_s": 1.0, "window_out_s": 1.8},
    ])
    code, out, summary = run_case(tmp_path, capsys, pause_session(),
                                  extra=["--review", str(review)])
    assert code == 0
    edit = json.loads(out.read_text(encoding="utf-8"))
    assert windows_of(edit) == [(0.0, 1.02), (1.78, 3.02)]
    assert summary["review"] == {"keep": 0, "cut": 1, "ignored": 0}


def test_review_stale_row_warned_and_ignored(tmp_path, capsys):
    review = write_review(tmp_path, [
        {"id": "c001", "action": "keep", "clip": "cam.mp4",
         "window_in_s": 50.0, "window_out_s": 51.0},
    ])
    code, out, summary = run_case(tmp_path, capsys, pause_session(),
                                  extra=["--review", str(review)])
    assert code == 0
    edit = json.loads(out.read_text(encoding="utf-8"))
    assert windows_of(edit) == [(0.0, 1.02), (1.78, 3.02)]
    assert summary["review"] == {"keep": 0, "cut": 0, "ignored": 1}
    assert any("does not overlap" in warning for warning in summary["warnings"])


def test_review_keep_does_not_shift_later_run_ids(tmp_path, capsys):
    sessions = [{"camera": "/rec/cam.mp4", "words": [
        ("Bună", 0.0, 0.4), ("lume", 0.5, 0.9), ("Salut", 1.9, 2.3),
        ("din", 2.4, 2.6), ("nou", 2.7, 2.9), ("Aici", 3.9, 4.3), ("final", 4.4, 4.8),
    ]}]
    review = write_review(tmp_path, [
        {"id": "c001", "action": "keep", "clip": "cam.mp4",
         "window_in_s": 1.0, "window_out_s": 1.8},
    ])
    code, out, summary = run_case(tmp_path, capsys, sessions,
                                  extra=["--review", str(review)])
    assert code == 0
    edit = json.loads(out.read_text(encoding="utf-8"))
    rejected = [row["id"] for row in rejected_of(edit)]
    assert rejected == ["c002"]
    assert next(row for row in edit["decisions"] if row["id"] == "c001")["status"] == "used"

    code, out, summary = run_case(tmp_path, capsys, sessions)
    assert code == 0
    edit = json.loads(out.read_text(encoding="utf-8"))
    assert [row["id"] for row in rejected_of(edit)] == ["c001", "c002"]


def test_review_missing_file_is_exit_2(tmp_path, capsys):
    words_path = tmp_path / "words.json"
    words_path.write_text(json.dumps(words_document([("Bună", 0.0, 0.4)])), encoding="utf-8")
    sessions_path = tmp_path / "sessions.yaml"
    sessions_path.write_text(yaml.safe_dump({"sessions": [
        {"camera": "/rec/cam.mp4", "words": str(words_path)}
    ]}), encoding="utf-8")
    code = rc.main([str(sessions_path), "--out", str(tmp_path / "edit.json"),
                    "--review", str(tmp_path / "nope.yaml"), "--json"])
    assert code == 2
    err = capsys.readouterr().err
    assert err.strip()
    assert "\n" not in err.strip()
