import json
from pathlib import Path

import pytest

import explain
import llm_director
from common.schema import validate
from test_llm_director import BRIEF, StubClient, build_workspace

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def flow_plan():
    return (FIXTURES / "director_plan_flow.json").read_text(encoding="utf-8")


def flow_plan_with_unknown_key():
    plan = json.loads(flow_plan())
    plan["shots"][1]["shot_key"] = "9999/s9"
    return json.dumps(plan)


def revised_plan():
    plan = json.loads(flow_plan())
    plan["shots"] = [
        {
            "id": "v1",
            "beat": "b1",
            "shot_key": "0057/s1",
            "purpose": "the bay opens the revised cut",
            "technique": "none",
            "moment": {"at_s": 10.0},
            "target_s": 3.0,
            "why": "The bay opens the revised cut.",
        },
        {
            "id": "v2",
            "beat": "b2",
            "shot_key": "0056/s1",
            "purpose": "the wake carries the revised middle",
            "technique": "none",
            "moment": {"at_s": 10.0},
            "target_s": 3.0,
            "why": "The wake carries the revised middle.",
        },
        {
            "id": "v3",
            "beat": "b3",
            "shot_key": "0080/s1",
            "purpose": "the cave ends the revised film",
            "technique": "none",
            "moment": {"at_s": 10.0},
            "target_s": 4.0,
            "why": "The cave ends the revised film.",
        },
    ]
    return plan


def review_approved():
    return json.dumps({"approved": True, "summary": "the cut works", "notes": []})


def review_revised():
    return json.dumps(
        {
            "approved": False,
            "summary": "the middle drags",
            "notes": ["opened on the bay instead"],
            "plan": revised_plan(),
        }
    )


def review_not_approved():
    return json.dumps(
        {
            "approved": False,
            "summary": "still too long",
            "notes": ["cut the last shot shorter"],
            "plan": revised_plan(),
        }
    )


