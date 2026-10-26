import json
from pathlib import Path

import pytest

import director_editor
from common.exit_codes import EXIT_OK, EXIT_USAGE
from common.moves import slot_duration
from common.schema import validate

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "director_editor.py"


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def segment(sid, start, end, role, motion_label="static", defects=None):
    return {
        "id": sid,
        "start_s": start,
        "end_s": end,
        "role": role,
        "confidence": 0.8,
        "motion_label": motion_label,
        "defects": defects or [],
    }


def quality_doc(file, duration, segments):
    return {"file": file, "duration_s": duration, "segments": segments}


def motion_doc(file, duration, moves=None, events=None):
    return {
        "file": file,
        "duration_s": duration,
        "analysis": {"method": "test", "source": "optical_flow", "sample_fps": 10},
        "trajectory": [],
        "phases": [],
        "moves": moves or [],
        "scene_activity": [],
        "events": events or [],
    }


def shotlog_shot(
    key,
    clip,
    segment_id,
    start,
    end,
    eligible,
    score,
    description,
    reason,
    similar_to=None,
    hero=None,
    role="hold",
    motion_label="static",
    semantic_motion=None,
    summary=None,
    peak_s=None,
):
    shot = {
        "key": key,
        "clip": clip,
        "segment_id": segment_id,
        "start_s": start,
        "end_s": end,
        "eligible": eligible,
        "role": role,
        "role_source": "geometry",
        "motion_label": motion_label,
        "label_source": "geometry",
        "score": score,
        "scores": {"labels": score},
        "description": description,
        "hook": description or segment_id,
        "reason": reason,
        "similar_to": similar_to or [],
        "hero": hero,
        "semantic_motion": semantic_motion,
        "summary": summary,
    }
    if peak_s is not None:
        shot["peak_s"] = peak_s
    return shot


def build_workspace(root, clips, quality_by_stem, motion_by_stem, shots, photos=None):
    write_json(
        root / "ingest.json",
        {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "source_dir": "/media",
            "clips": clips,
            "photos": photos or [],
        },
    )
    for stem, doc in quality_by_stem.items():
        write_json(root / "quality" / f"{stem}.json", doc)
    for stem, doc in motion_by_stem.items():
        write_json(root / "motion" / f"{stem}.json", doc)
    write_json(
        root / "shotlog.json",
        {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "weights": {"vision": 0.55, "labels": 0.45},
            "shots": shots,
        },
    )
    return root


def clip_record(name, creation_time):
    return {"file": name, "path": f"/media/{name}", "duration_s": 60.0, "creation_time": creation_time}


def hold_workspace(root, events=None, clip="clipA.mp4", start=0.0, end=20.0, score=0.6):
    stem = Path(clip).stem
    return build_workspace(
        root,
        [clip_record(clip, "2026-01-01T10:00:00")],
        {stem: quality_doc(clip, 60.0, [segment("s1", start, end, "hold")])},
        {stem: motion_doc(clip, 60.0, events=events)},
        [
            shotlog_shot(
                f"{stem}/s1", clip, "s1", start, end, True, score, "a hold",
                "interest 60 · static · hold · clean", summary="a hold",
            )
        ],
    )


def move_workspace(root, events=None, clip="clipA.mp4", start=0.0, end=20.0, score=0.9):
    stem = Path(clip).stem
    move = {
        "id": "m1",
        "start_s": start,
        "end_s": end,
        "label": "pan_right",
        "speed": "regular",
        "steadiness": "no_shaking",
        "steady_start_s": start,
        "steady_end_s": end,
        "handle_in_s": start,
        "handle_out_s": end,
        "confidence": 0.9,
    }
    return build_workspace(
        root,
        [clip_record(clip, "2026-01-01T10:00:00")],
        {stem: quality_doc(clip, 60.0, [segment("s1", start, end, "move", motion_label="pan_right")])},
        {stem: motion_doc(clip, 60.0, moves=[move], events=events)},
        [
            shotlog_shot(
                f"{stem}/s1", clip, "s1", start, end, True, score, "a move",
                "interest 90 · pan_right · move · clean", role="move", motion_label="pan_right", summary="a move",
            )
        ],
    )


