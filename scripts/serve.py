#!/usr/bin/env python3
"""Web app for recap workspaces, bound to the tailnet address only.

Usage:
    python scripts/serve.py --workspaces ~/media/projects
    python scripts/serve.py --workspaces ~/media/projects --inbox ~/media/inbox --check --json

``--workspaces`` is a folder whose direct subfolders are project workspaces. The
Projects page lists, in descending folder-name order, every direct child
directory that holds at least one of ``.run.json``, ``recap_720p.mp4``,
``recap_final.mp4``, ``shotlog.html``, ``explain.html`` or ``edit.json``, with a
running/failed/done status badge. Each project page has the Recap / Why? / Shots
tabs (recap player, ``explain.html``, ``shotlog.html``) and a Re-edit form;
``/upload`` takes clips and photos from a phone browser into the inbox and
starts ``recap.py`` in the background. Only the four finished artifacts are ever
served; proxies, ``edit.json``, ``.run.json``, ``source/`` symlinks and every
other file stay private. The address is a single host, never the wildcard, so
the server is reachable over the tailnet only. The tool writes inside a
workspace only ``.run.json`` and ``run.log``; the inbox receives uploads and is
never cleaned.
"""

import argparse
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

TOOL = "scripts/serve.py"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8091
DEFAULT_MUSIC_DIR = "~/media/music"
DEFAULT_INBOX = "~/media/inbox"
DEFAULT_MAX_UPLOAD_MB = 4096
DEFAULT_MAX_TOTAL_MB = 20480
UPLOAD_CHUNK = 65536
WILDCARD_BINDS = {"", "0.0.0.0", "::"}
FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
BRIEF_MAX_CHARS = 4000
UPLOAD_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm",
    ".jpg", ".jpeg", ".heic", ".heif", ".png",
}
LENGTH_CHOICES = {"auto": None, "30s": 30, "60s": 60, "90s": 90, "3min": 180}
ORDER_CHOICES = {"auto": None, "chronological": "chronological", "best": "score"}
AMBIENT_CHOICES = {"auto", "6", "8", "12", "16", "20"}
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
        "--inbox",
        default=DEFAULT_INBOX,
        help=f"inbox the uploads land in (default: {DEFAULT_INBOX})",
    )
    parser.add_argument(
        "--max-upload-mb",
        type=int,
        default=DEFAULT_MAX_UPLOAD_MB,
        help=f"per-file upload size cap in MB (default: {DEFAULT_MAX_UPLOAD_MB})",
    )
    parser.add_argument(
        "--max-total-mb",
        type=int,
        default=DEFAULT_MAX_TOTAL_MB,
        help=f"whole-request upload size cap in MB (default: {DEFAULT_MAX_TOTAL_MB})",
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
    """Return the project folder names under ``root``, most recently worked on first."""
    root = Path(root)
    folders = [child for child in root.iterdir() if child.is_dir() and is_project(child)]

    def last_activity(folder):
        times = [
            (folder / name).stat().st_mtime
            for name in DETECTED_ARTIFACTS
            if (folder / name).is_file()
        ]
        return max(times, default=0.0)

    folders.sort(key=lambda folder: (last_activity(folder), folder.name), reverse=True)
    return [folder.name for folder in folders]


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


THEME_CSS = """
:root{color-scheme:dark;
--water:#13202b;--deck:#1b2a37;--rope:#2b3d4d;--stone:#ece5d5;--haze:#9badbb;
--sea:#57b8c4;--keep:#a3cf7f;--left:#e2806f;--busy:#e8b54d;
--radius-s:6px;--radius-l:14px;--tap:44px}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--water);color:var(--stone);
font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--sea)}
a:focus-visible,button:focus-visible,select:focus-visible,input:focus-visible,
textarea:focus-visible,summary:focus-visible{outline:2px solid var(--sea);outline-offset:2px}
.wrap{max-width:760px;margin:0 auto;padding:0 16px 48px}
.bar{display:flex;align-items:center;gap:20px;padding:14px 0;margin-bottom:8px;
border-bottom:1px solid var(--rope)}
.mark{font-family:ui-serif,Georgia,"Times New Roman",serif;font-size:1.15rem;
color:var(--stone);text-decoration:none;margin-right:auto;letter-spacing:.01em}
.bar a.nav{color:var(--haze);text-decoration:none;min-height:var(--tap);
display:inline-flex;align-items:center}
.bar a.nav.on{color:var(--stone)}
h1{font-family:ui-serif,Georgia,"Times New Roman",serif;font-weight:500;
font-size:2rem;line-height:1.15;margin:20px 0 6px;overflow-wrap:anywhere}
.lede{color:var(--haze);margin:0 0 18px}
.num{font-variant-numeric:tabular-nums}
.projects{list-style:none;margin:8px 0 0;padding:0}
.projects li{border-top:1px solid var(--rope)}
.projects li:last-child{border-bottom:1px solid var(--rope)}
.projects a.row{display:grid;grid-template-columns:1fr auto;gap:2px 12px;
padding:14px 2px;text-decoration:none;color:inherit}
.projects .name{font-family:ui-serif,Georgia,serif;font-size:1.25rem}
.projects .facts{grid-column:1;color:var(--haze);font-size:.9rem}
.state{grid-row:1/span 2;grid-column:2;align-self:center;font-size:.85rem;
display:inline-flex;align-items:center;gap:6px}
.state::before{content:"";width:9px;height:9px;border-radius:50%;background:currentColor}
.badge-done{color:var(--keep)}.badge-running{color:var(--busy)}.badge-failed{color:var(--left)}
.badge-running::before{animation:pulse 1.6s ease-in-out infinite}
@keyframes pulse{50%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.badge-running::before{animation:none}}
.empty{color:var(--haze)}
.tabs{display:flex;gap:4px;padding:4px;margin:10px 0 18px;background:var(--deck);
border-radius:var(--radius-l);width:fit-content;max-width:100%}
.tabs a{min-height:40px;display:inline-flex;align-items:center;padding:0 18px;
border-radius:10px;color:var(--haze);text-decoration:none}
.tabs a.active{background:var(--stone);color:var(--water)}
video{display:block;width:100%;aspect-ratio:16/9;background:#000;border-radius:var(--radius-l)}
.strip{margin:14px 0 4px}
.strip-track{display:flex;gap:3px;height:56px}
.strip-track button{flex:var(--w) 1 0;min-width:10px;border:0;border-radius:var(--radius-s);
padding:0 6px;font:inherit;font-size:.75rem;color:var(--water);cursor:pointer;
background:var(--sea);overflow:hidden;white-space:nowrap;text-overflow:ellipsis;
text-align:left;opacity:.72;transition:opacity .15s}
.strip-track button.photo{background:repeating-linear-gradient(135deg,var(--stone) 0 6px,#d9d0bd 6px 12px)}
.strip-track button.now{opacity:1;box-shadow:inset 0 -4px 0 var(--water)}
.strip-legend{display:flex;gap:16px;margin-top:8px;color:var(--haze);font-size:.85rem}
.strip-legend span::before{content:"";display:inline-block;width:10px;height:10px;
border-radius:3px;margin-right:6px;vertical-align:-1px}
.strip-legend .k-clip::before{background:var(--sea)}
.strip-legend .k-photo::before{background:var(--stone)}
.download{display:inline-flex;align-items:center;min-height:var(--tap);margin-top:6px}
iframe{display:block;width:100%;height:78vh;border:0;border-radius:var(--radius-l);
background:var(--deck)}
details.reedit{margin-top:28px;border-top:1px solid var(--rope);padding-top:8px}
details.reedit summary{min-height:var(--tap);display:flex;align-items:center;cursor:pointer;
font-family:ui-serif,Georgia,serif;font-size:1.2rem}
form{display:grid;gap:14px;max-width:520px;margin-top:8px}
form label{display:grid;gap:6px;font-size:.95rem}
.hint{color:var(--haze);font-size:.85rem}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media (max-width:420px){.pair{grid-template-columns:1fr}}
input,select,textarea{width:100%;min-height:var(--tap);background:var(--deck);color:var(--stone);
border:1px solid var(--rope);border-radius:10px;padding:8px 12px;font:inherit}
input[type=file]{padding:10px}
textarea{resize:vertical}
button.go{min-height:48px;border:0;border-radius:12px;background:var(--sea);color:var(--water);
font:inherit;font-weight:600;padding:0 26px;cursor:pointer;justify-self:start}
"""

STRIP_SCRIPT = """
(function(){var v=document.querySelector('video');var b=[].slice.call(document.querySelectorAll('.strip-track button'));
if(!v||!b.length)return;b.forEach(function(x){x.addEventListener('click',function(){v.currentTime=parseFloat(x.dataset.t);v.play();});});
v.addEventListener('timeupdate',function(){var t=v.currentTime;b.forEach(function(x){var s=parseFloat(x.dataset.t),e=parseFloat(x.dataset.e);x.classList.toggle('now',t>=s&&t<e);});});})();
"""


def render_page(title, body, active=None, script=""):
    """Return one full HTML page with the top bar and the shared theme."""
    nav = []
    for key, href, label in (("projects", "/", "Projects"), ("upload", "/upload", "New recap")):
        on = " on" if key == active else ""
        nav.append(f'<a class="nav{on}" href="{href}">{label}</a>')
    tail = f"<script>{script}</script>" if script else ""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html_escape(title)}</title>"
        f'<style>{THEME_CSS}</style></head><body><div class="wrap">'
        f'<header class="bar"><a class="mark" href="/">AI Director</a>{"".join(nav)}</header>'
        f"{body}</div>{tail}"
        "</body></html>\n"
    )


