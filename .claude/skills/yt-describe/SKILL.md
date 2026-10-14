---
name: yt-describe
description: Describe every kept quality segment of a clip with the vision model — three frames in time order with timestamps plus the motion label (one frame every ~5 s on a segment longer than 20 s), one structured answer per segment (description, summary of at most 12 words, tag list, subject, scenery, semantic motion, interest 0-100, hero flag), content beats that split a long segment at its scene changes (approach / inside / exit) and a verdict for every uncertain segment whose label wins in the shotlog (except a geometry `accidental`, whose role is final). Still photos listed in ingest.json are described with one frame as pseudo-segment p1 plus the main subject centre (`subject_position`) for the Ken Burns move. Use when the user asks what is actually on screen in a clip, wants descriptions or content scores for the kept segments, wants a long take split into its moments, wants the uncertain motion labels adjudicated, wants hero shots and semantic moves (orbit, tracking, reveal, travel) marked, or wants the still photos described. Trigger phrases - "descrie clipurile", "ce se vede în cadre", "describe the segments", "etichetează segmentele cu viziune", "care e mișcarea semantică", "care sunt cadrele hero", "rezolvă etichetele incerte", "împarte clipul lung în momente", "descrie pozele", "describe the photos", "unde e subiectul în poză". Not for camera-motion geometry (that is `motion`), quality defects and roles (that is `quality`) or generating clips.
compatibility: Run from the repository root. Needs the repo .venv with opencv-python-headless and Pillow; CPU only. Reads quality/<stem>.json and the clip read-only, writes describe/<stem>.json into the workspace; still photos come from ingest.json and are read-only too. Frames are held in memory only and never written to disk. The default backend is llm_backend from config.yaml; use --backend mock for an offline run with no network and no spend.
---

# yt-describe

Describe every kept quality segment with the vision model before any editing decision. For each segment the tool samples frames in time order — three frames, or on a segment longer than 20 s one frame every ~5 s (capped at 24) — sends them with the segment facts (timestamps, geometric role, motion label, uncertainty) and stores one structured answer: description, a one-line `summary` of at most 12 words, a short `tags` list (at most six lowercase tags), subject, scenery, semantic motion, interest 0–100 and a hero flag. A long segment also gets **content beats**: a contiguous tiling of the segment at the scene changes (approach / inside / exit, enter / leave), each beat with its own label, summary, interest and hero flag — every beat becomes its own shotlog candidate. Beats are kept only when the model returns at least two and the segment is longer than 20 s. Every **uncertain** segment also gets a `verdict` whose role and motion label win in the shotlog — except a geometry `accidental`, whose role stays final (only its motion label can be corrected). Confident `accidental` / `setup` / `teardown` segments are skipped without a model call. When `ingest.json` lists still photos, each photo is described with **one frame** as pseudo-segment `p1` and carries `subject_position` — the main subject centre in normalised coordinates — for the Ken Burns move. **Frames are never written to disk** — the repo is public. All commands run from the repository root.

## Step 1: describe a workspace

```bash
.venv/bin/python scripts/describe.py --workspace /path/to/workspace
```

- `--workspace` is required and must hold `quality/`. `DIR/describe/<stem>.json` is written per clip, created if missing. The source clips are never written to.
- `--clips` limits the run to comma-separated 4-digit counters or stem substrings (e.g. `--clips 0067,0081`). Without it every `quality/*.json` is processed. It does not apply to photos.
- `--backend` (`mock` or `openrouter`) overrides `llm_backend` from the config; `--model` overrides `models.vision`; `--config` points at another config file. The default model id is `models.vision`.
- `--frame-width` (default 640) is the width frames are resized to before sending; JPEG quality is 85.
- `--force` re-describes a clip or photo whose output already exists. Without it, a clip or photo whose JSON exists and validates against the `describe` schema is skipped.
- On any error (missing workspace, unreadable quality file, missing clip, write failure) it prints one `[error] ...` line to stderr and exits 1. It never asks questions.

## Step 2: describe the still photos

```bash
.venv/bin/python scripts/describe.py --workspace /path/to/workspace --backend mock --photos-only
```

- Photo coverage is automatic: when `ingest.json` in the workspace has a non-empty `photos[]`, every photo is described in the same run as the clips. `--photos-only` skips the video segments and describes only the photos (useful when there is no `quality/` yet).
- Each photo is one record `DIR/describe/<stem>.json` (`stem` = photo file name without extension), validated against `schemas/describe.schema.json` with `media_type: "photo"`.
- The photo prompt is `prompts/describe_photo.md`; `--prompt-file` overrides it (and the video prompt) with one file.
- Re-run safety: an existing valid photo record is skipped unless `--force`.

## Step 3: read the result

```bash
.venv/bin/python scripts/describe.py --workspace /path/to/workspace --json
```

