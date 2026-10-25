import json
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

import beats
import llm_director
from common.llm import BudgetExceeded
from common.moves import slot_screen_length
from common.photos import ken_burns_for
from common.schema import validate
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME
from helpers import make_click

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "llm_director.py"
FIXTURES = ROOT / "tests" / "fixtures"

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


def photo_plan_answer(shot_key="photo_01/p1"):
    return json.dumps(
        {
            "logline": "a still and a clip",
            "structure": [{"id": "b1", "name": "Open", "intent": "open calm", "target_s": 8.0}],
            "slot_plan": [
                {"id": "v1", "beat": "b1", "shot_key": "clipA/s1", "target_s": 5.0, "why": "the hold", "keep_whole": False},
                {"id": "p1", "beat": "b1", "shot_key": shot_key, "target_s": 3.0, "why": "the still", "keep_whole": False},
            ],
        }
    )


def photo_editor_answer(shot_key="photo_01/p1", duration_s=3.0):
    return json.dumps(
        {
            "slots": [
                {"id": "v1", "shot_key": "clipA/s1", "in_s": 2.0, "out_s": 7.0, "transition": "cut"},
                {"id": "p1", "shot_key": shot_key, "duration_s": duration_s, "transition": "crossfade"},
            ]
        }
    )


def photo_editor_answer_missing_photo():
    return json.dumps(
        {
            "slots": [
                {"id": "v1", "shot_key": "clipA/s1", "in_s": 2.0, "out_s": 7.0, "transition": "cut"},
            ]
        }
    )


def photo_fact(key, name, subject_position=None, tags=None, hero=None, similar_to=None):
    return {
        "shot_key": key,
        "media_type": "photo",
        "photo": name,
        "photo_path": f"/media/{name}",
        "capture_time": "2026-01-01T10:01:00+00:00",
        "subject_position": subject_position,
        "tags": tags or [],
        "score": 0.8,
        "summary": "lighthouse",
        "hero": hero,
        "similar_to": similar_to or [],
        "window_in_s": 0.0,
        "window_out_s": 0.04,
        "landing_at_s": None,
        "landing_hold_key": None,
        "keep_whole": False,
        "target_s": 3.0,
        "role": "hold",
        "motion_label": "static",
        "description": "a still",
    }


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
        self.calls.append({"role": role, "messages": messages})
        if not self.answers:
            raise AssertionError("stub client ran out of answers")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer(messages)
        return StubResponse(answer, cost_usd=self.cost_per_call)


def plan_answer():
    return (FIXTURES / "shot_plan_valid.json").read_text(encoding="utf-8")


def editor_answer():
    return json.dumps(
        {
            "slots": [
                {"id": "v1", "shot_key": "0056/s1", "in_s": 2.0, "out_s": 7.0, "transition": "cut"},
                {"id": "v2", "shot_key": "0057/s1", "in_s": 2.0, "out_s": 8.0, "transition": "crossfade"},
                {"id": "v3", "shot_key": "0080/s1", "in_s": 12.0, "out_s": 20.0, "transition": "crossfade"},
            ]
        }
    )


def editor_answer_out_of_window():
    return json.dumps(
        {
            "slots": [
                {"id": "v1", "shot_key": "0056/s1", "in_s": 18.0, "out_s": 23.0, "transition": "cut"},
                {"id": "v2", "shot_key": "0057/s1", "in_s": 2.0, "out_s": 8.0, "transition": "crossfade"},
                {"id": "v3", "shot_key": "0080/s1", "in_s": 12.0, "out_s": 20.0, "transition": "crossfade"},
            ]
        }
    )


def editor_answer_missing_one():
    return json.dumps(
        {
            "slots": [
                {"id": "v1", "shot_key": "0056/s1", "in_s": 2.0, "out_s": 7.0, "transition": "cut"},
                {"id": "v2", "shot_key": "0057/s1", "in_s": 2.0, "out_s": 8.0, "transition": "crossfade"},
            ]
        }
    )


def editor_answer_empty():
    return json.dumps({"slots": []})


def editor_answer_long_ordinary():
    return json.dumps(
        {
            "slots": [
                {"id": "v1", "shot_key": "0056/s1", "in_s": 0.0, "out_s": 20.0, "transition": "cut"},
                {"id": "v2", "shot_key": "0057/s1", "in_s": 2.0, "out_s": 8.0, "transition": "crossfade"},
                {"id": "v3", "shot_key": "0080/s1", "in_s": 12.0, "out_s": 20.0, "transition": "crossfade"},
            ]
        }
    )


def reviewer_approved():
    return json.dumps({"approved": True, "verdict": "good", "fixes": []})


def reviewer_not_approved():
    return json.dumps(
        {
            "approved": False,
            "verdict": "the middle drags",
            "fixes": [{"slot": "v2", "problem": "too long", "change": "shorten to about 2.5 s"}],
        }
    )


def calls_for(client, marker):
    return [call for call in client.calls if marker in call["messages"][0]["content"]]


def test_happy_path(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), editor_answer(), reviewer_approved()], cost_per_call=0.002)
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert summary["status"] == "written"
    assert summary["rounds_used"] == 1
    assert summary["approved"] is True
    assert summary["fallback"] is None
    assert summary["llm_cost_usd"] == pytest.approx(0.006)
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    plan = json.loads((workspace / "shot_plan.json").read_text(encoding="utf-8"))
    assert validate(edit, "edit") == []
    assert validate(plan, "shot_plan") == []
    assert edit["video"][0]["reason"].startswith("interest 60")
    assert "the wake opens the day" in edit["video"][0]["reason"]
    assert edit["video"][0]["transition"] == {"type": "cut", "duration_s": 0}
    assert edit["video"][1]["transition"] == {"type": "crossfade", "duration_s": 0.3}
    assert edit["audio"]["ambience"]["enabled"] is True
    assert edit["audio"]["music"] is None