def edit_summary(directory):
    """Read edit.json and return the timeline blocks and totals, or None."""
    path = Path(directory) / "edit.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    slots = document.get("video") if isinstance(document, dict) else None
    if not isinstance(slots, list):
        return None
    blocks = []
    start = 0.0
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        try:
            if slot.get("kind") == "photo" or slot.get("photo"):
                length = float(slot.get("duration_s") or 0.0)
                kind = "photo"
            else:
                length = float(slot.get("out_s", 0.0)) - float(slot.get("in_s", 0.0))
                kind = "clip"
        except (TypeError, ValueError):
            continue
        if length <= 0:
            continue
        label = slot.get("shot_key") or slot.get("photo") or slot.get("clip") or slot.get("id") or ""
        blocks.append({"start": start, "end": start + length, "kind": kind, "label": str(label)})
        start += length
    if not blocks:
        return None
    photos = sum(1 for block in blocks if block["kind"] == "photo")
    return {"blocks": blocks, "total": start, "shots": len(blocks), "photos": photos}


def describe_summary(summary):
    """One plain line about an edit: length, shots, photos."""
    if not summary:
        return "No edit yet"
    parts = [f"{summary['total']:.0f} s", f"{summary['shots']} shots"]
    if summary["photos"]:
        parts.append(f"{summary['photos']} photo" + ("s" if summary["photos"] != 1 else ""))
    return ", ".join(parts)


