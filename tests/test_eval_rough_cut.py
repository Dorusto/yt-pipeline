import json
from pathlib import Path

import pytest
import yaml

import eval_rough_cut as erc
import export_filmcraft as ef
import rough_cut as rc
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from fs_probe import safe_is_dir

REFERENCE_DIR = Path("/root/media/eval/second-brain")
REFERENCE_FCPROJ = REFERENCE_DIR / "Second Brain.fcproj"
REFERENCE_SEQUENCE = "Second Brain - 15 min"

needs_reference = pytest.mark.skipif(
    not safe_is_dir(REFERENCE_DIR), reason="reference not present"
)


def write_words(path, entries):
    path.write_text(json.dumps({
        "segments": [{
            "id": 0,
            "start": entries[0][1] if entries else 0.0,
            "end": entries[-1][2] if entries else 0.0,
            "text": " ".join(entry[0] for entry in entries),
            "words": [{"word": text, "start": start, "end": end} for text, start, end in entries],
        }]
    }), encoding="utf-8")


def make_edit(sources, slots):
    return {
        "schema_version": "0.1",
        "generated_at": "2026-10-21T00:00:00+00:00",
        "tool": "tests",
        "brief": {"text": "synthetic eval fixture"},
        "sources": sources,
        "video": slots,
    }


def source(stem):
    return {"file": f"{stem}.mp4", "path": f"/footage/{stem}.mp4", "duration_s": 10.0, "media_type": "video"}


def slot(slot_id, stem, in_s, out_s):
    return {"id": slot_id, "clip": f"{stem}.mp4", "in_s": in_s, "out_s": out_s, "score": 1.0, "reason": "kept speech"}


def write_sessions(tmp_path, entries):
    path = tmp_path / "sessions.yaml"
    path.write_text(yaml.safe_dump({"sessions": entries}), encoding="utf-8")
    return path


def build_reference(tmp_path, capsys, sources, slots, sequence="Main"):
    ref_edit = tmp_path / "reference_edit.json"
    ref_edit.write_text(json.dumps(make_edit(sources, slots)), encoding="utf-8")
    fcproj = tmp_path / "reference.fcproj"
    code = ef.main([str(ref_edit), "--out", str(fcproj), "--sequence", sequence, "--json"])
    assert code == EXIT_OK
    capsys.readouterr()
    return fcproj


def run_eval(capsys, edit_path, sessions_path, fcproj, sequence, words_dir, extra=None):
    argv = [
        "--edit", str(edit_path), "--sessions", str(sessions_path),
        "--fcproj", str(fcproj), "--sequence", sequence,
        "--words-dir", str(words_dir), "--json",
    ] + (extra or [])
    code = erc.main(argv)
    captured = capsys.readouterr()
    report = json.loads(captured.out) if captured.out.strip() else None
    return code, report, captured.err


CAM_A_WORDS = [
    ("unu", 0.0, 0.5),
    ("doi", 0.6, 1.0),
    ("trei", 1.1, 1.5),
    ("patru", 1.6, 2.0),
    ("cinci", 2.1, 2.5),
    ("șase", 2.6, 3.0),
    ("șapte", 3.1, 3.5),
    ("opt", 3.6, 4.0),
]

CAM_B_WORDS = [
    ("nouă", 0.0, 0.5),
    ("zece", 0.6, 1.0),
    ("unsprezece", 1.1, 1.5),
]


