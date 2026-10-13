---
name: yt-watchdog
description: Check whether the build agent is overspending on OpenRouter or stalled, and alert Doru on ntfy only when something is wrong. Use when the user asks if the agent is stuck, how much the agents spent today, or wants the watchdog set up or tested. Trigger phrases - "agentul s-a blocat?", "cât a cheltuit azi", "verifică paznicul", "is the agent stuck", "watchdog". Not for phase reports or normal status messages (that is scripts/notify.py).
compatibility: Run from the repository root on the machine where the agent runs (the LXC). Needs pgrep and network access to openrouter.ai and the ntfy server. No GPU, no LLM cost.
---

# yt-watchdog

`scripts/agent_watchdog.py` runs two checks and alerts once per problem:

- **spend** — today's OpenRouter usage for the agents' key above `--spend-usd` (default 3). Once per UTC day.
- **stall** — an `opencode` process runs but no file changed in the repo or `~/.local/share/opencode` for `--stall-hours` (default 2). Once per stall episode.

```bash
.venv/bin/python scripts/agent_watchdog.py --repo ~/projects/yt-pipeline --dry-run --json
.venv/bin/python scripts/agent_watchdog.py --repo ~/projects/yt-pipeline --spend-usd 3 --stall-hours 2
```

The key comes from `OPENROUTER_API_KEY` or `--key-file` (default `~/.config/opencode/secrets/openrouter.key`) and is never printed. Alerts already sent are remembered in `~/.cache/yt-pipeline/watchdog-state.json`. `--dry-run` sends nothing and keeps no state.

Cron (every 30 minutes) is described in `docs/AGENT_SETUP.md`, section 11. Rejections and empty coder replies are reported by the architect itself (see `.opencode/agents/architect.md`), not by this script.