def render_index(entries):
    """Return the Projects page: one row per project with its state and edit facts."""
    rows = []
    for entry in entries:
        name = html_escape(entry["name"])
        status = html_escape(entry["status"])
        facts = html_escape(describe_summary(entry.get("summary")))
        rows.append(
            f'<li><a class="row" href="/{name}/">'
            f'<span class="name">{name}</span>'
            f'<span class="state badge-{status}">{status}</span>'
            f'<span class="facts num">{facts}</span></a></li>'
        )
    if rows:
        body = f'<ul class="projects">{"".join(rows)}</ul>'
    else:
        body = ('<p class="empty">No projects yet. Start one from '
                '<a href="/upload">New recap</a>.</p>')
    return render_page(
        "Projects",
        f'<h1>Projects</h1><p class="lede">Every folder you sent, newest first.</p>{body}',
        active="projects",
    )


def render_strip(summary):
    """The edit as a proportional strip under the player; tap a block to jump there."""
    if not summary:
        return ""
    buttons = []
    for block in summary["blocks"]:
        width = max(0.4, block["end"] - block["start"])
        label = html_escape(block["label"])
        buttons.append(
            f'<button type="button" class="{block["kind"]}" style="--w:{width:.2f}" '
            f'data-t="{block["start"]:.2f}" data-e="{block["end"]:.2f}" '
            f'title="{label} at {block["start"]:.1f} s">{label}</button>'
        )
    return (
        '<div class="strip" aria-label="Edit timeline">'
        f'<div class="strip-track">{"".join(buttons)}</div>'
        '<div class="strip-legend"><span class="k-clip">clip</span>'
        '<span class="k-photo">photo</span>'
        f'<span class="num">{html_escape(describe_summary(summary))}</span></div></div>'
    )


