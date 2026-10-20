import json
from pathlib import Path

import subs_from_edit as sfe

FIXTURES = Path(__file__).parent / "fixtures" / "publish"
FCPROJ = FIXTURES / "fixture.fcproj"

EXPECTED_WORDS = [
    {"word": "doi", "start": 0.2, "end": 0.6},
    {"word": "trei", "start": 1.0, "end": 1.5},
    {"word": "patru", "start": 2.0, "end": 2.6},
    {"word": "cinci.", "start": 3.2, "end": 3.8},
    {"word": "Alfa", "start": 4.2, "end": 4.7},
    {"word": "beta", "start": 5.0, "end": 5.5},
    {"word": "gama.", "start": 6.0, "end": 6.6},
    {"word": "opt", "start": 8.6, "end": 9.0},
    {"word": "nouă.", "start": 9.3, "end": 9.8},
]

EXPECTED_SRT = (
    "1\n00:00:00,200 --> 00:00:03,800\ndoi trei patru cinci.\n\n"
    "2\n00:00:04,200 --> 00:00:06,600\nAlfa beta gama.\n\n"
    "3\n00:00:08,600 --> 00:00:09,800\nopt nouă.\n"
)


def run_fixture(tmp_path, capsys, name="fixture"):
    out = tmp_path / "out"
    rc = sfe.main([
        "--fcproj", str(FCPROJ), "--sequence", "Main",
        "--words-dir", str(FIXTURES), "--out-dir", str(out),
        "--name", name, "--json",
    ])
    summary = json.loads(capsys.readouterr().out)
    return rc, out, summary


def test_fixture_end_to_end_words(tmp_path, capsys):
    rc, out, _ = run_fixture(tmp_path, capsys)
    assert rc == 0
    words = json.loads((out / "fixture_words.json").read_text(encoding="utf-8"))
    assert words == EXPECTED_WORDS


def test_fixture_end_to_end_srt(tmp_path, capsys):
    rc, out, _ = run_fixture(tmp_path, capsys)
    assert rc == 0
    assert (out / "fixture_RO.srt").read_text(encoding="utf-8") == EXPECTED_SRT


def test_fixture_summary_counts_and_clips(tmp_path, capsys):
    rc, _, summary = run_fixture(tmp_path, capsys)
    assert rc == 0
    assert summary["words_kept"] == 9
    assert summary["words_dropped"] == 6
    assert summary["cues"] == 3
    assert summary["llm_cost_usd"] == 0.0
    assert summary["wall_time_s"] >= 0.0
    clips = summary["clips"]
    assert clips[0] == {
        "name": "camera A.mp4", "kept_words": 4,
        "timeline_start_s": 0.0, "timeline_end_s": 4.0,
    }
    assert clips[1]["timeline_start_s"] == 4.0 and clips[1]["timeline_end_s"] == 7.0
    assert clips[2]["timeline_start_s"] == 8.0 and clips[2]["timeline_end_s"] == 10.0
    assert clips[3] == {"name": "camera B.mp4", "skipped": "disabled"}


def test_cut_in_middle_of_word_is_dropped(tmp_path, capsys):
    _, out, _ = run_fixture(tmp_path, capsys)
    kept = [word["word"] for word in json.loads((out / "fixture_words.json").read_text(encoding="utf-8"))]
    assert "șapte" not in kept
    assert "Unu" not in kept
    assert "delta" not in kept
    assert "epsilon" not in kept


def test_two_clips_from_same_source(tmp_path, capsys):
    _, out, summary = run_fixture(tmp_path, capsys)
    names = [clip["name"] for clip in summary["clips"]]
    assert names.count("camera A.mp4") == 2
    kept = [word["word"] for word in json.loads((out / "fixture_words.json").read_text(encoding="utf-8"))]
    assert kept.count("opt") == 1
    assert kept.count("nouă.") == 1


def test_gap_between_clips_keeps_timeline_positions(tmp_path, capsys):
    _, out, _ = run_fixture(tmp_path, capsys)
    words = json.loads((out / "fixture_words.json").read_text(encoding="utf-8"))
    assert words[-2]["word"] == "opt" and words[-2]["start"] == 8.6
    assert words[-1]["word"] == "nouă." and words[-1]["start"] == 9.3


