#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Watch the build agents and send an ntfy alert only when something looks wrong.

Run it from cron every 15 minutes. Three checks:

- spend: today's OpenRouter usage for the agents' API key is above a threshold;
- stall: an ``opencode`` process is running but nothing has changed in the
  watched folders for longer than a threshold;
- no-progress: an ``opencode`` process is running but there has been no commit
  for longer than a threshold.

Each problem is reported once: once per UTC day for spend, once per stall
episode for stall. State lives in a small JSON file.

Usage:
    python scripts/agent_watchdog.py --repo <repo>
    python scripts/agent_watchdog.py --repo <repo> --spend-usd 3 --stall-hours 2 --json
    python scripts/agent_watchdog.py --repo <repo> --dry-run
    python scripts/agent_watchdog.py --repo <repo> --kill-over-usd 11 --kill-no-commit-hours 1.5

The API key is read from OPENROUTER_API_KEY or from --key-file
(default ~/.config/opencode/secrets/openrouter.key). It is never printed.

Two stop rules, both off unless given: no commit for N hours
(``--kill-no-commit-hours``) and one hard daily spend ceiling
(``--kill-over-usd``). The run-spend ledger measures the current lot's spend,
which the single ``Blocked`` stop notification reports; it is not a stop
threshold.

The alert label comes from the newest task brief in ``<repo>/.opencode/tasks/``
(review, audit and README notes skipped), else the tmux session running the
agent, else nothing — no per-lot flag.

The no-commit clock starts at the later of the last commit and the agent start
(the run marker, else the running ``opencode`` process start time), so a fresh
run is never reported as a long no-commit stretch and a future-dated commit
never reads as a negative age. A stop is claimed only after the process is
re-checked.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from common.notify import NotifyError, send
from common.openrouter import key_info
from common.spend import read_spend_ledger
from common.exit_codes import EXIT_OK, EXIT_ATTENTION, EXIT_USAGE, EXIT_RUNTIME

DEFAULT_KEY_FILE = "~/.config/opencode/secrets/openrouter.key"
DEFAULT_STATE_FILE = "~/.cache/yt-pipeline/watchdog-state.json"
DEFAULT_LEDGER_FILE = "~/.cache/yt-pipeline/run-spend.json"
DEFAULT_EXTRA_WATCH = "~/.local/share/opencode"
DEFAULT_TASKS_DIR = ".opencode/tasks"
TASK_FILE_SKIP_PREFIXES = ("review-", "AUDIT-", "README")
SKIPPED_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}
IS_WINDOWS = sys.platform.startswith("win")


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
    data = key_info(api_key, timeout=timeout)
    return float(data.get("usage_daily") or 0.0)


def agent_running():
    """Return True when a process named opencode (or opencode.exe on Windows) is running."""
    names = {"opencode", "opencode.exe"} if IS_WINDOWS else {"opencode"}
    try:
        import psutil
    except ImportError:
        return _agent_running_fallback()
    try:
        for process in psutil.process_iter(["name"]):
            try:
                name = process.info.get("name")
            except psutil.Error:
                continue
            if name and name.lower() in names:
                return True
    except psutil.Error:
        return False
    return False


def stop_agent(timeout=10.0):
    """Terminate every opencode process (kill after ``timeout`` s); return how many were stopped."""
    names = {"opencode", "opencode.exe"} if IS_WINDOWS else {"opencode"}
    try:
        import psutil
    except ImportError:
        return EXIT_OK
    targets = []
    for process in psutil.process_iter(["name"]):
        try:
            name = process.info.get("name")
        except psutil.Error:
            continue
        if name and name.lower() in names:
            targets.append(process)
    for process in targets:
        try:
            process.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(targets, timeout=timeout)
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            pass
    return len(targets)


def opencode_process_start():
    """Return the start time (epoch seconds) of the running opencode process, or 0.0."""
    names = {"opencode", "opencode.exe"} if IS_WINDOWS else {"opencode"}
    try:
        import psutil
    except ImportError:
        return 0.0
    try:
        for process in psutil.process_iter(["name"]):
            try:
                name = process.info.get("name")
            except psutil.Error:
                continue
            if name and name.lower() in names:
                try:
                    return float(process.create_time())
                except (psutil.Error, AttributeError, OSError):
                    continue
    except psutil.Error:
        return 0.0
    return 0.0


