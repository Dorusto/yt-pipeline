---
name: yt-recap
description: Turn a folder of raw clips and a brief in plain language into a recap MP4 with one command — doctor.py checks the machine, then recap.py chains proxy, ingest, motion, quality, describe, similarity, index, router, llm_director and render as subprocesses, skipping stations whose outputs are up to date. Add --final for the full-quality render from the originals and a Kdenlive project. Use when the user asks to build a recap from a folder, run the whole pipeline in one command, turn a folder plus a brief into an MP4, or make a reel from footage. Trigger phrases - "fă un recap din folderul ăsta", "reel din footage", "montaj automat din clipuri", "folder + brief într-un MP4", "build a recap from this folder", "one command recap", "rulează pipeline-ul complet", "serve the workspaces", "preview link", "open the recap on the phone". Not for running a single analysis station (`ingest`/`motion`/`quality`/`describe`/`similarity`/`index`), only building an edit list (`director`/`llm_director`), only rendering (`render`), Shorts (`shorts_generator.py`) or transcripts (`yt-transcript`).
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
- Stations run as subprocesses in order: proxy, ingest, motion, quality, describe, similarity, index, router, llm_director, render; with `--final` also render_final (from the originals) and export_kdenlive.
- One progress line per station, e.g. `[ 3/10] quality        skip      0.0s  up to date`. A station whose outputs are up to date is skipped.
- The recap lands at `~/media/trip/workspace/recap_720p.mp4`.

## Step 3: review

After a run the workspace holds `recap_720p.mp4` (the recap to watch), `edit.json` (the edit list), `shotlog.html` (contact sheet of the analysed shots), `shotlog.json`, `route.json`, `brief.txt` and `llm_spend.json`. When the cut is not what was wanted, look at `shotlog.html` and `edit.json` first.

## Phone flow: serve.py

Serve the finished workspaces over the tailnet so the recap opens on the phone:

```bash
.venv/bin/python scripts/serve.py --workspaces ~/media/projects
```

- `--workspaces DIR` is the folder whose direct subfolders are projects (a project holds at least one of `recap_720p.mp4`, `recap_final.mp4`, `shotlog.html`, `edit.json`; the list is newest folder name first).
- Binds the machine's tailnet IPv4 address by default. `--bind` refuses the wildcard (`0.0.0.0`, `::`, empty) so the server never leaves the tailnet; `127.0.0.1` is local only.
- `--port N` (default `8091`; `0` picks an ephemeral port). `--check` validates the root and the bind, prints the summary and exits without serving. `--json` prints one summary object at startup.
- URL layout: `<base>/` is the index, `<base>/<project>/` is the project page, and the three artifacts are `<base>/<project>/recap_720p.mp4`, `<base>/<project>/recap_final.mp4` and `<base>/<project>/shotlog.html`. Nothing else in a project is served.

## Phone flow: inbox.py

Turn an inbox folder filled by a phone sync tool into one recap per day with no
command per clip:

```bash
.venv/bin/python scripts/inbox.py --inbox ~/media/inbox --projects ~/media/projects \
    --preview-base http://10.10.1.61:8091 --notify
```

- `--inbox DIR` is the watched folder (required, read-only). `--projects DIR`
  (default `~/media/projects`) holds one workspace per day (`<projects>/YYYY-MM-DD/`)
  and must not be inside the inbox.
- Groups clips by day: a dated parent folder (`inbox/2026-10-14/`) wins, then a
  date in the filename (`YYYY-MM-DD`, `YYYYMMDD`, `YYYY_MM_DD`), then the file's
  mtime. A day whose clips share one folder is handed to `recap.py` directly;
  mixed folders get a symlink farm under `<workspace>/source/`.
- Waits until the inbox has been quiet for `--quiet-minutes N` (default 5; `0`
  disables) before running, so a sync in progress is not cut in half.
- Brief priority: `brief.txt` in the day folder (the clips' folder, or their
  `YYYY-MM-DD` parent when they are scattered), then `brief.txt` in the inbox,
  then `--default-brief` (built-in default: `about 90 seconds, chronological,
  calm start, more energy towards the end, ambient sound only`). The resolved
  brief is copied to `<workspace>/inbox_brief.txt`.
- `--once` scans and runs once then exits (for tests and cron); without it the
  tool polls every `--poll-seconds N` (default 60) until Ctrl-C. `--notify`,
  `--preview-base` and `--budget-usd` are forwarded to `recap.py`, and `--force`
  re-runs days with nothing new.
- Re-run safety lives in `<projects>/inbox_state.json`: a new clip, a changed
  brief or `--force` re-invokes `recap.py`, which then skips its own up-to-date
  stations. The inbox is never written to; everything goes under `--projects`.
- `--json` prints one summary object (`wall_time_s`, `llm_cost_usd`, one entry
  per day with `brief_source` and `status`); progress lines go to stderr.

## Go further

- `--final` also renders `recap_final.mp4` from the originals and writes `fragments.yaml` + `recap.kdenlive`.
- `--budget-usd X` caps the LLM spend for this run (forwarded to describe and llm_director).
- `--force` re-runs every station.
- `--notify` posts a short ntfy message with the preview link after a successful run (a fully skipped run says `Recap ready (cached)`); a failed run never notifies.
- `--preview-base URL` is the base of the `serve.py` server (e.g. `http://10.10.1.61:8091`); the link is `<base>/<workspace folder name>/recap_720p.mp4`. Without it the message carries the workspace path and the summary's `preview_url` is `null`.
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
- Proxies are 720p copies under `workspace/proxy/`; they can be deleted and rebuilt at any time.
