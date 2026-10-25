import http.client
import json
import subprocess
import sys
import threading
from pathlib import Path

import llm_director
import notebook
import serve
from common.llm import JSONResponse
from common.notebook import notebook_block
from common.schema import validate

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "notebook.py"


def run_notebook(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_notebook(path, lessons, waiting=None):
    document = {
        "schema_version": "1",
        "generated_at": "2026-10-24T00:00:00+00:00",
        "tool": "notebook",
        "lessons": lessons,
        "waiting": waiting or [],
    }
    Path(path).write_text(json.dumps(document), encoding="utf-8")


def make_workspace(tmp_path, ts="2020-01-01T00:00:00+00:00"):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "feedback.jsonl").write_text(
        json.dumps({"ts": ts, "action": "feedback", "text": "keep the tunnel longer"}) + "\n",
        encoding="utf-8",
    )
    return workspace


def stub_learn_client(monkeypatch, answer, merge_answer=None, cost=0.01):
    calls = []
    sent = []

    class StubClient:
        def __init__(self):
            self.count = 0

        def complete_json(self, role, messages, model=None, **kwargs):
            self.count += 1
            sent.append(messages)
            payload = answer if self.count == 1 else (
                merge_answer if merge_answer is not None else answer
            )
            return JSONResponse(
                data=payload,
                text="",
                model="stub-model",
                role=role,
                cost_usd=cost,
                attempts=1,
            )

    def fake_make_client(config=None, ledger_path=None):
        calls.append(config)
        return StubClient()

    monkeypatch.setattr(notebook, "make_client", fake_make_client)
    return sent


def test_add_then_list_increments_ids(tmp_path):
    notebook = tmp_path / "notebook.json"
    first = run_notebook("--notebook", str(notebook), "--add", "one", "--json")
    assert first.returncode == 0
    summary = json.loads(first.stdout)
    assert summary["ok"] is True
    assert summary["count"] == 1
    assert summary["changed"]["added"] == ["l001"]
    second = run_notebook("--notebook", str(notebook), "--add", "two", "--json")
    assert second.returncode == 0
    summary = json.loads(second.stdout)
    assert summary["count"] == 2
    assert summary["changed"]["added"] == ["l002"]
    listed = run_notebook("--notebook", str(notebook), "--list", "--json")
    assert listed.returncode == 0
    summary = json.loads(listed.stdout)
    assert summary["count"] == 2
    assert [lesson["id"] for lesson in summary["lessons"]] == ["l001", "l002"]


def test_limit_refuses_the_third_add_and_leaves_the_file(tmp_path):
    notebook = tmp_path / "notebook.json"
    run_notebook("--notebook", str(notebook), "--limit", "2", "--add", "one", "--add", "two")
    before = notebook.read_text(encoding="utf-8")
    result = run_notebook("--notebook", str(notebook), "--limit", "2", "--add", "three", "--json")
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["exit_code"] == 2
    assert "notebook is full (2 lessons)" in payload["error"]
    assert notebook.read_text(encoding="utf-8") == before


def test_merge_replaces_two_lessons_with_one(tmp_path):
    notebook = tmp_path / "notebook.json"
    run_notebook("--notebook", str(notebook), "--add", "one", "--add", "two", "--add", "three")
    result = run_notebook(
        "--notebook", str(notebook), "--merge", "l001,l002", "--text", "merged", "--json"
    )
    assert result.returncode == 0
    summary = json.loads(result.stdout)
    assert summary["count"] == 2
    assert summary["changed"]["merged"] == ["l001", "l002"]
    assert summary["changed"]["removed"] == ["l001", "l002"]
    assert [lesson["id"] for lesson in summary["lessons"]] == ["l003", "l004"]


def test_merge_with_a_repeated_id_is_usage(tmp_path):
    notebook = tmp_path / "notebook.json"
    run_notebook("--notebook", str(notebook), "--add", "one", "--add", "two")
    before = notebook.read_text(encoding="utf-8")
    result = run_notebook(
        "--notebook", str(notebook), "--merge", "l001,l001", "--text", "merged", "--json"
    )
    assert result.returncode == 2
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["ok"] is False
    assert payload["exit_code"] == 2
    assert notebook.read_text(encoding="utf-8") == before


