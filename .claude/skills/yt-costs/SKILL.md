---
name: yt-costs
description: Keep a small SQLite ledger of what the project costs and earns — model jobs, build-agent spend, fixed monthly costs, one-off investments and donations — with idempotent imports and a totals view. Use when the user asks what the project costs or earns, wants to add a cost or a donation, import the model or build-agent spend, or see the balance. Trigger phrases - "cât costă proiectul", "cât am cheltuit", "adaugă o donație", "importă cheltuielile", "cât e balanța", "costs ledger", "add a cost", "import the spend", "what is the balance", "totals". Not for the per-run LLM budget cap (that is `scripts/common/llm.py` and the watchdog), not for the read-only Costs page in the web app (that is `scripts/serve.py`, `GET /costs`) and not for any donation payment handling.
compatibility: Run from the repository root. Needs the repo .venv (stdlib only — sqlite3, json). The ledger is a SQLite file outside the repository; no model is called, so llm_cost_usd is always 0.0. Reads llm_spend.json files and a run-spend JSON read-only.
---

# yt-costs

The costs ledger tool. It keeps one SQLite file of what the project costs and earns and answers with a `totals` view. All commands run from the repository root.

## The ledger path (three ways)

The ledger is a SQLite file **outside the repository**. Its path comes from:

1. `--ledger PATH`, else
2. the `YT_PIPELINE_COSTS_DB` environment variable, else
3. `costs.path` in the config.

With none set the tool stops with a usage error naming those three ways. Docs and examples write `<costs.db>`. There is no personal default.

## Commands

```bash
.venv/bin/python scripts/costs.py --ledger <costs.db> add --kind donation --amount 10 --date 2026-10-01
.venv/bin/python scripts/costs.py --ledger <costs.db> import-jobs --workspaces <workspaces>
.venv/bin/python scripts/costs.py --ledger <costs.db> import-dev --from <run-spend.json>
.venv/bin/python scripts/costs.py --ledger <costs.db> totals --month 2026-10 --json
.venv/bin/python scripts/costs.py --ledger <costs.db> list --kind model_job
```

- `add --kind K --amount USD [--date D] [--note TEXT]` appends one manual entry. `--kind` is one of the five kinds, `--amount` is a finite number `> 0`, `--date` defaults to today and must match `YYYY-MM-DD`. `source` is `null`.
- `import-jobs --workspaces DIR` reads every immediate subdirectory's `llm_spend.json` (through `common.llm.read_ledger`) and writes one `model_job` entry per project; `source` is the project directory name, `date` the file's modification date. An unreadable ledger is skipped with a warning on stderr and does not fail the run.
- `import-dev --from FILE` reads the build agents' run-spend JSON (a list, or an object with an `entries` list) and sums `cost_usd` per calendar day; one `dev_spend` entry per day, `source` the date.
- `totals [--month YYYY-MM]` sums per kind, computes the recurring monthly cost, the fixed total over the months since the first fixed entry and the balance.
- `list [--kind K]` lists entries, optionally filtered by kind.

## Kinds

`model_job`, `dev_spend`, `fixed_monthly`, `investment`, `donation`.

## Idempotent imports

Both imports are idempotent by `source`: the project name for model jobs, the date for dev spend. Re-running updates the existing row instead of inserting a duplicate, so `count` stays stable.

## JSON shapes

Every subcommand accepts `--json` (before or after the subcommand) and reports `wall_time_s` and `llm_cost_usd` (`0.0`).

- `add`: `{"ok": true, "entry": {"id", "date", "kind", "amount_usd", "note", "source"}, ...}`
- `import-jobs`: `{"ok": true, "imported": [{"project", "total_usd", "action"}], "count", ...}`
- `import-dev`: `{"ok": true, "imported": [{"date", "cost_usd", "action"}], "count", ...}`
- `totals`: `{"ok": true, "month", "by_kind": {...}, "monthly_running_usd", "development_total_usd", "model_total_usd", "donations_usd", "investments_usd", "fixed_months", "fixed_total_usd", "balance_usd", ...}` — the object the web page will read.
- `list`: `{"ok": true, "entries": [...], "count", ...}`

## Exit codes

`0` success; `2` usage error (bad flag, unknown kind, bad date or month, missing ledger path); `3` runtime failure after work started (SQLite or IO). A `--json` failure prints one `{"ok": false, "error": "...", "exit_code": N}` object on stdout and the human `[error]` line on stderr.

## Notes

- The ledger is written outside the repository; never commit it.
- No model is called and no donation payment is handled.
- The web app's read-only **Costs** page (`scripts/serve.py`, `GET /costs`) reads the same `totals` object through `scripts/common/costs.py`; the shared module is the one place the totals math lives.
