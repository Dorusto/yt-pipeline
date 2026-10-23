#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Web app for recap workspaces, bound to the tailnet address only.

Usage:
    python scripts/serve.py --workspaces <workspaces>
    python scripts/serve.py --workspaces <workspaces> --inbox <inbox> --check --json

``--workspaces`` is a folder whose direct subfolders are project workspaces. The
Projects page lists, in descending folder-name order, every direct child
directory that holds at least one of ``.run.json``, ``recap_720p.mp4``,
``recap_720p_9x16.mp4``, ``recap_final.mp4``, ``shotlog.html``, ``explain.html`` or ``edit.json``, with a
running/failed/done status badge. Each project page has the Recap / Why? / Shots
tabs (recap player, ``explain.html``, ``shotlog.html``) and a Re-edit form;
``/upload`` takes clips and photos from a phone browser into the inbox and
starts ``recap.py`` in the background. Only the finished artifacts (both recap formats, final, explain, shots) are ever
served, also from ``versions/vNNN/``; proxies, ``edit.json``, ``.run.json``,
``source/`` symlinks and every other file stay private. The address is a single host, never the wildcard, so
the server is reachable over the tailnet only. The tool writes inside a
workspace only ``.run.json``, ``run.log`` (with a start marker per run) and
``expectations.yaml`` (the Shots-tab review taps); the inbox receives uploads and is
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
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import yaml

from common.config import (
    ConfigError,
    inbox_path,
    load_config,
    music_library,
    no_inbox_message,
    no_music_library_message,
)
from common.media import clip_kind
from common.moves import slot_screen_length
from common.timeline_words import fade_for_join
from common.pacing import estimate_all_good
from common.versions import list_versions
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME
from common.errors import report_error

TOOL = "scripts/serve.py"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8091
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
LENGTH_CHOICES = {"auto": None, "30s": 30, "60s": 60, "90s": 90, "3min": 180, "all": 0, "custom": None}
ORDER_CHOICES = {"auto": None, "chronological": "chronological", "best": "score"}
AMBIENT_CHOICES = {"auto", "6", "8", "12", "16", "20"}
DETECTED_ARTIFACTS = (
    ".run.json",
    "recap_720p.mp4",
    "recap_720p_9x16.mp4",
    "recap_final.mp4",
    "recap_final_9x16.mp4",
    "shotlog.html",
    "explain.html",
    "edit.json",
)
VERTICAL_PREVIEW = "recap_720p_9x16.mp4"
VERTICAL_FINAL = "recap_final_9x16.mp4"
FORMAT_CHOICES = {"auto": None, "16x9": "16x9", "9x16": "9x16", "both": "both"}
SERVED_FILES = (
    "recap_720p.mp4",
    VERTICAL_PREVIEW,
    "recap_final.mp4",
    VERTICAL_FINAL,
    "shotlog.html",
    "explain.html",
)
CONTENT_TYPES = {
    "recap_720p.mp4": "video/mp4",
    VERTICAL_PREVIEW: "video/mp4",
    "recap_final.mp4": "video/mp4",
    VERTICAL_FINAL: "video/mp4",
    "shotlog.html": "text/html; charset=utf-8",
    "explain.html": "text/html; charset=utf-8",
}
ARTIFACT_LABELS = (
    ("recap_720p.mp4", "recap"),
    (VERTICAL_PREVIEW, "vertical"),
    ("recap_final.mp4", "final"),
    (VERTICAL_FINAL, "final vertical"),
    ("explain.html", "why"),
    ("shotlog.html", "shots"),
)
TABS = (("recap", "Recap"), ("why", "Why?"), ("shots", "Shots"), ("cuts", "Cuts"), ("shorts", "Shorts"))
TAB_KEYS = tuple(key for key, _ in TABS)
SHORTS_LAYOUT_POLICIES = ("auto", "on", "off")
SHORTS_LAYOUTS = ("face", "split")
SHORTS_CANDIDATES_FILE = "shorts_candidates.json"
MUSIC_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}
VERSION_RE = re.compile(r"^v\d{1,4}$")
RUN_MARKER = "=== recap run started"
PROGRESS_RE = re.compile(r"^\[\s*(\d+)/\s*(\d+)\]\s+(\S+)\s+(\S+)")
STEP_SECONDS_RE = re.compile(r"(\d+(?:\.\d+)?)s\b")
LENGTH_MIN_S = 5
LENGTH_MAX_S = 3600
LOG_TAIL_BYTES = 262144
STATION_PLAIN_NAMES = {
    "proxy": "making proxies",
    "motion": "measuring camera motion",
    "quality": "checking quality",
    "describe": "describing shots (vision model)",
    "similarity": "finding look-alike shots",
    "index": "ranking shots",
    "router": "reading the brief",
    "music_pick": "choosing music",
    "llm_director": "editing (model)",
    "explain": "writing the explanation",
    "render": "rendering 16:9",
    "render_vertical": "rendering 9:16",
}

BRAND_PATH = REPO_ROOT / "brand.json"
PROGRESS_FALLBACK_COLOR = "#57b8c4"
SHOT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,63}$")
EXPECTATION_KINDS = ("must_include", "wrong_label")
NOTE_MAX_CHARS = 200
EXPECTATIONS_FILE = "expectations.yaml"
CUT_REVIEW_FILE = "cut_review.yaml"
CUT_PREVIEW_DIR = "cut_previews"
CUT_ACTIONS = ("keep", "cut")
CUT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,63}$")
MODE_B_TOOL = "scripts/rough_cut.py"


def brand_progress_color(brand_path):
    """The brand accent for the progress bar, or the fallback when brand.json is unusable."""
    try:
        data = json.loads(Path(brand_path).read_text(encoding="utf-8"))
        color = data["colors"]["cool"]
    except (OSError, ValueError, KeyError, TypeError):
        return PROGRESS_FALLBACK_COLOR
    if not isinstance(color, str) or not color.strip():
        return PROGRESS_FALLBACK_COLOR
    return color.strip()


BRAND_PROGRESS_COLOR = brand_progress_color(BRAND_PATH)


SHOTS_OVERLAY_STYLE = """
.editbar{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 14px}
.editbar button{min-height:36px;padding:0 12px;border-radius:999px;border:1px solid var(--rope);
background:var(--water);color:var(--stone);font:inherit;font-size:.85rem;cursor:pointer}
.editbar button.on{background:var(--stone);color:var(--water)}
.badge.used{color:var(--sea)}
.why-out{font-size:.8rem;color:var(--left);margin:2px 0 6px;overflow-wrap:anywhere}
.card.in-edit{outline:2px solid var(--sea)}
.shot-actions{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 0}
.shot-actions button{min-height:32px;padding:0 10px;border-radius:999px;border:1px solid var(--rope);
background:var(--water);color:var(--stone);font:inherit;font-size:.8rem;cursor:pointer}
.shot-actions button.on{background:var(--sea);color:var(--water)}
"""

SHOTS_OVERLAY_SCRIPT = """
(function(){var d=window.EDIT_DECISIONS||{};var cards=[].slice.call(document.querySelectorAll('article.card'));
if(!cards.length)return;
var hasEdit=Object.keys(d).length>0;
if(hasEdit){cards.forEach(function(c){var r=c.querySelector('.range');if(!r)return;var k=r.textContent.split(' \u00b7 ').pop().trim();var x=d[k];
var b=c.querySelector('.badges');if(!x||!b)return;c.dataset.state=x.status;
if(x.status==='used'){c.classList.add('in-edit');var s=document.createElement('span');s.className='badge used';s.textContent='in the edit as '+(x.slot||'');b.insertBefore(s,b.firstChild);}
else{var w=document.createElement('p');w.className='why-out';w.textContent='left out: '+(x.reason||'');b.parentNode.insertBefore(w,b.nextSibling);}});}
cards.forEach(function(c){var r=c.querySelector('.range');if(!r)return;var k=r.textContent.split(' \u00b7 ').pop().trim();var b=c.querySelector('.badges');if(!b)return;
var wrap=document.createElement('div');wrap.className='shot-actions';
[['must_include','must be in'],['wrong_label','wrong label']].forEach(function(o){
var btn=document.createElement('button');btn.type='button';btn.textContent=o[1];
btn.addEventListener('click',function(){var body=new URLSearchParams();body.set('shot',k);body.set('kind',o[0]);
fetch(window.location.pathname.split('/shotlog.html')[0]+'/expectations',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body.toString()}).then(function(res){if(res.ok)btn.classList.add('on');});});
wrap.appendChild(btn);});
b.parentNode.insertBefore(wrap,b.nextSibling);});
if(hasEdit){var used=cards.filter(function(c){return c.dataset.state==='used';}).length;
var good=cards.filter(function(c){return c.dataset.state!=='used'&&!c.classList.contains('ineligible');}).length;
var bar=document.createElement('div');bar.className='editbar';
[['all','All '+cards.length],['used','In the edit '+used],['out','Good but left out '+good]].forEach(function(o,i){
var btn=document.createElement('button');btn.type='button';btn.textContent=o[1];if(!i)btn.className='on';
btn.addEventListener('click',function(){[].forEach.call(bar.children,function(e){e.classList.remove('on');});btn.classList.add('on');
cards.forEach(function(c){var st=c.dataset.state;var show=o[0]==='all'||(o[0]==='used'&&st==='used')||(o[0]==='out'&&st!=='used'&&!c.classList.contains('ineligible'));c.style.display=show?'':'none';});});
bar.appendChild(btn);});
var main=document.querySelector('main');if(main)main.parentNode.insertBefore(bar,main);}})();
"""