def two_clip_workspace(root, first_end=20.0, second_end=20.0):
    return build_workspace(
        root,
        [clip_record("clipA.mp4", "2026-01-01T10:00:00"), clip_record("clipB.mp4", "2026-01-01T10:01:00")],
        {
            "clipA": quality_doc("clipA.mp4", 60.0, [segment("s1", 0.0, first_end, "hold")]),
            "clipB": quality_doc("clipB.mp4", 60.0, [segment("s1", 0.0, second_end, "hold")]),
        },
        {"clipA": motion_doc("clipA.mp4", 60.0), "clipB": motion_doc("clipB.mp4", 60.0)},
        [
            shotlog_shot("clipA/s1", "clipA.mp4", "s1", 0.0, first_end, True, 0.6, "a hold", "interest 60", summary="a hold"),
            shotlog_shot("clipB/s1", "clipB.mp4", "s1", 0.0, second_end, True, 0.7, "another hold", "interest 70", summary="another hold"),
        ],
    )


def landing_workspace(root):
    clip = "clipA.mp4"
    move = {
        "id": "m1",
        "start_s": 0.0,
        "end_s": 10.0,
        "label": "pan_right",
        "speed": "regular",
        "steadiness": "no_shaking",
        "steady_start_s": 0.0,
        "steady_end_s": 10.0,
        "handle_in_s": 0.0,
        "handle_out_s": 10.0,
        "confidence": 0.9,
    }
    return build_workspace(
        root,
        [clip_record(clip, "2026-01-01T10:00:00")],
        {
            "clipA": quality_doc(
                clip,
                60.0,
                [segment("s1", 0.0, 10.0, "move", motion_label="pan_right"), segment("s2", 10.0, 20.0, "hold")],
            )
        },
        {"clipA": motion_doc(clip, 60.0, moves=[move])},
        [
            shotlog_shot(
                "clipA/s1", clip, "s1", 0.0, 10.0, True, 0.9, "the move",
                "interest 90 · pan_right · move · clean", role="move", motion_label="pan_right", summary="the move",
            ),
            shotlog_shot("clipA/s2", clip, "s2", 10.0, 20.0, True, 0.5, "the hold", "interest 50", summary="the hold"),
        ],
    )


def photo_workspace(root):
    clip = "clipA.mp4"
    photo = {
        "file": "photo_01.jpg",
        "path": "/media/photo_01.jpg",
        "size_bytes": 100,
        "width": 64,
        "height": 48,
        "orientation": None,
        "creation_time": "2026-01-01T10:01:00+00:00",
        "creation_time_source": "exif",
        "format": "JPEG",
        "warnings": [],
    }
    photo_shot = {
        "key": "photo_01/p1",
        "clip": "photo_01.jpg",
        "segment_id": "p1",
        "start_s": 0.0,
        "end_s": 0.04,
        "media_type": "photo",
        "capture_time": "2026-01-01T10:01:00+00:00",
        "subject_position": {"x": 0.2, "y": 0.5},
        "eligible": True,
        "role": "hold",
        "role_source": "geometry",
        "motion_label": "static",
        "label_source": "geometry",
        "confidence": 0.7,
        "uncertain": False,
        "uncertain_reasons": [],
        "description": "a still",
        "summary": "lighthouse",
        "tags": [],
        "subject": "lighthouse",
        "scenery": "coast",
        "semantic_motion": "none",
        "interest": 70,
        "hero": None,
        "score": 0.8,
        "scores": {"vision": 0.8, "labels": 0.7},
        "hook": "lighthouse",
        "reason": "interest 70",
        "similar_to": [],
        "suppressed_by": None,
    }
    return build_workspace(
        root,
        [clip_record(clip, "2026-01-01T10:00:00")],
        {"clipA": quality_doc(clip, 60.0, [segment("s1", 0.0, 20.0, "hold")])},
        {"clipA": motion_doc(clip, 60.0)},
        [
            shotlog_shot("clipA/s1", clip, "s1", 0.0, 20.0, True, 0.5, "a hold", "interest 50", summary="a hold"),
            photo_shot,
        ],
        photos=[photo],
    )


def plan_doc(shots, target_duration_s=30.0, text="a test brief"):
    return {
        "schema_version": "1",
        "generated_at": "2026-10-25T21:00:00+00:00",
        "tool": "director",
        "brief": {
            "text": text,
            "target_duration_s": target_duration_s,
            "order": "chronological",
            "pacing": "calm-to-energy",
            "ambient_only": False,
        },
        "content_type": "vacation",
        "idea": "a test idea",
        "structure": {
            "kind": "frame",
            "why": "a test reason",
            "beats": [{"id": "b1", "name": "All", "intent": "test", "target_s": target_duration_s}],
        },
        "shots": shots,
    }


def plan_shot(sid, shot_key, technique="none", target_s=3.0, why="a test reason", moment=None, pieces=None, beat="b1"):
    shot = {
        "id": sid,
        "beat": beat,
        "shot_key": shot_key,
        "purpose": "test",
        "technique": technique,
        "target_s": target_s,
        "why": why,
    }
    if moment is not None:
        shot["moment"] = moment
    if pieces is not None:
        shot["pieces"] = pieces
    return shot


