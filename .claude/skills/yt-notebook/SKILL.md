---
name: yt-notebook
description: Read and edit the instance's learning notebook — a small JSON file of short lessons the director keeps between edits, at most 40 of them. Use when the user wants to see the lessons the director learned, add a lesson, remove a stale one, merge overlapping ones, or propose moving a repeated sentence (for example the sponsor line) to channel.fixed. Trigger phrases - "ce lecții a învățat directorul", "adaugă o lecție", "scoate lecția veche", "combină lecțiile", "propune mutarea propoziției în channel.fixed", "the director's notebook", "add a lesson", "remove a stale lesson", "merge overlapping lessons", "propose moving a repeated sentence". Not for building the edit (that is `yt-llm-director` / `yt-director`), not for writing the config (the owner says yes by editing channel.fixed) and not for the web app (that is `serve.py`).
compatibility: Run with the `yt` entry point on PATH. Reads and writes one JSON file (the notebook path from --notebook, else YT_PIPELINE_NOTEBOOK, else notebook.path in the config; no personal default). No model call and no spend. Safe to re-run; a write past the 40-lesson limit is refused and nothing is written.
---

# yt-notebook

The **learning notebook** is a small JSON file of short lessons the director keeps between edits on this instance. `yt notebook` reads and edits it and enforces a fixed limit of **40 lessons**. The director (`yt-llm-director`) reads the notebook before every edit and hands it to the screenwriter, the editor and the reviewer; the web app shows it as a collapsible section at the bottom of the project page where a lesson can be deleted. All commands run through `yt <tool>`.

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

- Exactly one command is required: `--list`, `--add`, `--remove`, `--merge` or `--propose`. `--add` and `--remove` are repeatable.
- Ids are `lNNN` (zero-padded to 3, one past the highest id in the file). `--add` appends a `kind: "lesson"` entry; `--propose` appends a `kind: "proposal"` entry with a `part`.
- `--merge IDS --text TEXT` removes the named lessons and appends one `kind: "lesson"` entry with the new text. `--remove ID` removes one lesson. An unknown id is a usage error naming the id.
- `--propose PART --text TEXT` records a proposal to move a repeated sentence to `channel.fixed.<part>`. `PART` must be one of `members_sentence`, `closing`, `thumbnail_formula`; anything else is a usage error naming the valid parts. **The code never writes the config** — the owner says yes by editing `channel.fixed` himself.
- `--limit N` overrides the 40-lesson limit (a test override). When the resulting notebook would hold more than the limit, the write fails with `EXIT_USAGE` and the message `notebook is full (40 lessons): merge overlapping lessons or remove stale ones first, then retry`; nothing is written.
- The file is written atomically and validated against `schemas/notebook.schema.json` before the write.

## Step 3: read the result

```bash
yt notebook --notebook /path/to/notebook.json --add "..." --json
```

The `--json` output is a single object, e.g. `{"ok": true, "notebook": "...", "count": 3, "limit": 40, "changed": {"added": ["l003"], "removed": [], "merged": []}, "lessons": [...], "wall_time_s": 0.0, "llm_cost_usd": 0.0}`. `llm_cost_usd` is always `0.0` — this tool calls no model service.

## Notes

- Exit codes: `0` success; `2` usage (no command, unknown id, limit reached, bad notebook JSON, no path, bad `--propose` part); `3` runtime (write failure). Every failure with `--json` prints one `{"ok": false, "error": ..., "exit_code": N}` object.
- Related skills: `yt-llm-director` (the director reads the notebook before every edit) and `yt-director` (the rule-based edit).
- The notebook is per instance and optional: with no path set the director reads nothing and the web app shows no section.