def test_reviewer_sends_fixes(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(
        [
            plan_answer(),
            editor_answer(),
            reviewer_not_approved(),
            editor_answer(),
            reviewer_approved(),
        ]
    )
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert summary["rounds_used"] == 2
    assert summary["approved"] is True
    editor_calls = calls_for(client, "# Editor")
    assert len(editor_calls) == 2
    second_user = editor_calls[1]["messages"][1]["content"]
    assert "Review fixes from the previous round" in second_user
    assert "shorten to about 2.5 s" in second_user


def test_max_rounds_keeps_clean_draft(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(
        [
            plan_answer(),
            editor_answer(),
            reviewer_not_approved(),
            editor_answer(),
            reviewer_not_approved(),
        ]
    )
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, rounds=2)
    assert code == 0
    assert summary["rounds_used"] == 2
    assert summary["approved"] is False
    assert summary["status"] == "written"
    assert any("did not approve" in warning for warning in summary["warnings"])
    assert (workspace / "edit.json").exists()


def test_max_rounds_with_violations_keeps_the_repaired_draft(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(
        [
            plan_answer(),
            editor_answer_out_of_window(),
            reviewer_not_approved(),
            editor_answer_out_of_window(),
            reviewer_not_approved(),
        ]
    )
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, rounds=2)
    assert code == 0
    assert summary["status"] == "written"
    assert summary["fallback"] is None
    assert summary["approved"] is False
    assert any(warning.startswith("repaired:") for warning in summary["warnings"])
    record = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert record["meta"]["director"]["by"] == "llm"
    assert record["meta"]["director"]["approved"] is False
    plan = json.loads((workspace / "shot_plan.json").read_text(encoding="utf-8"))
    assert [slot["id"] for slot in record["video"]] == [slot["id"] for slot in plan["slot_plan"]]


def test_repair_rebuilds_a_bad_cut_from_its_shot():
    facts = {"0001/s1": fact("0001/s1", "clip_0001_x.mp4", 0.0, 20.0)}
    plan = {"slot_plan": [{"id": "v1", "shot_key": "0001/s1", "target_s": 5.0, "why": "a", "keep_whole": False}]}
    record = {"video": [
        {"id": "v1", "clip": "clip_0001_x.mp4", "in_s": 30.0, "out_s": 40.0},
        {"id": "v9", "clip": "clip_0001_x.mp4", "in_s": 0.0, "out_s": 4.0},
    ]}
    violations = llm_director.validate_edit(record, plan, facts)
    assert violations
    notes = llm_director.repair_draft(record, plan, facts, {}, violations)
    assert [slot["id"] for slot in record["video"]] == ["v1"]
    assert llm_director.blocking(llm_director.validate_edit(record, plan, facts)) == []
    assert any("v9" in note for note in notes)


def star_facts():
    return {
        "0001/s1": fact("0001/s1", "clip_0001_x.mp4", 0.0, 20.0, score=0.3),
        "0002/s1": fact(
            "0002/s1", "clip_0002_x.mp4", 0.0, 12.0,
            keep_whole=True, score=0.95, hero=True, role="move", motion_label="travel_forward",
        ),
        "0003/s1": fact("0003/s1", "clip_0003_x.mp4", 0.0, 12.0, score=0.2),
        "0004/s1": fact("0004/s1", "clip_0004_x.mp4", 0.0, 12.0, score=0.25),
    }


def star_plan(shot_keys):
    return {
        "brief": {"target_duration_s": 60.0},
        "slot_plan": [
            {"id": f"v{index + 1}", "shot_key": key, "target_s": 4.0, "why": "a", "keep_whole": False}
            for index, key in enumerate(shot_keys)
        ],
    }


def star_record(pairs):
    return {
        "video": [
            {"id": sid, "clip": clip, "in_s": 0.0, "out_s": 4.0, "score": 0.3}
            for sid, clip in pairs
        ]
    }


def test_required_star_keys_keeps_top_share():
    facts = star_facts()
    assert llm_director.required_star_keys(facts, 3) == ["0002/s1"]
    assert llm_director.required_star_keys(facts, 10)[0] == "0002/s1"


def test_hero_missing_when_top_star_left_out():
    facts = star_facts()
    plan = star_plan(["0001/s1", "0003/s1", "0004/s1"])
    record = star_record([
        ("v1", "clip_0001_x.mp4"),
        ("v2", "clip_0003_x.mp4"),
        ("v3", "clip_0004_x.mp4"),
    ])
    violations = llm_director.validate_edit(record, plan, facts)
    missing = [item for item in violations if item["rule"] == "hero_missing"]
    assert [item["slot"] for item in missing] == ["0002/s1"]
    assert any(item["rule"] == "hero_missing" for item in llm_director.blocking(violations))


def test_hero_missing_not_fired_when_star_is_planned():
    facts = star_facts()
    plan = star_plan(["0001/s1", "0002/s1", "0003/s1"])
    record = star_record([
        ("v1", "clip_0001_x.mp4"),
        ("v2", "clip_0002_x.mp4"),
        ("v3", "clip_0003_x.mp4"),
    ])
    record["video"][1]["in_s"] = 0.0
    record["video"][1]["out_s"] = 4.0
    violations = llm_director.validate_edit(record, plan, facts)
    assert [item for item in violations if item["rule"] == "hero_missing"] == []


def test_hero_photo_stays_non_blocking():
    facts = {
        "photo_01/p1": fact(
            "photo_01/p1", "photo_01.jpg", 0.0, 0.04, score=0.95, hero=True, media_type="photo"
        ),
        "0001/s1": fact("0001/s1", "clip_0001_x.mp4", 0.0, 20.0, score=0.3),
    }
    plan = star_plan(["0001/s1"])
    record = star_record([("v1", "clip_0001_x.mp4")])
    violations = llm_director.validate_edit(record, plan, facts)
    assert [item for item in violations if item["rule"] == "hero_missing"] == []
    hero_items = [item for item in violations if item["rule"] == "hero"]
    assert [item["slot"] for item in hero_items] == ["photo_01/p1"]
    assert not any(item["rule"] == "hero_missing" for item in llm_director.blocking(violations))
    assert all(item["rule"] != "hero" for item in llm_director.blocking(violations))


def test_repair_swaps_missing_star_in():
    facts = star_facts()
    plan = star_plan(["0001/s1", "0003/s1", "0004/s1"])
    plan["brief"]["target_duration_s"] = 6.0
    record = star_record([
        ("v1", "clip_0001_x.mp4"),
        ("v2", "clip_0003_x.mp4"),
        ("v3", "clip_0004_x.mp4"),
    ])
    violations = llm_director.validate_edit(record, plan, facts)
    notes = llm_director.repair_draft(record, plan, facts, {}, violations)
    used = {slot.get("shot_key") for slot in plan.get("slot_plan") or []}
    assert "0002/s1" in used
    assert "0003/s1" not in used
    assert any("swapped 0002/s1 in for 0003/s1" in note for note in notes)
    assert [item for item in llm_director.validate_edit(record, plan, facts) if item["rule"] == "hero_missing"] == []
    assert llm_director.blocking(llm_director.validate_edit(record, plan, facts)) == []


def test_repair_star_swap_skips_a_jump_cut_victim():
    facts = {
        "0001/s1": fact("0001/s1", "clip_0001_x.mp4", 0.0, 20.0, score=0.3),
        "0002/s1": fact(
            "0002/s1", "clip_0001_x.mp4", 8.0, 14.0,
            keep_whole=True, score=0.95, hero=True, role="move", motion_label="travel_forward",
        ),
        "0003/s1": fact("0003/s1", "clip_0003_x.mp4", 0.0, 12.0, score=0.15),
    }
    plan = star_plan(["0001/s1", "0003/s1"])
    plan["brief"]["target_duration_s"] = 6.0
    record = star_record([
        ("v1", "clip_0001_x.mp4"),
        ("v2", "clip_0003_x.mp4"),
    ])
    violations = llm_director.validate_edit(record, plan, facts)
    assert [item["rule"] for item in violations if item["rule"] == "hero_missing"] == ["hero_missing"]
    assert llm_director.pick_swap_victim(record, plan, facts, facts["0002/s1"], 6.0, 1.0, 5.0) == "v1"
    notes = llm_director.repair_draft(record, plan, facts, {}, violations)
    used = {slot.get("shot_key") for slot in plan.get("slot_plan") or []}
    assert "0002/s1" in used
    assert "0003/s1" in used
    assert any("swapped 0002/s1 in for 0001/s1" in note for note in notes)
    assert llm_director.blocking(llm_director.validate_edit(record, plan, facts)) == []


def test_repair_keeps_chronological_order():
    facts = {
        "0001/s1": fact("0001/s1", "clip_0001_x.mp4", 0.0, 20.0, score=0.3),
        "0002/s1": fact("0002/s1", "clip_0002_x.mp4", 0.0, 20.0, score=0.4),
        "0003/s1": fact("0003/s1", "clip_0003_x.mp4", 0.0, 20.0, score=0.35),
        "0004/s1": fact(
            "0004/s1", "clip_0004_x.mp4", 0.0, 20.0,
            keep_whole=True, score=0.95, hero=True, role="move", motion_label="travel_forward",
        ),
    }
    plan = star_plan(["0001/s1", "0002/s1", "0003/s1"])
    plan["brief"]["order"] = "chronological"
    plan["brief"]["target_duration_s"] = 6.0
    record = star_record([
        ("v1", "clip_0001_x.mp4"),
        ("v2", "clip_0002_x.mp4"),
        ("v3", "clip_0003_x.mp4"),
    ])
    violations = llm_director.validate_edit(record, plan, facts)
    assert any(item["rule"] == "hero_missing" for item in violations)
    llm_director.repair_draft(record, plan, facts, {}, violations)
    clips = [slot["clip"] for slot in record["video"]]
    assert clips == sorted(clips)
    assert "0004/s1" in {slot.get("shot_key") for slot in plan["slot_plan"]}
    assert llm_director.blocking(llm_director.validate_edit(record, plan, facts)) == []


def test_resort_resets_transitions_without_beat_grid():
    facts = {
        "0001/s1": fact("0001/s1", "clip_0001_x.mp4", 0.0, 20.0, score=0.3),
        "0002/s1": fact("0002/s1", "clip_0002_x.mp4", 0.0, 20.0, score=0.4),
        "0003/s1": fact("0003/s1", "clip_0003_x.mp4", 0.0, 20.0, score=0.35),
    }
    plan = star_plan(["0003/s1", "0001/s1", "0002/s1"])
    plan["brief"]["order"] = "chronological"
    record = star_record([
        ("v1", "clip_0003_x.mp4"),
        ("v2", "clip_0001_x.mp4"),
        ("v3", "clip_0002_x.mp4"),
    ])
    record["video"][0]["transition"] = {"type": "cut", "duration_s": 0}
    record["video"][1]["transition"] = {"type": "crossfade", "duration_s": 0.3}
    record["video"][2]["transition"] = {"type": "crossfade", "duration_s": 0.3}
    notes = []
    assert llm_director.enforce_chronological_order(record, plan, facts, notes) is True
    clips = [slot["clip"] for slot in record["video"]]
    assert clips == sorted(clips)
    assert record["video"][0]["transition"] == {"type": "cut", "duration_s": 0}
    assert all(slot["transition"] == {"type": "crossfade", "duration_s": 0.3} for slot in record["video"][1:])


def test_synthesised_slot_keeps_shot_key():
    facts = {"0001/s1": fact("0001/s1", "clip_0001_x.mp4", 0.0, 20.0, score=0.3)}
    plan = star_plan(["0001/s1"])
    record = {"video": []}
    llm_director.ensure_all_planned_slots(record, plan, facts, {}, 6.0)
    assert record["video"][0]["shot_key"] == "0001/s1"


def test_fill_to_target_adds_slots_until_the_brief_length():
    facts = {
        "0001/s1": fact("0001/s1", "clip_0001_x.mp4", 0.0, 20.0, score=0.3),
        "0002/s1": fact("0002/s1", "clip_0002_x.mp4", 0.0, 20.0, score=0.5),
        "0003/s1": fact("0003/s1", "clip_0003_x.mp4", 0.0, 20.0, score=0.4),
        "0004/s1": fact("0004/s1", "clip_0004_x.mp4", 0.0, 20.0, score=0.2),
        "0005/s1": fact("0005/s1", "clip_0005_x.mp4", 0.0, 20.0, score=0.1),
    }
    plan = star_plan(["0001/s1"])
    plan["brief"]["target_duration_s"] = 12.0
    record = {
        "video": [
            {"id": "v1", "clip": "clip_0001_x.mp4", "in_s": 8.0, "out_s": 11.0, "shot_key": "0001/s1", "score": 0.3},
        ]
    }
    notes = llm_director.fill_to_target(record, plan, facts, {}, 6.0, [])
    used = [row.get("shot_key") for row in plan.get("slot_plan") or []]
    assert "0002/s1" in used
    assert any("added 0002/s1" in note for note in notes)
    total = sum(llm_director.slot_duration(row) for row in record["video"])
    assert total >= 12.0 - llm_director.TARGET_SLACK_S


def test_validator_catches_rule_break(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(
        [
            plan_answer(),
            editor_answer_out_of_window(),
            reviewer_not_approved(),
            editor_answer(),
            reviewer_approved(),
        ]
    )
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert summary["status"] == "written"
    assert summary["approved"] is True
    reviewer_calls = calls_for(client, "# Reviewer")
    assert "outside the shot window" in reviewer_calls[0]["messages"][1]["content"]


def test_ineligible_segment_not_in_screenwriter_menu(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    quality_path = workspace / "quality" / "clip_0056_x.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["segments"].append(segment("s2", 20.0, 30.0, "accidental"))
    quality_path.write_text(json.dumps(quality), encoding="utf-8")
    shotlog_path = workspace / "shotlog.json"
    shotlog_data = json.loads(shotlog_path.read_text(encoding="utf-8"))
    shotlog_data["shots"].append(
        shotlog_shot(
            "0056/s2",
            "clip_0056_x.mp4",
            "s2",
            20.0,
            30.0,
            True,
            0.95,
            "a bump",
            "interest 95 · accidental · accidental · clean",
            role="accidental",
        )
    )
    shotlog_path.write_text(json.dumps(shotlog_data), encoding="utf-8")

    shotlog = llm_director.load_shotlog(workspace)
    facts, _, _ = llm_director.build_shot_facts(workspace, shotlog)
    assert "0056/s2" not in facts

    client = StubClient([plan_answer()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, dry_run=True)
    assert code == 0
    screenwriter_calls = calls_for(client, "# Screenwriter")
    user = screenwriter_calls[0]["messages"][1]["content"]
    assert "0056/s2" not in user
    assert summary["candidates"] == len(facts)


def test_brief_facts_reach_the_screenwriter(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    (workspace / "brief_facts.json").write_text(
        json.dumps({"place": "Retezat", "when": "July 2026"}), encoding="utf-8"
    )
    client = StubClient([plan_answer()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, dry_run=True)
    assert code == 0
    user = calls_for(client, "# Screenwriter")[0]["messages"][1]["content"]
    assert "Place: Retezat" in user
    assert "When: July 2026" in user


def test_missing_editor_slot_is_violation(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(
        [
            plan_answer(),
            editor_answer_missing_one(),
            reviewer_not_approved(),
            editor_answer(),
            reviewer_approved(),
        ]
    )
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert summary["status"] == "written"
    assert summary["fallback"] is None
    assert summary["approved"] is True
    reviewer_calls = calls_for(client, "# Reviewer")
    first_user = reviewer_calls[0]["messages"][1]["content"]
    assert "missing" in first_user
    assert "planned but the editor produced no cut window" in first_user
    assert "v3" in first_user


def test_empty_editor_answer_falls_back(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), editor_answer_empty(), editor_answer_empty()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert summary["status"] == "fallback"
    assert summary["fallback"] == "rules"
    record = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert record["meta"]["director"]["by"] == "rules"
    assert any("editor produced no valid edit" in note for note in record["meta"]["director"]["notes"])


def test_validator_catches_missing_plan_slot():
    facts = {
        "0001/s1": fact("0001/s1", "clip_0001_x.mp4", 0.0, 20.0),
        "0001/s2": fact("0001/s2", "clip_0001_x.mp4", 20.0, 40.0),
    }
    plan = {
        "slot_plan": [
            {"id": "v1", "shot_key": "0001/s1", "target_s": 5.0, "why": "a", "keep_whole": False},
            {"id": "v2", "shot_key": "0001/s2", "target_s": 5.0, "why": "b", "keep_whole": False},
        ]
    }
    record = {"video": [{"id": "v1", "clip": "clip_0001_x.mp4", "in_s": 0.0, "out_s": 5.0}]}
    violations = llm_director.validate_edit(record, plan, facts)
    missing = [item for item in violations if item["rule"] == "missing"]
    assert len(missing) == 1
    assert missing[0]["slot"] == "v2"


def test_budget_exceeded_falls_back(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), BudgetExceeded("budget reached")])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert summary["status"] == "fallback"
    assert summary["fallback"] == "rules"
    assert any("budget" in warning for warning in summary["warnings"])


def test_budget_exceeded_no_fallback_exits_3(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), BudgetExceeded("budget reached")])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, fallback_rules=False)
    assert code == EXIT_RUNTIME
    assert summary["status"] == "error"
    assert not (workspace / "edit.json").exists()


def test_fallback_summary_reports_shortlist(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), BudgetExceeded("budget reached")])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert summary["status"] == "fallback"
    assert summary["shortlist"] > 0
    assert summary["shortlist"] <= summary["candidates"]