def write_plan(root, shots, **kwargs):
    path = root / "director_plan.json"
    write_json(path, plan_doc(shots, **kwargs))
    return path


def execute(root, plan_path, similar_fn=None, **kwargs):
    if similar_fn is None:
        similar_fn = lambda first, second: 0.0
    return director_editor.run_editor(root, plan_path, similar_fn=similar_fn, **kwargs)


def test_shot_keys_keep_the_plan_order_even_when_a_technique_cannot_be_done(tmp_path):
    root = two_clip_workspace(tmp_path / "ws", first_end=2.0)
    plan_path = write_plan(
        root,
        [
            plan_shot("v1", "clipA/s1", technique="jerky_approach", target_s=5.0, pieces=3),
            plan_shot("v2", "clipB/s1", technique="none", target_s=3.0),
        ],
    )
    edit, report, _ = execute(root, plan_path)
    assert [slot["shot_key"] for slot in edit["video"]] == ["clipA/s1", "clipB/s1"]
    assert report["shots"][0]["technique_done"] is None
    assert report["shots"][0]["why_not"]
    assert report["shots"][1]["technique_done"] == "none"


def test_every_shot_length_is_inside_its_bounds(tmp_path):
    root = two_clip_workspace(tmp_path / "ws", first_end=1.5)
    plan_path = write_plan(
        root,
        [
            plan_shot("v1", "clipA/s1", technique="none", target_s=5.0),
            plan_shot("v2", "clipB/s1", technique="none", target_s=8.0),
            plan_shot("v3", "clipB/s1", technique="none", target_s=1.0),
        ],
    )
    edit, report, _ = execute(root, plan_path)
    assert report["shots"][0]["length_s"] == pytest.approx(1.5)
    assert report["shots"][1]["length_s"] == pytest.approx(8.0)
    assert report["shots"][2]["length_s"] == pytest.approx(2.0)
    for slot in edit["video"]:
        assert slot["out_s"] > slot["in_s"]


def test_every_slot_stays_inside_the_shot_window(tmp_path):
    root = move_workspace(tmp_path / "ws", start=2.0, end=18.0)
    plan_path = write_plan(
        root,
        [
            plan_shot("v1", "clipA/s1", technique="none", target_s=4.0, moment={"at_s": 3.0}),
            plan_shot("v2", "clipA/s1", technique="jerky_approach", target_s=5.0, pieces=3),
            plan_shot("v3", "clipA/s1", technique="time_compression", target_s=4.0, pieces=3),
        ],
    )
    edit, _, _ = execute(root, plan_path)
    for slot in edit["video"]:
        assert slot["in_s"] >= 2.0 - 1e-9
        assert slot["out_s"] <= 18.0 + 1e-9


def test_a_move_with_a_landing_ends_at_or_after_the_floor(tmp_path):
    root = landing_workspace(tmp_path / "ws")
    plan_path = write_plan(
        root,
        [plan_shot("v1", "clipA/s1", technique="cut_on_motion", target_s=3.0, moment={"at_s": 2.0})],
    )
    edit, _, _ = execute(root, plan_path)
    assert edit["video"][0]["out_s"] >= 10.0 + 0.75 - 1e-9


def test_jerky_approach_has_forward_pieces_and_a_normal_entry(tmp_path):
    root = move_workspace(tmp_path / "ws")
    plan_path = write_plan(
        root,
        [plan_shot("v1", "clipA/s1", technique="jerky_approach", target_s=5.0, pieces=3)],
    )
    edit, report, _ = execute(root, plan_path)
    assert [slot["id"] for slot in edit["video"]] == ["v1", "v1p2", "v1p3"]
    pieces = report["shots"][0]["pieces"]
    assert len(pieces) == 3
    starts = [piece["in_s"] for piece in pieces]
    assert starts == sorted(starts)
    assert len(set(starts)) == 3
    assert pieces[-1]["out_s"] - pieces[-1]["in_s"] >= 1.0 - 1e-9
    assert report["shots"][0]["technique_done"] == "jerky_approach"


def test_clean_entry_starts_before_the_subject_enters(tmp_path):
    root = hold_workspace(tmp_path / "ws", events=[{"t_s": 5.0, "type": "subject_enters"}])
    plan_path = write_plan(root, [plan_shot("v1", "clipA/s1", technique="clean_entry", target_s=3.0)])
    edit, report, _ = execute(root, plan_path)
    slot = edit["video"][0]
    assert slot["in_s"] <= 5.0 - 0.5 + 1e-9
    assert slot["in_s"] < 5.0
    assert report["shots"][0]["technique_done"] == "clean_entry"


