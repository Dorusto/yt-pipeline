#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run every pipeline station from a folder of clips and a brief to a recap MP4.

Usage:
    python scripts/recap.py --source <source-folder> --brief "about 90 seconds, calm start"
    python scripts/recap.py --source <source-folder> --brief-file brief.txt --final --json
    python scripts/recap.py --source <source-folder> --brief "..." --feedback "drop 0071/s2" --json

``recap.py`` is the single entry point: it chains the existing station CLIs as
subprocesses in order (proxy, ingest, motion, quality, describe, similarity,
index, router, llm_director, render, and with ``--final`` the final render and
the Kdenlive export), skips stations whose outputs are already up to date,
prints a start line and one progress line per station and a ``--json`` summary
with the wall time and the total LLM cost. It imports no other tool and calls
no model itself.
Originals are read-only; everything is written under the workspace.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from common import notify, versions
from common.config import ConfigError, music_library, no_music_library_message
from common.media import video_files
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = "scripts/recap.py"


class RecapError(RuntimeError):
    """Raised when a recap run cannot be started or completed."""


class Context:
    """Paths and flags shared by every station in one recap run."""

    def __init__(
        self,
        source,
        workspace,
        brief_text,
        final,
        budget_usd,
        force,
        json_mode,
        python,
        repo_root,
        music=None,
        ambient_under_music_db=None,
        notify=False,
        preview_base=None,
        feedback=None,
        music_dir=None,
        target_duration_s=None,
        order=None,
        no_audio_clean=False,
        frame_format="16x9",
        speed_ramp=False,
    ):
        self.frame_format = frame_format
        self.speed_ramp = speed_ramp
        self.source = source
        self.workspace = workspace
        self.brief_text = brief_text
        self.final = final
        self.budget_usd = budget_usd
        self.force = force
        self.json_mode = json_mode
        self.python = python
        self.repo_root = repo_root
        self.music = music
        self.ambient_under_music_db = ambient_under_music_db
        self.notify = notify
        self.preview_base = preview_base
        self.feedback = list(feedback or [])
        self.music_dir = music_dir
        self.target_duration_s = target_duration_s
        self.order = order
        self.no_audio_clean = no_audio_clean