def build_controlled_case(tmp_path, capsys):
    words_dir = tmp_path / "words"
    words_dir.mkdir()
    write_words(words_dir / "camA.json", CAM_A_WORDS)
    write_words(words_dir / "camB.json", CAM_B_WORDS)

    our_edit = tmp_path / "edit.json"
    our_edit.write_text(json.dumps(make_edit(
        [source("camA"), source("camB")],
        [slot("w001", "camA", 0.0, 2.0), slot("w002", "camB", 0.0, 1.0)],
    )), encoding="utf-8")

    sessions_path = write_sessions(tmp_path, [
        {"camera": "/footage/camA.mp4", "words": str(words_dir / "camA.json")},
        {"camera": "/footage/camB.mp4", "words": str(words_dir / "camB.json")},
    ])

    fcproj = build_reference(
        tmp_path, capsys,
        [source("camA")],
        [slot("r001", "camA", 1.0, 3.0)],
    )
    return our_edit, sessions_path, fcproj, words_dir


def test_precision_recall_exact(tmp_path, capsys):
    our_edit, sessions_path, fcproj, words_dir = build_controlled_case(tmp_path, capsys)
    code, report, err = run_eval(capsys, our_edit, sessions_path, fcproj, "Main", words_dir)
    assert code == EXIT_OK, err
    assert report["ours"] == {"kept": 6, "sessions": 2}
    assert report["doru"] == {"kept": 4, "sessions_used": 1, "sessions_total": 2}
    assert report["intersect"] == 2
    assert report["precision"] == pytest.approx(2 / 6, abs=1e-4)
    assert report["recall"] == pytest.approx(2 / 4, abs=1e-4)


def test_session_bucket_and_used_metrics(tmp_path, capsys):
    our_edit, sessions_path, fcproj, words_dir = build_controlled_case(tmp_path, capsys)
    code, report, err = run_eval(capsys, our_edit, sessions_path, fcproj, "Main", words_dir)
    assert code == EXIT_OK, err
    assert report["bucket"] == {"session_words": 3, "section_words": 0, "total": 3}
    assert report["precision_used"] == pytest.approx(2 / 4, abs=1e-4)
    assert report["recall_used"] == pytest.approx(2 / 4, abs=1e-4)


def test_section_bucket(tmp_path, capsys):
    words_dir = tmp_path / "words"
    words_dir.mkdir()
    write_words(words_dir / "camA.json", CAM_A_WORDS)

    our_edit = tmp_path / "edit.json"
    our_edit.write_text(json.dumps(make_edit(
        [source("camA")],
        [slot("w001", "camA", 0.0, 4.0)],
    )), encoding="utf-8")
    sessions_path = write_sessions(tmp_path, [
        {"camera": "/footage/camA.mp4", "words": str(words_dir / "camA.json")},
    ])
    fcproj = build_reference(tmp_path, capsys, [source("camA")], [slot("r001", "camA", 0.0, 1.0)])

    code, report, err = run_eval(
        capsys, our_edit, sessions_path, fcproj, "Main", words_dir, extra=["--section-min-s", "2.0"]
    )
    assert code == EXIT_OK, err
    assert report["bucket"] == {"session_words": 0, "section_words": 6, "total": 6}
    assert report["precision"] == pytest.approx(2 / 8, abs=1e-4)
    assert report["recall"] == pytest.approx(1.0, abs=1e-4)
    assert report["precision_used"] == pytest.approx(1.0, abs=1e-4)
    assert report["recall_used"] == pytest.approx(1.0, abs=1e-4)


def test_section_below_threshold_not_bucketed(tmp_path, capsys):
    words_dir = tmp_path / "words"
    words_dir.mkdir()
    write_words(words_dir / "camA.json", CAM_A_WORDS)

    our_edit = tmp_path / "edit.json"
    our_edit.write_text(json.dumps(make_edit(
        [source("camA")],
        [slot("w001", "camA", 0.0, 4.0)],
    )), encoding="utf-8")
    sessions_path = write_sessions(tmp_path, [
        {"camera": "/footage/camA.mp4", "words": str(words_dir / "camA.json")},
    ])
    fcproj = build_reference(tmp_path, capsys, [source("camA")], [slot("r001", "camA", 0.0, 1.0)])

    code, report, err = run_eval(
        capsys, our_edit, sessions_path, fcproj, "Main", words_dir, extra=["--section-min-s", "5.0"]
    )
    assert code == EXIT_OK, err
    assert report["bucket"]["section_words"] == 0