def test_learn_writes_at_most_three_lessons_and_sets_the_marker(tmp_path, monkeypatch):
    notebook_file = tmp_path / "notebook.json"
    workspace = make_workspace(tmp_path)
    answer = {
        "lessons": [
            {"text": "keep the tunnel longer", "kind": "lesson", "part": None},
            {"text": "the sponsor line repeats", "kind": "proposal", "part": "members_sentence"},
            {"text": "third lesson", "kind": "lesson", "part": None},
            {"text": "fourth lesson", "kind": "lesson", "part": None},
        ],
        "remove": [],
    }
    stub_learn_client(monkeypatch, answer)
    summary = notebook.learn(str(workspace), str(notebook_file), 40)
    assert summary["status"] == "written"
    assert summary["count"] == 3
    assert len(summary["added"]) == 3
    assert summary["llm_cost_usd"] > 0
    assert (workspace / ".learn.json").is_file()
    document = read_json(notebook_file)
    assert len(document["lessons"]) == 3
    assert document["lessons"][1]["kind"] == "proposal"
    assert document["lessons"][1]["part"] == "members_sentence"
    assert validate(document, "notebook") == []


def test_learn_skips_when_there_is_no_new_feedback(tmp_path, monkeypatch):
    notebook_file = tmp_path / "notebook.json"
    workspace = make_workspace(tmp_path)
    answer = {"lessons": [{"text": "one", "kind": "lesson", "part": None}], "remove": []}
    stub_learn_client(monkeypatch, answer)
    first = notebook.learn(str(workspace), str(notebook_file), 40)
    assert first["status"] == "written"
    before = notebook_file.read_text(encoding="utf-8")

    def boom(*args, **kwargs):
        raise AssertionError("the skipped pass must not call the model")

    monkeypatch.setattr(notebook, "make_client", boom)
    second = notebook.learn(str(workspace), str(notebook_file), 40)
    assert second["status"] == "skipped"
    assert second["reason"] == "no new feedback"
    assert second["llm_cost_usd"] == 0.0
    assert second["added"] == [] and second["removed"] == []
    assert notebook_file.read_text(encoding="utf-8") == before


def test_learn_merges_when_over_the_limit(tmp_path, monkeypatch):
    notebook_file = tmp_path / "notebook.json"
    write_notebook(
        notebook_file,
        [
            {"id": "l001", "text": "old one", "kind": "lesson", "created_at": "2026-01-01T00:00:00+00:00"},
            {"id": "l002", "text": "old two", "kind": "lesson", "created_at": "2026-01-02T00:00:00+00:00"},
            {"id": "l003", "text": "old three", "kind": "lesson", "created_at": "2026-01-03T00:00:00+00:00"},
        ],
    )
    workspace = make_workspace(tmp_path)
    answer = {
        "lessons": [
            {"text": "new one", "kind": "lesson", "part": None},
            {"text": "new two", "kind": "lesson", "part": None},
            {"text": "new three", "kind": "lesson", "part": None},
        ],
        "remove": [],
    }
    merge_answer = {
        "merge": [{"ids": ["l001", "l002", "l003"], "text": "the three old lessons merged"}],
        "remove": [],
    }
    stub_learn_client(monkeypatch, answer, merge_answer=merge_answer)
    summary = notebook.learn(str(workspace), str(notebook_file), 4)
    assert summary["status"] == "written"
    assert summary["count"] == 4
    assert summary["waiting"] == 0
    assert summary["removed"] == ["l001", "l002", "l003"]
    document = read_json(notebook_file)
    texts = [lesson["text"] for lesson in document["lessons"]]
    assert "the three old lessons merged" in texts
    assert "new one" in texts and "new two" in texts and "new three" in texts
    assert document["waiting"] == []


def test_learn_keeps_new_lessons_waiting_when_still_over(tmp_path, monkeypatch):
    notebook_file = tmp_path / "notebook.json"
    write_notebook(
        notebook_file,
        [
            {"id": "l001", "text": "old one", "kind": "lesson", "created_at": "2026-01-01T00:00:00+00:00"},
            {"id": "l002", "text": "old two", "kind": "lesson", "created_at": "2026-01-02T00:00:00+00:00"},
            {"id": "l003", "text": "old three", "kind": "lesson", "created_at": "2026-01-03T00:00:00+00:00"},
        ],
    )
    workspace = make_workspace(tmp_path)
    answer = {
        "lessons": [
            {"text": "new one", "kind": "lesson", "part": None},
            {"text": "new two", "kind": "lesson", "part": None},
            {"text": "new three", "kind": "lesson", "part": None},
        ],
        "remove": [],
    }
    merge_answer = {"merge": [], "remove": []}
    stub_learn_client(monkeypatch, answer, merge_answer=merge_answer)
    summary = notebook.learn(str(workspace), str(notebook_file), 3)
    assert summary["status"] == "written"
    assert summary["count"] == 3
    assert summary["waiting"] == 3
    assert summary["removed"] == []
    document = read_json(notebook_file)
    assert [lesson["id"] for lesson in document["lessons"]] == ["l001", "l002", "l003"]
    assert [lesson["text"] for lesson in document["waiting"]] == ["new one", "new two", "new three"]
    assert validate(document, "notebook") == []


