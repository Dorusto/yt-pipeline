#!/usr/bin/env python3
"""Read-only web app for recap workspaces, bound to the tailnet address only.

Usage:
    python scripts/serve.py --workspaces ~/media/projects
    python scripts/serve.py --workspaces ~/media/projects --check --json

``--workspaces`` is a folder whose direct subfolders are project workspaces. The
Projects page lists, in descending folder-name order, every direct child
directory that holds at least one of ``.run.json``, ``recap_720p.mp4``,
``recap_final.mp4``, ``shotlog.html``, ``explain.html`` or ``edit.json``, with a
running/failed/done status badge. Each project page has the Recap / Why? / Shots
tabs (recap player, ``explain.html``, ``shotlog.html``) and ``/upload`` shows the
upload form whose POST handler the next task wires. Only the four finished
artifacts are ever served; proxies, ``edit.json``, ``.run.json``, ``source/``
symlinks and every other file stay private. The address is a single host, never
the wildcard, so the server is reachable over the tailnet only. The tool only
reads; it never writes inside the workspaces folder.
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
DEFAULT_MUSIC_DIR = "~/media/music"
WILDCARD_BINDS = {"", "0.0.0.0", "::"}
DETECTED_ARTIFACTS = (
    ".run.json",
    "recap_720p.mp4",
    "recap_final.mp4",
    "shotlog.html",
    "explain.html",
    "edit.json",
)
SERVED_FILES = ("recap_720p.mp4", "recap_final.mp4", "shotlog.html", "explain.html")
CONTENT_TYPES = {
    "recap_720p.mp4": "video/mp4",
    "recap_final.mp4": "video/mp4",
    "shotlog.html": "text/html; charset=utf-8",
    "explain.html": "text/html; charset=utf-8",
}
ARTIFACT_LABELS = (
    ("recap_720p.mp4", "recap"),
    ("recap_final.mp4", "final"),
    ("explain.html", "why"),
    ("shotlog.html", "shots"),
)
TABS = (("recap", "Recap"), ("why", "Why?"), ("shots", "Shots"))
TAB_KEYS = tuple(key for key, _ in TABS)
MUSIC_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}


class ServeError(RuntimeError):
    """Raised when the server cannot start or the request is unacceptable."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="serve.py",
        usage=(
            "serve.py [-h] --workspaces WORKSPACES [--bind ADDR] [--port PORT]"
            " [--music-dir DIR] [--check] [--json]"
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
        "--music-dir",
        default=DEFAULT_MUSIC_DIR,
        help=f"folder of music tracks offered on the Upload page (default: {DEFAULT_MUSIC_DIR})",
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


def is_project(directory):
    """Return True when the folder holds at least one project marker file."""
    return any((Path(directory) / name).is_file() for name in DETECTED_ARTIFACTS)


def discover_projects(root):
    """Return the project folder names under ``root``, newest first."""
    root = Path(root)
    names = [child.name for child in root.iterdir() if child.is_dir() and is_project(child)]
    return sorted(names, reverse=True)


def existing_artifacts(directory):
    """Return the served artifact names present in ``directory``, in fixed order."""
    return [name for name in SERVED_FILES if (Path(directory) / name).is_file()]


def read_run_pid(directory):
    """Return the pid from ``.run.json``, or None when it is absent or malformed."""
    path = Path(directory) / ".run.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int):
        return None
    return pid


def project_status(directory):
    """Return running, failed or done for one project workspace."""
    directory = Path(directory)
    pid = read_run_pid(directory)
    if pid is not None:
        import psutil

        if psutil.pid_exists(pid):
            return "running"
        if not (directory / "recap_720p.mp4").is_file():
            return "failed"
    return "done"


def list_music_tracks(music_dir):
    """Return the audio file names offered on the Upload page, sorted by name."""
    directory = Path(music_dir).expanduser()
    try:
        names = [
            child.name
            for child in directory.iterdir()
            if child.is_file() and child.suffix.lower() in MUSIC_EXTENSIONS
        ]
    except OSError:
        return []
    return sorted(names)


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
    ".menu{display:flex;gap:1.2rem;padding:.4rem 0 .8rem;border-bottom:1px solid #333}"
    ".menu a{text-decoration:none}"
    ".badge{font-size:.8rem;padding:.1rem .5rem;border-radius:8px;border:1px solid #444}"
    ".badge-done{color:#8f8}.badge-running{color:#fc6}.badge-failed{color:#f88}"
    ".marks{color:#aaa;font-size:.9rem}.marks a{margin-right:.6rem}"
    ".tabs{display:flex;gap:.5rem;margin:.6rem 0}"
    ".tabs a{padding:.25rem .7rem;border:1px solid #444;border-radius:6px;text-decoration:none}"
    ".tabs a.active{background:#6cf;color:#111;border-color:#6cf}"
    "iframe{width:100%;min-height:70vh;border:1px solid #333;background:#111}"
    "form{max-width:480px}form label{display:block;margin:.7rem 0}"
    "input,select,textarea{width:100%;box-sizing:border-box;background:#1b1b1b;"
    "color:#eee;border:1px solid #444;border-radius:4px;padding:.35rem}"
    "button{background:#6cf;color:#111;border:0;border-radius:4px;padding:.5rem 1.2rem}"
    ".empty{color:#aaa}"
)