def build_parser():
    parser = argparse.ArgumentParser(
        prog="recap.py",
        usage=(
            "recap.py [-h] --source SOURCE (--brief BRIEF | --brief-file BRIEF_FILE)"
            " [--workspace WORKSPACE] [--final] [--budget-usd BUDGET_USD]"
            " [--music MUSIC] [--format {16x9,9x16,both}] [--speed-ramp] [--feedback TEXT] [--force] [--notify]"
            " [--preview-base PREVIEW_BASE] [--json]"
        ),
        description=(
            "Run every pipeline station from a folder of clips and a brief to a recap MP4."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/recap.py --source <source-folder> --brief \"about 90 seconds\"\n"
            "  python scripts/recap.py --source <source-folder> --brief-file brief.txt"
            " --final --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        required=True,
        help="folder of original clips (non-recursive, read-only)",
    )
    parser.add_argument("--brief", help="the brief in plain language")
    parser.add_argument("--brief-file", help="path to a file holding the brief")
    parser.add_argument(
        "--workspace",
        help="workspace folder (default: <source parent>/workspace)",
    )
    parser.add_argument(
        "--final",
        action="store_true",
        help="also render the final from the originals and export Kdenlive",
    )
    parser.add_argument(
        "--budget-usd",
        type=float,
        default=None,
        help="LLM budget forwarded to describe and llm_director",
    )
    parser.add_argument(
        "--music",
        default=None,
        help=(
            "music track: a file to detect beats and mix under the recap, or 'auto' to pick "
            "one from the music library (found via --music-dir, else YT_PIPELINE_MUSIC_LIBRARY, "
            "else music.library in the config)"
        ),
    )
    parser.add_argument(
        "--music-dir",
        default=None,
        help=(
            "folder of tracks for --music auto (or YT_PIPELINE_MUSIC_LIBRARY, or "
            "music.library in the config; no personal default)"
        ),
    )
    parser.add_argument(
        "--target-duration-s",
        type=float,
        default=None,
        help="override the target duration parsed from the brief",
    )
    parser.add_argument(
        "--order",
        choices=["chronological", "score"],
        default=None,
        help="override the order parsed from the brief",
    )
    parser.add_argument(
        "--ambient-under-music-db",
        type=float,
        default=None,
        help="how far the ambient bus sits under the music, in dB (recorded in edit.json)",
    )
    parser.add_argument(
        "--no-audio-clean",
        action="store_true",
        help="keep the raw ambient mix (no wind high-pass, denoise or loudnorm)",
    )
    parser.add_argument(
        "--format",
        dest="frame_format",
        choices=["16x9", "9x16", "both"],
        default="16x9",
        help=(
            "16x9 (default), 9x16 (recap_720p_9x16.mp4, 720x1280, a moving crop that follows"
            " the subject) or both"
        ),
    )
    parser.add_argument(
        "--speed-ramp",
        action="store_true",
        help=(
            "optional effect: let the middle half of a long camera move play up to 1.2x so more of the "
            "move fits (default off: normal speed everywhere)"
        ),
    )
    parser.add_argument(
        "--feedback",
        action="append",
        default=None,
        metavar="TEXT",
        help=(
            "feedback in plain words against the current edit's short ids, "
            'e.g. "drop 0071/s2, tunnel longer"; repeatable; re-runs only '
            "llm_director and render"
        ),
    )
    parser.add_argument(
        "--new-edit",
        action="store_true",
        help="make a new edit even when the brief and the options did not change (re-runs llm_director, explain and the renders)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-run every station",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="post the preview link to ntfy after the run",
    )
    parser.add_argument(
        "--preview-base",
        default=None,
        help="base URL of the serve.py server used to build the preview link",
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def is_within(path, directory):
    path = Path(path).expanduser().resolve()
    directory = Path(directory).expanduser().resolve()
    return path == directory or directory in path.parents


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_newer(path, reference):
    path = Path(path)
    reference = Path(reference)
    if not path.is_file() or not reference.is_file():
        return False
    return path.stat().st_mtime > reference.stat().st_mtime


def all_newer(output, inputs):
    output = Path(output)
    if not output.is_file() or not inputs:
        return False
    out_mtime = output.stat().st_mtime
    return all(
        Path(item).is_file() and Path(item).stat().st_mtime < out_mtime for item in inputs
    )


def recorded_brief_matches(path, brief_text):
    data = load_json(path)
    if not isinstance(data, dict):
        return False
    brief = data.get("brief")
    if not isinstance(brief, dict):
        return False
    return brief.get("text") == brief_text


def recorded_speed_ramp_matches(path, speed_ramp):
    """True when the edit at ``path`` was built with the same speed-ramp setting."""
    data = load_json(path)
    if not isinstance(data, dict):
        return False
    meta = data.get("meta")
    recorded = bool(meta.get("speed_ramp", False)) if isinstance(meta, dict) else False
    return recorded == bool(speed_ramp)


def directory_files(directory):
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return [path for path in directory.iterdir() if path.is_file()]


def is_up_to_date(name, ctx):
    """Return True when a station's outputs are newer than its inputs."""
    ws = ctx.workspace
    src = ctx.source
    proxy_dir = ws / "proxy"
    proxies = video_files(proxy_dir)
    if name == "proxy":
        clips = video_files(src)
        return bool(clips) and all((proxy_dir / clip.name).is_file() for clip in clips)
    if name == "ingest":
        return all_newer(ws / "ingest.json", proxies)
    if name == "beats":
        return ctx.music is not None and ctx.music != "auto" and is_newer(ws / "beats.json", ctx.music)
    if name == "music_pick":
        choice = ws / MUSIC_CHOICE_NAME
        if not choice.is_file():
            return False
        if not is_newer(choice, ws / "shotlog.json"):
            return False
        chosen = resolve_music_choice(ws)
        return chosen is not None and is_newer(ws / "beats.json", chosen)
    if name == "motion":
        return bool(proxies) and all(
            is_newer(ws / "motion" / f"{clip.stem}.json", clip) for clip in proxies
        )
    if name == "quality":
        return bool(proxies) and all(
            is_newer(ws / "quality" / f"{clip.stem}.json", clip)
            and is_newer(
                ws / "quality" / f"{clip.stem}.json",
                ws / "motion" / f"{clip.stem}.json",
            )
            for clip in proxies
        )
    if name == "describe":
        return bool(proxies) and all(
            is_newer(
                ws / "describe" / f"{clip.stem}.json",
                ws / "quality" / f"{clip.stem}.json",
            )
            for clip in proxies
        )
    if name == "similarity":
        return all_newer(ws / "similarity.json", directory_files(ws / "quality"))
    if name == "index":
        inputs = []
        for sub in ("motion", "quality", "describe"):
            files = directory_files(ws / sub)
            if not files:
                return False
            inputs.extend(files)
        if not (ws / "similarity.json").is_file():
            return False
        inputs.append(ws / "similarity.json")
        return all_newer(ws / "shotlog.json", inputs)
    if name == "router":
        path = ws / "route.json"
        return recorded_brief_matches(path, ctx.brief_text) and is_newer(
            path, ws / "shotlog.json"
        )
    if name == "llm_director":
        path = ws / "edit.json"
        return (
            recorded_brief_matches(path, ctx.brief_text)
            and recorded_speed_ramp_matches(path, ctx.speed_ramp)
            and is_newer(path, ws / "shotlog.json")
        )
    if name == "explain":
        return is_newer(ws / "explain.html", ws / "edit.json")
    if name == "render":
        return is_newer(ws / "recap_720p.mp4", ws / "edit.json")
    if name == "render_vertical":
        return is_newer(ws / VERTICAL_PREVIEW_NAME, ws / "edit.json")
    if name == "render_final":
        return is_newer(ws / "recap_final.mp4", ws / "edit.json")
    if name == "render_final_vertical":
        return is_newer(ws / VERTICAL_FINAL_NAME, ws / "edit.json")
    if name == "export_kdenlive":
        return is_newer(ws / "recap.kdenlive", ws / "edit.json")
    return False


def station_command(ctx, script, args, force=False, budget=False):
    command = [ctx.python, f"scripts/{script}", *args]
    if force:
        command.append("--force")
    if budget and ctx.budget_usd is not None:
        command += ["--budget-usd", str(ctx.budget_usd)]
    command.append("--json")
    return command


INDEPENDENT_RENDERS = ("render", "render_vertical", "render_final", "render_final_vertical")
VERTICAL_PREVIEW_NAME = "recap_720p_9x16.mp4"
VERTICAL_FINAL_NAME = "recap_final_9x16.mp4"
MUSIC_TOKEN = "@music@"
MUSIC_CHOICE_NAME = "music_choice.json"


def music_arg(ctx):
    """The ``--music`` value for a station, or None when no track is configured."""
    if ctx.music is None:
        return None
    return MUSIC_TOKEN if ctx.music == "auto" else str(ctx.music)


def resolve_music_choice(workspace):
    """The chosen track path from WORKSPACE/music_choice.json, or None."""
    data = load_json(Path(workspace) / MUSIC_CHOICE_NAME)
    if not isinstance(data, dict):
        return None
    path = data.get("path")
    return Path(path).expanduser().resolve() if path else None


def resolve_auto_music(ctx):
    """Point ``ctx.music`` at the chosen track; False when the choice is unreadable."""
    chosen = resolve_music_choice(ctx.workspace)
    if chosen is None:
        return False
    ctx.music = chosen
    return True


def resolve_argv(argv, ctx):
    """Substitute the ``@music@`` token with the resolved track path."""
    value = str(ctx.music) if ctx.music is not None and ctx.music != "auto" else MUSIC_TOKEN

    def substitute(parts):
        return [value if item == MUSIC_TOKEN else item for item in parts]

    if not argv:
        return []
    if isinstance(argv[0], str):
        return substitute(argv)
    return [substitute(command) for command in argv]


def director_args(ctx):
    args = [
        "--workspace",
        str(ctx.workspace),
        "--brief-file",
        str(ctx.workspace / "brief.txt"),
    ]
    for text in ctx.feedback:
        args += ["--feedback", text]
    if ctx.target_duration_s is not None:
        args += ["--target-duration-s", str(ctx.target_duration_s)]
    if ctx.order is not None:
        args += ["--order", ctx.order]
    if ctx.music is not None:
        args += ["--beats", str(ctx.workspace / "beats.json"), "--music", music_arg(ctx)]
    if ctx.ambient_under_music_db is not None:
        args += ["--ambient-under-music-db", str(ctx.ambient_under_music_db)]
    if ctx.speed_ramp:
        args.append("--speed-ramp")
    return args


def render_args(ctx, out_path):
    args = ["--edit", str(ctx.workspace / "edit.json"), "--out", str(out_path)]
    if ctx.music is not None:
        args += ["--music", music_arg(ctx)]
    if ctx.ambient_under_music_db is not None:
        args += ["--ambient-under-music-db", str(ctx.ambient_under_music_db)]
    if ctx.no_audio_clean:
        args.append("--no-audio-clean")
    return args


def build_stations(ctx):
    """Return the ordered station specs for this run, each with its command list."""
    ws = ctx.workspace
    src = ctx.source
    proxy_dir = ws / "proxy"
    force = ctx.force
    stations = [
        {
            "name": "proxy",
            "argv": [
                station_command(
                    ctx, "proxy.py", [str(src), "--out", str(proxy_dir)], force=force
                )
            ],
        },
        {
            "name": "ingest",
            "argv": [
                station_command(ctx, "ingest.py", [str(proxy_dir), "--workspace", str(ws)])
            ],
        },
        {
            "name": "motion",
            "argv": [
                station_command(
                    ctx, "motion.py", [str(proxy_dir), "--workspace", str(ws)], force=force
                )
            ],
        },
        {
            "name": "quality",
            "argv": [
                station_command(
                    ctx, "quality.py", [str(proxy_dir), "--workspace", str(ws)], force=force
                )
            ],
        },
        {
            "name": "describe",
            "argv": [
                station_command(
                    ctx, "describe.py", ["--workspace", str(ws)], force=force, budget=True
                )
            ],
        },
        {
            "name": "similarity",
            "argv": [
                station_command(
                    ctx, "similarity.py", ["--workspace", str(ws)], force=force
                )
            ],
        },
        {
            "name": "index",
            "argv": [
                station_command(ctx, "index.py", ["--workspace", str(ws)], force=force)
            ],
        },
        {
            "name": "router",
            "argv": [
                station_command(
                    ctx,
                    "router.py",
                    ["--brief-file", str(ws / "brief.txt"), "--workspace", str(ws)],
                    force=force,
                )
            ],
        },
        {
            "name": "llm_director",
            "argv": [
                station_command(
                    ctx,
                    "llm_director.py",
                    director_args(ctx),
                    force=force or getattr(ctx, "new_edit", False),
                    budget=True,
                )
            ],
        },
    ]
    stations.append(
        {
            "name": "explain",
            "argv": [station_command(ctx, "explain.py", ["--workspace", str(ws)], force=force)],
        }
    )
    if ctx.frame_format in ("16x9", "both"):
        stations.append(
            {
                "name": "render",
                "argv": [
                    station_command(
                        ctx, "render.py", render_args(ctx, ws / "recap_720p.mp4"), force=force
                    )
                ],
            }
        )
    if ctx.frame_format in ("9x16", "both"):
        stations.append(
            {
                "name": "render_vertical",
                "argv": [
                    station_command(
                        ctx,
                        "render.py",
                        render_args(ctx, ws / VERTICAL_PREVIEW_NAME) + ["--format", "9x16"],
                        force=force,
                    )
                ],
            }
        )
    if ctx.music == "auto":
        pick_args = [
            "--brief-file",
            str(ws / "brief.txt"),
            "--workspace",
            str(ws),
        ]
        if ctx.music_dir is not None:
            pick_args = ["--library", str(ctx.music_dir)] + pick_args
        if ctx.target_duration_s:
            pick_args += ["--target-duration-s", str(ctx.target_duration_s)]
        index_at = next(i for i, station in enumerate(stations) if station["name"] == "index")
        stations.insert(
            index_at + 1,
            {
                "name": "music_pick",
                "argv": [station_command(ctx, "music_pick.py", pick_args, force=force)],
            },
        )
    elif ctx.music is not None:
        stations.insert(
            2,
            {
                "name": "beats",
                "argv": [
                    station_command(
                        ctx,
                        "beats.py",
                        [str(ctx.music), "--out", str(ws / "beats.json")],
                        force=force,
                    )
                ],
            },
        )
    if ctx.final:
        if ctx.frame_format in ("16x9", "both"):
            stations.append(
                {
                    "name": "render_final",
                    "argv": [
                        station_command(
                            ctx,
                            "render.py",
                            render_args(ctx, ws / "recap_final.mp4")
                            + ["--originals-dir", str(src)],
                            force=force,
                        )
                    ],
                }
            )
        if ctx.frame_format in ("9x16", "both"):
            stations.append(
                {
                    "name": "render_final_vertical",
                    "argv": [
                        station_command(
                            ctx,
                            "render.py",
                            render_args(ctx, ws / VERTICAL_FINAL_NAME)
                            + ["--originals-dir", str(src), "--format", "9x16"],
                            force=force,
                        )
                    ],
                }
            )
        kdenlive_args = [str(ws / "fragments.yaml")]
        if shutil.which("melt") is None:
            kdenlive_args.append("--skip-melt")
        stations.append(
            {
                "name": "export_kdenlive",
                "argv": [
                    station_command(
                        ctx,
                        "edit_to_fragments.py",
                        [
                            "--edit",
                            str(ws / "edit.json"),
                            "--out",
                            str(ws / "fragments.yaml"),
                            "--source-dir",
                            str(src),
                            "--kdenlive-out",
                            str(ws / "recap.kdenlive"),
                        ],
                        force=force,
                    ),
                    station_command(ctx, "kdenlive_from_fragments.py", kdenlive_args),
                ],
            }
        )
    return stations


def normalize_commands(argv):
    if not argv:
        return []
    if isinstance(argv[0], str):
        return [argv]
    return list(argv)


def parse_child_json(stdout):
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def execute(station, argv, ctx):
    """Run one station as a subprocess and return {"status", "wall_time_s", "llm_cost_usd", "detail"}."""
    started = time.monotonic()
    returncode = 0
    stdout = ""
    stderr = ""
    for command in normalize_commands(resolve_argv(argv, ctx)):
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                cwd=str(ctx.repo_root),
            )
        except OSError as exc:
            return {
                "status": "FAIL",
                "wall_time_s": round(time.monotonic() - started, 3),
                "llm_cost_usd": 0.0,
                "detail": "exit 1",
                "returncode": 1,
                "stderr": str(exc),
            }
        returncode = result.returncode
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if returncode != 0:
            break
    wall_time_s = round(time.monotonic() - started, 3)
    if returncode != 0:
        return {
            "status": "FAIL",
            "wall_time_s": wall_time_s,
            "llm_cost_usd": 0.0,
            "detail": f"exit {returncode}",
            "returncode": returncode,
            "stderr": stderr,
        }
    data = parse_child_json(stdout)
    llm_cost_usd = 0.0
    child_status = None
    if isinstance(data, dict):
        value = data.get("llm_cost_usd")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            llm_cost_usd = float(value)
        status_value = data.get("status")
        if isinstance(status_value, str):
            child_status = status_value
    detail = child_status if child_status in ("written", "skipped", "fallback") else "ok"
    return {
        "status": "done",
        "wall_time_s": wall_time_s,
        "llm_cost_usd": llm_cost_usd,
        "detail": detail,
        "returncode": 0,
        "stderr": stderr,
    }


def write_brief(workspace, text):
    """Write ``brief.txt`` atomically, only when its content differs."""
    path = Path(workspace) / "brief.txt"
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == text:
                return path
        except OSError:
            pass
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
    return path


def read_ledger(workspace):
    data = load_json(Path(workspace) / "llm_spend.json")
    if not isinstance(data, dict):
        return 0.0
    value = data.get("total_usd")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


def print_progress(ctx, index, total, name, status, wall_time_s, detail):
    line = f"[{index:2d}/{total:2d}] {name:<15} {status:<5} {wall_time_s:8.1f}s  {detail}"
    stream = sys.stderr if ctx.json_mode else sys.stdout
    print(line, file=stream)


def print_start(ctx, index, total, name):
    stream = sys.stderr if ctx.json_mode else sys.stdout
    print(f"[{index:2d}/{total:2d}] {name:<15} start", file=stream, flush=True)


def print_failure(name, result):
    code = result.get("returncode")
    if code is None:
        detail = result.get("detail") or ""
        code = detail.split()[-1] if detail.startswith("exit ") else "?"
    print(f"[error] {name} failed (exit {code})", file=sys.stderr)
    lines = (result.get("stderr") or "").strip().splitlines()
    for line in lines[-20:]:
        print(line, file=sys.stderr)


def build_preview_url(preview_base, workspace):
    """Return the serve.py preview URL for a workspace, or None without a base."""
    if not preview_base:
        return None
    base = preview_base.rstrip("/")
    name = urllib.parse.quote(Path(workspace).name)
    return f"{base}/{name}/recap_720p.mp4"


def notify_preview(ctx, summary):
    """Post the recap preview link to ntfy; return True when it was sent."""
    lines = [f"Recap ready: {ctx.workspace}"]
    if summary["preview_url"]:
        lines.append(f"Preview: {summary['preview_url']}")
    all_skipped = all(entry["status"] == "skip" for entry in summary["stations"])
    title = "Recap ready (cached)" if all_skipped else "Recap ready (720p)"
    try:
        notify.send("\n".join(lines), title=title)
    except notify.NotifyError as exc:
        print(f"[warn] notify failed: {exc}", file=sys.stderr)
        return False
    return True


def run(ctx):
    """Run the station chain and return the flat summary object."""
    started = time.monotonic()
    write_brief(ctx.workspace, ctx.brief_text)
    if not versions.version_dirs(ctx.workspace):
        versions.snapshot(ctx.workspace, {"note": "kept before this run"}, keep_renders=True)
    stations = build_stations(ctx)
    total = len(stations)
    entries = []
    failed_name = None
    for index, station in enumerate(stations, start=1):
        name = station["name"]
        rerun = name in ("llm_director", "explain", "render", "render_vertical") and (
            ctx.feedback or getattr(ctx, "new_edit", False)
        )
        if not ctx.force and not rerun and is_up_to_date(name, ctx):
            if name == "music_pick" and not resolve_auto_music(ctx):
                failed_name = name
                print_failure(name, {"stderr": f"no readable {MUSIC_CHOICE_NAME} in {ctx.workspace}"})
                break
            entries.append(
                {
                    "name": name,
                    "status": "skip",
                    "wall_time_s": 0.0,
                    "llm_cost_usd": 0.0,
                    "detail": "up to date",
                }
            )
            print_progress(ctx, index, total, name, "skip", 0.0, "up to date")
            continue
        print_start(ctx, index, total, name)
        result = execute(station, station["argv"], ctx)
        if name == "music_pick" and result["status"] != "FAIL" and not resolve_auto_music(ctx):
            result = {
                "status": "FAIL",
                "wall_time_s": result["wall_time_s"],
                "llm_cost_usd": result["llm_cost_usd"],
                "detail": "no music_choice.json",
                "returncode": 1,
                "stderr": f"no readable {MUSIC_CHOICE_NAME} in {ctx.workspace}",
            }
        entries.append(
            {
                "name": name,
                "status": result["status"],
                "wall_time_s": result["wall_time_s"],
                "llm_cost_usd": result["llm_cost_usd"],
                "detail": result["detail"],
            }
        )
        print_progress(
            ctx,
            index,
            total,
            name,
            result["status"],
            result["wall_time_s"],
            result["detail"],
        )
        if result["status"] == "FAIL":
            failed_name = failed_name or name
            print_failure(name, result)
            if name not in INDEPENDENT_RENDERS:
                break
    version = keep_version(ctx, entries, failed_name)
    wall_time_s = round(time.monotonic() - started, 3)
    llm_cost_usd = round(sum(entry["llm_cost_usd"] for entry in entries), 8)
    outputs = {
        "preview": str(ctx.workspace / "recap_720p.mp4"),
        "preview_vertical": (
            str(ctx.workspace / VERTICAL_PREVIEW_NAME) if ctx.frame_format != "16x9" else None
        ),
        "final": str(ctx.workspace / "recap_final.mp4") if ctx.final else None,
        "final_vertical": (
            str(ctx.workspace / VERTICAL_FINAL_NAME)
            if ctx.final and ctx.frame_format != "16x9"
            else None
        ),
        "kdenlive": str(ctx.workspace / "recap.kdenlive") if ctx.final else None,
    }
    summary = {
        "tool": TOOL,
        "status": "failed" if failed_name else "done",
        "source": str(ctx.source),
        "workspace": str(ctx.workspace),
        "brief": ctx.brief_text,
        "final": ctx.final,
        "stations": entries,
        "outputs": outputs,
        "wall_time_s": wall_time_s,
        "llm_cost_usd": llm_cost_usd,
        "llm_spend_usd": read_ledger(ctx.workspace),
        "preview_url": build_preview_url(ctx.preview_base, ctx.workspace),
        "notified": False,
        "version": version,
    }
    return notify_summary(ctx, summary)


EDIT_STATIONS = ("llm_director", *INDEPENDENT_RENDERS)


def keep_version(ctx, entries, failed_name):
    """Snapshot the edit and its renders when this run made a new one; return the version name."""
    made = [entry["name"] for entry in entries if entry["name"] in EDIT_STATIONS and entry["status"] != "skip"]
    if not made:
        return None
    meta = {
        "brief": ctx.brief_text,
        "feedback": list(ctx.feedback),
        "target_duration_s": ctx.target_duration_s,
        "format": ctx.frame_format,
        "speed_ramp": ctx.speed_ramp,
        "status": "failed" if failed_name else "done",
        "failed_at": failed_name,
    }
    try:
        directory = versions.snapshot(ctx.workspace, meta)
    except OSError as exc:
        print(f"[warn] could not keep this version: {exc}", file=sys.stderr)
        return None
    return directory.name if directory else None


def notify_summary(ctx, summary):
    """Notify after a successful run and return the updated summary."""
    if ctx.notify and summary["status"] != "failed":
        summary["notified"] = notify_preview(ctx, summary)
    return summary


def print_human(summary):
    if summary["status"] == "failed":
        failed = next(
            (entry for entry in reversed(summary["stations"]) if entry["status"] == "FAIL"),
            None,
        )
        name = failed["name"] if failed else "?"
        print(
            f"recap: failed at {name} after {summary['wall_time_s']:.1f}s,"
            f" llm cost ${summary['llm_cost_usd']:.4f}"
        )
        return
    print(
        f"recap: done in {summary['wall_time_s']:.1f}s,"
        f" llm cost ${summary['llm_cost_usd']:.4f}"
        f" (ledger ${summary['llm_spend_usd']:.4f})"
    )
    print(f"preview: {summary['outputs']['preview']}")
    if summary["outputs"]["final"]:
        print(f"final: {summary['outputs']['final']}")
    if summary["outputs"]["kdenlive"]:
        print(f"kdenlive: {summary['outputs']['kdenlive']}")


def resolve_brief(args):
    if args.brief is not None and args.brief_file is not None:
        raise RecapError("give either --brief or --brief-file, not both")
    if args.brief is None and args.brief_file is None:
        raise RecapError("provide --brief or --brief-file")
    if args.brief_file is not None:
        path = Path(args.brief_file).expanduser()
        if not path.is_file():
            raise RecapError(f"brief file not found: {path}")
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RecapError(f"could not read brief file: {exc}") from exc
    return args.brief


def resolve_workspace(value, source):
    if value is None:
        return (source.parent / "workspace").resolve()
    return Path(value).expanduser().resolve()


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        source = Path(args.source).expanduser().resolve()
        if not source.exists():
            raise RecapError(f"source not found: {source}")
        if not source.is_dir():
            raise RecapError(f"source is not a directory: {source}")
        if not video_files(source):
            raise RecapError(f"no video files in {source}")
        brief_text = resolve_brief(args)
        workspace = resolve_workspace(args.workspace, source)
        if is_within(workspace, source):
            raise RecapError(f"workspace must not be inside the source: {workspace}")
        workspace.mkdir(parents=True, exist_ok=True)
        music = None
        if args.music:
            if args.music == "auto":
                music = "auto"
            else:
                music = Path(args.music).expanduser().resolve()
                if not music.is_file():
                    raise RecapError(f"music file not found: {music}")
        library = music_library(args.music_dir) if (args.music or args.music_dir) else None
        music_dir = library.resolve() if library is not None else None
        if music == "auto" and music_dir is None:
            raise RecapError(no_music_library_message("--music-dir"))
    except (RecapError, ConfigError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return EXIT_USAGE
    ctx = Context(
        source=source,
        workspace=workspace,
        brief_text=brief_text,
        final=args.final,
        budget_usd=args.budget_usd,
        force=args.force,
        json_mode=args.json,
        python=sys.executable,
        repo_root=REPO_ROOT,
        music=music,
        ambient_under_music_db=args.ambient_under_music_db,
        notify=args.notify,
        preview_base=args.preview_base,
        feedback=args.feedback,
        music_dir=music_dir,
        target_duration_s=args.target_duration_s,
        order=args.order,
        no_audio_clean=args.no_audio_clean,
        frame_format=args.frame_format,
        speed_ramp=args.speed_ramp,
    )
    ctx.new_edit = args.new_edit
    summary = run(ctx)
    if args.json:
        print(json.dumps(summary))
    else:
        print_human(summary)
    return EXIT_RUNTIME if summary["status"] == "failed" else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