def test_clean_exit_ends_after_the_subject_left(tmp_path):
    root = hold_workspace(tmp_path / "ws", events=[{"t_s": 15.0, "type": "subject_leaves"}])
    plan_path = write_plan(root, [plan_shot("v1", "clipA/s1", technique="clean_exit", target_s=3.0)])
    edit, report, _ = execute(root, plan_path)
    slot = edit["video"][0]
    assert slot["out_s"] >= 15.0 + 0.3 - 1e-9
    assert report["shots"][0]["technique_done"] == "clean_exit"


def test_similar_neighbours_are_reported(tmp_path):
    root = two_clip_workspace(tmp_path / "ws")
    plan_path = write_plan(
        root,
        [
            plan_shot("v1", "clipA/s1", technique="none", target_s=3.0),
            plan_shot("v2", "clipB/s1", technique="none", target_s=3.0),
        ],
    )
    _, report, _ = execute(root, plan_path, similar_fn=lambda first, second: 0.95)
    assert report["similar_neighbours"] == [{"first": "v1", "second": "v2", "score": 0.95}]
    _, report, _ = execute(root, plan_path, similar_fn=lambda first, second: 0.5)
    assert report["similar_neighbours"] == []


def test_the_same_input_gives_the_same_output(tmp_path):
    root = move_workspace(tmp_path / "ws")
    plan_path = write_plan(
        root,
        [
            plan_shot("v1", "clipA/s1", technique="jerky_approach", target_s=5.0, pieces=3),
            plan_shot("v2", "clipA/s1", technique="clean_entry", target_s=3.0),
        ],
    )
    first_edit, first_report, _ = execute(root, plan_path)
    second_edit, second_report, _ = execute(root, plan_path)
    assert first_edit["video"] == second_edit["video"]
    assert first_report["shots"] == second_report["shots"]


def test_unknown_shot_key_is_a_clear_error(tmp_path):
    root = hold_workspace(tmp_path / "ws")
    plan_path = write_plan(root, [plan_shot("v1", "clipA/s9", technique="none", target_s=3.0)])
    with pytest.raises(director_editor.EditorError) as excinfo:
        execute(root, plan_path)
    assert "clipA/s9" in str(excinfo.value)
    assert director_editor.main(["--workspace", str(root), "--plan", str(plan_path), "--json"]) == EXIT_USAGE


def test_written_files_validate(tmp_path):
    root = move_workspace(tmp_path / "ws")
    plan_path = write_plan(root, [plan_shot("v1", "clipA/s1", technique="none", target_s=3.0)])
    execute(root, plan_path)
    edit = json.loads((root / "edit.json").read_text(encoding="utf-8"))
    report = json.loads((root / "editor_report.json").read_text(encoding="utf-8"))
    assert validate(edit, "edit") == []
    assert validate(report, "editor_report") == []


def test_a_photo_shot_gets_a_short_duration_and_a_ken_burns_move(tmp_path):
    root = photo_workspace(tmp_path / "ws")
    plan_path = write_plan(root, [plan_shot("p1", "photo_01/p1", technique="none", target_s=3.0)])
    edit, report, _ = execute(root, plan_path)
    slot = edit["video"][0]
    assert slot["kind"] == "photo"
    assert 2.0 <= slot["duration_s"] <= 4.0
    assert slot["ken_burns"]["move"] in {"zoom_in", "zoom_out", "pan_left", "pan_right"}
    assert report["shots"][0]["in_s"] is None
    assert report["shots"][0]["length_s"] == pytest.approx(slot["duration_s"])


def test_cuts_land_on_beats_with_a_grid_and_do_not_move_without_one(tmp_path):
    root = hold_workspace(tmp_path / "ws")
    plan_path = write_plan(
        root,
        [plan_shot("v1", "clipA/s1", technique="none", target_s=3.2, moment={"at_s": 10.0})],
    )
    edit, _, _ = execute(root, plan_path)
    assert edit["video"][0]["out_s"] == pytest.approx(11.6)
    beats_path = root / "beats.json"
    write_json(
        beats_path,
        {
            "beat_s": [round(0.5 * index, 4) for index in range(60)],
            "downbeat_s": [round(2.0 * index, 4) for index in range(15)],
            "beat_period_s": 0.5,
        },
    )
    edit, report, _ = execute(root, plan_path, beats_path=beats_path)
    cut = edit["video"][0]["out_s"]
    assert min(abs(cut - beat) for beat in [round(0.5 * index, 4) for index in range(60)]) <= 0.25
    assert report["shots"][0]["length_s"] == pytest.approx(cut - edit["video"][0]["in_s"])


