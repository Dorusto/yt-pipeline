import http.client
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET

import pytest
import yaml

import serve
import common.config as config
from common.costs import connect, insert_entry
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    serve._credit_cache.update({"at": 0.0, "value": None, "failed_at": 0.0})
    serve._totals_cache.update({"at": 0.0, "url": None, "value": None, "failed_at": 0.0})


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


def start_server(root, music_dir=None, inbox=None, max_upload_mb=4096, max_total_mb=20480,
                 ledger=None, requests=None, donation_links=None,
                 features=None, consent_text=None,
                 storage_gb=None, credit_warn_usd=None, project_totals_url=None,
                 brand_logo=None):
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
        ledger,
        requests,
        donation_links,
        features,
        consent_text,
        storage_gb,
        credit_warn_usd,
        project_totals_url,
        brand_logo if brand_logo is not None else serve.DEFAULT_BRAND_LOGO,
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
        assert b'<video controls playsinline preload="metadata" poster="thumbs/poster.jpg" src="recap_720p.mp4">' in body
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
        assert b'<video controls playsinline preload="metadata" poster="thumbs/poster.jpg" src="recap_720p.mp4">' in body
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
        assert body.startswith((project / "shotlog.html").read_bytes())
        assert b"must be in" in body and b"wrong label" in body
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
        assert serve.main(["--workspaces", str(root), "--bind", value, "--check"]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "tailnet-only: refusing to bind the wildcard address" in err


def test_missing_root_fails(tmp_path, capsys):
    assert serve.main(["--workspaces", str(tmp_path / "nope"), "--check"]) == EXIT_USAGE
    assert "[error]" in capsys.readouterr().err


def test_check_json_summary(tmp_path, capsys):
    root = make_root(tmp_path)
    music = make_music_dir(tmp_path, ["a.mp3", "b.wav"])
    assert (
        serve.main(
            [
                "--workspaces",
                str(root),
                "--inbox",
                str(tmp_path / "inbox"),
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
            [
                "--workspaces",
                str(root),
                "--inbox",
                str(tmp_path / "inbox"),
                "--bind",
                "127.0.0.1",
                "--port",
                "8099",
                "--check",
                "--json",
            ]
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
        serve.main(
            ["--workspaces", str(root), "--inbox", str(tmp_path / "inbox"), "--bind", "127.0.0.1", "--check"]
        )
        == 0
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


def write_meta(directory, title="O zi pe munte", missing=None):
    (directory / "recap_meta.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "generated_at": "2026-10-15T10:00:00+00:00",
                "tool": "scripts/recap_meta.py",
                "title": title,
                "description": "Prima propoziție. A doua propoziție.",
                "hashtags": ["#munte", "#recap", "#vara", "#drone", "#drumetie"],
                "missing": missing or [],
                "model": "mock-model",
                "llm_cost_usd": 0.0,
            }
        ),
        encoding="utf-8",
    )


def test_recap_panel_shows_the_meta_box(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    write_meta(project, missing=["place"])
    page = serve.render_recap_panel(project)
    assert "O zi pe munte" in page
    assert "Prima propoziție. A doua propoziție." in page
    assert "#munte #recap #vara #drone #drumetie" in page
    assert page.count('class="copy"') == 3
    assert "Rewrite" in page
    assert "rewrite-meta" in page


def test_meta_box_marks_a_missing_fact(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    (project / "brief_facts.json").write_text(
        json.dumps({"when": "April 2026"}), encoding="utf-8"
    )
    write_meta(project, missing=["place"])
    page = serve.render_recap_panel(project)
    assert 'name="fact_place"' in page
    assert "Add this for a better title" in page
    assert page.index("Add this for a better title") > page.index('name="fact_place"')


def test_post_rewrite_meta_saves_facts_and_reruns_recap_meta(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    write_version(project, 1, "first", "2026-10-15T10:00:00")
    calls = []
    popen_calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command[0],
            0,
            json.dumps({"ok": True, "status": "written", "llm_cost_usd": 0.0123}),
            "",
        )

    monkeypatch.setattr(serve.subprocess, "run", fake_run)
    monkeypatch.setattr(serve.subprocess, "Popen", lambda *a, **k: popen_calls.append(a))
    server = start_server(root)
    try:
        status, headers, body = request(
            server,
            "POST",
            "/2026-10-14/rewrite-meta",
            headers=multipart_headers(),
            body=multipart_body([("fact_place", None, "Retezat"), ("version", None, "v001")]),
        )
        assert status == 303, body
        assert "meta_cost=0.0123" in headers["Location"]
        assert "v=1" in headers["Location"]
        assert calls and calls[0][1].endswith("recap_meta.py")
        assert popen_calls == []
        workspace_facts = json.loads((project / "brief_facts.json").read_text(encoding="utf-8"))
        version_facts = json.loads(
            (project / "versions" / "v001" / "brief_facts.json").read_text(encoding="utf-8")
        )
        assert workspace_facts["place"] == "Retezat"
        assert version_facts["place"] == "Retezat"
    finally:
        server.shutdown()
        server.server_close()


def test_recap_panel_without_meta_box(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    page = serve.render_recap_panel(project)
    assert 'class="copy"' not in page
    assert "Title, description and hashtags" not in page


def test_recap_panel_uses_the_shown_version_meta(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    write_version(project, 1, "first", "2026-10-15T10:00:00")
    write_version(project, 2, "second", "2026-10-15T11:00:00")
    write_meta(project / "versions" / "v001", title="Titlu vechi")
    write_meta(project / "versions" / "v002", title="Titlu nou")
    page = serve.render_recap_panel(project, wanted=1)
    assert "Titlu vechi" in page
    assert "Titlu nou" not in page


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


def test_run_report_reports_the_running_station(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    log = project / "run.log"
    log.write_text(
        f"{serve.RUN_MARKER} 2026-10-15 11:00:00 ===\n[ 3/11] quality         start\n",
        encoding="utf-8",
    )
    stamp = time.time() - 30
    os.utime(log, (stamp, stamp))
    report = serve.run_report(project)
    assert report["current"] == "quality"
    assert report["current_step"] == 3
    assert report["total"] == 11
    assert isinstance(report["elapsed"], float)
    assert 20 < report["elapsed"] < 90
    log.write_text(
        f"{serve.RUN_MARKER} 2026-10-15 11:00:00 ===\n"
        "[ 3/11] quality         start\n"
        "[ 3/11] quality         done      2.0s  written\n",
        encoding="utf-8",
    )
    report = serve.run_report(project)
    assert report["current"] is None
    assert report["current_step"] == 0
    assert report["elapsed"] is None
    assert report["done"] == 3
    assert report["last"] == "quality"


def test_run_notice_shows_the_current_station_and_progress(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    (project / ".run.json").write_text(json.dumps({"pid": 4242}), encoding="utf-8")
    (project / "run.log").write_text(
        f"{serve.RUN_MARKER} 2026-10-15 11:00:00 ===\n[ 3/11] quality         start\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(serve, "pid_alive", lambda pid: True)
    page = serve.render_project("2026-10-14", project, "recap")
    assert "Now: checking quality" in page
    assert "step 3 of 11</span>" in page
    assert '<progress max="11"' in page
    notice = serve.render_run_notice(
        "running",
        {
            "outcome": None,
            "done": 2,
            "total": 11,
            "last": "ingest",
            "failed_at": None,
            "error": None,
            "current": "quality",
            "current_step": 3,
            "elapsed": None,
        },
    )
    assert "step 3 of 11</span> Now: checking quality" in notice
    assert '<progress max="11" value="2">' in notice
    notice = serve.render_run_notice(
        "running",
        {
            "outcome": None,
            "done": 11,
            "total": 13,
            "last": "explain",
            "failed_at": None,
            "error": None,
            "current": "render_final",
            "current_step": 12,
            "elapsed": 5.0,
        },
    )
    assert "step 12 of 13</span> Now: render_final" in notice
    assert "5 s</span>" in notice


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


def test_shots_tab_marks_what_is_in_the_edit(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    (project / "shotlog.html").write_text(
        '<html><body><main>'
        '<details class="shots-section" data-verdict="used" id="used" data-keys="0001/s1" data-keep>'
        '<summary>Used in the edit (<span class="count">1</span>)</summary></details>'
        '</main></body></html>',
        encoding="utf-8",
    )
    edit = {"video": [], "decisions": [
        {"id": "0001/s1", "status": "used", "slot": "v3", "reason": "hero"},
        {"id": "0001/s2", "status": "rejected", "reason": "did not fit the length </script>"},
    ]}
    (project / "edit.json").write_text(json.dumps(edit), encoding="utf-8")
    regenerated = []
    monkeypatch.setattr(serve, "_regenerate_shotlog", lambda directory: regenerated.append(directory))
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/shotlog.html")
        page = body.decode()
        assert status == 200
        assert "must be in" in page and "wrong label" in page
        assert "EDIT_DECISIONS" not in page
        assert regenerated == []
    finally:
        server.shutdown()
        server.server_close()


def test_the_reedit_button_always_asks_for_a_new_edit(tmp_path):
    command = serve.build_recap_command("py", tmp_path, tmp_path, "brief", {"submit": "reedit"}, tmp_path)
    assert "--new-edit" in command
    command = serve.build_recap_command("py", tmp_path, tmp_path, "brief", {"submit": "start"}, tmp_path)
    assert "--new-edit" not in command


def test_served_html_themes_the_progress_bar(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/")
        page = body.decode()
        assert status == 200
        assert "::-webkit-progress-value" in page
        assert "::-moz-progress-bar" in page
        assert "var(--accent)" in page
    finally:
        server.shutdown()
        server.server_close()


def test_append_expectation_creates_the_yaml(tmp_path):
    path = tmp_path / "expectations.yaml"
    serve.append_expectation(path, "must_include", "0071/s2", "", now="2026-10-21T18:55:00Z")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document["must_include"] == [
        {"any_of": ["0071/s2"], "note": "must be in (phone review 2026-10-21T18:55:00Z)"}
    ]


def test_append_expectation_wrong_label_row(tmp_path):
    path = tmp_path / "expectations.yaml"
    serve.append_expectation(path, "wrong_label", "0071/s2", "", now="2026-10-21T18:55:00Z")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document["wrong_label"] == [
        {"shot": "0071/s2", "note": "wrong label (phone review 2026-10-21T18:55:00Z)"}
    ]


def test_append_expectation_is_idempotent(tmp_path):
    path = tmp_path / "expectations.yaml"
    serve.append_expectation(path, "must_include", "0071/s2", "", now="t1")
    serve.append_expectation(path, "must_include", "0071/s2", "", now="t2")
    serve.append_expectation(path, "wrong_label", "0071/s2", "", now="t1")
    serve.append_expectation(path, "wrong_label", "0071/s2", "", now="t2")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert len(document["must_include"]) == 1
    assert len(document["wrong_label"]) == 1


def test_append_expectation_preserves_length_s_and_other_keys(tmp_path):
    path = tmp_path / "expectations.yaml"
    path.write_text("length_s:\n  target: 60\n  tolerance: 3\ncustom: keep\n", encoding="utf-8")
    serve.append_expectation(path, "must_include", "0071/s2", "", now="t")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document["length_s"] == {"target": 60, "tolerance": 3}
    assert document["custom"] == "keep"
    assert document["must_include"][0]["any_of"] == ["0071/s2"]


def test_append_expectation_keeps_a_user_note(tmp_path):
    path = tmp_path / "expectations.yaml"
    serve.append_expectation(path, "must_include", "0071/s2", "the tunnel", now="t")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document["must_include"][0]["note"] == "the tunnel"


def test_validate_note_rejects_a_long_note():
    assert serve.validate_note("x" * 200) == "x" * 200
    assert serve.validate_note("a\nb") == "a b"
    with pytest.raises(serve.UploadError) as exc:
        serve.validate_note("x" * 500)
    assert exc.value.status == 400


def test_validate_shot_and_kind():
    assert serve.validate_shot("0071/s2") == "0071/s2"
    assert serve.validate_shot("p1") == "p1"
    for bad in ("", " ", "-bad", "a" * 65, "has space"):
        with pytest.raises(serve.UploadError):
            serve.validate_shot(bad)
    assert serve.validate_kind("must_include") == "must_include"
    assert serve.validate_kind("wrong_label") == "wrong_label"
    with pytest.raises(serve.UploadError):
        serve.validate_kind("bogus")


def test_expectations_written_by_the_buttons_pass_eval_edit(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "edit.json").write_text(
        json.dumps({"video": [{"shot_key": "0071/s2", "clip": "a.mp4", "in_s": 0.0, "out_s": 5.0}]}),
        encoding="utf-8",
    )
    path = workspace / "expectations.yaml"
    serve.append_expectation(path, "must_include", "0071/s2", "", now="2026-10-21T18:55:00Z")
    serve.append_expectation(path, "wrong_label", "0071/s2", "", now="2026-10-21T18:55:00Z")
    result = subprocess.run(
        [
            sys.executable,
            str(serve.REPO_ROOT / "scripts" / "eval_edit.py"),
            "--workspace",
            str(workspace),
            "--expect",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "must_include 0071/s2" in result.stdout
    assert "wrong_label" not in result.stdout


def form_headers():
    return {"Content-Type": "application/x-www-form-urlencoded"}


def post_expectations(server, project, fields):
    return request(
        server,
        "POST",
        f"/{project}/expectations",
        headers=form_headers(),
        body=urllib.parse.urlencode(fields),
    )


def test_post_expectations_writes_the_file(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, headers, body = post_expectations(
            server, "2026-10-14", {"shot": "0071/s2", "kind": "must_include"}
        )
        assert status == 200
        assert headers["Content-Type"] == "application/json"
        assert json.loads(body) == {
            "ok": True,
            "shot": "0071/s2",
            "kind": "must_include",
            "file": "expectations.yaml",
        }
        document = yaml.safe_load(
            (root / "2026-10-14" / "expectations.yaml").read_text(encoding="utf-8")
        )
        assert document["must_include"][0]["any_of"] == ["0071/s2"]
    finally:
        server.shutdown()
        server.server_close()


def test_post_expectations_rejects_bad_input(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = post_expectations(
            server, "2026-10-14", {"shot": "bad key", "kind": "must_include"}
        )
        assert status == 400 and b"shot" in body
        status, _, body = post_expectations(
            server, "2026-10-14", {"shot": "0071/s2", "kind": "bogus"}
        )
        assert status == 400 and b"kind" in body
        status, _, body = post_expectations(
            server,
            "2026-10-14",
            {"shot": "0071/s2", "kind": "must_include", "note": "x" * 500},
        )
        assert status == 400 and b"note" in body
    finally:
        server.shutdown()
        server.server_close()


def test_post_expectations_unknown_project_and_get(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, _ = post_expectations(
            server, "nope", {"shot": "0071/s2", "kind": "must_include"}
        )
        assert status == 404
        status, _, _ = request(server, "GET", "/2026-10-14/expectations")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_shots_tab_has_the_review_buttons(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    (project / "shotlog.html").write_text(
        "<html><body><main><article class=\"card\"><div class=\"range\">a.mp4 · 00:00.0–00:05.0 · 0001/s1</div>"
        "<div class=\"badges\"></div></article></main></body></html>",
        encoding="utf-8",
    )
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/shotlog.html")
        page = body.decode()
        assert status == 200
        assert "must be in" in page
        assert "wrong label" in page
        assert "/expectations" in page
    finally:
        server.shutdown()
        server.server_close()


def test_shots_tab_moves_used_cards_into_the_used_section(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    (project / "shotlog.html").write_text(
        "<html><body><main><article class=\"card\"><div class=\"range\">a.mp4 · 00:00.0–00:05.0 · 0001/s1</div>"
        "<div class=\"badges\"></div></article></main></body></html>",
        encoding="utf-8",
    )
    (project / "edit.json").write_text(
        json.dumps(
            {
                "video": [],
                "decisions": [{"id": "0001/s1", "status": "used", "slot": "v1", "reason": "hero"}],
            }
        ),
        encoding="utf-8",
    )
    regenerated = []

    def fake_regenerate(directory):
        regenerated.append(directory)
        (project / "shotlog.html").write_text(
            '<html><body><main>'
            '<details class="shots-section" data-verdict="used" id="used" data-keys="0001/s1" data-keep>'
            '<summary>Used in the edit (<span class="count">1</span>)</summary></details>'
            '</main></body></html>',
            encoding="utf-8",
        )

    monkeypatch.setattr(serve, "_regenerate_shotlog", fake_regenerate)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/shotlog.html")
        page = body.decode()
        assert status == 200
        assert "must be in" in page and "wrong label" in page
        assert "shots-section" in page
        assert regenerated == [project]
        assert "EDIT_DECISIONS" not in page
    finally:
        server.shutdown()
        server.server_close()


def test_shotlog_regeneration_updates_the_used_grouping(tmp_path):
    workspace = tmp_path / "ws"
    quality_dir = workspace / "quality"
    quality_dir.mkdir(parents=True)
    stem = "dji_20261012_000000_0081_0000000000000_video"
    (quality_dir / f"{stem}.json").write_text(
        json.dumps(
            {
                "file": f"{stem}.mp4",
                "duration_s": 10.0,
                "segments": [
                    {
                        "id": "s1",
                        "start_s": 0.0,
                        "end_s": 10.0,
                        "role": "hold",
                        "confidence": 0.7,
                        "motion_label": "static",
                        "motion_label_confidence": 0.8,
                        "uncertain": False,
                        "uncertain_reasons": [],
                        "defects": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    describe_dir = workspace / "describe"
    describe_dir.mkdir()
    (describe_dir / f"{stem}.json").write_text(
        json.dumps(
            {
                "file": f"{stem}.mp4",
                "duration_s": 10.0,
                "model": "mock/vision",
                "segments": [
                    {
                        "id": "s1",
                        "start_s": 0.0,
                        "end_s": 10.0,
                        "status": "described",
                        "role": "hold",
                        "motion_label": "static",
                        "uncertain": False,
                        "uncertain_reasons": [],
                        "description": "a wide shot of the valley",
                        "summary": "a wide shot",
                        "tags": [],
                        "subject": "valley",
                        "scenery": None,
                        "semantic_motion": None,
                        "interest": 60,
                        "hero": False,
                        "verdict": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (workspace / "similarity.json").write_text(json.dumps({"pairs": []}), encoding="utf-8")
    first = subprocess.run(
        [
            sys.executable,
            str(serve.REPO_ROOT / "scripts" / "index.py"),
            "--workspace",
            str(workspace),
        ],
        capture_output=True,
        text=True,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    shotlog = workspace / "shotlog.html"
    assert 'data-keys=""' in shotlog.read_text(encoding="utf-8")
    (workspace / "edit.json").write_text(
        json.dumps(
            {
                "video": [],
                "decisions": [{"id": "0081/s1", "status": "used", "slot": "v1", "reason": "hero"}],
            }
        ),
        encoding="utf-8",
    )
    page = serve.shotlog_with_edit(shotlog, workspace)
    assert 'data-keys="0081/s1"' in page
    assert "must be in" in page and "wrong label" in page


def test_project_page_groups_why_and_shots_under_details(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/")
        page = body.decode()
        assert status == 200
        assert '<span class="tab-group">' in page
        assert '<span class="tab-group-label">Details</span>' not in page
        start = page.index('<span class="tab-group">')
        group = page[start:page.index("</nav>", start)]
        assert 'href="/2026-10-14/?tab=why"' in group
        assert 'href="/2026-10-14/?tab=shots"' in group
    finally:
        server.shutdown()
        server.server_close()


def make_mode_b_root(tmp_path, media=None, duration=30.0):
    root = tmp_path / "projects"
    project = root / "mode-b"
    project.mkdir(parents=True, exist_ok=True)
    words = {
        "segments": [{
            "id": 0,
            "start": 0.0,
            "end": 20.5,
            "text": "unu doi trei patru cinci",
            "words": [
                {"word": "unu", "start": 0.0, "end": 0.5},
                {"word": "doi", "start": 0.6, "end": 1.0},
                {"word": "trei", "start": 12.4, "end": 12.9},
                {"word": "patru", "start": 13.0, "end": 13.5},
                {"word": "cinci", "start": 20.0, "end": 20.5},
            ],
        }]
    }
    (project / "words.json").write_text(json.dumps(words), encoding="utf-8")
    (project / "sessions.yaml").write_text(
        yaml.safe_dump({"sessions": [
            {"camera": "/footage/cam.mp4", "words": str(project / "words.json")}
        ]}),
        encoding="utf-8",
    )
    clip = media.name if media is not None else "cam.mp4"
    path = str(media) if media is not None else "/footage/cam.mp4"
    edit = {
        "schema_version": "0.1",
        "generated_at": "2026-10-21T00:00:00+00:00",
        "tool": "scripts/rough_cut.py",
        "brief": {"text": "mode b"},
        "sources": [{"file": clip, "path": path, "duration_s": duration, "media_type": "video"}],
        "video": [],
        "decisions": [
            {"id": "w001", "status": "used", "reason": "kept speech", "slot": "w001",
             "score": 1.0, "clip": clip, "window_in_s": 0.0, "window_out_s": 12.4},
            {"id": "c001", "status": "rejected", "reason": "pause 1.2 s", "slot": None,
             "score": None, "clip": clip, "window_in_s": 12.4, "window_out_s": 13.6},
            {"id": "c002", "status": "rejected", "reason": "filler 'deci'", "slot": None,
             "score": None, "clip": clip, "window_in_s": 14.0, "window_out_s": 14.5},
            {"id": "c003", "status": "used", "reason": "kept by review", "slot": "w002",
             "score": 1.0, "clip": clip, "window_in_s": 15.0, "window_out_s": 16.0},
        ],
        "meta": {"tool": "scripts/rough_cut.py", "wall_time_s": 0.1, "llm_cost_usd": 0.0},
    }
    (project / "edit.json").write_text(json.dumps(edit), encoding="utf-8")
    return root


def post_cut_review(server, project, fields):
    return request(
        server,
        "POST",
        f"/{project}/cut-review",
        headers=form_headers(),
        body=urllib.parse.urlencode(fields),
    )


def test_post_cut_review_writes_the_file(tmp_path):
    root = make_mode_b_root(tmp_path)
    server = start_server(root)
    try:
        status, headers, body = post_cut_review(server, "mode-b", {"id": "c001", "action": "keep"})
        assert status == 200
        assert headers["Content-Type"] == "application/json"
        assert json.loads(body) == {
            "ok": True, "id": "c001", "action": "keep", "file": "cut_review.yaml",
        }
        text = (root / "mode-b" / "cut_review.yaml").read_text(encoding="utf-8")
        assert text.startswith(
            "# Written by scripts/serve.py from phone cut review taps; machine-owned."
        )
        document = yaml.safe_load(text)
        row = document["review"][0]
        assert row["id"] == "c001"
        assert row["action"] == "keep"
        assert row["clip"] == "cam.mp4"
        assert row["window_in_s"] == 12.4
        assert row["window_out_s"] == 13.6
        assert row["note"] == ""
        assert row["reviewed_at"]
    finally:
        server.shutdown()
        server.server_close()


def test_post_cut_review_replaces_the_row(tmp_path):
    root = make_mode_b_root(tmp_path)
    server = start_server(root)
    try:
        post_cut_review(server, "mode-b", {"id": "c001", "action": "keep"})
        post_cut_review(server, "mode-b", {"id": "c001", "action": "cut"})
        post_cut_review(server, "mode-b", {"id": "c002", "action": "keep"})
        document = yaml.safe_load(
            (root / "mode-b" / "cut_review.yaml").read_text(encoding="utf-8")
        )
        assert len(document["review"]) == 2
        assert {row["id"]: row["action"] for row in document["review"]} == {
            "c001": "cut", "c002": "keep",
        }
    finally:
        server.shutdown()
        server.server_close()


def test_post_cut_review_rejects_bad_input(tmp_path):
    root = make_mode_b_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = post_cut_review(server, "mode-b", {"id": "c001", "action": "bogus"})
        assert status == 400 and b"action" in body
        status, _, body = post_cut_review(server, "mode-b", {"action": "keep"})
        assert status == 400 and b"id" in body
        status, _, _ = post_cut_review(server, "nope", {"id": "c001", "action": "keep"})
        assert status == 404
        status, _, _ = request(server, "GET", "/mode-b/cut-review")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_cuts_tab_lists_the_cuts(tmp_path):
    root = make_mode_b_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/mode-b/?tab=cuts")
        page = body.decode()
        assert status == 200
        assert page.count('class="cut-row"') == 3
        assert "trei patru" in page
        assert 'data-action="keep"' in page and 'data-action="cut"' in page
        assert "/mode-b/cut-audio?id=c001" in page
    finally:
        server.shutdown()
        server.server_close()


def test_mode_b_project_lists_the_cuts_tab(tmp_path):
    root = make_mode_b_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/mode-b/")
        assert status == 200
        assert b'href="/mode-b/?tab=cuts"' in body
    finally:
        server.shutdown()
        server.server_close()


def test_non_mode_b_project_has_no_cuts_tab(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/")
        assert status == 200
        assert b"?tab=cuts" not in body
        status, _, body = request(server, "GET", "/2026-10-14/?tab=cuts")
        assert status == 200
        assert b'<video controls playsinline preload="metadata" poster="thumbs/poster.jpg" src="recap_720p.mp4">' in body
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_cut_audio_serves_and_caches(tmp_path):
    root = tmp_path / "projects"
    project = root / "mode-b"
    project.mkdir(parents=True)
    media = project / "cam.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
         "-c:a", "pcm_s16le", str(media)],
        capture_output=True,
        check=True,
    )
    root = make_mode_b_root(tmp_path, media=media, duration=20.0)
    server = start_server(root)
    try:
        status, headers, body = request(server, "GET", "/mode-b/cut-audio?id=c001")
        assert status == 200
        assert headers["Content-Type"] == "audio/wav"
        assert body.startswith(b"RIFF")
        cache = root / "mode-b" / "cut_previews" / "c001.wav"
        assert cache.is_file()
        first = cache.read_bytes()
        status, headers, body = request(server, "GET", "/mode-b/cut-audio?id=c001")
        assert status == 200 and body == first
        status, _, _ = request(server, "GET", "/mode-b/cut-audio?id=nope")
        assert status == 404
        status, _, _ = request(server, "GET", "/mode-b/cut-audio")
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()


def test_cut_audio_missing_media_is_404(tmp_path):
    root = make_mode_b_root(tmp_path)
    server = start_server(root)
    try:
        status, _, _ = request(server, "GET", "/mode-b/cut-audio?id=c001")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def write_serve_config(tmp_path, shorts_layout=None):
    lines = [
        "models:",
        "  director: mock-model",
        "llm_budget_usd: null",
        "llm_backend: mock",
    ]
    if shorts_layout is not None:
        lines.append(f"shorts_layout: {shorts_layout}")
    path = tmp_path / "config.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


SHORTS_CANDIDATES = [
    {
        "rank": 1, "start_s": 0.0, "end_s": 30.0, "duration_s": 30.0, "score": 70.0,
        "hook": 60, "payoff": 80, "reason": "strong open",
        "title": "A strong opening", "description": "It teases the span. It spoils nothing.",
        "chapter_start_s": 0.0,
        "chapter_title": "Intro", "layout": "split",
    },
    {
        "rank": 2, "start_s": 40.0, "end_s": 65.0, "duration_s": 25.0, "score": 65.0,
        "hook": 55, "payoff": 75, "reason": "nice payoff",
        "title": "The payoff lands", "description": "The setup pays off. Watch to the end.",
    },
]


def write_shorts_candidates(project, candidates):
    document = {
        "schema_version": "0.2",
        "generated_at": "2026-10-21T00:00:00+00:00",
        "tool": "scripts/shorts_rank.py",
        "candidates": candidates,
    }
    (project / "shorts_candidates.json").write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8"
    )
    return document


def post_shorts_candidates(server, project, fields):
    return request(
        server,
        "POST",
        f"/{project}/shorts-candidates",
        headers=form_headers(),
        body=urllib.parse.urlencode(fields),
    )


def test_upload_and_reedit_forms_have_the_shorts_layout_select(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_CONFIG", str(write_serve_config(tmp_path)))
    page = serve.render_upload([])
    assert '<select name="shorts_layout">' in page
    assert "Shorts layout" in page
    assert '<option value="auto" selected>Auto</option>' in page
    assert '<option value="on">on</option>' in page
    assert '<option value="off">off</option>' in page
    assert '<select name="shorts_layout">' in serve.render_reedit("2026-10-14", [])


def test_shorts_layout_select_uses_the_config_value(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_CONFIG", str(write_serve_config(tmp_path, "off")))
    page = serve.render_upload([])
    select = page[page.index('<select name="shorts_layout">'):]
    select = select[: select.index("</select>")]
    assert '<option value="off" selected>off</option>' in select
    assert '<option value="auto" selected>Auto</option>' not in select


def test_shorts_layout_select_falls_back_to_auto_on_a_bad_config(tmp_path, monkeypatch):
    bad = tmp_path / "config.yaml"
    bad.write_text("not: a valid config\n", encoding="utf-8")
    monkeypatch.setenv("YT_CONFIG", str(bad))
    page = serve.render_upload([])
    assert '<option value="auto" selected>Auto</option>' in page


def test_shorts_tab_lists_the_candidates(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    write_shorts_candidates(project, SHORTS_CANDIDATES)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/")
        assert status == 200
        assert b'href="/2026-10-14/?tab=shorts"' in body
        status, _, body = request(server, "GET", "/2026-10-14/?tab=shorts")
        page = body.decode()
        assert status == 200
        assert page.count('class="short-row"') == 2
        assert "strong open" in page
        assert "nice payoff" in page
        assert "A strong opening" in page
        assert "It teases the span. It spoils nothing." in page
        assert "The payoff lands" in page
        assert "The setup pays off. Watch to the end." in page
        assert page.count('class="short-title"') == 2
        assert page.count('class="short-description"') == 2
        assert "Intro" in page
        assert '<option value="split" selected>split</option>' in page
        assert '<option value="face" selected>face</option>' in page
        assert "/shorts-candidates" in page
    finally:
        server.shutdown()
        server.server_close()


def test_project_without_shorts_candidates_has_no_shorts_tab(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/")
        assert status == 200
        assert b"?tab=shorts" not in body
        status, _, body = request(server, "GET", "/2026-10-14/?tab=shorts")
        assert status == 200
        assert b'<video controls playsinline preload="metadata" poster="thumbs/poster.jpg" src="recap_720p.mp4">' in body
    finally:
        server.shutdown()
        server.server_close()


def test_post_shorts_candidates_writes_the_layout(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    before = write_shorts_candidates(project, SHORTS_CANDIDATES)
    server = start_server(root)
    try:
        status, headers, body = post_shorts_candidates(
            server, "2026-10-14", {"rank": "1", "layout": "face"}
        )
        assert status == 200
        assert headers["Content-Type"] == "application/json"
        assert json.loads(body) == {"ok": True, "rank": 1, "layout": "face"}
        after = json.loads((project / "shorts_candidates.json").read_text(encoding="utf-8"))
        assert after["candidates"][0]["layout"] == "face"
        assert after["candidates"][1] == before["candidates"][1]
        assert after["schema_version"] == before["schema_version"]
        assert after["generated_at"] == before["generated_at"]
        assert after["tool"] == before["tool"]
        status, _, _ = post_shorts_candidates(
            server, "2026-10-14", {"rank": "1", "layout": "face"}
        )
        assert status == 200
        again = json.loads((project / "shorts_candidates.json").read_text(encoding="utf-8"))
        assert again == after
    finally:
        server.shutdown()
        server.server_close()


def test_post_shorts_candidates_rejects_bad_input(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    write_shorts_candidates(project, SHORTS_CANDIDATES)
    server = start_server(root)
    try:
        status, _, body = post_shorts_candidates(
            server, "2026-10-14", {"rank": "99", "layout": "face"}
        )
        assert status == 400 and b"rank" in body
        status, _, body = post_shorts_candidates(
            server, "2026-10-14", {"rank": "1", "layout": "weird"}
        )
        assert status == 400 and b"layout" in body
        status, _, _ = post_shorts_candidates(server, "nope", {"rank": "1", "layout": "face"})
        assert status == 404
        status, _, _ = request(server, "GET", "/2026-10-14/shorts-candidates")
        assert status == 404
        status, _, _ = request(
            server, "PUT", "/2026-10-14/shorts-candidates", headers=form_headers(), body=b""
        )
        assert status == 405
    finally:
        server.shutdown()
        server.server_close()


def test_post_shorts_candidates_missing_file_is_400(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = post_shorts_candidates(
            server, "2026-10-14", {"rank": "1", "layout": "face"}
        )
        assert status == 400
        assert b"shorts candidates" in body
    finally:
        server.shutdown()
        server.server_close()


def test_post_shorts_candidates_refuses_split_on_a_camera_only_project(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    write_shorts_candidates(project, SHORTS_CANDIDATES)
    edit = {
        "sources": [
            {
                "file": "camera 2026-09-29 20-58-37.mp4",
                "path": "/x/camera 2026-09-29 20-58-37.mp4",
                "media_type": "video",
            }
        ]
    }
    (project / "edit.json").write_text(json.dumps(edit), encoding="utf-8")
    server = start_server(root)
    try:
        status, _, body = post_shorts_candidates(
            server, "2026-10-14", {"rank": "1", "layout": "split"}
        )
        assert status == 400
        assert b"split" in body
        status, _, _ = post_shorts_candidates(
            server, "2026-10-14", {"rank": "1", "layout": "face"}
        )
        assert status == 200
    finally:
        server.shutdown()
        server.server_close()


def test_shorts_tab_offers_face_only_for_a_camera_only_project(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    write_shorts_candidates(project, SHORTS_CANDIDATES)
    edit = {
        "sources": [
            {
                "file": "camera 2026-09-29 20-58-37.mp4",
                "path": "/x/camera 2026-09-29 20-58-37.mp4",
                "media_type": "video",
            }
        ]
    }
    (project / "edit.json").write_text(json.dumps(edit), encoding="utf-8")
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=shorts")
        page = body.decode()
        assert status == 200
        assert '<option value="face"' in page
        assert '<option value="split"' not in page
    finally:
        server.shutdown()
        server.server_close()


def test_shorts_tab_offers_both_layouts_for_two_angles(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    write_shorts_candidates(project, SHORTS_CANDIDATES)
    edit = {
        "sources": [
            {
                "file": "camera 2026-09-29 20-58-37.mp4",
                "path": "/x/camera 2026-09-29 20-58-37.mp4",
                "media_type": "video",
            },
            {
                "file": "2026-09-29 20-58-35.mp4",
                "path": "/x/2026-09-29 20-58-35.mp4",
                "media_type": "video",
            },
        ]
    }
    (project / "edit.json").write_text(json.dumps(edit), encoding="utf-8")
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/?tab=shorts")
        page = body.decode()
        assert status == 200
        assert '<option value="face"' in page
        assert '<option value="split"' in page
    finally:
        server.shutdown()
        server.server_close()





def isolate_music_env(tmp_path, monkeypatch, library=None):
    text = (
        "models:\n  director: deepseek/deepseek-v4.1-flash\n"
        "llm_budget_usd: 5.0\nllm_backend: mock\n"
    )
    if library is not None:
        text += f"music:\n  library: {library}\n"
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.delenv("YT_PIPELINE_MUSIC_LIBRARY", raising=False)
    monkeypatch.setenv("YT_CONFIG", str(path))


def test_check_without_music_library_reports_no_tracks(tmp_path, monkeypatch, capsys):
    isolate_music_env(tmp_path, monkeypatch)
    root = make_root(tmp_path)
    assert serve.main(["--workspaces", str(root), "--inbox", str(tmp_path / "inbox"), "--bind", "127.0.0.1", "--check", "--json"]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["music_tracks"] == 0
    assert summary["music_library"] is None
    assert "no music library configured" in captured.err
    for way in ("--music-dir", "YT_PIPELINE_MUSIC_LIBRARY", "music.library"):
        assert way in captured.err


def test_check_reads_the_music_library_from_env_and_config(tmp_path, monkeypatch, capsys):
    music = make_music_dir(tmp_path, ["a.mp3", "b.wav"])
    isolate_music_env(tmp_path, monkeypatch)
    monkeypatch.setenv("YT_PIPELINE_MUSIC_LIBRARY", str(music))
    root = make_root(tmp_path)
    assert serve.main(["--workspaces", str(root), "--inbox", str(tmp_path / "inbox"), "--bind", "127.0.0.1", "--check", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["music_tracks"] == 2
    assert summary["music_library"] == str(music)
    isolate_music_env(tmp_path, monkeypatch, library=str(music))
    monkeypatch.delenv("YT_PIPELINE_MUSIC_LIBRARY", raising=False)
    assert serve.main(["--workspaces", str(root), "--inbox", str(tmp_path / "inbox"), "--bind", "127.0.0.1", "--check", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["music_library"] == str(music)


def test_build_server_defaults_to_no_music_library(tmp_path):
    root = make_root(tmp_path)
    server = serve.build_server(root, "127.0.0.1", 0, None, str(tmp_path / "inbox"), 1024, 1024)
    try:
        assert server.music_dir is None
        assert serve.list_music_tracks(None) == []
    finally:
        server.server_close()


def test_build_recap_command_passes_the_library_to_auto_music(tmp_path):
    music_dir = tmp_path / "music"
    command = serve.build_recap_command(
        "py", tmp_path / "in", tmp_path / "ws", "brief", {"music": "auto"}, music_dir
    )
    assert "--music" in command and command[command.index("--music") + 1] == "auto"
    assert "--music-dir" in command
    assert command[command.index("--music-dir") + 1] == str(music_dir.resolve())


def test_build_recap_command_without_library_omits_music_dir(tmp_path):
    command = serve.build_recap_command(
        "py", tmp_path / "in", tmp_path / "ws", "brief", {"music": "auto"}, None
    )
    assert "--music" in command
    assert "--music-dir" not in command


def test_missing_inbox_names_the_three_ways(tmp_path, monkeypatch, capsys):
    isolate_music_env(tmp_path, monkeypatch)
    monkeypatch.delenv("YT_PIPELINE_INBOX", raising=False)
    root = make_root(tmp_path)
    assert serve.main(["--workspaces", str(root), "--bind", "127.0.0.1"]) == EXIT_USAGE
    error = capsys.readouterr().err
    assert "no inbox configured" in error
    for way in ("--inbox", "YT_PIPELINE_INBOX", "inbox.path"):
        assert way in error


def test_check_without_inbox_warns_and_validates(tmp_path, monkeypatch, capsys):
    isolate_music_env(tmp_path, monkeypatch)
    monkeypatch.delenv("YT_PIPELINE_INBOX", raising=False)
    root = make_root(tmp_path)
    assert serve.main(["--workspaces", str(root), "--bind", "127.0.0.1", "--check", "--json"]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["status"] == "check"
    assert summary["bind"] == "127.0.0.1"
    assert summary["inbox"] is None
    assert len(summary["projects"]) == 1
    assert "no inbox configured" in captured.err
    for way in ("--inbox", "YT_PIPELINE_INBOX", "inbox.path"):
        assert way in captured.err


def test_inbox_from_flag_env_and_config(tmp_path, monkeypatch, capsys):
    root = make_root(tmp_path)
    inbox = tmp_path / "drop"
    isolate_music_env(tmp_path, monkeypatch)
    monkeypatch.setenv("YT_PIPELINE_INBOX", str(inbox))
    assert (
        serve.main(["--workspaces", str(root), "--bind", "127.0.0.1", "--check", "--json"]) == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["inbox"] == str(inbox.resolve())
    monkeypatch.delenv("YT_PIPELINE_INBOX", raising=False)
    assert (
        serve.main(
            ["--workspaces", str(root), "--inbox", str(inbox), "--bind", "127.0.0.1", "--check", "--json"]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["inbox"] == str(inbox.resolve())
    config = tmp_path / "config.yaml"
    config.write_text(
        "models:\n  director: deepseek/deepseek-v4.1-flash\n"
        "llm_budget_usd: 5.0\nllm_backend: mock\n"
        f"inbox:\n  path: {inbox}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("YT_CONFIG", str(config))
    assert (
        serve.main(["--workspaces", str(root), "--bind", "127.0.0.1", "--check", "--json"]) == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["inbox"] == str(inbox.resolve())


def test_no_personal_default_inbox_path():
    assert not hasattr(serve, "DEFAULT_INBOX")
    help_text = serve.build_parser().format_help()
    assert "~/media/inbox" not in help_text
    assert "YT_PIPELINE_INBOX" in help_text


def test_upload_page_has_a_drop_zone_that_adds_files():
    page = serve.render_upload([])
    assert "data-drop" in page
    assert 'name="files" multiple' in page
    assert "DataTransfer" in page


def test_upload_page_shows_upload_progress():
    page = serve.render_upload([])
    assert 'class="upload-progress"' in page
    assert "upload.onprogress" in page


def test_run_notice_shows_step_times_and_a_live_total(tmp_path):
    project = tmp_path / "trip"
    project.mkdir()
    (project / "run.log").write_text(
        f"{serve.RUN_MARKER} 2026-10-15 11:00:00 ===\n"
        "[ 1/12] proxy           start\n"
        "[ 1/12] proxy           done     321.9s  written\n"
        "[ 2/12] ingest          start\n",
        encoding="utf-8",
    )
    report = serve.run_report(project)
    assert report["steps"] == [{"step": 1, "name": "proxy", "seconds": 321.9}]
    assert report["started_at"] is not None
    notice = serve.render_run_notice("running", report)
    assert "5 min 22 s" in notice
    assert "Total so far" in notice
    assert "data-since" in notice
    assert "previous version" not in notice
    assert "previous version" in serve.render_run_notice("running", report, has_previous=True)


def test_run_cost_and_version_cost(tmp_path):
    import json as _json

    project = tmp_path / "trip"
    (project / "versions" / "v001").mkdir(parents=True)
    (project / "versions" / "v002").mkdir(parents=True)
    (project / "llm_spend.json").write_text(_json.dumps({"entries": [
        {"ts": "2026-10-15T10:00:00+00:00", "cost_usd": 0.04},
        {"ts": "2026-10-15T12:00:00+00:00", "cost_usd": 0.02},
    ]}), encoding="utf-8")
    (project / "versions" / "v001" / "version.json").write_text(
        _json.dumps({"created_at": "2026-10-15T11:00:00+0000"}), encoding="utf-8")
    (project / "versions" / "v002" / "version.json").write_text(
        _json.dumps({"created_at": "2026-10-15T13:00:00+0000"}), encoding="utf-8")
    first = {"path": project / "versions" / "v001", "meta": {"created_at": "2026-10-15T11:00:00+0000"}}
    second = {"path": project / "versions" / "v002", "meta": {"created_at": "2026-10-15T13:00:00+0000"}}
    assert round(serve.version_cost(first), 3) == 0.04
    assert round(serve.version_cost(second), 3) == 0.02
    assert serve.format_cost(0.0473) == "$0,05"


def test_drop_form_names_the_shown_version():
    form = serve.render_drop_form("trip", {"name": "v002"})
    assert 'action="/trip/drop-shot"' in form
    assert 'value="v002"' in form
    assert 'name="shots" value=""' in form
    assert "Tap shots" not in form and "<button" not in form


def test_trim_form_names_the_shown_version():
    form = serve.render_trim_form("trip", {"name": "v002"})
    assert 'action="/trip/trim-shot"' in form
    assert 'value="v002"' in form
    assert 'name="shot" value=""' in form and 'name="delta" value=""' in form
    assert "<button" not in form


def test_strip_script_handles_the_trim_form():
    assert "form.trim-shot" in serve.PICK_SCRIPT
    assert "trim.submit()" in serve.PICK_SCRIPT
    assert "shot-edit" in serve.PICK_SCRIPT


def make_ledger(path):
    connection = connect(path)
    insert_entry(connection, "2026-10-01", "fixed_monthly", 3.0, "", None)
    insert_entry(connection, "2026-10-01", "model_job", 1.5, "", "proj")
    insert_entry(connection, "2026-10-01", "dev_spend", 2.0, "", "2026-10-01")
    insert_entry(connection, "2026-10-01", "donation", 10.0, "", None)
    connection.close()


def test_costs_page_with_a_ledger(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    (project / "llm_spend.json").write_text(
        json.dumps({"entries": [{"cost_usd": 0.05}]}), encoding="utf-8"
    )
    (project / "versions" / "v001").mkdir(parents=True)
    (project / "versions" / "v002").mkdir(parents=True)
    ledger = tmp_path / "costs.db"
    make_ledger(ledger)
    server = start_server(root, ledger=ledger)
    try:
        status, _, body = request(server, "GET", "/costs")
        page = body.decode()
        assert status == 200
        assert "2026-10-14" in page
        assert "$0,05" in page
        assert "2 versions" in page
        assert "Monthly running" in page and "$3,00" in page
        assert "Development total" in page and "$2,00" in page
        assert "Donations" in page and "$10,00" in page
        assert "Balance" in page
        assert "Feedback and requests" in page
    finally:
        server.shutdown()
        server.server_close()


def test_costs_page_without_a_ledger(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/costs")
        page = body.decode()
        assert status == 200
        assert "2026-10-14" in page
        assert "The ledger is not set up." in page
    finally:
        server.shutdown()
        server.server_close()


def test_costs_page_shows_every_donation_link(tmp_path):
    root = make_root(tmp_path)
    links = [
        {"label": "Ko-fi", "url": "https://example.org/k"},
        {"label": "GitHub", "url": "https://example.org/g"},
    ]
    server = start_server(root, donation_links=links)
    try:
        status, _, body = request(server, "GET", "/costs")
        page = body.decode()
        assert status == 200
        assert 'href="https://example.org/k"' in page and "Ko-fi" in page
        assert 'href="https://example.org/g"' in page and "GitHub" in page
    finally:
        server.shutdown()
        server.server_close()


def test_costs_request_form_writes_one_line(tmp_path):
    root = make_root(tmp_path)
    requests = tmp_path / "requests.jsonl"
    server = start_server(root, requests=requests)
    try:
        status, headers, _ = request(
            server,
            "POST",
            "/costs/request",
            headers=form_headers(),
            body=urllib.parse.urlencode(
                {"type": "Feedback", "text": "please add chapters", "contact": "me@example.com", "backed": "on"}
            ),
        )
        assert status == 303
        assert headers["Location"] == "/costs?thanks=1"
        lines = requests.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["type"] == "Feedback"
        assert row["text"] == "please add chapters"
        assert row["contact"] == "me@example.com"
        assert row["backed"] is True
        assert len(row["date"]) == 10
    finally:
        server.shutdown()
        server.server_close()


def test_costs_request_stores_the_type(tmp_path):
    root = make_root(tmp_path)
    requests = tmp_path / "requests.jsonl"
    server = start_server(root, requests=requests)
    try:
        status, _, _ = request(
            server,
            "POST",
            "/costs/request",
            headers=form_headers(),
            body=urllib.parse.urlencode({"type": "Feature request", "text": "a new thing"}),
        )
        assert status == 303
        row = json.loads(requests.read_text(encoding="utf-8").splitlines()[0])
        assert row["type"] == "Feature request"
        assert row["backed"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_costs_page_hides_the_form_without_requests(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/costs")
        page = body.decode()
        assert status == 200
        assert 'action="/costs/request"' not in page
        assert "Requests are not set up." in page
    finally:
        server.shutdown()
        server.server_close()


def test_top_bar_has_the_costs_link(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/")
        assert status == 200
        assert b'href="/costs"' in body
    finally:
        server.shutdown()
        server.server_close()


def test_check_reports_the_ledger_and_requests(tmp_path, capsys):
    root = make_root(tmp_path)
    ledger = tmp_path / "costs.db"
    requests = tmp_path / "requests.jsonl"
    assert (
        serve.main(
            [
                "--workspaces",
                str(root),
                "--inbox",
                str(tmp_path / "inbox"),
                "--bind",
                "127.0.0.1",
                "--port",
                "0",
                "--ledger",
                str(ledger),
                "--requests",
                str(requests),
                "--check",
                "--json",
            ]
        )
        == EXIT_OK
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["ledger"] == str(ledger)
    assert summary["requests"] == str(requests)


def test_check_warns_without_ledger_and_requests(tmp_path, capsys, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.delenv("YT_PIPELINE_COSTS_DB", raising=False)
    monkeypatch.delenv("YT_PIPELINE_REQUESTS", raising=False)
    monkeypatch.setattr(config, "load_config", lambda *a, **k: {})
    assert (
        serve.main(
            [
                "--workspaces",
                str(root),
                "--inbox",
                str(tmp_path / "inbox"),
                "--bind",
                "127.0.0.1",
                "--port",
                "0",
                "--check",
                "--json",
            ]
        )
        == EXIT_OK
    )
    captured = capsys.readouterr()
    assert "no costs ledger configured" in captured.err
    assert "no requests file configured" in captured.err
    summary = json.loads(captured.out)
    assert summary["ledger"] is None
    assert summary["requests"] is None


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_opener(payload):
    def opener(request, timeout=None):
        return FakeResponse(payload)
    return opener


def test_fetch_model_credit_parses_the_key_endpoint():
    credit = serve.fetch_model_credit(
        "k", opener=fake_opener({"data": {"usage": 1.8, "limit": 5.0}})
    )
    assert credit == {"usage": 1.8, "limit": 5.0, "remaining": 3.2}
    no_limit = serve.fetch_model_credit(
        "k", opener=fake_opener({"data": {"usage": 1.8, "limit": None}})
    )
    assert no_limit["limit"] is None
    assert no_limit["remaining"] is None


def test_fetch_project_totals_parses_the_json():
    payload = {"balance_usd": 12.5, "monthly_running_usd": 3.0}
    assert serve.fetch_project_totals(
        "https://example.org/costs.json", opener=fake_opener(payload)
    ) == payload
    with pytest.raises(ValueError):
        serve.fetch_project_totals(
            "https://example.org/costs.json", opener=fake_opener({"nope": 1})
        )


def test_costs_page_shows_storage_and_credit(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(
        serve, "fetch_model_credit",
        lambda *a, **k: {"usage": 1.8, "limit": 5.0, "remaining": 3.2},
    )
    server = start_server(root, storage_gb=100)
    try:
        status, _, body = request(server, "GET", "/costs")
        page = body.decode()
        assert status == 200
        assert "GB of 100 GB used" in page
        assert "Model credit: $3.20 left of $5.00" in page
    finally:
        server.shutdown()
        server.server_close()


def test_storage_line_warns_over_80_percent(tmp_path):
    root = make_root(tmp_path)
    (root / "2026-10-14" / "big.bin").write_bytes(b"x" * 1_000_000)
    server = start_server(root, storage_gb=0.0001)
    try:
        status, _, body = request(server, "GET", "/costs")
        page = body.decode()
        assert status == 200
        assert "almost full" in page
        assert 'class="storage warn"' in page
    finally:
        server.shutdown()
        server.server_close()


def test_upload_page_shows_storage_and_credit(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(
        serve, "fetch_model_credit",
        lambda *a, **k: {"usage": 1.8, "limit": 5.0, "remaining": 3.2},
    )
    server = start_server(root, storage_gb=100)
    try:
        status, _, body = request(server, "GET", "/upload")
        page = body.decode()
        assert status == 200
        assert "GB of 100 GB used" in page
        assert "Model credit: $3.20 left of $5.00" in page
    finally:
        server.shutdown()
        server.server_close()


def test_upload_page_warns_on_low_credit(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(
        serve, "fetch_model_credit",
        lambda *a, **k: {"usage": 4.5, "limit": 5.0, "remaining": 0.5},
    )
    server = start_server(root, credit_warn_usd=1.0)
    try:
        status, _, body = request(server, "GET", "/upload")
        page = body.decode()
        assert status == 200
        assert "Low model credit" in page
        assert "a run may pass the key limit" in page
    finally:
        server.shutdown()
        server.server_close()


def test_upload_page_no_warning_when_credit_is_enough(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(
        serve, "fetch_model_credit",
        lambda *a, **k: {"usage": 1.8, "limit": 5.0, "remaining": 3.2},
    )
    server = start_server(root, credit_warn_usd=1.0)
    try:
        status, _, body = request(server, "GET", "/upload")
        page = body.decode()
        assert status == 200
        assert "Low model credit" not in page
    finally:
        server.shutdown()
        server.server_close()


def test_upload_refused_over_the_storage_limit(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    inbox = tmp_path / "inbox"
    server = start_server(root, inbox=inbox, storage_gb=0.0000001)
    try:
        status, _, body = upload(
            server,
            [("folder", None, "kotor-day"), ("brief", None, "hello"), ("files", "a.mp4", b"x")],
        )
        assert status == 413
        assert b"storage limit reached" in body
        assert b"GB used" in body
        assert not (inbox / "kotor-day").exists()
    finally:
        server.shutdown()
        server.server_close()


def test_credit_unknown_on_a_failed_call(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")

    def boom(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr(serve, "fetch_model_credit", boom)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/costs")
        page = body.decode()
        assert status == 200
        assert "Model credit: unknown" in page
    finally:
        server.shutdown()
        server.server_close()


def test_credit_no_limit_set(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(
        serve, "fetch_model_credit",
        lambda *a, **k: {"usage": 0.5, "limit": None, "remaining": None},
    )
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/costs")
        page = body.decode()
        assert status == 200
        assert "no credit limit set" in page
    finally:
        server.shutdown()
        server.server_close()


def test_costs_json_serves_the_totals(tmp_path):
    root = make_root(tmp_path)
    ledger = tmp_path / "costs.db"
    make_ledger(ledger)
    server = start_server(root, ledger=ledger)
    try:
        status, _, body = request(server, "GET", "/costs.json")
        assert status == 200
        payload = json.loads(body)
        assert isinstance(payload["balance_usd"], (int, float))
        assert "notes" not in payload
        assert "requests" not in payload
    finally:
        server.shutdown()
        server.server_close()


def test_costs_json_without_a_ledger_is_404(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, _ = request(server, "GET", "/costs.json")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_costs_page_shows_project_totals(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    totals = {
        "balance_usd": 7.5,
        "monthly_running_usd": 3.0,
        "development_total_usd": 2.0,
        "donations_usd": 10.0,
    }
    monkeypatch.setattr(serve, "fetch_project_totals", lambda *a, **k: totals)
    server = start_server(root, project_totals_url="https://example.org/costs.json")
    try:
        status, _, body = request(server, "GET", "/costs")
        page = body.decode()
        assert status == 200
        assert "Model cost of your clips" in page
        assert "Your model cost to cover" in page
        assert "The project's own costs" in page
        assert "$2,00" in page
        assert page.index("Your model cost to cover") < page.index("The project's own costs")
        assert "Donations minus everything" not in page
    finally:
        server.shutdown()
        server.server_close()


def test_costs_page_project_totals_unavailable(tmp_path, monkeypatch):
    root = make_root(tmp_path)

    def boom(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr(serve, "fetch_project_totals", boom)
    server = start_server(root, project_totals_url="https://example.org/costs.json")
    try:
        status, _, body = request(server, "GET", "/costs")
        page = body.decode()
        assert status == 200
        assert "Project costs are unavailable." in page
    finally:
        server.shutdown()
        server.server_close()


def test_check_reports_the_limits_settings(tmp_path, capsys, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.delenv(config.STORAGE_GB_ENV, raising=False)
    monkeypatch.delenv(config.PROJECT_TOTALS_URL_ENV, raising=False)
    monkeypatch.setattr(config, "load_config", lambda *a, **k: {})
    assert (
        serve.main(
            [
                "--workspaces",
                str(root),
                "--inbox",
                str(tmp_path / "inbox"),
                "--bind",
                "127.0.0.1",
                "--port",
                "0",
                "--storage-gb",
                "100",
                "--project-totals-url",
                "https://example.org/costs.json",
                "--check",
                "--json",
            ]
        )
        == EXIT_OK
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["storage_gb"] == 100.0
    assert summary["credit_warn_usd"] == 1.0
    assert summary["project_totals_url"] == "https://example.org/costs.json"

    assert (
        serve.main(
            [
                "--workspaces",
                str(root),
                "--inbox",
                str(tmp_path / "inbox"),
                "--bind",
                "127.0.0.1",
                "--port",
                "0",
                "--check",
                "--json",
            ]
        )
        == EXIT_OK
    )
    captured = capsys.readouterr()
    assert "no storage limit configured" in captured.err
    assert "no project totals URL configured" in captured.err
    summary = json.loads(captured.out)
    assert summary["storage_gb"] is None
    assert summary["project_totals_url"] is None


def test_upload_page_shows_the_consent_checkbox(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root, consent_text="I own this footage.")
    try:
        status, _, body = request(server, "GET", "/upload")
        assert status == 200
        assert b"I own this footage." in body
        assert b'name="consent"' in body
        assert b"required" in body
    finally:
        server.shutdown()
        server.server_close()


def test_upload_without_consent_is_refused(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    inbox = tmp_path / "inbox"
    stub_recap(monkeypatch, tmp_path)
    server = start_server(root, inbox=inbox, consent_text="I own this footage.")
    try:
        status, _, body = upload(
            server,
            [("folder", None, "kotor-day"), ("brief", None, "hello"), ("files", "a.mp4", b"x")],
        )
        assert status == 400
        assert b"consent" in body
        assert not (inbox / "kotor-day").exists()
        status, headers, _ = upload(
            server,
            [
                ("folder", None, "kotor-day"),
                ("brief", None, "hello"),
                ("consent", None, "1"),
                ("files", "a.mp4", b"x"),
            ],
        )
        assert status == 303
        assert headers["Location"] == "/kotor-day/"
    finally:
        server.shutdown()
        server.server_close()


def test_delete_project_removes_workspace_and_inbox_source(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    name = "kotor-day"
    workspace = root / name
    workspace.mkdir()
    inbox = tmp_path / "inbox"
    source = inbox / name
    source.mkdir(parents=True)
    (source / "clip.mp4").write_bytes(b"video")
    (workspace / ".run.json").write_text(
        json.dumps({"pid": 2**31 - 1, "source": str(source)}), encoding="utf-8"
    )
    server = start_server(root, inbox=inbox)
    try:
        status, headers, _ = request(server, "POST", f"/{name}/delete")
        assert status == 303
        assert headers["Location"] == "/"
        assert not workspace.exists()
        assert not source.exists()
    finally:
        server.shutdown()
        server.server_close()


def test_delete_project_refused_while_running(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.setattr(serve, "project_status", lambda directory: "running")
    server = start_server(root)
    try:
        status, _, body = request(server, "POST", "/2026-10-14/delete")
        assert status == 409
        assert b"already running" in body
        assert (root / "2026-10-14").is_dir()
    finally:
        server.shutdown()
        server.server_close()


def test_delete_project_get_is_405(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, _ = request(server, "GET", "/2026-10-14/delete")
        assert status == 405
    finally:
        server.shutdown()
        server.server_close()


def test_features_off_refuse_the_routes(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root, features=("upload",))
    try:
        assert request(server, "GET", "/costs")[0] == 404
        assert request(
            server, "POST", "/costs/request", headers=form_headers(), body=b""
        )[0] == 404
        assert request(server, "GET", "/2026-10-14/explain.html")[0] == 404
        assert request(server, "GET", "/2026-10-14/shotlog.html")[0] == 404
        assert request(server, "GET", "/upload")[0] == 200
    finally:
        server.shutdown()
        server.server_close()


def test_features_off_hide_the_links(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root, features=("upload",))
    try:
        status, _, body = request(server, "GET", "/")
        assert status == 200
        assert b'href="/costs"' not in body
        status, _, body = request(server, "GET", "/2026-10-14/")
        assert status == 200
        assert b"?tab=why" not in body
        assert b"?tab=shots" not in body
    finally:
        server.shutdown()
        server.server_close()


def test_features_default_to_all(tmp_path, monkeypatch):
    monkeypatch.delenv("YT_PIPELINE_FEATURES", raising=False)
    monkeypatch.setattr(config, "load_config", lambda *a, **k: {})
    assert config.web_features() == config.WEB_FEATURES
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        assert request(server, "GET", "/costs")[0] == 200
    finally:
        server.shutdown()
        server.server_close()


def test_unknown_feature_is_a_config_error(tmp_path):
    with pytest.raises(config.ConfigError):
        config.web_features(cli_value="bogus")
    path = tmp_path / "config.yaml"
    path.write_text(
        "models:\n  coder: x\nllm_budget_usd: 1.0\nllm_backend: mock\n"
        "web:\n  features: [bogus]\n",
        encoding="utf-8",
    )
    with pytest.raises(config.ConfigError):
        config.load_config(path)


def test_consent_text_from_env_and_config(monkeypatch):
    monkeypatch.setenv("YT_PIPELINE_CONSENT_TEXT", "x")
    assert config.consent_text() == "x"
    monkeypatch.delenv("YT_PIPELINE_CONSENT_TEXT")
    assert config.consent_text(config={"web": {"consent_text": "y"}}) == "y"


def test_upload_page_has_the_brief_hint_chips():
    page = serve.render_upload([])
    for label in (
        "The story or the best moment",
        "Mood",
        "Length",
        "What to leave out",
    ):
        assert label in page
    assert "Where and when" not in page
    assert 'data-line="Who is in it: "' not in page
    assert 'data-line="The best moment: "' in page


def test_reedit_form_has_the_brief_hint_chips():
    page = serve.render_reedit("2026-10-14", [])
    assert 'data-line="The best moment: "' in page
    assert 'data-line="Who is in it: "' not in page
    assert 'name="brief"' in page
    assert "brief-hints" in page


def test_brief_placeholder_is_multiline_and_not_a_value():
    page = serve.render_upload([])
    assert 'placeholder="For example:' in page
    for line in (
        "The best moment: the ride down the tree-lined lane",
        "Mood: calm start, energy at the end",
        "Leave out: the parking lot shots",
    ):
        assert line in page
    assert "Where and when: a motorbike weekend in the Eifel, April 2026" not in page
    assert "Who is in it: me, and my friend on the second bike" not in page
    start = page.index('name="brief"')
    tag = page[start:page.index(">", start)]
    assert "value=" not in tag


def test_upload_page_has_the_fact_fields():
    page = serve.render_upload([])
    for name in ("fact_place", "fact_when", "fact_who", "fact_occasion"):
        assert f'name="{name}"' in page


def test_reedit_form_shows_the_saved_facts():
    page = serve.render_reedit("2026-10-14", [], facts={"place": "Retezat"})
    assert 'value="Retezat"' in page


def test_upload_page_without_consent_has_no_checkbox(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/upload")
        assert status == 200
        assert b'name="consent"' not in body
    finally:
        server.shutdown()
        server.server_close()


def test_project_page_has_the_delete_bin(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/")
        assert status == 200
        assert b"delete-project" in body
        assert b"project-del" in body
    finally:
        server.shutdown()
        server.server_close()


def test_project_page_shows_the_reviewer_line(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    edit = {
        "meta": {
            "director": {
                "review": {
                    "approved": False,
                    "objections": [
                        {"slot": "v2", "shot": 2, "text": "too long — shorten to about 2.5 s"}
                    ],
                }
            }
        }
    }
    (project / "edit.json").write_text(json.dumps(edit), encoding="utf-8")
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/")
        assert status == 200
        page = body.decode()
        assert "Reviewer: not approved" in page
        assert "Shot 2" in page
        assert "too long — shorten to about 2.5 s" in page
    finally:
        server.shutdown()
        server.server_close()


def test_project_page_without_review_shows_nothing(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/")
        assert status == 200
        assert b"Reviewer:" not in body
    finally:
        server.shutdown()
        server.server_close()


def test_feedback_reply_page_shows_the_reply_and_estimate(tmp_path, monkeypatch):
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
        status, _, body = request(
            server,
            "POST",
            "/2026-10-14/feedback-reply",
            headers=multipart_headers(),
            body=multipart_body([("feedback", None, "drop 0071/s2")]),
        )
        assert status == 200, body
        page = body.decode()
        assert "I will drop 0071/s2." in page
        assert "about $0,05 and 12 s" in page
        assert "Make the new version" in page
        assert "Change my request" in page
        assert calls == []
    finally:
        server.shutdown()
        server.server_close()


def test_feedback_reply_requires_text(tmp_path, monkeypatch):
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


def test_approve_starts_the_reedit_and_records_it(tmp_path, monkeypatch):
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
        status, headers, body = request(
            server,
            "POST",
            "/2026-10-14/re-edit",
            headers=multipart_headers(),
            body=multipart_body(
                [("feedback", None, "drop 0071/s2"), ("approved", None, "1")]
            ),
        )
        assert status == 303, body
        assert headers["Location"] == "/2026-10-14/"
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


def test_reedit_without_approve_records_no_approved_line(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    inbox = tmp_path / "inbox"
    source = inbox / "kotor-day"
    source.mkdir(parents=True)
    (source / "brief.txt").write_text("about 60 seconds", encoding="utf-8")
    (root / "2026-10-14" / ".run.json").write_text(
        json.dumps({"pid": 1, "source": str(source)}), encoding="utf-8"
    )
    stub_recap(monkeypatch, tmp_path)
    server = start_server(root, inbox=inbox)
    try:
        status, _, body = request(
            server,
            "POST",
            "/2026-10-14/re-edit",
            headers=multipart_headers(),
            body=multipart_body([("feedback", None, "drop 0071/s2")]),
        )
        assert status == 303, body
        assert not (root / "2026-10-14" / "feedback.jsonl").exists()
    finally:
        server.shutdown()
        server.server_close()


def test_reedit_form_has_the_ask_button():
    page = serve.render_reedit("2026-10-14", [])
    assert "feedback-reply" in page
    assert "Ask the director" in page


def test_reedit_form_prefills_the_feedback():
    page = serve.render_reedit("2026-10-14", [], feedback_prefill="drop 0071/s2")
    assert "drop 0071/s2" in page


def make_files_project(tmp_path):
    root = tmp_path / "projects"
    project = root / "trip"
    project.mkdir(parents=True)
    (project / "recap_720p.mp4").write_bytes(b"x")
    proxy = project / "proxy"
    proxy.mkdir()
    (proxy / "clip_a.mp4").write_bytes(b"a" * 100)
    (proxy / "photo.jpg").write_bytes(b"p" * 50)
    inbox = tmp_path / "inbox"
    source = inbox / "trip"
    source.mkdir(parents=True)
    (source / "clip_b.mp4").write_bytes(b"b" * 200)
    (project / ".run.json").write_text(
        json.dumps({"pid": 2**31 - 1, "source": str(source)}), encoding="utf-8"
    )
    (project / "edit.json").write_text(
        json.dumps({"video": [{"clip": "clip_a.mp4", "in_s": 0.0, "out_s": 5.0}]}),
        encoding="utf-8",
    )
    return root, project, source


def test_files_tab_lists_proxy_files_with_state(tmp_path):
    root, project, source = make_files_project(tmp_path)
    server = start_server(root, inbox=tmp_path / "inbox")
    try:
        status, _, body = request(server, "GET", "/trip/?tab=files")
        page = body.decode()
        assert status == 200
        assert "clip_a.mp4" in page
        assert "photo.jpg" in page
        assert "in the edit" in page
        assert "clip_b.mp4" in page
        assert "new" in page
        assert "3 files, 1 new since the last edit, 0 left out" in page
    finally:
        server.shutdown()
        server.server_close()


def test_exclude_file_writes_and_shows_left_out(tmp_path):
    root, project, source = make_files_project(tmp_path)
    server = start_server(root, inbox=tmp_path / "inbox")
    try:
        status, headers, _ = request(
            server,
            "POST",
            "/trip/exclude-file",
            headers=form_headers(),
            body=urllib.parse.urlencode({"name": "clip_a.mp4", "action": "exclude"}),
        )
        assert status == 303
        assert headers["Location"] == "/trip/?tab=files"
        assert json.loads((project / "excluded.json").read_text(encoding="utf-8")) == {
            "excluded": ["clip_a.mp4"]
        }
        status, _, body = request(server, "GET", "/trip/?tab=files")
        page = body.decode()
        assert "left out" in page
        assert "Bring back" in page
    finally:
        server.shutdown()
        server.server_close()


def test_include_file_brings_it_back(tmp_path):
    root, project, source = make_files_project(tmp_path)
    (project / "excluded.json").write_text(
        json.dumps({"excluded": ["clip_a.mp4"]}), encoding="utf-8"
    )
    server = start_server(root, inbox=tmp_path / "inbox")
    try:
        status, headers, _ = request(
            server,
            "POST",
            "/trip/exclude-file",
            headers=form_headers(),
            body=urllib.parse.urlencode({"name": "clip_a.mp4", "action": "include"}),
        )
        assert status == 303
        assert json.loads((project / "excluded.json").read_text(encoding="utf-8")) == {
            "excluded": []
        }
    finally:
        server.shutdown()
        server.server_close()


def test_add_files_stores_in_the_recorded_source(tmp_path):
    root, project, source = make_files_project(tmp_path)
    server = start_server(root, inbox=tmp_path / "inbox")
    try:
        status, headers, body = request(
            server,
            "POST",
            "/trip/add-files",
            headers=multipart_headers(),
            body=multipart_body([("files", "clip_c.mp4", b"new-bytes")]),
        )
        assert status == 303, body
        assert headers["Location"] == "/trip/?tab=files"
        assert (source / "clip_c.mp4").read_bytes() == b"new-bytes"
    finally:
        server.shutdown()
        server.server_close()


def test_reedit_form_shows_the_counts_line(tmp_path):
    root, project, source = make_files_project(tmp_path)
    server = start_server(root, inbox=tmp_path / "inbox")
    try:
        status, _, body = request(server, "GET", "/trip/")
        page = body.decode()
        assert status == 200
        assert "3 files, 1 new since the last edit, 0 left out" in page
        assert "Manage files" in page
    finally:
        server.shutdown()
        server.server_close()


def test_reedit_open_flag_opens_the_form(tmp_path):
    root, project, source = make_files_project(tmp_path)
    server = start_server(root, inbox=tmp_path / "inbox")
    try:
        _, _, body = request(server, "GET", "/trip/?tab=files&reedit=1")
        page = body.decode()
        assert '<details class="reedit" open>' in page
    finally:
        server.shutdown()
        server.server_close()


def test_files_routes_refused_when_recap_off(tmp_path):
    root, project, source = make_files_project(tmp_path)
    server = start_server(root, inbox=tmp_path / "inbox", features=("upload",))
    try:
        status, _, _ = request(
            server,
            "POST",
            "/trip/exclude-file",
            headers=form_headers(),
            body=urllib.parse.urlencode({"name": "clip_a.mp4", "action": "exclude"}),
        )
        assert status == 404
        status, _, _ = request(
            server,
            "POST",
            "/trip/add-files",
            headers=multipart_headers(),
            body=multipart_body([("files", "clip_c.mp4", b"x")]),
        )
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_pages_share_the_theme_tokens(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        for path in ("/", "/upload", "/costs", "/2026-10-14/"):
            status, _, body = request(server, "GET", path)
            assert status == 200, path
            text = body.decode("utf-8")
            for token in ("--water:", "--sea:", "--radius-l:", "--tap:44px",
                          "--font-display:", "--sp-4:", "--bg:var(--water)"):
                assert token in text, (path, token)
            assert "prefers-color-scheme:light" in text, path
            assert 'class="brand-mark"' in text, path
    finally:
        server.shutdown()
        server.server_close()


def test_brand_page_lists_the_three_options(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/brand")
        assert status == 200
        text = body.decode("utf-8")
        for name in ("option-1", "option-2", "option-3"):
            assert name in text, name
        assert "in use" in text
    finally:
        server.shutdown()
        server.server_close()


def test_brand_svg_files_are_served(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, headers, body = request(server, "GET", "/brand/option-1.svg")
        assert status == 200
        assert headers["Content-Type"] == "image/svg+xml"
        assert body.startswith(b"<svg")
    finally:
        server.shutdown()
        server.server_close()


def test_favicon_serves_the_chosen_mark(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, headers, body = request(server, "GET", "/favicon.svg")
        assert status == 200
        assert headers["Content-Type"] == "image/svg+xml"
        assert body == (serve.BRAND_DIR / "option-1.svg").read_bytes()
    finally:
        server.shutdown()
        server.server_close()
    server = start_server(root, brand_logo="option-2")
    try:
        status, _, body = request(server, "GET", "/favicon.svg")
        assert status == 200
        assert body == (serve.BRAND_DIR / "option-2.svg").read_bytes()
    finally:
        server.shutdown()
        server.server_close()


def test_every_page_links_the_favicon(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        for path in ("/", "/upload", "/costs", "/2026-10-14/"):
            status, _, body = request(server, "GET", path)
            assert status == 200, path
            assert '<link rel="icon" type="image/svg+xml" href="/favicon.svg">' in body.decode("utf-8"), path
    finally:
        server.shutdown()
        server.server_close()


def test_header_shows_the_chosen_mark(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        _, _, body = request(server, "GET", "/")
        text = body.decode("utf-8")
        assert 'class="brand-mark"' in text
        assert "Clapperboard" in text
    finally:
        server.shutdown()
        server.server_close()
    server = start_server(root, brand_logo="option-3")
    try:
        _, _, body = request(server, "GET", "/")
        assert "Focus reticle" in body.decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()


def test_brand_svg_files_are_valid_and_use_two_theme_colours():
    for name in serve.BRAND_OPTIONS:
        path = serve.BRAND_DIR / f"{name}.svg"
        text = path.read_text(encoding="utf-8")
        ET.fromstring(text)
        assert "var(--sea,#57b8c4)" in text, name
        assert "var(--stone,#ece5d5)" in text, name


def test_check_json_reports_brand_logo(tmp_path, capsys):
    root = make_root(tmp_path)
    assert (
        serve.main(
            [
                "--workspaces",
                str(root),
                "--inbox",
                str(tmp_path / "inbox"),
                "--bind",
                "127.0.0.1",
                "--port",
                "0",
                "--check",
                "--json",
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["brand_logo"] == "option-1"


def test_theme_has_a_light_palette():
    assert "prefers-color-scheme:light" in serve.THEME_CSS
    assert "--water:#f4f4f2" in serve.THEME_CSS
    assert "--stone:#1b1c1f" in serve.THEME_CSS


def test_key_classes_on_each_page(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        _, _, index_page = request(server, "GET", "/")
        assert b'class="bar"' in index_page
        assert b'class="mark"' in index_page
        assert b'class="projects"' in index_page
        _, _, upload = request(server, "GET", "/upload")
        assert b'class="drop"' in upload
        assert b'class="go start"' in upload
        assert b'class="brief-hints"' in upload
        _, _, costs = request(server, "GET", "/costs")
        assert b"class=\"clip-costs\"" in costs
        _, _, project = request(server, "GET", "/2026-10-14/")
        assert b'class="tabs"' in project
        assert b'class="panel"' in project
        assert b'class="player"' in project
    finally:
        server.shutdown()
        server.server_close()


def write_edit(project, slots):
    (project / "edit.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "generated_at": "2026-10-23T00:00:00+00:00",
                "brief": {"text": "a calm recap"},
                "sources": [{"file": "a.mp4", "path": "/x/a.mp4", "duration_s": 10.0}],
                "video": slots,
            }
        ),
        encoding="utf-8",
    )


def test_thumbs_are_served(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    (project / "thumbs").mkdir()
    (project / "thumbs" / "poster.jpg").write_bytes(b"posterbytes")
    (project / "thumbs" / "shot-1.jpg").write_bytes(b"shotbytes")
    version = project / "versions" / "v001"
    (version / "thumbs").mkdir(parents=True)
    (version / "edit.json").write_text("{}", encoding="utf-8")
    (version / "thumbs" / "poster.jpg").write_bytes(b"versionposter")
    server = start_server(root)
    try:
        status, headers, body = request(server, "GET", "/2026-10-14/thumbs/poster.jpg")
        assert status == 200
        assert headers["Content-Type"] == "image/jpeg"
        assert body == b"posterbytes"
        status, _, body = request(server, "GET", "/2026-10-14/thumbs/shot-1.jpg")
        assert status == 200
        assert body == b"shotbytes"
        status, _, body = request(server, "GET", "/2026-10-14/versions/v001/thumbs/poster.jpg")
        assert status == 200
        assert body == b"versionposter"
        assert request(server, "GET", "/2026-10-14/thumbs/evil.jpg")[0] == 404
        assert request(server, "GET", "/2026-10-14/thumbs/../edit.json")[0] == 404
    finally:
        server.shutdown()
        server.server_close()


def test_projects_page_shows_poster_cards(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    (project / "thumbs").mkdir()
    (project / "thumbs" / "poster.jpg").write_bytes(b"jpg")
    server = start_server(root)
    try:
        _, _, body = request(server, "GET", "/")
        text = body.decode()
        assert 'class="card"' in text
        assert 'src="/2026-10-14/thumbs/poster.jpg"' in text
    finally:
        server.shutdown()
        server.server_close()
    (project / "thumbs" / "poster.jpg").unlink()
    server = start_server(root)
    try:
        _, _, body = request(server, "GET", "/")
        text = body.decode()
        assert 'class="poster empty"' in text
        assert "thumbs/poster.jpg" not in text
    finally:
        server.shutdown()
        server.server_close()


def test_strip_shows_shot_thumbnails(tmp_path):
    root = make_root(tmp_path)
    project = root / "2026-10-14"
    write_edit(
        project,
        [{"id": "v1", "clip": "a.mp4", "in_s": 0.0, "out_s": 4.0, "score": 0.5, "reason": "r"}],
    )
    (project / "thumbs").mkdir()
    (project / "thumbs" / "shot-1.jpg").write_bytes(b"jpg")
    recap = project / "recap_720p.mp4"
    stamp = (project / "edit.json").stat().st_mtime + 10
    os.utime(recap, (stamp, stamp))
    server = start_server(root)
    try:
        _, _, body = request(server, "GET", "/2026-10-14/")
        assert "background-image:url('thumbs/shot-1.jpg')" in body.decode()
    finally:
        server.shutdown()
        server.server_close()
    (project / "thumbs" / "shot-1.jpg").unlink()
    server = start_server(root)
    try:
        _, _, body = request(server, "GET", "/2026-10-14/")
        assert "background-image" not in body.decode()
    finally:
        server.shutdown()
        server.server_close()


def test_pages_are_phone_first(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        for path in ("/", "/upload", "/costs", "/2026-10-14/"):
            status, _, body = request(server, "GET", path)
            assert status == 200, path
            assert b'name="viewport" content="width=device-width, initial-scale=1"' in body, path
        _, _, upload = request(server, "GET", "/upload")
        assert b'accept="video/*,image/*"' in upload
        assert b"multiple" in upload
        _, _, project = request(server, "GET", "/2026-10-14/")
        assert b"playsinline" in project
    finally:
        server.shutdown()
        server.server_close()


def test_shared_bin_icon_and_ask_helper():
    assert serve.BIN_ICON in serve.page_script(serve.PICK_SCRIPT)
    assert serve.BIN_ICON in serve.page_script(serve.DELETE_SCRIPT)
    assert serve.BIN_ICON in serve.page_script(serve.FILES_SCRIPT)
    for script in (serve.PICK_SCRIPT, serve.DELETE_SCRIPT, serve.FILES_SCRIPT):
        assert "askYesNo(" in script
        assert "__BIN__" in script
    assert serve.page_script(serve.PICK_SCRIPT).count(serve.BIN_ICON) == 1


def test_recap_artifacts_are_refused_with_the_recap_part_off():
    for artifact in ("recap_720p.mp4", "recap_720p_9x16.mp4", "recap_final.mp4", "recap_final_9x16.mp4"):
        assert serve.ARTIFACT_FEATURES[artifact] == "recap"
    assert "shorts_candidates.json" not in serve.ARTIFACT_FEATURES


def test_brief_hints_and_placeholder_have_no_length():
    labels = [label for label, _ in serve.BRIEF_HINTS]
    assert "Length" not in labels
    assert "Length: about 60 seconds" not in serve.BRIEF_PLACEHOLDER


def test_upload_without_consent_removes_the_staged_files(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    inbox = tmp_path / "inbox"
    stub_recap(monkeypatch, tmp_path)
    server = start_server(root, inbox=inbox, consent_text="I own this footage.")
    try:
        status, _, _ = upload(
            server,
            [("folder", None, "kotor-day"), ("brief", None, "hello"), ("files", "a.mp4", b"x")],
        )
        assert status == 400
        assert list(inbox.glob(".upload-*")) == []
    finally:
        server.shutdown()
        server.server_close()


def test_credit_failure_is_cached_for_two_minutes(monkeypatch):
    calls = []

    def boom(*args, **kwargs):
        calls.append(1)
        raise OSError("down")

    monkeypatch.setattr(serve, "fetch_model_credit", boom)
    assert serve.model_credit("k", now=1000.0) is None
    assert serve.model_credit("k", now=1001.0) is None
    assert len(calls) == 1
    assert serve.model_credit("k", now=1000.0 + serve.FAILURE_CACHE_SECONDS + 1) is None
    assert len(calls) == 2


def test_totals_failure_is_cached_for_two_minutes(monkeypatch):
    calls = []

    def boom(*args, **kwargs):
        calls.append(1)
        raise OSError("down")

    monkeypatch.setattr(serve, "fetch_project_totals", boom)
    assert serve.project_totals("http://x/costs.json", now=1000.0) is None
    assert serve.project_totals("http://x/costs.json", now=1001.0) is None
    assert len(calls) == 1


def test_http_timeout_is_three_seconds():
    assert serve.HTTP_TIMEOUT_SECONDS == 3
    assert serve.fetch_model_credit.__defaults__[0] == 3
    assert serve.fetch_project_totals.__defaults__[0] == 3


def test_reedit_form_carries_the_shown_version():
    page = serve.render_reedit("kotor-day", [], version="v002")
    assert '<input type="hidden" name="version" value="v002">' in page
    assert 'name="version"' not in serve.render_reedit("kotor-day", [])


def test_project_date_is_the_filming_date(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    (project / "ingest.json").write_text(
        json.dumps(
            {
                "clips": [{"creation_time": "2026-07-10T10:00:00+00:00"}],
                "photos": [{"creation_time": "2026-07-08T10:00:00+00:00"}],
            }
        ),
        encoding="utf-8",
    )
    assert serve.project_date(project) == "2026-07-08"
    assert serve.project_date(tmp_path / "missing") == ""


def test_instance_due_block_is_red_until_paid(monkeypatch):
    monkeypatch.setattr(serve, "EUR_PER_USD", None)
    open_block = serve.instance_due_block(3.0, 1.0, 5.0)
    assert 'class="balance minus"' in open_block
    assert "$2,00" in open_block and "Credit limit: $5,00" in open_block
    paid_block = serve.instance_due_block(3.0, 3.0)
    assert 'class="balance plus"' in paid_block and "Paid" in paid_block


def test_instance_paid_sums_the_local_donations(tmp_path):
    from common.costs import connect as connect_ledger, insert_entry

    ledger = tmp_path / "costs.db"
    assert serve.instance_paid(ledger) == 0.0
    connection = connect_ledger(ledger)
    insert_entry(connection, "2026-10-23", "donation", 2.5, "", None)
    insert_entry(connection, "2026-10-24", "donation", 1.5, "", None)
    connection.commit()
    connection.close()
    assert serve.instance_paid(ledger) == 4.0
