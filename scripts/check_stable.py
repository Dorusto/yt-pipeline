#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fail a pull request that changes a stable file without the approval trailer.

Reads the machine-readable ``stable-files`` block of ``docs/RELEASES.md`` and the
files changed in a git range. When the latest version is released (its heading is
not a candidate), a changed stable file needs a commit in the range carrying the
``Stable-change: approved`` trailer. Before the first tag nothing is enforced.

Usage:
    python scripts/check_stable.py --base origin/master --head HEAD
    python scripts/check_stable.py --changed-files changed.txt --commit-messages messages.txt --json

Exit code 1 when a stable file changed without the trailer; 2 on a usage error.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME
from common.errors import report_error
from common.git import GitError, git_lines
from common.summary import summary_fields

SECTION_RE = re.compile(r"^##\s+")
VERSION_RE = re.compile(r"^##\s+v\d+\.\d+\.\d+")
VERSION_TOKEN_RE = re.compile(r"v\d+\.\d+\.\d+")
FENCE_OPEN_RE = re.compile(r"^```stable-files\s*$")
FENCE_CLOSE_RE = re.compile(r"^```\s*$")
APPROVAL_RE = re.compile(r"^Stable-change:\s*approved\s*$", re.MULTILINE | re.IGNORECASE)


def _first_stable_block(lines):
    collected = None
    for line in lines:
        if collected is None:
            if FENCE_OPEN_RE.match(line):
                collected = []
            continue
        if FENCE_CLOSE_RE.match(line):
            break
        collected.append(line)
    if collected is None:
        return []
    return [entry for entry in (line.strip() for line in collected) if entry]


def _version_key(version):
    """The three numeric parts of ``vX.Y.Z`` for comparison."""
    return tuple(int(part) for part in version.lstrip("v").split("."))


def parse_stable_files(releases_text):
    """Return ``(version, enforced, stable_files)`` from the highest released version section."""
    sections = []
    heading = None
    body = []
    for line in releases_text.splitlines():
        if SECTION_RE.match(line):
            if heading is not None:
                sections.append((heading, body))
            heading = line
            body = []
        elif heading is not None:
            body.append(line)
    if heading is not None:
        sections.append((heading, body))
    released = []
    for heading, body in sections:
        if not VERSION_RE.match(heading):
            continue
        if re.search(r"candidate|not released", heading, re.IGNORECASE):
            continue
        token = VERSION_TOKEN_RE.search(heading)
        version = token.group(0) if token else heading.strip()
        released.append((version, body))
    if not released:
        return None, False, []
    version, body = max(released, key=lambda item: _version_key(item[0]))
    return version, True, _first_stable_block(body)


def has_approval(commit_messages):
    """Return True when a commit message carries the ``Stable-change: approved`` trailer."""
    return bool(APPROVAL_RE.search(commit_messages))


def find_violations(changed_files, stable_files):
    """Return the changed paths that are listed as stable files."""
    return [path for path in changed_files if path in stable_files]


def build_parser():
    parser = argparse.ArgumentParser(
        description="Fail a pull request that changes a stable file without the approval trailer.",
        epilog="Example: python scripts/check_stable.py --base origin/master --head HEAD --json",
    )
    parser.add_argument("--releases", default="docs/RELEASES.md", help="path to RELEASES.md")
    parser.add_argument("--base", help="base git ref of the range")
    parser.add_argument("--head", help="head git ref of the range")
    parser.add_argument("--changed-files", help="file with one changed path per line (overrides the git diff)")
    parser.add_argument("--commit-messages", help="file with the commit messages (overrides the git log)")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def main(argv=None):
    started = time.monotonic()
    args = build_parser().parse_args(argv)
    releases_path = Path(args.releases)
    if not releases_path.exists():
        return report_error(f"releases file not found: {releases_path}", EXIT_USAGE, args.json)
    version, enforced, stable_files = parse_stable_files(releases_path.read_text(encoding="utf-8"))
    try:
        if args.changed_files:
            changed = [
                line.strip()
                for line in Path(args.changed_files).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        else:
            if not args.base or not args.head:
                return report_error("--base and --head are required without --changed-files", EXIT_USAGE, args.json)
            changed = git_lines("diff", "--name-only", f"{args.base}..{args.head}")
        if args.commit_messages:
            messages = Path(args.commit_messages).read_text(encoding="utf-8")
        else:
            messages = "\n".join(git_lines("log", f"{args.base}..{args.head}", "--format=%B"))
    except GitError as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    violations = find_violations(changed, stable_files)
    approved = has_approval(messages)
    ok = (not enforced) or (not violations) or approved
    if args.json:
        print(json.dumps({
            "ok": ok,
            "enforced": enforced,
            "version": version,
            "stable_files": stable_files,
            "changed": changed,
            "violations": violations,
            "approved": approved,
            **summary_fields(started),
        }))
    elif not ok:
        for path in violations:
            print(
                f'[blocked] {path} is a stable file; add the "Stable-change: approved" trailer to a commit in the range',
                file=sys.stderr,
            )
    else:
        print(f"ok ({len(changed)} changed files, {len(stable_files)} stable files)")
    return EXIT_ATTENTION if not ok else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
