---
name: yt-recap
description: Turn a folder of raw clips and a brief into a recap MP4 with one command — chains the whole pipeline. Add --final for the full-quality render and --music FILE or auto. Use when the user asks to build a recap from a folder, run the whole pipeline in one command, turn a folder plus a brief into an MP4, or make a reel from footage. Trigger phrases - "fă un recap din folderul ăsta", "reel din footage", "montaj automat din clipuri", "folder + brief într-un MP4", "build a recap from this folder", "one command recap", "rulează pipeline-ul complet", "serve the workspaces", "preview link", "open the recap on the phone", "fă-mi un recap din clipurile de azi", "recap din clipurile noi", "trimite-mi linkul la recap", "pune muzică pe recap", "recap cu muzică". Not for running a single analysis station (`ingest`/`motion`/`quality`/`describe`/`similarity`/`index`), only building an edit list (`director`/`llm_director`), only rendering (`render`), Shorts (`shorts_generator.py`) or transcripts (`yt-transcript`).
compatibility: Run with the `yt` entry point on PATH. Needs ffmpeg and ffprobe on the PATH and OPENROUTER_API_KEY in the environment (unless llm_backend is mock). The originals folder is never written to; proxies and every output go under the workspace. Works from Telegram via an assistant (Hermes): the assistant runs the same commands and replies with the preview link built from `--preview-base`.
---

# yt-recap

The front door of the AI Director pipeline: one command turns a folder of raw clips and a brief in plain language into a recap MP4. A run costs a few cents of vision and director calls, all tracked in the workspace `llm_spend.json`.

## Step 1: check the machine

```bash
yt doctor --source <trip-folder>/raw
```

Ten checks in a fixed order: python deps, ffmpeg, ffprobe, config, models, API key, disk space, workspace writable, workspace outside the originals, originals read-only. Exit 0 means nothing failed (warnings are allowed); exit 1 means at least one check failed. Fix what it lists, then continue. Add `--workspace DIR` to check a specific workspace and `--json` for a machine-readable summary.

## Step 2: run the recap

```bash
yt recap --source <trip-folder>/raw \
    --workspace <trip-folder>/workspace \
    --brief "about 90 seconds, chronological, calm start, more energy towards the end, ambient sound only"
```

- `--source` is the folder of original clips (non-recursive, read-only). `--workspace` defaults to `<source parent>/workspace` and must not be inside the source.
- Give exactly one of `--brief` (inline text) or `--brief-file` (path).
- Stations run as subprocesses in order: proxy, ingest, motion, quality, describe, similarity, index, router, llm_director, render, thumbs; with `--final` also render_final (from the originals) and export_kdenlive. With `--music FILE` a `beats` station runs right after ingest; with `--music auto` a `music_pick` station runs right after index (the shotlog tags exist by then) and writes `beats.json` itself.
- One progress line per station, e.g. `[ 3/10] quality        skip      0.0s  up to date`. A station whose outputs are up to date is skipped. A station that is about to run prints a start line first, e.g. `[ 3/10] quality         start`, then its result line; a skipped station prints only its `skip` line.
- The recap lands at `<trip-folder>/workspace/recap_720p.mp4`; the `thumbs` station writes `thumbs/poster.jpg` and `thumbs/shot-N.jpg` next to it.

## Step 3: review

After a run the workspace holds `recap_720p.mp4` (the recap to watch), `edit.json` (the edit list), `shotlog.html` (contact sheet of the analysed shots), `shotlog.json`, `route.json`, `brief.txt`, `thumbs/` (the poster and the per-shot thumbnails) and `llm_spend.json`. When the cut is not what was wanted, look at `shotlog.html` and `edit.json` first.

## Phone flow: serve.py

Serve the finished workspaces over the tailnet as a small web app (the Shots-tab review taps and the Mode B Cuts-tab review taps):

```bash
yt serve --workspaces <workspaces> --inbox <inbox>
```

