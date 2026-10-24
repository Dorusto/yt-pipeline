---
name: yt-recap-meta
description: Write the YouTube title, a 2-3 sentence description and 5-10 hashtags for a recap — reads a workspace edit.json, builds the deterministic facts (the brief text, the four facts place/when/who/occasion from brief_facts.json, with the ingest date for when, and the deduplicated shot descriptions), asks the metadata text model role and writes recap_meta.json validated against the schema. Use when the user asks for the title, description or hashtags of a recap, wants the upload text for a finished edit, or wants recap_meta.json. Trigger phrases - "fă titlul și descrierea recapului", "scrie descrierea pentru YouTube", "ce hashtag-uri pun", "titlu pentru montaj", "write the recap title", "description and hashtags for the recap", "recap_meta.json", "upload text for the edit". Not for building the edit (that is `director`/`llm_director`), rendering the MP4 (that is `render`), the publish kit (`publish`) or the web app (that is `serve.py`).
compatibility: Run with the `yt` entry point on PATH. Needs the repo .venv and the model config (config.yaml, llm_backend mock for offline runs). Reads edit.json (required) and ingest.json (optional) read-only, writes recap_meta.json (or --out) atomically. Safe to re-run; an up-to-date output is skipped unless --force.
---

# yt-recap-meta

Write the upload text for a recap: turn `WORKSPACE/edit.json` into one **`WORKSPACE/recap_meta.json`** holding the YouTube **title**, a 2-3 sentence **description** and 5-10 **hashtags**. The tool builds the deterministic facts itself (the brief text, the four facts place/when/who/occasion from `brief_facts.json`, with the ingest date for `when`, the deduplicated shot descriptions), asks the `metadata` text model role and lists the fact fields the brief and `brief_facts.json` did not carry by field name in `missing`. All commands run from the repository root.

## Step 1: write the metadata

```bash
yt recap_meta --workspace <workspace>
```

- `--workspace` is required and must hold `edit.json` (validated against `schemas/edit.schema.json`); `ingest.json` is optional and only supplies the date, and `brief_facts.json` is optional and supplies the four facts. `WORKSPACE/recap_meta.json` is written there, created if missing.
- `--out PATH` sets the output path (default `WORKSPACE/recap_meta.json`).
- `--prompt FILE` uses an alternative prompt (default `prompts/recap_meta.md`).
- `--dry-run` builds the message, prints it and writes nothing; no model is called.
- `--force` rewrites even when the output is up to date. Without it, a `recap_meta.json` newer than `edit.json` is skipped and counted as `skipped`.
- `--budget-usd USD` overrides `llm_budget_usd` for this run.
- `--json` prints exactly one JSON object on stdout (human output goes to stderr in that mode).
- On any error it prints one `[error] ...` line to stderr: exit 2 for a missing workspace, a missing/unreadable or schema-invalid `edit.json`, a missing prompt file or a config error (a missing `OPENROUTER_API_KEY`); exit 3 for a model call failure, an invalid answer after the retry or a write failure. It never asks questions.

## Step 2: read the result

```bash
yt recap_meta --workspace <workspace> --json
```

The `--json` output is a single object, e.g. `{"ok": true, "status": "written", "out": "/abs/workspace/recap_meta.json", "title": "...", "hashtags": 7, "missing": ["place"], "model": "...", "wall_time_s": 0.1, "llm_cost_usd": 0.0012}`. `status` is `"written"`, `"skipped"` or `"dry_run"`; `hashtags` is the count; `missing` lists the fact fields the brief and `brief_facts.json` did not carry (`place`, `when`, `who`, `occasion`). A `--dry-run` summary carries `message` instead of `title`/`hashtags`/`model` and `llm_cost_usd` is `0.0`.

## What the tool builds

- `brief_text` — `edit.json` `brief.text`.
- `place` — `brief_facts.json` `place` only (the brief syntax no longer sets it).
- `when` — `brief_facts.json` `when`, else the date part (`YYYY-MM-DD`) of the earliest clip `creation_time` in `ingest.json`.
- `who` — `brief_facts.json` `who`.
- `occasion` — `brief_facts.json` `occasion`.
- `descriptions` — the non-empty `description` of every slot in `video[]`, in timeline order, deduplicated.
- `missing` — the field names (`place`, `when`, `who`, `occasion`) whose value is empty.

The message is the prompt, then `## Brief`, `## Facts` (`Place:`/`When:`/`Who is in it:`/`Occasion:`) and `## Shots` (one `- <description>` line per shot). The answer must be one JSON object with `title` (at most 100 characters), `description` (2-3 sentences, at most 1000 characters) and `hashtags` (5-10, each starting with `#`); an invalid answer is retried once, then the run exits 3.

## Notes

- The web app's Recap tab shows the shown version's `recap_meta.json` as a box with a Copy button per field and a Rewrite form that marks each missing fact field; `recap.py` runs this tool as a station after `explain`, and each version keeps its own text.
- The model role is `metadata` (the same text role `metadata.py` and `chapters.py` use); the model id and the budget come from `config.yaml`.
- The output is validated against `schemas/recap_meta.schema.json` before it is written; the write is atomic.