The `--json` output is a single object, e.g. `{"clips": 4, "described": 4, "skipped": 0, "segments_described": 23, "segments_skipped": 0, "beats": 5, "photos": 3, "model": "deepseek/deepseek-v4.1-flash", "wall_time_s": 13.49, "llm_cost_usd": 0.0}`. `photos` is the number of photo records written this run. Read `DIR/describe/<stem>.json` for the per-clip or per-photo detail; it is validated against `schemas/describe.schema.json` and written atomically, so a run leaves a complete valid file or none.

## What the fields mean

- `media_type` is `"video"` (default) or `"photo"`. A photo record has one pseudo-segment `p1` (`start_s: 0.0`, `end_s: 0.04`, `role: "hold"`, `motion_label: "static"`, `uncertain: false`, one frame at `t_s: 0.0`) and `duration_s: 0.0`.
- `segments` has one entry per quality segment, in time order. `status` is `described` (the model saw the frames) or `skipped` (no call was made).
- A segment is **skipped** with `skip_reason: "confident <role>"` when its `role` is `accidental`, `setup` or `teardown` **and** `uncertain` is false. Everything else is described — including confident holds and moves, and including uncertain accidental/setup/teardown, whose label may be wrong.
- `frames` lists the sampled timestamps in time order. A short segment gets three: `t_i = start_s + (i + 0.5) / 3 * (end_s - start_s)`; a segment longer than 20 s gets one frame every ~5 s (capped at 24); a photo gets exactly one at `t_s: 0.0`. The images themselves are never stored.
- `beats` splits a long segment (> 20 s) into its content moments at the scene changes: a contiguous tiling of the segment (first `start_s` is the segment's start, last `end_s` is the segment's end, each beat's start is the previous beat's end) with `id` `b1`..`bN`, a `label` (`approach`, `inside`, `exit`, `enter`, `leave`, ...), a per-beat `summary`, `interest` (0–100 or null) and `hero` (boolean or null — null means "inherit the segment's"). Empty when the segment is short or the model marked no scene change. **Each beat becomes its own shotlog candidate** in `index`, so a 134 s tunnel transit can offer its approach and its exit separately.
- `description`, `subject`, `scenery`, `semantic_motion` (`orbit`, `tracking`, `reveal`, `travel`, `none`), `interest` (0–100) and `hero` are the model's answer for a described segment. A photo's `semantic_motion` is always `"none"`.
- `subject_position` is the main subject centre of a photo in normalised image coordinates (`x` and `y` in 0..1, `{"x": 0.5, "y": 0.5}` when the model does not say). The director turns it into a Ken Burns move; it is absent on video segments.
- `summary` is the one-line summary in at most 12 words (the tool trims longer answers) and `tags` is the short lowercase tag list (at most six entries, each at most 32 characters, de-duplicated). Both are for the contact sheet and the phone review; the full `description` stays for the LLM director.
- `verdict` is the model's adjudication of an **uncertain** segment: `role`, `motion_label`, `confidence` (0–1) and a short `note`. Its label wins in the shotlog, except on a geometry `accidental` where only the motion label can be corrected — the role stays `accidental`. On a confident segment `verdict` is always `null`. When the model returns no verdict for an uncertain segment the tool retries once; if it still fails it keeps the description and falls back to the geometric role and label with `confidence: 0.0` and a warning.
- `cost_usd` is the cost of that segment's call (the sum of both calls when a verdict retry happened); the file's `llm_cost_usd` is the sum and `model` is the id actually used.
- `warnings` collects non-fatal problems: unparseable model output, retried once with the same request (the segment becomes `skipped` with `skip_reason: "unparseable model output"` only when the retry also fails, and both calls are charged to it), and a missing verdict after the retry.

## Budget and caps

- Every call is charged to the spend ledger (`WORKSPACE/llm_spend.json` by default, `--spend-ledger` overrides) and `llm_budget_usd` (or `--budget-usd`) caps the job.
- `--max-calls` stops after that many model calls. When the budget is reached or `--max-calls` is hit, the remaining segments are marked `status: "skipped"` with `skip_reason: "budget_exceeded"` / `"max_calls"`, the outputs written so far stay on disk, the summary is printed and the tool exits **2**.
- Exit codes: `0` success, `1` bad input or I/O error, `2` stopped because the budget or `--max-calls` was reached.

## Notes

- Label, never delete: the tool only describes and scores. It never cuts, trims or ranks.
- This is the vision stage of D1b, after the CPU-only `ingest`, `motion` and `quality`; its output feeds `index` / `shotlog.json` and the director. Still photos come from `ingest.json` and are described in the same pass.
- For an offline run with no network and no spend use `--backend mock`; the mock answers deterministically from the segment facts.