def test_json_summary_and_failure_object(tmp_path, capsys):
    root = hold_workspace(tmp_path / "ws")
    plan_path = write_plan(root, [plan_shot("v1", "clipA/s1", technique="none", target_s=3.0)])
    assert director_editor.main(["--workspace", str(root), "--plan", str(plan_path), "--json"]) == EXIT_OK
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["ok"] is True
    assert summary["slots"] == 1
    assert "wall_time_s" in summary
    assert summary["llm_cost_usd"] == 0.0
    bad_plan = write_plan(root, [plan_shot("v1", "clipA/s9", technique="none", target_s=3.0)])
    assert director_editor.main(["--workspace", str(root), "--plan", str(bad_plan), "--json"]) == EXIT_USAGE
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["exit_code"] == EXIT_USAGE


def test_editor_cli_records_music_and_ambient(tmp_path):
    root = hold_workspace(tmp_path / "ws")
    plan_path = write_plan(root, [plan_shot("v1", "clipA/s1", technique="none", target_s=3.0)])
    music = tmp_path / "track.mp3"
    music.write_bytes(b"x")
    assert (
        director_editor.main(
            [
                "--workspace",
                str(root),
                "--plan",
                str(plan_path),
                "--music",
                str(music),
                "--ambient-under-music-db",
                "8",
                "--json",
            ]
        )
        == EXIT_OK
    )
    edit = json.loads((root / "edit.json").read_text(encoding="utf-8"))
    assert edit["audio"]["music"] == {"path": str(music)}
    assert edit["audio"]["ambient_under_music_db"] == 8


def test_editor_without_music_records_none(tmp_path):
    root = hold_workspace(tmp_path / "ws")
    plan_path = write_plan(root, [plan_shot("v1", "clipA/s1", technique="none", target_s=3.0)])
    edit, _, _ = execute(root, plan_path)
    assert edit["audio"]["music"] is None
    assert edit["audio"]["ambient_under_music_db"] is None


def test_speed_ramp_extends_a_move_and_keeps_the_target_length(tmp_path):
    root = move_workspace(tmp_path / "ws")
    plan_path = write_plan(root, [plan_shot("v1", "clipA/s1", technique="none", target_s=3.0)])
    edit, report, _ = execute(root, plan_path, speed_ramp=True)
    slot = edit["video"][0]
    assert slot["middle_speed"] > 1.0
    assert slot["out_s"] - slot["in_s"] > 3.0
    assert slot_duration(slot) == pytest.approx(3.0, abs=0.05)
    assert report["shots"][0]["length_s"] == pytest.approx(3.0, abs=0.05)


def test_without_speed_ramp_no_slot_gets_middle_speed(tmp_path):
    root = move_workspace(tmp_path / "ws")
    plan_path = write_plan(root, [plan_shot("v1", "clipA/s1", technique="none", target_s=3.0)])
    edit, _, _ = execute(root, plan_path)
    assert "middle_speed" not in edit["video"][0]
    assert edit["video"][0]["out_s"] - edit["video"][0]["in_s"] == pytest.approx(3.0)


def test_speed_ramp_never_touches_a_photo_or_a_static_hold(tmp_path):
    photo_root = photo_workspace(tmp_path / "photos")
    photo_plan = write_plan(photo_root, [plan_shot("p1", "photo_01/p1", technique="none", target_s=3.0)])
    photo_edit, _, _ = execute(photo_root, photo_plan, speed_ramp=True)
    assert "middle_speed" not in photo_edit["video"][0]
    hold_root = hold_workspace(tmp_path / "holds")
    hold_plan = write_plan(hold_root, [plan_shot("v1", "clipA/s1", technique="none", target_s=3.0)])
    hold_edit, _, _ = execute(hold_root, hold_plan, speed_ramp=True)
    assert "middle_speed" not in hold_edit["video"][0]


def test_speed_ramp_leaves_multi_piece_and_clean_techniques_at_normal_speed(tmp_path):
    root = move_workspace(tmp_path / "ws")
    plan_path = write_plan(
        root,
        [
            plan_shot("v1", "clipA/s1", technique="jerky_approach", target_s=5.0, pieces=3),
            plan_shot("v2", "clipA/s1", technique="time_compression", target_s=4.0, pieces=3),
        ],
    )
    edit, _, _ = execute(root, plan_path, speed_ramp=True)
    assert all("middle_speed" not in slot for slot in edit["video"])