def render_recap_panel(directory):
    """Return the Recap tab panel: the player with the edit strip, or a placeholder line."""
    parts = []
    if (Path(directory) / "recap_720p.mp4").is_file():
        parts.append('<video controls preload="metadata" src="recap_720p.mp4"></video>')
        parts.append(render_strip(edit_summary(directory)))
    else:
        parts.append('<p class="empty">No recap yet. It appears here when the render finishes.</p>')
    if (Path(directory) / "recap_final.mp4").is_file():
        parts.append('<a class="download" href="recap_final.mp4">Download the full-quality recap_final.mp4</a>')
    return "\n".join(parts)


def render_iframe_panel(directory, artifact, empty_text):
    """Return a tab panel embedding ``artifact``, or a placeholder line."""
    if (Path(directory) / artifact).is_file():
        return f'<iframe src="{artifact}" title="{html_escape(artifact)}"></iframe>'
    return f'<p class="empty">{html_escape(empty_text)}</p>'


def render_project(name, directory, tab, music_tracks=()):
    """Return the project page with the tabs and the Re-edit form."""
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
            "No explanation for this edit yet. Run scripts/explain.py, or re-edit it once to create one.",
        ),
        "shots": render_iframe_panel(
            directory,
            "shotlog.html",
            "No shot list yet. It appears when the analysis finishes.",
        ),
    }
    status = project_status(directory)
    lede = f'<span class="state badge-{status}">{status}</span>'
    body = (
        f"<h1>{html_escape(name)}</h1>"
        f'<p class="lede">{lede}</p>'
        f'<nav class="tabs">{"".join(tab_links)}</nav>'
        f'<section class="panel">{panels[tab]}</section>'
        f"{render_reedit(name, music_tracks)}"
    )
    script = STRIP_SCRIPT if tab == "recap" else ""
    return render_page(name, body, active="projects", script=script)


def render_select(name, options, default=None):
    """Return one ``<select>`` with the given (value, label) options."""
    rendered = "".join(
        f'<option value="{html_escape(value)}"'
        f'{" selected" if default == value else ""}>{html_escape(label)}</option>'
        for value, label in options
    )
    return f'<select name="{html_escape(name)}">{rendered}</select>'


def option_fields(music_tracks):
    """The shared option selects, every one defaulting to Auto."""
    music_options = [("auto", "Auto"), ("none", "none")]
    music_options.extend((track, track) for track in music_tracks)
    return [
        (
            "Length",
            render_select(
                "length",
                [("auto", "Auto"), ("30s", "30s"), ("60s", "60s"), ("90s", "90s"), ("3min", "3min")],
                default="auto",
            ),
        ),
        (
            "Order",
            render_select(
                "order",
                [("auto", "Auto"), ("chronological", "chronological"), ("best", "best")],
                default="auto",
            ),
        ),
        (
            "Clean the sound",
            render_select("audio_clean", [("auto", "Auto"), ("yes", "yes"), ("no", "no")], default="auto"),
        ),
        ("Music", render_select("music", music_options, default="auto")),
        (
            "Ambient under the music (dB)",
            render_select(
                "ambient_under_music_db",
                [("auto", "Auto"), ("6", "6"), ("8", "8"), ("12", "12"), ("16", "16"), ("20", "20")],
                default="auto",
            ),
        ),
    ]


