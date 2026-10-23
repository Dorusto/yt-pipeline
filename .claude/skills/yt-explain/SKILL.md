---
name: yt-explain
description: Write explain.html in a workspace — a short plain-language summary at the top and collapsed sections: the brief, the screenwriter plan in words (logline, structure beats, slot plan), the shots in the edit with a base64 thumbnail per slot showing the cut in/out seconds versus the candidate window, the slot score and the slot's reason, what was left out and why grouped by reason from edit.json decisions[], and the technical notes, with short ids like 0071/s2. Self-contained HTML (base64 thumbnails, no external assets), no model calls, re-run safe. Use when the user asks why the edit looks the way it does, wants the edit explained or audited, wants to see which candidates were rejected and why, or wants explain.html. Trigger phrases - "de ce arată așa", "explică montajul", "why does the edit look like this", "explain the edit", "unde e explicatia", "fă explain.html". Not for building the edit (that is `director`/`llm_director`), rendering the MP4 (that is `render`), indexing the shotlog (that is `index`) or the web app (a later task).
compatibility: Run from the repository root. Needs the repo .venv; CPU only, no network, no model call and no spend. Reads edit.json (required), shot_plan.json and shotlog.json (optional) and the source files in edit.json sources[] read-only, writes explain.html (or --out) atomically. Safe to re-run; an up-to-date output is skipped unless --force.
---

# yt-explain

Explain an edit: turn `WORKSPACE/edit.json` — the single source of truth — into one self-contained **`WORKSPACE/explain.html`** that answers *why does the edit look like this*: what was asked (the brief), what the screenwriter planned (when the LLM director wrote a plan), what ended up on the timeline (one thumbnail row per slot, the cut versus the candidate window it came from, the reason) and what was left out (every candidate used or rejected, with the reason the director recorded). No model is called. All commands run from the repository root.

## Step 1: write the explanation

```bash
.venv/bin/python scripts/explain.py --workspace /path/to/workspace
```

- `--workspace` is required and must hold `edit.json` (validated against `schemas/edit.schema.json`); `shot_plan.json` and `shotlog.json` are optional. `DIR/explain.html` is written there, created if missing. The sources in `edit.json` `sources[]` are never written to; `--out` that would overwrite a source file or land inside a sources folder is refused.
- `--out PATH` sets the output path (default `WORKSPACE/explain.html`).
- `--force` rewrites even when the output is up to date. Without it, an `explain.html` newer than `edit.json`, `shot_plan.json` and `shotlog.json` is skipped and counted as `skipped`.
- `--json` prints exactly one JSON object on stdout (human output goes to stderr in that mode).
- On any error it prints one `[error] ...` line to stderr: exit 2 for a missing workspace, a missing/unreadable or schema-invalid `edit.json`, an unreadable optional file or a refused output path; exit 3 when the HTML cannot be written. It never asks questions.

## Step 2: read the result

```bash
.venv/bin/python scripts/explain.py --workspace /path/to/workspace --json
```

The `--json` output is a single object, e.g. `{"status": "written", "out": "/abs/workspace/explain.html", "slots": 3, "candidates": 12, "used": 3, "rejected": 9, "wall_time_s": 0.4, "llm_cost_usd": 0.0}`. `status` is `"written"` or `"skipped"`; the counts come from the loaded `edit.json` either way; `llm_cost_usd` is always `0.0` — this tool calls no model service.

## What the page shows

The page opens with a short plain-language summary (3-4 sentences: the shot and candidate counts, the plan's beats in order, the reviewer verdict when `meta.director.review` is set, and who edited) and puts the detail behind collapsed `<details>` sections (no `open` attribute; the open state is remembered per section in `sessionStorage`):

1. **Brief** — `brief.text` verbatim, plus the target duration, `order`, `pacing`, and `audio.music` (path) / `audio.ambient_under_music_db` when set.
2. **The director's plan** — when `shot_plan.json` exists: the `logline`, then every `structure[]` beat (`name`, `intent`, `target_s`), then every `slot_plan[]` row (`id`, `shot_key`, `target_s`, `why`). When it does not exist, one line says so.
3. **Shots in the edit** — one row per `video[]` slot, in order: a base64 JPEG thumbnail (a frame at the middle of the cut for video slots, the photo itself for photo slots, ~320 px wide; a placeholder with the file name when no frame can be read), the slot id and short id (`v1 · 0071/s2`), the source file name and the slot `score`, the cut versus the candidate window (e.g. `cut 4.00–9.50 s of 0.00–14.20 s`; the window comes from the matching `decisions[]` row, else from `shotlog.json`, else it is omitted), the slot's `reason` verbatim — and for photo slots the `duration_s` and the `ken_burns.move`.
4. **Left out and why** — only the rejected `decisions[]` rows, grouped by reason category (`near duplicate`, `setup/teardown`, `accidental`, `shaky`, `blurry`, `defect`, `overlapping window`, `low score`, `did not fit the length`, `not eligible`, else the reason text), each group a nested collapsed `<details>` with its count; one line when nothing was left out.
5. **Technical notes** — who made the edit and the model loop's notes (repairs, fallback reason), or one line when there is none.

The page is a single self-contained file (no external CSS/JS/CDN; the only JavaScript is the inline `sessionStorage` open-state snippet), dark like `shotlog.html`, readable on a phone — the web app shows it in an iframe. Thumbnails exist only as base64 inside this one generated file; no frames are extracted to disk. The file is generated output in the workspace and is never committed.

## Notes

- This is the explanation adapter of D2: it reads what the directors wrote (`edit.json`, optionally `shot_plan.json` and `shotlog.json`) and renders it; it never changes the edit, the shotlog or the schemas.
- The short ids (`0071/s2`) are the same keys the shotlog, `decisions[]` and the id feedback use, so a rejected row can be traced back to its shot.
- A slot with `middle_speed` shows `middle at 1.20x` on its cut line; the cut times are source times, the on-screen length is `0.25L + 0.5L/s + 0.25L`.
- A slot cut around its best moment shows `centred on the best moment at 12.4 s (from vision|motion|middle)` under its cut line (only when the edit recorded `centred` and `peak_s`).