def test_disabled_clip_is_skipped(tmp_path, capsys):
    _, _, summary = run_fixture(tmp_path, capsys)
    assert summary["clips"][3]["skipped"] == "disabled"
    assert "kept_words" not in summary["clips"][3]


def test_fcproj_speed_error_names_clip(tmp_path, capsys):
    data = json.loads(FCPROJ.read_text(encoding="utf-8"))
    data["project"]["items"]["10"]["kind"]["Sequence"]["audio_tracks"][0]["items"][0]["speed"] = 2.0
    proj = tmp_path / "speed.fcproj"
    proj.write_text(json.dumps(data), encoding="utf-8")
    rc = sfe.main([
        "--fcproj", str(proj), "--sequence", "Main",
        "--words-dir", str(FIXTURES), "--out-dir", str(tmp_path / "out"), "--json",
    ])
    assert rc == 2
    assert "camera A.mp4" in capsys.readouterr().err


def test_fcproj_reverse_error_names_clip(tmp_path, capsys):
    data = json.loads(FCPROJ.read_text(encoding="utf-8"))
    data["project"]["items"]["10"]["kind"]["Sequence"]["audio_tracks"][0]["items"][0]["reverse"] = True
    proj = tmp_path / "reverse.fcproj"
    proj.write_text(json.dumps(data), encoding="utf-8")
    rc = sfe.main([
        "--fcproj", str(proj), "--sequence", "Main",
        "--words-dir", str(FIXTURES), "--out-dir", str(tmp_path / "out"), "--json",
    ])
    assert rc == 2
    assert "camera A.mp4" in capsys.readouterr().err