CUTS_STYLE = """
.cut-row{border-top:1px solid var(--rope);padding:12px 0}
.cut-row:last-child{border-bottom:1px solid var(--rope)}
.cut-head{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline}
.cut-id{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--sea)}
.cut-reason{color:var(--stone)}
.cut-meta{color:var(--haze);font-size:.85rem}
.cut-words{color:var(--haze);font-size:.9rem;margin:4px 0;overflow-wrap:anywhere}
.cut-actions{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 0}
.cut-actions button{min-height:36px;padding:0 14px;border-radius:999px;border:1px solid var(--rope);
background:var(--water);color:var(--stone);font:inherit;font-size:.85rem;cursor:pointer}
.cut-actions button.on{background:var(--sea);color:var(--water)}
.cut-audio{margin-top:8px;width:100%}
@media (max-width:600px){.cut-head{display:block}.cut-meta{display:block;margin-top:2px}}
"""

CUTS_SCRIPT = """
(function(){var base=window.location.pathname.split('?')[0].replace(/\\/$/,'');
[].forEach.call(document.querySelectorAll('.cut-row'),function(row){var id=row.dataset.id;
[].forEach.call(row.querySelectorAll('.cut-actions button'),function(btn){
btn.addEventListener('click',function(){var body=new URLSearchParams();body.set('id',id);body.set('action',btn.dataset.action);
fetch(base+'/cut-review',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body.toString()}).then(function(res){if(res.ok){[].forEach.call(row.querySelectorAll('.cut-actions button'),function(other){other.classList.remove('on');});btn.classList.add('on');}});});});});})();
"""

SHORTS_STYLE = """
.short-row{border-top:1px solid var(--rope);padding:12px 0}
.short-row:last-child{border-bottom:1px solid var(--rope)}
.short-head{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline}
.short-rank{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--sea)}
.short-reason{color:var(--stone)}
.short-title{margin:4px 0 0}
.short-description{color:var(--stone);margin:2px 0 0}
.short-meta{color:var(--haze);font-size:.85rem}
.short-chapter{color:var(--haze);font-size:.9rem;margin:4px 0}
.short-layout{display:flex;align-items:center;gap:8px;margin:6px 0 0}
.short-layout select{width:auto;min-width:120px}
"""

SHORTS_SCRIPT = """
(function(){var base=window.location.pathname.split('?')[0].replace(/\\/$/,'');
[].forEach.call(document.querySelectorAll('.short-row'),function(row){var rank=row.dataset.rank;
[].forEach.call(row.querySelectorAll('.short-layout select'),function(sel){
sel.addEventListener('change',function(){var body=new URLSearchParams();body.set('rank',rank);body.set('layout',sel.value);
fetch(base+'/shorts-candidates',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body.toString()});});});});})();
"""


