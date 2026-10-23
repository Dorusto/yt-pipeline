import json
from pathlib import Path

import pytest

import eval_labels
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.schema import validate

FIXTURES = Path(__file__).parent / "fixtures" / "eval"


def motion_doc(moves=None, events=None, file_name="clip.mp4"):
    doc = {"file": file_name, "duration_s": 60.0, "moves": moves or []}
    if events is not None:
        doc["events"] = events
    return doc


def move(start, end, label):
    return {"id": "m", "start_s": start, "end_s": end, "label": label}


def quality_doc(segments=None, file_name="clip.mp4"):
    return {"file": file_name, "duration_s": 60.0, "segments": segments or []}


def qseg(start, end, role="move", horizon_deg=None, defects=None):
    segment = {
        "id": "s",
        "start_s": start,
        "end_s": end,
        "role": role,
        "confidence": 0.9,
        "defects": defects or [],
        "metrics": {},
    }
    if horizon_deg is not None:
        segment["metrics"]["horizon_deg"] = horizon_deg
    return segment


def gt_clip(segments, events=None):
    clip = {"segments": segments}
    if events is not None:
        clip["events"] = events
    return clip


def test_parse_clock_mm_ss():
    assert eval_labels.parse_clock("01:35") == 95.0
    assert eval_labels.parse_clock("02:43") == 163.0
    assert eval_labels.parse_clock("00:00") == 0.0


def test_parse_clock_hh_mm_ss():
    assert eval_labels.parse_clock("01:02:03") == 3723.0


def test_parse_clock_number_passes_through():
    assert eval_labels.parse_clock(12.5) == 12.5


def test_label_credit_exact_and_equivalent():
    assert eval_labels.label_credit("pan_left", "pan_left") == 1.0
    assert eval_labels.label_credit("hold", "static") == 1.0
    assert eval_labels.label_credit("orbit", "orbit_candidate") == 1.0


def test_label_credit_family():
    assert eval_labels.label_credit("travel_forward", "dolly_in") == 0.5
    assert eval_labels.label_credit("travel_backward", "dolly_out") == 0.5
    assert eval_labels.label_credit("truck_left", "pan_left") == 0.5
    assert eval_labels.label_credit("orbit", "pan_right") == 0.5


def test_label_credit_wrong():
    assert eval_labels.label_credit("pan_left", "pan_right") == 0.0
    assert eval_labels.label_credit("travel_forward", "dolly_out") == 0.0


def test_perfect_match_scores_one_on_all_components():
    clip = gt_clip(
        segments=[
            {
                "start": "00:00",
                "end": "00:10",
                "expect_move": "pan_left",
                "expect_role": "move",
                "horizon": "level",
            }
        ],
        events=[{"at": "00:05"}],
    )
    row = eval_labels.score_clip(
        "0100",
        clip,
        motion_doc([move(0, 10, "pan_left")], events=[{"t_s": 5.0}]),
        quality_doc([qseg(0, 10, "move", horizon_deg=1.0)]),
        [],
        5.0,
        3.0,
    )
    assert row["move"]["score"] == 1.0
    assert row["role"]["score"] == 1.0
    assert row["horizon"]["score"] == 1.0
    assert row["events"]["score"] == 1.0
    assert row["overall"] == 1.0


def test_family_move_scores_half():
    segments = [{"start": "00:00", "end": "00:10", "expect_move": "travel_forward"}]
    component = eval_labels.move_component(segments, motion_doc([move(0, 10, "dolly_in")]))
    assert component["score"] == 0.5
    assert component["segments"][0]["seconds_exact"] == 0.0
    assert component["segments"][0]["seconds_family"] == 10.0


def test_wrong_move_scores_zero():
    segments = [{"start": "00:00", "end": "00:10", "expect_move": "pan_left"}]
    component = eval_labels.move_component(segments, motion_doc([move(0, 10, "pan_right")]))
    assert component["score"] == 0.0
    assert component["segments"][0]["seconds_wrong"] == 10.0


