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


def stub_tool(monkeypatch, stdout, returncode=0, stderr=""):
    """Replace the tool subprocess with a stub that records its argv."""
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    monkeypatch.setattr(serve.subprocess, "run", fake_run)
    return calls


def test_chat_message_runs_the_director_chat_and_redirects(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    recap_calls = stub_recap(monkeypatch, tmp_path)
    stdout = json.dumps(
        {
            "reply": "I will drop 0071/s2.",
            "decision_note": None,
            "can_do": "all",
            "missing_tool": None,
            "estimate_usd": 0.05,
            "estimate_s": 12.3,
            "wall_time_s": 1.0,
            "llm_cost_usd": 0.0003,
        }
    )
    calls = stub_tool(monkeypatch, stdout)
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
        assert headers["Location"] == "/2026-10-14/?tab=director#chat-latest"
        assert "--chat" in calls[-1]
        assert "--interpret-feedback" not in calls[-1]
        assert recap_calls == []
    finally:
        server.shutdown()
        server.server_close()


def test_chat_decision_note_reaches_the_next_run(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    stub_recap(monkeypatch, tmp_path)
    stdout = json.dumps(
        {
            "reply": "I read this as: drop 0071/s2.",
            "decision_note": "drop 0071/s2",
            "can_do": "all",
            "missing_tool": None,
            "estimate_usd": None,
            "estimate_s": None,
            "wall_time_s": 1.0,
            "llm_cost_usd": 0.0003,
        }
    )
    stub_tool(monkeypatch, stdout)
    server = start_server(root)
    try:
        status, _, body = request(
            server,
            "POST",
            "/2026-10-14/feedback-reply",
            headers=multipart_headers(),
            body=multipart_body([("feedback", None, "drop 0071/s2")]),
        )
        assert status == 303, body
        row = {
            "ts": "2026-10-24T10:00:00+00:00",
            "action": "reply",
            "text": "drop 0071/s2",
            "reply": "I read this as: drop 0071/s2.",
            "decision_note": "drop 0071/s2",
        }
        (project / "feedback.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        status, _, body = request(server, "GET", "/2026-10-14/?tab=director")
        assert status == 200
        page = body.decode()
        assert '<p class="chat-decision">Decision: drop 0071/s2</p>' in page
        form = page.split('class="chat-approve"', 1)[1].split("</form>", 1)[0]
        assert "Decision: drop 0071/s2" in form
        rows = [
            json.loads(line)
            for line in (project / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert rows[0]["decision_note"] == "drop 0071/s2"
    finally:
        server.shutdown()
        server.server_close()


def test_chat_model_failure_is_a_clear_message(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    stub_recap(monkeypatch, tmp_path)
    stub_tool(
        monkeypatch,
        "",
        returncode=3,
        stderr="[error] model produced no usable reply after one retry\n",
    )
    server = start_server(root)
    try:
        status, _, body = request(
            server,
            "POST",
            "/2026-10-14/feedback-reply",
            headers=multipart_headers(),
            body=multipart_body([("feedback", None, "drop 0071/s2")]),
        )
        assert status == 502
        assert b"model produced no usable reply after one retry" in body
    finally:
        server.shutdown()
        server.server_close()


def test_director_tab_shows_the_explanation(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    plan = {
        "schema_version": "1",
        "generated_at": "2026-10-25T21:00:00+00:00",
        "tool": "director",
        "brief": {"text": "a calm recap"},
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
    report = {
        "schema_version": "1",
        "generated_at": "2026-10-25T21:00:00+00:00",
        "tool": "director_editor",
        "shots": [],
        "total_length_s": 12.0,
        "target_length_s": 90.0,
        "could_not_do": ["could not do the jerky approach"],
        "similar_neighbours": [{"first": "v1", "second": "v2", "score": 0.93}],
        "summary": "3 slots",
        "wall_time_s": 0.1,
        "llm_cost_usd": 0.0,
    }
    rounds = {
        "schema_version": "0.1",
        "generated_at": "2026-10-25T21:00:00+00:00",
        "tool": "scripts/llm_director.py",
        "rounds": [
            {
                "round": 1,
                "plan": plan,
                "report": report,
                "review": {"approved": False, "summary": "the middle drags", "notes": ["cut the last shot shorter"]},
                "cost_usd": 0.001,
            }
        ],
        "total_cost_usd": 0.001,
    }
    (project / "director_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (project / "editor_report.json").write_text(json.dumps(report), encoding="utf-8")
    (project / "director_rounds.json").write_text(json.dumps(rounds), encoding="utf-8")
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=director")
        assert status == 200
        page = body.decode()
        assert "A calm morning on the water ends with the cave." in page
        assert "the order carries the story" in page
        assert "open on the wake" in page and "sets the calm" in page
        assert "could not do the jerky approach" in page
        assert "12.0 s of 90.0 s" in page
        assert "cut the last shot shorter" in page
    finally:
        server.shutdown()
        server.server_close()


def test_director_tab_without_files_shows_the_empty_text(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=director")
        assert status == 200
        assert b"No director data yet" in body
    finally:
        server.shutdown()
        server.server_close()


def test_debug_tab_renders(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    plan = {
        "schema_version": "1",
        "generated_at": "2026-10-25T21:00:00+00:00",
        "tool": "director",
        "brief": {"text": "a calm recap"},
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
                "technique": "cut_on_motion",
                "moment": {"at_s": 4.0},
                "target_s": 3.0,
                "why": "sets the calm",
            }
        ],
    }
    report = {
        "schema_version": "1",
        "generated_at": "2026-10-25T21:00:00+00:00",
        "tool": "director_editor",
        "shots": [],
        "total_length_s": 12.0,
        "target_length_s": 90.0,
        "could_not_do": [],
        "similar_neighbours": [],
        "summary": "3 slots",
        "wall_time_s": 0.1,
        "llm_cost_usd": 0.0,
    }
    rounds = {
        "schema_version": "0.1",
        "generated_at": "2026-10-25T21:00:00+00:00",
        "tool": "scripts/llm_director.py",
        "rounds": [],
        "total_cost_usd": 0.001,
    }
    edit = {
        "meta": {"llm_cost_usd": 0.006},
        "video": [{"id": "v1", "shot_key": "0056/s1", "in_s": 2.0, "out_s": 7.0}],
    }
    ledger = {
        "total_usd": 0.001,
        "entries": [
            {"ts": "2026-10-25T21:00:00+00:00", "role": "director", "model": "stub/director", "cost_usd": 0.001}
        ],
    }
    (project / "director_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (project / "editor_report.json").write_text(json.dumps(report), encoding="utf-8")
    (project / "director_rounds.json").write_text(json.dumps(rounds), encoding="utf-8")
    (project / "edit.json").write_text(json.dumps(edit), encoding="utf-8")
    (project / "llm_spend.json").write_text(json.dumps(ledger), encoding="utf-8")
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=debug")
        assert status == 200
        page = body.decode()
        assert "0056/s1" in page
        assert "2.0–7.0" in page
        assert "cut_on_motion" in page
        assert "A calm morning on the water ends with the cave." in page
        assert "could_not_do" in page
        assert "total_cost_usd" in page
        assert "stub/director" in page
        assert "Total: 0.006 USD" in page
    finally:
        server.shutdown()
        server.server_close()


def test_why_tab_is_gone(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/")
        assert status == 200
        page = body.decode()
        assert "?tab=why" not in page
        assert "Why?" not in page
        status, _, body = request(server, "GET", "/2026-10-14/?tab=why")
        assert status == 200
        assert b'<iframe src="explain.html"' not in body
        assert b'<video controls playsinline preload="metadata" poster="thumbs/poster.jpg" src="recap_720p.mp4">' in body
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
        "meta": "on",
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


def reply_row(index, can_do=None):
    row = {
        "ts": f"2026-10-24T10:0{index}:00+00:00",
        "action": "reply",
        "text": f"request {index}",
        "reply": f"answer {index}",
    }
    if can_do is not None:
        row["can_do"] = can_do
    return row


def write_replies(project, rows):
    (project / "feedback.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def test_chat_panel_shows_only_the_last_three_exchanges(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    write_replies(project, [reply_row(index) for index in range(5)])
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=director")
        assert status == 200
        page = body.decode()
        assert '<details class="chat-earlier"><summary>Earlier messages (2)</summary>' in page
        earlier = page.split('<details class="chat-earlier">', 1)[1].split("</details>", 1)[0]
        assert "request 0" in earlier and "request 1" in earlier
        assert "request 2" not in earlier
        assert "request 2" in page and "request 3" in page and "request 4" in page
    finally:
        server.shutdown()
        server.server_close()


def test_newest_exchange_carries_the_chat_latest_anchor(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    write_replies(project, [reply_row(index) for index in range(5)])
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=director")
        assert status == 200
        page = body.decode()
        assert page.count('id="chat-latest"') == 1
        anchored = page.split('id="chat-latest"', 1)[1]
        assert anchored.index("request 4") < anchored.index('class="chat-form"')
        assert "request 3" not in anchored.split('class="chat-form"', 1)[0]
        assert 'href="/2026-10-14/?tab=director#chat-latest"' in page
    finally:
        server.shutdown()
        server.server_close()


def test_chat_form_and_button_come_after_the_newest_exchange(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    write_replies(project, [reply_row(index, can_do="all") for index in range(5)])
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=director")
        assert status == 200
        page = body.decode()
        newest = page.index('<div class="chat-msg director"><p>answer 4</p></div>')
        assert page.index('class="chat-form"') > newest
        assert page.index('class="chat-approve"') > newest
    finally:
        server.shutdown()
        server.server_close()


def test_chat_has_no_scroll_box(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=director")
        assert status == 200
        page = body.decode()
        rule = page.split(".chat{", 1)[1].split("}", 1)[0]
        assert "max-height" not in rule
        assert "overflow" not in rule
    finally:
        server.shutdown()
        server.server_close()


def test_chat_panel_disables_the_button_when_can_do_false(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    write_replies(project, [reply_row(0, can_do=False)])
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=director")
        assert status == 200
        page = body.decode()
        assert "Answer the director first" in page
        assert "Make the new version" not in page
    finally:
        server.shutdown()
        server.server_close()


def test_chat_panel_keeps_the_button_when_can_do_true(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    write_replies(project, [reply_row(0, can_do=True)])
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=director")
        assert status == 200
        page = body.decode()
        assert "Make the new version" in page
        assert "Answer the director first" not in page
    finally:
        server.shutdown()
        server.server_close()


def test_normalise_can_do_reads_the_old_bool_and_the_enum():
    assert serve.normalise_can_do(False) == "none"
    assert serve.normalise_can_do(True) == "all"
    assert serve.normalise_can_do("part") == "part"
    assert serve.normalise_can_do("none") == "none"
    assert serve.normalise_can_do(None) == "all"
