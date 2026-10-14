#!/usr/bin/env python3
"""Watch the build agents and send an ntfy alert only when something looks wrong.

Run it from cron every 30 minutes. Two checks:

- spend: today's OpenRouter usage for the agents' API key is above a threshold;
- stall: an ``opencode`` process is running but nothing has changed in the
  watched folders for longer than a threshold.

Each problem is reported once: once per UTC day for spend, once per stall
episode for stall. State lives in a small JSON file.

Usage:
    python scripts/agent_watchdog.py --repo ~/projects/yt-pipeline
    python scripts/agent_watchdog.py --repo ~/projects/yt-pipeline --spend-usd 3 --stall-hours 2 --json
    python scripts/agent_watchdog.py --repo ~/projects/yt-pipeline --dry-run

The API key is read from OPENROUTER_API_KEY or from --key-file
(default ~/.config/opencode/secrets/openrouter.key). It is never printed.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from common.notify import NotifyError, send

KEY_INFO_URL = "https://openrouter.ai/api/v1/key"
DEFAULT_KEY_FILE = "~/.config/opencode/secrets/openrouter.key"
DEFAULT_STATE_FILE = "~/.cache/yt-pipeline/watchdog-state.json"
DEFAULT_EXTRA_WATCH = "~/.local/share/opencode"
SKIPPED_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}


def read_api_key(key_file):
    env_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if env_key:
        return env_key
    path = Path(key_file).expanduser()
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def fetch_daily_usage(api_key, timeout=15):
    """Return today's USD usage for the key, as reported by OpenRouter."""
    request = urllib.request.Request(KEY_INFO_URL, headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8")).get("data", {})
    return float(data.get("usage_daily") or 0.0)


def agent_running():
    try:
        result = subprocess.run(["pgrep", "-x", "opencode"], capture_output=True, check=False)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def newest_mtime(paths):
    newest = 0.0
    for root in paths:
        root = Path(root).expanduser()
        if not root.exists():
            continue
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIPPED_DIRS]
            for name in files:
                try:
                    newest = max(newest, os.stat(os.path.join(current, name)).st_mtime)
                except OSError:
                    continue
    return newest


def load_state(path):
    try:
        return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, indent=2), encoding="utf-8")


def check_spend(daily_usd, threshold, state, today):
    """Return an alert message when today's spend crosses the threshold for the first time."""
    if daily_usd is None or daily_usd <= threshold:
        return None
    if state.get("spend_alerted_on") == today:
        return None
    state["spend_alerted_on"] = today
    return f"OpenRouter spend today is ${daily_usd:.2f} (threshold ${threshold:.2f}). Check the agent."


def last_commit_time(repo):
    """Return when a branch last moved (epoch seconds, from the ref files), 0 when unknown.

    Commit dates are backdated by project rule, so the ref files' modification
    time is the real signal of a new commit.
    """
    git_dir = Path(repo).expanduser() / ".git"
    candidates = [git_dir / "logs" / "HEAD"]
    heads = git_dir / "refs" / "heads"
    if heads.exists():
        candidates.extend(path for path in heads.rglob("*") if path.is_file())
    times = [path.stat().st_mtime for path in candidates if path.exists()]
    return max(times) if times else 0.0


def check_no_progress(running, last_commit, now, limit_seconds, state):
    """Return an alert when the agent runs but has not committed for too long, once per episode.

    Catches loops that keep writing logs (so files change) without producing work.
    """
    if not running or last_commit <= 0:
        return None
    if now - last_commit <= limit_seconds:
        return None
    if state.get("progress_alerted_for") == last_commit:
        return None
    state["progress_alerted_for"] = last_commit
    hours = (now - last_commit) / 3600
    return f"Agent is running but made no commit for {hours:.1f} h. It may be looping; check tmux."


def check_stall(running, last_activity, now, stall_seconds, state):
    """Return an alert message when the agent runs but nothing changed for too long, once per episode."""
    if not running or last_activity <= 0:
        return None
    idle = now - last_activity
    if idle <= stall_seconds:
        return None
    if state.get("stall_alerted_for") == last_activity:
        return None
    state["stall_alerted_for"] = last_activity
    hours = idle / 3600
    return f"Agent process is running but nothing changed for {hours:.1f} h. It may be stuck."


def build_parser():
    parser = argparse.ArgumentParser(
        description="Alert on ntfy when the build agent overspends or stalls.",
        epilog="Example: python scripts/agent_watchdog.py --repo ~/projects/yt-pipeline --json",
    )
    parser.add_argument("--repo", required=True, help="folder the agent works in")
    parser.add_argument("--watch", action="append", default=None,
                        help=f"extra folder to watch for activity (default {DEFAULT_EXTRA_WATCH}); repeatable")
    parser.add_argument("--spend-usd", type=float, default=3.0, help="daily spend threshold in USD (default 3)")
    parser.add_argument("--stall-hours", type=float, default=2.0, help="hours without changes = stalled (default 2)")
    parser.add_argument("--no-commit-hours", type=float, default=1.5,
                        help="hours without a commit while the agent runs = probably looping (default 1.5)")
    parser.add_argument("--key-file", default=DEFAULT_KEY_FILE, help="file holding the OpenRouter key")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="where alerts already sent are remembered")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    parser.add_argument("--dry-run", action="store_true", help="evaluate and print, but send nothing and keep state")
    return parser


def main(argv=None):
    started = time.monotonic()
    args = build_parser().parse_args(argv)
    now = time.time()
    today = datetime.now(timezone.utc).date().isoformat()
    state = load_state(args.state_file)
    errors = []

    daily_usd = None
    api_key = read_api_key(args.key_file)
    if api_key:
        try:
            daily_usd = fetch_daily_usage(api_key)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            errors.append(f"spend check failed: {exc.__class__.__name__}")
    else:
        errors.append("spend check skipped: no API key found")

    watch = [args.repo] + (args.watch if args.watch is not None else [DEFAULT_EXTRA_WATCH])
    running = agent_running()
    last_activity = newest_mtime(watch)
    last_commit = last_commit_time(args.repo)

    alerts = [m for m in (
        check_spend(daily_usd, args.spend_usd, state, today),
        check_stall(running, last_activity, now, args.stall_hours * 3600, state),
        check_no_progress(running, last_commit, now, args.no_commit_hours * 3600, state),
    ) if m]

    sent = 0
    if not args.dry_run:
        for message in alerts:
            try:
                send(message, title="yt-pipeline watchdog", priority="high", tags=["warning"])
                sent += 1
            except NotifyError as exc:
                errors.append(f"notify failed: {exc}")
        save_state(args.state_file, state)

    summary = {
        "agent_running": running,
        "idle_hours": round((now - last_activity) / 3600, 2) if last_activity else None,
        "hours_since_commit": round((now - last_commit) / 3600, 2) if last_commit else None,
        "spend_today_usd": daily_usd,
        "alerts": alerts,
        "sent": sent,
        "dry_run": args.dry_run,
        "errors": errors,
        "wall_time_s": round(time.monotonic() - started, 2),
        "llm_cost_usd": 0.0,
    }
    if args.json:
        print(json.dumps(summary))
    else:
        for message in alerts:
            print(f"[alert] {message}")
        for error in errors:
            print(f"[warn] {error}", file=sys.stderr)
        if not alerts:
            print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
