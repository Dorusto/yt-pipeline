---
name: yt-router
description: Read a brief in plain language and the workspace and write route.json — the detected content type (vacation, tutorial, presentation, talking-head) and every pipeline station in run order with enabled, settings and reason. Deterministic, no model. Use when the user asks which stations should run for a project, what kind of video a brief describes, how to route a brief, or wants a route.json / edit plan front door. Trigger phrases - "rutează brief-ul", "ce stații rulează", "fă route.json", "ce tip de video e", "route the brief", "which stations run", "detect the content type". Not for running any station, choosing the cut (that is `director`), analysing footage (`ingest`/`motion`/`quality`/`describe`) or rendering (that is `render`).
compatibility: Run from the repository root. Pure Python, no model and no GPU; needs only the repo .venv. Writes route.json to the workspace or the given --out path; source footage is never touched.
---

# yt-router

The front door of the AI Director pipeline. It reads a brief in plain language plus, optionally, the workspace, then writes `route.json` telling every later tool which stations run and with which settings. The same stations serve every kind of video; the router only switches them on or off. It calls no model and costs nothing.

## Step 1: route a brief

```bash
.venv/bin/python scripts/router.py --brief "about 90 seconds, chronological, calm start, more energy towards the end, ambient sound only"
.venv/bin/python scripts/router.py --brief-file brief.txt --workspace /path/to/workspace --json
.venv/bin/python scripts/router.py --workspace /path/to/workspace --content-type tutorial --out route.json
```

- Give exactly one of `--brief` (inline text) or `--brief-file` (path). Neither exits 2 with one `[error] ...` line on stderr.
- `--workspace DIR` is optional. When given it is used for content-type detection and the output defaults to `DIR/route.json`; without it the output is `./route.json`.
- `--content-type {vacation,tutorial,presentation,talking-head}` forces the type and reports `content_type_source: "flag"`.
- `--out PATH` overrides the output path. `--force` rewrites even when the output is up to date.
- `--json` prints one flat summary object. It never asks questions.

## Step 2: read route.json

`route.json` is validated against `schemas/route.schema.json` before it is written, so a run either leaves a complete, valid file or none.

- `brief` holds the brief verbatim plus the parsed hints: `target_duration_s`, `order` (`chronological`/`score`), `pacing` (`calm-to-energy`/`uniform`), `music_file`, `ambient_only` and the `signals` phrases that were matched.
- `content_type` and `content_type_source` (`flag` > `brief` keywords > `workspace` footage > `default` vacation).
- `stations` lists all fifteen stations in run order — `ingest, motion, quality, describe, similarity, audio_analysis, index, director, audio_clean, beats, render, export_kdenlive, graphics, chapters, shorts` — each with `enabled`, `settings`, `reason` and, for `beats`, an optional `required_when`.
- `meta.llm_cost_usd` is always `0.0`: the router calls no model.

The `--json` summary has `status` (`written`/`skipped`), `out`, `content_type`, `content_type_source`, `stations_enabled`, `target_duration_s`, `music_file`, `warnings`, `wall_time_s` and `llm_cost_usd`.

## Rules the router applies

- Duration: the first `number unit` where the unit is `s`/`sec`/`second(s)` or `min`/`minute(s)` (× 60). `null` when the brief names no length.
- Order and pacing come from fixed phrases; `signals` records every phrase that matched.
- Music: a path-like token ending in `.mp3`, `.wav`, `.m4a`, `.flac` or `.ogg`, or the text after `music:` / `song:` / `soundtrack:`.
- `beats` only runs for a vacation or talking-head brief that names a music file and does not ask for ambient sound only; a tutorial or presentation never uses it.
- `graphics`, `chapters` and `shorts` stay off for vacation and on for the other three types.
- `director` defaults to the LLM loop (`llm`/`rules`/3 rounds); "no llm", "rules only" or "without ai" in the brief switches it to `rules`/0.
- Re-running is safe: when the output exists, validates and is newer than the brief file (and the workspace `shotlog.json`), nothing is written and the summary reports `status: "skipped"`.

## Notes

- This tool only decides; it runs no station. The LLM director, audio clean-up, beats, the final render and the Kdenlive export read `route.json` later.
- Do not point it at source footage expecting it to write there — outputs go to the workspace, never into the footage folder.
- For later stages see `docs/VISION.md` and the `director`, `render` and `yt-rough-cut` skills.
