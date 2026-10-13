#!/usr/bin/env python3
"""Refuse commits that would publish private material in this public repository.

Checks the files staged for commit (or the paths given) for:

- footage, audio, images, subtitles and model weights by extension;
- files larger than 1 MB;
- secrets: OpenRouter / OpenAI / GitHub / AWS keys and private key blocks;
- embedded base64 images (frames or thumbnails pasted into text files).

Usage:
    python scripts/check_public.py                 # staged files (pre-commit hook)
    python scripts/check_public.py --all --json    # every tracked file
    python scripts/check_public.py docs/x.md       # given paths

Install the hook once per clone: ``git config core.hooksPath .githooks``.
Exit code 1 when something is found; nothing is changed.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

BLOCKED_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".lrf", ".insv",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg",
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif", ".tif", ".tiff", ".dng", ".arw",
    ".srt", ".ass", ".vtt",
    ".onnx", ".rnnn", ".pt", ".pth", ".safetensors", ".bin", ".gguf",
    ".kdenlive", ".sqlite", ".db",
}
ALLOWED_PREFIXES = ("tests/fixtures/", "examples/")
MAX_BYTES = 1_000_000
SECRET_PATTERNS = {
    "openrouter key": re.compile(r"sk-or-v1-[A-Za-z0-9]{20,}"),
    "openai-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{32,}"),
    "github token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "aws access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
BASE64_IMAGE = re.compile(r"data:image/[a-z]+;base64,[A-Za-z0-9+/]{200,}")


def git_lines(*args):
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    return [line for line in result.stdout.splitlines() if line.strip()]


def staged_files():
    return git_lines("diff", "--cached", "--name-only", "--diff-filter=ACMR")


def check_file(path):
    """Return a list of problems for one file path (relative to the repo root)."""
    problems = []
    normalized = path.replace("\\", "/")
    file_path = Path(path)
    allowed_location = normalized.startswith(ALLOWED_PREFIXES)
    if file_path.suffix.lower() in BLOCKED_EXTENSIONS and not allowed_location:
        problems.append(f"{path}: media/model/subtitle file type {file_path.suffix} is never published")
    if not file_path.exists():
        return problems
    size = file_path.stat().st_size
    if size > MAX_BYTES:
        problems.append(f"{path}: {size / 1_000_000:.1f} MB is over the 1 MB limit")
    try:
        text = file_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return problems
    for name, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            problems.append(f"{path}: looks like it contains a {name}")
    if BASE64_IMAGE.search(text):
        problems.append(f"{path}: contains an embedded base64 image (frame or thumbnail)")
    return problems


def build_parser():
    parser = argparse.ArgumentParser(
        description="Block footage, images, secrets and large files from this public repo.",
        epilog="Example: python scripts/check_public.py --all --json",
    )
    parser.add_argument("paths", nargs="*", help="files to check (default: staged files)")
    parser.add_argument("--all", action="store_true", help="check every tracked file")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def main(argv=None):
    started = time.monotonic()
    args = build_parser().parse_args(argv)
    if args.paths:
        files = args.paths
    elif args.all:
        files = git_lines("ls-files")
    else:
        files = staged_files()
    problems = [problem for path in files for problem in check_file(path)]
    if args.json:
        print(json.dumps({
            "checked": len(files),
            "problems": problems,
            "ok": not problems,
            "wall_time_s": round(time.monotonic() - started, 2),
            "llm_cost_usd": 0.0,
        }))
    else:
        for problem in problems:
            print(f"[blocked] {problem}", file=sys.stderr)
        if problems:
            print("Commit refused: this repository is public. Remove the files above or add them to .gitignore.",
                  file=sys.stderr)
        else:
            print(f"ok ({len(files)} files)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
