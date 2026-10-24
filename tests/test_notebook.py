import http.client
import json
import subprocess
import sys
import threading
from pathlib import Path

import llm_director
import serve
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
        status, _, body = request(server, "GET", "/2026-10-14/")
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
        assert headers["Location"] == "/2026-10-14/#notebook"
        assert read_json(notebook)["lessons"] == []
    finally:
        server.shutdown()
        server.server_close()