def test_learn_moves_the_newest_lessons_to_waiting(tmp_path, monkeypatch):
    notebook_file = tmp_path / "notebook.json"
    write_notebook(
        notebook_file,
        [
            {"id": "l001", "text": "old one", "kind": "lesson", "created_at": "2026-01-01T00:00:00+00:00"},
            {"id": "l002", "text": "old two", "kind": "lesson", "created_at": "2026-01-02T00:00:00+00:00"},
            {"id": "l003", "text": "old three", "kind": "lesson", "created_at": "2026-01-03T00:00:00+00:00"},
        ],
    )
    workspace = make_workspace(tmp_path)
    answer = {
        "lessons": [
            {"text": "new one", "kind": "lesson", "part": None},
            {"text": "new two", "kind": "lesson", "part": None},
            {"text": "new three", "kind": "lesson", "part": None},
        ],
        "remove": [],
    }
    merge_answer = {"merge": [{"ids": ["l004", "l005"], "text": "merged new"}], "remove": []}
    stub_learn_client(monkeypatch, answer, merge_answer=merge_answer)
    summary = notebook.learn(str(workspace), str(notebook_file), 3)
    assert summary["status"] == "written"
    assert summary["count"] == 3
    assert summary["waiting"] == 2
    document = read_json(notebook_file)
    assert [lesson["id"] for lesson in document["lessons"]] == ["l001", "l002", "l003"]
    assert [lesson["id"] for lesson in document["waiting"]] == ["l006", "l007"]
    assert document["waiting"][1]["text"] == "merged new"
    assert validate(document, "notebook") == []


def test_merge_uses_the_next_id_across_waiting(tmp_path):
    notebook_file = tmp_path / "notebook.json"
    write_notebook(
        notebook_file,
        [
            {"id": "l001", "text": "one", "kind": "lesson"},
            {"id": "l002", "text": "two", "kind": "lesson"},
            {"id": "l003", "text": "three", "kind": "lesson"},
        ],
        waiting=[
            {"id": "l004", "text": "waiting one", "kind": "lesson"},
            {"id": "l005", "text": "waiting two", "kind": "lesson"},
        ],
    )
    result = run_notebook(
        "--notebook",
        str(notebook_file),
        "--limit",
        "3",
        "--merge",
        "l001,l002",
        "--text",
        "merged",
        "--json",
    )
    assert result.returncode == 0
    summary = json.loads(result.stdout)
    assert summary["changed"]["added"] == ["l006"]
    document = read_json(notebook_file)
    ids = [lesson["id"] for lesson in document["lessons"] + document["waiting"]]
    assert len(ids) == len(set(ids))


def test_learn_writes_nothing_when_waiting_is_full(tmp_path, monkeypatch):
    notebook_file = tmp_path / "notebook.json"
    write_notebook(
        notebook_file,
        [{"id": "l001", "text": "one", "kind": "lesson"}],
        waiting=[
            {"id": "l002", "text": "waiting one", "kind": "lesson"},
            {"id": "l003", "text": "waiting two", "kind": "lesson"},
            {"id": "l004", "text": "waiting three", "kind": "lesson"},
        ],
    )
    workspace = make_workspace(tmp_path)
    before = notebook_file.read_text(encoding="utf-8")

    def boom(*args, **kwargs):
        raise AssertionError("the waiting-full pass must not call the model")

    monkeypatch.setattr(notebook, "make_client", boom)
    summary = notebook.learn(str(workspace), str(notebook_file), 40)
    assert summary["status"] == "waiting_full"
    assert summary["llm_cost_usd"] == 0.0
    assert summary["added"] == [] and summary["removed"] == []
    assert notebook_file.read_text(encoding="utf-8") == before
    assert not (workspace / ".learn.json").exists()