def test_missing_words_json_names_clip_and_path(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = sfe.main([
        "--fcproj", str(FCPROJ), "--sequence", "Main",
        "--words-dir", str(empty), "--out-dir", str(tmp_path / "out"), "--json",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "camera A.mp4" in err
    assert "camera A.json" in err
    assert str(empty) in err


def test_unknown_sequence_lists_names(tmp_path, capsys):
    rc = sfe.main([
        "--fcproj", str(FCPROJ), "--sequence", "Nope",
        "--words-dir", str(FIXTURES), "--out-dir", str(tmp_path / "out"), "--json",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Nope" in err and "Main" in err


def test_unknown_track_lists_names(tmp_path, capsys):
    rc = sfe.main([
        "--fcproj", str(FCPROJ), "--sequence", "Main", "--track", "Nope",
        "--words-dir", str(FIXTURES), "--out-dir", str(tmp_path / "out"), "--json",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Nope" in err and "Audio 1" in err


def test_both_paths_is_an_error(tmp_path, capsys):
    rc = sfe.main([
        "--fcproj", str(FCPROJ), "--sequence", "Main", "--from-edit", str(FCPROJ),
        "--words-dir", str(FIXTURES), "--out-dir", str(tmp_path / "out"), "--json",
    ])
    assert rc == 2
    assert "exactly one" in capsys.readouterr().err


def test_neither_path_is_an_error(tmp_path, capsys):
    rc = sfe.main([
        "--words-dir", str(FIXTURES), "--out-dir", str(tmp_path / "out"), "--json",
    ])
    assert rc == 2
    assert "exactly one" in capsys.readouterr().err


def test_cue_line_length_splits_long_run():
    placements = [
        {"word": "a" * 30, "start": 0.0, "end": 0.5},
        {"word": "b" * 30, "start": 0.6, "end": 1.1},
        {"word": "c" * 30, "start": 1.2, "end": 1.7},
    ]
    cues = sfe.build_cues(placements)
    assert len(cues) == 2
    assert len(cues[0]) == 2
    lines = sfe.layout_lines([piece["word"] for piece in cues[0]])
    assert len(lines) == 2
    first_block = sfe.build_srt(cues).strip().split("\n\n")[0]
    assert first_block.count("\n") == 3


def test_single_long_word_goes_alone():
    placements = [{"word": "x" * 50, "start": 0.0, "end": 0.5}]
    cues = sfe.build_cues(placements)
    assert len(cues) == 1
    assert sfe.build_srt(cues).splitlines()[2] == "x" * 50


def write_words(path, entries):
    path.write_text(json.dumps({
        "text": " ".join(entry["word"].strip() for entry in entries),
        "language": "ro",
        "segments": [{
            "id": 0, "start": 0.0, "end": 30.0,
            "text": " ".join(entry["word"].strip() for entry in entries),
            "words": entries,
        }],
    }), encoding="utf-8")


def test_from_edit_crossfade_shifts_words(tmp_path, capsys):
    words_dir = tmp_path / "words"
    words_dir.mkdir()
    write_words(words_dir / "foo.json", [
        {"word": " a", "start": 1.5, "end": 1.9, "probability": 0.9},
        {"word": " b", "start": 5.5, "end": 5.9, "probability": 0.9},
        {"word": " c", "start": 9.0, "end": 9.4, "probability": 0.9},
    ])
    edit = {
        "schema_version": "0.1",
        "generated_at": "2026-10-20T00:00:00",
        "brief": {"text": "x"},
        "sources": [{"file": "foo.mp4", "path": "/footage/foo.mp4"}],
        "video": [
            {"id": "v1", "clip": "foo.mp4", "in_s": 1.0, "out_s": 3.0, "score": 1.0, "reason": "x"},
            {"id": "v2", "clip": "foo.mp4", "in_s": 5.0, "out_s": 8.0, "score": 1.0, "reason": "x",
             "transition": {"type": "crossfade", "duration_s": 1.0}},
        ],
    }
    edit_path = tmp_path / "edit.json"
    edit_path.write_text(json.dumps(edit), encoding="utf-8")
    out = tmp_path / "out"
    rc = sfe.main([
        "--from-edit", str(edit_path), "--words-dir", str(words_dir),
        "--out-dir", str(out), "--json",
    ])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["words_kept"] == 2
    assert summary["words_dropped"] == 1
    words = json.loads((out / "edit_words.json").read_text(encoding="utf-8"))
    assert words == [
        {"word": "a", "start": 0.5, "end": 0.9},
        {"word": "b", "start": 1.5, "end": 1.9},
    ]


def test_from_edit_middle_speed_error(tmp_path, capsys):
    words_dir = tmp_path / "words"
    words_dir.mkdir()
    write_words(words_dir / "foo.json", [{"word": " a", "start": 1.5, "end": 1.9, "probability": 0.9}])
    edit = {
        "schema_version": "0.1",
        "generated_at": "2026-10-20T00:00:00",
        "brief": {"text": "x"},
        "sources": [{"file": "foo.mp4", "path": "/footage/foo.mp4"}],
        "video": [
            {"id": "v1", "clip": "foo.mp4", "in_s": 1.0, "out_s": 3.0, "score": 1.0, "reason": "x",
             "middle_speed": 1.2},
        ],
    }
    edit_path = tmp_path / "edit.json"
    edit_path.write_text(json.dumps(edit), encoding="utf-8")
    rc = sfe.main([
        "--from-edit", str(edit_path), "--words-dir", str(words_dir),
        "--out-dir", str(tmp_path / "out"), "--json",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "v1" in err and "foo.mp4" in err


def test_from_edit_photo_slot_occupies_timeline(tmp_path, capsys):
    words_dir = tmp_path / "words"
    words_dir.mkdir()
    write_words(words_dir / "foo.json", [{"word": " a", "start": 1.5, "end": 1.9, "probability": 0.9}])
    edit = {
        "schema_version": "0.1",
        "generated_at": "2026-10-20T00:00:00",
        "brief": {"text": "x"},
        "sources": [
            {"file": "foo.mp4", "path": "/footage/foo.mp4"},
            {"file": "pic.jpg", "path": "/footage/pic.jpg", "media_type": "photo"},
        ],
        "video": [
            {"id": "p1", "kind": "photo", "photo": "pic.jpg", "duration_s": 3.0,
             "ken_burns": {"move": "zoom_in", "amount": 0.08, "focus_x": 0.5, "focus_y": 0.5},
             "score": 1.0, "reason": "x"},
            {"id": "v1", "clip": "foo.mp4", "in_s": 1.0, "out_s": 3.0, "score": 1.0, "reason": "x"},
        ],
    }
    edit_path = tmp_path / "edit.json"
    edit_path.write_text(json.dumps(edit), encoding="utf-8")
    out = tmp_path / "out"
    rc = sfe.main([
        "--from-edit", str(edit_path), "--words-dir", str(words_dir),
        "--out-dir", str(out), "--json",
    ])
    assert rc == 0
    words = json.loads((out / "edit_words.json").read_text(encoding="utf-8"))
    assert words == [{"word": "a", "start": 3.5, "end": 3.9}]


def test_from_edit_crossfade_without_duration_uses_default(tmp_path, capsys):
    words_dir = tmp_path / "words"
    words_dir.mkdir()
    write_words(words_dir / "foo.json", [
        {"word": " a", "start": 1.5, "end": 1.9, "probability": 0.9},
        {"word": " b", "start": 5.5, "end": 5.9, "probability": 0.9},
    ])
    edit = {
        "schema_version": "0.1",
        "generated_at": "2026-10-20T00:00:00",
        "brief": {"text": "x"},
        "sources": [{"file": "foo.mp4", "path": "/footage/foo.mp4"}],
        "video": [
            {"id": "v1", "clip": "foo.mp4", "in_s": 1.0, "out_s": 3.0, "score": 1.0, "reason": "x"},
            {"id": "v2", "clip": "foo.mp4", "in_s": 5.0, "out_s": 7.0, "score": 1.0, "reason": "x",
             "transition": {"type": "crossfade"}},
        ],
    }
    edit_path = tmp_path / "edit.json"
    edit_path.write_text(json.dumps(edit), encoding="utf-8")
    out = tmp_path / "out"
    rc = sfe.main([
        "--from-edit", str(edit_path), "--words-dir", str(words_dir),
        "--out-dir", str(out), "--json",
    ])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["clips"][1]["timeline_start_s"] == 1.7
    words = json.loads((out / "edit_words.json").read_text(encoding="utf-8"))
    assert words == [
        {"word": "a", "start": 0.5, "end": 0.9},
        {"word": "b", "start": 2.2, "end": 2.6},
    ]


def test_from_edit_crossfade_clamps_to_half_neighbour(tmp_path, capsys):
    words_dir = tmp_path / "words"
    words_dir.mkdir()
    write_words(words_dir / "foo.json", [
        {"word": " a", "start": 1.5, "end": 1.9, "probability": 0.9},
        {"word": " b", "start": 5.5, "end": 5.9, "probability": 0.9},
    ])
    edit = {
        "schema_version": "0.1",
        "generated_at": "2026-10-20T00:00:00",
        "brief": {"text": "x"},
        "sources": [{"file": "foo.mp4", "path": "/footage/foo.mp4"}],
        "video": [
            {"id": "v1", "clip": "foo.mp4", "in_s": 1.0, "out_s": 3.0, "score": 1.0, "reason": "x"},
            {"id": "v2", "clip": "foo.mp4", "in_s": 5.0, "out_s": 7.0, "score": 1.0, "reason": "x",
             "transition": {"type": "crossfade", "duration_s": 2.0}},
        ],
    }
    edit_path = tmp_path / "edit.json"
    edit_path.write_text(json.dumps(edit), encoding="utf-8")
    out = tmp_path / "out"
    rc = sfe.main([
        "--from-edit", str(edit_path), "--words-dir", str(words_dir),
        "--out-dir", str(out), "--json",
    ])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["clips"][1]["timeline_start_s"] == 1.0
    words = json.loads((out / "edit_words.json").read_text(encoding="utf-8"))
    assert words == [
        {"word": "a", "start": 0.5, "end": 0.9},
        {"word": "b", "start": 1.5, "end": 1.9},
    ]