def render_page(title, body):
    """Return one full HTML page with the top menu and the shared style."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html_escape(title)}</title>"
        f"<style>{PAGE_STYLE}</style></head><body>"
        '<nav class="menu"><a href="/">Projects</a><a href="/upload">Upload</a></nav>'
        f"{body}"
        "</body></html>\n"
    )


def render_index(entries):
    """Return the Projects page: one row per project with status and artifacts."""
    rows = []
    for entry in entries:
        name = html_escape(entry["name"])
        status = entry["status"]
        marks = " ".join(
            f'<a href="/{name}/{html_escape(artifact)}">{html_escape(label)}</a>'
            for artifact, label in ARTIFACT_LABELS
            if artifact in entry["artifacts"]
        )
        rows.append(
            f'<li><a href="/{name}/">{name}</a> '
            f'<span class="badge badge-{html_escape(status)}">{html_escape(status)}</span> '
            f'<span class="marks">{marks}</span></li>'
        )
    body = "\n".join(rows) if rows else "<li>no projects</li>"
    return render_page("Recaps", f"<h1>Recaps</h1><ul>{body}</ul>")


def render_recap_panel(directory):
    """Return the Recap tab panel: the player, or a placeholder line."""
    parts = []
    if (Path(directory) / "recap_720p.mp4").is_file():
        parts.append('<video controls preload="metadata" src="recap_720p.mp4"></video>')
    else:
        parts.append('<p class="empty">No recap yet.</p>')
    if (Path(directory) / "recap_final.mp4").is_file():
        parts.append('<p><a href="recap_final.mp4">recap_final.mp4</a></p>')
    return "\n".join(parts)


def render_iframe_panel(directory, artifact, empty_text):
    """Return a tab panel embedding ``artifact``, or a placeholder line."""
    if (Path(directory) / artifact).is_file():
        return f'<iframe src="{artifact}" title="{html_escape(artifact)}"></iframe>'
    return f'<p class="empty">{html_escape(empty_text)}</p>'


def render_project(name, directory, tab):
    """Return the project page with the Recap / Why? / Shots tabs."""
    directory = Path(directory)
    tab_links = []
    for key, label in TABS:
        active = ' class="active"' if key == tab else ""
        tab_links.append(
            f'<a href="/{html_escape(name)}/?tab={key}"{active}>{html_escape(label)}</a>'
        )
    panels = {
        "recap": render_recap_panel(directory),
        "why": render_iframe_panel(
            directory,
            "explain.html",
            "The explanation has not been generated yet (scripts/explain.py).",
        ),
        "shots": render_iframe_panel(
            directory,
            "shotlog.html",
            "The shotlog has not been generated yet.",
        ),
    }
    body = (
        f"<h1>{html_escape(name)}</h1>"
        f'<nav class="tabs">{"".join(tab_links)}</nav>'
        f'<section class="panel">{panels[tab]}</section>'
    )
    return render_page(name, body)


def render_select(name, options):
    """Return one ``<select>`` with the given (value, label) options."""
    rendered = "".join(
        f'<option value="{html_escape(value)}">{html_escape(label)}</option>'
        for value, label in options
    )
    return f'<select name="{html_escape(name)}">{rendered}</select>'


def render_upload(music_tracks):
    """Return the Upload page with the form the next task wires to POST."""
    music_options = [("auto", "Auto"), ("none", "none")]
    music_options.extend((track, track) for track in music_tracks)
    fields = [
        ("Files", '<input type="file" name="files" multiple accept="video/*,image/*">'),
        ("Folder", '<input type="text" name="folder">'),
        ("Brief", '<textarea name="brief" rows="4"></textarea>'),
        (
            "Length",
            render_select(
                "length",
                [("auto", "Auto"), ("30s", "30s"), ("60s", "60s"), ("90s", "90s"), ("3min", "3min")],
            ),
        ),
        (
            "Order",
            render_select(
                "order",
                [("auto", "Auto"), ("chronological", "chronological"), ("best", "best")],
            ),
        ),
        (
            "Audio clean",
            render_select("audio_clean", [("auto", "Auto"), ("yes", "yes"), ("no", "no")]),
        ),
        ("Music", render_select("music", music_options)),
        (
            "Ambient under music (dB)",
            render_select(
                "ambient_under_music_db",
                [("auto", "Auto"), ("6", "6"), ("8", "8"), ("12", "12"), ("16", "16"), ("20", "20")],
            ),
        ),
    ]
    rows = "\n".join(f"<label>{html_escape(label)}{control}</label>" for label, control in fields)
    body = (
        "<h1>Upload</h1>"
        '<p class="empty">Files land in the inbox and the recap starts in the background.</p>'
        '<form method="post" action="/upload" enctype="multipart/form-data">'
        f"{rows}"
        '<button type="submit" name="submit" value="start">Start</button>'
        "</form>"
    )
    return render_page("Upload", body)


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


def parse_tab(raw_path):
    """Return the requested tab key from the query string, falling back to recap."""
    values = urllib.parse.parse_qs(urllib.parse.urlsplit(raw_path).query).get("tab", [])
    tab = values[0] if values else "recap"
    return tab if tab in TAB_KEYS else "recap"


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
    """Serve the allowlisted pages and the four finished artifacts."""

    server_version = "yt-serve/2.0"

    def do_GET(self):
        self.handle_request(send_body=True)

    def do_HEAD(self):
        self.handle_request(send_body=False)

    def do_POST(self):
        segments = parse_path(self.path)
        if segments == ["upload"]:
            self.send_not_implemented()
        else:
            self._method_not_allowed()

    def _method_not_allowed(self):
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_PUT = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_OPTIONS = _method_not_allowed

    def collect_entries(self):
        """Return one list entry per project with its status and artifacts."""
        root = self.server.root
        entries = []
        for name in discover_projects(root):
            directory = root / name
            entries.append(
                {
                    "name": name,
                    "status": project_status(directory),
                    "artifacts": existing_artifacts(directory),
                }
            )
        return entries

    def handle_request(self, send_body):
        """Route one request against the allowlist and write the response."""
        root = self.server.root
        segments = parse_path(self.path)
        if segments is None:
            self.send_not_found()
            return
        if not segments:
            self.send_html(render_index(self.collect_entries()), send_body)
            return
        if segments == ["upload"]:
            self.send_html(render_upload(list_music_tracks(self.server.music_dir)), send_body)
            return
        if len(segments) == 1:
            directory = root / segments[0]
            if not directory.is_dir() or not is_project(directory):
                self.send_not_found()
                return
            self.send_html(render_project(segments[0], directory, parse_tab(self.path)), send_body)
            return
        if len(segments) == 2:
            artifact = segments[1]
            if artifact not in SERVED_FILES:
                self.send_not_found()
                return
            directory = root / segments[0]
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

    def send_not_implemented(self):
        body = b"Upload is not wired yet: the next task connects the POST handler.\n"
        self.send_response(501)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
    """Threading HTTP server that knows the workspace root and music folder."""

    daemon_threads = True

    def __init__(self, address, root, music_dir):
        self.root = Path(root)
        self.music_dir = music_dir
        super().__init__(address, WorkspaceHandler)


def build_server(root, bind, port, music_dir=DEFAULT_MUSIC_DIR):
    """Bind ``root`` at ``bind:port`` and return the ready server."""
    return WorkspaceServer((bind, port), root, music_dir)


def build_summary(root, bind, port, projects, status, started, music_dir=DEFAULT_MUSIC_DIR):
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
                "status": project_status(directory),
                "preview": f"{base}/{name}/recap_720p.mp4"
                if "recap_720p.mp4" in artifacts
                else None,
                "final": f"{base}/{name}/recap_final.mp4"
                if "recap_final.mp4" in artifacts
                else None,
                "shotlog": f"{base}/{name}/shotlog.html"
                if "shotlog.html" in artifacts
                else None,
                "why": f"{base}/{name}/explain.html" if "explain.html" in artifacts else None,
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
        "music_tracks": len(list_music_tracks(music_dir)),
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
            for key, label in (
                ("preview", "recap"),
                ("final", "final"),
                ("why", "why"),
                ("shotlog", "shotlog"),
            )
            if project[key]
        ]
        print(f"  {project['name']}  {project['status']}  {' '.join(marks)}")


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
            server = build_server(root, bind, args.port, args.music_dir)
        except OSError as exc:
            raise ServeError(f"could not bind {bind}:{args.port}: {exc}") from exc
    except ServeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    port = server.server_address[1]
    projects = discover_projects(root)
    summary = build_summary(
        root, bind, port, projects, "check" if args.check else "serving", started, args.music_dir
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
