import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

import beats
import llm_director
from common.beatsnap import absorb_pair, snap_slots
from common.config import ConfigError
from common.facts import make_fact
from common.exit_codes import EXIT_USAGE


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "llm_director.py"

BRIEF = "about 90 seconds, chronological, calm start, more energy towards the end, ambient sound only"


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


def motion_doc(file, duration, moves=None):
    return {
        "file": file,
        "duration_s": duration,
        "analysis": {"method": "test", "source": "optical_flow", "sample_fps": 10},
        "trajectory": [],
        "phases": [],
        "moves": moves or [],
        "scene_activity": [],
        "events": [],
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
):
    return {
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


def build_workspace(root):
    clips = [
        {"file": "clip_0056_x.mp4", "path": "/media/clip_0056_x.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:00:00"},
        {"file": "clip_0057_x.mp4", "path": "/media/clip_0057_x.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:01:00"},
        {"file": "clip_0080_x.mp4", "path": "/media/clip_0080_x.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:02:00"},
    ]
    write_json(
        root / "ingest.json",
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips},
    )
    for clip in clips:
        stem = Path(clip["file"]).stem
        write_json(root / "quality" / f"{stem}.json", quality_doc(clip["file"], 60.0, [segment("s1", 0.0, 20.0, "hold")]))
        write_json(root / "motion" / f"{stem}.json", motion_doc(clip["file"], 60.0))
    write_json(
        root / "shotlog.json",
        {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "weights": {"vision": 0.55, "labels": 0.45},
            "shots": [
                shotlog_shot("0056/s1", "clip_0056_x.mp4", "s1", 0.0, 20.0, True, 0.6, "the wake", "interest 60 · static by geometry · hold · clean", summary="the wake"),
                shotlog_shot("0057/s1", "clip_0057_x.mp4", "s1", 0.0, 20.0, True, 0.7, "the bay", "interest 70 · static by geometry · hold · clean", summary="the bay"),
                shotlog_shot(
                    "0080/s1",
                    "clip_0080_x.mp4",
                    "s1",
                    0.0,
                    20.0,
                    True,
                    0.9,
                    "the cave",
                    "interest 90 · hero · travel_forward by geometry · move · clean",
                    hero=True,
                    role="move",
                    motion_label="travel_forward",
                    semantic_motion="travel",
                    summary="the cave",
                ),
            ],
        },
    )
    return root