def test_partial_overlap_leaves_uncovered():
    segments = [{"start": "00:00", "end": "00:10", "expect_move": "pan_left"}]
    component = eval_labels.move_component(segments, motion_doc([move(2, 6, "pan_left")]))
    scored = component["segments"][0]
    assert scored["seconds_exact"] == 4.0
    assert scored["seconds_uncovered"] == 6.0
    assert component["score"] == 0.4


def test_still_phase_scores_hold():
    segments = [{"start": "00:00", "end": "00:10", "expect_move": "hold"}]
    motion = motion_doc()
    motion["phases"] = [{"start_s": 0.0, "end_s": 10.0, "type": "still", "dominant_motion": "static"}]
    component = eval_labels.move_component(segments, motion)
    assert component["score"] == 1.0
    assert component["segments"][0]["seconds_exact"] == 10.0


def test_move_wins_over_still_phase_on_overlap():
    segments = [{"start": "00:00", "end": "00:10", "expect_move": "pan_left"}]
    motion = motion_doc([move(0, 5, "pan_left")])
    motion["phases"] = [{"start_s": 0.0, "end_s": 10.0, "type": "still", "dominant_motion": "static"}]
    component = eval_labels.move_component(segments, motion)
    scored = component["segments"][0]
    assert scored["seconds_exact"] == 5.0
    assert scored["seconds_wrong"] == 5.0
    assert component["score"] == 0.5


def test_horizon_false_positive_scores_zero():
    segments = [{"start": "00:00", "end": "00:10", "horizon": "level"}]
    component = eval_labels.horizon_component(segments, quality_doc([qseg(0, 10, "hold", horizon_deg=12.0)]), 5.0)
    assert component["score"] == 0.0
    assert component["fp_rate"] == 1.0
    assert component["level_rate"] == 0.0
    assert component["segments"][0]["fp_seconds"] == 10.0


def test_horizon_defect_counts_as_tilted():
    segments = [{"start": "00:00", "end": "00:10", "horizon": "level"}]
    tilted = qseg(0, 10, "hold", horizon_deg=1.0, defects=[{"name": "horizon", "value": 0.3}])
    component = eval_labels.horizon_component(segments, quality_doc([tilted]), 5.0)
    assert component["score"] == 0.0
    assert component["fp_rate"] == 1.0


def test_horizon_null_abstains():
    segments = [{"start": "00:00", "end": "00:10", "horizon": "level"}]
    component = eval_labels.horizon_component(segments, quality_doc([qseg(0, 10, "hold")]), 5.0)
    assert component["score"] == 0.0
    assert component["fp_rate"] == 0.0
    assert component["level_rate"] == 0.0
    assert component["segments"][0]["abstain_seconds"] == 10.0


def test_event_within_and_outside_tolerance():
    events = [{"at": "00:05"}, {"at": "00:20"}]
    component = eval_labels.events_component(events, motion_doc(events=[{"t_s": 6.5}, {"t_s": 30.0}]), 3.0)
    assert component["score"] == 0.5
    assert component["recall"] == 0.5
    assert component["precision"] == 0.5
    assert component["f1"] == 0.5
    assert component["matched_count"] == 1
    assert component["predicted_count"] == 2
    assert component["gt_count"] == 2
    assert component["events"][0]["matched_t_s"] == 6.5
    assert component["events"][1]["matched_t_s"] is None


def test_event_without_motion_events_is_missed():
    component = eval_labels.events_component([{"at": "00:05"}], motion_doc(), 3.0)
    assert component["score"] == 0.0
    assert component["recall"] == 0.0
    assert component["precision"] == 0.0
    assert component["predicted_count"] == 0
    assert component["events"][0]["matched_t_s"] is None


def test_event_matching_is_one_to_one():
    events = [{"at": "00:32"}, {"at": "00:38"}]
    component = eval_labels.events_component(events, motion_doc(events=[{"t_s": 35.0}]), 3.0)
    assert component["matched_count"] == 1
    assert component["recall"] == 0.5
    assert component["precision"] == 1.0
    assert abs(component["f1"] - 2 / 3) < 1e-9
    matched_times = [item["matched_t_s"] for item in component["events"] if item["matched_t_s"] is not None]
    assert matched_times == [35.0]


