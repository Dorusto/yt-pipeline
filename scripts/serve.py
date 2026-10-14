#!/usr/bin/env python3
"""Read-only HTTP server for recap workspaces, bound to the tailnet address only.

Usage:
    python scripts/serve.py --workspaces ~/media/projects
    python scripts/serve.py --workspaces ~/media/projects --check --json

``--workspaces`` is a folder whose direct subfolders are project workspaces. The
server lists, in descending folder-name order, every direct child directory that
holds at least one of ``recap_720p.mp4``, ``recap_final.mp4``, ``shotlog.html``
or ``edit.json``, and serves an index page plus a page per project. Only the
three finished artifacts are ever served; proxies, ``edit.json``, ``source/``
symlinks and every other file stay private. The address is a single host,
never the wildcard, so the server is reachable over the tailnet only. The tool
only reads; it never writes inside the workspaces folder.
"""

import argparse
import http.server
import json
import socket
import sys
import time
import urllib.parse
from pathlib import Path

TOOL = "scripts/serve.py"
DEFAULT_PORT = 8091
WILDCARD_BINDS = {"", "0.0.0.0", "::"}
DETECTED_ARTIFACTS = ("recap_720p.mp4", "recap_final.mp4", "shotlog.html", "edit.json")
SERVED_FILES = ("recap_720p.mp4", "recap_final.mp4", "shotlog.html")
CONTENT_TYPES = {
    "recap_720p.mp4": "video/mp4",
    "recap_final.mp4": "video/mp4",
    "shotlog.html": "text/html; charset=utf-8",
}


class ServeError(RuntimeError):
    """Raised when the server cannot start or the request is unacceptable."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="serve.py",
        usage=(
            "serve.py [-h] --workspaces WORKSPACES [--bind ADDR] [--port PORT]"
            " [--check] [--json]"
        ),
        description=(
            "Serve the recap projects in a workspaces folder over the tailnet"
            " (read-only, single bind address)."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/serve.py --workspaces ~/media/projects\n"
            "  python scripts/serve.py --workspaces ~/media/projects --port 8091 --json\n"
            "  python scripts/serve.py --workspaces ~/media/projects --check --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--workspaces",
        required=True,
        help="folder whose direct subfolders are project workspaces",
    )
    parser.add_argument(
        "--bind",
        default=None,
        help="host address to bind (default: auto-detected primary IPv4)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"TCP port, 0 for an ephemeral port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the root and the bind, print the summary and exit",
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def detect_bind():
    """Return this machine's primary IPv4 address, falling back to loopback."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def discover_projects(root):
    """Return the project folder names under ``root``, newest first."""
    root = Path(root)
    names = [
        child.name
        for child in root.iterdir()
        if child.is_dir()
        and any((child / artifact).is_file() for artifact in DETECTED_ARTIFACTS)
    ]
    return sorted(names, reverse=True)


def existing_artifacts(directory):
    """Return the served artifact names present in ``directory``, in fixed order."""
    return [name for name in SERVED_FILES if (Path(directory) / name).is_file()]


