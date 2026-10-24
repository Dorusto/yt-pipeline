import inspect
import json

import pytest

import llm_director
from common.pacing import estimate_all_good
from common.schema import validate
from test_llm_director import (
    BRIEF,
    build_workspace,
    editor_answer,
    editor_answer_out_of_window,
    plan_answer,
)


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
    inputs = workspace / "director" / "inputs.md"
    assert inputs.exists()
    text = inputs.read_text(encoding="utf-8")
    assert BRIEF in text
    assert "shot_plan.json" in text
    assert "edit.json" in text
    assert "0056/s1" in text
    assert "window_in_s" in text
    assert "# Playbook" in text
    assert not (workspace / "edit.json").exists()


def test_apply_writes_agent_edit(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    (workspace / "shot_plan.json").write_text(plan_answer(), encoding="utf-8")
    (workspace / "edit.json").write_text(editor_answer(), encoding="utf-8")
    summary, code = llm_director.apply_agent_answer(workspace, brief=BRIEF)
    assert code == 0
    assert summary["status"] == "written"
    assert summary["slots"] == 3
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert validate(edit, "edit") == []
    assert edit["meta"]["director"]["by"] == "agent"
    assert edit["meta"]["director"]["approved"] is True


def test_apply_repairs_an_invalid_edit(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    (workspace / "shot_plan.json").write_text(plan_answer(), encoding="utf-8")
    (workspace / "edit.json").write_text(editor_answer_out_of_window(), encoding="utf-8")
    summary, code = llm_director.apply_agent_answer(workspace, brief=BRIEF)
    assert code == 0
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert validate(edit, "edit") == []
    assert any("repaired" in warning for warning in summary["warnings"])


def test_apply_rejects_an_unrepairable_edit(tmp_path, capsys):
    workspace = build_workspace(tmp_path / "ws")
    shotlog_path = workspace / "shotlog.json"
    data = json.loads(shotlog_path.read_text(encoding="utf-8"))
    for shot in data["shots"]:
        if shot["key"] == "0056/s1":
            shot["similar_to"] = ["0057/s1"]
    shotlog_path.write_text(json.dumps(data), encoding="utf-8")
    (workspace / "shot_plan.json").write_text(plan_answer(), encoding="utf-8")
    (workspace / "edit.json").write_text(editor_answer(), encoding="utf-8")
    code = llm_director.main(["--workspace", str(workspace), "--brief", BRIEF, "--apply", "--json"])
    assert code == 2
    captured = capsys.readouterr()
    assert "similar" in captured.err


def test_apply_skips_when_already_applied(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    (workspace / "shot_plan.json").write_text(plan_answer(), encoding="utf-8")
    (workspace / "edit.json").write_text(editor_answer(), encoding="utf-8")
    first, code = llm_director.apply_agent_answer(workspace, brief=BRIEF)
    assert code == 0
    assert first["status"] == "written"
    second, code = llm_director.apply_agent_answer(workspace, brief=BRIEF)
    assert code == 0
    assert second["status"] == "skipped"


def test_apply_force_reapplies_a_complete_edit(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    (workspace / "shot_plan.json").write_text(plan_answer(), encoding="utf-8")
    (workspace / "edit.json").write_text(editor_answer(), encoding="utf-8")
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
    (workspace / "shot_plan.json").write_text(plan_answer(), encoding="utf-8")
    (workspace / "edit.json").write_text(editor_answer(), encoding="utf-8")
    summary, code = llm_director.apply_agent_answer(workspace, brief=BRIEF, target_duration_s=0)
    assert code == 0
    edit = json.loads((workspace / "edit.json").read_text(encoding="utf-8"))
    assert edit["brief"]["target_duration_s"] == expected


def test_apply_missing_plan_is_usage_error(tmp_path, capsys):
    workspace = build_workspace(tmp_path / "ws")
    (workspace / "edit.json").write_text(editor_answer(), encoding="utf-8")
    code = llm_director.main(["--workspace", str(workspace), "--brief", BRIEF, "--apply", "--json"])
    assert code == 2
    captured = capsys.readouterr()
    assert "shot_plan.json" in captured.err


def test_prepare_apply_roundtrip_via_cli(tmp_path, capsys):
    workspace = build_workspace(tmp_path / "ws")
    code = llm_director.main(["--workspace", str(workspace), "--brief", BRIEF, "--prepare", "--json"])
    assert code == 0
    prepared = json.loads(capsys.readouterr().out.strip())
    assert prepared["status"] == "prepared"
    (workspace / "shot_plan.json").write_text(plan_answer(), encoding="utf-8")
    (workspace / "edit.json").write_text(editor_answer(), encoding="utf-8")
    code = llm_director.main(["--workspace", str(workspace), "--brief", BRIEF, "--apply", "--json"])
    assert code == 0
    written = json.loads(capsys.readouterr().out.strip())
    assert written["status"] == "written"


def test_apply_agent_answer_has_no_notebook_parameter():
    parameters = inspect.signature(llm_director.apply_agent_answer).parameters
    assert "notebook" not in parameters


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
