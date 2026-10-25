---
name: yt-notebook
description: Read and edit the instance's learning notebook — a small JSON file of short lessons the director keeps between edits, at most 40 of them — and run the learning pass that writes lessons from the feedback. Use when the user wants to see the lessons the director learned, add a lesson, remove a stale one, merge overlapping ones, propose moving a repeated sentence (for example the sponsor line) to channel.fixed, or make the notebook learn from the feedback after an edit. Trigger phrases - "ce lecții a învățat directorul", "adaugă o lecție", "scoate lecția veche", "combină lecțiile", "propune mutarea propoziției în channel.fixed", "fă notebook-ul să învețe", "the director's notebook", "add a lesson", "remove a stale lesson", "merge overlapping lessons", "propose moving a repeated sentence", "learn from the feedback". Not for building the edit (that is `yt-llm-director` / `yt-director`), not for writing the config (the owner says yes by editing channel.fixed) and not for the web app (that is `serve.py`).
compatibility: Run with the `yt` entry point on PATH. Reads and writes one JSON file (the notebook path from --notebook, else YT_PIPELINE_NOTEBOOK, else notebook.path in the config; no personal default). The read/edit commands make no model call and no spend; `--learn` makes one cheap metadata-class model call charged to the notebook ledger next to the notebook file (`notebook.spend.json`), plus one more merge call when the notebook is full. Safe to re-run; a write past the 40-lesson limit is refused and nothing is written.
---

# yt-notebook

The **learning notebook** is a small JSON file of short lessons the director keeps between edits on this instance. `yt notebook` reads and edits it and enforces a fixed limit of **40 lessons**. The director (`yt-llm-director`) reads the notebook before every edit and hands it to the screenwriter, the editor and the reviewer; the web app shows it as a collapsible section in the **Director** tab, where a lesson can be deleted. All commands run through `yt <tool>`.

## Step 1: read the notebook

```bash
yt notebook --notebook /path/to/notebook.json --list
```

- `--notebook` is the notebook file. Without it the path comes from `YT_PIPELINE_NOTEBOOK`, else `notebook.path` in the config; with none of the three set it is a usage error naming those three ways. There is no personal default.
- A missing file is an empty notebook (no error); the first write creates it.
- `--list` prints the notebook and the count (`notebook: N/40 lessons`).

## Step 2: edit the notebook

```bash
yt notebook --notebook /path/to/notebook.json --add "keep the tunnel longer"
yt notebook --notebook /path/to/notebook.json --remove l003
yt notebook --notebook /path/to/notebook.json --merge l001,l002 --text "keep the tunnel longer and land on the hold"
yt notebook --notebook /path/to/notebook.json --propose members_sentence --text "the sponsor line repeats in every clip"
```

- Exactly one command is required: `--list`, `--add`, `--remove`, `--merge`, `--propose` or `--learn`. `--add` and `--remove` are repeatable.
- Ids are `lNNN` (zero-padded to 3, one past the highest id across the lessons and the waiting lessons). `--add` appends a `kind: "lesson"` entry; `--propose` appends a `kind: "proposal"` entry with a `part`.
- `--merge IDS --text TEXT` removes the named lessons and appends one `kind: "lesson"` entry with the new text. `--remove ID` removes one lesson. An unknown id is a usage error naming the id; the same id twice in `--merge` is a usage error too.
- `--propose PART --text TEXT` records a proposal to move a repeated sentence to `channel.fixed.<part>`. `PART` must be one of `members_sentence`, `closing`, `thumbnail_formula`; anything else is a usage error naming the valid parts. **The code never writes the config** — the owner says yes by editing `channel.fixed` himself.
- `--limit N` overrides the 40-lesson limit (a test override). When the resulting notebook would hold more than the limit, the write fails with `EXIT_USAGE` and the message `notebook is full (40 lessons): merge overlapping lessons or remove stale ones first, then retry`; nothing is written.
- The file is written atomically and validated against `schemas/notebook.schema.json` before the write.

## Step 3: run the learning pass

```bash
yt notebook --notebook /path/to/notebook.json --learn --workspace /path/to/workspace
```

