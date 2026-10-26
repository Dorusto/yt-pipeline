import json

import pytest

import llm_director
from common.exit_codes import EXIT_USAGE
from test_llm_director import BRIEF, StubClient, build_workspace


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def feedback_rows(workspace):
    path = workspace / "feedback.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def plan_doc():
    return {
        "schema_version": "1",
        "generated_at": "2026-10-25T21:00:00+00:00",
        "tool": "director",
        "brief": {"text": BRIEF},
        "content_type": "vacation",
        "idea": "A calm morning on the water ends with the cave.",
        "structure": {
            "kind": "journey",
            "why": "the order carries the story",
            "beats": [{"id": "b1", "name": "The wake", "intent": "open calm", "target_s": 30}],
        },
        "shots": [
            {
                "id": "v1",
                "beat": "b1",
                "shot_key": "0056/s1",
                "purpose": "open on the wake",
                "technique": "none",
                "target_s": 3.0,
                "why": "sets the calm",
            }
        ],
    }


def report_doc():
    return {
        "schema_version": "1",
        "generated_at": "2026-10-25T21:00:00+00:00",
        "tool": "director_editor",
        "shots": [],
        "total_length_s": 3.0,
        "target_length_s": 90.0,
        "could_not_do": [],
        "similar_neighbours": [],
        "summary": "3 slots, 12.0 s of 90.0 s",
        "wall_time_s": 0.1,
        "llm_cost_usd": 0.0,
    }


def rounds_doc():
    return {
        "schema_version": "0.1",
        "generated_at": "2026-10-25T21:00:00+00:00",
        "tool": "scripts/llm_director.py",
        "rounds": [
            {
                "round": 1,
                "plan": plan_doc(),
                "report": report_doc(),
                "review": {"approved": True, "summary": "the cut works", "notes": []},
                "cost_usd": 0.001,
            }
        ],
        "total_cost_usd": 0.001,
    }


def test_plain_answer_appends_a_reply_row(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(
        [
            json.dumps(
                {
                    "reply": "I read this as: drop 0071/s2.",
                    "decision_note": None,
                    "can_do": "all",
                    "missing_tool": None,
                }
            )
        ]
    )
    summary, code = llm_director.director_chat(workspace, "drop 0071/s2", client=client)
    assert code == 0
    assert summary["reply"] == "I read this as: drop 0071/s2."
    assert summary["decision_note"] is None
    assert summary["can_do"] == "all"
    assert summary["missing_tool"] is None
    assert "wall_time_s" in summary
    assert summary["llm_cost_usd"] == pytest.approx(0.001)
    rows = feedback_rows(workspace)
    assert len(rows) == 1
    assert rows[0]["action"] == "reply"
    assert rows[0]["text"] == "drop 0071/s2"
    assert rows[0]["reply"] == "I read this as: drop 0071/s2."
    assert rows[0]["decision_note"] is None
    assert rows[0]["tool"] == "scripts/llm_director.py"


def test_decision_note_is_stored_as_an_owner_note(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(
        [
            json.dumps(
                {
                    "reply": "I read this as: drop 0071/s2.",
                    "decision_note": "drop 0071/s2",
                    "can_do": "all",
                    "missing_tool": None,
                }
            )
        ]
    )
    summary, code = llm_director.director_chat(workspace, "drop 0071/s2", client=client)
    assert code == 0
    assert summary["decision_note"] == "drop 0071/s2"
    rows = feedback_rows(workspace)
    assert len(rows) == 2
    assert rows[0]["action"] == "reply"
    assert rows[0]["decision_note"] == "drop 0071/s2"
    assert rows[1]["action"] == "feedback"
    assert rows[1]["text"] == "drop 0071/s2"


def test_context_carries_the_plan_report_rounds_notes_and_notebook(tmp_path, monkeypatch):
    workspace = build_workspace(tmp_path / "ws")
    write_json(workspace / "director_plan.json", plan_doc())
    write_json(workspace / "editor_report.json", report_doc())
    write_json(workspace / "director_rounds.json", rounds_doc())
    (workspace / "feedback.jsonl").write_text(
        json.dumps({"action": "feedback", "text": "note one"}) + "\n"
        + json.dumps({"action": "feedback", "text": "note two"}) + "\n",
        encoding="utf-8",
    )
    notebook = tmp_path / "notebook.json"
    write_json(
        notebook,
        {
            "schema_version": "1",
            "generated_at": "2026-10-25T00:00:00+00:00",
            "tool": "notebook",
            "lessons": [{"id": "l001", "text": "lesson from the notebook", "kind": "lesson"}],
        },
    )
    monkeypatch.setenv("YT_PIPELINE_NOTEBOOK", str(notebook))
    client = StubClient(
        [json.dumps({"reply": "ok", "decision_note": None, "can_do": "all", "missing_tool": None})]
    )
    summary, code = llm_director.director_chat(workspace, "drop 0071/s2", client=client)
    assert code == 0
    user = client.calls[0]["messages"][1]["content"]
    assert "A calm morning on the water ends with the cave." in user
    assert "3 slots, 12.0 s of 90.0 s" in user
    assert '"total_cost_usd": 0.001' in user
    assert "note one" in user and "note two" in user
    assert "lesson from the notebook" in user


def test_model_failure_raises(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient([RuntimeError("boom")])
    with pytest.raises(llm_director.LLMDirectorRuntimeError):
        llm_director.director_chat(workspace, "drop 0071/s2", client=client)


def test_unparseable_answer_is_retried_once(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(
        [
            "not json",
            json.dumps({"reply": "ok", "decision_note": None, "can_do": "all", "missing_tool": None}),
        ]
    )
    summary, code = llm_director.director_chat(workspace, "drop 0071/s2", client=client)
    assert code == 0
    assert summary["reply"] == "ok"
    assert len(client.calls) == 2


def test_chat_cli_prints_the_summary(tmp_path, monkeypatch, capsys):
    workspace = build_workspace(tmp_path / "ws")
    client = StubClient(
        [json.dumps({"reply": "ok", "decision_note": None, "can_do": "all", "missing_tool": None})]
    )
    monkeypatch.setattr(llm_director, "make_client", lambda **kwargs: client)
    code = llm_director.main(["--workspace", str(workspace), "--chat", "drop 0071/s2", "--json"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reply"] == "ok"


def test_chat_with_prepare_is_a_usage_error(tmp_path):
    workspace = build_workspace(tmp_path / "ws")
    code = llm_director.main(["--workspace", str(workspace), "--chat", "x", "--prepare"])
    assert code == EXIT_USAGE