def render_form_rows(fields):
    rows = [f"<label>{html_escape(label)}{control}</label>" for label, control in fields]
    paired = []
    index = 0
    while index < len(rows):
        if index + 1 < len(rows) and fields[index][1].startswith("<select") and fields[index + 1][1].startswith("<select"):
            paired.append(f'<div class="pair">{rows[index]}{rows[index + 1]}</div>')
            index += 2
        else:
            paired.append(rows[index])
            index += 1
    return "\n".join(paired)


AUTO_HINT = '<p class="hint">Leave anything on Auto and the director decides from the brief and the footage.</p>'


def render_upload(music_tracks):
    """Return the Upload page with the form wired to POST /upload."""
    fields = [
        ("Clips and photos", '<input type="file" name="files" multiple accept="video/*,image/*">'),
        ("Project name", '<input type="text" name="folder" placeholder="kotor-day-2" autocomplete="off">'),
        ("Brief", '<textarea name="brief" rows="4" '
                  'placeholder="About 60 seconds, chronological, calm start, energy at the end"></textarea>'),
    ]
    fields.extend(option_fields(music_tracks))
    body = (
        "<h1>New recap</h1>"
        '<p class="lede">Pick the clips and photos of one day, say what you want, and start. '
        "The files go to the inbox and the edit runs on the server; the project shows up under Projects.</p>"
        '<form method="post" action="/upload" enctype="multipart/form-data">'
        f"{render_form_rows(fields)}"
        f"{AUTO_HINT}"
        '<button class="go" type="submit" name="submit" value="start">Upload and start</button>'
        "</form>"
    )
    return render_page("New recap", body, active="upload")