def build_phrase_workspace(root):
    clips = [{"file": "clipA.mp4", "path": "/media/clipA.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:00:00"}]
    write_json(
        root / "ingest.json",
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips},
    )
    write_json(
        root / "quality" / "clipA.json",
        quality_doc(
            "clipA.mp4",
            60.0,
            [
                segment("s1", 0.0, 10.0, "move", motion_label="pan_right"),
                segment("s2", 10.0, 20.0, "hold"),
            ],
        ),
    )
    write_json(
        root / "motion" / "clipA.json",
        motion_doc(
            "clipA.mp4",
            60.0,
            [
                {
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
            ],
        ),
    )
    write_json(
        root / "shotlog.json",
        {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "weights": {"vision": 0.55, "labels": 0.45},
            "shots": [
                shotlog_shot("clipA/s1", "clipA.mp4", "s1", 0.0, 10.0, True, 0.9, "the move", "interest 90 · pan_right · move · clean", role="move", motion_label="pan_right"),
                shotlog_shot("clipA/s2", "clipA.mp4", "s2", 10.0, 20.0, True, 0.5, "the hold", "interest 50 · static · hold · clean"),
            ],
        },
    )
    return root


def write_photo(path, size=(64, 48), color=(120, 80, 40)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG")
    return path


def photo_ingest_record(path, creation_time="2026-01-01T10:01:00+00:00"):
    return {
        "file": path.name,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "width": 64,
        "height": 48,
        "orientation": None,
        "creation_time": creation_time,
        "creation_time_source": "exif",
        "format": "JPEG",
        "warnings": [],
    }


def photo_shot(
    key,
    name,
    capture_time,
    score,
    subject_position=None,
    tags=None,
    hero=None,
    eligible=True,
    similar_to=None,
):
    return {
        "key": key,
        "clip": name,
        "segment_id": "p1",
        "start_s": 0.0,
        "end_s": 0.04,
        "media_type": "photo",
        "capture_time": capture_time,
        "subject_position": subject_position,
        "eligible": eligible,
        "role": "hold",
        "role_source": "geometry",
        "motion_label": "static",
        "label_source": "geometry",
        "confidence": 0.7,
        "uncertain": False,
        "uncertain_reasons": [],
        "description": "a still",
        "summary": "lighthouse",
        "tags": tags or [],
        "subject": "lighthouse",
        "scenery": "coast",
        "semantic_motion": "none",
        "interest": 70,
        "hero": hero,
        "score": score,
        "scores": {"vision": score, "labels": 0.7},
        "hook": "lighthouse",
        "reason": "interest 70 · static by geometry · hold · clean",
        "similar_to": similar_to or [],
        "suppressed_by": None,
    }


def build_photo_workspace(root, photo_path, photo_shot_record, video_shots=None):
    clips = [
        {"file": "clipA.mp4", "path": "/media/clipA.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:00:00"},
    ]
    write_json(
        root / "ingest.json",
        {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "source_dir": "/media",
            "clips": clips,
            "photos": [photo_ingest_record(photo_path)],
        },
    )
    write_json(root / "quality" / "clipA.json", quality_doc("clipA.mp4", 60.0, [segment("s1", 0.0, 20.0, "hold")]))
    write_json(root / "motion" / "clipA.json", motion_doc("clipA.mp4", 60.0))
    shots = video_shots or [
        shotlog_shot("clipA/s1", "clipA.mp4", "s1", 0.0, 20.0, True, 0.5, "a hold", "interest 50 · static by geometry · hold · clean"),
    ]
    write_json(
        root / "shotlog.json",
        {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "weights": {"vision": 0.55, "labels": 0.45},
            "shots": shots + [photo_shot_record],
        },
    )
    return root


class StubResponse:
    def __init__(self, text, cost_usd=0.001, model="stub/director"):
        self.text = text
        self.cost_usd = cost_usd
        self.model = model
        self.role = "director"
        self.prompt_tokens = 0
        self.completion_tokens = 0


class StubClient:
    def __init__(self, answers, cost_per_call=0.001):
        self.answers = list(answers)
        self.calls = []
        self.cost_per_call = cost_per_call

    def complete(self, role, messages, **kwargs):
        self.calls.append({"role": role, "messages": messages, "kwargs": kwargs})
        if not self.answers:
            raise AssertionError("stub client ran out of answers")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer(messages)
        return StubResponse(answer, cost_usd=self.cost_per_call)


def test_missing_brief_raises(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    with pytest.raises(llm_director.LLMDirectorError):
        llm_director.run(workspace)


def test_shot_facts_phrase_window(tmp_path):
    workspace = build_phrase_workspace(tmp_path / "ws")
    shotlog = llm_director.load_shotlog(workspace)
    facts, _, _ = llm_director.build_shot_facts(workspace, shotlog)
    fact = facts["clipA/s1"]
    assert fact["window_in_s"] == 0.0
    assert fact["window_out_s"] == 20.0
    assert fact["landing_at_s"] == 10.0
    assert fact["landing_hold_key"] == "clipA/s2"


def fact(key, clip, window_in, window_out, keep_whole=False, similar_to=None, score=0.5, hero=False, role="hold", motion_label="static", media_type=None, segment_id=None):
    row = {
        "shot_key": key,
        "clip": clip,
        "segment_id": segment_id,
        "window_in_s": window_in,
        "window_out_s": window_out,
        "landing_at_s": None,
        "landing_hold_key": None,
        "keep_whole": keep_whole,
        "target_s": 5.0,
        "score": score,
        "summary": None,
        "role": role,
        "motion_label": motion_label,
        "hero": hero,
        "similar_to": similar_to or [],
    }
    if media_type is not None:
        row["media_type"] = media_type
    return row


def test_print_human_with_baseline(capsys):
    summary = {
        "total_duration_s": 12.0,
        "warnings": ["a warning"],
        "baseline": {"slots": 2, "total_duration_s": 12.0, "reasons": ["first reason", "second reason"]},
    }
    llm_director.print_human(summary, None)
    out = capsys.readouterr().out
    assert "baseline (rules): 2 slots, 12.0s" in out
    assert "first reason" in out
    assert "warning: a warning" in out


def test_cli_missing_brief_exits_2(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--workspace", str(workspace)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == EXIT_USAGE
    assert "[error]" in result.stderr


def test_cli_help_exits_0():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--dry-run" in result.stdout


def build_beats_workspace(root):
    clips = [
        {"file": "clipA.mp4", "path": "/media/clipA.mp4", "duration_s": 160.0, "creation_time": "2026-01-01T10:00:00"},
        {"file": "clipB.mp4", "path": "/media/clipB.mp4", "duration_s": 60.0, "creation_time": "2026-01-01T10:03:00"},
    ]
    write_json(
        root / "ingest.json",
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips},
    )
    write_json(
        root / "quality" / "clipA.json",
        quality_doc("clipA.mp4", 160.0, [segment("s1", 0.0, 160.0, "move", motion_label="travel_forward")]),
    )
    write_json(
        root / "motion" / "clipA.json",
        motion_doc(
            "clipA.mp4",
            160.0,
            [{
                "id": "m1", "start_s": 10.0, "end_s": 150.0, "label": "travel_forward",
                "speed": "regular", "steadiness": "no_shaking",
                "steady_start_s": 10.0, "steady_end_s": 150.0, "handle_in_s": 10.0, "handle_out_s": 150.0,
                "confidence": 0.9,
            }],
        ),
    )
    write_json(
        root / "quality" / "clipB.json",
        quality_doc("clipB.mp4", 60.0, [segment("s1", 0.0, 20.0, "hold")]),
    )
    write_json(root / "motion" / "clipB.json", motion_doc("clipB.mp4", 60.0, []))
    first = shotlog_shot(
        "clipA/s1b1", "clipA.mp4", "s1b1", 0.0, 60.0, True, 0.95, "approach",
        "approach · interest 95", role="move", motion_label="travel_forward",
    )
    first.update({"beat_of": "s1", "beat_id": "b1", "beat_label": "approach", "summary": "boat approaches the tunnel"})
    second = shotlog_shot(
        "clipA/s1b2", "clipA.mp4", "s1b2", 60.0, 160.0, True, 0.55, "exit",
        "exit · interest 55", role="move", motion_label="travel_forward",
    )
    second.update({"beat_of": "s1", "beat_id": "b2", "beat_label": "exit", "summary": "tunnel exit"})
    other = shotlog_shot(
        "clipB/s1", "clipB.mp4", "s1", 0.0, 20.0, True, 0.7, "the other take", "interest 70",
    )
    write_json(
        root / "shotlog.json",
        {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "weights": {"vision": 0.55, "labels": 0.45},
            "shots": [first, second, other],
        },
    )
    return root


def test_shot_facts_one_per_beat(tmp_path):
    workspace = build_beats_workspace(tmp_path / "ws")
    shotlog = llm_director.load_shotlog(workspace)
    facts, _, _ = llm_director.build_shot_facts(workspace, shotlog)
    assert set(facts) == {"clipA/s1b1", "clipA/s1b2", "clipB/s1"}
    first = facts["clipA/s1b1"]
    assert first["window_in_s"] == 10.0
    assert first["window_out_s"] == 60.0
    assert first["summary"] == "boat approaches the tunnel"
    second = facts["clipA/s1b2"]
    assert second["window_in_s"] == 60.0
    assert second["window_out_s"] == 150.0
    assert facts["clipB/s1"]["window_out_s"] == 20.0


def test_window_ends_at_the_steady_motion_end(tmp_path):
    clips = [{"file": "clipA.mp4", "path": "/media/clipA.mp4", "duration_s": 30.0, "creation_time": "2026-01-01T10:00:00"}]
    write_json(
        tmp_path / "ingest.json",
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips},
    )
    write_json(
        tmp_path / "quality" / "clipA.json",
        quality_doc("clipA.mp4", 30.0, [segment("s1", 0.0, 30.0, "move", motion_label="pan_right")]),
    )
    write_json(
        tmp_path / "motion" / "clipA.json",
        motion_doc(
            "clipA.mp4",
            30.0,
            [{
                "id": "m1", "start_s": 0.0, "end_s": 30.0, "label": "pan_right",
                "speed": "regular", "steadiness": "no_shaking",
                "steady_start_s": 2.0, "steady_end_s": 18.0, "handle_in_s": 1.75, "handle_out_s": 25.0,
                "confidence": 0.9,
            }],
        ),
    )
    write_json(
        tmp_path / "shotlog.json",
        {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "weights": {"vision": 0.55, "labels": 0.45},
            "shots": [
                shotlog_shot("clipA/s1", "clipA.mp4", "s1", 0.0, 30.0, True, 0.9, "the move", "interest 90 · pan_right", role="move", motion_label="pan_right"),
            ],
        },
    )
    shotlog = llm_director.load_shotlog(tmp_path)
    facts, _, _ = llm_director.build_shot_facts(tmp_path, shotlog)
    assert facts["clipA/s1"]["window_in_s"] == 1.75
    assert facts["clipA/s1"]["window_out_s"] == 18.0


def test_important_cap_defaults_to_six():
    assert llm_director.DEFAULT_IMPORTANT_MAX_S == 6.0


def test_pacing_table_at_the_anchors():
    assert llm_director.pacing_table(20) == {
        "slot_min": 1.5,
        "slot_target": 2.0,
        "slot_max": 2.5,
        "hero_max": 3.5,
        "n_slots": 10,
    }
    assert llm_director.pacing_table(90) == {
        "slot_min": 2.0,
        "slot_target": 3.6,
        "slot_max": 5.0,
        "hero_max": 6.0,
        "n_slots": 25,
    }
    assert llm_director.pacing_table(180) == {
        "slot_min": 2.5,
        "slot_target": 5.0,
        "slot_max": 6.0,
        "hero_max": 6.0,
        "n_slots": 32,
    }


def test_pacing_table_clamps_outside_the_anchors():
    assert llm_director.pacing_table(10) == llm_director.pacing_table(20)
    assert llm_director.pacing_table(240) == llm_director.pacing_table(180)


def test_pacing_table_interpolates_between_the_anchors():
    pacing = llm_director.pacing_table(60)
    assert 1.5 < pacing["slot_min"] < 2.0
    assert 2.0 < pacing["slot_target"] < 3.6
    assert 2.5 < pacing["slot_max"] < 5.0
    assert 3.5 < pacing["hero_max"] < 6.0
    assert 10 < pacing["n_slots"] < 25


def test_shot_facts_respect_pacing_clamps_for_a_short_brief(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    shotlog = llm_director.load_shotlog(workspace)
    pacing = llm_director.pacing_table(20)
    facts, _, _ = llm_director.build_shot_facts(
        workspace, shotlog, pacing["hero_max"], None, pacing["slot_min"], pacing["slot_max"],
    )
    assert facts["0056/s1"]["target_s"] == 2.5
    assert facts["0057/s1"]["target_s"] == 2.5
    assert facts["0080/s1"]["target_s"] == 3.5
    assert facts["0080/s1"]["keep_whole"] is True


def test_photo_fact_shape(tmp_path):
    photo = write_photo(tmp_path / "media" / "photo_01.jpg")
    shot = photo_shot(
        "photo_01/p1",
        photo.name,
        "2026-01-01T10:01:00+00:00",
        0.8,
        subject_position={"x": 0.2, "y": 0.5},
        tags=["coast"],
    )
    workspace = build_photo_workspace(tmp_path / "ws", photo, shot)
    shotlog = llm_director.load_shotlog(workspace)
    facts, _, _ = llm_director.build_shot_facts(workspace, shotlog)
    fact = facts["photo_01/p1"]
    assert fact["media_type"] == "photo"
    assert fact["photo"] == photo.name
    assert fact["photo_path"] == str(photo)
    assert fact["capture_time"] == "2026-01-01T10:01:00+00:00"
    assert fact["subject_position"] == {"x": 0.2, "y": 0.5}
    assert fact["tags"] == ["coast"]
    assert fact["window_in_s"] == 0.0
    assert fact["window_out_s"] == 0.04
    assert fact["landing_at_s"] is None
    assert fact["keep_whole"] is False
    assert fact["target_s"] == 3.0


def test_burst_suppressed_photo_not_in_facts(tmp_path):
    photo = write_photo(tmp_path / "media" / "burst.jpg")
    shot = photo_shot("burst/p1", photo.name, "2026-01-01T10:01:00+00:00", 0.9, eligible=False)
    workspace = build_photo_workspace(tmp_path / "ws", photo, shot)
    shotlog = llm_director.load_shotlog(workspace)
    facts, _, _ = llm_director.build_shot_facts(workspace, shotlog)
    assert "burst/p1" not in facts


def beat_grid(period=0.5, count=60, downbeats=None, top_level=False):
    beats = [round(period * index, 4) for index in range(count)]
    down = downbeats if downbeats is not None else beats[::4]
    payload = {
        "beat_s": beats,
        "downbeat_s": down,
        "beat_period_s": period,
        "duration_s": round(beats[-1] + period, 3),
        "bpm": round(60.0 / period, 3),
    }
    return payload if top_level else {"measurement": payload}


def test_load_beats_accepts_both_shapes(tmp_path):
    for index, data in enumerate([beat_grid(top_level=False), beat_grid(top_level=True)]):
        path = tmp_path / f"beats{index}.json"
        write_json(path, data)
        grid = llm_director.load_beats(path)
        assert grid["beat_s"][0] == 0.0
        assert grid["beat_period_s"] == 0.5
        assert grid["downbeat_s"]


def test_beat_snap_downbeat_over_cap_falls_back_to_a_beat():
    beat_s = [round(0.5 * index, 4) for index in range(20)]
    grid = {"beat_s": beat_s, "downbeat_s": [2.0, 4.0, 6.0, 8.0], "beat_period_s": 0.5}
    slots = [{"id": "v1", "clip": "a.mp4", "in_s": 0.0, "out_s": 1.9}]
    bounds = [
        {"photo": False, "important": False, "low": 0.5, "high": 2.0, "window_in": 0.0, "cap": 1.5}
    ]
    warnings = []
    stats = snap_slots(slots, bounds, grid, warnings)
    assert slots[0]["out_s"] == 1.5
    assert slots[0]["beat_grid"] == "beat"
    assert stats["unsnapped"] == 0
    assert not [warning for warning in warnings if "not snapped" in warning]


def test_help_lists_beats():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--beats" in result.stdout


def test_absorb_pair_rollback_restores_a_video_slot_exactly():
    left = {"id": "v5", "clip": "x.mp4", "in_s": 6.535, "out_s": 10.465, "speed": 1.0}
    right = {"id": "v6", "clip": "x.mp4", "in_s": 20.0, "out_s": 20.9, "speed": 1.0}
    moved = absorb_pair(
        left,
        {"low": 6.0, "high": 12.0, "window_in": 6.0},
        right,
        {"low": 20.0, "high": 21.0, "window_in": 20.0},
        1.0,
    )
    assert moved is False
    assert left == {"id": "v5", "clip": "x.mp4", "in_s": 6.535, "out_s": 10.465, "speed": 1.0}
    assert "duration_s" not in left


def test_help_lists_feedback():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--feedback" in result.stdout
    assert "drop 0071/s2" in result.stdout


def test_facts_carry_the_peak_and_where_it_came_from(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    shotlog = llm_director.load_shotlog(workspace)
    shotlog["0056/s1"]["peak_s"] = 4.5
    path = workspace / "motion" / "clip_0057_x.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["scene_activity"] = [{"t_s": index + 0.5, "activity": 0.002} for index in range(20)]
    data["scene_activity"][12]["activity"] = 0.02
    path.write_text(json.dumps(data), encoding="utf-8")
    facts, _, _ = llm_director.build_shot_facts(workspace, shotlog)
    assert (facts["0056/s1"]["peak_s"], facts["0056/s1"]["peak_source"]) == (4.5, "vision")
    assert (facts["0057/s1"]["peak_s"], facts["0057/s1"]["peak_source"]) == (12.5, "motion")
    assert (facts["0080/s1"]["peak_s"], facts["0080/s1"]["peak_source"]) == (10.0, "middle")


def test_previous_edit_context_carries_the_shot_words(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_json(
        workspace / "shotlog.json",
        {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "weights": {"vision": 0.55, "labels": 0.45},
            "shots": [
                {
                    "key": "0072/s7b1",
                    "clip": "clip_0072_x.mp4",
                    "segment_id": "s7",
                    "summary": "the bunker entrance",
                    "subject": "a concrete bunker",
                    "tags": ["bunker", "entrance"],
                },
                {
                    "key": "0072/s7b2",
                    "clip": "clip_0072_x.mp4",
                    "segment_id": "s7",
                    "summary": "inside the bunker",
                    "subject": "the dark corridor",
                    "tags": ["bunker", "inside"],
                },
            ],
        },
    )
    write_json(
        workspace / "edit.json",
        {
            "video": [
                {
                    "id": "v1",
                    "shot_key": "0072/s7b1",
                    "clip": "clip_0072_x.mp4",
                    "in_s": 0.0,
                    "out_s": 4.0,
                    "reason": "the bunker entrance",
                }
            ],
            "decisions": [
                {"id": "0072/s7b2", "status": "rejected", "reason": "pointless jump cut: another beat of 0072/s7b1"}
            ],
        },
    )
    context = llm_director.previous_edit_context(workspace)
    used = context["used"][0]
    assert used["summary"] == "the bunker entrance"
    assert used["subject"] == "a concrete bunker"
    assert used["tags"] == ["bunker", "entrance"]
    rejected = context["rejected"][0]
    assert rejected["summary"] == "inside the bunker"
    assert rejected["subject"] == "the dark corridor"


def test_previous_edit_context_without_a_shotlog_never_raises(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_json(
        workspace / "edit.json",
        {
            "video": [
                {"id": "v1", "shot_key": "0072/s7b1", "clip": "clip_0072_x.mp4", "in_s": 0.0, "out_s": 4.0, "reason": "a"}
            ],
            "decisions": [{"id": "0072/s7b2", "status": "rejected", "reason": "low score"}],
        },
    )
    context = llm_director.previous_edit_context(workspace)
    assert context["used"][0]["summary"] is None
    assert context["used"][0]["subject"] is None
    assert context["used"][0]["tags"] == []
    assert context["rejected"][0]["summary"] is None
    assert context["rejected"][0]["subject"] is None


def test_last_director_estimate_reads_run_log_and_spend(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    marker_utc = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    marker_local = marker_utc.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    (workspace / "run.log").write_text(
        f"=== recap run started {marker_local} ===\n"
        "[ 9/12] llm_director    done      12.3s  written\n",
        encoding="utf-8",
    )
    write_json(
        workspace / "llm_spend.json",
        {
            "total_usd": 0.5,
            "entries": [
                {"ts": "2026-01-01T09:00:00+00:00", "role": "director", "cost_usd": 0.4},
                {"ts": "2026-01-01T10:00:01+00:00", "role": "director", "cost_usd": 0.1},
                {"ts": "2026-01-01T10:00:02+00:00", "role": "metadata", "cost_usd": 0.2},
            ],
        },
    )
    estimate_usd, estimate_s = llm_director.last_director_estimate(workspace)
    assert estimate_s == 12.3
    assert estimate_usd == pytest.approx(0.1)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert llm_director.last_director_estimate(empty) == (None, None)


def test_reasoning_effort_flag_is_accepted():
    args = llm_director.build_parser().parse_args(
        ["--workspace", "ws", "--reasoning-effort", "high"]
    )
    assert args.reasoning_effort == "high"


def test_reasoning_effort_unknown_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        llm_director.build_parser().parse_args(
            ["--workspace", "ws", "--reasoning-effort", "bogus"]
        )
    assert exc.value.code == 2


def test_call_client_passes_the_reasoning_effort():
    client = StubClient([json.dumps({"ok": True})])
    state = llm_director.LoopState()
    state.reasoning_effort = "low"
    llm_director.call_client(client, [{"role": "user", "content": "x"}], state)
    assert client.calls[0]["kwargs"] == {"extra_body": {"reasoning": {"effort": "low"}}}


def test_call_client_off_disables_reasoning():
    client = StubClient([json.dumps({"ok": True})])
    state = llm_director.LoopState()
    state.reasoning_effort = "off"
    llm_director.call_client(client, [{"role": "user", "content": "x"}], state)
    assert client.calls[0]["kwargs"] == {"extra_body": {"reasoning": {"enabled": False}}}


def test_call_client_without_effort_sends_no_extra_body():
    client = StubClient([json.dumps({"ok": True})])
    state = llm_director.LoopState()
    llm_director.call_client(client, [{"role": "user", "content": "x"}], state)
    assert client.calls[0]["kwargs"] == {}


def test_run_rejects_an_unknown_reasoning_effort(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([])
    with pytest.raises(ConfigError):
        llm_director.run(workspace, brief=BRIEF, client=client, reasoning_effort="bogus")


def test_shortlist_rows_keeps_stars_and_top_scores_in_order():
    rows = [
        {"key": f"{index:04d}/s1", "score": 0.5 + (index % 30) / 100.0, "hero": index < 5}
        for index in range(100)
    ]
    result = llm_director.shortlist_rows(rows, 25)
    assert len(result) <= llm_director.SHORTLIST_MAX
    assert len(result) < len(rows)
    keys = [row["key"] for row in result]
    assert keys == [row["key"] for row in rows if row["key"] in set(keys)]
    for index in range(5):
        assert f"{index:04d}/s1" in keys


def test_shortlist_includes_each_clips_best_eligible_segment():
    rows = []
    for clip_index in range(5):
        for row_index in range(10):
            score = 0.80 - 0.01 * (clip_index * 10 + row_index)
            rows.append(
                {
                    "key": f"c{clip_index}/s{row_index}",
                    "clip": f"c{clip_index}",
                    "score": score,
                    "eligible": True,
                }
            )
    result = llm_director.shortlist_rows(rows, 5)
    keys = [row["key"] for row in result]
    assert keys == [row["key"] for row in rows if row["key"] in set(keys)]
    for clip_index in range(5):
        best = max(
            (row for row in rows if row["clip"] == f"c{clip_index}"),
            key=lambda row: row["score"],
        )
        assert best["key"] in keys


def test_shortlist_skips_whip_short_hold_best():
    rows = []
    for clip_index in range(5):
        for row_index in range(10):
            score = 0.80 - 0.01 * (clip_index * 10 + row_index)
            rows.append(
                {
                    "key": f"c{clip_index}/s{row_index}",
                    "clip": f"c{clip_index}",
                    "score": score,
                    "eligible": True,
                }
            )
    rows.append({"key": "c9/s0", "clip": "c9", "score": 0.50, "eligible": True, "whip": "short_hold"})
    rows.append({"key": "c9/s1", "clip": "c9", "score": 0.05, "eligible": True})
    result = llm_director.shortlist_rows(rows, 5)
    keys = [row["key"] for row in result]
    assert "c9/s0" not in keys
    assert "c9/s1" in keys


def test_shot_fact_carries_faces():
    candidate = {
        "shot_key": "clipA/s1",
        "clip": "clipA.mp4",
        "in_s": 0.0,
        "out_s": 10.0,
        "segment": {"faces_max": 3, "face_size_max": 0.2},
        "role": "hold",
        "motion_label": "static",
        "similar_to": [],
    }
    fact = make_fact(candidate, 6.0)
    assert fact["faces_max"] == 3
    assert fact["face_size_max"] == pytest.approx(0.2)