- `--workspaces DIR` is the folder whose direct subfolders are projects (a project holds at least one of `.run.json`, `recap_720p.mp4`, `recap_final.mp4`, `shotlog.html`, `explain.html`, `edit.json`; the list is newest folder name first).
- The Projects page (`<base>/`) is a grid of poster cards, one per project, each with its `thumbs/poster.jpg` (a striped placeholder when there is none), a status badge — `running` (a live `.run.json` pid), `failed` (dead pid, no recap yet) or `done` — and small links to the artifacts that exist (recap / final / why / shots). The top menu links Projects and Upload.
- Each project page (`<base>/<project>/`) has the tabs Recap / **Director** / Files / Cuts / Shorts (`?tab=recap|director|files|cuts|shorts`, default recap, works without JS): the Recap tab is the `recap_720p.mp4` player plus a link to `recap_final.mp4` when it exists. The `explain.html` and `shotlog.html` pages stay on disk and are still reachable by URL (`?tab=why`, `?tab=shots`) but are no longer linked in the tab bar. The `shotlog.html` page (reachable by URL only) marks each card "in the edit as vN" or "left out: reason" from `edit.json` decisions, with the filters All / In the edit / Good but left out. Every shot card also has two review buttons — **must be in** and **wrong label** — that `POST /<project>/expectations` (`shot`, `kind`, optional `note`) and append to `<workspace>/expectations.yaml`, the file `scripts/eval_edit.py --workspace WS --expect WS/expectations.yaml` checks. A `must_include` tap writes `{any_of: [shot], note}` (checked by `eval_edit.py`); a `wrong_label` tap writes `{shot, note}` under the top-level `wrong_label` key, which `eval_edit.py` ignores — it is the record Doru and the agents read before the next re-edit/re-analysis. Taps are idempotent (a second tap of the same shot and kind adds nothing) and the file is machine-owned: it is rewritten with a one-line header comment, so hand-written comments are lost. The buttons appear even when the workspace has no `edit.json`.
- A **Mode B project** (a workspace with `sessions.yaml`, or an `edit.json` whose `meta.tool` is `scripts/rough_cut.py`) also gets a **Cuts** tab (`?tab=cuts`): one row per automatic cut run from `edit.json` decisions (the rejected runs plus the runs kept by review), each with its id, reason, clip, window, the words of the span and a keep/cut toggle. A tap `POST /<project>/cut-review` (urlencoded `id`, `action` = `keep`/`cut`, optional `note`; 200 JSON, 400 one-line text, 404 unknown project, 405 otherwise) appends idempotently per cut id to `<workspace>/cut_review.yaml` (atomic, machine-owned, one-line header comment) — the file `rough_cut.py --review` and `eval_rough_cut.py --review` read. Each row also has an audio player `GET /<project>/cut-audio?id=<cut id>` that serves an `audio/wav` snippet of the cut span plus one second each side from the `sources[]` media, cached under `<workspace>/cut_previews/<id>.wav`. Non-Mode-B projects have no Cuts tab and `?tab=cuts` falls back to Recap.
- A project with a `shorts_candidates.json` also gets a **Shorts** tab (`?tab=shorts`): one row per ranked candidate with its rank, span, duration, score, hook/payoff, title, description, reason and chapter, and a **layout** select (`face`/`split`, the candidate's current value selected, `face` when it has none). A change posts `POST /<project>/shorts-candidates` (urlencoded `rank`, `layout`; 200 JSON `{ok, rank, layout}`, 400 one-line text for a missing/unknown rank, a bad layout, a missing file or `split` on a camera-only project, 404 unknown project, 405 otherwise) and rewrites only that candidate's `layout` key in `<workspace>/shorts_candidates.json` (atomic, `indent=2`, trailing newline, every other key untouched; re-posting is safe). A camera-only session (video `sources[]` with no `screen` angle by `common.media.clip_kind`) offers `face` only. Projects without the file have no Shorts tab and `?tab=shorts` falls back to Recap.
- The Upload page (`<base>/upload`) takes clips and photos from the phone browser: files, folder, brief, length, order, audio clean, music, ambient-under-music dB, shorts layout. The Brief field has hint chips (The story or the best moment, Mood, What to leave out) and a multi-line example placeholder that is never sent; the four optional fact fields (place, when, who, occasion) sit above it and are stored in `brief_facts.json`. When the instance sets a consent text (`--consent-text` / `YT_PIPELINE_CONSENT_TEXT` / `web.consent_text`), the page shows it with a required "I agree" checkbox and refuses an upload without it. `POST /upload` streams the files into `--inbox/<folder>/` (allowlisted extensions, `--max-upload-mb`/`--max-total-mb` caps, never overwrites, never outside the inbox), writes `brief.txt` there and starts `recap.py` in the background; the Projects page shows the run status (`running`/`failed`/`done` from `<workspace>/.run.json`). The project page has a **Re-edit** form (the same options plus a `feedback` box; Length has an "Exact seconds…" option that reveals a seconds box `length_s` under it) that re-runs only `llm_director` → `render` with `--feedback`, and a bin icon next to the title that deletes the workspace and its inbox source (`POST /<project>/delete`, refused while a run is going). The **Director** tab has a **chat with the director**: the reviewer's objections from `edit.json` `meta.director.review` appear as the reviewer's own plain sentences, each naming the shot by its timeline number; the user writes a message and `POST /<project>/feedback-reply` (multipart `feedback`, optional `version`) runs `llm_director.py --interpret-feedback`, appends the reply to `feedback.jsonl` and starts no run, then redirects back to the chat; the reply is structured (`reply`, `can_do`, `missing_tool`) with `can_do` `all`/`part`/`none`, and when it is not `all` the director says in plain words what it can do instead and the request is noted — the web app appends one `missing_tool` line to the requests file, an owner signal written whether or not the requests page is switched on (it needs a configured requests file, not the `requests` feature); the reply shows with a cost/time estimate and, when `can_do` is `all`, a **Make the new version** button that posts the last message plus the director's reading to `POST /<project>/re-edit` with `approved=1` and `submit=reedit`, while a reply that is not `all` shows a disabled **Answer the director first** button instead until the owner sends another message. The chat shows the last 3 exchanges; older ones sit in a closed **Earlier messages (N)** section above them, and the message box and the approve/disabled button sit directly under the newest exchange. The Recap tab's **Delete this version** action (`POST /<project>/delete-version`, urlencoded `version`, e.g. `v002`) removes one version folder, refused for the latest, the first and while a run is going. The Recap tab shows version chips (`?v=N`) and a second player with `&with=M`; while running it shows the last finished version plus `Now: <plain name> (step i of N, <seconds>s)` and a progress bar of the stations (from `run.log`, after the `=== recap run started` marker serve writes) and refreshes every 20 s; a failed run shows the station and the error line. A **Stop** button on the progress card posts `POST /<project>/stop-run` (part of `recap`), which stops the run and its child processes, leaves the previous version and is refused with 409 when nothing runs. The run-progress `<progress>` bar is themed from the theme accent token (`--accent`, `::-webkit-progress-value` / `::-moz-progress-bar`). A finished child is reaped, so a dead pid never stays `running`.
- A project page also has a **Files** tab (`?tab=files`, part of the `recap` feature): every clip and photo of the project from its proxies and its recorded inbox source, with its length or "photo", its size and its state (`in the edit`, `new`, `left out`). **Add files** posts `POST /<project>/add-files` (multipart, the same consent and storage checks as the Upload page) and streams the files into the project's recorded inbox source folder; the tab then says "N new files. They join the next edit." with a **Make a new edit** link (`?tab=files&reedit=1` opens the Re-edit form). A bin icon posts `POST /<project>/exclude-file` (`name`, `action` = `exclude`/`include`) and writes `<workspace>/excluded.json` (`{"excluded": [...]}`, atomic, reversible); a left-out file shows **Bring back**. Nothing is deleted from disk. Every station that lists clips or photos skips the excluded names (`ingest.py`, `proxy.py --exclude-file`, `motion.py`, `quality.py`, `describe.py`, `similarity.py`, `index.py`), and `recap.py` proxies and analyses only the new files, reusing the cached proxies when the originals are gone.
- Binds the machine's tailnet IPv4 address by default. `--bind` refuses the wildcard (`0.0.0.0`, `::`, empty) so the server never leaves the tailnet; `127.0.0.1` is local only.
- `--port N` (default `8091`; `0` picks an ephemeral port). `--music-dir DIR` is the music library folder whose audio files (`.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg`) are offered on the Upload page — from `--music-dir`, else `YT_PIPELINE_MUSIC_LIBRARY`, else `music.library` in the config (no personal default; with none set the page offers no tracks and `--music auto` stops with a usage error naming the three ways). `--inbox DIR` is where uploads land — from `--inbox`, else `YT_PIPELINE_INBOX`, else `inbox.path` in the config (no personal default; with none set the server stops with a usage error naming the three ways); `--max-upload-mb` (4096) and `--max-total-mb` (20480) cap the upload size. `--features` (comma-separated) / `YT_PIPELINE_FEATURES` / `web.features` lists the parts this instance shows (`upload`, `recap`, `why`, `shots`, `shorts`, `costs`, `requests`; default all) and a part that is off is refused with a 404, not just hidden; `--consent-text` / `YT_PIPELINE_CONSENT_TEXT` / `web.consent_text` is the optional upload consent text. `--brand-logo` / `YT_PIPELINE_BRAND_LOGO` / `brand.logo` picks the logo mark this instance shows (default `option-1`). `--check` validates the root and the bind, prints the summary and exits without serving; without a configured inbox it warns with the shared three-ways message and still validates the root and the bind. `--json` prints one summary object at startup (including `music_tracks`, `music_library`, `inbox`, `features`, `consent` and `brand_logo`).
- URL layout: `<base>/` is the Projects page, `<base>/upload` the upload form, `<base>/<project>/` the project page, and the artifacts are `<base>/<project>/recap_720p.mp4`, `<base>/<project>/recap_720p_9x16.mp4`, `<base>/<project>/recap_final.mp4`, `<base>/<project>/recap_final_9x16.mp4`, `<base>/<project>/shotlog.html` and `<base>/<project>/explain.html` (videos support HTTP Range). The thumbnails are `<base>/<project>/thumbs/<poster|shot-N>.jpg` (and the same under `versions/vNNN/`). `GET /brand` shows the three logo options, `GET /brand/<name>.svg` serves one option and `GET /favicon.svg` the chosen mark. `POST /<project>/delete` deletes the project and its inbox source. Nothing else in a project is served — proxies, `edit.json`, `.run.json` and every analysis folder stay private.

## Phone flow: inbox.py

Turn an inbox folder filled by a phone sync tool into one recap per day with no
command per clip:

```bash
yt inbox --inbox <inbox> --projects <workspaces> \
    --preview-base http://<agent-host>:8091 --notify
```

- `--inbox DIR` is the watched folder (required, read-only). `--projects DIR`
  holds one workspace per day (`<projects>/YYYY-MM-DD/`) and must not be inside
  the inbox. It is resolved from `--projects`, else the `YT_PIPELINE_PROJECTS`
  environment variable, else `projects.path` in the config, else a usage error
  naming those three ways.
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
- `--music FILE` detects the track's beats (`beats.json`), passes `--beats` to the director so cuts snap to the music, and mixes the track under the cleaned ambient bus in both renders. The beats station is skipped when `beats.json` is newer than the music file. `--music auto` picks the track from the music library (`--music-dir` / `YT_PIPELINE_MUSIC_LIBRARY` / `music.library`) with `scripts/music_pick.py` (brief pace + shot tags + `beats.py` BPM/energy, see `yt-music-pick`) and writes `WORKSPACE/music_choice.json` + `WORKSPACE/beats.json`; the chosen path is what ends up in `edit.json` `audio.music`.
- `--format 16x9|9x16|both` (default `16x9`): `9x16` renders `recap_720p_9x16.mp4` (720x1280, a moving crop that follows the subject, reframe paths cached in `WORKSPACE/reframe/`) in a `render_vertical` station instead of `render`; `both` runs `render` then `render_vertical`. With `--final` the vertical final is `recap_final_9x16.mp4` (`render_final_vertical`, 1080x1920). The JSON `outputs` has `preview_vertical` and `final_vertical`. The web app's Upload and Re-edit forms have a Format select (Auto = 16:9) and the Recap tab shows the vertical player when `recap_720p_9x16.mp4` exists (also served: `recap_final_9x16.mp4`).
- `--speed-ramp` (default off, normal speed) lets the middle half of long camera moves play up to 1.2x; it is passed to `director.py` / `llm_director.py`, and changing it re-runs the edit and the render. The web Upload and Re-edit forms have a Speed ramp select (Auto = off / off / on). Trimmed cuts are centred on the best moment (vision `peak_s`, else the motion signal, else the middle).
- `--target-duration-s N` and `--order chronological|score` override what the brief parses; `--music-dir DIR` is the library for `--music auto`.
- `--budget-usd X` caps the LLM spend for this run (forwarded to describe and llm_director).
- `--feedback TEXT` (repeatable) sends plain-word feedback against the current edit's short ids (e.g. `--feedback "drop 0071/s2, tunnel longer"`) to the `llm_director` station and re-runs **only** llm_director → explain → render even when they are up to date — the analysis stations keep their normal skip rule. The feedback lines land in `WORKSPACE/feedback.jsonl` (written by `llm_director`, not by recap).
- The director reads the instance **notebook** before every edit (the file at `--notebook`, else `YT_PIPELINE_NOTEBOOK`, else `notebook.path` in the config) and hands its lessons to the screenwriter, the editor and the reviewer; with no notebook set it reads nothing. The web app learns after each new version it makes: it runs `notebook.py --learn` in the background, one cheap model call that writes at most 3 short lessons from the feedback since the last pass. The notebook is edited with `yt-notebook`.
- Every run that makes a new edit keeps it in `WORKSPACE/versions/vNNN/` (`edit.json`, the renders made from it, `version.json`); the summary's `version` names it (null when nothing new was made). A failed render station does not stop the other render stations (status is still `failed`).
- `--new-edit` makes a new edit even when nothing changed (llm_director with `--force`, then explain and the renders); the web Re-edit button always sends it, so pressing Re-edit twice gives two versions.
- `--force` re-runs every station.
- `--notify` posts a short ntfy message with the preview link after a successful run (a fully skipped run says `Recap ready (cached)`); a failed run never notifies.
- `--preview-base URL` is the base of the `serve.py` server (e.g. `http://<agent-host>:8091`); the link is `<base>/<workspace folder name>/recap_720p.mp4`. Without it the message carries the workspace path and the summary's `preview_url` is `null`.
- `--json` prints one flat summary with `wall_time_s`, `llm_cost_usd` (this run) and `llm_spend_usd` (the workspace ledger).
- `--brief-file PATH` reads the brief from a file instead of `--brief`.

## Phone / Telegram phrases

The phrases an assistant (Hermes) receives on Telegram and what each one runs.
Setup is Doru's step; see [docs/guides/phone-flow.md](../../../docs/guides/phone-flow.md).

| You say (RO) | What happens |
|:---|:---|
| "fă-mi un recap din clipurile de azi" / "recap la clipurile noi" | `scripts/inbox.py --once ... --notify` on the inbox (today's group) |
| "reel din clipurile astea" / "montaj scurt, energic" | same, with a short energetic brief ("about 60 seconds, energetic, fast cuts") |
| "recap din folderul X, 3 minute, epic" | `scripts/recap.py --source X --brief "about 3 minutes, epic"` |
| "fă-l mai scurt / mai lent / mai epic" | new brief → `recap.py --brief "..."` on the same workspace (only router → llm_director → explain → render re-run) |
| "renunță la 0071/s2, tunelul mai lung" | `recap.py --feedback "drop 0071/s2, tunnel longer"` on the same workspace (only llm_director → explain → render re-run) |
| "unde e recap-ul?" / "trimite-mi linkul" | the `serve.py` URL of the latest project, from `--preview-base` + the workspace folder name |
| "reia de la capăt" | `recap.py --force` |

## Re-run rules

- Same brief: every station is skipped and the run costs nothing.
- New brief: only router → llm_director → explain → render re-run; the analysis is cached.
- `--feedback`: only llm_director → explain → render re-run; the analysis stays cached and the feedback lands in `feedback.jsonl`.
- New clips in the source folder: proxies and analysis run for the new clips; add `--force` if you want the edit redone, or re-run just the stations you need.

## Gotchas

- The originals folder must not be the workspace; `recap.py` refuses a workspace inside the source.
- The budget stops the chain when it is reached. Raise `--budget-usd` or edit `config.yaml: llm_budget_usd`.
- `doctor.py` warns when the originals folder is writable; make it read-only with `chmod -R a-w <trip-folder>/raw`.
- Proxies are 720p copies under `workspace/proxy/`; still photos (JPEG/HEIC) are copied through next to them so ingest sees the whole day. Proxies can be deleted and rebuilt at any time.
