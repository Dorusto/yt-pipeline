import http.client
import json
import os
import sys
import threading
import time

import pytest

import serve


def make_root(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    project = root / "2026-10-14"
    project.mkdir()
    (project / "recap_720p.mp4").write_bytes(bytes(range(100)))
    (project / "shotlog.html").write_text("<html>contact sheet</html>", encoding="utf-8")
    (project / "explain.html").write_text("<html>why</html>", encoding="utf-8")
    (project / "edit.json").write_text("{}", encoding="utf-8")
    proxy_only = root / "raw"
    (proxy_only / "proxy").mkdir(parents=True)
    (proxy_only / "proxy" / "clip.mp4").write_bytes(b"proxy")
    (proxy_only / "proxy" / "x.mp4").write_bytes(b"proxy")
    (root / "notes.txt").write_text("ignore", encoding="utf-8")
    return root


def make_music_dir(tmp_path, names):
    music = tmp_path / "music"
    music.mkdir()
    for name in names:
        (music / name).write_bytes(b"audio")
    return music


def start_server(root, music_dir=None, inbox=None, max_upload_mb=4096, max_total_mb=20480):
    if music_dir is None:
        music_dir = str(root / "no-music-dir")
    if inbox is None:
        inbox = root / "inbox"
    inbox = str(inbox)
    server = serve.build_server(
        root,
        "127.0.0.1",
        0,
        music_dir,
        inbox,
        max_upload_mb * 1024 * 1024,
        max_total_mb * 1024 * 1024,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def request(server, method, path, headers=None, body=None):
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=5
    )
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    data = response.read()
    result = response.status, dict(response.getheaders()), data
    connection.close()
    return result


BOUNDARY = "testboundary123"


def multipart_body(parts):
    """Build a multipart/form-data body from ``(name, filename, data)`` rows."""
    chunks = []
    for name, filename, data in parts:
        chunks.append(f"--{BOUNDARY}\r\n".encode())
        if filename is None:
            chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            chunks.append(data.encode() if isinstance(data, str) else data)
        else:
            chunks.append(
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n".encode()
            )
            chunks.append(data if isinstance(data, bytes) else data.encode())
        chunks.append(b"\r\n")
    chunks.append(f"--{BOUNDARY}--\r\n".encode())
    return b"".join(chunks)


def multipart_headers():
    return {"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"}


def test_discover_projects_ignores_non_projects(tmp_path):
    root = make_root(tmp_path)
    assert serve.discover_projects(root) == ["2026-10-14"]


def test_projects_sorted_descending(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    for name, stamp in (("2026-10-12", 1000), ("kotor-demo", 3000), ("2026-10-13", 2000)):
        folder = root / name
        folder.mkdir()
        recap = folder / "recap_720p.mp4"
        recap.write_bytes(b"x")
        os.utime(recap, (stamp, stamp))
    assert serve.discover_projects(root) == ["kotor-demo", "2026-10-13", "2026-10-12"]


def test_run_json_alone_is_a_project(tmp_path):
    root = tmp_path / "projects"
    folder = root / "2026-10-15"
    folder.mkdir(parents=True)
    (folder / ".run.json").write_text('{"pid": 1, "started_at": "x", "label": "recap"}', encoding="utf-8")
    assert serve.discover_projects(root) == ["2026-10-15"]


def test_project_status_running_for_live_pid(tmp_path):
    folder = tmp_path / "2026-10-14"
    folder.mkdir()
    (folder / ".run.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    assert serve.project_status(folder) == "running"


def test_project_status_failed_for_dead_pid_without_recap(tmp_path):
    folder = tmp_path / "2026-10-14"
    folder.mkdir()
    (folder / ".run.json").write_text(json.dumps({"pid": 2**31 - 1}), encoding="utf-8")
    assert serve.project_status(folder) == "failed"


def test_project_status_done_for_dead_pid_with_recap(tmp_path):
    folder = tmp_path / "2026-10-14"
    folder.mkdir()
    (folder / ".run.json").write_text(json.dumps({"pid": 2**31 - 1}), encoding="utf-8")
    (folder / "recap_720p.mp4").write_bytes(b"x")
    assert serve.project_status(folder) == "done"


def test_project_status_ignores_malformed_run_json(tmp_path):
    folder = tmp_path / "2026-10-14"
    folder.mkdir()
    (folder / ".run.json").write_text("not json", encoding="utf-8")
    assert serve.project_status(folder) == "done"
    (folder / ".run.json").write_text('{"pid": "abc"}', encoding="utf-8")
    assert serve.project_status(folder) == "done"


def test_index_lists_project_with_done_badge(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, headers, body = request(server, "GET", "/")
        assert status == 200
        assert headers["Content-Type"].startswith("text/html")
        assert b"2026-10-14" in body
        assert b'href="/2026-10-14/"' in body
        assert b"badge-done" in body
        assert b">done<" in body
        assert b"raw" not in body
    finally:
        server.shutdown()
        server.server_close()


def test_index_shows_running_badge(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    (project / ".run.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/")
        assert status == 200
        assert b"badge-running" in body
        assert b">running<" in body
    finally:
        server.shutdown()
        server.server_close()


def test_index_shows_failed_badge(tmp_path):
    root = tmp_path / "projects"
    project = root / "2026-10-14"
    project.mkdir(parents=True)
    (project / ".run.json").write_text(json.dumps({"pid": 2**31 - 1}), encoding="utf-8")
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/")
        assert status == 200
        assert b"badge-failed" in body
        assert b">failed<" in body
    finally:
        server.shutdown()
        server.server_close()


def test_project_page_default_tab_is_recap(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/")
        assert status == 200
        assert b'<video controls preload="metadata" src="recap_720p.mp4">' in body
        assert b'href="/2026-10-14/?tab=recap"' in body
        assert b'href="/2026-10-14/?tab=why"' in body
        assert b'href="/2026-10-14/?tab=shots"' in body
        assert b"recap_final.mp4" not in body
        assert b"edit.json" not in body
    finally:
        server.shutdown()
        server.server_close()


def test_project_page_without_recap_has_no_player(tmp_path):
    root = tmp_path / "projects"
    folder = root / "2026-10-14"
    folder.mkdir(parents=True)
    (folder / "recap_final.mp4").write_bytes(b"final")
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/")
        assert status == 200
        assert b"recap_final.mp4" in body
        assert b"<video" not in body
    finally:
        server.shutdown()
        server.server_close()


def test_tab_why_embeds_explain(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=why")
        assert status == 200
        assert b'<iframe src="explain.html"' in body
    finally:
        server.shutdown()
        server.server_close()


def test_tab_shots_embeds_shotlog(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=shots")
        assert status == 200
        assert b'<iframe src="shotlog.html"' in body
    finally:
        server.shutdown()
        server.server_close()


def test_tab_without_file_shows_placeholder(tmp_path):
    root = tmp_path / "projects"
    folder = root / "2026-10-14"
    folder.mkdir(parents=True)
    (folder / "recap_720p.mp4").write_bytes(b"x")
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=why")
        assert status == 200
        assert b"explain.html" not in body
        assert b"scripts/explain.py" in body
        status, _, body = request(server, "GET", "/2026-10-14/?tab=shots")
        assert status == 200
        assert b"shotlog.html" not in body
    finally:
        server.shutdown()
        server.server_close()


def test_unknown_tab_falls_back_to_recap(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=bogus")
        assert status == 200
        assert b'<video controls preload="metadata" src="recap_720p.mp4">' in body
    finally:
        server.shutdown()
        server.server_close()


def test_get_artifacts(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    server = start_server(root)
    try:
        status, headers, body = request(server, "GET", "/2026-10-14/recap_720p.mp4")
        assert status == 200
        assert headers["Content-Type"] == "video/mp4"
        assert headers["Accept-Ranges"] == "bytes"
        assert body == bytes(range(100))
        status, headers, body = request(server, "GET", "/2026-10-14/shotlog.html")
        assert status == 200
        assert headers["Content-Type"] == "text/html; charset=utf-8"
        assert body == (project / "shotlog.html").read_bytes()
        status, headers, body = request(server, "GET", "/2026-10-14/explain.html")
        assert status == 200
        assert headers["Content-Type"] == "text/html; charset=utf-8"
        assert body == (project / "explain.html").read_bytes()
        status, _, _ = request(server, "GET", "/2026-10-14/recap_final.mp4")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_head_has_no_body(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, headers, body = request(server, "HEAD", "/2026-10-14/recap_720p.mp4")
        assert status == 200
        assert headers["Content-Length"] == "100"
        assert body == b""
    finally:
        server.shutdown()
        server.server_close()


def test_traversal_and_unknown_return_404(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        for path in (
            "/2026-10-14/../brief.txt",
            "/2026-10-14/edit.json",
            "/2026-10-14/.run.json",
            "/2026-10-14/proxy/clip.mp4",
            "/2026-10-14/proxy/x.mp4",
            "/2026-10-14/motion/",
            "/nope/",
            "/2026-10-14/missing.mp4",
            "/2026-10-14%2f..%2fbrief.txt",
        ):
            status, _, _ = request(server, "GET", path)
            assert status == 404, path
    finally:
        server.shutdown()
        server.server_close()


def test_backslash_path_returns_404(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, _ = request(server, "GET", "/2026-10-14%5Crecap_720p.mp4")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_post_outside_upload_returns_405(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, headers, _ = request(server, "POST", "/")
        assert status == 405
        assert headers["Allow"] == "GET, HEAD"
    finally:
        server.shutdown()
        server.server_close()


def test_upload_music_options_sorted_by_name(tmp_path):
    root = make_root(tmp_path)
    music = make_music_dir(tmp_path, ["b.mp3", "a.WAV", "skip.txt", "c.ogg"])
    server = start_server(root, str(music))
    try:
        status, _, body = request(server, "GET", "/upload")
        assert status == 200
        assert b'<option value="a.WAV">a.WAV</option>' in body
        assert b'<option value="b.mp3">b.mp3</option>' in body
        assert b'<option value="c.ogg">c.ogg</option>' in body
        assert b"skip.txt" not in body
        assert body.index(b"a.WAV") < body.index(b"b.mp3") < body.index(b"c.ogg")
        assert (
            b'<option value="auto" selected>Auto</option><option value="none">none</option>' in body
        )
    finally:
        server.shutdown()
        server.server_close()


def test_upload_missing_music_dir_has_no_tracks(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root, str(tmp_path / "gone"))
    try:
        status, _, body = request(server, "GET", "/upload")
        assert status == 200
        assert b'<option value="auto" selected>Auto</option>' in body
        assert b'<option value="none">none</option>' in body
        assert b".mp3" not in body
    finally:
        server.shutdown()
        server.server_close()


FAKE_PID = None


def stub_recap(monkeypatch, tmp_path):
    """Replace the spawned recap with a stub that records its argv."""
    calls = []

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def wait(self):
            return 0

    def fake_popen(command, **kwargs):
        calls.append(command)
        (tmp_path / "last_command.txt").write_text("\n".join(command), encoding="utf-8")
        return FakeProcess(4242)

    monkeypatch.setattr(serve.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(serve, "project_status", lambda directory: "done")
    return calls


def upload(server, parts):
    return request(server, "POST", "/upload", headers=multipart_headers(), body=multipart_body(parts))


def test_upload_stores_files_and_starts_the_recap(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    inbox = tmp_path / "inbox"
    calls = stub_recap(monkeypatch, tmp_path)
    server = start_server(root, inbox=inbox)
    try:
        status, headers, body = upload(
            server,
            [
                ("folder", None, "kotor-day"),
                ("brief", None, "about 60 seconds, chronological"),
                ("length", None, "60s"),
                ("order", None, "best"),
                ("audio_clean", None, "no"),
                ("music", None, "auto"),
                ("ambient_under_music_db", None, "8"),
                ("files", "clip1.mp4", b"video-bytes"),
                ("files", "photo1.jpg", b"photo-bytes"),
            ],
        )
        assert status == 303, body
        assert headers["Location"] == "/kotor-day/"
        assert (inbox / "kotor-day" / "clip1.mp4").read_bytes() == b"video-bytes"
        assert (inbox / "kotor-day" / "photo1.jpg").read_bytes() == b"photo-bytes"
        assert (inbox / "kotor-day" / "brief.txt").read_text(encoding="utf-8").strip() == (
            "about 60 seconds, chronological"
        )
        marker = json.loads((root / "kotor-day" / ".run.json").read_text(encoding="utf-8"))
        assert marker["pid"] == 4242
        assert marker["source"] == str(inbox / "kotor-day")
        assert len(calls) == 1
        command = calls[0]
        assert "--target-duration-s" in command and "60" in command
        assert "--order" in command and "score" in command
        assert "--no-audio-clean" in command
        assert "--music" in command and "auto" in command
        assert "--ambient-under-music-db" in command and "8" in command
        assert "--brief" in command
    finally:
        server.shutdown()
        server.server_close()


def test_upload_refuses_to_overwrite(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    inbox = tmp_path / "inbox"
    (inbox / "kotor-day").mkdir(parents=True)
    (inbox / "kotor-day" / "clip1.mp4").write_bytes(b"original")
    stub_recap(monkeypatch, tmp_path)
    server = start_server(root, inbox=inbox)
    try:
        status, _, body = upload(
            server,
            [
                ("folder", None, "kotor-day"),
                ("brief", None, "hello"),
                ("files", "clip1.mp4", b"new-bytes"),
            ],
        )
        assert status == 409
        assert b"already exists" in body
        assert (inbox / "kotor-day" / "clip1.mp4").read_bytes() == b"original"
    finally:
        server.shutdown()
        server.server_close()


def test_upload_rejects_bad_names_and_folders(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    inbox = tmp_path / "inbox"
    stub_recap(monkeypatch, tmp_path)
    server = start_server(root, inbox=inbox)
    try:
        status, _, body = upload(
            server,
            [("folder", None, "ok"), ("brief", None, "hello"), ("files", "run.exe", b"x")],
        )
        assert status == 400
        assert b"unsupported" in body
        status, _, body = upload(
            server,
            [("folder", None, "../escape"), ("brief", None, "hello"), ("files", "a.mp4", b"x")],
        )
        assert status == 400
        assert b"folder" in body
        status, _, body = upload(
            server,
            [("folder", None, "ok"), ("brief", None, "   "), ("files", "a.mp4", b"x")],
        )
        assert status == 400
        assert b"brief" in body
        status, _, body = upload(
            server,
            [("folder", None, "ok"), ("brief", None, "hello")],
        )
        assert status == 400
        assert b"no files" in body
    finally:
        server.shutdown()
        server.server_close()


def test_upload_rejects_a_body_over_the_cap(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    inbox = tmp_path / "inbox"
    stub_recap(monkeypatch, tmp_path)
    server = start_server(root, inbox=inbox, max_total_mb=0)
    try:
        status, _, body = upload(
            server,
            [("folder", None, "ok"), ("brief", None, "hello"), ("files", "a.mp4", b"x" * 100)],
        )
        assert status in (413, 400)
    finally:
        server.shutdown()
        server.server_close()


def test_reedit_reruns_with_feedback(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    inbox = tmp_path / "inbox"
    source = inbox / "kotor-day"
    source.mkdir(parents=True)
    (source / "brief.txt").write_text("about 60 seconds", encoding="utf-8")
    calls = stub_recap(monkeypatch, tmp_path)
    server = start_server(root, inbox=inbox)
    try:
        status, headers, body = request(
            server,
            "POST",
            "/2026-10-14/re-edit",
            headers=multipart_headers(),
            body=multipart_body([("feedback", None, "drop 0071/s2, tunnel longer")]),
        )
        assert status == 409
        assert b"source" in body
        (root / "2026-10-14" / ".run.json").write_text(
            json.dumps({"pid": 1, "source": str(source)}), encoding="utf-8"
        )
        status, headers, body = request(
            server,
            "POST",
            "/2026-10-14/re-edit",
            headers=multipart_headers(),
            body=multipart_body([("feedback", None, "drop 0071/s2, tunnel longer")]),
        )
        assert status == 303, body
        assert headers["Location"] == "/2026-10-14/"
        command = calls[-1]
        assert "--feedback" in command
        assert "drop 0071/s2, tunnel longer" in command
    finally:
        server.shutdown()
        server.server_close()


def test_endpoints_refuse_while_a_run_is_live(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    inbox = tmp_path / "inbox"
    monkeypatch.setattr(serve, "project_status", lambda directory: "running")
    server = start_server(root, inbox=inbox)
    try:
        status, _, _ = upload(
            server,
            [("folder", None, "kotor-day"), ("brief", None, "hello"), ("files", "a.mp4", b"x")],
        )
        assert status == 409
        status, _, body = request(
            server,
            "POST",
            "/2026-10-14/re-edit",
            headers=multipart_headers(),
            body=multipart_body([("feedback", None, "x")]),
        )
        assert status == 409
        assert b"already running" in body
    finally:
        server.shutdown()
        server.server_close()


def test_project_page_shows_the_reedit_form(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/")
        assert status == 200
        assert b"re-edit" in body
        assert b'name="feedback"' in body
        assert b'action="/2026-10-14/re-edit"' in body
    finally:
        server.shutdown()
        server.server_close()


def test_upload_page_form(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, headers, body = request(server, "GET", "/upload")
        assert status == 200
        assert headers["Content-Type"].startswith("text/html")
        for fragment in (
            b' enctype="multipart/form-data"',
            b' method="post"',
            b' action="/upload"',
            b'name="files"',
            b'name="folder"',
            b'name="brief"',
            b'name="length"',
            b'name="order"',
            b'name="audio_clean"',
            b'name="music"',
            b'name="ambient_under_music_db"',
            b">Auto<",
            b"inbox",
        ):
            assert fragment in body, fragment
    finally:
        server.shutdown()
        server.server_close()


def test_range_returns_206(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, headers, body = request(
            server, "GET", "/2026-10-14/recap_720p.mp4", {"Range": "bytes=0-9"}
        )
        assert status == 206
        assert headers["Content-Range"] == "bytes 0-9/100"
        assert headers["Content-Length"] == "10"
        assert headers["Accept-Ranges"] == "bytes"
        assert body == bytes(range(10))

        status, headers, body = request(
            server, "GET", "/2026-10-14/recap_720p.mp4", {"Range": "bytes=10-"}
        )
        assert status == 206
        assert headers["Content-Range"] == "bytes 10-99/100"
        assert body == bytes(range(10, 100))

        status, headers, body = request(
            server, "GET", "/2026-10-14/recap_720p.mp4", {"Range": "bytes=-10"}
        )
        assert status == 206
        assert headers["Content-Range"] == "bytes 90-99/100"
        assert body == bytes(range(90, 100))
    finally:
        server.shutdown()
        server.server_close()


def test_invalid_range_returns_416(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        for value in ("bytes=500-600", "bytes=abc", "bytes=9-2", "chunks=0-9"):
            status, _, _ = request(
                server, "GET", "/2026-10-14/recap_720p.mp4", {"Range": value}
            )
            assert status == 416, value
    finally:
        server.shutdown()
        server.server_close()


def test_wildcard_bind_refused(tmp_path, capsys):
    root = make_root(tmp_path)
    for value in ("0.0.0.0", "::", ""):
        assert serve.main(["--workspaces", str(root), "--bind", value, "--check"]) == 1
    err = capsys.readouterr().err
    assert "tailnet-only: refusing to bind the wildcard address" in err


def test_missing_root_fails(tmp_path, capsys):
    assert serve.main(["--workspaces", str(tmp_path / "nope"), "--check"]) == 1
    assert "[error]" in capsys.readouterr().err


def test_check_json_summary(tmp_path, capsys):
    root = make_root(tmp_path)
    music = make_music_dir(tmp_path, ["a.mp3", "b.wav"])
    assert (
        serve.main(
            [
                "--workspaces",
                str(root),
                "--bind",
                "127.0.0.1",
                "--port",
                "0",
                "--music-dir",
                str(music),
                "--check",
                "--json",
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["tool"] == "scripts/serve.py"
    assert summary["status"] == "check"
    assert summary["bind"] == "127.0.0.1"
    assert isinstance(summary["port"], int) and summary["port"] > 0
    assert summary["url"] == f"http://127.0.0.1:{summary['port']}/"
    assert summary["music_tracks"] == 2
    assert len(summary["projects"]) == 1
    project = summary["projects"][0]
    assert project["name"] == "2026-10-14"
    assert project["status"] == "done"
    assert project["preview"].endswith("/2026-10-14/recap_720p.mp4")
    assert project["shotlog"].endswith("/2026-10-14/shotlog.html")
    assert project["why"].endswith("/2026-10-14/explain.html")
    assert project["final"] is None
    assert isinstance(summary["wall_time_s"], (int, float))
    assert summary["llm_cost_usd"] == 0.0


def test_check_reports_requested_port(tmp_path, capsys):
    root = make_root(tmp_path)
    assert (
        serve.main(
            ["--workspaces", str(root), "--bind", "127.0.0.1", "--port", "8099", "--check", "--json"]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["port"] == 8099


def test_default_bind_is_not_wildcard():
    assert serve.detect_bind() not in serve.WILDCARD_BINDS


def test_help_mentions_music_dir(capsys):
    try:
        serve.build_parser().parse_args(["--help"])
    except SystemExit:
        pass
    assert "--music-dir" in capsys.readouterr().out


def test_check_human_output(tmp_path, capsys):
    root = make_root(tmp_path)
    assert (
        serve.main(["--workspaces", str(root), "--bind", "127.0.0.1", "--check"]) == 0
    )
    out = capsys.readouterr().out
    assert out.startswith("check: http://127.0.0.1:")
    assert "2026-10-14" in out
    assert "done" in out


def test_edit_summary_uses_the_ramped_on_screen_length(tmp_path):
    edit = {
        "video": [
            {"id": "v1", "clip": "a.mp4", "in_s": 0.0, "out_s": 5.0, "middle_speed": 1.2},
            {"id": "v2", "clip": "b.mp4", "in_s": 0.0, "out_s": 2.0},
        ]
    }
    (tmp_path / "edit.json").write_text(json.dumps(edit), encoding="utf-8")
    summary = serve.edit_summary(tmp_path)
    ramped = 0.25 * 5.0 + 0.5 * 5.0 / 1.2 + 0.25 * 5.0
    assert summary["blocks"][0]["end"] == pytest.approx(ramped)
    assert summary["blocks"][1]["start"] == pytest.approx(ramped)
    assert summary["total"] == pytest.approx(ramped + 2.0)


def test_vertical_recap_is_served_and_shown_on_the_recap_tab(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    (project / "recap_720p_9x16.mp4").write_bytes(bytes(range(50)))
    (project / "recap_final_9x16.mp4").write_bytes(bytes(range(20)))
    server = start_server(root)
    try:
        status, headers, body = request(server, "GET", "/2026-10-14/recap_720p_9x16.mp4")
        assert status == 200 and headers["Content-Type"] == "video/mp4"
        assert body == bytes(range(50))
        status, _, body = request(server, "GET", "/2026-10-14/recap_final_9x16.mp4")
        assert status == 200 and body == bytes(range(20))
        status, _, body = request(server, "GET", "/2026-10-14/")
        page = body.decode("utf-8")
        assert 'src="recap_720p_9x16.mp4"' in page and 'class="vertical"' in page
        assert 'src="recap_720p.mp4"' in page
        status, _, _ = request(server, "GET", "/2026-10-14/edit.json")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_vertical_only_project_shows_the_player_and_is_not_failed(tmp_path):
    root = tmp_path / "projects"
    project = root / "vert"
    project.mkdir(parents=True)
    (project / "recap_720p_9x16.mp4").write_bytes(b"x")
    (project / ".run.json").write_text(json.dumps({"pid": 2 ** 22 + 12345}), encoding="utf-8")
    assert serve.project_status(project) == "done"
    assert 'src="recap_720p_9x16.mp4"' in serve.render_recap_panel(project)
    assert serve.existing_artifacts(project) == ["recap_720p_9x16.mp4"]


def test_upload_and_reedit_forms_have_the_format_select(tmp_path):
    page = serve.render_upload([])
    assert '<select name="format">' in page
    assert "Auto (16:9)" in page and ">9:16<" in page and ">both<" in page
    assert '<select name="format">' in serve.render_reedit("2026-10-14", [])


@pytest.mark.parametrize(
    "value,expected", [("9x16", "9x16"), ("both", "both"), ("16x9", "16x9"), ("auto", None), (None, None), ("junk", None)]
)
def test_format_field_becomes_the_recap_argv(tmp_path, value, expected):
    fields = {} if value is None else {"format": value}
    command = serve.build_recap_command("python", tmp_path / "in", tmp_path / "ws", "brief", fields, tmp_path / "music")
    if expected is None:
        assert "--format" not in command
    else:
        assert command[command.index("--format") + 1] == expected


def test_check_summary_lists_the_vertical_preview(tmp_path):
    root = make_root(tmp_path)
    (root / "2026-10-14" / "recap_720p_9x16.mp4").write_bytes(b"x")
    summary = serve.build_summary(root, "127.0.0.1", 8080, ["2026-10-14"], "ok", 0.0)
    assert summary["projects"][0]["vertical"].endswith("/2026-10-14/recap_720p_9x16.mp4")


def test_upload_and_reedit_forms_have_the_speed_ramp_select():
    page = serve.render_upload([])
    assert '<select name="speed_ramp">' in page
    assert "Speed ramp" in page and "Auto (off)" in page and ">on<" in page and ">off<" in page
    assert '<select name="speed_ramp">' in serve.render_reedit("2026-10-14", [])


@pytest.mark.parametrize(
    "value,expected", [("on", True), ("off", False), ("auto", False), (None, False), ("junk", False)]
)
def test_speed_ramp_field_becomes_the_recap_argv(tmp_path, value, expected):
    fields = {} if value is None else {"speed_ramp": value}
    command = serve.build_recap_command("python", tmp_path / "in", tmp_path / "ws", "brief", fields, tmp_path / "music")
    assert ("--speed-ramp" in command) is expected


def write_version(project, number, text, stamp, feedback=None, video=True):
    directory = project / "versions" / f"v{number:03d}"
    directory.mkdir(parents=True)
    edit = {"video": [{"clip": "a.mp4", "in_s": 0.0, "out_s": 4.0}]}
    (directory / "edit.json").write_text(json.dumps(edit), encoding="utf-8")
    if video:
        (directory / "recap_720p.mp4").write_bytes(text.encode())
    meta = {"created_at": stamp, "feedback": feedback or [], "status": "done" if video else "failed"}
    if not video:
        meta["failed_at"] = "render"
    (directory / "version.json").write_text(json.dumps(meta), encoding="utf-8")


def test_project_page_lists_versions_and_compares_two(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    write_version(project, 1, "first", "2026-10-15T10:00:00")
    write_version(project, 2, "second", "2026-10-15T11:00:00", feedback=["tunnel longer"])
    server = start_server(root)
    try:
        _, _, body = request(server, "GET", "/2026-10-14/")
        page = body.decode()
        assert 'src="versions/v002/recap_720p.mp4"' in page
        assert "tunnel longer" in page
        assert "Compare with" in page
        _, _, body = request(server, "GET", "/2026-10-14/?tab=recap&v=2&with=1")
        page = body.decode()
        assert 'src="versions/v002/recap_720p.mp4"' in page
        assert 'src="versions/v001/recap_720p.mp4"' in page
        status, _, data = request(server, "GET", "/2026-10-14/versions/v001/recap_720p.mp4")
        assert status == 200 and data == b"first"
        status, _, _ = request(server, "GET", "/2026-10-14/versions/v001/edit.json")
        assert status == 404
        status, _, _ = request(server, "GET", "/2026-10-14/versions/..%2F/recap_720p.mp4")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_latest_version_without_video_falls_back_to_the_last_one_that_played(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    write_version(project, 1, "first", "2026-10-15T10:00:00")
    write_version(project, 2, "", "2026-10-15T11:00:00", video=False)
    page = serve.render_recap_panel(project, "2026-10-14", status="done",
                                    report={"done": 0, "total": 0, "last": None, "failed_at": None, "error": None, "outcome": None})
    assert 'src="versions/v001/recap_720p.mp4"' in page
    assert 'class="on failed"' not in page


def test_failed_run_is_reported_with_its_error(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    (project / ".run.json").write_text(json.dumps({"pid": 999999}), encoding="utf-8")
    (project / "run.log").write_text(
        "old run noise\n"
        f"{serve.RUN_MARKER} 2026-10-15 11:00:00 ===\n"
        "[ 9/11] llm_director    done    397.0s  written\n"
        "[10/11] render_vertical FAIL      0.2s  exit 1\n"
        "[error] render_vertical failed (exit 1)\n"
        "Traceback (most recent call last):\n"
        "common.photos.PhotoError: cannot read photo /tmp/x/proxy/a.jpg\n"
        '{"tool": "scripts/recap.py", "status": "failed"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(serve, "pid_alive", lambda pid: False)
    assert serve.project_status(project) == "failed"
    report = serve.run_report(project)
    assert report["failed_at"] == "render_vertical"
    assert report["error"].endswith("cannot read photo /tmp/x/proxy/a.jpg")
    page = serve.render_project("2026-10-14", project, "recap")
    assert "failed at render_vertical" in page
    assert "cannot read photo" in page


def test_running_project_shows_progress_and_refreshes(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    (project / ".run.json").write_text(json.dumps({"pid": 4242}), encoding="utf-8")
    (project / "run.log").write_text(
        f"{serve.RUN_MARKER} 2026-10-15 11:00:00 ===\n[ 8/11] router          done      1.0s  written\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(serve, "pid_alive", lambda pid: True)
    page = serve.render_project("2026-10-14", project, "recap")
    assert "8 of 11 steps done" in page
    assert 'http-equiv="refresh"' in page


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="zombie state is checked on Linux")
def test_a_finished_child_that_was_not_reaped_is_not_running():
    import subprocess

    import psutil

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    deadline = time.time() + 10
    while time.time() < deadline and psutil.Process(child.pid).status() != psutil.STATUS_ZOMBIE:
        time.sleep(0.05)
    assert not serve.pid_alive(child.pid)
    child.wait()
    assert not serve.pid_alive(child.pid)


def test_exact_seconds_win_over_the_length_menu(tmp_path):
    command = serve.build_recap_command("py", tmp_path, tmp_path, "brief", {"length": "60s", "length_s": "45"}, tmp_path)
    assert command[command.index("--target-duration-s") + 1] == "45"
    command = serve.build_recap_command("py", tmp_path, tmp_path, "brief", {"length": "all", "length_s": ""}, tmp_path)
    assert command[command.index("--target-duration-s") + 1] == "0"
    command = serve.build_recap_command("py", tmp_path, tmp_path, "brief", {"length": "auto", "length_s": "2"}, tmp_path)
    assert "--target-duration-s" not in command
    assert 'name="length_s"' in serve.render_upload([])


def test_strip_overlaps_crossfaded_shots_like_the_render(tmp_path):
    edit = {"video": [
        {"clip": "a.mp4", "in_s": 0.0, "out_s": 2.0},
        {"clip": "b.mp4", "in_s": 0.0, "out_s": 2.0, "transition": {"type": "crossfade", "duration_s": 0.3}},
        {"kind": "photo", "photo": "p.jpg", "duration_s": 2.0, "transition": {"type": "cut"}},
    ]}
    (tmp_path / "edit.json").write_text(json.dumps(edit), encoding="utf-8")
    summary = serve.edit_summary(tmp_path)
    assert [round(block["start"], 2) for block in summary["blocks"]] == [0.0, 1.7, 3.7]
    assert round(summary["total"], 2) == 5.7


def test_exact_seconds_live_inside_the_length_menu(tmp_path):
    page = serve.render_upload([])
    assert 'value="custom"' in page
    label = page[page.index("<label>Length"):]
    label = label[: label.index("</label>")]
    assert 'name="length_s"' in label
    command = serve.build_recap_command("py", tmp_path, tmp_path, "brief", {"length": "custom", "length_s": "75"}, tmp_path)
    assert command[command.index("--target-duration-s") + 1] == "75"
    command = serve.build_recap_command("py", tmp_path, tmp_path, "brief", {"length": "custom", "length_s": ""}, tmp_path)
    assert "--target-duration-s" not in command


def test_shots_tab_marks_what_is_in_the_edit(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    (project / "shotlog.html").write_text(
        "<html><body><main><article class=\"card\"><div class=\"range\">a.mp4 · 00:00.0–00:05.0 · 0001/s1</div>"
        "<div class=\"badges\"></div></article></main></body></html>",
        encoding="utf-8",
    )
    edit = {"video": [], "decisions": [
        {"id": "0001/s1", "status": "used", "slot": "v3", "reason": "hero"},
        {"id": "0001/s2", "status": "rejected", "reason": "did not fit the length </script>"},
    ]}
    (project / "edit.json").write_text(json.dumps(edit), encoding="utf-8")
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/shotlog.html")
        page = body.decode()
        assert status == 200
        assert "EDIT_DECISIONS" in page
        assert '"slot": "v3"' in page
        assert "</script>\"" not in page
        assert page.index("EDIT_DECISIONS") < page.index("</body>")
    finally:
        server.shutdown()
        server.server_close()