def edit_decisions(directory):
    """``{shot id: {status, slot, reason}}`` from the workspace's edit.json, or an empty dict."""
    try:
        document = json.loads((Path(directory) / "edit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = document.get("decisions") if isinstance(document, dict) else None
    result = {}
    for row in rows or []:
        if isinstance(row, dict) and row.get("id"):
            result[str(row["id"])] = {
                "status": row.get("status"),
                "slot": row.get("slot"),
                "reason": row.get("reason"),
            }
    return result


def is_mode_b(directory):
    """True when the workspace is a Mode B project: sessions.yaml or a rough_cut edit.json."""
    directory = Path(directory)
    if (directory / "sessions.yaml").is_file():
        return True
    try:
        document = json.loads((directory / "edit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    meta = document.get("meta") if isinstance(document, dict) else None
    return isinstance(meta, dict) and meta.get("tool") == MODE_B_TOOL


def cut_decisions(directory):
    """The cut rows of a Mode B edit.json: rejected cuts plus kept-by-review keeps."""
    try:
        document = json.loads((Path(directory) / "edit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = document.get("decisions") if isinstance(document, dict) else None
    result = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        status = row.get("status")
        reason = row.get("reason")
        if status == "rejected" or (status == "used" and reason == "kept by review"):
            result.append({
                "id": str(row["id"]),
                "status": status,
                "reason": reason,
                "clip": row.get("clip"),
                "window_in_s": row.get("window_in_s"),
                "window_out_s": row.get("window_out_s"),
            })
    return result


def shorts_layout_default():
    """The config's ``shorts_layout`` policy, or ``auto`` when it is missing or invalid."""
    try:
        value = load_config().get("shorts_layout", "auto")
    except (ConfigError, OSError, ValueError):
        return "auto"
    if value is True:
        value = "on"
    elif value is False:
        value = "off"
    return value if value in SHORTS_LAYOUT_POLICIES else "auto"


def load_shorts_candidates(directory):
    """The candidate rows of a workspace's shorts_candidates.json, or an empty list."""
    try:
        document = json.loads((Path(directory) / SHORTS_CANDIDATES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = document.get("candidates") if isinstance(document, dict) else None
    return [row for row in rows or [] if isinstance(row, dict)]


def shorts_layout_options(directory):
    """The layout values offered for a project: ``face`` only for a camera-only session."""
    try:
        document = json.loads((Path(directory) / "edit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return SHORTS_LAYOUTS
    sources = document.get("sources") if isinstance(document, dict) else None
    if not isinstance(sources, list):
        return SHORTS_LAYOUTS
    video_names = [
        str(row.get("file") or "")
        for row in sources
        if isinstance(row, dict) and row.get("media_type") != "photo"
    ]
    if not video_names:
        return SHORTS_LAYOUTS
    if any(clip_kind(name) == "screen" for name in video_names):
        return SHORTS_LAYOUTS
    return ("face",)


def set_candidate_layout(path, rank, layout):
    """Rewrite one candidate's ``layout`` key atomically and return the document."""
    path = Path(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("shorts candidates must be a mapping")
    rows = document.get("candidates")
    if not isinstance(rows, list):
        raise ValueError("candidates must be a list")
    for row in rows:
        if isinstance(row, dict) and row.get("rank") == rank:
            row["layout"] = layout
    write_text_atomic(path, json.dumps(document, indent=2) + "\n")
    return document


def shotlog_with_edit(path, decisions):
    """The shotlog page with the review buttons and, when an edit exists, its used/left-out marks."""
    text = Path(path).read_text(encoding="utf-8")
    payload = json.dumps(decisions).replace("</", "<\\/")
    addition = (
        f"<style>{SHOTS_OVERLAY_STYLE}</style>"
        f"<script>window.EDIT_DECISIONS={payload};{SHOTS_OVERLAY_SCRIPT}</script>"
    )
    if "</body>" in text:
        return text.replace("</body>", addition + "</body>", 1)
    return text + addition


class ServeRuntimeError(RuntimeError):
    """Raised when the preview server cannot bind."""


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
            " (single bind address, tailnet-only)."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/serve.py --workspaces <workspaces>\n"
            "  python scripts/serve.py --workspaces <workspaces> --inbox <inbox> --port 8091 --json\n"
            "  python scripts/serve.py --workspaces <workspaces> --inbox <inbox> --check --json\n"
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
        default=None,
        help=(
            "folder of music tracks offered on the Upload page (or YT_PIPELINE_MUSIC_LIBRARY, "
            "or music.library in the config; no personal default)"
        ),
    )
    parser.add_argument(
        "--inbox",
        default=None,
        help=(
            "inbox the uploads land in (or YT_PIPELINE_INBOX, or inbox.path "
            "in the config; no personal default)"
        ),
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
        help=(
            "validate the root and the bind, print the summary and exit; without a "
            "configured inbox it warns and still validates the root and the bind"
        ),
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


def pid_alive(pid):
    """True when ``pid`` is a live process; a finished child waiting to be reaped is not."""
    import psutil

    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def last_run_lines(directory):
    """The run.log lines of the latest run (after the last start marker)."""
    path = Path(directory) / "run.log"
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - LOG_TAIL_BYTES))
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = text.splitlines()
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].startswith(RUN_MARKER):
            return lines[index + 1:]
    return lines


def run_report(directory):
    """What the latest run did: outcome, progress, the station running now and the error."""
    lines = last_run_lines(directory)
    report = {
        "outcome": None,
        "done": 0,
        "total": 0,
        "last": None,
        "failed_at": None,
        "error": None,
        "current": None,
        "current_step": 0,
        "elapsed": None,
        "steps": [],
        "started_at": None,
        "step_started_at": None,
    }
    for line in lines:
        match = PROGRESS_RE.match(line)
        if match:
            report["total"] = int(match.group(2))
            if match.group(4) == "start":
                report["current"] = match.group(3)
                report["current_step"] = int(match.group(1))
                continue
            seconds = STEP_SECONDS_RE.search(line[match.end():])
            report["steps"].append({
                "step": int(match.group(1)),
                "name": match.group(3),
                "seconds": float(seconds.group(1)) if seconds else None,
            })
            report["done"] = int(match.group(1))
            report["last"] = match.group(3)
            report["current"] = None
            report["current_step"] = 0
            if match.group(4) == "FAIL" and report["failed_at"] is None:
                report["failed_at"] = match.group(3)
            continue
        stripped = line.strip()
        if stripped.startswith("{") and '"scripts/recap.py"' in stripped:
            try:
                data = json.loads(stripped)
            except ValueError:
                continue
            if isinstance(data, dict):
                report["outcome"] = data.get("status")
    report["error"] = failure_text(lines)
    report["started_at"] = last_run_start(directory)
    report["cost_usd"] = spend_between(directory, report["started_at"]) if report["started_at"] else None
    if report["current"] is not None:
        report["elapsed"] = run_log_age(directory)
        if report["elapsed"] is not None:
            report["step_started_at"] = time.time() - report["elapsed"]
    return report


def _epoch(stamp):
    """Epoch seconds of an ISO time with an offset, or None."""
    from datetime import datetime

    text = str(stamp or "")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z",):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def spend_between(directory, start, end=None):
    """Model spend in USD recorded in llm_spend.json between two epoch times."""
    try:
        data = json.loads((Path(directory) / "llm_spend.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    total = 0.0
    for entry in data.get("entries") or []:
        when = _epoch(entry.get("ts"))
        if when is None or (start is not None and when < start) or (end is not None and when > end):
            continue
        total += float(entry.get("cost_usd") or 0.0)
    return total


def format_cost(usd):
    """$0.047, or < $0.001 for a tiny spend."""
    if usd is None:
        return "?"
    if 0 < usd < 0.001:
        return "< $0.001"
    return f"${usd:.3f}"


def last_run_start(directory):
    """Epoch seconds of the latest run's start marker, or None."""
    path = Path(directory) / "run.log"
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - LOG_TAIL_BYTES))
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return None
    for line in reversed(text.splitlines()):
        if line.startswith(RUN_MARKER):
            stamp = line[len(RUN_MARKER):].strip(" =")
            try:
                return time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                return None
    return None


def run_log_age(directory):
    """Seconds since run.log was last written, or None when it cannot be read."""
    try:
        return time.time() - (Path(directory) / "run.log").stat().st_mtime
    except OSError:
        return None


def failure_text(lines):
    """The most telling line of the first failure in the log, or None."""
    for index, line in enumerate(lines):
        if not (line.startswith("[error]") and " failed" in line):
            continue
        detail = None
        for follow in lines[index + 1:]:
            if PROGRESS_RE.match(follow) or follow.startswith("[error]") or follow.lstrip().startswith("{"):
                break
            if follow.strip():
                detail = follow.strip()
        return detail or line[len("[error]"):].strip()
    return None


def project_status(directory):
    """Return running, failed or done for one project workspace."""
    directory = Path(directory)
    pid = read_run_pid(directory)
    if pid is None:
        return "done"
    if pid_alive(pid):
        return "running"
    report = run_report(directory)
    if report["outcome"] == "done":
        return "done"
    if report["outcome"] == "failed" or report["failed_at"]:
        return "failed"
    if not (directory / "recap_720p.mp4").is_file() and not (directory / VERTICAL_PREVIEW).is_file():
        return "failed"
    return "done"


def list_music_tracks(music_dir):
    """Return the audio file names offered on the Upload page, sorted by name."""
    if music_dir is None:
        return []
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
.lede{color:var(--haze);margin:0 0 18px;display:flex;flex-wrap:wrap;gap:4px 16px;align-items:center}
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
video.vertical{aspect-ratio:9/16;max-width:360px;margin-top:14px}
.strip{margin:14px 0 4px}
.strip-track{display:flex;gap:3px;height:56px}
.strip-track button{flex:var(--w) 1 0;min-width:16px;border:0;border-radius:var(--radius-s);
padding:0;font:inherit;font-size:.72rem;letter-spacing:-.02em;color:var(--water);cursor:pointer;
background:var(--sea);overflow:hidden;white-space:nowrap;text-overflow:clip;
text-align:center;opacity:.72;transition:opacity .15s}
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
.notice{border-radius:var(--radius-l);padding:12px 14px;margin:0 0 14px;background:var(--deck);
border-left:4px solid var(--busy)}
.notice.failed{border-left-color:var(--left)}
.notice code{overflow-wrap:anywhere;font-size:.85rem;color:var(--haze)}
.versions{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0 0 12px;color:var(--haze);font-size:.9rem}
.versions a{min-height:36px;min-width:44px;display:inline-flex;align-items:center;justify-content:center;
padding:0 12px;border-radius:999px;background:var(--deck);color:var(--stone);text-decoration:none}
.versions a.on{background:var(--stone);color:var(--water)}
.versions a.failed{color:var(--left)}
.versions a.on.failed{background:var(--left);color:var(--water)}
.player{margin-bottom:22px}
.player h2{font-family:ui-serif,Georgia,serif;font-weight:500;font-size:1.15rem;margin:0 0 8px}
.vmeta{color:var(--haze);font-size:.88rem;margin:6px 0 0;overflow-wrap:anywhere}
input.length-s{margin-top:8px}
input.length-s[hidden]{display:none}
button.go{min-height:48px;border:0;border-radius:12px;background:var(--sea);color:var(--water);
font:inherit;font-weight:600;padding:0 26px;cursor:pointer;justify-self:start}
"""

PROGRESS_CSS = (
    "progress{width:100%;height:10px;accent-color:var(--sea);border-radius:999px;display:block;margin:8px 0 0}\n"
    f"progress::-webkit-progress-value{{background:{BRAND_PROGRESS_COLOR};border-radius:999px}}\n"
    f"progress::-moz-progress-bar{{background:{BRAND_PROGRESS_COLOR};border-radius:999px}}\n"
)

LENGTH_SCRIPT = """
[].forEach.call(document.querySelectorAll('select[name=length]'),function(s){var i=s.parentNode.querySelector('.length-s');if(!i)return;
function sync(){var on=s.value==='custom';i.hidden=!on;if(!on)i.value='';}
s.addEventListener('change',function(){sync();if(s.value==='custom')i.focus();});sync();});
"""

DROP_STYLE = (
    ".drop{border:2px dashed currentColor;border-radius:12px;padding:16px;opacity:.85}"
    ".drop.over{opacity:1;border-style:solid}"
    ".drop-hint{margin:.5em 0 0}"
    ".drop-count{font-weight:600}"
)

# Drag and drop for the upload form: every drop (or pick) adds to the selection
# instead of replacing it, and a drop anywhere on the page lands in the form.
DROP_SCRIPT = """
(function(){var z=document.querySelector('[data-drop]');if(!z)return;var inp=z.querySelector('input[type=file]');var c=z.querySelector('.drop-count');var keep=[];
function show(){c.textContent=keep.length?keep.length+(keep.length===1?' file selected':' files selected'):'';}
function put(list){[].forEach.call(list,function(f){if(!keep.some(function(k){return k.name===f.name&&k.size===f.size;}))keep.push(f);});
try{var dt=new DataTransfer();keep.forEach(function(f){dt.items.add(f);});inp.files=dt.files;}catch(e){}show();}
inp.addEventListener('change',function(){put([].slice.call(inp.files));});
['dragenter','dragover'].forEach(function(t){document.addEventListener(t,function(e){e.preventDefault();z.classList.add('over');});});
document.addEventListener('dragleave',function(e){if(!e.relatedTarget)z.classList.remove('over');});
document.addEventListener('drop',function(e){e.preventDefault();z.classList.remove('over');if(e.dataTransfer&&e.dataTransfer.files.length)put(e.dataTransfer.files);});
})();
"""

# Upload progress: the form is sent with XMLHttpRequest so the page can show how
# much has reached the server; without JavaScript the form posts as before.
UPLOAD_PROGRESS_SCRIPT = """
(function(){var f=document.querySelector('form[action="/upload"]');if(!f||!window.XMLHttpRequest||!window.FormData)return;
var box=f.querySelector('.upload-progress'),bar=box.querySelector('progress'),st=box.querySelector('.upload-status'),btn=f.querySelector('button[type=submit]');
function mb(n){return (n/1048576).toFixed(0)+' MB';}
f.addEventListener('submit',function(e){e.preventDefault();var d=new FormData(f);d.append('submit','start');
var x=new XMLHttpRequest();x.open('POST',f.action);box.hidden=false;btn.disabled=true;var t0=Date.now();
x.upload.onprogress=function(ev){if(!ev.lengthComputable)return;var pc=Math.floor(ev.loaded*100/ev.total);bar.value=pc;
var sec=(Date.now()-t0)/1000,rate=ev.loaded/Math.max(sec,1),left=(ev.total-ev.loaded)/Math.max(rate,1);
st.textContent='Uploading: '+pc+'% ('+mb(ev.loaded)+' of '+mb(ev.total)+'), about '+Math.ceil(left/60)+' min left';};
x.upload.onload=function(){bar.value=100;st.textContent='Upload complete, the server is checking the files...';};
x.onload=function(){if(x.status>=200&&x.status<400){window.location=x.responseURL||'/';}else{btn.disabled=false;st.textContent='Upload failed ('+x.status+'): '+(x.responseText||'').replace(/<[^>]*>/g,' ').trim().slice(0,200);}};
x.onerror=function(){btn.disabled=false;st.textContent='Upload interrupted: the connection to the server was lost. Pick the files again and retry.';};
x.send(d);});})();
"""

RUN_NOTICE_STYLE = (
    ".run-now{margin:.6em 0 .4em;font-size:1.05em}"
    ".step-badge{display:inline-block;padding:.1em .6em;border-radius:999px;border:1px solid currentColor;"
    "font-weight:600;margin-right:.4em}"
    ".run-total{margin:.4em 0}"
    ".run-steps ol{margin:.4em 0;padding-left:1.2em}"
    ".run-steps li{display:flex;justify-content:space-between;gap:1em;max-width:28em}"
    ".run-hint{opacity:.75;margin:.4em 0 0}"
)

# Durations in the run notice keep counting between the page refreshes.
TICK_SCRIPT = """
(function(){function f(s){s=Math.max(0,Math.round(s));if(s<60)return s+' s';var m=Math.floor(s/60),r=s%60;
if(m<60)return m+' min '+(r<10?'0':'')+r+' s';var h=Math.floor(m/60);m=m%60;return h+' h '+(m<10?'0':'')+m+' min';}
var d=document.querySelector('details.run-steps');if(d){try{if(sessionStorage.getItem('runSteps')==='open')d.open=true;}catch(e){}d.addEventListener('toggle',function(){try{sessionStorage.setItem('runSteps',d.open?'open':'closed');}catch(e){}});}
var t=[].slice.call(document.querySelectorAll('.tick[data-since]'));if(!t.length)return;
function go(){var n=Date.now()/1000;t.forEach(function(e){e.textContent=f(n-parseFloat(e.dataset.since));});}go();setInterval(go,1000);})();
"""

STRIP_SCRIPT = """
[].forEach.call(document.querySelectorAll('.player'),function(p){var v=p.querySelector('video');var b=[].slice.call(p.querySelectorAll('.strip-track button'));
if(!v||!b.length)return;b.forEach(function(x){x.addEventListener('click',function(){v.currentTime=parseFloat(x.dataset.t);v.play();});});
v.addEventListener('timeupdate',function(){var t=v.currentTime;b.forEach(function(x){var s=parseFloat(x.dataset.t),e=parseFloat(x.dataset.e);x.classList.toggle('now',t>=s&&t<e);});});});
"""


def render_page(title, body, active=None, script="", refresh=None):
    """Return one full HTML page with the top bar and the shared theme."""
    nav = []
    for key, href, label in (("projects", "/", "Projects"), ("upload", "/upload", "New recap")):
        on = " on" if key == active else ""
        nav.append(f'<a class="nav{on}" href="{href}">{label}</a>')
    tail = f"<script>{script}</script>" if script else ""
    reload_tag = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"{reload_tag}"
        f"<title>{html_escape(title)}</title>"
        f'<style>{THEME_CSS}{PROGRESS_CSS}</style></head><body><div class="wrap">'
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
                length = slot_screen_length(
                    {**slot, "in_s": float(slot.get("in_s", 0.0)), "out_s": float(slot.get("out_s", 0.0))}
                )
                kind = "clip"
        except (TypeError, ValueError):
            continue
        if length <= 0:
            continue
        label = slot.get("shot_key") or slot.get("photo") or slot.get("clip") or slot.get("id") or ""
        if blocks:
            start -= fade_for_join(slot, blocks[-1]["end"] - blocks[-1]["start"], length)
            blocks[-1]["end"] = start
        blocks.append({"start": start, "end": start + length, "kind": kind, "label": str(label)})
        start += length
    if not blocks:
        return None
    photos = sum(1 for block in blocks if block["kind"] == "photo")
    return {"blocks": blocks, "total": start, "shots": len(blocks), "photos": photos}


def all_good_estimate(directory):
    """How long an edit with every good shot would be, from shotlog.json; None when absent."""
    try:
        shotlog = json.loads((Path(directory) / "shotlog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return estimate_all_good(shotlog if isinstance(shotlog, dict) else {})


def format_length(seconds):
    """Seconds as '45 s' or '3 min 20 s'."""
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min {rest} s" if rest else f"{minutes} min"


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
    for number, block in enumerate(summary["blocks"], start=1):
        width = max(0.4, block["end"] - block["start"])
        label = html_escape(block["label"])
        buttons.append(
            f'<button type="button" class="{block["kind"]}" style="--w:{width:.2f}" '
            f'data-t="{block["start"]:.2f}" data-e="{block["end"]:.2f}" '
            f'title="{number}: {label} at {block["start"]:.1f} s" '
            f'aria-label="Shot {number}, {label}, at {block["start"]:.1f} s">{number}</button>'
        )
    return (
        '<div class="strip" aria-label="Edit timeline">'
        f'<div class="strip-track">{"".join(buttons)}</div>'
        '<div class="strip-legend"><span class="k-clip">clip</span>'
        '<span class="k-photo">photo</span>'
        f'<span class="num">{html_escape(describe_summary(summary))}</span></div></div>'
    )


def edit_matches_renders(directory):
    """True when the renders in ``directory`` were made from its current edit.json."""
    directory = Path(directory)
    edit = directory / "edit.json"
    renders = [directory / name for name in ("recap_720p.mp4", VERTICAL_PREVIEW) if (directory / name).is_file()]
    if not edit.is_file() or not renders:
        return False
    return edit.stat().st_mtime <= max(path.stat().st_mtime for path in renders)


def render_player(directory, prefix="", heading=None, meta_line=None, trust_edit=False):
    """One player block: the video(s) of a folder, its strip and an optional note."""
    directory = Path(directory)
    parts = [f"<h2>{html_escape(heading)}</h2>"] if heading else []
    has_wide = (directory / "recap_720p.mp4").is_file()
    has_vertical = (directory / VERTICAL_PREVIEW).is_file()
    if has_wide:
        parts.append(f'<video controls preload="metadata" src="{prefix}recap_720p.mp4"></video>')
    if has_vertical:
        parts.append(
            f'<video class="vertical" controls preload="metadata" src="{prefix}{VERTICAL_PREVIEW}"></video>'
        )
    if has_wide or has_vertical:
        if trust_edit or edit_matches_renders(directory):
            parts.append(render_strip(edit_summary(directory)))
    else:
        parts.append('<p class="empty">This version has no video: its render did not finish.</p>')
    if meta_line:
        parts.append(f'<p class="vmeta">{meta_line}</p>')
    for final_name in ("recap_final.mp4", VERTICAL_FINAL):
        if (directory / final_name).is_file():
            parts.append(
                f'<a class="download" href="{prefix}{final_name}">Download the full-quality {final_name}</a>'
            )
    return f'<div class="player">{"".join(parts)}</div>'


def version_cost(version):
    """Model spend of the run that made this version: since the version before it."""
    path = Path(version["path"])
    made = _epoch(version["meta"].get("created_at"))
    if made is None:
        return None
    before = None
    for sibling in path.parent.glob("*/version.json"):
        if sibling.parent == path:
            continue
        try:
            other = _epoch(json.loads(sibling.read_text(encoding="utf-8")).get("created_at"))
        except (OSError, ValueError):
            continue
        if other is not None and other < made and (before is None or other > before):
            before = other
    return spend_between(path.parent.parent, before, made)


def version_meta_line(version):
    """When a version was made, how long it is and the feedback that asked for it."""
    meta = version["meta"]
    bits = []
    created = str(meta.get("created_at") or "")
    if created:
        bits.append(html_escape(created[:16].replace("T", " ")))
    summary = edit_summary(version["path"])
    if summary:
        bits.append(html_escape(describe_summary(summary)))
    cost = version_cost(version)
    if cost is not None:
        bits.append(f"model cost {format_cost(cost)}")
    if meta.get("status") == "failed":
        bits.append(f"failed at {html_escape(meta.get('failed_at') or '?')}")
    feedback = " / ".join(str(item) for item in meta.get("feedback") or [])
    if feedback:
        bits.append(f"asked: \u201c{html_escape(feedback)}\u201d")
    elif meta.get("note"):
        bits.append(html_escape(meta["note"]))
    return " \u00b7 ".join(bits)


def has_video(version):
    return any(name.endswith(".mp4") for name in version["files"])


def pick_version(versions, wanted):
    """The version to show: the one asked for, else the newest with a video, else the newest."""
    by_number = {version["number"]: version for version in versions}
    if wanted in by_number:
        return by_number[wanted]
    with_video = [version for version in versions if has_video(version)]
    if with_video:
        return with_video[-1]
    return versions[-1] if versions else None


def running_step_text(report):
    """The step sentence of the running notice: the station running now or the count so far."""
    current = report.get("current")
    if current:
        name = html_escape(STATION_PLAIN_NAMES.get(current, current))
        step = report.get("current_step") or 0
        return f"Now: {name}"
    if report["total"]:
        return (
            f"{report['done']} of {report['total']} steps done"
            f" (last: {html_escape(report['last'])})"
        )
    return "starting"


def format_duration(seconds):
    """12 s, 4 min 05 s or 1 h 02 min."""
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {rest:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def live_time(since, seconds):
    """A duration that keeps counting in the browser (TICK_SCRIPT) from ``since``."""
    attr = f' data-since="{since:.0f}"' if since is not None else ""
    return f'<span class="tick"{attr}>{format_duration(seconds)}</span>'


def render_step_list(report):
    """The finished steps with how long each took."""
    rows = []
    for item in report.get("steps") or []:
        label = html_escape(STATION_PLAIN_NAMES.get(item["name"], item["name"]))
        took = format_duration(item["seconds"]) if item["seconds"] is not None else "?"
        rows.append(f'<li><span>{item["step"]}. {label}</span><span class="num">{took}</span></li>')
    if not rows:
        return ""
    return f'<details class="run-steps"><summary>Steps done</summary><ol>{"".join(rows)}</ol></details>'


def render_run_notice(status, report, has_previous=False):
    """The banner about the run in progress or the run that failed; empty otherwise."""
    if status == "running":
        bar = ""
        if report["total"]:
            bar = f'<progress max="{report["total"]}" value="{report["done"]}"></progress>'
        now = time.time()
        step_line = ""
        if report.get("current"):
            badge = f'step {report.get("current_step") or 0} of {report["total"]}'
            took = ""
            if report.get("elapsed") is not None:
                took = f' \u00b7 {live_time(report.get("step_started_at"), report["elapsed"])}'
            step_line = (
                f'<p class="run-now"><span class="step-badge">{badge}</span> '
                f'{running_step_text(report)}{took}</p>'
            )
        else:
            step_line = f'<p class="run-now">{running_step_text(report)}</p>'
        total_line = ""
        if report.get("started_at") is not None:
            total_line = (
                '<p class="run-total">Total so far: '
                f'{live_time(report["started_at"], now - report["started_at"])}</p>'
            )
        if report.get("cost_usd") is not None:
            total_line += f'<p class="run-total">Model cost so far: {format_cost(report["cost_usd"])}</p>'
        hint = "This page refreshes by itself."
        if has_previous:
            hint = "The previous version stays below until the new one is ready. " + hint
        return (
            f'<div class="notice"><strong>Making a new edit</strong>{step_line}{bar}{total_line}'
            f'{render_step_list(report)}<p class="run-hint">{hint}</p></div>'
        )
    if status == "failed":
        where = html_escape(report["failed_at"] or "an unknown step")
        error = f"<br><code>{html_escape(report['error'])}</code>" if report["error"] else ""
        return (
            f'<div class="notice failed"><strong>The last run failed at {where}.</strong>'
            f" The video below is the last one that finished.{error}</div>"
        )
    return ""


def render_version_links(name, versions, shown, compare=None):
    """The row of version chips, plus compare links for the other versions."""
    if len(versions) < 2:
        return ""
    base = f"/{html_escape(name)}/?tab=recap"
    chips = []
    for version in versions:
        classes = []
        if version["number"] == shown["number"]:
            classes.append("on")
        if version["meta"].get("status") == "failed" or not has_video(version):
            classes.append("failed")
        label = version["name"].lstrip("v").lstrip("0") or "0"
        chips.append(
            f'<a class="{" ".join(classes)}" href="{base}&amp;v={version["number"]}" '
            f'title="{html_escape(version_meta_line(version))}">v{label}</a>'
        )
    others = [
        f'<a class="{"on" if compare and compare["number"] == version["number"] else ""}" '
        f'href="{base}&amp;v={shown["number"]}&amp;with={version["number"]}">'
        f'v{version["name"].lstrip("v").lstrip("0") or "0"}</a>'
        for version in versions
        if version["number"] != shown["number"] and has_video(version)
    ]
    row = f'<div class="versions"><span>Version</span>{"".join(chips)}</div>'
    if others:
        row += f'<div class="versions"><span>Compare with</span>{"".join(others)}</div>'
    return row


def render_recap_panel(directory, name=None, wanted=None, compare_with=None, status=None, report=None):
    """Return the Recap tab panel: run notice, version chips, the player(s) with their strips."""
    directory = Path(directory)
    status = status or project_status(directory)
    report = report or run_report(directory)
    versions = list_versions(directory)
    has_previous = any(has_video(version) for version in versions) or (
        (directory / "recap_720p.mp4").is_file() or (directory / VERTICAL_PREVIEW).is_file()
    )
    parts = [render_run_notice(status, report, has_previous=has_previous)]
    shown = pick_version(versions, wanted)
    if shown is None:
        has_any = (directory / "recap_720p.mp4").is_file() or (directory / VERTICAL_PREVIEW).is_file()
        if has_any:
            parts.append(render_player(directory, trust_edit=status != "running" and edit_matches_renders(directory)))
        else:
            parts.append('<p class="empty">No recap yet. It appears here when the render finishes.</p>')
            for final_name in ("recap_final.mp4", VERTICAL_FINAL):
                if (directory / final_name).is_file():
                    parts.append(
                        f'<a class="download" href="{final_name}">Download the full-quality {final_name}</a>'
                    )
        return "\n".join(part for part in parts if part)
    compare = pick_version(versions, compare_with) if compare_with is not None else None
    if compare is not None and compare["number"] == shown["number"]:
        compare = None
    parts.append(render_version_links(name or directory.name, versions, shown, compare))
    heading = shown["name"].lstrip("v").lstrip("0") or "0"
    parts.append(
        render_player(
            shown["path"],
            prefix=f"versions/{shown['name']}/",
            heading=f"Version {heading}" + (" (latest)" if shown is versions[-1] else ""),
            meta_line=version_meta_line(shown),
            trust_edit=True,
        )
    )
    if compare is not None:
        other = compare["name"].lstrip("v").lstrip("0") or "0"
        parts.append(
            render_player(
                compare["path"],
                prefix=f"versions/{compare['name']}/",
                heading=f"Version {other}",
                meta_line=version_meta_line(compare),
                trust_edit=True,
            )
        )
    return "\n".join(part for part in parts if part)


def render_iframe_panel(directory, artifact, empty_text):
    """Return a tab panel embedding ``artifact``, or a placeholder line."""
    if (Path(directory) / artifact).is_file():
        return f'<iframe src="{artifact}" title="{html_escape(artifact)}"></iframe>'
    return f'<p class="empty">{html_escape(empty_text)}</p>'


def render_cuts_panel(directory, name):
    """The Cuts tab: one row per automatic cut with a keep/cut toggle and an audio preview."""
    directory = Path(directory)
    decisions = cut_decisions(directory)
    if not decisions:
        return '<p class="empty">No cuts to review yet. Run scripts/rough_cut.py first.</p>'
    actions = cut_review_actions(directory / CUT_REVIEW_FILE)
    rows = []
    for decision in decisions:
        cut_id = html_escape(decision["id"])
        reason = html_escape(decision.get("reason") or "")
        clip = html_escape(decision.get("clip") or "")
        start = decision.get("window_in_s")
        end = decision.get("window_out_s")
        span = f"{format_timecode(start)}\u2013{format_timecode(end)}"
        length = ""
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            length = f"{end - start:.1f} s"
        words = html_escape(cut_words_text(directory, decision))
        current = actions.get(decision["id"])
        buttons = []
        for action in CUT_ACTIONS:
            on = " on" if current == action else ""
            buttons.append(
                f'<button type="button" class="cut-{action}{on}" data-action="{action}">{action}</button>'
            )
        rows.append(
            f'<article class="cut-row" data-id="{cut_id}">'
            f'<div class="cut-head"><span class="cut-id">{cut_id}</span>'
            f'<span class="cut-reason">{reason}</span>'
            f'<span class="cut-meta num">{clip} \u00b7 {span} \u00b7 {length}</span></div>'
            f'<p class="cut-words">{words}</p>'
            f'<div class="cut-actions">{"".join(buttons)}</div>'
            f'<audio class="cut-audio" controls preload="none" '
            f'src="/{html_escape(name)}/cut-audio?id={cut_id}"></audio>'
            f'</article>'
        )
    return f'<style>{CUTS_STYLE}</style><div class="cuts">{"".join(rows)}</div>'


def render_shorts_panel(directory, name):
    """The Shorts tab: one row per ranked candidate with a face/split layout select."""
    directory = Path(directory)
    candidates = load_shorts_candidates(directory)
    if not candidates:
        return '<p class="empty">No Shorts candidates yet. Run scripts/shorts_rank.py first.</p>'
    layouts = shorts_layout_options(directory)
    rows = []
    for candidate in candidates:
        rank = candidate.get("rank")
        start = candidate.get("start_s")
        end = candidate.get("end_s")
        span = f"{format_timecode(start)}\u2013{format_timecode(end)}"
        duration = candidate.get("duration_s")
        length = f"{float(duration):.1f} s" if isinstance(duration, (int, float)) else ""
        score = candidate.get("score")
        hook = candidate.get("hook")
        payoff = candidate.get("payoff")
        score_text = f"score {float(score):.1f}" if isinstance(score, (int, float)) else ""
        hook_text = (
            f"hook {float(hook):.0f} / payoff {float(payoff):.0f}"
            if isinstance(hook, (int, float)) and isinstance(payoff, (int, float))
            else ""
        )
        meta = " \u00b7 ".join(part for part in (f"[{span}]", length, score_text, hook_text) if part)
        reason = html_escape(candidate.get("reason") or "")
        title = html_escape(candidate.get("title") or "")
        description = html_escape(candidate.get("description") or "")
        title_html = f'<p class="short-title">{title}</p>' if title else ""
        description_html = f'<p class="short-description">{description}</p>' if description else ""
        chapter = candidate.get("chapter_title")
        chapter_html = f'<p class="short-chapter">Chapter: {html_escape(chapter)}</p>' if chapter else ""
        current = candidate.get("layout") if candidate.get("layout") in layouts else "face"
        select = render_select("layout", [(value, value) for value in layouts], default=current)
        rows.append(
            f'<article class="short-row" data-rank="{html_escape(rank)}">'
            f'<div class="short-head"><span class="short-rank">#{html_escape(rank)}</span>'
            f'<span class="short-meta num">{meta}</span></div>'
            f"{title_html}"
            f'<p class="short-reason">{reason}</p>'
            f"{description_html}"
            f"{chapter_html}"
            f'<div class="short-layout"><label>Layout {select}</label></div>'
            f'</article>'
        )
    return f'<style>{SHORTS_STYLE}</style><div class="shorts">{"".join(rows)}</div>'


def render_project(name, directory, tab, music_tracks=(), wanted=None, compare_with=None):
    """Return the project page with the tabs and the Re-edit form."""
    directory = Path(directory)
    mode_b = is_mode_b(directory)
    has_shorts = (directory / SHORTS_CANDIDATES_FILE).is_file()
    if tab == "cuts" and not mode_b:
        tab = "recap"
    if tab == "shorts" and not has_shorts:
        tab = "recap"
    status = project_status(directory)
    report = run_report(directory)
    tab_links = []
    for key, label in TABS:
        if key == "cuts" and not mode_b:
            continue
        if key == "shorts" and not has_shorts:
            continue
        active = ' class="active"' if key == tab else ""
        tab_links.append(
            f'<a href="/{html_escape(name)}/?tab={key}"{active}>{html_escape(label)}</a>'
        )
    panels = {
        "recap": render_recap_panel(directory, name, wanted, compare_with, status, report),
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
    if mode_b:
        panels["cuts"] = render_cuts_panel(directory, name)
    if has_shorts:
        panels["shorts"] = render_shorts_panel(directory, name)
    lede = f'<span class="state badge-{status}">{status}</span>'
    estimate = all_good_estimate(directory)
    if estimate and estimate["shots"]:
        lede += (
            f' <span class="facts num">All good shots would make about '
            f'{format_length(estimate["seconds"])} ({estimate["shots"]} shots)</span>'
        )
    body = (
        f"<style>{RUN_NOTICE_STYLE}</style><h1>{html_escape(name)}</h1>"
        f'<p class="lede">{lede}</p>'
        f'<nav class="tabs">{"".join(tab_links)}</nav>'
        f'<section class="panel">{panels[tab]}</section>'
        f"{render_reedit(name, music_tracks)}"
    )
    script = (STRIP_SCRIPT + TICK_SCRIPT if tab == "recap" else "") + LENGTH_SCRIPT
    if tab == "cuts":
        script += CUTS_SCRIPT
    if tab == "shorts":
        script += SHORTS_SCRIPT
    refresh = 20 if status == "running" else None
    return render_page(name, body, active="projects", script=script, refresh=refresh)


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
                [
                    ("auto", "Auto"), ("30s", "30s"), ("60s", "60s"), ("90s", "90s"),
                    ("3min", "3 min"), ("all", "All good shots"), ("custom", "Exact seconds\u2026"),
                ],
                default="auto",
            )
            + f'<input class="length-s" type="number" name="length_s" min="{LENGTH_MIN_S}" '
            f'max="{LENGTH_MAX_S}" step="1" inputmode="numeric" placeholder="seconds, e.g. 45" '
            'aria-label="Length in seconds">',
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
        (
            "Format",
            render_select(
                "format",
                [("auto", "Auto (16:9)"), ("16x9", "16:9"), ("9x16", "9:16"), ("both", "both")],
                default="auto",
            ),
        ),
        (
            "Speed ramp",
            render_select(
                "speed_ramp",
                [("auto", "Auto (off)"), ("off", "off"), ("on", "on")],
                default="auto",
            ),
        ),
        (
            "Shorts layout",
            render_select(
                "shorts_layout",
                [("auto", "Auto"), ("on", "on"), ("off", "off")],
                default=shorts_layout_default(),
            ),
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


def is_short_control(control):
    """Selects and number boxes sit two to a row."""
    return control.startswith("<select") or control.startswith('<input type="number"')


def render_form_rows(fields):
    rows = [f"<label>{html_escape(label)}{control}</label>" for label, control in fields]
    paired = []
    index = 0
    while index < len(rows):
        if index + 1 < len(rows) and is_short_control(fields[index][1]) and is_short_control(fields[index + 1][1]):
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
        ("Clips and photos", '<div class="drop" data-drop>'
                             '<input type="file" name="files" multiple accept="video/*,image/*">'
                             '<p class="drop-hint">Drag clips and photos here (several at once, '
                             'or in several drops), or tap to choose. <span class="drop-count"></span></p></div>'),
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
        '<div class="upload-progress" hidden><progress max="100" value="0"></progress>'
        '<p class="upload-status" aria-live="polite"></p></div>'
        "</form>"
    )
    return render_page("New recap", f"<style>{DROP_STYLE}</style>{body}", active="upload",
                       script=LENGTH_SCRIPT + DROP_SCRIPT + UPLOAD_PROGRESS_SCRIPT)


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


def parse_number(raw_path, key):
    """An integer query parameter, or None."""
    values = urllib.parse.parse_qs(urllib.parse.urlsplit(raw_path).query).get(key, [])
    if values and values[0].isdigit() and len(values[0]) <= 4:
        return int(values[0])
    return None


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


def parse_urlencoded(body):
    """Return the form fields of an ``application/x-www-form-urlencoded`` body."""
    fields = {}
    for name, value in urllib.parse.parse_qsl(
        body.decode("utf-8", "replace"), keep_blank_values=True
    ):
        fields[name] = value
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


def validate_shot(value):
    """The shot key from a review tap, or a 400 when it is missing or malformed."""
    shot = (value or "").strip()
    if not SHOT_KEY_RE.match(shot):
        raise UploadError(400, "invalid shot key")
    return shot


def validate_kind(value):
    """The expectation kind from a review tap, or a 400 when it is not one of the two."""
    kind = (value or "").strip()
    if kind not in EXPECTATION_KINDS:
        raise UploadError(400, "invalid kind")
    return kind


def validate_note(value):
    """The optional single-line note, or a 400 when it is longer than the cap."""
    note = (value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(note) > NOTE_MAX_CHARS:
        raise UploadError(400, "note too long")
    return note


def load_expectations_document(path):
    """The expectations YAML as a mapping, or an empty mapping when the file is absent."""
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read expectations: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("expectations must be a mapping")
    return data


def expectation_stamp(now=None):
    """The UTC timestamp for a review note, or the given value as text."""
    if now is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(now, datetime):
        return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(now)


def must_include_has(rows, shot):
    """True when ``shot`` already appears in any ``any_of`` list of the must_include rows."""
    for row in rows:
        if isinstance(row, str) and row == shot:
            return True
        if isinstance(row, dict) and shot in [str(key) for key in (row.get("any_of") or [])]:
            return True
    return False


def append_expectation(path, kind, shot, note, now=None):
    """Append one review expectation to the YAML file idempotently and return the document."""
    path = Path(path)
    document = load_expectations_document(path)
    stamp = expectation_stamp(now)
    if kind == "must_include":
        rows = document.get("must_include")
        if not isinstance(rows, list):
            rows = []
        if not must_include_has(rows, shot):
            rows.append({"any_of": [shot], "note": note or f"must be in (phone review {stamp})"})
        document["must_include"] = rows
    else:
        rows = document.get("wrong_label")
        if not isinstance(rows, list):
            rows = []
        if not any(isinstance(row, dict) and str(row.get("shot")) == shot for row in rows):
            rows.append({"shot": shot, "note": note or f"wrong label (phone review {stamp})"})
        document["wrong_label"] = rows
    text = (
        "# Written by scripts/serve.py from phone review taps; machine-owned.\n"
        + yaml.safe_dump(document, sort_keys=False, allow_unicode=True, default_flow_style=False)
    )
    write_text_atomic(path, text)
    return document


def load_cut_review_document(path):
    """The cut review YAML as a mapping with a ``review`` list, or an empty one when absent."""
    path = Path(path)
    if not path.is_file():
        return {"review": []}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read cut review: {exc}") from exc
    if data is None:
        return {"review": []}
    if not isinstance(data, dict):
        raise ValueError("cut review must be a mapping")
    rows = data.get("review")
    if rows is None:
        data["review"] = []
    elif not isinstance(rows, list):
        raise ValueError("cut review rows must be a list")
    return data


def cut_review_actions(path):
    """``{cut id: action}`` from the workspace's cut_review.yaml, or an empty dict."""
    try:
        document = load_cut_review_document(path)
    except ValueError:
        return {}
    actions = {}
    for row in document.get("review") or []:
        if isinstance(row, dict) and row.get("id"):
            actions[str(row["id"])] = row.get("action")
    return actions


def append_cut_review(path, cut_id, action, note, clip, window_in_s, window_out_s, now=None):
    """Record one phone cut review tap idempotently and return the document."""
    path = Path(path)
    document = load_cut_review_document(path)
    row = {
        "id": cut_id,
        "action": action,
        "clip": clip,
        "window_in_s": window_in_s,
        "window_out_s": window_out_s,
        "note": note,
        "reviewed_at": expectation_stamp(now),
    }
    rows = document.get("review") or []
    replaced = False
    updated = []
    for existing in rows:
        if isinstance(existing, dict) and str(existing.get("id")) == cut_id:
            updated.append(row)
            replaced = True
        else:
            updated.append(existing)
    if not replaced:
        updated.append(row)
    document["review"] = updated
    text = (
        "# Written by scripts/serve.py from phone cut review taps; machine-owned.\n"
        + yaml.safe_dump(document, sort_keys=False, allow_unicode=True, default_flow_style=False)
    )
    write_text_atomic(path, text)
    return document


def validate_cut_id(value):
    """The cut id from a review tap, or a 400 when it is missing or malformed."""
    cut_id = (value or "").strip()
    if not CUT_ID_RE.match(cut_id):
        raise UploadError(400, "invalid cut id")
    return cut_id


def validate_cut_action(value):
    """The keep/cut action from a review tap, or a 400 when it is not one of the two."""
    action = (value or "").strip()
    if action not in CUT_ACTIONS:
        raise UploadError(400, "invalid action")
    return action


def validate_rank(value):
    """The candidate rank from a layout tap, or a 400 when it is missing or not a positive integer."""
    text = (value or "").strip()
    if not text.isdigit() or len(text) > 4:
        raise UploadError(400, "invalid rank")
    rank = int(text)
    if rank < 1:
        raise UploadError(400, "invalid rank")
    return rank


def validate_layout(value):
    """The face/split layout from a layout tap, or a 400 when it is not one of the two."""
    layout = (value or "").strip()
    if layout not in SHORTS_LAYOUTS:
        raise UploadError(400, "invalid layout")
    return layout


def format_timecode(seconds):
    """Seconds as ``MM:SS.d``."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return ""
    if value < 0:
        value = 0.0
    minutes, rest = divmod(value, 60)
    return f"{int(minutes):02d}:{rest:04.1f}"


def load_sessions_words(directory):
    """The sessions manifest of a workspace, or an empty list."""
    try:
        document = yaml.safe_load((Path(directory) / "sessions.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(document, dict):
        return []
    sessions = document.get("sessions")
    return sessions if isinstance(sessions, list) else []


def session_words_for_clip(sessions, clip):
    """The word JSON path of the session whose camera file matches ``clip``, or None."""
    if not clip:
        return None
    target_name = Path(str(clip)).name
    target_stem = Path(str(clip)).stem
    for session in sessions:
        if not isinstance(session, dict):
            continue
        camera = Path(str(session.get("camera") or ""))
        if camera.name == target_name or camera.stem == target_stem:
            return session.get("words")
    return None


def cut_words_text(directory, decision):
    """The words of a cut span as text, from the session's word JSON; empty when unknown."""
    words_path = session_words_for_clip(load_sessions_words(directory), decision.get("clip"))
    if not words_path:
        return ""
    try:
        data = json.loads(Path(words_path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    start = decision.get("window_in_s")
    end = decision.get("window_out_s")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return ""
    texts = []
    for segment in data.get("segments") or []:
        for word in segment.get("words") or []:
            text = str(word.get("word", "")).strip()
            if not text:
                continue
            word_start = word.get("start")
            word_end = word.get("end")
            if not isinstance(word_start, (int, float)) or not isinstance(word_end, (int, float)):
                continue
            if start <= (word_start + word_end) / 2.0 < end:
                texts.append(text)
    return " ".join(texts)


def build_cut_preview(workspace, decision, cache):
    """Cut the decision's span plus one second each side into a cached WAV."""
    document = json.loads((Path(workspace) / "edit.json").read_text(encoding="utf-8"))
    sources = document.get("sources") if isinstance(document, dict) else None
    clip = decision.get("clip")
    source = next(
        (row for row in sources or [] if isinstance(row, dict) and row.get("file") == clip),
        None,
    )
    if source is None:
        raise ValueError("source not found")
    media = Path(str(source.get("path") or ""))
    if not media.is_file():
        raise ValueError("media not found")
    start = decision.get("window_in_s")
    end = decision.get("window_out_s")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        raise ValueError("bad window")
    span_start = max(0.0, float(start) - 1.0)
    span_end = float(end) + 1.0
    duration = source.get("duration_s")
    if isinstance(duration, (int, float)) and duration > 0:
        span_end = min(span_end, float(duration))
    if span_end <= span_start:
        raise ValueError("empty span")
    cache = Path(cache)
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_name(cache.name + ".part")
    command = [
        "ffmpeg", "-y",
        "-ss", f"{span_start:.3f}",
        "-i", str(media),
        "-t", f"{span_end - span_start:.3f}",
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        "-f", "wav",
        str(temporary),
    ]
    try:
        result = subprocess.run(command, capture_output=True)
        if result.returncode != 0 or not temporary.is_file():
            raise ValueError("ffmpeg failed")
        os.replace(temporary, cache)
    finally:
        if temporary.exists():
            temporary.unlink()


def exact_length(value):
    """A typed length in whole seconds, or None when the box is empty or out of range."""
    try:
        seconds = int(round(float((value or "").strip().replace(",", "."))))
    except ValueError:
        return None
    if LENGTH_MIN_S <= seconds <= LENGTH_MAX_S:
        return seconds
    return None


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
    length = exact_length(fields.get("length_s"))
    if length is None:
        length = LENGTH_CHOICES.get(fields.get("length") or "auto")
    if length is not None and (length or fields.get("length") == "all"):
        command += ["--target-duration-s", str(length)]
    order = ORDER_CHOICES.get(fields.get("order") or "auto")
    if order:
        command += ["--order", order]
    if (fields.get("audio_clean") or "auto") == "no":
        command.append("--no-audio-clean")
    frame_format = FORMAT_CHOICES.get(fields.get("format") or "auto")
    if frame_format:
        command += ["--format", frame_format]
    if (fields.get("speed_ramp") or "auto") == "on":
        command.append("--speed-ramp")
    music = fields.get("music") or "auto"
    if music == "auto":
        command += ["--music", "auto"]
        if music_dir is not None:
            command += ["--music-dir", str(Path(music_dir).expanduser().resolve())]
    elif music and music != "none" and music_dir is not None:
        command += ["--music", str(Path(music_dir).expanduser().resolve() / Path(music).name)]
    ambient = fields.get("ambient_under_music_db") or "auto"
    if ambient in AMBIENT_CHOICES and ambient != "auto":
        command += ["--ambient-under-music-db", ambient]
    feedback = (fields.get("feedback") or "").strip()
    if feedback:
        command += ["--feedback", feedback[:BRIEF_MAX_CHARS]]
    if fields.get("submit") == "reedit":
        command.append("--new-edit")
    return command


def start_recap(command, workspace, source, options):
    """Spawn the recap in the background, record ``.run.json`` and return the pid."""
    workspace.mkdir(parents=True, exist_ok=True)
    with (workspace / "run.log").open("ab") as log:
        log.write(f"\n{RUN_MARKER} {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n".encode("utf-8"))
        log.flush()
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
    threading.Thread(target=handle.wait, daemon=True).start()
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
        if len(segments) == 2 and segments[1] == "expectations":
            self.handle_expectations(segments[0])
            return
        if len(segments) == 2 and segments[1] == "cut-review":
            self.handle_cut_review(segments[0])
            return
        if len(segments) == 2 and segments[1] == "shorts-candidates":
            self.handle_shorts_candidates(segments[0])
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
                self.server.music_dir is not None
                and (Path(self.server.music_dir).expanduser() / Path(music).name).is_file()
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

    def handle_expectations(self, project):
        """Append one phone-review expectation to the workspace's expectations.yaml."""
        workspace = self.server.root / project
        if not workspace.is_dir() or not is_project(workspace):
            self.send_error_text(404, "unknown project")
            return
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            self.send_error_text(400, "expected application/x-www-form-urlencoded")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > self.server.max_total_bytes:
            self.send_error_text(413, "request body too large")
            return
        fields = parse_urlencoded(self.rfile.read(length))
        try:
            shot = validate_shot(fields.get("shot"))
            kind = validate_kind(fields.get("kind"))
            note = validate_note(fields.get("note"))
        except UploadError as exc:
            self.send_error_text(exc.status, exc.message)
            return
        try:
            append_expectation(workspace / EXPECTATIONS_FILE, kind, shot, note)
        except (OSError, ValueError):
            self.send_error_text(400, "unreadable expectations")
            return
        self.send_json({"ok": True, "shot": shot, "kind": kind, "file": EXPECTATIONS_FILE})

    def handle_cut_review(self, project):
        """Record one phone cut review tap in the workspace's cut_review.yaml."""
        workspace = self.server.root / project
        if not workspace.is_dir() or not is_project(workspace):
            self.send_error_text(404, "unknown project")
            return
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            self.send_error_text(400, "expected application/x-www-form-urlencoded")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > self.server.max_total_bytes:
            self.send_error_text(413, "request body too large")
            return
        fields = parse_urlencoded(self.rfile.read(length))
        try:
            cut_id = validate_cut_id(fields.get("id"))
            action = validate_cut_action(fields.get("action"))
            note = validate_note(fields.get("note"))
        except UploadError as exc:
            self.send_error_text(exc.status, exc.message)
            return
        decision = next((row for row in cut_decisions(workspace) if row["id"] == cut_id), None)
        if decision is None:
            self.send_error_text(400, "unknown cut")
            return
        try:
            append_cut_review(
                workspace / CUT_REVIEW_FILE,
                cut_id,
                action,
                note,
                decision.get("clip"),
                decision.get("window_in_s"),
                decision.get("window_out_s"),
            )
        except (OSError, ValueError):
            self.send_error_text(400, "unreadable cut review")
            return
        self.send_json({"ok": True, "id": cut_id, "action": action, "file": CUT_REVIEW_FILE})

    def handle_shorts_candidates(self, project):
        """Rewrite one Shorts candidate's layout in the workspace's shorts_candidates.json."""
        workspace = self.server.root / project
        if not workspace.is_dir() or not is_project(workspace):
            self.send_error_text(404, "unknown project")
            return
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            self.send_error_text(400, "expected application/x-www-form-urlencoded")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > self.server.max_total_bytes:
            self.send_error_text(413, "request body too large")
            return
        fields = parse_urlencoded(self.rfile.read(length))
        try:
            rank = validate_rank(fields.get("rank"))
            layout = validate_layout(fields.get("layout"))
        except UploadError as exc:
            self.send_error_text(exc.status, exc.message)
            return
        path = workspace / SHORTS_CANDIDATES_FILE
        if not path.is_file():
            self.send_error_text(400, "no shorts candidates")
            return
        if not any(row.get("rank") == rank for row in load_shorts_candidates(workspace)):
            self.send_error_text(400, "unknown rank")
            return
        if layout == "split" and "split" not in shorts_layout_options(workspace):
            self.send_error_text(400, "split is not available for a camera-only project")
            return
        try:
            set_candidate_layout(path, rank, layout)
        except (OSError, ValueError):
            self.send_error_text(400, "unreadable shorts candidates")
            return
        self.send_json({"ok": True, "rank": rank, "layout": layout})

    def handle_cut_audio(self, project, send_body):
        """Serve a short WAV preview of one cut span, cached under cut_previews/."""
        workspace = self.server.root / project
        if not workspace.is_dir() or not is_project(workspace):
            self.send_error_text(404, "unknown project")
            return
        values = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("id", [])
        raw_id = (values[0] if values else "").strip()
        if not CUT_ID_RE.match(raw_id):
            self.send_error_text(400, "invalid cut id")
            return
        decision = next((row for row in cut_decisions(workspace) if row["id"] == raw_id), None)
        if decision is None:
            self.send_error_text(404, "unknown cut")
            return
        cache = workspace / CUT_PREVIEW_DIR / f"{raw_id}.wav"
        if not cache.is_file():
            try:
                build_cut_preview(workspace, decision, cache)
            except (OSError, ValueError):
                self.send_error_text(404, "cannot build the preview")
                return
        self.send_wav(cache, send_body)

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

    def send_json(self, payload):
        body = (json.dumps(payload) + "\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
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
                    segments[0],
                    directory,
                    parse_tab(self.path),
                    list_music_tracks(self.server.music_dir),
                    wanted=parse_number(self.path, "v"),
                    compare_with=parse_number(self.path, "with"),
                ),
                send_body,
            )
            return
        if len(segments) == 2 and segments[1] == "cut-audio":
            self.handle_cut_audio(segments[0], send_body)
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
            if artifact == "shotlog.html":
                self.send_html(shotlog_with_edit(target, edit_decisions(directory)), send_body)
                return
            self.send_file(target, send_body)
            return
        if len(segments) == 4 and segments[1] == "versions" and VERSION_RE.match(segments[2]):
            artifact = segments[3]
            directory = root / segments[0]
            target = directory / "versions" / segments[2] / artifact
            if artifact not in SERVED_FILES or not is_project(directory) or not target.is_file():
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

    def send_wav(self, path, send_body):
        data = Path(path).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if send_body:
            self.wfile.write(data)


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


def build_server(root, bind, port, music_dir=None, inbox=None,
                 max_file_bytes=None, max_total_bytes=None):
    """Bind ``root`` at ``bind:port`` and return the ready server."""
    return WorkspaceServer(
        (bind, port),
        root,
        music_dir,
        Path(inbox).expanduser().resolve() if inbox is not None else None,
        max_file_bytes if max_file_bytes is not None else DEFAULT_MAX_UPLOAD_MB * 1024 * 1024,
        max_total_bytes if max_total_bytes is not None else DEFAULT_MAX_TOTAL_MB * 1024 * 1024,
    )


def build_summary(root, bind, port, projects, status, started, music_dir=None,
                  inbox=None):
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
                "vertical": f"{base}/{name}/{VERTICAL_PREVIEW}"
                if VERTICAL_PREVIEW in artifacts
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
        "music_library": str(Path(music_dir).expanduser()) if music_dir else None,
        "inbox": str(Path(inbox).expanduser().resolve()) if inbox else None,
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
        inbox = inbox_path(args.inbox)
        if inbox is None:
            if args.check:
                print(f"[warn] {no_inbox_message('--inbox')}", file=sys.stderr)
            else:
                raise ServeError(no_inbox_message("--inbox"))
        else:
            inbox = inbox.resolve()
        music_dir = music_library(args.music_dir)
        if music_dir is None:
            print(f"[warn] {no_music_library_message('--music-dir')}", file=sys.stderr)
        try:
            server = build_server(
                root,
                bind,
                args.port,
                music_dir,
                inbox,
                args.max_upload_mb * 1024 * 1024,
                args.max_total_mb * 1024 * 1024,
            )
        except OSError as exc:
            raise ServeRuntimeError(f"could not bind {bind}:{args.port}: {exc}") from exc
    except ServeRuntimeError as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    except (ServeError, ConfigError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)

    port = server.server_address[1]
    projects = discover_projects(root)
    summary = build_summary(
        root, bind, port, projects, "check" if args.check else "serving", started,
        music_dir, inbox,
    )
    if args.json:
        print(json.dumps(summary), flush=True)
    else:
        print_human(summary)
        sys.stdout.flush()

    if args.check:
        server.server_close()
        return EXIT_OK

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