def test_disagreement_grouping_and_ranking(tmp_path, capsys):
    our_edit, sessions_path, fcproj, words_dir = build_controlled_case(tmp_path, capsys)
    code, report, err = run_eval(capsys, our_edit, sessions_path, fcproj, "Main", words_dir)
    assert code == EXIT_OK, err
    runs = report["disagreements"]
    assert len(runs) == 3
    assert runs[0] == {
        "side": "ours_only", "stem": "camA", "start_s": 0.0, "end_s": 1.0,
        "words": 2, "sample": ["unu", "doi"],
    }
    assert runs[1]["stem"] == "camB" and runs[1]["side"] == "ours_only"
    assert runs[2] == {
        "side": "doru_only", "stem": "camA", "start_s": 2.1, "end_s": 3.0,
        "words": 2, "sample": ["cinci", "șase"],
    }


def test_cross_check_agrees(tmp_path, capsys):
    our_edit, sessions_path, fcproj, words_dir = build_controlled_case(tmp_path, capsys)
    code, report, err = run_eval(capsys, our_edit, sessions_path, fcproj, "Main", words_dir)
    assert code == EXIT_OK, err
    assert report["cross_check"] == {"subs_from_edit_words_kept": 4, "agrees": True}


def test_json_ok_is_first_key(tmp_path, capsys):
    our_edit, sessions_path, fcproj, words_dir = build_controlled_case(tmp_path, capsys)
    code, report, err = run_eval(capsys, our_edit, sessions_path, fcproj, "Main", words_dir)
    assert code == EXIT_OK, err
    assert list(report.keys())[0] == "ok"
    assert report["ok"] is True
    assert report["llm_cost_usd"] == 0.0
    assert report["wall_time_s"] >= 0.0


def test_out_writes_report(tmp_path, capsys):
    our_edit, sessions_path, fcproj, words_dir = build_controlled_case(tmp_path, capsys)
    out = tmp_path / "report.json"
    code, report, err = run_eval(
        capsys, our_edit, sessions_path, fcproj, "Main", words_dir, extra=["--out", str(out)]
    )
    assert code == EXIT_OK, err
    assert json.loads(out.read_text(encoding="utf-8")) == report


def test_missing_edit_is_exit_2(tmp_path, capsys):
    _, sessions_path, fcproj, words_dir = build_controlled_case(tmp_path, capsys)
    code, report, err = run_eval(
        capsys, tmp_path / "nope.json", sessions_path, fcproj, "Main", words_dir
    )
    assert code == EXIT_USAGE
    assert "nope.json" in err
    assert report == {"ok": False, "error": report["error"], "exit_code": EXIT_USAGE}


def test_missing_sessions_is_exit_2(tmp_path, capsys):
    our_edit, _, fcproj, words_dir = build_controlled_case(tmp_path, capsys)
    code, report, err = run_eval(
        capsys, our_edit, tmp_path / "nope.yaml", fcproj, "Main", words_dir
    )
    assert code == EXIT_USAGE
    assert "nope.yaml" in err


def test_missing_words_is_exit_3(tmp_path, capsys):
    our_edit, sessions_path, fcproj, _ = build_controlled_case(tmp_path, capsys)
    empty = tmp_path / "empty"
    empty.mkdir()
    code, report, err = run_eval(capsys, our_edit, sessions_path, fcproj, "Main", empty)
    assert code == EXIT_RUNTIME
    assert "camA.json" in err


def test_missing_fcproj_is_exit_2(tmp_path, capsys):
    our_edit, sessions_path, _, words_dir = build_controlled_case(tmp_path, capsys)
    code, report, err = run_eval(
        capsys, our_edit, sessions_path, tmp_path / "nope.fcproj", "Main", words_dir
    )
    assert code == EXIT_USAGE
    assert "nope.fcproj" in err