def test_event_false_positives_lower_precision():
    events = [{"at": "00:10"}]
    component = eval_labels.events_component(events, motion_doc(events=[{"t_s": 10.0}, {"t_s": 50.0}]), 3.0)
    assert component["recall"] == 1.0
    assert component["precision"] == 0.5
    assert abs(component["f1"] - 2 / 3) < 1e-9
    assert component["score"] == component["f1"]


def test_missing_motion_gives_note_and_zero_move(tmp_path):
    (tmp_path / "motion").mkdir()
    (tmp_path / "quality").mkdir()
    gt = {"clips": {"0100": gt_clip([{"start": "00:00", "end": "00:10", "expect_move": "hold"}])}}
    report = eval_labels.evaluate(gt, tmp_path, ground_truth_path="GT.yaml")
    row = report["clips"][0]
    assert "missing motion output" in row["notes"]
    assert "missing quality output" in row["notes"]
    assert row["move"]["score"] == 0.0
    assert row["file"] is None


def test_multiple_outputs_note_and_first_sorted(tmp_path):
    motion = tmp_path / "motion"
    motion.mkdir()
    (tmp_path / "quality").mkdir()
    (motion / "clip_0100_a.json").write_text(json.dumps(motion_doc([move(0, 10, "hold")], file_name="a.mp4")), encoding="utf-8")
    (motion / "clip_0100_b.json").write_text(json.dumps(motion_doc([move(0, 10, "hold")], file_name="b.mp4")), encoding="utf-8")
    gt = {"clips": {"0100": gt_clip([{"start": "00:00", "end": "00:10", "expect_move": "hold"}])}}
    report = eval_labels.evaluate(gt, tmp_path)
    row = report["clips"][0]
    assert any("multiple" in note for note in row["notes"])
    assert row["file"] == "a.mp4"


def test_file_falls_back_to_quality(tmp_path):
    (tmp_path / "motion").mkdir()
    quality = tmp_path / "quality"
    quality.mkdir()
    (quality / "clip_0100_x.json").write_text(
        json.dumps(quality_doc([qseg(0, 10, "move", horizon_deg=0.5)], file_name="q.mp4")),
        encoding="utf-8",
    )
    gt = {"clips": {"0100": gt_clip([{"start": "00:00", "end": "00:10", "expect_role": "move"}])}}
    report = eval_labels.evaluate(gt, tmp_path)
    assert report["clips"][0]["file"] == "q.mp4"


def test_totals_arithmetic():
    clips = [
        {"move": {"score": 1.0}, "role": None, "horizon": {"score": 0.0}, "events": None, "overall": 0.5},
        {"move": {"score": 0.0}, "role": {"score": 1.0}, "horizon": {"score": 1.0}, "events": {"score": 0.0}, "overall": 0.5},
    ]
    totals = eval_labels.totals_row(clips)
    assert totals["move"] == 0.5
    assert totals["role"] == 1.0
    assert totals["horizon"] == 0.5
    assert totals["events"] == 0.0
    assert totals["overall"] == 0.5
    assert totals["clips_scored"] == 2


def test_totals_null_component_when_no_clip_has_it():
    totals = eval_labels.totals_row(
        [{"move": None, "role": None, "horizon": None, "events": None, "overall": 0.0}]
    )
    assert totals["move"] is None
    assert totals["role"] is None
    assert totals["horizon"] is None
    assert totals["events"] is None
    assert totals["overall"] == 0.0


def test_fixture_run_json_validates_and_scores(capsys):
    code = eval_labels.main(
        [
            "--ground-truth",
            str(FIXTURES / "ground_truth_tiny.yaml"),
            "--workspace",
            str(FIXTURES / "workspace"),
            "--json",
        ]
    )
    assert code == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert validate(report, "eval") == []
    assert report["meta"]["llm_cost_usd"] == 0.0
    assert report["meta"]["wall_time_s"] >= 0.0
    by_id = {clip["clip_id"]: clip for clip in report["clips"]}
    assert by_id["0100"]["overall"] == 1.0
    assert by_id["0100"]["move"]["score"] == 1.0
    assert by_id["0101"]["move"]["score"] == 0.5
    assert by_id["0102"]["move"]["score"] == 0.0
    assert "missing motion output" in by_id["0102"]["notes"]
    assert report["totals"]["clips_scored"] == 3
    assert report["totals"]["role"] == 1.0
    assert report["totals"]["horizon"] == 1.0
    assert report["totals"]["events"] == 1.0