class PictureStub:
    """A picture_fn stub that writes a small file and records every call."""

    def __init__(self):
        self.calls = []

    def __call__(self, path, t_s, out, width):
        self.calls.append({"path": path, "t_s": t_s, "out": Path(out), "width": width})
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"\xff\xd8fake")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_valid_plan_end_to_end_gives_llm(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([flow_plan(), review_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, picture_fn=PictureStub())
    assert code == 0
    assert summary["status"] == "written"
    assert summary["rounds_used"] == 1
    edit = read_json(workspace / "edit.json")
    assert edit["meta"]["director"]["by"] == "llm"
    assert edit["meta"]["director"]["approved"] is True
    assert validate(edit, "edit") == []
    assert (workspace / "director_plan.json").is_file()
    assert (workspace / "editor_report.json").is_file()
    rounds = read_json(workspace / "director_rounds.json")
    assert len(rounds["rounds"]) == 1


def test_unknown_shot_key_repaired_once(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([flow_plan_with_unknown_key(), flow_plan(), review_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, picture_fn=PictureStub())
    assert code == 0
    assert len(client.calls) == 3
    repair = client.calls[1]["messages"][-1]["content"]
    assert "unknown shot_key: 9999/s9" in repair
    plan = read_json(workspace / "director_plan.json")
    keys = [shot["shot_key"] for shot in plan["shots"]]
    assert "9999/s9" not in keys
    edit = read_json(workspace / "edit.json")
    assert edit["meta"]["director"]["by"] == "llm"


def test_two_invalid_answers_fall_back_to_rules(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(["not json", "still not json"])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client)
    assert code == 0
    assert summary["status"] == "fallback"
    edit = read_json(workspace / "edit.json")
    assert edit["meta"]["director"]["by"] == "rules"
    assert any("no usable plan" in warning for warning in summary["warnings"])


def test_approved_review_ends_after_round_one(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([flow_plan(), review_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, picture_fn=PictureStub())
    assert code == 0
    rounds = read_json(workspace / "director_rounds.json")
    assert len(rounds["rounds"]) == 1
    assert len(client.calls) == 2


def test_the_review_call_asks_for_a_review_not_a_plan(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([flow_plan(), review_approved()])
    llm_director.run(workspace, brief=BRIEF, client=client, picture_fn=PictureStub())
    first, second = client.calls
    assert "The review of the cut" not in first["messages"][0]["content"]
    assert "The review of the cut" in second["messages"][0]["content"]


def test_revised_plan_runs_the_editor_again_and_stops_after_two_rounds(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([flow_plan(), review_revised(), review_not_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, picture_fn=PictureStub())
    assert code == 0
    rounds = read_json(workspace / "director_rounds.json")
    assert len(rounds["rounds"]) == 2
    edit = read_json(workspace / "edit.json")
    assert edit["meta"]["director"]["approved"] is False
    assert edit["meta"]["director"]["notes"] == ["cut the last shot shorter"]
    assert edit["video"][0]["shot_key"] == "0057/s1"
    assert any("did not approve after 2 rounds" in warning for warning in summary["warnings"])


def test_the_cut_is_delivered_even_when_the_report_lists_problems(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    plan = json.loads(flow_plan())
    plan["shots"][1]["technique"] = "clean_entry"
    client = StubClient([json.dumps(plan), review_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, picture_fn=PictureStub())
    assert code == 0
    assert summary["fallback"] is None
    edit = read_json(workspace / "edit.json")
    assert edit["meta"]["director"]["by"] == "llm"
    report = read_json(workspace / "editor_report.json")
    assert report["could_not_do"]
    for note in report["could_not_do"]:
        assert note in summary["warnings"]


def test_pictures_made_once_per_slot(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    stub = PictureStub()
    client = StubClient([flow_plan(), review_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, picture_fn=stub)
    assert code == 0
    edit = read_json(workspace / "edit.json")
    assert len(stub.calls) == len(edit["video"])
    for call, slot in zip(stub.calls, edit["video"]):
        assert call["width"] == 320
        assert call["t_s"] == pytest.approx((slot["in_s"] + slot["out_s"]) / 2)
        assert call["out"].parent == workspace / "director" / "rounds" / "round-1"
        assert call["out"].name == f"{slot['id']}.jpg"


def test_cost_sums_every_call(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(
        [flow_plan_with_unknown_key(), flow_plan(), review_approved()], cost_per_call=0.002
    )
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, picture_fn=PictureStub())
    assert code == 0
    assert len(client.calls) == 3
    assert summary["llm_cost_usd"] == pytest.approx(0.006)
    edit = read_json(workspace / "edit.json")
    assert edit["meta"]["llm_cost_usd"] == pytest.approx(0.006)
    rounds = read_json(workspace / "director_rounds.json")
    assert rounds["total_cost_usd"] == pytest.approx(0.006)


def test_dry_run_writes_the_plan_only(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([flow_plan()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, dry_run=True)
    assert code == 0
    assert (workspace / "director_plan.json").is_file()
    assert not (workspace / "edit.json").exists()
    assert summary["baseline"]


def test_rerun_safety_skips(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    first = StubClient([flow_plan(), review_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=first, picture_fn=PictureStub())
    assert code == 0
    assert summary["status"] == "written"
    second = StubClient([])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=second)
    assert code == 0
    assert summary["status"] == "skipped"
    assert second.calls == []


def test_review_invalid_ends_the_rounds_and_delivers_the_cut(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([flow_plan(), "not json"])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, picture_fn=PictureStub())
    assert code == 0
    edit = read_json(workspace / "edit.json")
    assert edit["meta"]["director"]["by"] == "llm"
    assert edit["meta"]["director"]["approved"] is None
    assert any("no usable review" in warning for warning in summary["warnings"])


def test_reasoning_effort_reaches_every_call(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([flow_plan(), review_approved()])
    summary, code = llm_director.run(
        workspace, brief=BRIEF, client=client, reasoning_effort="low", picture_fn=PictureStub()
    )
    assert code == 0
    assert client.calls
    assert all(
        call["kwargs"].get("extra_body") == {"reasoning": {"effort": "low"}}
        for call in client.calls
    )


def test_json_summary_has_every_key(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([flow_plan(), review_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, picture_fn=PictureStub())
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
        "shortlist",
        "total_duration_s",
        "validator_violations",
        "warnings",
        "wall_time_s",
        "llm_cost_usd",
    ):
        assert key in summary


def test_feedback_reaches_the_director_payload(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([flow_plan(), review_approved()])
    summary, code = llm_director.run(
        workspace, brief=BRIEF, client=client, feedback=["drop 0071/s2"], picture_fn=PictureStub()
    )
    assert code == 0
    user = client.calls[0]["messages"][1]["content"]
    assert "drop 0071/s2" in user
    rows = [
        json.loads(line)
        for line in (workspace / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(row["action"] == "feedback" and row["text"] == "drop 0071/s2" for row in rows)


def test_flow_records_music_and_ambient_in_the_edit(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    music = tmp_path / "track.mp3"
    music.write_bytes(b"x")
    client = StubClient([flow_plan(), review_approved()])
    summary, code = llm_director.run(
        workspace,
        brief=BRIEF,
        client=client,
        picture_fn=PictureStub(),
        music=str(music),
        ambient_under_music_db=8,
    )
    assert code == 0
    edit = read_json(workspace / "edit.json")
    assert edit["audio"]["music"] == {"path": str(music.resolve())}
    assert edit["audio"]["ambient_under_music_db"] == 8


def test_flow_without_music_records_none(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([flow_plan(), review_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, picture_fn=PictureStub())
    assert code == 0
    edit = read_json(workspace / "edit.json")
    assert edit["audio"]["music"] is None
    assert edit["audio"]["ambient_under_music_db"] is None


def test_new_flow_edit_has_one_used_decision_per_plan_shot(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([flow_plan(), review_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, picture_fn=PictureStub())
    assert code == 0
    edit = read_json(workspace / "edit.json")
    plan = read_json(workspace / "director_plan.json")
    used = [row for row in edit["decisions"] if row["status"] == "used"]
    assert [row["id"] for row in used] == [shot["shot_key"] for shot in plan["shots"]]
    first = used[0]
    assert first["slot"] == "v1"
    assert first["reason"].startswith("open on the wake so the viewer settles into the calm — ")
    assert "(technique opening_shot done)" in first["reason"]
    assert first["score"] == 0.6
    assert first["clip"] == "clip_0056_x.mp4"
    assert first["window_in_s"] == 0.0
    assert first["window_out_s"] == 20.0
    assert "(technique" not in used[1]["reason"]


def test_editor_could_not_do_items_become_rejected_decisions(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    plan = json.loads(flow_plan())
    plan["shots"][1]["technique"] = "clean_entry"
    client = StubClient([json.dumps(plan), review_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, picture_fn=PictureStub())
    assert code == 0
    edit = read_json(workspace / "edit.json")
    rejected = [row for row in edit["decisions"] if row["status"] == "rejected"]
    assert rejected
    assert rejected[0]["id"] == "0057/s1"
    assert "no subject-enters event" in rejected[0]["reason"]
    assert rejected[0]["slot"] is None
    assert rejected[0]["score"] is None


def test_explain_renders_the_new_flow_decisions(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([flow_plan(), review_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, picture_fn=PictureStub())
    assert code == 0
    explain_summary, explain_code = explain.run(workspace)
    assert explain_code == 0
    html = (workspace / "explain.html").read_text(encoding="utf-8")
    assert "open on the wake so the viewer settles into the calm" in html


def test_flow_speed_ramp_reaches_the_editor(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([flow_plan(), review_approved()])
    summary, code = llm_director.run(
        workspace, brief=BRIEF, client=client, picture_fn=PictureStub(), speed_ramp=True
    )
    assert code == 0
    edit = read_json(workspace / "edit.json")
    move_slot = next(slot for slot in edit["video"] if slot["shot_key"] == "0080/s1")
    assert move_slot["middle_speed"] > 1.0
    assert edit["meta"]["speed_ramp"] is True


def test_flow_without_speed_ramp_has_no_middle_speed(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([flow_plan(), review_approved()])
    summary, code = llm_director.run(workspace, brief=BRIEF, client=client, picture_fn=PictureStub())
    assert code == 0
    edit = read_json(workspace / "edit.json")
    assert all("middle_speed" not in slot for slot in edit["video"])