def test_bad_sequence_is_exit_2(tmp_path, capsys):
    our_edit, sessions_path, fcproj, words_dir = build_controlled_case(tmp_path, capsys)
    code, report, err = run_eval(capsys, our_edit, sessions_path, fcproj, "Nope", words_dir)
    assert code == EXIT_USAGE
    assert "Nope" in err and "Main" in err


def test_speed_not_one_is_exit_2(tmp_path, capsys):
    our_edit, sessions_path, fcproj, words_dir = build_controlled_case(tmp_path, capsys)
    data = json.loads(fcproj.read_text(encoding="utf-8"))
    for value in data["project"]["items"].values():
        kind = value.get("kind") or {}
        if "Sequence" in kind:
            kind["Sequence"]["audio_tracks"][0]["items"][0]["speed"] = 2.0
    broken = tmp_path / "speed.fcproj"
    broken.write_text(json.dumps(data), encoding="utf-8")
    code, report, err = run_eval(capsys, our_edit, sessions_path, broken, "Main", words_dir)
    assert code == EXIT_USAGE
    assert "speed" in err


def test_reverse_is_exit_2(tmp_path, capsys):
    our_edit, sessions_path, fcproj, words_dir = build_controlled_case(tmp_path, capsys)
    data = json.loads(fcproj.read_text(encoding="utf-8"))
    for value in data["project"]["items"].values():
        kind = value.get("kind") or {}
        if "Sequence" in kind:
            kind["Sequence"]["audio_tracks"][0]["items"][0]["reverse"] = True
    broken = tmp_path / "reverse.fcproj"
    broken.write_text(json.dumps(data), encoding="utf-8")
    code, report, err = run_eval(capsys, our_edit, sessions_path, broken, "Main", words_dir)
    assert code == EXIT_USAGE
    assert "reverse" in err


def test_disabled_clip_is_skipped(tmp_path, capsys):
    words_dir = tmp_path / "words"
    words_dir.mkdir()
    write_words(words_dir / "camA.json", CAM_A_WORDS)
    our_edit = tmp_path / "edit.json"
    our_edit.write_text(json.dumps(make_edit(
        [source("camA")], [slot("w001", "camA", 0.0, 2.0)],
    )), encoding="utf-8")
    sessions_path = write_sessions(tmp_path, [
        {"camera": "/footage/camA.mp4", "words": str(words_dir / "camA.json")},
    ])
    fcproj = build_reference(tmp_path, capsys, [source("camA")], [slot("r001", "camA", 1.0, 3.0)])
    data = json.loads(fcproj.read_text(encoding="utf-8"))
    for value in data["project"]["items"].values():
        kind = value.get("kind") or {}
        if "Sequence" in kind:
            kind["Sequence"]["audio_tracks"][0]["items"][0]["enabled"] = False
    disabled = tmp_path / "disabled.fcproj"
    disabled.write_text(json.dumps(data), encoding="utf-8")
    code, report, err = run_eval(capsys, our_edit, sessions_path, disabled, "Main", words_dir)
    assert code == EXIT_OK, err
    assert report["doru"]["kept"] == 0
    assert report["doru"]["sessions_used"] == 0
    assert report["bucket"]["session_words"] == 8


def test_until_s_trims_the_boundary_clip(tmp_path, capsys):
    words_dir = tmp_path / "words"
    words_dir.mkdir()
    write_words(words_dir / "camA.json", CAM_A_WORDS)
    our_edit = tmp_path / "edit.json"
    our_edit.write_text(json.dumps(make_edit(
        [source("camA")], [slot("w001", "camA", 0.0, 4.0)],
    )), encoding="utf-8")
    sessions_path = write_sessions(tmp_path, [
        {"camera": "/footage/camA.mp4", "words": str(words_dir / "camA.json")},
    ])
    fcproj = build_reference(
        tmp_path, capsys, [source("camA")],
        [slot("r001", "camA", 0.0, 2.0), slot("r002", "camA", 2.0, 4.0)],
    )

    code, report, err = run_eval(capsys, our_edit, sessions_path, fcproj, "Main", words_dir)
    assert code == EXIT_OK, err
    assert report["doru"]["kept"] == 8

    code, report, err = run_eval(
        capsys, our_edit, sessions_path, fcproj, "Main", words_dir, extra=["--until-s", "3.0"]
    )
    assert code == EXIT_OK, err
    assert report["doru"]["kept"] == 6