- `--learn` reads the workspace's `feedback.jsonl` rows newer than the `last_learn_at` in the machine-owned marker `<workspace>/.learn.json` (all rows when the marker is absent), makes **one cheap `metadata`-class model call** through `common/llm.py` (the cost is charged to the notebook ledger next to the notebook file) and writes at most 3 short lessons. A lesson is a general rule for any project and never names a shot id, a clip number or a project name; the learn pass skips such a lesson with a `[warn]` line and still succeeds. When the 40-lesson limit would be passed it makes **one more merge call** (`prompts/notebook_merge.md`) to merge overlapping lessons or remove stale ones; lessons that still do not fit are kept as `waiting` lessons in the file (never dropped) and promoted into `lessons` as soon as there is room. The `waiting` list holds at most 3 lessons in total; when 3 are already waiting the pass makes no model call, writes nothing and leaves the marker (`--json` `status: waiting_full`, `llm_cost_usd` 0.0), and when some room is left it asks the model for at most the remaining room's worth of new lessons, so nothing is learned or paid twice. A notebook that already holds more lessons than the limit (hand-edited or a lower `--limit`) makes the pass return `waiting_full` with no model call and no write, the over-limit lessons staying in the file as lessons until the owner removes some; the overflow to `waiting` is capped at 3, the rest staying in `lessons`. After the merge step any lesson past the limit (merged ones included) goes to `waiting`, newest first, so the written notebook never holds more than the limit.
- `--workspace PATH` is required with `--learn` and only valid with it; a missing workspace is a usage error. With no new feedback the pass is skipped, makes no model call and writes nothing (the marker stays).
- The model answer is `{"lessons": [{"text", "kind", "part"}], "remove": ["lNNN"]}`; a `proposal` needs a `part` in `channel.fixed`. An unknown id in `remove` is ignored with a warning. The merge answer is `{"merge": [{"ids": ["l001", "l002"], "text": "..."}], "remove": ["lNNN"]}`; a merge with fewer than 2 known ids is skipped with a warning.
- After a successful write the marker `<workspace>/.learn.json` records `{"last_learn_at": ...}`. The web app runs this pass in the background after each new version it makes: after a successful run, after a shot is dropped or trimmed, and after a version is deleted. It is skipped when there is no new feedback.

## Step 4: read the result

```bash
yt notebook --notebook /path/to/notebook.json --add "..." --json
yt notebook --notebook /path/to/notebook.json --learn --workspace /path/to/workspace --json
```

The `--json` output is a single object. For a read/edit command, e.g. `{"ok": true, "notebook": "...", "count": 3, "limit": 40, "waiting": 0, "changed": {"added": ["l003"], "removed": [], "merged": [], "promoted": []}, "lessons": [...], "wall_time_s": 0.0, "llm_cost_usd": 0.0}`. For `--learn`, e.g. `{"ok": true, "status": "written", "notebook": "...", "count": 3, "limit": 40, "waiting": 0, "added": ["l003"], "removed": [], "model": "...", "wall_time_s": 0.0, "llm_cost_usd": 0.01}`; a skipped pass has `"status": "skipped"`, `"reason": "no new feedback"` and `llm_cost_usd` 0.0; a pass whose waiting list is full has `"status": "waiting_full"`, `"reason": "waiting lessons are full"` and `llm_cost_usd` 0.0. The reason is `"waiting lessons are full"` when 3 wait and `"the notebook is over its limit"` when the file is over the limit.

## Notes

- Exit codes: `0` success (written or skipped); `2` usage (no command, unknown id, limit reached, bad notebook JSON, no path, bad `--propose` part, `--learn` without `--workspace`, missing workspace, missing `OPENROUTER_API_KEY`); `3` runtime (model call failed, unparseable answer, write failure). Every failure with `--json` prints one `{"ok": false, "error": ..., "exit_code": N}` object.
- A learn pass that still does not fit after the merge call keeps the new lessons as `waiting` lessons in the file (never dropped); the web app shows them with the same delete form and the director never sees them. The `waiting` list holds at most 3 lessons in total; the newest lessons past the limit go there first. A notebook that already holds more lessons than the limit (hand-edited or a lower `--limit`) makes the pass return `waiting_full` with no model call and no write, the over-limit lessons staying in the file as lessons until the owner removes some; the overflow to `waiting` is capped at 3, the rest staying in `lessons`. The learn-pass cost is charged to the notebook ledger next to the notebook file (`notebook.spend.json`) and counted in the instance total model cost, never in the per-clip list.
- Related skills: `yt-llm-director` (the director reads the notebook before every edit and the host agent runs the learn pass after `--apply`) and `yt-director` (the rule-based edit).
- The notebook is per instance and optional: with no path set the director reads nothing and the web app shows no section.
