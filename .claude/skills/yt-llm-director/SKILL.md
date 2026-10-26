---
name: yt-llm-director
description: Turn a brief and a shotlog into edit.json with a film crew — one director call writes director_plan.json (the idea, structure and shots), the code editor cuts it into edit.json and editor_report.json without changing the shots, and the director reviews the cut (one picture per shot plus the report) for at most 2 rounds. The rule-based scripts/director.py is the fallback when the model gives no usable plan and the --dry-run baseline. Agent mode (--prepare/--apply) writes the plan with no model call; --chat talks to the director. Use when the user asks for an LLM-directed edit, a story-driven cut or a director plan. Trigger phrases - "fă editul cu LLM", "planul de cadre", "directorul LLM", "llm director", "director_plan.json", "build the edit with the model", "ask the director", "agent mode", "prepare and apply", "directorul agent". Not for the rule-based cut (`director`), analysing footage (`ingest`/`motion`/`quality`/`describe`/`similarity`), indexing the shotlog (`index`) or rendering (`render`).
compatibility: Run with the `yt` entry point on PATH. Needs the repo .venv and, for the real flow, an OpenRouter key and models.director in config.yaml; works offline with llm_backend mock (it then falls back to the rules). Reads shotlog.json, ingest.json, motion/<stem>.json and quality/<stem>.json from the workspace read-only and writes director_plan.json, edit.json, editor_report.json and director_rounds.json atomically. Spend goes to WORKSPACE/llm_spend.json and is capped by llm_budget_usd.
---

# yt-llm-director

Direct an edit with a model as a film crew. One director call reads the brief and the shortlist of shot facts and writes `director_plan.json` — the idea, the structure and the shots. The code editor (`director_editor.py`) executes the plan: it cuts the exact windows into `edit.json` and answers with `editor_report.json`, never changing, swapping, dropping or reordering the shots. The director then reviews the cut — one picture per shot plus the report — and approves it or sends a complete revised plan with its notes, for at most 2 rounds. When the model gives no usable plan, the rule-based `scripts/director.py` takes over. All commands run through `yt <tool>`.

## Step 1: direct an edit with the film crew

```bash
yt llm_director --workspace /path/to/workspace \
  --brief "about 90 seconds, chronological, calm start, more energy towards the end, ambient sound only"
yt llm_director --workspace /path/to/workspace --brief-file brief.txt --json
yt llm_director --workspace /path/to/workspace --brief "..." --rounds 2 --out my_edit.json
```

