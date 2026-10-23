#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Watch the build agents and send an ntfy alert only when something looks wrong.

Run it from cron every 30 minutes. Two checks:

- spend: today's OpenRouter usage for the agents' API key is above a threshold;
- stall: an ``opencode`` process is running but nothing has changed in the
  watched folders for longer than a threshold.

Each problem is reported once: once per UTC day for spend, once per stall
episode for stall. State lives in a small JSON file.

Usage:
    python scripts/agent_watchdog.py --repo <repo>
    python scripts/agent_watchdog.py --repo <repo> --spend-usd 3 --stall-hours 2 --json
    python scripts/agent_watchdog.py --repo <repo> --dry-run
    python scripts/agent_watchdog.py --repo <repo> --run-cap 6 --lot "S51 lot 1" --start-run

The API key is read from OPENROUTER_API_KEY or from --key-file
(default ~/.config/opencode/secrets/openrouter.key). It is never printed.

With ``--run-cap`` the spend is measured since the run started (a run-spend
ledger kept by this script), not per day: ntfy ``Budget`` at 80 % of the cap
and ntfy ``Blocked`` plus a stop at 100 %.

The no-commit clock starts at the later of the last commit and the agent start
(the run marker, else the running ``opencode`` process start time), so a fresh
run is never reported as a long no-commit stretch and a future-dated commit
never reads as a negative age. A stop is claimed only after the process is
re-checked. Every ntfy message is prefixed with ``--lot``, else the tmux
session running the agent, else nothing.
"""

import argparse
import json
import os
import shutil
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
DEFAULT_LEDGER_FILE = "~/.cache/yt-pipeline/run-spend.json"
DEFAULT_EXTRA_WATCH = "~/.local/share/opencode"
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
    request = urllib.request.Request(KEY_INFO_URL, headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8")).get("data", {})
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
        return 0
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


def resolve_label(args):
    """Return the alert label: ``--lot``, else the tmux session running the agent, else None."""
    if args.lot:
        return args.lot
    return tmux_session_for_agent()


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


def kill_reason(running, daily_usd, kill_over_usd, last_commit, now, kill_no_commit_hours,
                run_spend=None, run_cap=None, lot=None, agent_start=0.0):
    """Why the agent must be stopped now, or None. All limits are off unless set."""
    if not running:
        return None
    if run_cap is not None and run_spend is not None and run_cap > 0 and run_spend >= run_cap:
        label = f"{lot}: " if lot else ""
        return f"{label}run spend ${run_spend:.2f} is over the run cap ${run_cap:.2f}"
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


def read_spend_ledger(path):
    """Return the spend ledger entries (list of dicts with ``cost_usd``), [] when absent."""
    target = Path(path).expanduser()
    if not target.exists():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"spend ledger {target} is unreadable: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("entries", [])
    if not isinstance(data, list):
        raise ValueError(f"spend ledger {target} must hold an entries list")
    return [entry for entry in data if isinstance(entry, dict) and isinstance(entry.get("cost_usd"), (int, float))]


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


def run_cap_reached(run_spend, run_cap):
    """True when the run spend is at or over the run cap."""
    return run_cap is not None and run_cap > 0 and run_spend is not None and run_spend >= run_cap


def check_run_budget(run_spend, run_cap, state, lot=None):
    """Alert once per run when run spend reaches 80 % of the cap (never stops)."""
    if run_spend is None or run_cap is None or run_cap <= 0:
        return None
    markers = state.setdefault("run_alerted", [])
    if run_spend >= run_cap or "budget-80" in markers:
        return None
    if run_spend < 0.8 * run_cap:
        return None
    markers.append("budget-80")
    label = f"{lot}: " if lot else ""
    return (
        f"{label}run spend ${run_spend:.2f} reached 80% of the run cap ${run_cap:.2f}. "
        "Check what is left to do before it stops the agent."
    )


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
    parser.add_argument("--run-cap", type=float, default=None,
                        help="hard cap in USD for the current agent run (default off)")
    parser.add_argument("--lot", default=None,
                        help="label naming the lot/task, prefixed to every alert (else the tmux session)")
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
        state["run_alerted"] = []
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
            state["run_alerted"] = []
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
    if args.run_cap is not None:
        budget_message = check_run_budget(run_spend, args.run_cap, state, args.lot)
        if budget_message:
            notifications.append(("Budget", labelled(budget_message, label)))

    reason = kill_reason(
        running, daily_usd, args.kill_over_usd, last_commit, now, args.kill_no_commit_hours,
        run_spend=run_spend, run_cap=args.run_cap, lot=args.lot, agent_start=agent_start,
    )
    stopped = 0
    stop_verified = False
    if reason:
        if not args.dry_run:
            stopped = stop_agent()
            stop_verified = not agent_running()
        reason_text = strip_label(reason, label)
        if args.run_cap is not None and run_spend is not None and run_cap_reached(run_spend, args.run_cap):
            state.setdefault("run_alerted", []).append("blocked")
            if args.dry_run:
                blocked = f"{reason_text} — would stop the agent. Its changes stay in the working tree."
            elif stop_verified:
                blocked = f"{reason_text} — the agent was stopped. Its changes stay in the working tree."
            else:
                blocked = f"{reason_text} — tried to stop the agent but it is still running."
            notifications.append(("Blocked", labelled(blocked, label)))
        else:
            if args.dry_run:
                stop_message = f"Would stop the agent: {reason_text}. Its changes stay in the working tree."
            elif stop_verified:
                stop_message = f"Stopped the agent: {reason_text}. Its changes stay in the working tree."
            else:
                stop_message = f"Tried to stop the agent but it is still running: {reason_text}"
            notifications.append(("yt-pipeline watchdog", labelled(stop_message, label)))

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
        "run_cap_usd": args.run_cap,
        "run_started_at": run_started_at,
        "run_alerted": list(state.get("run_alerted", [])),
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
