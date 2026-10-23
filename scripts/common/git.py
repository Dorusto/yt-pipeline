# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run git commands and return their stdout lines.

The check tools read the repository through git; a failed command is a runtime
error, not an empty result, so the caller can report it instead of silently
checking nothing.
"""

import subprocess


class GitError(RuntimeError):
    """A git command failed."""


def git_lines(*args):
    """Run `git <args>` and return its non-empty stdout lines; raise GitError on a non-zero exit."""
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        message = detail[-1] if detail else f"git {' '.join(args)} exited {result.returncode}"
        raise GitError(message)
    return [line for line in result.stdout.splitlines() if line.strip()]
