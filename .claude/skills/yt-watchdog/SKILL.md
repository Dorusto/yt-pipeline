---
name: yt-watchdog
description: Check whether the build agent is overspending on OpenRouter or stalled, and alert Doru on ntfy only when something is wrong. Use when the user asks if the agent is stuck, how much the agents spent today, or wants the watchdog set up or tested. Trigger phrases - "agentul s-a blocat?", "cât a cheltuit azi", "verifică paznicul", "is the agent stuck", "watchdog". Not for phase reports or normal status messages (that is scripts/notify.py).
compatibility: Run from the repository root on the machine where the agent runs (the LXC). Detects the agent process with psutil when available, else pgrep (POSIX) or tasklist (Windows). Needs network access to openrouter.ai and the ntfy server. No GPU, no LLM cost.
---

# yt-watchdog

`scripts/agent_watchdog.py` runs two checks and alerts once per problem:

- **spend** — today's OpenRouter usage for the agents' key above `--spend-usd` (default 3). Once per UTC day.
- **stall** — an `opencode` process runs but no file changed in the repo or `~/.local/share/opencode` for `--stall-hours` (default 2). Once per stall episode.

```bash
.venv/bin/python scripts/agent_watchdog.py --repo <repo> --dry-run --json
.venv/bin/python scripts/agent_watchdog.py --repo <repo> --spend-usd 3 --stall-hours 2
.venv/bin/python scripts/agent_watchdog.py --repo <repo> --run-cap 6 --lot "S51 lot 1" --start-run --dry-run --json
```

The key comes from `OPENROUTER_API_KEY` or `--key-file` (default `~/.config/opencode/secrets/openrouter.key`) and is never printed. Alerts already sent are remembered in `~/.cache/yt-pipeline/watchdog-state.json`. `--dry-run` sends nothing and keeps no state.

Hard stops (off unless given): `--kill-over-usd X` stops every `opencode` process when today's spend passes X; `--kill-no-commit-hours H` stops it after H hours without a commit. The no-commit clock starts at the later of the last commit and the agent start (the run marker, else the running `opencode` process start time), so a fresh run is never reported as a long no-commit stretch and a future-dated commit never reads as a negative age. After a stop the process is re-checked: the alert says "Stopped the agent" only when it really ended, otherwise "Tried to stop the agent but it is still running". The summary adds `kill_reason`, `stopped`, `stop_verified` and `label`. Three checks now: spend, stall (no file changes) and no-progress (no commit for `--no-commit-hours`, default 1.5, while the agent runs — catches loops that keep writing logs). Every ntfy message is prefixed with `--lot`, else the tmux session running the agent, else nothing. Cron (every 30 minutes) is described in `docs/AGENT_SETUP.md`, section 11. Rejections and empty coder replies are reported by the architect itself (see `.opencode/agents/architect.md`), not by this script.

Per-run spend cap: `--run-cap USD` measures spend **since the run started**, not per day. The run's spend ledger (`--ledger`, default `~/.cache/yt-pipeline/run-spend.json`) accumulates the OpenRouter daily-usage delta as `openrouter-delta` entries, so everything billed to the key counts exactly once. `--start-run` begins a fresh run (new run marker, empty ledger); without it a run starts the first time the agent is seen running after being absent. At 80 % of the cap it sends one ntfy with `--title Budget`; at 100 % it stops the agent and sends `--title Blocked` naming the `--lot` label and the reason. The `--json` summary adds `run_spend_usd`, `run_cap_usd`, `run_started_at` and `run_alerted`.
