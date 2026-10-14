---
name: yt-recap
description: Turn a folder of raw clips and a brief in plain language into a recap MP4 with one command — doctor.py checks the machine, then recap.py chains proxy, ingest, motion, quality, describe, similarity, index, router, llm_director and render as subprocesses, skipping stations whose outputs are up to date. Add --final for the full-quality render from the originals and a Kdenlive project. Add --music FILE to detect the track's beats and mix it under the recap. Use when the user asks to build a recap from a folder, run the whole pipeline in one command, turn a folder plus a brief into an MP4, or make a reel from footage. Trigger phrases - "fă un recap din folderul ăsta", "reel din footage", "montaj automat din clipuri", "folder + brief într-un MP4", "build a recap from this folder", "one command recap", "rulează pipeline-ul complet". Not for running a single analysis station (`ingest`/`motion`/`quality`/`describe`/`similarity`/`index`), only building an edit list (`director`/`llm_director`), only rendering (`render`), Shorts (`shorts_generator.py`) or transcripts (`yt-transcript`).
compatibility: Run from the repository root with the repo .venv. Needs ffmpeg and ffprobe on the PATH and OPENROUTER_API_KEY in the environment (unless llm_backend is mock). The originals folder is never written to; proxies and every output go under the workspace.
---

# yt-recap

The front door of the AI Director pipeline: one command turns a folder of raw clips and a brief in plain language into a recap MP4. A run costs a few cents of vision and director calls, all tracked in the workspace `llm_spend.json`.

## Step 1: check the machine

```bash
.venv/bin/python scripts/doctor.py --source ~/media/trip/raw
```

Ten checks in a fixed order: python deps, ffmpeg, ffprobe, config, models, API key, disk space, workspace writable, workspace outside the originals, originals read-only. Exit 0 means nothing failed (warnings are allowed); exit 1 means at least one check failed. Fix what it lists, then continue. Add `--workspace DIR` to check a specific workspace and `--json` for a machine-readable summary.

## Step 2: run the recap

```bash
.venv/bin/python scripts/recap.py --source ~/media/trip/raw \
    --workspace ~/media/trip/workspace \
    --brief "about 90 seconds, chronological, calm start, more energy towards the end, ambient sound only"
```

- `--source` is the folder of original clips (non-recursive, read-only). `--workspace` defaults to `<source parent>/workspace` and must not be inside the source.
- Give exactly one of `--brief` (inline text) or `--brief-file` (path).
- Stations run as subprocesses in order: proxy, ingest, motion, quality, describe, similarity, index, router, llm_director, render; with `--final` also render_final (from the originals) and export_kdenlive. With `--music FILE` a `beats` station runs right after ingest.
- One progress line per station, e.g. `[ 3/10] quality        skip      0.0s  up to date`. A station whose outputs are up to date is skipped.
- The recap lands at `~/media/trip/workspace/recap_720p.mp4`.

## Step 3: review

After a run the workspace holds `recap_720p.mp4` (the recap to watch), `edit.json` (the edit list), `shotlog.html` (contact sheet of the analysed shots), `shotlog.json`, `route.json`, `brief.txt` and `llm_spend.json`. When the cut is not what was wanted, look at `shotlog.html` and `edit.json` first.

## Go further

- `--final` also renders `recap_final.mp4` from the originals and writes `fragments.yaml` + `recap.kdenlive`.
- `--music FILE` detects the track's beats (`beats.json`), passes `--beats` to the director so cuts snap to the music, and mixes the track under the cleaned ambient bus in both renders. The beats station is skipped when `beats.json` is newer than the music file.
- `--budget-usd X` caps the LLM spend for this run (forwarded to describe and llm_director).
- `--force` re-runs every station.
- `--json` prints one flat summary with `wall_time_s`, `llm_cost_usd` (this run) and `llm_spend_usd` (the workspace ledger).
- `--brief-file PATH` reads the brief from a file instead of `--brief`.

## Re-run rules

- Same brief: every station is skipped and the run costs nothing.
- New brief: only router → llm_director → render re-run; the analysis is cached.
- New clips in the source folder: proxies and analysis run for the new clips; add `--force` if you want the edit redone, or re-run just the stations you need.

## Gotchas

- The originals folder must not be the workspace; `recap.py` refuses a workspace inside the source.
- The budget stops the chain when it is reached. Raise `--budget-usd` or edit `config.yaml: llm_budget_usd`.
- `doctor.py` warns when the originals folder is writable; make it read-only with `chmod -R a-w ~/media/trip/raw`.
- Proxies are 720p copies under `workspace/proxy/`; still photos (JPEG/HEIC) are copied through next to them so ingest sees the whole day. Proxies can be deleted and rebuilt at any time.
