#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run any yt-pipeline tool from any folder.

Usage:
    yt --help
    yt --json
    yt --help --json
    yt <tool> [args...]

Run any yt-pipeline tool from any folder. <tool> is a script name in scripts/
without the .py extension (for example: yt doctor --source <folder>).

Options:
    -h, --help   show this help and the tool list
    --json       print the resolved root, python and tool list as JSON
"""

import json
import subprocess
import sys
import time

from common.entry import EntryError, resolve_python, resolve_repo_root
from common.exit_codes import EXIT_OK, EXIT_USAGE
from common.errors import report_error
from common.summary import summary_fields

USAGE = """Usage:
    yt --help
    yt --json
    yt --help --json
    yt <tool> [args...]

Run any yt-pipeline tool from any folder. <tool> is a script name in scripts/
without the .py extension (for example: yt doctor --source <folder>).

Options:
    -h, --help   show this help and the tool list
    --json       print the resolved root, python and tool list as JSON"""


def available_tools(root):
    """Return the sorted tool names in ``root/scripts``."""
    return sorted(path.stem for path in (root / "scripts").glob("*.py"))


def dispatch_tool(root, python, tool, rest, json_out):
    """Run ``scripts/<tool>.py`` with ``rest`` and return its exit code."""
    script = root / "scripts" / f"{tool}.py"
    if not script.is_file():
        return report_error(f"unknown tool: {tool}", EXIT_USAGE, json_out=json_out)
    return subprocess.run([python, str(script), *rest]).returncode


def print_usage(stream):
    """Print the usage block to ``stream``."""
    print(USAGE, file=stream)


def print_json_info(started, json_out):
    """Print the resolved root, python and tool list as one JSON object."""
    try:
        root = resolve_repo_root()
        python = resolve_python(root)
    except EntryError as exc:
        return report_error(exc, EXIT_USAGE, json_out=json_out)
    payload = {
        "ok": True,
        "root": str(root),
        "python": python,
        "tools": available_tools(root),
    }
    payload.update(summary_fields(started))
    print(json.dumps(payload))
    return EXIT_OK


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    started = time.monotonic()
    json_out = "--json" in args
    if not args:
        print_usage(sys.stderr)
        return EXIT_USAGE
    first = args[0]
    if first in ("-h", "--help"):
        rest = args[1:]
        if rest and rest != ["--json"]:
            return report_error(
                f"--help takes no arguments: {rest[0]}", EXIT_USAGE, json_out=json_out
            )
        if json_out:
            return print_json_info(started, json_out)
        print_usage(sys.stdout)
        print("Tools:")
        try:
            root = resolve_repo_root()
        except EntryError as exc:
            return report_error(exc, EXIT_USAGE, json_out=json_out)
        for name in available_tools(root):
            print(name)
        return EXIT_OK
    if first == "--json":
        rest = args[1:]
        if rest and rest not in (["--help"], ["-h"]):
            return report_error(
                f"--json takes no arguments: {rest[0]}", EXIT_USAGE, json_out=json_out
            )
        return print_json_info(started, json_out)
    if first.startswith("-"):
        return report_error(f"unknown option: {first}", EXIT_USAGE, json_out=json_out)
    try:
        root = resolve_repo_root()
        python = resolve_python(root)
    except EntryError as exc:
        return report_error(exc, EXIT_USAGE, json_out=json_out)
    return dispatch_tool(root, python, first, args[1:], json_out)


if __name__ == "__main__":
    sys.exit(main())