def test_learn_asks_for_only_the_remaining_room(tmp_path, monkeypatch):
    notebook_file = tmp_path / "notebook.json"
    write_notebook(
        notebook_file,
        [
            {"id": "l001", "text": "one", "kind": "lesson"},
            {"id": "l002", "text": "two", "kind": "lesson"},
        ],
        waiting=[
            {"id": "l003", "text": "waiting one", "kind": "lesson"},
            {"id": "l004", "text": "waiting two", "kind": "lesson"},
        ],
    )
    workspace = make_workspace(tmp_path)
    answer = {
        "lessons": [
            {"text": "new one", "kind": "lesson", "part": None},
            {"text": "new two", "kind": "lesson", "part": None},
            {"text": "new three", "kind": "lesson", "part": None},
        ],
        "remove": [],
    }
    sent = stub_learn_client(monkeypatch, answer, merge_answer={"merge": [], "remove": []})
    summary = notebook.learn(str(workspace), str(notebook_file), 2)
    assert summary["status"] == "written"
    assert summary["count"] == 2
    assert summary["waiting"] == 3
    assert "at most 1" in sent[0][0]["content"]
    document = read_json(notebook_file)
    assert [lesson["id"] for lesson in document["lessons"]] == ["l001", "l002"]
    assert [lesson["id"] for lesson in document["waiting"]] == ["l003", "l004", "l005"]


def test_learn_refuses_a_hand_edited_over_limit_file(tmp_path, monkeypatch):
    notebook_file = tmp_path / "notebook.json"
    write_notebook(
        notebook_file,
        [
            {"id": "l001", "text": "one", "kind": "lesson"},
            {"id": "l002", "text": "two", "kind": "lesson"},
            {"id": "l003", "text": "three", "kind": "lesson"},
            {"id": "l004", "text": "four", "kind": "lesson"},
            {"id": "l005", "text": "five", "kind": "lesson"},
        ],
    )
    workspace = make_workspace(tmp_path)
    before = notebook_file.read_text(encoding="utf-8")

    def boom(*args, **kwargs):
        raise AssertionError("an over-limit notebook must not call the model")

    monkeypatch.setattr(notebook, "make_client", boom)
    summary = notebook.learn(str(workspace), str(notebook_file), 3)
    assert summary["status"] == "waiting_full"
    assert summary["llm_cost_usd"] == 0.0
    assert summary["waiting"] <= 3
    assert summary["added"] == [] and summary["removed"] == []
    document = read_json(notebook_file)
    assert [lesson["id"] for lesson in document["lessons"]] == [
        "l001", "l002", "l003", "l004", "l005",
    ]
    assert document["waiting"] == []
    assert notebook_file.read_text(encoding="utf-8") == before
    assert not (workspace / ".learn.json").exists()
    assert validate(document, "notebook") == []

    # the owner removes lessons down to the limit; the pass runs again
    for lesson_id in ("l004", "l005"):
        result = run_notebook("--notebook", str(notebook_file), "--remove", lesson_id, "--json")
        assert result.returncode == 0
    answer = {"lessons": [{"text": "new one", "kind": "lesson", "part": None}], "remove": []}
    stub_learn_client(monkeypatch, answer, merge_answer={"merge": [], "remove": []})
    summary = notebook.learn(str(workspace), str(notebook_file), 3)
    assert summary["status"] == "written"
    assert summary["count"] == 3


def test_removing_a_lesson_writes_a_waiting_lesson(tmp_path):
    notebook_file = tmp_path / "notebook.json"
    write_notebook(
        notebook_file,
        [
            {"id": "l001", "text": "one", "kind": "lesson"},
            {"id": "l002", "text": "two", "kind": "lesson"},
            {"id": "l003", "text": "three", "kind": "lesson"},
        ],
        waiting=[{"id": "l004", "text": "waiting one", "kind": "lesson"}],
    )
    result = run_notebook(
        "--notebook", str(notebook_file), "--limit", "3", "--remove", "l001", "--json"
    )
    assert result.returncode == 0
    summary = json.loads(result.stdout)
    assert summary["count"] == 3
    document = read_json(notebook_file)
    assert [lesson["id"] for lesson in document["lessons"]] == ["l002", "l003", "l004"]
    assert document["waiting"] == []


def test_remove_can_discard_a_waiting_lesson(tmp_path):
    notebook_file = tmp_path / "notebook.json"
    write_notebook(
        notebook_file,
        [
            {"id": "l001", "text": "one", "kind": "lesson"},
            {"id": "l002", "text": "two", "kind": "lesson"},
            {"id": "l003", "text": "three", "kind": "lesson"},
        ],
        waiting=[{"id": "l004", "text": "waiting one", "kind": "lesson"}],
    )
    result = run_notebook("--notebook", str(notebook_file), "--remove", "l004", "--json")
    assert result.returncode == 0
    summary = json.loads(result.stdout)
    assert summary["count"] == 3
    document = read_json(notebook_file)
    assert [lesson["id"] for lesson in document["lessons"]] == ["l001", "l002", "l003"]
    assert document["waiting"] == []


