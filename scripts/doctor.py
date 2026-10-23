#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check that this machine can run the recap pipeline and print what to fix.

Usage:
    python scripts/doctor.py --source <source-folder>
    python scripts/doctor.py --source <source-folder> --workspace <workspace> --json

The tool runs thirteen setup checks in a fixed order: python dependencies,
``ffmpeg`` and ``ffprobe``, the project config, the models the recap chain
calls, the OpenRouter API key, free disk space, write access to the workspace,
that the workspace is outside the originals, that the originals folder is
read-only, that the publish CLIs answer ``--help``, that the whisper package is
present and that the config carries the publish roles and ``channel.members``.
It calls no model and never prints a secret. It reads paths and writes only a
tiny probe file inside the workspace, removed right after; it never writes
under ``--source``.
"""

import argparse
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

from common.config import ConfigError
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME
from common.errors import report_error
from common.sizes import BYTES_PER_GB, format_gb

TOOL = "scripts/doctor.py"
REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_IMPORTS = ("cv2", "numpy", "yaml", "jsonschema", "openai")
MODEL_ROLES = ("vision", "director")
PUBLISH_CLIS = (
    "scripts/subs_from_edit.py",
    "scripts/transcribe.py",
    "scripts/metadata.py",
    "scripts/publish.py",
)
PUBLISH_ROLES = ("translate", "metadata")
DISK_WARN_GB = 5.0
DISK_FAIL_GB = 2.0
PROBE_NAME = ".doctor-probe"

DEPS_FIX = (
    "install the dependencies in the repo venv:"
    " uv venv && uv pip install -r requirements.txt"
)
FFMPEG_FIX = "install ffmpeg (for example: apt install ffmpeg)"
CONFIG_FIX = "copy config.yaml.example to config.yaml and set the models you use"
API_KEY_FIX = "export OPENROUTER_API_KEY=... (get one at https://openrouter.ai/keys)"
OUTSIDE_FIX = "choose a --workspace outside --source"
PUBLISH_CHAIN_FIX = "re-run from the repo root with the repo .venv"
WHISPER_FIX = (
    "pip install openai-whisper (or run the chain with --transcribe-backend mock"
    " / word JSONs from the edit)"
)
PUBLISH_CONFIG_FIX = "set them in config.yaml (see config.yaml.example)"


class DoctorError(RuntimeError):
    """Raised when the doctor itself cannot run a check."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="doctor.py",
        description="Check that this machine can run the recap pipeline and print what to fix.",
        epilog=(
            "examples:\n"
            "  python scripts/doctor.py --source <source-folder>\n"
            "  python scripts/doctor.py --source <source-folder> --workspace <workspace> --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        help="originals folder to check (read-only expectations)",
    )
    parser.add_argument(
        "--workspace",
        help="workspace to check for write access (default: <source parent>/workspace)",
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    return parser


def load_project_config():
    """Load the project config and return ``(config, resolved path)``."""
    from common.config import load_config, resolve_config_path

    path = resolve_config_path()
    return load_config(path), path


def result(name, status, detail, fix=""):
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def format_budget(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"${value:.2f}"
    return "no limit"


def check_python_deps():
    missing = []
    for module in REQUIRED_IMPORTS:
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(module)
    if missing:
        detail = "missing: " + ", ".join(missing)
        return result("python_deps", "fail", detail, DEPS_FIX)
    return result("python_deps", "ok", ", ".join(REQUIRED_IMPORTS))


def check_binary(name):
    path = shutil.which(name)
    if path is None:
        return result(name, "fail", f"{name} not found on PATH", FFMPEG_FIX)
    return result(name, "ok", path)


def evaluate_config():
    """Return ``(config or None, config check)`` for the project config."""
    try:
        config, path = load_project_config()
    except (ConfigError, ImportError, OSError) as exc:
        return None, result("config", "fail", f"could not load config: {exc}", CONFIG_FIX)
    backend = config.get("llm_backend")
    budget = format_budget(config.get("llm_budget_usd"))
    detail = f"{path} (llm_backend {backend}, budget {budget})"
    if Path(path).name == "config.yaml.example":
        return config, result("config", "warn", detail, CONFIG_FIX)
    return config, result("config", "ok", detail)


def check_models(config):
    models = (config or {}).get("models") or {}
    missing = []
    parts = []
    for role in MODEL_ROLES:
        value = models.get(role)
        if not value:
            missing.append(role)
            parts.append(f"{role}: <missing>")
        else:
            parts.append(f"{role}: {value}")
    detail = ", ".join(parts)
    if missing:
        roles = ", ".join(f"models.{role}" for role in missing)
        fix = f"add the missing {roles} to config.yaml"
        return result("models", "fail", detail, fix)
    return result("models", "ok", detail)


def check_api_key(config):
    backend = (config or {}).get("llm_backend")
    if backend == "mock":
        return result("openrouter_api_key", "ok", "not needed (llm_backend: mock)")
    if not os.environ.get("OPENROUTER_API_KEY"):
        return result(
            "openrouter_api_key", "fail", "OPENROUTER_API_KEY is not set", API_KEY_FIX
        )
    return result("openrouter_api_key", "ok", "set (hidden)")


def nearest_existing(path):
    path = Path(path)
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def check_disk_space(workspace):
    path = workspace if workspace is not None else Path.cwd()
    fix = f"free up disk space on {path}"
    try:
        usage = shutil.disk_usage(nearest_existing(path))
    except OSError as exc:
        return result("disk_space", "fail", f"could not read disk usage at {path}: {exc}", fix)
    free = usage.free / BYTES_PER_GB
    detail = f"{format_gb(usage.free)} free at {path}"
    if free < DISK_FAIL_GB:
        return result("disk_space", "fail", detail, fix)
    if free < DISK_WARN_GB:
        return result("disk_space", "warn", detail, fix)
    return result("disk_space", "ok", detail)


def check_workspace_writable(workspace):
    if workspace is None:
        return result("workspace_writable", "ok", "not checked (no --workspace)")
    try:
        Path(workspace).mkdir(parents=True, exist_ok=True)
        probe = Path(workspace) / PROBE_NAME
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        fix = f"choose a writable --workspace (current: {workspace})"
        return result("workspace_writable", "fail", f"not writable: {exc}", fix)
    return result("workspace_writable", "ok", "writable")


def is_within(path, directory):
    path = Path(path).expanduser().resolve()
    directory = Path(directory).expanduser().resolve()
    return path == directory or directory in path.parents


def check_workspace_outside_source(workspace, source):
    if source is None:
        return result("workspace_outside_source", "ok", "not checked (no --source)")
    if is_within(workspace, source):
        detail = f"workspace {workspace} is inside {source}"
        return result("workspace_outside_source", "fail", detail, OUTSIDE_FIX)
    return result("workspace_outside_source", "ok", "outside the originals")


def check_originals_read_only(source):
    if source is None:
        return result("originals_read_only", "ok", "not checked (no --source)")
    fix = f"chmod -R a-w {source} (the tools refuse to write there anyway)"
    try:
        permissions = stat.S_IMODE(source.stat().st_mode)
    except OSError as exc:
        return result("originals_read_only", "warn", f"could not stat originals: {exc}", fix)
    if permissions & 0o222:
        detail = f"originals folder is writable (mode {permissions:o})"
        return result("originals_read_only", "warn", detail, fix)
    return result("originals_read_only", "ok", "read-only")


def check_publish_chain():
    for script in PUBLISH_CLIS:
        path = REPO_ROOT / script
        try:
            proc = subprocess.run(
                [sys.executable, str(path), "--help"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            return result("publish_chain", "fail", f"{script}: {exc}", PUBLISH_CHAIN_FIX)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            detail = f"{script} exited {proc.returncode}"
            if tail:
                detail += f": {tail[-1]}"
            return result("publish_chain", "fail", detail, PUBLISH_CHAIN_FIX)
    return result("publish_chain", "ok", "4 publish CLIs answer --help")


def check_whisper_package():
    if importlib.util.find_spec("whisper") is None:
        return result("whisper_package", "warn", "whisper not installed", WHISPER_FIX)
    return result("whisper_package", "ok", "whisper importable")


def check_publish_config(config):
    models = (config or {}).get("models") or {}
    channel = (config or {}).get("channel") or {}
    missing = []
    for role in PUBLISH_ROLES:
        if not models.get(role):
            missing.append(f"models.{role}")
    members = channel.get("members")
    if not (isinstance(members, str) and members.strip()):
        missing.append("channel.members")
    if missing:
        detail = "missing: " + ", ".join(missing)
        return result("publish_config", "warn", detail, PUBLISH_CONFIG_FIX)
    return result("publish_config", "ok", "publish roles and channel.members set")


def run_checks(source, workspace):
    """Run all thirteen checks in order and return the list of check objects."""
    config, config_check = evaluate_config()
    return [
        check_python_deps(),
        check_binary("ffmpeg"),
        check_binary("ffprobe"),
        config_check,
        check_models(config),
        check_api_key(config),
        check_disk_space(workspace),
        check_workspace_writable(workspace),
        check_workspace_outside_source(workspace, source),
        check_originals_read_only(source),
        check_publish_chain(),
        check_whisper_package(),
        check_publish_config(config),
    ]


def build_summary(checks, source, workspace, started):
    fixes = [
        check["fix"]
        for check in checks
        if check["status"] in ("warn", "fail") and check["fix"]
    ]
    ok = all(check["status"] != "fail" for check in checks)
    return {
        "tool": TOOL,
        "ok": ok,
        "checks": checks,
        "fixes": fixes,
        "source": str(source) if source is not None else None,
        "workspace": str(workspace) if workspace is not None else None,
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": 0.0,
    }


def print_human(checks):
    for check in checks:
        print(f"[{check['status']:<4}] {check['name']:<22} {check['detail']}")
    print()
    fixes = [
        check["fix"]
        for check in checks
        if check["status"] in ("warn", "fail") and check["fix"]
    ]
    if fixes:
        print("fix these:")
        for fix in fixes:
            print(f"  - {fix}")
    else:
        print("all good")


def resolve_paths(args):
    source = Path(args.source).expanduser().resolve() if args.source is not None else None
    if args.workspace is not None:
        workspace = Path(args.workspace).expanduser().resolve()
    elif source is not None:
        workspace = (source.parent / "workspace").resolve()
    else:
        workspace = None
    return source, workspace


def main(argv=None):
    args = build_parser().parse_args(argv)
    started = time.monotonic()
    source, workspace = resolve_paths(args)
    try:
        checks = run_checks(source, workspace)
    except Exception as exc:
        return report_error(exc, EXIT_RUNTIME, args.json)
    summary = build_summary(checks, source, workspace, started)
    if args.json:
        print(json.dumps(summary))
    else:
        print_human(checks)
    return EXIT_OK if summary["ok"] else EXIT_ATTENTION


if __name__ == "__main__":
    sys.exit(main())