def html_escape(text):
    """Escape a value for safe interpolation into the generated HTML."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


PAGE_STYLE = (
    "body{font-family:system-ui,sans-serif;margin:0;padding:1rem;"
    "background:#111;color:#eee}a{color:#6cf}"
    "ul{list-style:none;padding:0}li{padding:.4rem 0}"
    "video{width:100%;max-width:640px;background:#000}"
    ".marks{color:#aaa;font-size:.9rem}"
)


def render_index(projects):
    """Return the HTML index page linking to every project."""
    rows = []
    for project in projects:
        directory = Path(project["root"]) / project["name"]
        marks = " ".join(existing_artifacts(directory))
        rows.append(
            f'<li><a href="/{html_escape(project["name"])}/">'
            f'{html_escape(project["name"])}</a> '
            f'<span class="marks">{html_escape(marks)}</span></li>'
        )
    body = "\n".join(rows) if rows else "<li>no projects</li>"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Recaps</title>"
        f"<style>{PAGE_STYLE}</style></head><body>"
        "<h1>Recaps</h1>"
        f"<ul>{body}</ul>"
        "</body></html>\n"
    )


def render_project(name, artifacts):
    """Return the HTML page for one project with a player and artifact links."""
    links = "\n".join(
        f'<li><a href="{html_escape(artifact)}">{html_escape(artifact)}</a></li>'
        for artifact in artifacts
    )
    player = ""
    if "recap_720p.mp4" in artifacts:
        player = (
            '<video controls preload="metadata" src="recap_720p.mp4"></video>'
        )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html_escape(name)}</title>"
        f"<style>{PAGE_STYLE}</style></head><body>"
        f"<h1>{html_escape(name)}</h1>"
        f"{player}"
        f"<ul>{links}</ul>"
        "</body></html>\n"
    )


def parse_path(raw_path):
    """Return the decoded path segments, or None when the path is unsafe."""
    path = urllib.parse.urlsplit(raw_path).path
    path = urllib.parse.unquote(path)
    if "\\" in path or "\x00" in path:
        return None
    segments = [segment for segment in path.split("/") if segment not in ("", ".")]
    if any(segment == ".." for segment in segments):
        return None
    return segments


def parse_range(value, size):
    """Return (start, end) for a single byte range, or None when it is invalid."""
    value = (value or "").strip()
    if not value.startswith("bytes="):
        return None
    spec = value[len("bytes="):].strip()
    if "," in spec or "-" not in spec:
        return None
    start_text, end_text = spec.split("-", 1)
    if start_text == "":
        if not end_text.isdigit():
            return None
        suffix = int(end_text)
        if suffix <= 0 or size == 0:
            return None
        return max(0, size - suffix), size - 1
    if not start_text.isdigit():
        return None
    start = int(start_text)
    if end_text == "":
        end = size - 1
    else:
        if not end_text.isdigit():
            return None
        end = int(end_text)
    if size == 0 or start > end or start >= size:
        return None
    return start, min(end, size - 1)


class WorkspaceHandler(http.server.BaseHTTPRequestHandler):
    """Serve the allowlisted index, project pages and three artifacts."""

    server_version = "yt-serve/1.0"

    def do_GET(self):
        self.handle_request(send_body=True)

    def do_HEAD(self):
        self.handle_request(send_body=False)

    def _method_not_allowed(self):
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_OPTIONS = _method_not_allowed

    def handle_request(self, send_body):
        """Route one request against the allowlist and write the response."""
        root = self.server.root
        segments = parse_path(self.path)
        if segments is None:
            self.send_not_found()
            return
        if not segments:
            projects = [
                {"name": name, "root": str(root)} for name in discover_projects(root)
            ]
            self.send_html(render_index(projects), send_body)
            return
        project = segments[0]
        directory = root / project
        if len(segments) == 1:
            if not directory.is_dir():
                self.send_not_found()
                return
            artifacts = existing_artifacts(directory)
            if not artifacts:
                self.send_not_found()
                return
            self.send_html(render_project(project, artifacts), send_body)
            return
        if len(segments) == 2:
            artifact = segments[1]
            if artifact not in SERVED_FILES:
                self.send_not_found()
                return
            target = directory / artifact
            if not directory.is_dir() or not target.is_file():
                self.send_not_found()
                return
            self.send_file(target, send_body)
            return
        self.send_not_found()

    def send_not_found(self):
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_html(self, text, send_body):
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def send_file(self, path, send_body):
        size = path.stat().st_size
        start, end, status = 0, max(size - 1, 0), 200
        range_header = self.headers.get("Range")
        if range_header is not None:
            parsed = parse_range(range_header, size)
            if parsed is None:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end = parsed
            status = 206
        length = end - start + 1 if size else 0
        self.send_response(status)
        self.send_header("Content-Type", CONTENT_TYPES.get(path.name, "application/octet-stream"))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not send_body or not length:
            return
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


class WorkspaceServer(http.server.ThreadingHTTPServer):
    """Threading HTTP server that knows the workspace root."""

    daemon_threads = True

    def __init__(self, address, root):
        self.root = Path(root)
        super().__init__(address, WorkspaceHandler)


def build_server(root, bind, port):
    """Bind ``root`` at ``bind:port`` and return the ready server."""
    return WorkspaceServer((bind, port), root)


def build_summary(root, bind, port, projects, status, started):
    """Return the flat summary object shared by ``--json`` and ``--check``."""
    host = f"[{bind}]" if ":" in bind else bind
    base = f"http://{host}:{port}"
    entries = []
    for name in projects:
        directory = Path(root) / name
        artifacts = existing_artifacts(directory)
        entries.append(
            {
                "name": name,
                "url": f"{base}/{name}/",
                "preview": f"{base}/{name}/recap_720p.mp4"
                if "recap_720p.mp4" in artifacts
                else None,
                "final": f"{base}/{name}/recap_final.mp4"
                if "recap_final.mp4" in artifacts
                else None,
                "shotlog": f"{base}/{name}/shotlog.html"
                if "shotlog.html" in artifacts
                else None,
            }
        )
    return {
        "tool": TOOL,
        "status": status,
        "root": str(root),
        "bind": bind,
        "port": port,
        "url": f"{base}/",
        "projects": entries,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }


def print_human(summary):
    """Print the human startup summary, one line per project."""
    print(f"{summary['status']}: {summary['url']}")
    if not summary["projects"]:
        print("  no projects")
        return
    for project in summary["projects"]:
        marks = [
            label
            for key, label in (("preview", "recap"), ("final", "final"), ("shotlog", "shotlog"))
            if project[key]
        ]
        print(f"  {project['name']}  {' '.join(marks)}")


def resolve_root(value):
    """Resolve and validate the workspaces folder, raising ServeError otherwise."""
    root = Path(value).expanduser().resolve()
    if not root.exists():
        raise ServeError(f"workspaces folder not found: {root}")
    if not root.is_dir():
        raise ServeError(f"workspaces is not a directory: {root}")
    return root


def resolve_bind(value):
    """Return the bind address, auto-detecting it and refusing the wildcard."""
    if value is None:
        return detect_bind()
    bind = value.strip()
    if bind in WILDCARD_BINDS:
        raise ServeError("tailnet-only: refusing to bind the wildcard address")
    return bind


def main(argv=None):
    args = build_parser().parse_args(argv)
    started = time.monotonic()
    try:
        root = resolve_root(args.workspaces)
        bind = resolve_bind(args.bind)
        try:
            server = build_server(root, bind, args.port)
        except OSError as exc:
            raise ServeError(f"could not bind {bind}:{args.port}: {exc}") from exc
    except ServeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    port = server.server_address[1]
    projects = discover_projects(root)
    summary = build_summary(
        root, bind, port, projects, "check" if args.check else "serving", started
    )
    if args.json:
        print(json.dumps(summary), flush=True)
    else:
        print_human(summary)
        sys.stdout.flush()

    if args.check:
        server.server_close()
        return 0

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
