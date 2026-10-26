import inspect
import json

import pytest

import llm_director
from common.pacing import estimate_all_good
from common.schema import validate
from test_director_flow import PictureStub, flow_plan, revised_plan
from test_llm_director import BRIEF, build_workspace


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


def write_plan(workspace):
    (workspace / "director_plan.json").write_text(flow_plan(), encoding="utf-8")


def test_prepare_writes_inputs_and_makes_no_model_call(tmp_path, monkeypatch):
    workspace = build_workspace(tmp_path / "ws")

    def boom(*args, **kwargs):
        raise AssertionError("prepare must not call the model")

    monkeypatch.setattr(llm_director, "make_client", boom)
    summary, code = llm_director.prepare_agent_inputs(workspace, brief=BRIEF)
    assert code == 0
    assert summary["status"] == "prepared"
    assert summary["llm_cost_usd"] == 0.0
    assert summary["candidates"] == 3
    assert summary["review"] is False
    inputs = workspace / "director" / "inputs.md"
    assert inputs.exists()
    text = inputs.read_text(encoding="utf-8")
    assert BRIEF in text
    assert "# Director — the idea, the structure, the shots" in text
    assert "director_plan.json" in text
    assert "schemas/director_plan.schema.json" in text
    assert "0056/s1" in text
    assert "window_in_s" in text
    payload_text = text.split("## The payload", 1)[1].split("```json", 1)[1].split("```", 1)[0].strip()
    payload = json.loads(payload_text)
    assert payload["brief"]["text"] == BRIEF
    assert {fact["shot_key"] for fact in payload["facts"]} == {"0056/s1", "0057/s1", "0080/s1"}
    assert not (workspace / "edit.json").exists()