- `--workspace` is required and must hold `shotlog.json`, `ingest.json`, `motion/<stem>.json` and `quality/<stem>.json`. Without `shotlog.json` (or with no eligible shots) it falls back to the rules immediately and says so in the warnings.
- Give exactly one of `--brief` (inline text) or `--brief-file` (path). Neither exits 2 with one `[error] ...` line on stderr.
- `--out` (default `WORKSPACE/edit.json`) is the edit list; `--plan-out` (default `WORKSPACE/director_plan.json`) is the plan.
- `--rounds` (default 2, max 2) is the number of review rounds. `--model` overrides `models.director`; `--budget-usd` overrides `llm_budget_usd`; `--important-max-s` caps a hero or semantic-move slot (default: from the pacing table for the brief's target duration, 6.0 s ceiling).
- `--reasoning-effort` (`low`, `medium`, `high` or `off`) sets how much hidden reasoning the model spends; it overrides `director.reasoning_effort` in the config, which defaults to `medium`. Every call the flow makes — the plan call, the repair retry and the review call — carries it as OpenRouter's `reasoning` body (`off` disables reasoning). There is no environment variable.
- `--beats FILE` (optional) is the beat grid JSON from `scripts/beats.py --out` (keys `beat_s`, `downbeat_s`, `beat_period_s`/`duration_s`, either under `measurement` or at the top level; times are absolute seconds in the music, which starts at timeline 0). It is handed to the editor, which snaps every timeline cut to a beat (preferring a downbeat, then a half-bar beat, then the nearest beat, within the slot's cap) and joins every non-first slot with a hard cut. The rule-based fallback and the `--dry-run` baseline get the same grid.
- `--music FILE` and `--ambient-under-music-db DB` are recorded in `edit.json` `audio.music` and `audio.ambient_under_music_db` by the editor, so the render's mix is reproducible from the edit alone.
- `--speed-ramp` (default off) lets the middle half of long camera moves play up to 1.2x on screen; without it every slot plays at normal speed. The editor writes `middle_speed` and `meta.speed_ramp` records the setting; a changed value re-runs the edit.
- `--target-duration-s`, `--order` and `--pacing` override the hints parsed from the brief; without them the brief is parsed the same way `router.py` parses it.
- `--fallback-rules` (default) falls back to `scripts/director.py` when the model gives no usable plan; `--no-fallback-rules` makes that an error instead.
- `--feedback TEXT` (repeatable) revises the current edit from plain words against its short ids, e.g. `--feedback "drop 0071/s2, tunnel longer"`. Every flag is appended as one JSON line (`ts`, `action`, `text`, `tool`) to `WORKSPACE/feedback.jsonl` (the learning store, append-only, written even when the run later falls back), the up-to-date check is skipped for this run, and the director call gains a block with the feedback texts, the previous edit's used slots (id, `shot_key`, clip, in/out or duration, reason) and its rejected candidates (id + reason from `edit.json` `decisions[]`), so every short id in the feedback resolves; with no previous `edit.json` the block says so and the feedback still reaches the director.
- `--force` rewrites even when the output is up to date. `--json` prints the machine-readable summary. It never asks questions.
- The director reads the instance **notebook** before every edit (the file at `--notebook`, else `YT_PIPELINE_NOTEBOOK`, else `notebook.path` in the config) and hands its lessons to the director call; with no notebook set it reads nothing. The notebook is edited with `yt-notebook`.

## Step 2: how the flow runs

- **One director call.** `prompts/director.md` plus a payload of the brief, the content type, the shortlist's shot facts, the feedback block and the notebook. The answer is parsed (markdown fences tolerated) and validated against `schemas/director_plan.schema.json`; a parse or schema failure is retried once with the exact validation errors, and a plan that still will not validate is a flow failure (fallback or error).
- **The code editor.** `director_editor.py` runs as a subprocess with the plan, the workspace, `--beats`/`--music`/`--ambient-under-music-db`/`--speed-ramp` and writes `edit.json` and `editor_report.json`. It never changes the shots; what it cannot do goes in the report's `could_not_do` and reaches the warnings.
- **The notes round.** One picture per shot (the middle frame, under `director/rounds/round-N/`) plus the report go back to the director, which answers `director_review.json`: `approved: true`, or `approved: false` with a complete revised plan and its notes. A not-approved review re-runs the editor on the revised plan. At most 2 rounds; after the second round the cut is delivered with the remaining notes in `edit.json` `meta.director.notes`.
- **The record.** `director_rounds.json` records every round (the plan, the report, the review summary and notes, the round's cost). `edit.json` `meta.director` carries `by` (`llm`, `agent` or `rules`), `approved` and the notes; `decisions[]` gets one used row per plan shot (purpose, why, technique done or not) plus the editor's could-not-do items. `explain.html` and the web page show them.
- **The fallback.** `scripts/director.py` runs as a subprocess with the same workspace, brief and parsed hints; its edit is copied to `--out` and the summary reports `status: "fallback"`, `fallback: "rules"`. The director code is never imported.
- **Budget.** Every call is charged to `WORKSPACE/llm_spend.json` and capped by `llm_budget_usd`. The ledger is cumulative across runs and phases, so the cap applies to its running total — for a per-round budget set `--budget-usd` to the ledger total plus what the round may still spend. A `BudgetExceeded` stops the flow, keeps whatever valid artifacts exist and falls back to the rules (or exits 3 with `--no-fallback-rules`).

## Step 3: dry run and the rule-based baseline

```bash
yt llm_director --workspace /path/to/workspace --brief "..." --dry-run --json
```

`--dry-run` runs **only** the director call: it writes `director_plan.json`, calls no editor and no review, and writes no `edit.json`. It also runs `scripts/director.py` as a subprocess with a throwaway `--out` and reports the rule-based baseline — the slot count, the total duration and the top reasons — so the model's plan can be compared against the deterministic cut. The `--json` summary carries a `baseline` object with `slots`, `total_duration_s` and `reasons`.

## Step 4: read the result

`director_plan.json` is validated against `schemas/director_plan.schema.json`, `edit.json` against `schemas/edit.schema.json` and `editor_report.json` against `schemas/editor_report.schema.json` before they are written, so a run either leaves complete, valid files or none.

The `--json` summary is a flat object: `status` (`written`/`skipped`/`fallback`/`error`), `out`, `plan`, `rounds_used`, `approved` (bool or null), `fallback` (null or `"rules"`), `slots` (video slots written), `candidates` (the full eligible count), `shortlist` (rows offered to the director), `total_duration_s`, `validator_violations` (0 in this flow), `warnings`, `wall_time_s`, `llm_cost_usd` (the sum of the run's calls), `beat_snap` (the editor's snap tally `{"beats", "snapped", "unsnapped", "downbeats", "half_bar"}`, or null without a grid), `applied_keys` and `not_applied_keys` (the rule fallback's owner keys, `[]` otherwise). `--dry-run` adds `baseline` and `dry_run`.

Exit codes follow the table: `0` success (an edit was written, including a fallback), `1` finished but a human must look at the result, `2` usage, input or config error (nothing ran), `3` runtime failure after work started (including the budget running out with no fallback written).

## Step 5: agent mode (prepare and apply)

A host coding agent can direct the edit without an OpenRouter key. `--prepare` writes `<workspace>/director/inputs.md` — the brief, the brief settings, the editing rules, the shortlisted shots and their shot facts, the director prompt, the payload and the two output shapes — and makes no model call. The agent writes `<workspace>/director_plan.json` (schema `schemas/director_plan.schema.json`), then `--apply` validates it, runs the code editor and writes the final `edit.json` with `meta.director.by = "agent"`. For the notes round, `--prepare --review` writes the pictures and the editor report into `inputs.md`; the agent writes `<workspace>/director_review.json` (schema `schemas/director_review.schema.json`) and `--apply` applies it.

```bash
yt llm_director --workspace /path/to/workspace --brief "..." --prepare --json
yt llm_director --workspace /path/to/workspace --brief "..." --apply --json
yt llm_director --workspace /path/to/workspace --brief "..." --prepare --review --json
```

- Both require `--workspace` and exactly one of `--brief` / `--brief-file`. `--prepare` accepts `--out`, `--plan-out`, `--important-max-s`, `--target-duration-s`, `--order`, `--pacing`, `--beats`, `--music`, `--ambient-under-music-db`, `--speed-ramp`, `--notebook` and `--json`; `--apply` accepts the same plus `--force`. `--prepare` does not accept `--force`.
- `--prepare`, `--apply`, `--chat` and `--dry-run` are mutually exclusive; more than one is a usage error naming the conflict. `--review` needs `--prepare`.
- `--prepare` needs `shotlog.json` (no fallback) and writes no `edit.json`; the `--json` summary is `{"status": "prepared", "inputs", "candidates", "shortlist", "review", "warnings", "wall_time_s", "llm_cost_usd": 0.0}`.
- `--target-duration-s 0` ("All good shots") works in agent mode exactly as in the flow: the target becomes the `estimate_all_good` seconds (at least 20 s) and the long-cut pacing is used.
- `--apply` reads the plan (or the review), rejects an invalid one with a usage error naming the first blocking rule, and writes the final edit; the `--json` summary is `{"status": "written", "out", "plan", "slots", "total_duration_s", "validator_violations", "rounds_used", "shortlist", "warnings", "wall_time_s", "llm_cost_usd": 0.0}`. When `edit.json` is already a full edit (a `video` list) and `--force` is not given, it writes nothing and reports `status: "skipped"`; with `--force` it re-applies from the plan.
- After `--apply` the host agent runs the same learning pass the web app runs: `yt notebook --notebook <file> --learn --workspace <workspace>` makes one cheap `metadata`-class model call and writes at most 3 short lessons from the feedback since the last pass (marker `<workspace>/.learn.json`). It never blocks the edit and a failure only logs.

## Step 6: chat with the director

```bash
yt llm_director --workspace /path/to/workspace --chat "drop 0071/s2, tunnel longer" --json
yt llm_director --workspace /path/to/workspace --chat "why did you open with the boat?" --version v003
```

`--chat TEXT` makes one director call with the director prompt plus the chat addendum, the last plan, the editor report, the rounds, the owner's earlier notes and the notebook; the answer is a plain reply and, when the owner decided something, a short `decision_note` stored as an owner note in `feedback.jsonl` and carried into the next run's feedback. The chat never edits the cut. `--version vNNN` reads the shown version's `edit.json` instead of the workspace one. The `--json` summary carries `reply`, `decision_note`, `can_do` (`"all" | "part" | "none"`), `missing_tool`, `estimate_usd`, `estimate_s`, `wall_time_s` and `llm_cost_usd`; the web app appends one `missing_tool` line to the requests file when `can_do` is not `"all"`.

## Notes

- Re-run safety: when `--out` exists, validates and is newer than the shotlog, the brief file, the plan and the beat grid, nothing is written and the summary reports `status: "skipped"`. `--dry-run` always runs the director call.
- The rule-based `scripts/director.py` stays the deterministic tool; this one is the model-driven film crew on top of the same shotlog and the same editing rules.
- Outputs go to the workspace, never into the footage folder. For the deterministic cut see the `director` skill; for the editor alone see `director-editor`; for the render see `render`.