def test_until_s_excludes_words_outside_the_covered_span(tmp_path, capsys):
    words_dir = tmp_path / "words"
    words_dir.mkdir()
    write_words(words_dir / "camA.json", CAM_A_WORDS)
    our_edit = tmp_path / "edit.json"
    our_edit.write_text(json.dumps(make_edit(
        [source("camA")], [slot("w001", "camA", 0.0, 4.0)],
    )), encoding="utf-8")
    sessions_path = write_sessions(tmp_path, [
        {"camera": "/footage/camA.mp4", "words": str(words_dir / "camA.json")},
    ])
    fcproj = build_reference(tmp_path, capsys, [source("camA")], [slot("r001", "camA", 1.0, 3.0)])

    code, report, err = run_eval(capsys, our_edit, sessions_path, fcproj, "Main", words_dir)
    assert code == EXIT_OK, err
    assert report["ours"]["kept"] == 8

    code, report, err = run_eval(
        capsys, our_edit, sessions_path, fcproj, "Main", words_dir, extra=["--until-s", "150"]
    )
    assert code == EXIT_OK, err
    assert report["ours"]["kept"] == 4


def test_until_s_buckets_stems_used_only_after_s(tmp_path, capsys):
    words_dir = tmp_path / "words"
    words_dir.mkdir()
    write_words(words_dir / "camA.json", CAM_A_WORDS)
    write_words(words_dir / "camB.json", CAM_B_WORDS)
    our_edit = tmp_path / "edit.json"
    our_edit.write_text(json.dumps(make_edit(
        [source("camA"), source("camB")],
        [slot("w001", "camA", 0.0, 2.0), slot("w002", "camB", 0.0, 1.0)],
    )), encoding="utf-8")
    sessions_path = write_sessions(tmp_path, [
        {"camera": "/footage/camA.mp4", "words": str(words_dir / "camA.json")},
        {"camera": "/footage/camB.mp4", "words": str(words_dir / "camB.json")},
    ])
    fcproj = build_reference(
        tmp_path, capsys, [source("camA"), source("camB")],
        [slot("r001", "camA", 0.0, 2.0), slot("r002", "camB", 0.0, 1.0)],
    )

    code, report, err = run_eval(
        capsys, our_edit, sessions_path, fcproj, "Main", words_dir, extra=["--until-s", "1.0"]
    )
    assert code == EXIT_OK, err
    assert report["doru"]["kept"] == 2
    assert report["bucket"]["session_words"] == 3


def test_until_s_reported(tmp_path, capsys):
    our_edit, sessions_path, fcproj, words_dir = build_controlled_case(tmp_path, capsys)

    code, report, err = run_eval(capsys, our_edit, sessions_path, fcproj, "Main", words_dir)
    assert code == EXIT_OK, err
    assert report["until_s"] is None

    code, report, err = run_eval(
        capsys, our_edit, sessions_path, fcproj, "Main", words_dir, extra=["--until-s", "150"]
    )
    assert code == EXIT_OK, err
    assert report["until_s"] == 150.0


