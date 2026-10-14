import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

import llm_director
from common.llm import BudgetExceeded
from common.photos import ken_burns_for
from common.schema import validate

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
                {"id": "v1", "shot_key": "0056/s1", "in_s": 2.0, "out_s": 25.0, "transition": "cut"},
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
    return [call for call in client.calls if call["messages"][0]["content"].startswith(marker)]


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


def test_max_rounds_with_violations_falls_back(tmp_path):
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
    assert summary["status"] == "fallback"
    assert summary["fallback"] == "rules"
    assert summary["validator_violations"] >= 1


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


def test_screenwriter_menu_is_cuttable_pool(tmp_path):
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


def test_budget_exceeded_no_fallback_exits_2(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([plan_answer(), BudgetExceeded("budget reached")])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, fallback_rules=False)
    assert code == 2
    assert summary["status"] == "error"
    assert not (workspace / "edit.json").exists()


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
    assert code == 1
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
    plan = {"slot_plan": [{"id": "v1", "shot_key": "0001/s1", "target_s": 5.0, "why": "a", "keep_whole": True}]}
    record = {"video": [{"id": "v1", "clip": "clip_0001_x.mp4", "in_s": 0.0, "out_s": 5.0}]}
    violations = llm_director.validate_edit(record, plan, facts)
    assert any(item["rule"] == "keep_whole" for item in violations)

    facts = {"0001/s1": fact("0001/s1", "clip_0001_x.mp4", 0.0, 20.0)}
    plan = {"slot_plan": [{"id": "v1", "shot_key": "0001/s1", "target_s": 5.0, "why": "a", "keep_whole": False}]}
    record = {"video": [{"id": "v1", "clip": "clip_0001_x.mp4", "in_s": 0.0, "out_s": 1.0}]}
    violations = llm_director.validate_edit(record, plan, facts)
    assert any(item["rule"] == "length" for item in violations)


def fact(key, clip, window_in, window_out, keep_whole=False, similar_to=None):
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
        "similar_to": similar_to or [],
    }


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


def test_cli_missing_brief_exits_1(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--workspace", str(workspace)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
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
    assert slot["in_s"] == pytest.approx(14.0)
    assert slot["out_s"] == pytest.approx(20.0)
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
    assert slot["out_s"] - slot["in_s"] >= llm_director.MIN_SLOT_S - llm_director.TOLERANCE_S


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
    assert slot["in_s"] == pytest.approx(14.0)
    assert slot["out_s"] == pytest.approx(20.0)


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
    assert any(item["rule"] == "same_take" for item in violations)


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


def test_beat_snap_uses_downbeats_at_structure_boundaries():
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
    assert stats == {"beats": 60, "snapped": 3, "unsnapped": 0}
    assert record_cuts(record) == [2.5, 4.75, 7.0]
    assert all(slot["transition"] == {"type": "cut", "duration_s": 0} for slot in record["video"][1:])


def test_beat_snap_video_window_exhausted_warns():
    beat_s = [round(0.5 * index, 4) for index in range(40)]
    grid = {"beat_s": beat_s, "downbeat_s": beat_s[::4], "beat_period_s": 0.5}
    record = {
        "video": [
            {"id": "v1", "clip": "a.mp4", "in_s": 0.0, "out_s": 2.4, "transition": {"type": "cut", "duration_s": 0}},
        ]
    }
    plan = {"slot_plan": [{"id": "v1", "beat": "b1", "shot_key": "a/s1"}]}
    facts = {"a/s1": video_bound_fact("a/s1", "a.mp4", 0.0, 2.4)}
    warnings = []
    stats = llm_director.apply_beat_snap(record, plan, facts, grid, warnings)
    assert record["video"][0]["out_s"] == 2.4
    assert stats["unsnapped"] == 1
    assert any("not snapped" in warning for warning in warnings)


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
    grid = {"beat_s": beat_s, "downbeat_s": beat_s[::4], "beat_period_s": 0.5}
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
    assert record["video"][0]["out_s"] == 2.9
    assert abs(record["video"][0]["in_s"] - 0.4) <= 0.02
    assert 2.0 <= record["video"][1]["duration_s"] <= 4.0
    assert not [warning for warning in warnings if "not snapped" in warning]
    assert stats["unsnapped"] == 0
