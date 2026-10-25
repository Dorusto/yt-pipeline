import json
import subprocess

import serve
from test_serve import (
    make_root,
    multipart_body,
    multipart_headers,
    request,
    start_server,
    stub_recap,
)


def test_chat_panel_shows_the_reviewer_objections(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    edit = {
        "meta": {
            "director": {
                "review": {
                    "approved": False,
                    "objections": [
                        {"slot": "v7", "shot": 7, "text": "shot 7 breaks the story after the departure"}
                    ],
                }
            }
        }
    }
    (project / "edit.json").write_text(json.dumps(edit), encoding="utf-8")
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=director")
        assert status == 200
        page = body.decode()
        assert "shot 7 breaks the story after the departure" in page
        assert '<span class="chat-shot">7</span>' in page
    finally:
        server.shutdown()
        server.server_close()


def test_chat_panel_shows_the_conversation(tmp_path, monkeypatch):
    monkeypatch.setattr(serve, "EUR_PER_USD", None)
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    row = {
        "ts": "2026-10-24T10:00:00+00:00",
        "action": "reply",
        "text": "drop 0071/s2",
        "reply": "I read this as: drop 0071/s2.",
        "estimate": {"usd": 0.05, "s": 12.3},
        "tool": "scripts/llm_director.py",
    }
    (project / "feedback.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=director")
        assert status == 200
        page = body.decode()
        assert "drop 0071/s2" in page
        assert "I read this as: drop 0071/s2." in page
        assert "about $0,05 and 12 s" in page
    finally:
        server.shutdown()
        server.server_close()


def test_chat_approve_carries_the_reedit_options(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    (project / ".run.json").write_text(
        json.dumps(
            {"options": {"length": "90s", "order": "chronological", "music": "track.mp3"}}
        ),
        encoding="utf-8",
    )
    row = {
        "ts": "2026-10-24T10:00:00+00:00",
        "action": "reply",
        "text": "drop 0071/s2",
        "reply": "I read this as: drop 0071/s2.",
    }
    (project / "feedback.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=director")
        assert status == 200
        page = body.decode()
        form = page.split('class="chat-approve"', 1)[1].split("</form>", 1)[0]
        assert 'name="length" value="90s"' in form
        assert 'name="order" value="chronological"' in form
        assert 'name="music" value="track.mp3"' in form
    finally:
        server.shutdown()
        server.server_close()


def test_chat_panel_keeps_the_button_when_can_do_all(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    row = {
        "ts": "2026-10-24T10:00:00+00:00",
        "action": "reply",
        "text": "drop 0071/s2",
        "reply": "I read this as: drop 0071/s2.",
        "can_do": "all",
    }
    (project / "feedback.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=director")
        assert status == 200
        page = body.decode()
        assert 'class="chat-approve"' in page
        assert "Make the new version" in page
        assert "Answer the director first" not in page
    finally:
        server.shutdown()
        server.server_close()


def test_chat_panel_disables_the_button_when_can_do_part(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    row = {
        "ts": "2026-10-24T10:00:00+00:00",
        "action": "reply",
        "text": "drop 0071/s2 and add subtitles",
        "reply": "I can drop the shot but I cannot add subtitles.",
        "can_do": "part",
        "missing_tool": "add subtitles",
    }
    (project / "feedback.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=director")
        assert status == 200
        page = body.decode()
        assert "Answer the director first" in page
        assert 'class="chat-approve"' not in page
        assert "Make the new version" not in page
    finally:
        server.shutdown()
        server.server_close()


def test_chat_panel_disables_the_button_when_can_do_none(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    row = {
        "ts": "2026-10-24T10:00:00+00:00",
        "action": "reply",
        "text": "add subtitles",
        "reply": "I cannot add subtitles.",
        "can_do": "none",
        "missing_tool": "add subtitles",
    }
    (project / "feedback.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=director")
        assert status == 200
        page = body.decode()
        assert "Answer the director first" in page
        assert 'class="chat-approve"' not in page
    finally:
        server.shutdown()
        server.server_close()


def test_chat_message_runs_the_interpret_call_and_redirects(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    calls = stub_recap(monkeypatch, tmp_path)
    stdout = json.dumps(
        {
            "reply": "I will drop 0071/s2.",
            "estimate_usd": 0.05,
            "estimate_s": 12.3,
            "wall_time_s": 1.0,
            "llm_cost_usd": 0.0003,
        }
    )
    monkeypatch.setattr(
        serve.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout, ""),
    )
    server = start_server(root)
    try:
        status, headers, body = request(
            server,
            "POST",
            "/2026-10-14/feedback-reply",
            headers=multipart_headers(),
            body=multipart_body([("feedback", None, "drop 0071/s2")]),
        )
        assert status == 303, body
        assert headers["Location"] == "/2026-10-14/?tab=director#chat"
        assert calls == []
    finally:
        server.shutdown()
        server.server_close()


def test_chat_missing_tool_is_logged(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    stub_recap(monkeypatch, tmp_path)
    stdout = json.dumps(
        {
            "reply": "I cannot add subtitles; I can drop or trim shots. Noted.",
            "can_do": "none",
            "missing_tool": "add subtitles",
            "estimate_usd": None,
            "estimate_s": None,
            "wall_time_s": 1.0,
            "llm_cost_usd": 0.0003,
        }
    )
    monkeypatch.setattr(
        serve.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout, ""),
    )
    requests_path = tmp_path / "requests.jsonl"
    server = start_server(root, requests=requests_path)
    try:
        status, _, body = request(
            server,
            "POST",
            "/2026-10-14/feedback-reply",
            headers=multipart_headers(),
            body=multipart_body([("feedback", None, "add subtitles")]),
        )
        assert status == 303, body
        lines = [
            json.loads(line)
            for line in requests_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(lines) == 1
        assert lines[0]["type"] == "missing_tool"
        assert lines[0]["text"] == "add subtitles: add subtitles"
        assert lines[0]["contact"] == ""
        assert lines[0]["backed"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_chat_part_missing_tool_is_logged(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    stub_recap(monkeypatch, tmp_path)
    stdout = json.dumps(
        {
            "reply": "I can drop the shot but I cannot add subtitles.",
            "can_do": "part",
            "missing_tool": "add subtitles",
            "estimate_usd": None,
            "estimate_s": None,
            "wall_time_s": 1.0,
            "llm_cost_usd": 0.0003,
        }
    )
    monkeypatch.setattr(
        serve.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout, ""),
    )
    requests_path = tmp_path / "requests.jsonl"
    server = start_server(root, requests=requests_path)
    try:
        status, _, body = request(
            server,
            "POST",
            "/2026-10-14/feedback-reply",
            headers=multipart_headers(),
            body=multipart_body([("feedback", None, "drop 0071/s2 and add subtitles")]),
        )
        assert status == 303, body
        lines = [
            json.loads(line)
            for line in requests_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(lines) == 1
        assert lines[0]["type"] == "missing_tool"
        assert lines[0]["text"] == "add subtitles: drop 0071/s2 and add subtitles"
    finally:
        server.shutdown()
        server.server_close()


def test_chat_can_do_logs_nothing(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    stub_recap(monkeypatch, tmp_path)
    stdout = json.dumps(
        {
            "reply": "I will drop 0071/s2.",
            "can_do": "all",
            "missing_tool": None,
            "estimate_usd": 0.05,
            "estimate_s": 12.3,
            "wall_time_s": 1.0,
            "llm_cost_usd": 0.0003,
        }
    )
    monkeypatch.setattr(
        serve.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout, ""),
    )
    requests_path = tmp_path / "requests.jsonl"
    server = start_server(root, requests=requests_path)
    try:
        status, _, body = request(
            server,
            "POST",
            "/2026-10-14/feedback-reply",
            headers=multipart_headers(),
            body=multipart_body([("feedback", None, "drop 0071/s2")]),
        )
        assert status == 303, body
        assert not requests_path.exists() or not requests_path.read_text(encoding="utf-8").strip()
    finally:
        server.shutdown()
        server.server_close()


def test_chat_message_requires_text(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    stub_recap(monkeypatch, tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(
            server,
            "POST",
            "/2026-10-14/feedback-reply",
            headers=multipart_headers(),
            body=multipart_body([("feedback", None, "   ")]),
        )
        assert status == 400
        assert b"write what should change first" in body
    finally:
        server.shutdown()
        server.server_close()


def test_chat_approve_starts_the_run(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    inbox = tmp_path / "inbox"
    source = inbox / "kotor-day"
    source.mkdir(parents=True)
    (source / "brief.txt").write_text("about 60 seconds", encoding="utf-8")
    (root / "2026-10-14" / ".run.json").write_text(
        json.dumps({"pid": 1, "source": str(source)}), encoding="utf-8"
    )
    calls = stub_recap(monkeypatch, tmp_path)
    server = start_server(root, inbox=inbox)
    try:
        status, _, body = request(
            server,
            "POST",
            "/2026-10-14/re-edit",
            headers=multipart_headers(),
            body=multipart_body(
                [
                    ("feedback", None, "drop 0071/s2"),
                    ("approved", None, "1"),
                    ("submit", None, "reedit"),
                ]
            ),
        )
        assert status == 303, body
        command = calls[-1]
        assert "--feedback" in command
        assert "drop 0071/s2" in command
        lines = [
            json.loads(line)
            for line in (root / "2026-10-14" / "feedback.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        assert [line["action"] for line in lines] == ["approved"]
    finally:
        server.shutdown()
        server.server_close()


def test_reviewer_box_stays_gone(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=director")
        assert status == 200
        assert b"Reviewer:" not in body
    finally:
        server.shutdown()
        server.server_close()


def test_reedit_form_has_no_ask_button():
    page = serve.render_reedit("2026-10-14", [])
    assert "feedback-reply" not in page
    assert "Ask the director" not in page


def test_reedit_fields_are_all_read():
    """Every REEDIT_FIELDS name changes the re-edit command or the facts."""
    values = {
        "brief": "a brief",
        "length": "90s",
        "length_s": "90",
        "order": "chronological",
        "audio_clean": "no",
        "format": "9x16",
        "speed_ramp": "on",
        "music": "track.mp3",
        "ambient_under_music_db": "12",
        "fact_place": "Place",
        "fact_when": "When",
        "fact_who": "Who",
        "fact_occasion": "Occasion",
    }
    assert set(values) == set(serve.REEDIT_FIELDS)
    base_command = serve.build_recap_command("py", "src", "ws", "brief", {}, "/lib")
    base_facts = serve.facts_from_fields({})
    for name in serve.REEDIT_FIELDS:
        fields = {name: values[name]}
        brief = fields.get("brief") or "brief"
        command = serve.build_recap_command("py", "src", "ws", brief, fields, "/lib")
        facts = serve.facts_from_fields(fields)
        assert command != base_command or facts != base_facts, name