def test_learn_without_workspace_is_usage(tmp_path):
    notebook_file = tmp_path / "notebook.json"
    result = run_notebook("--notebook", str(notebook_file), "--learn", "--json")
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["exit_code"] == 2


def test_learn_without_a_notebook_path_is_usage(tmp_path, monkeypatch):
    monkeypatch.delenv("YT_PIPELINE_NOTEBOOK", raising=False)
    workspace = make_workspace(tmp_path)
    result = run_notebook("--learn", "--workspace", str(workspace), "--json")
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["exit_code"] == 2


def test_remove_drops_one_and_unknown_id_is_usage(tmp_path):
    notebook = tmp_path / "notebook.json"
    run_notebook("--notebook", str(notebook), "--add", "one", "--add", "two")
    result = run_notebook("--notebook", str(notebook), "--remove", "l001", "--json")
    assert result.returncode == 0
    summary = json.loads(result.stdout)
    assert summary["count"] == 1
    assert summary["changed"]["removed"] == ["l001"]
    unknown = run_notebook("--notebook", str(notebook), "--remove", "l999", "--json")
    assert unknown.returncode == 2
    payload = json.loads(unknown.stdout)
    assert payload["exit_code"] == 2
    assert "l999" in payload["error"]


def test_propose_writes_a_proposal_and_unknown_part_is_usage(tmp_path):
    notebook = tmp_path / "notebook.json"
    result = run_notebook(
        "--notebook", str(notebook), "--propose", "members_sentence", "--text", "sponsor", "--json"
    )
    assert result.returncode == 0
    summary = json.loads(result.stdout)
    lesson = summary["lessons"][0]
    assert lesson["kind"] == "proposal"
    assert lesson["part"] == "members_sentence"
    bad = run_notebook("--notebook", str(notebook), "--propose", "bogus", "--text", "x", "--json")
    assert bad.returncode == 2
    payload = json.loads(bad.stdout)
    assert payload["exit_code"] == 2
    assert "members_sentence" in payload["error"]


def test_written_file_validates_against_the_schema(tmp_path):
    notebook = tmp_path / "notebook.json"
    run_notebook("--notebook", str(notebook), "--add", "one")
    run_notebook("--notebook", str(notebook), "--propose", "closing", "--text", "bye")
    document = read_json(notebook)
    assert validate(document, "notebook") == []


def test_json_failure_prints_one_error_object(tmp_path):
    notebook = tmp_path / "notebook.json"
    result = run_notebook("--notebook", str(notebook), "--remove", "l001", "--json")
    assert result.returncode == 2
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["ok"] is False
    assert payload["exit_code"] == 2
    assert isinstance(payload["error"], str) and payload["error"]


def test_notebook_block_and_screenwriter_user(tmp_path):
    notebook = tmp_path / "notebook.json"
    run_notebook("--notebook", str(notebook), "--add", "keep the tunnel longer")
    run_notebook("--notebook", str(notebook), "--propose", "members_sentence", "--text", "sponsor line")
    block = notebook_block(notebook)
    assert "keep the tunnel longer" in block
    assert "channel.fixed.members_sentence" in block
    user = llm_director.build_screenwriter_user(
        "brief", "vacation", {}, [], 6.0, notebook_block=block
    )
    assert "## Notebook" in user
    assert "keep the tunnel longer" in user


def make_root(tmp_path):
    root = tmp_path / "projects"
    project = root / "2026-10-14"
    project.mkdir(parents=True)
    (project / "recap_720p.mp4").write_bytes(b"x")
    return root


def start_server(root, notebook):
    server = serve.build_server(
        root,
        "127.0.0.1",
        0,
        None,
        str(root / "inbox"),
        notebook=notebook,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def request(server, method, path, headers=None, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    data = response.read()
    result = response.status, dict(response.getheaders()), data
    connection.close()
    return result


def test_serve_shows_the_notebook_and_deletes_a_lesson(tmp_path):
    root = make_root(tmp_path)
    notebook = tmp_path / "notebook.json"
    run_notebook("--notebook", str(notebook), "--add", "keep the tunnel longer")
    server = start_server(root, notebook)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=director")
        assert status == 200
        page = body.decode("utf-8")
        assert "keep the tunnel longer" in page
        assert 'action="/2026-10-14/notebook-delete"' in page
        assert 'name="id" value="l001"' in page
        status, headers, _ = request(
            server,
            "POST",
            "/2026-10-14/notebook-delete",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body="id=l001",
        )
        assert status == 303
        assert headers["Location"] == "/2026-10-14/?tab=director#notebook"
        assert read_json(notebook)["lessons"] == []
    finally:
        server.shutdown()
        server.server_close()