def test_table_output_layout(capsys):
    code = eval_labels.main(
        [
            "--ground-truth",
            str(FIXTURES / "ground_truth_tiny.yaml"),
            "--workspace",
            str(FIXTURES / "workspace"),
        ]
    )
    assert code == EXIT_OK
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0] == "clip    move  role  horizon  events  overall"
    assert lines[1].startswith("0100")
    assert lines[-1].startswith("TOTAL")
    assert lines[2].startswith("0101")
    assert lines[3].startswith("0102")


def test_out_written_and_overwritten(tmp_path):
    out = tmp_path / "report.json"
    args = [
        "--ground-truth",
        str(FIXTURES / "ground_truth_tiny.yaml"),
        "--workspace",
        str(FIXTURES / "workspace"),
        "--out",
        str(out),
    ]
    assert eval_labels.main(args) == EXIT_OK
    first = json.loads(out.read_text(encoding="utf-8"))
    assert eval_labels.main(args) == EXIT_OK
    second = json.loads(out.read_text(encoding="utf-8"))
    for report in (first, second):
        report.pop("generated_at")
        report["meta"].pop("wall_time_s")
    assert first == second
    assert not (tmp_path / "report.json.tmp").exists()
    assert validate({**second, "generated_at": "2026-10-13T00:00:00+00:00"}, "eval") == []


def test_out_write_failure_exits_one(tmp_path, capsys):
    code = eval_labels.main(
        [
            "--ground-truth",
            str(FIXTURES / "ground_truth_tiny.yaml"),
            "--workspace",
            str(FIXTURES / "workspace"),
            "--out",
            str(tmp_path / "missing" / "report.json"),
        ]
    )
    assert code == EXIT_RUNTIME
    assert "[error]" in capsys.readouterr().err


def test_exit_one_missing_ground_truth(tmp_path, capsys):
    (tmp_path / "motion").mkdir()
    (tmp_path / "quality").mkdir()
    code = eval_labels.main(["--ground-truth", str(tmp_path / "nope.yaml"), "--workspace", str(tmp_path)])
    assert code == EXIT_USAGE
    assert "[error]" in capsys.readouterr().err


def test_exit_one_missing_workspace(tmp_path, capsys):
    ground_truth = tmp_path / "gt.yaml"
    ground_truth.write_text("clips: {}\n", encoding="utf-8")
    code = eval_labels.main(
        ["--ground-truth", str(ground_truth), "--workspace", str(tmp_path / "nope")]
    )
    assert code == EXIT_USAGE
    assert "[error]" in capsys.readouterr().err