def test_until_s_nonpositive_is_exit_2(tmp_path, capsys):
    our_edit, sessions_path, fcproj, words_dir = build_controlled_case(tmp_path, capsys)
    for value in ("0", "-5"):
        code, report, err = run_eval(
            capsys, our_edit, sessions_path, fcproj, "Main", words_dir, extra=["--until-s", value]
        )
        assert code == EXIT_USAGE
        assert "--until-s" in err
        assert report == {"ok": False, "error": report["error"], "exit_code": EXIT_USAGE}


def write_review(tmp_path, rows):
    path = tmp_path / "cut_review.yaml"
    path.write_text(yaml.safe_dump({"review": rows}), encoding="utf-8")
    return path


def test_review_keep_moves_dropped_words_to_doru(tmp_path, capsys):
    our_edit, sessions_path, fcproj, words_dir = build_controlled_case(tmp_path, capsys)
    code, report, err = run_eval(capsys, our_edit, sessions_path, fcproj, "Main", words_dir)
    assert code == EXIT_OK, err
    assert report["doru"]["kept"] == 4
    assert report["recall"] == pytest.approx(2 / 4, abs=1e-4)
    assert "review_rows" not in report

    review = write_review(tmp_path, [
        {"id": "c001", "action": "keep", "clip": "camA.mp4",
         "window_in_s": 3.0, "window_out_s": 4.0},
    ])
    code, report, err = run_eval(
        capsys, our_edit, sessions_path, fcproj, "Main", words_dir,
        extra=["--review", str(review)],
    )
    assert code == EXIT_OK, err
    assert report["doru"]["kept"] == 6
    assert report["recall"] == pytest.approx(2 / 6, abs=1e-4)
    assert report["review_rows"] == 1


def test_review_cut_moves_kept_words_to_doru_dropped(tmp_path, capsys):
    our_edit, sessions_path, fcproj, words_dir = build_controlled_case(tmp_path, capsys)
    code, report, err = run_eval(capsys, our_edit, sessions_path, fcproj, "Main", words_dir)
    assert code == EXIT_OK, err
    assert report["precision"] == pytest.approx(2 / 6, abs=1e-4)

    review = write_review(tmp_path, [
        {"id": "c001", "action": "cut", "clip": "camA.mp4",
         "window_in_s": 1.0, "window_out_s": 1.6},
    ])
    code, report, err = run_eval(
        capsys, our_edit, sessions_path, fcproj, "Main", words_dir,
        extra=["--review", str(review)],
    )
    assert code == EXIT_OK, err
    assert report["doru"]["kept"] == 3
    assert report["precision"] == pytest.approx(1 / 6, abs=1e-4)
    assert report["review_rows"] == 1


def test_review_missing_file_is_exit_2(tmp_path, capsys):
    our_edit, sessions_path, fcproj, words_dir = build_controlled_case(tmp_path, capsys)
    code, report, err = run_eval(
        capsys, our_edit, sessions_path, fcproj, "Main", words_dir,
        extra=["--review", str(tmp_path / "nope.yaml")],
    )
    assert code == EXIT_USAGE
    assert "nope.yaml" in err


@needs_reference
@pytest.mark.needs_reference
def test_reference_cross_check(tmp_path, capsys):
    json_dir = REFERENCE_DIR / "json"
    stems = sorted(path.stem for path in json_dir.glob("camera *.json"))
    assert stems
    sessions_path = write_sessions(tmp_path, [
        {"camera": f"/footage/{stem}.mp4", "words": str(json_dir / f"{stem}.json")}
        for stem in stems
    ])
    edit_path = tmp_path / "edit.json"
    code = rc.main([str(sessions_path), "--out", str(edit_path), "--json"])
    assert code == EXIT_OK
    capsys.readouterr()

    code, report, err = run_eval(
        capsys, edit_path, sessions_path, REFERENCE_FCPROJ, REFERENCE_SEQUENCE, json_dir
    )
    assert code == EXIT_OK, err
    assert report["doru"]["kept"] == 1955
    assert report["cross_check"]["subs_from_edit_words_kept"] == 1891
    assert report["cross_check"]["agrees"] is True
