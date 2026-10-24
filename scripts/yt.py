#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run any yt-pipeline tool from any folder.

Usage:
    yt --help
    yt --list
    yt --json
    yt <tool> [args...]

Run any yt-pipeline tool from any folder. <tool> is a script name in scripts/
without the .py extension (for example: yt doctor --source <folder>).

Options:
    -h, --help   show this help and the tool list
    --list       print the tool names, one per line
    --json       print the resolved root, python and tool list as JSON
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from common.exit_codes import EXIT_OK, EXIT_USAGE
from common.errors import report_error
from common.summary import summary_fields

USAGE = """Usage:
    yt --help
    yt --list
    yt --json
    yt <tool> [args...]

Run any yt-pipeline tool from any folder. <tool> is a script name in scripts/
without the .py extension (for example: yt doctor --source <folder>).

Options:
    -h, --help   show this help and the tool list
    --list       print the tool names, one per line
    --json       print the resolved root, python and tool list as JSON"""


class YtError(RuntimeError):
    """Raised when the entry point cannot resolve the root or the interpreter."""


def resolve_repo_root():
    """Return the repository root, from ``YT_PIPELINE_HOME`` or by walking up from this script."""
    home = os.environ.get("YT_PIPELINE_HOME")
    if home:
        root = Path(home).expanduser().resolve()
        if not (root / "scripts").is_dir() or not (root / "requirements.txt").is_file():
            raise YtError(
                f"YT_PIPELINE_HOME does not hold scripts/ and requirements.txt: {root}"
            )
        return root
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts").is_dir() and (parent / "requirements.txt").is_file():
            return parent
    raise YtError(
        "could not find the repository root; set YT_PIPELINE_HOME to the folder"
        " holding scripts/ and requirements.txt"
    )


def resolve_python(root):
    """Return the interpreter to run the tools with."""
    venv = os.environ.get("YT_PIPELINE_VENV")
    if venv:
        base = Path(venv).expanduser().resolve()
        python = base / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.is_file():
            raise YtError(f"YT_PIPELINE_VENV has no interpreter: {python}")
        return str(python)
    local = root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    if local.is_file():
        return str(local)
    return "python3"


def available_tools(root):
    """Return the sorted tool names in ``root/scripts``."""
    return sorted(path.stem for path in (root / "scripts").glob("*.py"))


def dispatch_tool(root, python, tool, rest):
    """Run ``scripts/<tool>.py`` with ``rest`` and return its exit code."""
    script = root / "scripts" / f"{tool}.py"
    if not script.is_file():
        return report_error(f"unknown tool: {tool}", EXIT_USAGE, json_out=False)
    return subprocess.run([python, str(script), *rest]).returncode


def print_usage(stream):
    """Print the usage block to ``stream``."""
    print(USAGE, file=stream)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    started = time.monotonic()
    if not args:
        print_usage(sys.stderr)
        return EXIT_USAGE
    first = args[0]
    if first in ("-h", "--help"):
        print_usage(sys.stdout)
        print("Tools:")
        try:
            root = resolve_repo_root()
        except YtError as exc:
            return report_error(exc, EXIT_USAGE, json_out=False)
        for name in available_tools(root):
            print(name)
        return EXIT_OK
    if first == "--list":
        try:
            root = resolve_repo_root()
        except YtError as exc:
            return report_error(exc, EXIT_USAGE, json_out=False)
        for name in available_tools(root):
            print(name)
        return EXIT_OK
    if first == "--json":
        if len(args) > 1:
            return report_error(
                f"--json takes no arguments: {args[1]}", EXIT_USAGE, json_out=False
            )
        try:
            root = resolve_repo_root()
            python = resolve_python(root)
        except YtError as exc:
            return report_error(exc, EXIT_USAGE, json_out=False)
        payload = {
            "ok": True,
            "root": str(root),
            "python": python,
            "tools": available_tools(root),
        }
        payload.update(summary_fields(started))
        print(json.dumps(payload))
        return EXIT_OK
    if first.startswith("-"):
        return report_error(f"unknown option: {first}", EXIT_USAGE, json_out=False)
    try:
        root = resolve_repo_root()
        python = resolve_python(root)
    except YtError as exc:
        return report_error(exc, EXIT_USAGE, json_out=False)
    return dispatch_tool(root, python, first, args[1:])


if __name__ == "__main__":
    sys.exit(main())
