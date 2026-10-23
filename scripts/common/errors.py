"""Shared structured error reporting for every tool (AUDIT 2026-10 F24).

``report_error`` prints the human ``[error]`` line to stderr and, for ``--json``
runs, one ``{"ok": false, "error": ..., "exit_code": ...}`` object on stdout, so
an agent reads the same shape from every tool. It returns the exit code, so a
caller writes ``return report_error(exc, EXIT_USAGE, args.json)``.
"""

import json
import sys


def report_error(error, exit_code, json_out, **extra):
    """Print the error for humans and, when ``json_out``, the structured object; return ``exit_code``."""
    print(f"[error] {error}", file=sys.stderr)
    if json_out:
        payload = {"ok": False, "error": str(error), "exit_code": exit_code}
        payload.update(extra)
        print(json.dumps(payload))
    return exit_code
