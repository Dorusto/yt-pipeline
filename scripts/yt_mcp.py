#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Serve one plugin's tools over MCP.

Usage:
    yt yt_mcp --plugin <name>
    yt yt_mcp --plugin <name> --json
    yt yt_mcp --help

Expose one plugin's tool group as four typed MCP tools (``yt_list``,
``yt_describe``, ``yt_run``, ``yt_preview``) over stdio. The plugin -> tools
grouping comes from the marketplace and the skills, so the server only sees the
tools of the plugin it was started with.
"""

import argparse
import ast
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver import Image

from common.entry import EntryError, resolve_python, resolve_repo_root
from common.errors import report_error
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.ffmpeg_tools import probe_duration
from common.frames import extract_frame
from common.jsonio import parse_child_json
from common.plugins import (
    plugin_names,
    plugin_tools,
    skill_json_contract,
    tool_skill,
)
from common.summary import summary_fields
from common.versions import POSTER_NAME, RENDER_NAMES, THUMBS_DIR

DEFAULT_TIMEOUT_S = 300.0
DEFAULT_PREVIEW_WIDTH = 640


class PreviewError(RuntimeError):
    """Raised when a preview path cannot be resolved to an image or a video."""


def _tool_summary(root, name):
    """Return the first line of a tool module's docstring, or an empty string."""
    source = (Path(root) / "scripts" / f"{name}.py").read_text(encoding="utf-8")
    docstring = ast.get_docstring(ast.parse(source)) or ""
    return docstring.strip().splitlines()[0].strip() if docstring.strip() else ""


def run_help(root, python, tool):
    """Return the ``--help`` text of one tool (runs ``yt <tool> --help``)."""
    result = subprocess.run(
        [python, str(Path(root) / "scripts" / "yt.py"), tool, "--help"],
        capture_output=True,
        text=True,
    )
    return result.stdout + result.stderr


def run_tool(root, python, tool, args, timeout_s):
    """Run ``yt <tool> <args> --json`` and return the parsed JSON object or a structured error."""
    argv = list(args)
    if "--json" not in argv:
        argv.append("--json")
    try:
        result = subprocess.run(
            [python, str(Path(root) / "scripts" / "yt.py"), tool, *argv],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"{tool} timed out after {timeout_s:g}s",
            "exit_code": EXIT_RUNTIME,
        }
    except OSError as exc:
        return {"ok": False, "error": str(exc), "exit_code": EXIT_RUNTIME}
    payload = parse_child_json(result.stdout)
    if payload is None:
        return {
            "ok": False,
            "error": f"{tool} did not print a JSON object",
            "exit_code": result.returncode or EXIT_RUNTIME,
        }
    return payload


def preview_image(path, time_s=None, width=DEFAULT_PREVIEW_WIDTH):
    """Return an ``Image`` for a workspace folder or a video file."""
    target = Path(path)
    if target.is_dir():
        poster = target / THUMBS_DIR / POSTER_NAME
        if poster.is_file():
            return Image(data=poster.read_bytes(), format="jpeg")
        video = next(
            (
                target / name
                for name in RENDER_NAMES
                if name.endswith(".mp4") and (target / name).is_file()
            ),
            None,
        )
        if video is None:
            raise PreviewError(f"no preview in {path}")
    elif target.is_file():
        video = target
    else:
        raise PreviewError(f"no such path: {path}")
    t_s = time_s if time_s is not None else (probe_duration(video) or 0.0) / 2.0
    handle = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    handle.close()
    try:
        extract_frame(video, t_s, handle.name, width)
        data = Path(handle.name).read_bytes()
    finally:
        Path(handle.name).unlink(missing_ok=True)
    return Image(data=data, format="jpeg")


def build_server(plugin, root=None, timeout_s=DEFAULT_TIMEOUT_S):
    """Build the MCP server exposing one plugin's tools."""
    root = Path(root) if root is not None else resolve_repo_root()
    server = MCPServer("yt-pipeline")

    @server.tool()
    def yt_list() -> str:
        """List the tools of this plugin, one line each."""
        return "\n".join(
            f"{name}: {_tool_summary(root, name)}"
            for name in plugin_tools(root, plugin)
        )

    @server.tool()
    def yt_describe(tool: str) -> dict:
        """Return one tool's --help text, whether it supports --json, and its --json output contract."""
        if tool not in plugin_tools(root, plugin):
            return {
                "ok": False,
                "error": f"tool not in plugin {plugin}: {tool}",
                "exit_code": EXIT_USAGE,
            }
        text = run_help(root, resolve_python(root), tool)
        contract = skill_json_contract(root, tool_skill(root, tool))
        return {
            "ok": True,
            "tool": tool,
            "help": text,
            "json": "--json" in text,
            "contract": contract,
        }

    @server.tool()
    def yt_run(tool: str, args: list[str] | None = None) -> dict:
        """Run one tool with --json and return its JSON object."""
        if tool not in plugin_tools(root, plugin):
            return {
                "ok": False,
                "error": f"tool not in plugin {plugin}: {tool}",
                "exit_code": EXIT_USAGE,
            }
        return run_tool(root, resolve_python(root), tool, args or [], timeout_s)

    @server.tool()
    def yt_preview(path: str, time_s: float | None = None) -> Image:
        """Return a workspace poster or a video frame as an image."""
        return preview_image(path, time_s)

    return server


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="yt_mcp.py",
        description="Serve one plugin's tools over MCP (stdio).",
    )
    parser.add_argument("--plugin", required=True, help="the plugin whose tools to serve")
    parser.add_argument("--json", action="store_true", help="print the tool list and exit")
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help="the yt_run subprocess timeout in seconds",
    )
    args = parser.parse_args(argv)
    started = time.monotonic()
    try:
        root = resolve_repo_root()
    except EntryError as exc:
        return report_error(exc, EXIT_USAGE, json_out=args.json)
    if args.plugin not in plugin_names(root):
        return report_error(
            f"unknown plugin: {args.plugin}", EXIT_USAGE, json_out=args.json
        )
    if args.json:
        payload = {
            "ok": True,
            "plugin": args.plugin,
            "tools": plugin_tools(root, args.plugin),
        }
        payload.update(summary_fields(started))
        print(json.dumps(payload))
        return EXIT_OK
    server = build_server(args.plugin, root=root, timeout_s=args.timeout_s)
    server.run()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