def test_stop_summary_reports_shortlist(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), BudgetExceeded("budget reached")])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, fallback_rules=False)
    assert code == EXIT_RUNTIME
    assert summary["status"] == "error"
    assert summary["shortlist"] > 0


def test_unparseable_answers_retried_then_fallback(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(["not json at all", "still not json"])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert summary["status"] == "fallback"
    assert len(client.calls) == 2


def test_dry_run_writes_plan_only(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer()], cost_per_call=0.003)
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, dry_run=True)
    assert code == 0
    assert (workspace / "shot_plan.json").exists()
    assert not (workspace / "edit.json").exists()
    assert summary["baseline"]["slots"] >= 1
    assert summary["baseline"]["total_duration_s"] > 0
    assert summary["baseline"]["reasons"]
    assert summary["llm_cost_usd"] == pytest.approx(0.003)
    assert len(client.calls) == 1


def test_dry_run_ignores_rerun_safety(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    first = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    llm_director.run(workspace, brief=BRIEF, client=first)
    second = StubClient([plan_answer()], cost_per_call=0.004)
    summary, code = llm_director.run(workspace, brief=BRIEF, client=second, dry_run=True)
    assert code == 0
    assert summary["status"] == "written"
    assert len(second.calls) == 1
    assert summary["llm_cost_usd"] == pytest.approx(0.004)


def test_json_summary_has_every_key(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    for key in (
        "status",
        "out",
        "plan",
        "rounds_used",
        "approved",
        "fallback",
        "slots",
        "candidates",
        "total_duration_s",
        "validator_violations",
        "warnings",
        "wall_time_s",
        "llm_cost_usd",
    ):
        assert key in summary


def test_rerun_safety_skips(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    first = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    llm_director.run(workspace, brief=BRIEF, client=first)
    second = StubClient([])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=second)
    assert code == 0
    assert summary["status"] == "skipped"
    assert second.calls == []


def test_missing_brief_raises(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    with pytest.raises(llm_director.LLMDirectorError):
        llm_director.run(workspace)


def test_missing_shotlog_falls_back(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    (workspace / "shotlog.json").unlink()
    client = StubClient([])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert summary["status"] == "fallback"
    assert any("no shotlog" in warning for warning in summary["warnings"])
    assert client.calls == []


def test_missing_shotlog_no_fallback_errors(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    (workspace / "shotlog.json").unlink()
    client = StubClient([])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, fallback_rules=False)
    assert code == EXIT_USAGE
    assert summary["status"] == "error"


def test_shot_facts_phrase_window(tmp_path):
    workspace = build_phrase_workspace(tmp_path / "ws")
    shotlog = llm_director.load_shotlog(workspace)
    facts, _, _ = llm_director.build_shot_facts(workspace, shotlog)
    fact = facts["clipA/s1"]
    assert fact["window_in_s"] == 0.0
    assert fact["window_out_s"] == 20.0
    assert fact["landing_at_s"] == 10.0
    assert fact["landing_hold_key"] == "clipA/s2"


def test_validator_catches_landing_phrase(tmp_path):
    workspace = build_phrase_workspace(tmp_path / "ws")
    shotlog = llm_director.load_shotlog(workspace)
    facts, _, _ = llm_director.build_shot_facts(workspace, shotlog)
    plan = {"slot_plan": [{"id": "v1", "shot_key": "clipA/s1", "target_s": 10.0, "why": "test", "keep_whole": False}]}
    record = {"video": [{"id": "v1", "clip": "clipA.mp4", "in_s": 0.0, "out_s": 8.0}]}
    violations = llm_director.validate_edit(record, plan, facts)
    assert any(item["rule"] == "landing" for item in violations)


def test_validator_catches_same_take():
    facts = {
        "0001/s1": fact("0001/s1", "clip_0001_x.mp4", 0.0, 20.0),
        "0001/s2": fact("0001/s2", "clip_0001_x.mp4", 20.0, 40.0),
    }
    plan = {
        "slot_plan": [
            {"id": "v1", "shot_key": "0001/s1", "target_s": 5.0, "why": "a", "keep_whole": False},
            {"id": "v2", "shot_key": "0001/s2", "target_s": 5.0, "why": "b", "keep_whole": False},
        ]
    }
    record = {
        "video": [
            {"id": "v1", "clip": "clip_0001_x.mp4", "in_s": 0.0, "out_s": 5.0},
            {"id": "v2", "clip": "clip_0001_x.mp4", "in_s": 30.0, "out_s": 35.0},
        ]
    }
    violations = llm_director.validate_edit(record, plan, facts)
    assert any(item["rule"] == "same_take" for item in violations)


def test_validator_catches_similar():
    facts = {
        "0001/s1": fact("0001/s1", "clip_0001_x.mp4", 0.0, 20.0, similar_to=["0002/s1"]),
        "0002/s1": fact("0002/s1", "clip_0002_x.mp4", 0.0, 20.0, similar_to=["0001/s1"]),
    }
    plan = {
        "slot_plan": [
            {"id": "v1", "shot_key": "0001/s1", "target_s": 5.0, "why": "a", "keep_whole": False},
            {"id": "v2", "shot_key": "0002/s1", "target_s": 5.0, "why": "b", "keep_whole": False},
        ]
    }
    record = {
        "video": [
            {"id": "v1", "clip": "clip_0001_x.mp4", "in_s": 0.0, "out_s": 5.0},
            {"id": "v2", "clip": "clip_0002_x.mp4", "in_s": 0.0, "out_s": 5.0},
        ]
    }
    violations = llm_director.validate_edit(record, plan, facts)
    assert any(item["rule"] == "similar" for item in violations)


def test_validator_catches_keep_whole_and_length():
    facts = {"0001/s1": fact("0001/s1", "clip_0001_x.mp4", 0.0, 20.0, keep_whole=True)}
    facts["0001/s1"].update({"role": "move", "motion_label": "pan_right"})
    plan = {"slot_plan": [{"id": "v1", "shot_key": "0001/s1", "target_s": 5.0, "why": "a", "keep_whole": True}]}
    record = {"video": [{"id": "v1", "clip": "clip_0001_x.mp4", "in_s": 0.0, "out_s": 5.0}]}
    violations = llm_director.validate_edit(record, plan, facts)
    assert any(item["rule"] == "keep_whole" for item in violations)

    facts = {"0001/s1": fact("0001/s1", "clip_0001_x.mp4", 0.0, 20.0)}
    plan = {"slot_plan": [{"id": "v1", "shot_key": "0001/s1", "target_s": 5.0, "why": "a", "keep_whole": False}]}
    record = {"video": [{"id": "v1", "clip": "clip_0001_x.mp4", "in_s": 0.0, "out_s": 0.5}]}
    violations = llm_director.validate_edit(record, plan, facts)
    assert any(item["rule"] == "length" for item in violations)


def fact(key, clip, window_in, window_out, keep_whole=False, similar_to=None, score=0.5, hero=False, role="hold", motion_label="static", media_type=None):
    row = {
        "shot_key": key,
        "clip": clip,
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


def test_editor_facts_follow_fact_keep_whole(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    shotlog = llm_director.load_shotlog(workspace)
    facts, _, _ = llm_director.build_shot_facts(workspace, shotlog)
    plan = {
        "slot_plan": [
            {"id": "v3", "shot_key": "0080/s1", "target_s": 8.0, "why": "payoff", "keep_whole": False}
        ]
    }
    assert facts["0080/s1"]["keep_whole"] is True
    merged = llm_director.editor_facts(plan, facts)
    assert merged[0]["keep_whole"] is True

    answer = {
        "slots": [
            {"id": "v3", "shot_key": "0080/s1", "in_s": 12.0, "out_s": 18.0, "transition": "cut"}
        ]
    }
    warnings = []
    record = llm_director.build_edit_record(plan, answer, facts, shotlog, [], {}, 0.0, 0.0, warnings)
    assert record is not None
    slot = record["video"][0]
    assert facts["0080/s1"]["peak_s"] == pytest.approx(10.0)
    assert facts["0080/s1"]["peak_source"] == "middle"
    assert slot["in_s"] == pytest.approx(7.0)
    assert slot["out_s"] == pytest.approx(13.0)
    assert any("fixed v3" in note for note in warnings)
    violations = llm_director.validate_edit(record, plan, facts)
    assert not [item for item in violations if item["rule"] == "keep_whole"]


def test_normalisation_reported_in_warnings(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), editor_answer_long_ordinary(), reviewer_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert summary["status"] == "written"
    assert summary["fallback"] is None
    assert any("fixed v1" in warning for warning in summary["warnings"])
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    slot = edit["video"][0]
    assert slot["out_s"] - slot["in_s"] <= llm_director.MAX_SLOT_S + llm_director.TOLERANCE_S
    assert slot["out_s"] - slot["in_s"] >= llm_director.MIN_SHOT_LENGTH_S - llm_director.TOLERANCE_S


def test_missing_last_round_slot_is_synthesised(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(
        [
            plan_answer(),
            editor_answer_missing_one(),
            reviewer_not_approved(),
            editor_answer_missing_one(),
            reviewer_not_approved(),
        ]
    )
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, rounds=2)
    assert code == 0
    assert summary["status"] == "written"
    assert summary["fallback"] is None
    assert any("fixed v3" in warning for warning in summary["warnings"])
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert {slot["id"] for slot in edit["video"]} == {"v1", "v2", "v3"}
    slot = next(item for item in edit["video"] if item["id"] == "v3")
    assert slot["in_s"] == pytest.approx(7.0)
    assert slot["out_s"] == pytest.approx(13.0)
    assert "middle_speed" not in slot
    assert slot["peak_s"] == pytest.approx(10.0) and slot["centred"] is True
    assert slot_screen_length(slot) == pytest.approx(6.0, abs=0.01)


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


def test_same_take_rule_allows_beats_with_another_take_between():
    first = llm_director.make_fact(
        {"shot_key": "clipA/s1b1", "clip": "clipA.mp4", "in_s": 10.0, "out_s": 60.0,
         "keep_whole": False, "target_s": 5.0, "score": 0.9, "summary": "approach",
         "role": "move", "motion_label": "travel_forward", "hero": True, "similar_to": []},
        6.0,
    )
    middle = llm_director.make_fact(
        {"shot_key": "clipB/s1", "clip": "clipB.mp4", "in_s": 0.0, "out_s": 20.0,
         "keep_whole": False, "target_s": 5.0, "score": 0.7, "summary": "other",
         "role": "hold", "motion_label": "static", "hero": None, "similar_to": []},
        6.0,
    )
    last = llm_director.make_fact(
        {"shot_key": "clipA/s1b2", "clip": "clipA.mp4", "in_s": 60.0, "out_s": 150.0,
         "keep_whole": False, "target_s": 5.0, "score": 0.55, "summary": "exit",
         "role": "move", "motion_label": "travel_forward", "hero": None, "similar_to": []},
        6.0,
    )
    facts = {fact["shot_key"]: fact for fact in (first, middle, last)}
    plan = {
        "slot_plan": [
            {"id": "v1", "shot_key": "clipA/s1b1", "target_s": 5.0, "why": "a", "keep_whole": False},
            {"id": "v2", "shot_key": "clipB/s1", "target_s": 5.0, "why": "b", "keep_whole": False},
            {"id": "v3", "shot_key": "clipA/s1b2", "target_s": 5.0, "why": "c", "keep_whole": False},
        ]
    }
    record = {
        "video": [
            {"id": "v1", "clip": "clipA.mp4", "in_s": 20.0, "out_s": 25.0},
            {"id": "v2", "clip": "clipB.mp4", "in_s": 5.0, "out_s": 10.0},
            {"id": "v3", "clip": "clipA.mp4", "in_s": 70.0, "out_s": 75.0},
        ]
    }
    violations = llm_director.validate_edit(record, plan, facts)
    assert not any(item["rule"] == "same_take" for item in violations)
    record["video"][1], record["video"][2] = record["video"][2], record["video"][1]
    violations = llm_director.validate_edit(record, plan, facts)
    assert not any(item["rule"] == "same_take" for item in violations)
    record["video"][1]["in_s"], record["video"][1]["out_s"] = 28.0, 33.0
    violations = llm_director.validate_edit(record, plan, facts)
    assert any(item["rule"] == "same_take" for item in violations)


def test_same_take_static_framings_always_jump():
    facts = {
        "0001/s1": fact("0001/s1", "clip_0001_x.mp4", 0.0, 20.0),
        "0001/s2": fact("0001/s2", "clip_0001_x.mp4", 60.0, 80.0),
    }
    plan = {"slot_plan": [
        {"id": "v1", "shot_key": "0001/s1", "target_s": 5.0, "why": "a", "keep_whole": False},
        {"id": "v2", "shot_key": "0001/s2", "target_s": 5.0, "why": "b", "keep_whole": False},
    ]}
    record = {"video": [
        {"id": "v1", "clip": "clip_0001_x.mp4", "in_s": 7.5, "out_s": 12.5},
        {"id": "v2", "clip": "clip_0001_x.mp4", "in_s": 67.5, "out_s": 72.5},
    ]}
    violations = llm_director.validate_edit(record, plan, facts)
    assert any(item["rule"] == "same_take" for item in violations)
    notes = llm_director.repair_draft(record, plan, facts, {"0001/s1": {"score": 0.9}, "0001/s2": {"score": 0.5}}, violations)
    assert [slot["id"] for slot in record["video"]] == ["v1"]
    assert any("0001/s2" in note for note in notes)


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


def short_plan_answer():
    return json.dumps(
        {
            "logline": "a quick recap",
            "structure": [{"id": "b1", "name": "All", "intent": "the day in brief", "target_s": 20.0}],
            "slot_plan": [
                {"id": "v1", "beat": "b1", "shot_key": "0056/s1", "target_s": 1.5, "why": "the wake", "keep_whole": False},
                {"id": "v2", "beat": "b1", "shot_key": "0057/s1", "target_s": 2.0, "why": "the bay", "keep_whole": False},
                {"id": "v3", "beat": "b1", "shot_key": "0080/s1", "target_s": 3.5, "why": "the cave", "keep_whole": True},
            ],
        }
    )


def short_editor_answer():
    return json.dumps(
        {
            "slots": [
                {"id": "v1", "shot_key": "0056/s1", "in_s": 0.0, "out_s": 1.5, "transition": "cut"},
                {"id": "v2", "shot_key": "0057/s1", "in_s": 0.0, "out_s": 2.0, "transition": "crossfade"},
                {"id": "v3", "shot_key": "0080/s1", "in_s": 16.5, "out_s": 20.0, "transition": "crossfade"},
            ]
        }
    )


def test_short_edit_allows_one_and_a_half_second_slots(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([short_plan_answer(), short_editor_answer(), reviewer_approved()])
    summary, code = llm_director.run(
        workspace, brief="about 20 seconds, chronological, ambient sound only", client=client
    )
    assert code == 0
    assert summary["status"] == "written"
    assert summary["approved"] is True
    assert summary["validator_violations"] == 0
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    lengths = {slot["id"]: round(slot_screen_length(slot), 2) for slot in edit["video"]}
    assert lengths["v1"] == 1.5
    assert lengths["v2"] == 2.0
    assert lengths["v3"] == 3.5


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


def test_photo_shot_becomes_photo_slot(tmp_path):
    photo = write_photo(tmp_path / "media" / "photo_01.jpg")
    shot = photo_shot(
        "photo_01/p1",
        photo.name,
        "2026-01-01T10:01:00+00:00",
        0.8,
        subject_position={"x": 0.2, "y": 0.5},
    )
    workspace = build_photo_workspace(tmp_path / "ws", photo, shot)
    client = StubClient([photo_plan_answer(), photo_editor_answer(), reviewer_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert summary["status"] == "written"
    assert summary["approved"] is True
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert validate(edit, "edit") == []
    photo_slots = [slot for slot in edit["video"] if slot.get("kind") == "photo"]
    assert len(photo_slots) == 1
    slot = photo_slots[0]
    assert slot["id"] == "p1"
    assert slot["photo"] == photo.name
    assert 2.0 <= slot["duration_s"] <= 4.0
    assert slot["ken_burns"] == ken_burns_for({"x": 0.2, "y": 0.5}, [])
    photo_sources = [source for source in edit["sources"] if source.get("media_type") == "photo"]
    assert photo_sources == [
        {"file": photo.name, "path": str(photo), "duration_s": None, "media_type": "photo"}
    ]


def test_photo_ken_burns_matches_common_rule():
    specs = [
        ({"x": 0.2, "y": 0.5}, [], "pan_left"),
        ({"x": 0.8, "y": 0.5}, [], "pan_right"),
        ({"x": 0.5, "y": 0.5}, [], "zoom_in"),
        ({"x": 0.5, "y": 0.5}, ["close-up"], "zoom_out"),
        (None, [], "zoom_in"),
    ]
    for position, tags, move in specs:
        fact = photo_fact("photo/p1", "photo.jpg", subject_position=position, tags=tags)
        slot = llm_director.make_photo_slot_record(
            "p1", fact, {"score": 0.8}, {"why": "still"}, 3.0, {"type": "cut", "duration_s": 0}
        )
        assert slot["ken_burns"] == ken_burns_for(position, tags)
        assert slot["ken_burns"]["move"] == move


def test_photo_duration_from_answer():
    fact = photo_fact("photo/p1", "photo.jpg")
    assert llm_director.photo_duration_from_answer({"duration_s": 3.0}, fact) == 3.0
    assert llm_director.photo_duration_from_answer({"in_s": 1.0, "out_s": 4.0}, fact) == 3.0
    assert llm_director.photo_duration_from_answer({"duration_s": 8.0}, fact) == 4.0
    assert llm_director.photo_duration_from_answer({"duration_s": 0.5}, fact) == 2.0
    assert llm_director.photo_duration_from_answer({}, fact) == 3.0


def test_validator_flags_photo_duration():
    facts = {"photo_01/p1": photo_fact("photo_01/p1", "photo_01.jpg", subject_position={"x": 0.5, "y": 0.5})}
    plan = {
        "slot_plan": [
            {"id": "p1", "shot_key": "photo_01/p1", "target_s": 3.0, "why": "still", "keep_whole": False}
        ]
    }
    record = {
        "video": [
            {
                "id": "p1",
                "kind": "photo",
                "photo": "photo_01.jpg",
                "duration_s": 8.0,
                "ken_burns": ken_burns_for({"x": 0.5, "y": 0.5}, []),
                "score": 0.8,
                "reason": "still",
            }
        ]
    }
    violations = llm_director.validate_edit(record, plan, facts)
    assert any(item["rule"] == "photo_length" for item in violations)


def test_validator_flags_missing_hero_photo():
    facts = {"hero_photo/p1": photo_fact("hero_photo/p1", "hero_photo.jpg", hero=True)}
    plan = {
        "slot_plan": [
            {"id": "p1", "shot_key": "hero_photo/p1", "target_s": 3.0, "why": "hero", "keep_whole": False}
        ]
    }
    record = {"video": []}
    violations = llm_director.validate_edit(record, plan, facts)
    assert any(item["rule"] == "missing" and item["slot"] == "p1" for item in violations)


def test_validator_flags_unplanned_hero_photo():
    facts = {"hero_photo/p1": photo_fact("hero_photo/p1", "hero_photo.jpg", hero=True)}
    plan = {
        "slot_plan": [
            {"id": "v1", "shot_key": "clipA/s1", "target_s": 5.0, "why": "clip", "keep_whole": False}
        ]
    }
    record = {"video": []}
    violations = llm_director.validate_edit(record, plan, facts)
    assert any(item["rule"] == "hero" and item["slot"] == "hero_photo/p1" for item in violations)


def test_dropped_hero_photo_is_synthesised(tmp_path):
    photo = write_photo(tmp_path / "media" / "hero_photo.jpg")
    shot = photo_shot("hero_photo/p1", photo.name, "2026-01-01T10:01:00+00:00", 0.75, hero=True)
    workspace = build_photo_workspace(tmp_path / "ws", photo, shot)
    client = StubClient(
        [
            photo_plan_answer(shot_key="hero_photo/p1"),
            photo_editor_answer_missing_photo(),
            reviewer_not_approved(),
            photo_editor_answer_missing_photo(),
            reviewer_not_approved(),
        ]
    )
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, rounds=2)
    assert code == 0
    assert summary["status"] == "written"
    assert any("fixed p1" in warning for warning in summary["warnings"])
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert validate(edit, "edit") == []
    assert any(slot.get("kind") == "photo" and slot["photo"] == photo.name for slot in edit["video"])
    photo_sources = [source for source in edit["sources"] if source.get("media_type") == "photo"]
    assert photo_sources == [
        {"file": photo.name, "path": str(photo), "duration_s": None, "media_type": "photo"}
    ]


def test_photo_slot_from_in_out_answer(tmp_path):
    photo = write_photo(tmp_path / "media" / "photo_01.jpg")
    shot = photo_shot("photo_01/p1", photo.name, "2026-01-01T10:01:00+00:00", 0.8)
    workspace = build_photo_workspace(tmp_path / "ws", photo, shot)
    answer = json.dumps(
        {
            "slots": [
                {"id": "v1", "shot_key": "clipA/s1", "in_s": 2.0, "out_s": 7.0, "transition": "cut"},
                {"id": "p1", "shot_key": "photo_01/p1", "in_s": 1.0, "out_s": 4.0, "transition": "crossfade"},
            ]
        }
    )
    client = StubClient([photo_plan_answer(), answer, reviewer_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    slot = next(item for item in edit["video"] if item.get("kind") == "photo")
    assert slot["duration_s"] == 3.0


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


def video_bound_fact(key, clip, window_in, window_out, keep_whole=False):
    return {
        "shot_key": key,
        "clip": clip,
        "window_in_s": window_in,
        "window_out_s": window_out,
        "landing_at_s": None,
        "landing_hold_key": None,
        "keep_whole": keep_whole,
        "target_s": 5.0,
        "score": 0.5,
        "summary": None,
        "role": "hold",
        "motion_label": "static",
        "hero": False,
        "similar_to": [],
    }


def record_cuts(record):
    cuts = []
    position = 0.0
    for slot in record["video"]:
        position += slot["duration_s"] if slot.get("kind") == "photo" else slot["out_s"] - slot["in_s"]
        cuts.append(round(position, 4))
    return cuts


def test_load_beats_accepts_both_shapes(tmp_path):
    for index, data in enumerate([beat_grid(top_level=False), beat_grid(top_level=True)]):
        path = tmp_path / f"beats{index}.json"
        write_json(path, data)
        grid = llm_director.load_beats(path)
        assert grid["beat_s"][0] == 0.0
        assert grid["beat_period_s"] == 0.5
        assert grid["downbeat_s"]


def test_beat_snap_uses_downbeats_when_in_reach():
    beat_s = [round(0.5 * index, 4) for index in range(60)]
    grid = {"beat_s": beat_s, "downbeat_s": [0.0, 4.75, 9.5, 14.25], "beat_period_s": 0.5}
    record = {
        "video": [
            {"id": "v1", "clip": "a.mp4", "in_s": 0.0, "out_s": 2.4, "transition": {"type": "crossfade", "duration_s": 0.3}},
            {"id": "v2", "clip": "b.mp4", "in_s": 0.0, "out_s": 2.4, "transition": {"type": "crossfade", "duration_s": 0.3}},
            {"id": "v3", "clip": "c.mp4", "in_s": 0.0, "out_s": 2.4, "transition": {"type": "crossfade", "duration_s": 0.3}},
        ]
    }
    plan = {
        "slot_plan": [
            {"id": "v1", "beat": "b1", "shot_key": "a/s1"},
            {"id": "v2", "beat": "b1", "shot_key": "b/s1"},
            {"id": "v3", "beat": "b2", "shot_key": "c/s1"},
        ]
    }
    facts = {
        "a/s1": video_bound_fact("a/s1", "a.mp4", 0.0, 5.0),
        "b/s1": video_bound_fact("b/s1", "b.mp4", 0.0, 5.0),
        "c/s1": video_bound_fact("c/s1", "c.mp4", 0.0, 5.0),
    }
    warnings = []
    stats = llm_director.apply_beat_snap(record, plan, facts, grid, warnings)
    assert stats == {"beats": 60, "snapped": 3, "unsnapped": 0, "downbeats": 3, "half_bar": 0}
    assert record_cuts(record) == [4.75, 9.5, 14.25]
    assert all(slot["transition"] == {"type": "cut", "duration_s": 0} for slot in record["video"][1:])


def test_beat_snap_structure_cut_falls_back_to_the_nearest_beat():
    beat_s = [round(0.5 * index, 4) for index in range(40)]
    grid = {"beat_s": beat_s, "downbeat_s": [10.0, 20.0, 30.0], "beat_period_s": 0.5}
    record = {
        "video": [
            {"id": "v1", "clip": "a.mp4", "in_s": 0.0, "out_s": 2.4, "transition": {"type": "cut", "duration_s": 0}},
            {"id": "v2", "clip": "b.mp4", "in_s": 0.0, "out_s": 2.4, "transition": {"type": "cut", "duration_s": 0}},
        ]
    }
    plan = {
        "slot_plan": [
            {"id": "v1", "beat": "b1", "shot_key": "a/s1"},
            {"id": "v2", "beat": "b2", "shot_key": "b/s1"},
        ]
    }
    facts = {
        "a/s1": video_bound_fact("a/s1", "a.mp4", 0.0, 2.4),
        "b/s1": video_bound_fact("b/s1", "b.mp4", 0.0, 5.0),
    }
    warnings = []
    stats = llm_director.apply_beat_snap(record, plan, facts, grid, warnings)
    assert abs(record["video"][0]["out_s"] - 2.5) <= 0.02
    assert stats["unsnapped"] == 0


def test_beat_snap_click_track_every_cut_on_a_beat(tmp_path):
    track = tmp_path / "click.wav"
    make_click(track, duration=8.0)
    beats_path = tmp_path / "beats.json"
    assert beats.main([str(track), "--out", str(beats_path)]) == 0
    grid = llm_director.load_beats(beats_path)
    record = {
        "video": [
            {"id": "v1", "clip": "a.mp4", "in_s": 0.0, "out_s": 2.4, "transition": {"type": "cut", "duration_s": 0}},
            {"id": "v2", "clip": "b.mp4", "in_s": 0.0, "out_s": 2.4, "transition": {"type": "cut", "duration_s": 0}},
            {"id": "v3", "clip": "c.mp4", "in_s": 0.0, "out_s": 2.4, "transition": {"type": "cut", "duration_s": 0}},
        ]
    }
    plan = {
        "slot_plan": [
            {"id": "v1", "beat": "b1", "shot_key": "a/s1"},
            {"id": "v2", "beat": "b2", "shot_key": "b/s1"},
            {"id": "v3", "beat": "b3", "shot_key": "c/s1"},
        ]
    }
    facts = {
        "a/s1": video_bound_fact("a/s1", "a.mp4", 0.0, 2.4),
        "b/s1": video_bound_fact("b/s1", "b.mp4", 0.0, 2.4),
        "c/s1": video_bound_fact("c/s1", "c.mp4", 0.0, 2.4),
    }
    warnings = []
    stats = llm_director.apply_beat_snap(record, plan, facts, grid, warnings)
    assert stats["unsnapped"] == 0
    for cut in record_cuts(record):
        assert min(abs(cut - beat) for beat in grid["beat_s"]) <= 0.02


def test_beat_snap_video_window_exhausted_moves_within_the_slack():
    beat_s = [round(0.5 * index, 4) for index in range(40)]
    grid = {"beat_s": beat_s, "downbeat_s": [10.0, 20.0, 30.0], "beat_period_s": 0.5}
    record = {
        "video": [
            {"id": "v1", "clip": "a.mp4", "in_s": 0.0, "out_s": 2.4, "transition": {"type": "cut", "duration_s": 0}},
        ]
    }
    plan = {"slot_plan": [{"id": "v1", "beat": "b1", "shot_key": "a/s1"}]}
    facts = {"a/s1": video_bound_fact("a/s1", "a.mp4", 0.0, 2.4)}
    warnings = []
    stats = llm_director.apply_beat_snap(record, plan, facts, grid, warnings)
    assert abs(record["video"][0]["out_s"] - 2.5) <= 0.02
    assert stats["unsnapped"] == 0


def test_beat_snap_cut_beyond_the_slack_warns():
    grid = {"beat_s": [3.0, 8.0, 13.0], "downbeat_s": [3.0, 8.0, 13.0], "beat_period_s": 0.5}
    record = {
        "video": [
            {"id": "v1", "clip": "a.mp4", "in_s": 0.0, "out_s": 2.3, "transition": {"type": "cut", "duration_s": 0}},
        ]
    }
    plan = {"slot_plan": [{"id": "v1", "beat": "b1", "shot_key": "a/s1"}]}
    facts = {"a/s1": video_bound_fact("a/s1", "a.mp4", 0.0, 2.3)}
    warnings = []
    stats = llm_director.apply_beat_snap(record, plan, facts, grid, warnings)
    assert record["video"][0]["out_s"] == 2.3
    assert stats["unsnapped"] == 1
    assert any("not snapped" in warning for warning in warnings)


def test_beat_snap_downbeat_over_cap_falls_back_to_a_beat():
    beat_s = [round(0.5 * index, 4) for index in range(20)]
    grid = {"beat_s": beat_s, "downbeat_s": [2.0, 4.0, 6.0, 8.0], "beat_period_s": 0.5}
    slots = [{"id": "v1", "clip": "a.mp4", "in_s": 0.0, "out_s": 1.9}]
    bounds = [
        {"photo": False, "important": False, "low": 0.5, "high": 2.0, "window_in": 0.0, "cap": 1.5}
    ]
    warnings = []
    stats = llm_director.snap_slots(slots, bounds, grid, warnings)
    assert slots[0]["out_s"] == 1.5
    assert slots[0]["beat_grid"] == "beat"
    assert stats["unsnapped"] == 0
    assert not [warning for warning in warnings if "not snapped" in warning]


def test_beat_snap_prefers_downbeats():
    beat_s = [round(0.5 * index, 4) for index in range(140)]
    downbeat_s = beat_s[::4]
    grid = {"beat_s": beat_s, "downbeat_s": downbeat_s, "beat_period_s": 0.5}
    record = {
        "video": [
            {
                "id": f"v{index}",
                "clip": f"c{index}.mp4",
                "in_s": 0.0,
                "out_s": 3.0,
                "transition": {"type": "cut", "duration_s": 0},
            }
            for index in range(15)
        ]
    }
    plan = {
        "brief": {"target_duration_s": 60},
        "slot_plan": [
            {"id": f"v{index}", "beat": "b1", "shot_key": f"c{index}/s1"} for index in range(15)
        ],
    }
    facts = {
        f"c{index}/s1": video_bound_fact(f"c{index}/s1", f"c{index}.mp4", 0.0, 5.0)
        for index in range(15)
    }
    caps = {f"v{index}": llm_director.plan_pacing(plan)["slot_max"] for index in range(15)}
    warnings = []
    stats = llm_director.apply_beat_snap(record, plan, facts, grid, warnings, caps)
    assert stats["snapped"] >= 10
    downbeat_cuts = [slot for slot in record["video"] if slot.get("beat_grid") == "downbeat"]
    assert len(downbeat_cuts) >= stats["snapped"] / 2
    for slot, cut in zip(record["video"], record_cuts(record)):
        if slot.get("beat_grid") == "downbeat":
            assert min(abs(cut - downbeat) for downbeat in downbeat_s) <= 0.02


def test_run_with_beats_writes_cut_transitions(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    beats_path = tmp_path / "beats.json"
    write_json(beats_path, beat_grid(top_level=True, count=80))
    client = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, beats=str(beats_path))
    assert code == 0
    assert summary["status"] == "written"
    assert summary["beat_snap"]["beats"] == 80
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert edit["video"][0]["transition"] == {"type": "cut", "duration_s": 0}
    assert all(slot["transition"]["type"] == "cut" for slot in edit["video"][1:])


def test_run_without_beats_reports_no_beat_snap(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert summary["beat_snap"] is None


def test_help_lists_beats():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--beats" in result.stdout


def test_beat_snap_pair_absorb_moves_a_cut_pinned_at_the_window_end():
    beat_s = [round(0.5 * index, 4) for index in range(40)]
    grid = {"beat_s": beat_s, "downbeat_s": [10.0, 20.0, 30.0], "beat_period_s": 0.5}
    record = {
        "video": [
            {"id": "v1", "clip": "a.mp4", "in_s": 0.5, "out_s": 2.9, "transition": {"type": "cut", "duration_s": 0}},
            {"id": "p1", "kind": "photo", "photo": "p.jpg", "duration_s": 3.5, "transition": {"type": "cut", "duration_s": 0}},
        ]
    }
    plan = {
        "slot_plan": [
            {"id": "v1", "beat": "b1", "shot_key": "a/s1"},
            {"id": "p1", "beat": "b1", "shot_key": "p/p1"},
        ]
    }
    photo_fact = {
        "shot_key": "p/p1",
        "media_type": "photo",
        "photo": "p.jpg",
        "window_in_s": 0.0,
        "window_out_s": 0.04,
        "landing_at_s": None,
        "landing_hold_key": None,
        "keep_whole": False,
        "target_s": 3.0,
        "score": 0.5,
        "summary": None,
        "role": "hold",
        "motion_label": "static",
        "hero": False,
        "similar_to": [],
    }
    facts = {
        "a/s1": video_bound_fact("a/s1", "a.mp4", 0.0, 2.9),
        "p/p1": photo_fact,
    }
    warnings = []
    stats = llm_director.apply_beat_snap(record, plan, facts, grid, warnings)
    assert abs(record["video"][0]["out_s"] - 3.0) <= 0.02
    assert 2.0 <= record["video"][1]["duration_s"] <= 4.0
    assert not [warning for warning in warnings if "not snapped" in warning]
    assert stats["unsnapped"] == 0


def test_absorb_pair_rollback_restores_a_video_slot_exactly():
    left = {"id": "v5", "clip": "x.mp4", "in_s": 6.535, "out_s": 10.465, "speed": 1.0}
    right = {"id": "v6", "clip": "x.mp4", "in_s": 20.0, "out_s": 20.9, "speed": 1.0}
    moved = llm_director.absorb_pair(
        left,
        {"low": 6.0, "high": 12.0, "window_in": 6.0},
        right,
        {"low": 20.0, "high": 21.0, "window_in": 20.0},
        1.0,
    )
    assert moved is False
    assert left == {"id": "v5", "clip": "x.mp4", "in_s": 6.535, "out_s": 10.465, "speed": 1.0}
    assert "duration_s" not in left


def test_absorb_pair_rollback_restores_a_photo_slot_exactly():
    left = {"id": "p1", "kind": "photo", "photo": "p.jpg", "duration_s": 3.0}
    right = {"id": "v6", "clip": "x.mp4", "in_s": 20.0, "out_s": 20.4, "speed": 1.0}
    moved = llm_director.absorb_pair(
        left,
        {"photo": True, "important": False, "cap": None},
        right,
        {"low": 20.0, "high": 21.0, "window_in": 20.0},
        0.5,
    )
    assert moved is False
    assert left == {"id": "p1", "kind": "photo", "photo": "p.jpg", "duration_s": 3.0}
    assert "in_s" not in left and "out_s" not in left


def pollute_first_video_slot(monkeypatch):
    original = llm_director.attach_decisions

    def polluted(record, *args, **kwargs):
        original(record, *args, **kwargs)
        record["video"][0]["duration_s"] = None

    monkeypatch.setattr(llm_director, "attach_decisions", polluted)


def test_schema_failure_at_write_falls_back_to_rules(tmp_path, monkeypatch):
    workspace = build_workspace(tmp_path / "ws")
    pollute_first_video_slot(monkeypatch)
    client = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert summary["status"] == "fallback"
    assert summary["fallback"] == "rules"
    record = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert validate(record, "edit") == []
    assert record["meta"]["director"]["by"] == "rules"
    assert any(
        "duration_s" in warning and "not valid under any of the given schemas" in warning
        for warning in summary["warnings"]
    )
    assert any(note.startswith("edit:") and "duration_s" in note for note in record["meta"]["director"]["notes"])


def test_schema_failure_on_a_kept_draft_falls_back_to_rules(tmp_path, monkeypatch):
    workspace = build_workspace(tmp_path / "ws")
    pollute_first_video_slot(monkeypatch)
    client = StubClient(
        [
            plan_answer(),
            editor_answer(),
            reviewer_not_approved(),
            editor_answer(),
            reviewer_not_approved(),
        ]
    )
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, rounds=2)
    assert code == 0
    assert summary["status"] == "fallback"
    assert summary["fallback"] == "rules"
    record = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert validate(record, "edit") == []
    assert record["meta"]["director"]["by"] == "rules"
    assert any("duration_s" in note for note in record["meta"]["director"]["notes"])


def test_schema_failure_without_fallback_errors(tmp_path, monkeypatch):
    workspace = build_workspace(tmp_path / "ws")
    pollute_first_video_slot(monkeypatch)
    client = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, fallback_rules=False)
    assert code == EXIT_RUNTIME
    assert summary["status"] == "error"
    assert any("duration_s" in warning for warning in summary["warnings"])
    assert not (workspace / "edit.json").exists()


def write_previous_edit(workspace):
    write_json(
        workspace / "edit.json",
        {
            "schema_version": "0.1",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "tool": "test",
            "brief": {"text": BRIEF},
            "sources": [],
            "video": [
                {
                    "id": "v1",
                    "shot_key": "0071/s2",
                    "clip": "clip_0071_x.mp4",
                    "in_s": 4.0,
                    "out_s": 9.5,
                    "reason": "the tunnel",
                },
                {
                    "id": "v2",
                    "shot_key": "0057/s1",
                    "clip": "clip_0057_x.mp4",
                    "in_s": 2.0,
                    "out_s": 8.0,
                    "reason": "the bay",
                },
            ],
            "decisions": [
                {"id": "0071/s2", "status": "used", "reason": "the tunnel", "slot": "v1"},
                {"id": "0072/s3", "status": "rejected", "reason": "low score"},
            ],
        },
    )


def feedback_jsonl_rows(workspace):
    path = workspace / "feedback.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_feedback_block_reaches_screenwriter_and_editor(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    write_previous_edit(workspace)
    client = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    summary, code = llm_director.run(
        workspace, brief=BRIEF, client=client, feedback=["drop 0071/s2, tunnel longer"]
    )
    assert code == 0
    assert summary["status"] == "written"
    assert summary["feedback"] == ["drop 0071/s2, tunnel longer"]
    screenwriter_user = calls_for(client, "# Screenwriter")[0]["messages"][1]["content"]
    assert "Feedback to apply" in screenwriter_user
    assert "drop 0071/s2, tunnel longer" in screenwriter_user
    assert "Previous edit (the feedback refers to these ids):" in screenwriter_user
    assert '"shot_key": "0071/s2"' in screenwriter_user
    assert '"id": "0072/s3"' in screenwriter_user
    assert "low score" in screenwriter_user
    editor_user = calls_for(client, "# Editor")[0]["messages"][1]["content"]
    assert "Feedback to apply" in editor_user
    assert "drop 0071/s2, tunnel longer" in editor_user
    assert "0071/s2" in editor_user


def test_feedback_appends_one_line_per_flag(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    first = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    llm_director.run(workspace, brief=BRIEF, client=first, feedback=["drop 0071/s2", "tunnel longer"])
    rows = feedback_jsonl_rows(workspace)
    assert [row["text"] for row in rows] == ["drop 0071/s2", "tunnel longer"]
    for row in rows:
        assert row["action"] == "feedback"
        assert row["tool"] == "scripts/llm_director.py"
        assert row["ts"]
    second = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    llm_director.run(workspace, brief=BRIEF, client=second, feedback=["drop 0071/s2"])
    rows = feedback_jsonl_rows(workspace)
    assert len(rows) == 3
    assert rows[0]["text"] == "drop 0071/s2"
    assert rows[2]["text"] == "drop 0071/s2"


def test_feedback_forces_rerun_of_up_to_date_edit(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    first = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    llm_director.run(workspace, brief=BRIEF, client=first)
    second = StubClient([])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=second)
    assert code == 0
    assert summary["status"] == "skipped"
    assert second.calls == []
    third = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=third, feedback=["keep it as it is"])
    assert code == 0
    assert summary["status"] == "written"
    assert len(third.calls) == 3
    assert summary["feedback"] == ["keep it as it is"]


def test_feedback_without_previous_edit_still_reaches_the_screenwriter(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, feedback=["drop 0071/s2"])
    assert code == 0
    assert summary["status"] == "written"
    screenwriter_user = calls_for(client, "# Screenwriter")[0]["messages"][1]["content"]
    assert "Feedback to apply" in screenwriter_user
    assert "drop 0071/s2" in screenwriter_user
    assert "no previous edit.json" in screenwriter_user


def test_feedback_recorded_even_when_the_loop_falls_back(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(["not json at all", "still not json"])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, feedback=["drop 0071/s2"])
    assert code == 0
    assert summary["status"] == "fallback"
    rows = feedback_jsonl_rows(workspace)
    assert [row["text"] for row in rows] == ["drop 0071/s2"]


def test_no_feedback_keeps_summary_shape(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert summary["feedback"] == []


def test_help_lists_feedback():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--feedback" in result.stdout
    assert "drop 0071/s2" in result.stdout


def move_fact(key, clip, window_in, window_out, keep_whole=True):
    row = fact(key, clip, window_in, window_out, keep_whole=keep_whole)
    row.update({"role": "move", "motion_label": "pan_right"})
    return row


POLICY_PACING = {"slot_min": 2.0, "slot_target": 3.6, "slot_max": 5.0, "hero_max": 6.0}


def policy_plan(shot_keys):
    return {
        "brief": {"target_duration_s": 90},
        "slot_plan": [
            {"id": f"v{index + 1}", "shot_key": key, "target_s": 3.0, "why": "a", "keep_whole": False}
            for index, key in enumerate(shot_keys)
        ],
    }


def test_length_policy_extends_a_move_ramps_it_and_clamps_a_static_hold():
    facts = {
        "m/s1": move_fact("m/s1", "m.mp4", 0.0, 6.2),
        "h/s1": fact("h/s1", "h.mp4", 0.0, 20.0),
    }
    plan = policy_plan(["m/s1", "h/s1"])
    record = {
        "video": [
            {"id": "v1", "clip": "m.mp4", "in_s": 2.6, "out_s": 6.2, "score": 0.9, "role": "move", "motion_label": "pan_right", "shot_key": "m/s1"},
            {"id": "v2", "clip": "h.mp4", "in_s": 0.0, "out_s": 9.0, "score": 0.5, "role": "hold", "motion_label": "static", "shot_key": "h/s1"},
        ]
    }
    warnings = []
    caps = llm_director.apply_length_policy(record, plan, facts, POLICY_PACING, 6.0, warnings, speed_ramp=True)
    first, second = record["video"]
    assert first["in_s"] == 0.0 and first["out_s"] == 6.2
    assert 1.0 < first["middle_speed"] <= 1.2
    assert slot_screen_length(first) <= 6.0 + 1e-6
    assert slot_screen_length(second) == pytest.approx(5.0)
    assert "middle_speed" not in second
    assert caps == {"v1": 6.0, "v2": 5.0}
    assert any("exception" in warning for warning in warnings)
    assert not llm_director.validate_edit(record, plan, facts)


def test_validator_accepts_a_move_longer_than_slot_max_only_within_the_exception_share():
    facts = {
        f"m{index}/s1": move_fact(f"m{index}/s1", f"m{index}.mp4", 0.0, 6.0, keep_whole=False) for index in range(8)
    }
    plan = policy_plan(list(facts))
    video = [
        {"id": f"v{index + 1}", "clip": f"m{index}.mp4", "in_s": 0.0, "out_s": 6.0, "score": 0.5}
        for index in range(8)
    ]
    violations = llm_director.validate_edit({"video": video}, plan, facts)
    length_violations = [item for item in violations if item["rule"] == "length"]
    assert len(length_violations) == 8 - 2


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


PEAK_FACT = {**fact("p/s1", "p.mp4", 0.0, 20.0), "peak_s": 14.0, "peak_source": "vision"}


def test_expected_cut_is_centred_on_the_peak_inside_the_window():
    assert llm_director.expected_cut(PEAK_FACT, 4.0) == (12.0, 16.0)
    assert llm_director.expected_cut({**PEAK_FACT, "peak_s": 1.0}, 4.0) == (0.0, 4.0)
    assert llm_director.expected_cut({**PEAK_FACT, "peak_s": 19.5}, 4.0) == (16.0, 20.0)
    landing = {**PEAK_FACT, "peak_s": 2.0, "landing_at_s": 10.0}
    in_s, out_s = llm_director.expected_cut(landing, 3.0)
    assert out_s == pytest.approx(10.75) and in_s == pytest.approx(7.75)


def peak_edit(in_s, out_s):
    plan = {"slot_plan": [{"id": "v1", "shot_key": "p/s1", "target_s": 4.0, "why": "a", "keep_whole": False}]}
    record = {"video": [{"id": "v1", "clip": "p.mp4", "in_s": in_s, "out_s": out_s, "score": 0.5, "shot_key": "p/s1"}]}
    return plan, record


def test_validator_flags_a_trimmed_cut_that_misses_the_peak():
    plan, record = peak_edit(0.0, 4.0)
    violations = llm_director.validate_edit(record, plan, {"p/s1": PEAK_FACT})
    assert [item["rule"] for item in violations] == ["peak"]
    assert "14.00" in violations[0]["message"]


def test_validator_accepts_a_cut_centred_on_the_peak_and_a_whole_window():
    plan, record = peak_edit(12.0, 16.0)
    assert llm_director.validate_edit(record, plan, {"p/s1": PEAK_FACT}) == []
    whole = {**PEAK_FACT, "window_in_s": 0.0, "window_out_s": 4.0, "peak_s": 3.5}
    plan, record = peak_edit(0.0, 4.0)
    assert llm_director.validate_edit(record, plan, {"p/s1": whole}) == []


def test_validator_wants_a_hero_cut_centred_on_its_peak():
    hero = {**PEAK_FACT, "keep_whole": True, "role": "move", "motion_label": "pan_right"}
    plan, record = peak_edit(14.0, 20.0)
    violations = llm_director.validate_edit(record, plan, {"p/s1": hero}, 6.0)
    assert [item["rule"] for item in violations] == ["keep_whole"]
    plan, record = peak_edit(11.0, 17.0)
    assert llm_director.validate_edit(record, plan, {"p/s1": hero}, 6.0) == []


def test_normalise_recentres_a_cut_that_misses_the_peak_and_reports_it():
    in_s, out_s, notes = llm_director.normalise_window(0.0, 4.0, PEAK_FACT, {"target_s": 4.0}, 6.0, 2.0, 5.0)
    assert (in_s, out_s) == (12.0, 16.0)
    assert notes and "best moment" in notes[0]
    kept = llm_director.normalise_window(12.2, 16.2, PEAK_FACT, {"target_s": 4.0}, 6.0, 2.0, 5.0)
    assert kept[:2] == (12.2, 16.2) and kept[2] == []


def test_normalise_leaves_an_out_of_window_answer_for_the_validator():
    in_s, out_s, notes = llm_director.normalise_window(18.0, 23.0, PEAK_FACT, {"target_s": 5.0}, 6.0, 2.0, 5.0)
    assert (in_s, out_s) == (18.0, 23.0) and notes == []


def test_default_window_is_centred_on_the_peak():
    assert llm_director.default_window(PEAK_FACT, {"target_s": 4.0}, 6.0, 2.0, 5.0) == (12.0, 16.0)
    hero = {**PEAK_FACT, "keep_whole": True, "role": "move", "motion_label": "pan_right"}
    assert llm_director.default_window(hero, {}, 6.0, 2.0, 5.0) == (11.0, 17.0)


def test_policy_without_the_ramp_keeps_a_move_around_its_peak_at_normal_speed():
    facts = {"m/s1": {**move_fact("m/s1", "m.mp4", 0.0, 20.0), "peak_s": 9.0, "peak_source": "vision"}}
    plan = policy_plan(["m/s1"])
    record = {
        "video": [
            {"id": "v1", "clip": "m.mp4", "in_s": 0.0, "out_s": 20.0, "score": 0.9, "role": "move", "motion_label": "pan_right", "shot_key": "m/s1"},
        ]
    }
    llm_director.finalize_lengths(record, plan, facts, None, [], 6.0)
    slot = record["video"][0]
    assert "middle_speed" not in slot
    assert slot["in_s"] == pytest.approx(6.0) and slot["out_s"] == pytest.approx(12.0)
    assert (slot["peak_s"], slot["peak_source"], slot["centred"]) == (9.0, "vision", True)
    assert record["meta"]["speed_ramp"] is False


def test_policy_with_the_ramp_reproduces_the_middle_speed_behaviour():
    facts = {"m/s1": {**move_fact("m/s1", "m.mp4", 0.0, 20.0), "peak_s": 9.0, "peak_source": "vision"}}
    plan = policy_plan(["m/s1"])
    record = {
        "video": [
            {"id": "v1", "clip": "m.mp4", "in_s": 0.0, "out_s": 20.0, "score": 0.9, "role": "move", "motion_label": "pan_right", "shot_key": "m/s1"},
        ]
    }
    llm_director.finalize_lengths(record, plan, facts, None, [], 6.0, speed_ramp=True)
    slot = record["video"][0]
    assert slot["middle_speed"] == pytest.approx(1.2)
    assert slot_screen_length(slot) == pytest.approx(6.0, abs=0.01)
    assert (slot["in_s"] + slot["out_s"]) / 2 == pytest.approx(9.0, abs=0.01)
    assert record["meta"]["speed_ramp"] is True


def test_run_default_has_no_ramp_and_speed_ramp_reaches_the_record(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert all("middle_speed" not in slot for slot in edit["video"])
    assert edit["meta"]["speed_ramp"] is False
    assert all("peak_s" in slot and "centred" in slot for slot in edit["video"])
    client = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, speed_ramp=True)
    assert code == 0 and summary["status"] == "written"
    ramped = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert ramped["meta"]["speed_ramp"] is True


def test_up_to_date_edit_is_redone_when_the_speed_ramp_changes(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    llm_director.run(workspace, brief=BRIEF, client=client)
    again, _ = llm_director.run(workspace, brief=BRIEF, client=StubClient([]))
    assert again["status"] == "skipped"
    client = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    redone, _ = llm_director.run(workspace, brief=BRIEF, client=client, speed_ramp=True)
    assert redone["status"] == "written"


def test_help_lists_speed_ramp_and_the_prompts_describe_the_peak_rule():
    result = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True)
    assert "--speed-ramp" in result.stdout
    editor = (ROOT / "prompts" / "editor.md").read_text(encoding="utf-8")
    assert "peak_s" in editor and "centre" in editor.lower()
    assert "tail" not in editor.lower().replace("detail", "")


def test_repair_drops_a_slot_whose_window_is_too_short_for_any_valid_cut():
    facts = {
        "0001/s1": fact("0001/s1", "clip_0001_x.mp4", 0.0, 20.0),
        "0002/s1": fact("0002/s1", "clip_0002_x.mp4", 0.0, 1.2),
    }
    plan = {"slot_plan": [
        {"id": "v1", "shot_key": "0001/s1", "target_s": 5.0, "why": "a", "keep_whole": False},
        {"id": "v2", "shot_key": "0002/s1", "target_s": 5.0, "why": "b", "keep_whole": False},
    ]}
    record = {"video": [
        {"id": "v1", "clip": "clip_0001_x.mp4", "in_s": 7.5, "out_s": 12.5},
        {"id": "v2", "clip": "clip_0002_x.mp4", "in_s": 0.0, "out_s": 1.2},
    ]}
    violations = llm_director.validate_edit(record, plan, facts)
    assert any(item["rule"] == "length" for item in violations)
    notes = llm_director.repair_draft(record, plan, facts, {}, violations)
    assert [slot["id"] for slot in record["video"]] == ["v1"]
    assert llm_director.blocking(llm_director.validate_edit(record, plan, facts)) == []
    assert any("cannot give a valid cut" in note for note in notes)


def test_edit_records_the_reviewer_review(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert edit["meta"]["director"]["review"] == {"approved": True, "objections": []}
    assert validate(edit, "edit") == []


def test_edit_records_the_objections(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(
        [
            plan_answer(),
            editor_answer(),
            reviewer_not_approved(),
            editor_answer(),
            reviewer_not_approved(),
        ]
    )
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, rounds=2)
    assert code == 0
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    review = edit["meta"]["director"]["review"]
    assert review["approved"] is False
    assert len(review["objections"]) == 1
    objection = review["objections"][0]
    assert objection["slot"] == "v2"
    assert objection["shot"] == 2
    assert "too long" in objection["text"]
    assert "shorten to about 2.5 s" in objection["text"]
    assert validate(edit, "edit") == []


def test_old_edit_without_review_still_validates(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    llm_director.run(workspace, brief=BRIEF, client=client)
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    del edit["meta"]["director"]["review"]
    assert validate(edit, "edit") == []


def test_build_interpret_user_carries_the_reviewer_verdict(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    write_json(
        workspace / "edit.json",
        {
            "meta": {
                "director": {
                    "review": {
                        "approved": False,
                        "objections": [
                            {"slot": "v2", "shot": 2, "text": "shot 2 is too long"}
                        ],
                    }
                }
            }
        },
    )
    user = llm_director.build_interpret_user("x", workspace)
    assert "shot 2 is too long" in user


def test_build_interpret_user_without_a_review(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    user = llm_director.build_interpret_user("x", workspace)
    assert "reviewer's last verdict" not in user


def test_interpret_feedback_returns_a_reply(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(
        [json.dumps({"reply": "I will drop 0071/s2 and make the tunnel longer.", "can_do": True, "missing_tool": None})]
    )
    summary, code = llm_director.interpret_feedback(
        workspace, "drop 0071/s2, tunnel longer", client=client
    )
    assert code == 0
    assert summary["reply"] == "I will drop 0071/s2 and make the tunnel longer."
    assert summary["can_do"] is True
    assert summary["missing_tool"] is None
    assert summary["estimate_usd"] is None
    assert summary["estimate_s"] is None
    assert "wall_time_s" in summary
    assert summary["llm_cost_usd"] == pytest.approx(0.001)
    assert not (workspace / "edit.json").exists()
    rows = feedback_jsonl_rows(workspace)
    assert len(rows) == 1
    assert rows[0]["action"] == "reply"
    assert rows[0]["text"] == "drop 0071/s2, tunnel longer"
    assert rows[0]["reply"] == "I will drop 0071/s2 and make the tunnel longer."
    assert rows[0]["can_do"] is True
    assert rows[0]["missing_tool"] is None
    assert rows[0]["estimate"] == {"usd": None, "s": None}
    assert rows[0]["tool"] == "scripts/llm_director.py"


def test_interpret_feedback_reports_a_missing_tool(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(
        [
            json.dumps(
                {
                    "reply": "I cannot add subtitles; I can drop or trim shots. Noted.",
                    "can_do": False,
                    "missing_tool": "add subtitles",
                }
            )
        ]
    )
    summary, code = llm_director.interpret_feedback(workspace, "add subtitles", client=client)
    assert code == 0
    assert summary["can_do"] is False
    assert summary["missing_tool"] == "add subtitles"
    rows = feedback_jsonl_rows(workspace)
    assert len(rows) == 1
    assert rows[0]["action"] == "reply"
    assert rows[0]["can_do"] is False
    assert rows[0]["missing_tool"] == "add subtitles"


def test_interpret_feedback_retries_then_errors(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(["not json", "not json"])
    with pytest.raises(llm_director.LLMDirectorRuntimeError):
        llm_director.interpret_feedback(workspace, "x", client=client)
    assert len(client.calls) == 2


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


def test_cli_interpret_feedback_json(tmp_path, monkeypatch, capsys):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([json.dumps({"reply": "ok", "can_do": True, "missing_tool": None})])
    monkeypatch.setattr(llm_director, "make_client", lambda **kwargs: client)
    code = llm_director.main(["--workspace", str(workspace), "--interpret-feedback", "x", "--json"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    for key in ("reply", "can_do", "missing_tool", "estimate_usd", "estimate_s", "wall_time_s", "llm_cost_usd"):
        assert key in out


def build_many_workspace(root, count=100, stars=5):
    clips = []
    for index in range(count):
        name = f"clip_{index:04d}_x.mp4"
        clips.append(
            {
                "file": name,
                "path": f"/media/{name}",
                "duration_s": 60.0,
                "creation_time": f"2026-01-01T10:{index // 60:02d}:{index % 60:02d}",
            }
        )
    write_json(
        root / "ingest.json",
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_dir": "/media", "clips": clips},
    )
    shots = []
    for index, clip in enumerate(clips):
        stem = Path(clip["file"]).stem
        write_json(
            root / "quality" / f"{stem}.json",
            quality_doc(
                clip["file"],
                60.0,
                [segment("s1", 0.0, 20.0, "hold"), segment("s2", 20.0, 40.0, "hold")],
            ),
        )
        write_json(root / "motion" / f"{stem}.json", motion_doc(clip["file"], 60.0))
        shots.append(
            shotlog_shot(
                f"{index:04d}/s1",
                clip["file"],
                "s1",
                0.0,
                20.0,
                True,
                0.5 + (index % 30) / 100.0,
                f"shot {index}",
                f"interest {index}",
                hero=index < stars,
            )
        )
        shots.append(
            shotlog_shot(
                f"{index:04d}/s2",
                clip["file"],
                "s2",
                20.0,
                40.0,
                True,
                0.1 + (index % 10) / 100.0,
                f"second shot {index}",
                f"interest {index}",
            )
        )
    write_json(
        root / "shotlog.json",
        {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "weights": {"vision": 0.55, "labels": 0.45},
            "shots": shots,
        },
    )
    return root


def test_approved_on_round_one_stops_early(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert summary["rounds_used"] == 1
    assert summary["approved"] is True
    assert len(client.calls) == 3


def test_default_rounds_cap_at_two(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(
        [
            plan_answer(),
            editor_answer(),
            reviewer_not_approved(),
            editor_answer(),
            reviewer_not_approved(),
        ]
    )
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert summary["rounds_used"] == 2
    assert summary["approved"] is False


def test_rounds_three_runs_three_rounds(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(
        [
            plan_answer(),
            editor_answer(),
            reviewer_not_approved(),
            editor_answer(),
            reviewer_not_approved(),
            editor_answer(),
            reviewer_not_approved(),
        ]
    )
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, rounds=3)
    assert code == 0
    assert summary["rounds_used"] == 3
    assert summary["approved"] is False


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


def test_screenwriter_message_uses_the_shortlist(tmp_path):
    workspace = build_many_workspace(tmp_path / "ws")
    shotlog = llm_director.load_shotlog(workspace)
    context = llm_director._agent_context(workspace, shotlog, BRIEF, None, None, None, None)
    shortlist_keys = {row["key"] for row in context["shortlist"]}
    assert len(shortlist_keys) < len(context["shotlog_rows"])
    client = StubClient([plan_answer()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, dry_run=True)
    assert code == 0
    user = calls_for(client, "# Screenwriter")[0]["messages"][1]["content"]
    payload = user.split("Eligible shotlog rows:", 1)[1].strip()
    rows = json.loads(payload)
    assert {row["key"] for row in rows} == shortlist_keys
    assert summary["shortlist"] == len(shortlist_keys)
    assert summary["candidates"] == len(context["shotlog_rows"])


def test_json_summary_has_shortlist_and_candidates(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), editor_answer(), reviewer_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert "shortlist" in summary
    assert "candidates" in summary
    assert summary["shortlist"] <= summary["candidates"]



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
    fact = llm_director.make_fact(candidate, 6.0)
    assert fact["faces_max"] == 3
    assert fact["face_size_max"] == pytest.approx(0.2)