def test_apply_writes_agent_edit(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    write_plan(workspace)
    summary, code = llm_director.apply_agent_answer(workspace, brief=BRIEF)
    assert code == 0
    assert summary["status"] == "written"
    assert summary["slots"] == 3
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert validate(edit, "edit") == []
    assert edit["meta"]["director"]["by"] == "agent"
    assert (workspace / "editor_report.json").is_file()
    rounds = json.loads((workspace / "director_rounds.json").read_text(encoding="utf-8"))
    assert len(rounds["rounds"]) == 1


def test_apply_skips_when_already_applied(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    write_plan(workspace)
    first, code = llm_director.apply_agent_answer(workspace, brief=BRIEF)
    assert code == 0
    assert first["status"] == "written"
    second, code = llm_director.apply_agent_answer(workspace, brief=BRIEF)
    assert code == 0
    assert second["status"] == "skipped"


def test_apply_force_reapplies_a_complete_edit(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    write_plan(workspace)
    first, code = llm_director.apply_agent_answer(workspace, brief=BRIEF)
    assert code == 0
    assert first["status"] == "written"
    second, code = llm_director.apply_agent_answer(workspace, brief=BRIEF)
    assert code == 0
    assert second["status"] == "skipped"
    third, code = llm_director.apply_agent_answer(workspace, brief=BRIEF, force=True)
    assert code == 0
    assert third["status"] == "written"
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert validate(edit, "edit") == []
    assert edit["meta"]["director"]["by"] == "agent"


def test_apply_missing_plan_is_usage_error(tmp_path, capsys):
    workspace = build_workspace(tmp_path / "ws")
    code = llm_director.main(["--workspace", str(workspace), "--brief", BRIEF, "--apply", "--json"])
    assert code == 2
    captured = capsys.readouterr()
    assert "director_plan.json" in captured.err


def test_apply_invalid_plan_is_usage_error(tmp_path, capsys):
    workspace = build_workspace(tmp_path / "ws")
    plan = json.loads(flow_plan())
    plan["shots"][0]["shot_key"] = "9999/s9"
    (workspace / "director_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    code = llm_director.main(["--workspace", str(workspace), "--brief", BRIEF, "--apply", "--json"])
    assert code == 2
    captured = capsys.readouterr()
    assert "9999/s9" in captured.err


def test_prepare_review_writes_pictures_and_report(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    write_plan(workspace)
    llm_director.apply_agent_answer(workspace, brief=BRIEF)
    stub = PictureStub()
    summary, code = llm_director.prepare_agent_inputs(
        workspace, brief=BRIEF, review=True, picture_fn=stub
    )
    assert code == 0
    assert summary["review"] is True
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert len(stub.calls) == len(edit["video"])
    text = (workspace / "director" / "inputs.md").read_text(encoding="utf-8")
    assert "## The editor report" in text
    assert "director_review.json" in text
    assert "schemas/director_review.schema.json" in text
    for call in stub.calls:
        assert str(call["out"]) in text


def test_apply_review_approved_finishes(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    write_plan(workspace)
    llm_director.apply_agent_answer(workspace, brief=BRIEF)
    (workspace / "director_review.json").write_text(review_approved(), encoding="utf-8")
    summary, code = llm_director.apply_agent_answer(workspace, brief=BRIEF)
    assert code == 0
    assert summary["status"] == "written"
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert edit["meta"]["director"]["by"] == "agent"
    assert edit["meta"]["director"]["approved"] is True
    assert not (workspace / "director_review.json").exists()
    assert (workspace / "director_review.applied.json").is_file()


def test_apply_review_revised_runs_round_two(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    write_plan(workspace)
    llm_director.apply_agent_answer(workspace, brief=BRIEF)
    (workspace / "director_review.json").write_text(review_revised(), encoding="utf-8")
    summary, code = llm_director.apply_agent_answer(workspace, brief=BRIEF)
    assert code == 0
    assert summary["rounds_used"] == 2
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert edit["meta"]["director"]["approved"] is None
    assert edit["video"][0]["shot_key"] == "0057/s1"
    rounds = json.loads((workspace / "director_rounds.json").read_text(encoding="utf-8"))
    assert len(rounds["rounds"]) == 2
    stub = PictureStub()
    llm_director.prepare_agent_inputs(workspace, brief=BRIEF, review=True, picture_fn=stub)
    assert stub.calls
    assert stub.calls[0]["out"].parent.name == "round-2"
    (workspace / "director_review.json").write_text(review_not_approved(), encoding="utf-8")
    summary, code = llm_director.apply_agent_answer(workspace, brief=BRIEF)
    assert code == 0
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert edit["meta"]["director"]["approved"] is False
    assert edit["meta"]["director"]["notes"] == ["cut the last shot shorter"]


def test_apply_review_invalid_is_usage_error(tmp_path, capsys):
    workspace = build_workspace(tmp_path / "ws")
    write_plan(workspace)
    llm_director.apply_agent_answer(workspace, brief=BRIEF)
    (workspace / "director_review.json").write_text("not json", encoding="utf-8")
    code = llm_director.main(["--workspace", str(workspace), "--brief", BRIEF, "--apply", "--json"])
    assert code == 2
    captured = capsys.readouterr()
    assert "review" in captured.err


def test_prepare_and_apply_use_all_good_estimate(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    raw = json.loads((workspace / "shotlog.json").read_text(encoding="utf-8"))
    expected = max(20.0, estimate_all_good(raw)["seconds"])
    long_cut = llm_director.pacing_table(llm_director.LONG_CUT_PACING_S)
    summary, code = llm_director.prepare_agent_inputs(workspace, brief=BRIEF, target_duration_s=0)
    assert code == 0
    text = (workspace / "director" / "inputs.md").read_text(encoding="utf-8")
    assert f"target_duration_s: {expected}" in text
    assert f"slot_min: {long_cut['slot_min']}" in text
    assert f"slot_max: {long_cut['slot_max']}" in text
    write_plan(workspace)
    summary, code = llm_director.apply_agent_answer(workspace, brief=BRIEF, target_duration_s=0)
    assert code == 0
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert edit["brief"]["target_duration_s"] == expected


def test_prepare_with_force_is_usage_error(tmp_path, capsys):
    workspace = build_workspace(tmp_path / "ws")
    code = llm_director.main(
        ["--workspace", str(workspace), "--brief", BRIEF, "--prepare", "--force", "--json"]
    )
    assert code == 2
    captured = capsys.readouterr()
    assert "--force" in captured.err


def test_review_without_prepare_is_usage_error(tmp_path, capsys):
    workspace = build_workspace(tmp_path / "ws")
    code = llm_director.main(
        ["--workspace", str(workspace), "--brief", BRIEF, "--review", "--json"]
    )
    assert code == 2
    captured = capsys.readouterr()
    assert "--review" in captured.err


def test_apply_agent_answer_has_no_notebook_parameter():
    parameters = inspect.signature(llm_director.apply_agent_answer).parameters
    assert "notebook" not in parameters


def test_agent_summaries_carry_shortlist(tmp_path, capsys):
    workspace = build_workspace(tmp_path / "ws")
    code = llm_director.main(["--workspace", str(workspace), "--brief", BRIEF, "--prepare", "--json"])
    assert code == 0
    prepared = json.loads(capsys.readouterr().out.strip())
    assert "shortlist" in prepared
    assert isinstance(prepared["shortlist"], int)
    write_plan(workspace)
    code = llm_director.main(["--workspace", str(workspace), "--brief", BRIEF, "--apply", "--json"])
    assert code == 0
    written = json.loads(capsys.readouterr().out.strip())
    assert written["status"] == "written"
    assert "shortlist" in written
    code = llm_director.main(["--workspace", str(workspace), "--brief", BRIEF, "--apply", "--json"])
    assert code == 0
    skipped = json.loads(capsys.readouterr().out.strip())
    assert skipped["status"] == "skipped"
    assert "shortlist" in skipped
    assert isinstance(skipped["shortlist"], int)


def test_prepare_writes_the_notebook_block(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    notebook_file = tmp_path / "notebook.json"
    notebook_file.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "generated_at": "2026-10-24T00:00:00+00:00",
                "tool": "notebook",
                "lessons": [{"id": "l001", "text": "keep the tunnel longer", "kind": "lesson"}],
            }
        ),
        encoding="utf-8",
    )
    summary, code = llm_director.prepare_agent_inputs(
        workspace, brief=BRIEF, notebook=str(notebook_file)
    )
    assert code == 0
    text = (workspace / "director" / "inputs.md").read_text(encoding="utf-8")
    assert "## Notebook" in text
    assert "keep the tunnel longer" in text


def test_review_inputs_carry_the_review_prompt(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    write_plan(workspace)
    llm_director.apply_agent_answer(workspace, brief=BRIEF)
    llm_director.prepare_agent_inputs(workspace, brief=BRIEF, review=True, picture_fn=PictureStub())
    text = (workspace / "director" / "inputs.md").read_text(encoding="utf-8")
    assert "## The review of the cut" in text
    assert "ONE JSON object" in text
