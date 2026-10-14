---
name: yt-llm-director
description: Turn a brief and a workspace shotlog into edit.json with a three-role LLM loop — screenwriter (writes shot_plan.json), editor (picks the exact cut windows) and reviewer (checks the result against the brief and the editing rules and sends fixes back, max 3 rounds). The model and the budget come from the config, the prompts from prompts/*.md, and the rule-based scripts/director.py is the fallback when the loop fails and the --dry-run baseline. Use when the user asks for an LLM-directed edit, a shot plan, a story-driven cut, or wants the screenwriter/editor/reviewer loop. Trigger phrases - "fă editul cu LLM", "planul de cadre", "screenwriter editor reviewer", "directorul LLM", "llm director", "shot_plan.json", "build the edit with the model". Not for the deterministic rule-based cut (that is `director`), analysing footage (`ingest`/`motion`/`quality`/`describe`/`similarity`), indexing the shotlog (that is `index`) or rendering the MP4 (that is `render`).
compatibility: Run from the repository root. Needs the repo .venv and, for the real loop, an OpenRouter key and models.director in config.yaml; works offline with llm_backend mock (it then falls back to the rules). Reads shotlog.json, ingest.json, motion/<stem>.json and quality/<stem>.json from the workspace read-only and writes shot_plan.json and edit.json atomically. Spend goes to WORKSPACE/llm_spend.json and is capped by llm_budget_usd.
---

# yt-llm-director

Direct an edit with a model in three roles instead of the fixed rules. The screenwriter reads the brief and the shotlog and writes `shot_plan.json` (structure, beats, which shot goes where and roughly how long). The editor turns the plan into exact source timecodes. The reviewer checks the draft against the brief and the editing rules and sends fixes back for at most three rounds. When the loop cannot finish, the rule-based `scripts/director.py` takes over. All commands run from the repository root.

## Step 1: direct an edit with the loop

```bash
.venv/bin/python scripts/llm_director.py --workspace /path/to/workspace \
  --brief "about 90 seconds, chronological, calm start, more energy towards the end, ambient sound only"
.venv/bin/python scripts/llm_director.py --workspace /path/to/workspace --brief-file brief.txt --json
.venv/bin/python scripts/llm_director.py --workspace /path/to/workspace --brief "..." --rounds 2 --out my_edit.json
```

- `--workspace` is required and must hold `shotlog.json`, `ingest.json`, `motion/<stem>.json` and `quality/<stem>.json` — exactly what `scripts/director.py` reads. Without `shotlog.json` it falls back to the rules immediately and says so in the warnings.
- Give exactly one of `--brief` (inline text) or `--brief-file` (path). Neither exits 1 with one `[error] ...` line on stderr.
- `--out` (default `WORKSPACE/edit.json`) is the edit list; `--plan-out` (default `WORKSPACE/shot_plan.json`) is the plan.
- `--rounds` (default 3, max 3) is the number of review rounds. `--model` overrides `models.director`; `--budget-usd` overrides `llm_budget_usd`; `--important-max-s` (default 8.0) caps a hero or semantic-move slot.
- `--target-duration-s`, `--order` and `--pacing` override the hints parsed from the brief; without them the brief is parsed the same way `router.py` parses it.
- `--fallback-rules` (default) falls back to `scripts/director.py` when the loop fails; `--no-fallback-rules` makes the failure an error instead.
- `--force` rewrites even when the output is up to date. `--json` prints the machine-readable summary. It never asks questions.

## Step 2: dry run and the rule-based baseline

```bash
.venv/bin/python scripts/llm_director.py --workspace /path/to/workspace --brief "..." --dry-run --json
```

`--dry-run` runs **only** the screenwriter: it writes `shot_plan.json`, calls no editor and no reviewer, and writes no `edit.json`. It also runs `scripts/director.py` as a subprocess with a throwaway `--out` and reports the rule-based baseline — the slot count, the total duration and the top reasons — so the LLM edit can be compared against the deterministic one. The `--json` summary carries a `baseline` object with `slots`, `total_duration_s` and `reasons`.

## Step 3: read the result

`shot_plan.json` is validated against `schemas/shot_plan.schema.json` and `edit.json` against `schemas/edit.schema.json` before they are written, so a run either leaves complete, valid files or none.

The `--json` summary is a flat object: `status` (`written`/`skipped`/`fallback`), `out`, `plan`, `rounds_used`, `approved` (bool or null), `fallback` (null or `"rules"`), `slots` (video slots written), `candidates` (rows offered to the screenwriter), `total_duration_s`, `validator_violations` (count of the last validation), `warnings`, `wall_time_s` and `llm_cost_usd` (the sum of the run's calls). `--dry-run` adds `baseline` and `dry_run`.

Exit codes: `0` when an edit was written (including a fallback), `2` when the budget ran out and no fallback was written, `1` for any other error (one `[error] ...` line on stderr).

## How the loop works

- **Shot facts.** For every eligible shot the tool computes the same usable window the rule-based director computes: the hold segment bounds for a hold, the overlapping motion move's handle window for a move, and for a **phrase** (a move with the hold it lands on) the move's handle-in through the hold's end with `landing_at_s` set to the hold's start. When the shotlog carries **content beats** for a quality segment (`beat_of` set), the segment contributes one fact per beat — key `<clip counter>/<segment id><beat id>` (e.g. `0072/s7b1`) — each with the beat's own range intersected with the move window, so a long take offers its approach and its exit as separate cuttable shots. `keep_whole` is true for a hero or a complete semantic move — it is the shot's own flag and the plan cannot turn it off. Only the shots that come out of this step with a usable window are offered to the screenwriter — the same cuttable pool the editor can cut — and the summary reports their count as `candidates`. The editor gets the facts of the planned shots, with the shot's own `keep_whole` and the plan's `target_s`.
- **Screenwriter.** One call with `prompts/screenwriter.md`; the answer is parsed (markdown fences tolerated) and completed into a full plan, then validated. A parse or schema failure is retried once; a plan that still will not validate is a loop failure.
- **Editor.** One call with `prompts/editor.md`; the answer becomes one video slot per plan slot, in plan order, with `clip`, `in_s`, `out_s` from the answer, `score`/`reason`/`role`/`motion_label`/`description` from the shotlog row (`reason` = the shotlog reason plus the plan's `why`), `cut` on the first slot and `crossfade` 0.3 on the others unless the editor said `cut`, `speed` 1.0, and the same `audio`/`captions`/`graphics` shape `scripts/director.py` writes. When the model's window breaks one of the two length rules the editor prompt states, the tool applies the spec's formula itself instead of looping to a fallback and records a `fixed <slot>` warning: a hero or semantic shot gets `window_out_s` with `window_in_s` at most `window_out_s - important_max_s` (the tail), and an ordinary slot is trimmed to its target inside 2–12 s. A parse or schema failure is retried once. A plan slot the editor left without a usable answer is skipped and reported to the reviewer as `missing`; if it is still missing in the last draft the tool synthesises it from the shot fact (the spec's window, `fixed <slot>` warning) so a plan slot is never dropped. An answer with no usable slot at all is retried and, if it stays empty, is a loop failure.
- **Validator (code, not the model).** The draft is checked against the editing rules and every violation (slot id, rule, message) is handed to the reviewer. The rules: every plan slot has a cut window (`missing` when the editor produced none); every slot stays inside its shot's usable window; a landing phrase ends inside its hold at or after `landing_at_s` and its landing hold is never planned again; hero and complete semantic moves keep their whole window up to `--important-max-s`, keeping the tail; ordinary slots are 2–12 s; two slots from the same take are never back to back unless their windows touch within 0.5 s; two visually similar shots are never adjacent.
- **Reviewer.** One call with `prompts/reviewer.md`; `approved: true` with no violations ends the loop. Otherwise the fixes go back to the editor for another round. If the reviewer never approves, the last draft is kept only when it has zero violations (with a warning); otherwise the loop fails.
- **Budget.** Every call is charged to `WORKSPACE/llm_spend.json` and capped by `llm_budget_usd`. A `BudgetExceeded` stops the loop, keeps whatever valid artifacts exist and falls back to the rules (or exits 2 with `--no-fallback-rules`).
- **Fallback.** `scripts/director.py` is run as a subprocess with the same workspace, brief and parsed hints; its edit is copied to `--out` and the summary reports `status: "fallback"`, `fallback: "rules"`. The director code is never imported.

## Notes

- Re-run safety: when `--out` exists, validates and is newer than the shotlog, the brief file and `shot_plan.json`, nothing is written and the summary reports `status: "skipped"`. `--dry-run` always runs the screenwriter.
- The rule-based `scripts/director.py` stays the deterministic tool; this one is the model-driven loop on top of the same shotlog and the same editing rules.
- Outputs go to the workspace, never into the footage folder. For the deterministic cut see the `director` skill; for the render see `render`.