def test_exit_one_unparseable_ground_truth(tmp_path, capsys):
    ground_truth = tmp_path / "gt.yaml"
    ground_truth.write_text("clips: [\n", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    code = eval_labels.main(["--ground-truth", str(ground_truth), "--workspace", str(workspace)])
    assert code == EXIT_USAGE
    assert "[error]" in capsys.readouterr().err


def shotlog_doc(shots):
    return {
        "generated_at": "2026-10-13T00:00:00+00:00",
        "weights": {"vision": 0.55, "labels": 0.45},
        "shots": shots,
    }


def shot(
    clip,
    start,
    end,
    motion_label="static",
    label_source="geometry",
    role="move",
    role_source="geometry",
):
    return {
        "key": f"{clip}/s1",
        "clip": clip,
        "segment_id": "s1",
        "start_s": start,
        "end_s": end,
        "eligible": True,
        "role": role,
        "role_source": role_source,
        "motion_label": motion_label,
        "label_source": label_source,
        "score": 0.5,
        "scores": {"labels": 0.5},
        "hook": "hook",
        "reason": "reason",
    }


def write_shotlog(workspace, shots):
    (workspace / "shotlog.json").write_text(json.dumps(shotlog_doc(shots)), encoding="utf-8")


def test_shotlog_move_label_beats_geometry(tmp_path):
    motion = tmp_path / "motion"
    motion.mkdir()
    (tmp_path / "quality").mkdir()
    (motion / "clip_0100_x.json").write_text(
        json.dumps(motion_doc([move(0, 10, "pan_left")])), encoding="utf-8"
    )
    write_shotlog(
        tmp_path,
        [shot("clip_0100_x.mp4", 0, 10, motion_label="travel_backward", label_source="verdict")],
    )
    gt = {"clips": {"0100": gt_clip([{"start": "00:00", "end": "00:10", "expect_move": "travel_backward"}])}}
    geometry = eval_labels.evaluate(gt, tmp_path, labels="geometry")
    shotlog = eval_labels.evaluate(gt, tmp_path, labels="shotlog")
    assert geometry["clips"][0]["move"]["score"] == 0.0
    assert shotlog["clips"][0]["move"]["score"] == 1.0


def test_shotlog_role_from_verdict(tmp_path):
    (tmp_path / "motion").mkdir()
    quality = tmp_path / "quality"
    quality.mkdir()
    (quality / "clip_0100_x.json").write_text(
        json.dumps(quality_doc([qseg(0, 10, "move")])), encoding="utf-8"
    )
    write_shotlog(tmp_path, [shot("clip_0100_x.mp4", 0, 10, role="hold", role_source="verdict")])
    gt = {"clips": {"0100": gt_clip([{"start": "00:00", "end": "00:10", "expect_role": "hold"}])}}
    geometry = eval_labels.evaluate(gt, tmp_path, labels="geometry")
    shotlog = eval_labels.evaluate(gt, tmp_path, labels="shotlog")
    assert geometry["clips"][0]["role"]["score"] == 0.0
    assert shotlog["clips"][0]["role"]["score"] == 1.0


def test_auto_picks_shotlog_when_present(tmp_path):
    motion = tmp_path / "motion"
    motion.mkdir()
    (tmp_path / "quality").mkdir()
    (motion / "clip_0100_x.json").write_text(
        json.dumps(motion_doc([move(0, 10, "pan_left")])), encoding="utf-8"
    )
    write_shotlog(
        tmp_path,
        [shot("clip_0100_x.mp4", 0, 10, motion_label="travel_backward", label_source="verdict")],
    )
    gt = {"clips": {"0100": gt_clip([{"start": "00:00", "end": "00:10", "expect_move": "travel_backward"}])}}
    report = eval_labels.evaluate(gt, tmp_path, labels="auto")
    assert report["clips"][0]["move"]["score"] == 1.0
    assert any(note.startswith("labels: shotlog") for note in report["clips"][0]["notes"])


def test_auto_falls_back_to_geometry_without_shotlog(tmp_path):
    motion = tmp_path / "motion"
    motion.mkdir()
    (tmp_path / "quality").mkdir()
    (motion / "clip_0100_x.json").write_text(
        json.dumps(motion_doc([move(0, 10, "pan_left")])), encoding="utf-8"
    )
    gt = {"clips": {"0100": gt_clip([{"start": "00:00", "end": "00:10", "expect_move": "travel_backward"}])}}
    report = eval_labels.evaluate(gt, tmp_path, labels="auto")
    assert report["clips"][0]["move"]["score"] == 0.0
    assert "labels: geometry" in report["clips"][0]["notes"]


def test_labels_shotlog_without_shotlog_exits_one(tmp_path, capsys):
    (tmp_path / "motion").mkdir()
    (tmp_path / "quality").mkdir()
    ground_truth = tmp_path / "gt.yaml"
    ground_truth.write_text("clips: {}\n", encoding="utf-8")
    code = eval_labels.main(
        ["--ground-truth", str(ground_truth), "--workspace", str(tmp_path), "--labels", "shotlog"]
    )
    assert code == EXIT_USAGE
    assert "[error]" in capsys.readouterr().err


def test_geometry_report_matches_default_without_shotlog(tmp_path):
    motion = tmp_path / "motion"
    motion.mkdir()
    (tmp_path / "quality").mkdir()
    (motion / "clip_0100_x.json").write_text(
        json.dumps(motion_doc([move(0, 10, "pan_left")])), encoding="utf-8"
    )
    gt = {"clips": {"0100": gt_clip([{"start": "00:00", "end": "00:10", "expect_move": "pan_left"}])}}
    default = eval_labels.evaluate(gt, tmp_path, labels="auto", generated_at="2026-10-13T00:00:00+00:00")
    geometry = eval_labels.evaluate(gt, tmp_path, labels="geometry", generated_at="2026-10-13T00:00:00+00:00")
    assert default == geometry
    assert "labels: geometry" in geometry["clips"][0]["notes"]


def test_notes_name_the_label_source(tmp_path):
    motion = tmp_path / "motion"
    motion.mkdir()
    (tmp_path / "quality").mkdir()
    (motion / "clip_0100_x.json").write_text(
        json.dumps(motion_doc([move(0, 10, "pan_left")])), encoding="utf-8"
    )
    write_shotlog(
        tmp_path,
        [
            shot("clip_0100_x.mp4", 0, 5, motion_label="pan_left", label_source="geometry"),
            shot("clip_0100_x.mp4", 5, 10, motion_label="travel_backward", label_source="verdict"),
        ],
    )
    gt = {"clips": {"0100": gt_clip([{"start": "00:00", "end": "00:10", "expect_move": "travel_backward"}])}}
    shotlog = eval_labels.evaluate(gt, tmp_path, labels="shotlog")
    assert "labels: shotlog (1 of 2 segments by verdict)" in shotlog["clips"][0]["notes"]
    geometry = eval_labels.evaluate(gt, tmp_path, labels="geometry")
    assert "labels: geometry" in geometry["clips"][0]["notes"]


def test_shotlog_clip_without_rows_keeps_null_components(tmp_path):
    (tmp_path / "motion").mkdir()
    (tmp_path / "quality").mkdir()
    write_shotlog(tmp_path, [shot("clip_9999_x.mp4", 0, 10, motion_label="pan_left")])
    gt = {
        "clips": {
            "0100": gt_clip(
                [{"start": "00:00", "end": "00:10", "expect_move": "pan_left", "expect_role": "move"}]
            )
        }
    }
    report = eval_labels.evaluate(gt, tmp_path, labels="shotlog")
    row = report["clips"][0]
    assert row["move"] is None
    assert row["role"] is None
    assert "labels: shotlog (0 of 0 segments by verdict)" in row["notes"]


def test_shotlog_report_validates_and_json_keys(tmp_path, capsys):
    motion = tmp_path / "motion"
    motion.mkdir()
    (tmp_path / "quality").mkdir()
    (motion / "clip_0100_x.json").write_text(
        json.dumps(motion_doc([move(0, 10, "pan_left")])), encoding="utf-8"
    )
    write_shotlog(
        tmp_path,
        [shot("clip_0100_x.mp4", 0, 10, motion_label="travel_backward", label_source="verdict")],
    )
    ground_truth = tmp_path / "gt.yaml"
    ground_truth.write_text(
        "clips:\n  \"0100\":\n    segments:\n"
        "      - {start: \"00:00\", end: \"00:10\", expect_move: travel_backward}\n",
        encoding="utf-8",
    )
    code = eval_labels.main(
        [
            "--ground-truth",
            str(ground_truth),
            "--workspace",
            str(tmp_path),
            "--labels",
            "shotlog",
            "--json",
        ]
    )
    assert code == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert validate(report, "eval") == []
    assert report["meta"]["llm_cost_usd"] == 0.0
    assert report["meta"]["wall_time_s"] >= 0.0
    assert report["clips"][0]["move"]["score"] == 1.0


def test_help_lists_labels(capsys):
    with pytest.raises(SystemExit) as excinfo:
        eval_labels.main(["--help"])
    assert excinfo.value.code == EXIT_OK
    out = capsys.readouterr().out
    assert "--labels" in out
    assert "auto" in out
    assert "shotlog" in out
    assert "geometry" in out
    assert "--labels shotlog" in out