def agent_start_time(state, running):
    """Return the epoch second the current agent run started, or 0.0 when unknown."""
    started = parse_ts(state.get("run_started_at"))
    if started is not None:
        return started
    if running:
        return opencode_process_start()
    return 0.0


def _session_for_process(process, panes):
    seen = set()
    current = process
    while current is not None:
        try:
            pid = current.pid
        except Exception:
            return None
        if pid in seen:
            return None
        seen.add(pid)
        if pid in panes:
            return panes[pid]
        try:
            current = current.parent()
        except Exception:
            return None
    return None


def tmux_session_for_agent():
    """Return the tmux session whose pane runs the agent, or None when it cannot be found."""
    executable = shutil.which("tmux")
    if not executable:
        return None
    try:
        import psutil
    except ImportError:
        return None
    try:
        result = subprocess.run(
            [executable, "list-panes", "-a", "-F", "#{session_name} #{pane_pid}"],
            capture_output=True, check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    panes = {}
    for line in result.stdout.decode("utf-8", errors="ignore").splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        panes[int(parts[1])] = parts[0]
    if not panes:
        return None
    names = {"opencode", "opencode.exe"} if IS_WINDOWS else {"opencode"}
    try:
        for process in psutil.process_iter(["name"]):
            try:
                name = process.info.get("name")
            except psutil.Error:
                continue
            if not name or name.lower() not in names:
                continue
            session = _session_for_process(process, panes)
            if session:
                return session
    except psutil.Error:
        return None
    return None


def active_task_label(repo, tasks_dir=DEFAULT_TASKS_DIR):
    """Return the stem of the newest task brief in the repo, or None.

    The active brief is the most recently modified ``.md`` file in
    ``<repo>/.opencode/tasks/``; review, audit and README notes are skipped.
    """
    folder = Path(repo).expanduser() / tasks_dir
    try:
        candidates = list(folder.glob("*.md"))
    except OSError:
        return None
    newest = None
    newest_key = None
    for path in candidates:
        if path.name.startswith(TASK_FILE_SKIP_PREFIXES):
            continue
        try:
            key = (path.stat().st_mtime, path.name)
        except OSError:
            continue
        if newest_key is None or key > newest_key:
            newest_key = key
            newest = path
    return newest.stem if newest is not None else None


def resolve_label(args):
    """Return the alert label: the active task file, else the tmux session, else None."""
    return active_task_label(args.repo) or tmux_session_for_agent()


def labelled(message, label):
    """Prefix a message with the label once, unless it already starts with it."""
    if not label:
        return message
    prefix = f"{label}: "
    if message.startswith(prefix):
        return message
    return prefix + message


def strip_label(message, label):
    """Remove a leading label prefix from a message."""
    if label:
        prefix = f"{label}: "
        if message.startswith(prefix):
            return message[len(prefix):]
    return message


def kill_reason(running, daily_usd, kill_over_usd, last_commit, now, kill_no_commit_hours, agent_start=0.0):
    """Why the agent must be stopped now, or None. Both limits are off unless set."""
    if not running:
        return None
    if kill_over_usd is not None and daily_usd is not None and daily_usd > kill_over_usd:
        return f"spend today ${daily_usd:.2f} is over the hard cap ${kill_over_usd:.2f}"
    base = max(last_commit, agent_start)
    if kill_no_commit_hours is not None and base > 0:
        hours = max(0.0, now - base) / 3600
        if hours > kill_no_commit_hours:
            return f"no commit for {hours:.1f} h (hard limit {kill_no_commit_hours:.1f} h)"
    return None


def _agent_running_fallback():
    if IS_WINDOWS:
        return _tasklist_finds_agent()
    return _pgrep_finds_agent()


def _pgrep_finds_agent():
    executable = shutil.which("pgrep")
    if not executable:
        return False
    try:
        result = subprocess.run([executable, "-x", "opencode"], capture_output=True, check=False)
    except OSError:
        return False
    return result.returncode == 0


def _tasklist_finds_agent():
    executable = shutil.which("tasklist")
    if not executable:
        return False
    try:
        result = subprocess.run(
            [executable, "/FI", "IMAGENAME eq opencode.exe"], capture_output=True, check=False
        )
    except OSError:
        return False
    if result.returncode != 0:
        return False
    output = result.stdout.decode("utf-8", errors="ignore").lower()
    return "opencode.exe" in output


def newest_mtime(paths):
    newest = 0.0
    for root in paths:
        root = Path(root).expanduser()
        if not root.exists():
            continue
        for current, dirs, files in root.walk():
            dirs[:] = [d for d in dirs if d not in SKIPPED_DIRS]
            for name in files:
                try:
                    newest = max(newest, (current / name).stat().st_mtime)
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


def write_spend_ledger(path, entries):
    """Write the run-spend ledger atomically (sums are recomputed from the entries)."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps({"entries": entries}, indent=2), encoding="utf-8")
    tmp.replace(target)


def append_spend_entry(path, cost_usd, note, ts):
    """Append one spend entry to the run ledger and return the updated entries."""
    entries = read_spend_ledger(path)
    entries.append({"ts": ts, "cost_usd": round(float(cost_usd), 8), "note": note})
    write_spend_ledger(path, entries)
    return entries


def parse_ts(value):
    """Parse an ISO-8601 timestamp to epoch seconds, or None when unusable."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def run_spend_usd(entries, run_started_at, ledger_path=None):
    """Sum ``cost_usd`` over ledger entries that belong to the current run."""
    started = parse_ts(run_started_at)
    if started is None:
        return None
    ledger_time = Path(ledger_path).expanduser().stat().st_mtime if ledger_path and Path(ledger_path).expanduser().exists() else None
    total = 0.0
    for entry in entries:
        stamp = parse_ts(entry.get("ts"))
        if stamp is None:
            if ledger_time is not None and ledger_time >= started:
                total += float(entry["cost_usd"])
            continue
        if stamp >= started:
            total += float(entry["cost_usd"])
    return round(total, 8)


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

    Commit dates can differ from the wall clock, so the ref files' modification
    time is the real signal of a new commit.
    """
    git_dir = Path(repo).expanduser() / ".git"
    candidates = [git_dir / "logs" / "HEAD"]
    heads = git_dir / "refs" / "heads"
    if heads.exists():
        candidates.extend(path for path in heads.rglob("*") if path.is_file())
    times = [path.stat().st_mtime for path in candidates if path.exists()]
    return max(times) if times else 0.0


def check_no_progress(running, last_commit, now, limit_seconds, state, agent_start=0.0):
    """Return an alert when the agent runs but has not committed for too long, once per episode.

    The clock starts at the later of the last commit and the agent start, so a
    fresh run is never reported as a long no-commit stretch and the age is never
    negative. Catches loops that keep writing logs (so files change) without
    producing work.
    """
    if not running:
        return None
    base = max(last_commit, agent_start)
    if base <= 0:
        return None
    age = max(0.0, now - base)
    if age <= limit_seconds:
        return None
    if state.get("progress_alerted_for") == base:
        return None
    state["progress_alerted_for"] = base
    hours = age / 3600
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
        epilog="Example: python scripts/agent_watchdog.py --repo <repo> --json",
    )
    parser.add_argument("--repo", required=True, help="folder the agent works in")
    parser.add_argument("--watch", action="append", default=None,
                        help=f"extra folder to watch for activity (default {DEFAULT_EXTRA_WATCH}); repeatable")
    parser.add_argument("--spend-usd", type=float, default=3.0, help="daily spend threshold in USD (default 3)")
    parser.add_argument("--stall-hours", type=float, default=2.0, help="hours without changes = stalled (default 2)")
    parser.add_argument("--no-commit-hours", type=float, default=1.5,
                        help="hours without a commit while the agent runs = probably looping (default 1.5)")
    parser.add_argument("--kill-over-usd", type=float, default=None,
                        help="stop the agent when today's spend passes this many USD (default off)")
    parser.add_argument("--kill-no-commit-hours", type=float, default=None,
                        help="stop the agent after this many hours without a commit (default off)")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER_FILE,
                        help=f"run-spend ledger (default {DEFAULT_LEDGER_FILE})")
    parser.add_argument("--start-run", action="store_true",
                        help="start a new run now: fresh run marker and an empty ledger")
    parser.add_argument("--key-file", default=DEFAULT_KEY_FILE, help="file holding the OpenRouter key")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="where alerts already sent are remembered")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    parser.add_argument("--dry-run", action="store_true", help="evaluate and print, but send nothing and keep state")
    return parser


def main(argv=None, now=None):
    started = time.monotonic()
    args = build_parser().parse_args(argv)
    now = time.time() if now is None else now
    now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
    now_iso = now_dt.isoformat(timespec="seconds")
    today = now_dt.date().isoformat()
    state = load_state(args.state_file)
    errors = []
    label = resolve_label(args)

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

    ledger_path = Path(args.ledger).expanduser()
    try:
        entries = read_spend_ledger(ledger_path)
    except ValueError as exc:
        entries = []
        errors.append(f"ledger check failed: {exc}")

    run_started_at = state.get("run_started_at")
    if args.start_run:
        run_started_at = now_iso
        state["run_started_at"] = now_iso
        state["usage_daily_last_seen"] = None
        entries = []
        if not args.dry_run:
            write_spend_ledger(ledger_path, entries)
    elif running:
        stopped_at = parse_ts(state.get("agent_stopped_at"))
        started_at_epoch = parse_ts(run_started_at)
        stale = started_at_epoch is None or (stopped_at is not None and started_at_epoch < stopped_at)
        if stale:
            run_started_at = now_iso
            state["run_started_at"] = now_iso
    if not running:
        state["agent_stopped_at"] = now_iso

    if daily_usd is not None:
        last_seen = state.get("usage_daily_last_seen")
        if last_seen is not None and run_started_at and daily_usd > last_seen:
            delta = round(daily_usd - last_seen, 8)
            if args.dry_run:
                entries = entries + [{"ts": now_iso, "cost_usd": delta, "note": "openrouter-delta"}]
            else:
                entries = append_spend_entry(ledger_path, delta, "openrouter-delta", now_iso)
        state["usage_daily_last_seen"] = daily_usd

    run_spend = run_spend_usd(entries, run_started_at, ledger_path)
    agent_start = agent_start_time(state, running)

    notifications = []
    for message in (
        check_spend(daily_usd, args.spend_usd, state, today),
        check_stall(running, last_activity, now, args.stall_hours * 3600, state),
        check_no_progress(running, last_commit, now, args.no_commit_hours * 3600, state, agent_start),
    ):
        if message:
            notifications.append(("yt-pipeline watchdog", labelled(message, label)))

    reason = kill_reason(
        running, daily_usd, args.kill_over_usd, last_commit, now, args.kill_no_commit_hours,
        agent_start=agent_start,
    )
    stopped = 0
    stop_verified = False
    if reason:
        if not args.dry_run:
            stopped = stop_agent()
            stop_verified = not agent_running()
        reason_text = strip_label(reason, label)
        cost_note = f" This lot has cost ${run_spend:.2f}." if run_spend else ""
        if args.dry_run:
            stop_message = f"Would stop the agent: {reason_text}.{cost_note} Its changes stay in the working tree."
        elif stop_verified:
            stop_message = f"Stopped the agent: {reason_text}.{cost_note} Its changes stay in the working tree."
        else:
            stop_message = f"Tried to stop the agent but it is still running: {reason_text}.{cost_note}"
        notifications.append(("Blocked", labelled(stop_message, label)))

    alerts = [message for _, message in notifications]
    sent = 0
    if not args.dry_run:
        for title, message in notifications:
            try:
                send(message, title=title, priority="high", tags=["warning"])
                sent += 1
            except NotifyError as exc:
                errors.append(f"notify failed: {exc}")
        save_state(args.state_file, state)

    commit_base = max(last_commit, agent_start)
    summary = {
        "agent_running": running,
        "idle_hours": round(max(0.0, (now - last_activity) / 3600), 2) if last_activity else None,
        "hours_since_commit": round(max(0.0, (now - commit_base) / 3600), 2) if commit_base else None,
        "spend_today_usd": daily_usd,
        "run_spend_usd": run_spend,
        "run_started_at": run_started_at,
        "alerts": alerts,
        "kill_reason": reason,
        "stopped": stopped,
        "stop_verified": stop_verified,
        "label": label,
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
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