def render_reedit(name, music_tracks):
    """Return the Re-edit form with the same options plus a feedback box."""
    fields = list(option_fields(music_tracks))
    fields.append(("What should change?", '<textarea name="feedback" rows="3" '
                   'placeholder="drop 0071/s2, tunnel longer"></textarea>'))
    return (
        "<details class=\"reedit\"><summary>Re-edit</summary>"
        f'<form method="post" action="/{html_escape(name)}/re-edit" '
        'enctype="multipart/form-data">'
        f"{render_form_rows(fields)}"
        f"{AUTO_HINT}"
        '<button class="go" type="submit" name="submit" value="reedit">Re-edit</button>'
        "</form></details>"
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


class UploadError(RuntimeError):
    """Raised when an upload request is unacceptable."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class MultipartReader:
    """Stream one multipart/form-data body into fields and files on disk."""

    def __init__(self, stream, boundary, max_total_bytes, max_file_bytes, content_length=None):
        self.stream = stream
        self.delimiter = b"--" + boundary
        self.max_total_bytes = max_total_bytes
        self.max_file_bytes = max_file_bytes
        self.content_length = content_length
        self.buffer = b""
        self.consumed = 0
        self.finished = False

    def _read_more(self):
        if self.content_length is not None and self.consumed >= self.content_length:
            return False
        want = UPLOAD_CHUNK
        if self.content_length is not None:
            want = min(want, self.content_length - self.consumed)
        chunk = self.stream.read1(want) if hasattr(self.stream, "read1") else self.stream.read(want)
        if not chunk:
            return False
        self.consumed += len(chunk)
        if self.consumed > self.max_total_bytes:
            raise UploadError(413, "request body too large")
        self.buffer += chunk
        return True

    def _find(self, marker):
        return self.buffer.find(marker)

    def _consume_headers(self):
        while True:
            end = self.buffer.find(b"\r\n\r\n")
            if end >= 0:
                raw = self.buffer[:end]
                self.buffer = self.buffer[end + 4:]
                return raw
            if not self._read_more():
                raise UploadError(400, "truncated multipart headers")

    def _read_part(self, writer=None, limit=None):
        marker = b"\r\n" + self.delimiter
        written = 0
        while True:
            at = self._find(marker)
            if at >= 0:
                payload, self.buffer = self.buffer[:at], self.buffer[at:]
                if writer is not None and payload:
                    writer(payload)
                    written += len(payload)
                return written
            keep = len(marker) - 1
            payload = self.buffer[:-keep] if len(self.buffer) > keep else b""
            if payload:
                self.buffer = self.buffer[-keep:]
                if writer is not None:
                    writer(payload)
                written += len(payload)
                if limit is not None and written > limit:
                    raise UploadError(413, "file too large")
            if not self._read_more():
                raise UploadError(400, "truncated multipart part")

    def _next_part(self):
        """Consume up to and including the CRLF after a boundary; False at the end."""
        while True:
            if self.buffer.startswith(b"\r\n"):
                self.buffer = self.buffer[2:]
                continue
            if self.buffer.startswith(self.delimiter):
                rest = self.buffer[len(self.delimiter):]
                if rest.startswith(b"--"):
                    self.buffer = rest[2:]
                    self.finished = True
                    return False
                if rest.startswith(b"\r\n"):
                    self.buffer = rest[2:]
                    return True
                if not self._read_more():
                    raise UploadError(400, "malformed multipart boundary")
                continue
            if not self._read_more():
                self.finished = True
                return False

    def parts(self):
        """Yield ``(name, filename, data)`` per part; file bodies are read by the caller."""
        while self._next_part():
            headers_raw = self._consume_headers()
            name, filename = parse_part_headers(headers_raw)
            if filename is None:
                chunks = []
                self._read_part(writer=chunks.append, limit=BRIEF_MAX_CHARS * 4)
                yield name, None, b"".join(chunks)
            else:
                yield name, filename, None


def parse_part_headers(raw):
    """Return ``(name, filename)`` from one part's header block."""
    name = None
    filename = None
    for line in raw.split(b"\r\n"):
        if not line.lower().startswith(b"content-disposition:"):
            continue
        text = line.decode("utf-8", "replace")
        for key, value in re.findall(r'(\w+)="([^"]*)"', text):
            if key == "name":
                name = value
            elif key == "filename":
                filename = value
    return name, filename


def stream_to_file(reader, destination, limit):
    """Write one multipart file part to ``destination`` atomically; return its size."""
    temporary = destination.with_name(destination.name + ".part")

    def writer(payload):
        handle.write(payload)

    written = 0
    try:
        with temporary.open("wb") as handle:
            written = reader._read_part(writer=writer, limit=limit)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return written


def discard_staging(staging):
    """Remove a staging folder and anything left in it."""
    try:
        for child in staging.iterdir():
            child.unlink()
        staging.rmdir()
    except OSError:
        pass


def parse_upload(reader, inbox):
    """Stream one multipart body into a staging folder under the inbox.

    Returns ``(fields, staged, staging)`` where ``staged`` maps each base name
    to its staged path. Field order does not matter: the caller moves the files
    into the project folder once the folder name is known.
    """
    staging = Path(inbox) / f".upload-{os.getpid()}-{time.time_ns():x}"
    staging.mkdir(parents=True, exist_ok=False)
    fields = {}
    staged = {}
    try:
        for name, filename, data in reader.parts():
            if filename is None:
                fields[name] = (data or b"").decode("utf-8", "replace")
                continue
            base = Path(filename.replace("\\", "/")).name
            if not base or Path(base).suffix.lower() not in UPLOAD_EXTENSIONS:
                raise UploadError(400, f"unsupported file name: {base or filename}")
            if base in staged:
                raise UploadError(400, f"duplicate file name: {base}")
            stream_to_file(reader, staging / base, reader.max_file_bytes)
            staged[base] = staging / base
    except UploadError:
        discard_staging(staging)
        raise
    return fields, staged, staging


def parse_fields_only(reader):
    """Return the form fields of a multipart body that must carry no files."""
    fields = {}
    for name, filename, data in reader.parts():
        if filename is None:
            fields[name] = (data or b"").decode("utf-8", "replace")
            continue
        raise UploadError(400, "this form takes no files")
    return fields


def move_into_inbox(staged, inbox, folder):
    """Move the staged files to ``inbox/folder``; refuse to overwrite anything."""
    destination_dir = inbox / folder
    destination_dir.mkdir(parents=True, exist_ok=True)
    resolved_dir = destination_dir.resolve()
    if inbox.resolve() not in resolved_dir.parents:
        raise UploadError(400, "refused: destination outside the inbox")
    moved = []
    for base in sorted(staged):
        target = destination_dir / base
        if target.exists():
            raise UploadError(409, f"already exists: {base}")
        os.replace(staged[base], target)
        moved.append(base)
    return moved


def write_text_atomic(path, text):
    """Write a small text file atomically."""
    path = Path(path)
    temporary = path.with_name(path.name + ".part")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_brief_file(source):
    """The brief stored next to the uploaded files, or an empty string."""
    try:
        return (Path(source) / "brief.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def build_recap_command(python, source, workspace, brief, fields, music_dir):
    """The recap.py argv for one upload or re-edit."""
    command = [
        python,
        str(REPO_ROOT / "scripts" / "recap.py"),
        "--source",
        str(source),
        "--workspace",
        str(workspace),
        "--brief",
        brief,
        "--json",
    ]
    length = LENGTH_CHOICES.get(fields.get("length") or "auto")
    if length:
        command += ["--target-duration-s", str(length)]
    order = ORDER_CHOICES.get(fields.get("order") or "auto")
    if order:
        command += ["--order", order]
    if (fields.get("audio_clean") or "auto") == "no":
        command.append("--no-audio-clean")
    music = fields.get("music") or "auto"
    if music == "auto":
        command += ["--music", "auto"]
    elif music and music != "none":
        command += ["--music", str(Path(music_dir).expanduser().resolve() / Path(music).name)]
    ambient = fields.get("ambient_under_music_db") or "auto"
    if ambient in AMBIENT_CHOICES and ambient != "auto":
        command += ["--ambient-under-music-db", ambient]
    feedback = (fields.get("feedback") or "").strip()
    if feedback:
        command += ["--feedback", feedback[:BRIEF_MAX_CHARS]]
    return command


def start_recap(command, workspace, source, options):
    """Spawn the recap in the background, record ``.run.json`` and return the pid."""
    workspace.mkdir(parents=True, exist_ok=True)
    with (workspace / "run.log").open("ab") as log:
        handle = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=str(REPO_ROOT),
        )
    record = {
        "pid": handle.pid,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "label": "recap",
        "source": str(source),
        "options": options,
        "command": command,
    }
    (workspace / ".run.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return handle.pid


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
            self.handle_upload()
            return
        if len(segments) == 2 and segments[1] == "re-edit":
            self.handle_reedit(segments[0])
            return
        self._method_not_allowed()

    def _boundary(self):
        content_type = self.headers.get("Content-Type") or ""
        match = re.search(r'boundary="?([^";]+)"?', content_type)
        if not match or "multipart/form-data" not in content_type:
            return None
        return match.group(1).encode("utf-8")

    def _reader(self):
        boundary = self._boundary()
        if boundary is None:
            raise UploadError(400, "expected multipart/form-data")
        length = int(self.headers.get("Content-Length") or 0)
        if length > self.server.max_total_bytes:
            raise UploadError(413, "request body too large")
        return MultipartReader(
            self.rfile,
            boundary,
            self.server.max_total_bytes,
            self.server.max_file_bytes,
            content_length=length,
        )

    def handle_upload(self):
        """Store the uploaded files in the inbox and start the recap."""
        staging = None
        try:
            reader = self._reader()
            fields, staged, staging = parse_upload(reader, self.server.inbox)
            if not staged:
                raise UploadError(400, "no files uploaded")
            folder = (fields.get("folder") or "").strip()
            if not FOLDER_RE.match(folder):
                raise UploadError(400, "invalid folder name")
            brief = (fields.get("brief") or "").strip()
            if not brief:
                raise UploadError(400, "brief is required")
            if len(brief) > BRIEF_MAX_CHARS:
                raise UploadError(400, "brief too long")
            music = fields.get("music") or "auto"
            if music not in ("auto", "none") and not (
                (Path(self.server.music_dir).expanduser() / Path(music).name).is_file()
                and Path(music).suffix.lower() in MUSIC_EXTENSIONS
            ):
                raise UploadError(400, "unknown music track")
            source = self.server.inbox / folder
            if project_status(self.server.root / folder) == "running":
                raise UploadError(409, "already running")
            move_into_inbox(staged, self.server.inbox, folder)
            write_text_atomic(source / "brief.txt", brief + "\n")
        except UploadError as exc:
            if staging is not None:
                discard_staging(staging)
            self.send_error_text(exc.status, exc.message)
            return
        finally:
            if staging is not None and staging.exists():
                discard_staging(staging)
        self._start_for(fields, source, folder)

    def handle_reedit(self, project):
        """Re-run only the edit and the render with the form options and feedback."""
        workspace = self.server.root / project
        if project_status(workspace) == "running":
            self.send_error_text(409, "already running")
            return
        marker = workspace / ".run.json"
        try:
            if not marker.is_file():
                raise UploadError(409, "no recorded source for this project")
            data = json.loads(marker.read_text(encoding="utf-8"))
            source = Path(data.get("source") or "")
            if not source.is_dir():
                raise UploadError(409, "recorded source is gone")
            fields = parse_fields_only(self._reader())
        except UploadError as exc:
            self.send_error_text(exc.status, exc.message)
            return
        except (OSError, ValueError):
            self.send_error_text(409, "unreadable .run.json")
            return
        self._start_for(fields, source, project, reedit=True)

    def _start_for(self, fields, source, folder, reedit=False):
        workspace = self.server.root / folder
        if project_status(workspace) == "running":
            self.send_error_text(409, "already running")
            return
        brief = (fields.get("brief") or "").strip() or read_brief_file(source)
        if not brief:
            self.send_error_text(400, "brief is required")
            return
        command = build_recap_command(
            sys.executable, source, workspace, brief, fields, self.server.music_dir
        )
        try:
            start_recap(command, workspace, source, fields)
        except OSError as exc:
            self.send_error_text(500, f"could not start the recap: {exc}")
            return
        self.send_redirect(f"/{folder}/")

    def send_error_text(self, status, message):
        body = (message + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

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
                    "summary": edit_summary(directory),
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
            self.send_html(
                render_project(
                    segments[0], directory, parse_tab(self.path), list_music_tracks(self.server.music_dir)
                ),
                send_body,
            )
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
    """Threading HTTP server that knows the workspace root, inbox and limits."""

    daemon_threads = True

    def __init__(self, address, root, music_dir, inbox, max_file_bytes, max_total_bytes):
        self.root = Path(root)
        self.music_dir = music_dir
        self.inbox = inbox
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        super().__init__(address, WorkspaceHandler)


def build_server(root, bind, port, music_dir=DEFAULT_MUSIC_DIR, inbox=DEFAULT_INBOX,
                 max_file_bytes=None, max_total_bytes=None):
    """Bind ``root`` at ``bind:port`` and return the ready server."""
    return WorkspaceServer(
        (bind, port),
        root,
        music_dir,
        Path(inbox).expanduser().resolve(),
        max_file_bytes if max_file_bytes is not None else DEFAULT_MAX_UPLOAD_MB * 1024 * 1024,
        max_total_bytes if max_total_bytes is not None else DEFAULT_MAX_TOTAL_MB * 1024 * 1024,
    )


def build_summary(root, bind, port, projects, status, started, music_dir=DEFAULT_MUSIC_DIR,
                  inbox=DEFAULT_INBOX):
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
        "inbox": str(Path(inbox).expanduser().resolve()),
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
        inbox = Path(args.inbox).expanduser().resolve()
        try:
            server = build_server(
                root,
                bind,
                args.port,
                args.music_dir,
                inbox,
                args.max_upload_mb * 1024 * 1024,
                args.max_total_mb * 1024 * 1024,
            )
        except OSError as exc:
            raise ServeError(f"could not bind {bind}:{args.port}: {exc}") from exc
    except ServeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    port = server.server_address[1]
    projects = discover_projects(root)
    summary = build_summary(
        root, bind, port, projects, "check" if args.check else "serving", started,
        args.music_dir, inbox,
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
