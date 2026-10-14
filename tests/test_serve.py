import http.client
import json
import threading

import serve


def make_root(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    project = root / "2026-10-14"
    project.mkdir()
    (project / "recap_720p.mp4").write_bytes(bytes(range(100)))
    (project / "shotlog.html").write_text("<html>contact sheet</html>", encoding="utf-8")
    (project / "edit.json").write_text("{}", encoding="utf-8")
    proxy_only = root / "raw"
    (proxy_only / "proxy").mkdir(parents=True)
    (proxy_only / "proxy" / "clip.mp4").write_bytes(b"proxy")
    (root / "notes.txt").write_text("ignore", encoding="utf-8")
    return root


def start_server(root):
    server = serve.build_server(root, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def request(server, method, path, headers=None):
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=5
    )
    connection.request(method, path, headers=headers or {})
    response = connection.getresponse()
    body = response.read()
    result = response.status, dict(response.getheaders()), body
    connection.close()
    return result


def test_discover_projects_ignores_non_projects(tmp_path):
    root = make_root(tmp_path)
    assert serve.discover_projects(root) == ["2026-10-14"]


def test_projects_sorted_descending(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    for name in ("2026-10-12", "2026-10-14", "2026-10-13"):
        folder = root / name
        folder.mkdir()
        (folder / "recap_720p.mp4").write_bytes(b"x")
    assert serve.discover_projects(root) == ["2026-10-14", "2026-10-13", "2026-10-12"]


def test_index_lists_project(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, headers, body = request(server, "GET", "/")
        assert status == 200
        assert headers["Content-Type"].startswith("text/html")
        assert b"2026-10-14" in body
        assert b'href="/2026-10-14/"' in body
        assert b"raw" not in body
    finally:
        server.shutdown()
        server.server_close()


def test_project_page_links_existing_artifacts_only(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, _, body = request(server, "GET", "/2026-10-14/")
        assert status == 200
        assert b'<video controls preload="metadata" src="recap_720p.mp4">' in body
        assert b"recap_720p.mp4" in body
        assert b"shotlog.html" in body
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
        assert body == (project / "shotlog.html").read_bytes()
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
            "/2026-10-14/proxy/clip.mp4",
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


def test_post_returns_405(tmp_path):
    root = make_root(tmp_path)
    server = start_server(root)
    try:
        status, headers, _ = request(server, "POST", "/")
        assert status == 405
        assert headers["Allow"] == "GET, HEAD"
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
    assert (
        serve.main(
            [
                "--workspaces",
                str(root),
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
    assert summary["tool"] == "scripts/serve.py"
    assert summary["status"] == "check"
    assert summary["bind"] == "127.0.0.1"
    assert isinstance(summary["port"], int) and summary["port"] > 0
    assert summary["url"] == f"http://127.0.0.1:{summary['port']}/"
    assert len(summary["projects"]) == 1
    project = summary["projects"][0]
    assert project["name"] == "2026-10-14"
    assert project["preview"].endswith("/2026-10-14/recap_720p.mp4")
    assert project["shotlog"].endswith("/2026-10-14/shotlog.html")
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


def test_check_human_output(tmp_path, capsys):
    root = make_root(tmp_path)
    assert (
        serve.main(["--workspaces", str(root), "--bind", "127.0.0.1", "--check"]) == 0
    )
    out = capsys.readouterr().out
    assert out.startswith("check: http://127.0.0.1:")
    assert "2026-10-14" in out
