---
name: yt-publish
description: Build the publish kit for a finished edit — turn a FilmCraft project or an edit.json plus per-source Whisper word JSON files into a Romanian timeline .srt and a flat _words.json for the final cut, without transcribing again, run one Whisper pass over a media file to produce a raw .srt and the per-source word JSON, run the whole publish chain in one command (subtitles → corrections → verify → a human-review stop → EN translation → thumbnail → metadata draft), or build the manual-upload publish kit (video + RO/EN SRTs + thumbnail + studio.md copy-ready blocks and a Studio-order checklist). Use when the user wants subtitles for an assembled edit, a timeline word list for the cut, asks to map existing word timestamps onto the edit's clips, wants to transcribe a clip to .srt + word JSON, wants the publish kit run end to end, or wants the Studio upload sheet. Trigger phrases - "fă subtitrarea pentru montaj", "subtitrări din proiectul FilmCraft", "words.json pentru edit", "subtitles from the edit", "timeline subtitles", "map words onto the cut", "transcrie clipul în srt", "transcribe the clip to words.json", "rulează lanțul de publicare", "publish chain", "subtitles to metadata in one command", "publish.py", "fă publish kit-ul", "pregătește upload-ul manual", "studio.md", "publish kit". Not for translating subtitles alone (that is `yt-transcript`), not for correcting them alone (`correct_srt.py`), not for building the edit (that is `director`/`llm_director`) and not for rendering (that is `render`).
compatibility: Run from the repository root with the repo .venv. Step 1 makes no model calls and needs no new dependencies; step 2 needs openai-whisper + torch on the real machine but runs offline with --backend mock. Reads a FilmCraft project (JSON, schema filmcraft-project) or an edit.json (schemas/edit.schema.json) and one Whisper word JSON per source clip. Step 4 chains steps 1-3 plus correct_srt.py, translate_srt.py and thumbnail.py as subprocesses; it calls no model itself and runs offline with a mock config. Step 5 reads the chain's outputs and copies them into `publish-kit/`; `--kit-loudness` runs loudness.py as a subprocess. Originals are read-only; outputs are derived and overwritten on re-run.
---

# yt-publish

Build the publish kit for a finished edit. Step 1 produces the timeline subtitles and the word list for the final cut from word timestamps that already exist. Step 2 transcribes a source clip in one Whisper run when no word JSONs exist yet, and writes the raw `.srt` plus the per-source word JSON that step 1 consumes. Step 3 turns a corrected Romanian `.srt` plus a context note into a `metadata.md` publish draft. Step 4 runs the whole chain in one command, from the subtitles to the metadata draft, with a human-review stop in the middle. Step 5 builds the manual-upload kit: the final video, both SRTs, the thumbnail and a `studio.md` sheet with one copy-ready block per Studio field.

## Step 1: timeline subtitles from an edit project

Two inputs are accepted, and they use the same mapping rule.

From a FilmCraft project:

```bash
.venv/bin/python scripts/subs_from_edit.py --fcproj project.fcproj --sequence Main \
  --words-dir /path/to/words/ --out-dir /path/to/out/ [--track "Audio 1"] [--name NAME] [--json]
```

From an `edit.json` edit list:

```bash
.venv/bin/python scripts/subs_from_edit.py --from-edit edit.json \
  --words-dir /path/to/words/ --out-dir /path/to/out/ [--name NAME] [--json]
```

- `--words-dir` holds one Whisper word JSON per source clip, matched by file stem: source `camera A.mp4` needs `camera A.json`. The word JSON is the Whisper output shape (`segments[].words[]` with `word`, `start`, `end`); every word of the file is used, in order, and the leading space is stripped.
- `--out-dir` is created if missing. `--name` is the output stem (default: the sequence name, or the `--from-edit` file stem). It writes `<name>_RO.srt` and `<name>_words.json`.
- `--fcproj` reads only the named audio track (default `Audio 1`); the voice lives there. `--sequence` names the sequence item to use.
- `--from-edit` is mutually exclusive with `--fcproj`/`--sequence`/`--track`. Slots are placed in `video[]` order; a `crossfade` transition into a slot overlaps it with the previous one by `transition.duration_s` (0.3 s when absent), clamped to half of each neighbour's on-screen length — the same rule as `render.py`; a `cut` or absent transition does not overlap. Photo slots hold the timeline for their `duration_s` and contribute no words.
- Mapping: for each enabled clip, the source window is `[source_in, source_in + duration)` (FilmCraft) or `[in_s, out_s)` (edit.json). A word is kept when its midpoint lies in the window (half-open), then shifted to `clip_timeline_start + (word_time - window_start)`. Disabled clips are skipped and counted.
- The tool refuses `speed` ≠ 1.0 or `reverse: true` (FilmCraft) and `speed`/`middle_speed` ≠ 1.0 (edit.json): word times would not map linearly. The error names the clip or slot.
- The `.srt` packs words into cues: a cue ends after sentence punctuation (`.`, `!`, `?`, `…`) or a pause over 0.6 s, at most 2 lines of at most 42 characters, broken at word boundaries. A single word longer than 42 characters goes alone.
- `--json` prints a summary: `outputs`, `clips` (per timeline clip, with `kept_words` and timeline start/end; disabled clips carry `"skipped": "disabled"`), `words_kept`, `words_dropped`, `cues`, `wall_time_s` and `llm_cost_usd` (always 0.0 — no model calls).
- Errors exit 2 with a message on stderr and write nothing: missing word JSON (names the clip and the path it looked for), a non-1.0 speed or reverse, an unknown sequence or track (lists the names found), unreadable or non-JSON input, or both/neither of `--fcproj` and `--from-edit`.
- Re-run safe: the outputs are derived files and are overwritten.

## Step 2: transcribe a source clip

Run one Whisper pass over a media file and write a raw `.srt` plus the per-source word JSON that step 1's `--words-dir` reads. Use it when no edit project and no word JSONs exist yet, then transcribe each source clip with `--name` set to the source stem.

```bash
.venv/bin/python scripts/transcribe.py INPUT [--out-dir DIR] [--name NAME] [--model MODEL] \
  [--language ro] [--backend whisper|mock] [--json]
```

- `INPUT` is an audio (wav/mp3/…) or video file. For video, the audio track is extracted with ffmpeg to a temporary 16 kHz mono wav that is deleted afterwards.
- `--out-dir` is the output folder (default: the input file's parent; created if missing). `--name` is the output stem (default: the input file stem). It writes `<name>.srt` and `<name>_words.json`.
- `--backend whisper` (default) runs `openai-whisper`; `--backend mock` is deterministic and offline (no torch/whisper needed) and always returns `Bună ziua. Aceasta este o înregistrare de test.` with eight words.
- `--model` is the Whisper model id (default: `turbo` with a CUDA GPU, else `tiny`). `--language` is a Whisper code, default `ro`; `auto` lets Whisper detect.
- The `<name>_words.json` is the Whisper result shape (`text`, `language`, `segments[].words[]` with `word`, `start`, `end`, `probability`). Name it after the source stem (`camera A.json` for `camera A.mp4`) and step 1 picks it up.
- `--json` prints a summary: `outputs` (`srt`, `words_json`), `backend`, `model`, `device` (`cuda`/`cpu` for whisper, `mock` for mock), `language`, `segments`, `words`, `wall_time_s` and `llm_cost_usd` (always 0.0 — a local model, not an LLM call).
- Errors exit 2 with a message on stderr and write nothing: missing or unreadable input or an unknown `--backend`. An ffmpeg extraction failure (names the input and the stderr tail), `whisper` not installed (`openai-whisper is not installed (pip install openai-whisper); use --backend mock for offline runs`) or a model load failure exit 3 (runtime failure after work started).
- Re-run safe: the outputs are derived files and are overwritten.

## Step 3: metadata draft

Turn a corrected Romanian `.srt` plus a context note into a `metadata.md` publish draft: 3 titles, a description in Romanian and English with the channel's fixed CTA wording, chapters from the timeline, tags, a pinned-comment proposal and a `## Thumbnail` section with 2-3 photo concepts. A human reviews the draft; nothing is uploaded.

```bash
.venv/bin/python scripts/metadata.py SRT --context CONTEXT.md [--chapters chapters.json] \
  [--previous-url URL] [--repo-url URL] [--out-dir DIR] [--name NAME] [--prompt PATH] [--json]
```

- `SRT` is the corrected Romanian `.srt` (timeline time). `--context` is required: a markdown note with the clip's angle and what the viewer leaves with.
- `--previous-url` links a relevant earlier clip; `--repo-url` links the clip's own code repo. Both are optional and are passed to the model in a `## Links` section together with `channel_url`, the channel's code URL from `channel.github_url` in the config.
- `--chapters` takes a `chapters.json` (validated against `schemas/chapters.schema.json`) and uses its chapters instead of the model's: each `start_s` becomes a normalized `HH:MM:SS` timestamp and the existing chapter rules apply (first at `00:00:00`, non-decreasing, inside the transcript). The model's own `chapters` key becomes optional and is ignored when present; without the flag nothing changes. A missing or invalid file exits 2 naming the path or the offending chapter.
- `--out-dir` is the output folder (default: the SRT's parent; created if missing). `--name` is used in the draft header and the `--json` summary (default: the SRT stem). It writes `<out-dir>/metadata.md` and, next to it, `<out-dir>/metadata.json` with the validated fields (`name`, `titles`, `description_ro`, `description_en`, `chapters`, `tags`, `pinned_comment`, `thumbnail_concepts`) so the publish kit is deterministic from the model JSON.
- `--prompt` is an alternative prompt file (default: `prompts/metadata.md`).
- The tool sends the prompt file text plus `## Context`, `## Transcript` (the SRT as `[HH:MM:SS] text` lines) and `## Links` to the `metadata` model role at `temperature=0.3`, and parses one JSON object back (a ```json fence is stripped).
- The channel-members sentence and the closing are fixed and never taken from the model: the members sentence substitutes `channel.members` from the config, and the closing is always last. Both appear verbatim in the RO and the EN description. The description order is intro → github line (omitted when null) → members sentence → previous line (omitted when null or when `--previous-url` was not given) → subscribe line → promo lines (each prefixed with its `[label]`) → closing.
- Chapters must be `HH:MM:SS` or `MM:SS` (normalized to `HH:MM:SS`), non-decreasing, inside the transcript, first one `00:00:00`.
- The `## Thumbnail` section holds 2-3 distinct photo concepts (the `thumbnail_concepts` model key): each with pose, a real emotion (never neutral), thumbnail text of 3-4 words that says the same as the titles (row 1 title, row 2 keyword, kicker), theme `tech` or `creator`, and the source — a clip-frame timecode inside the transcript where that emotion fits, or a new photo to shoot. The chosen concept maps directly to the `thumbnail.py` flags (`--photo --title --keyword --kicker --theme`). Channel formula: own face + authentic emotion + short text, never a generic illustration.
- `--json` prints a summary: `output`, `titles`, `chapters`, `tags`, `thumbnail_concepts`, `wall_time_s` and `llm_cost_usd`.
- Errors exit 2 with a message on stderr and write nothing: missing or unreadable SRT/context, `channel.members` or `channel.github_url` missing or empty in the config, a missing model role, a bad chapter (named) from `--chapters`. Budget or LLM errors, unparseable model JSON, a missing or invalid JSON key (named) or a bad thumbnail concept (named) exit 3 (the model call failed after work started).
- Re-run safe: the output is a derived file and is overwritten.

## Step 4: the publish chain

One command runs the whole publish kit for a finished edit: subtitles (from the edit project if given, else transcribe) → corrections → verify (the corrected SRT against the final video) → a stop for human review → `--resume` → EN translation → thumbnail (when a photo is given) → metadata draft → the manual-upload kit (with `--kit`). A step whose output exists is skipped, and the run history is kept in `PROJECT/publish_state.json`.

```bash
.venv/bin/python scripts/publish.py --project DIR --context FILE \
  (--fcproj PROJ --sequence NAME [--track T] | --from-edit EDIT.json | --media FILE) \
  [--words-dir DIR] [--name NAME] \
  [--photo FILE --title T --keyword K --theme tech|creator [--kicker K]] \
  [--previous-url URL] [--repo-url URL] [--chapters FILE] \
  [--transcribe-backend whisper|mock] [--resume] [--force] [--json] \
  [--kit [--final-video FILE] [--kit-loudness] [--kit-title TITLE]]
```

- `--project` is the project folder (created if missing); all outputs and `publish_state.json` live there. `--context` is the context note for `metadata.py`.
- Source, exactly one of: `--fcproj PROJ --sequence NAME [--track T]` (+ `--words-dir DIR`, required) → `subs_from_edit.py`; `--from-edit EDIT.json` (+ `--words-dir DIR`, required) → `subs_from_edit.py`; `--media FILE` → `transcribe.py` (`--words-dir` not allowed with it).
- `--name` is the artifact stem (default: the project folder's name).
- `--photo` + `--title` + `--keyword` + `--theme` (+ optional `--kicker`) run the `thumbnail.py` step; without `--photo` the step is skipped and `--title`/`--keyword`/`--theme` must not be given.
- `--previous-url`/`--repo-url` are passed to `metadata.py`. `--transcribe-backend` is passed to `transcribe.py` (`whisper` default, `mock` for offline runs).
- `--chapters FILE` is forwarded to `metadata.py` as `--chapters FILE`, so the metadata draft takes its chapters from that `chapters.json` instead of the model. It is a metadata-step input, allowed with or without `--kit`.
- Steps, each a subprocess of the station CLI: subtitles → correct → verify → review → translate → thumbnail → metadata → kit (only with `--kit`). The working raw SRT is `PROJECT/<name>.srt`; `subs_from_edit.py` writes `<name>_RO.srt`, which the chain renames to `<name>.srt` before `correct_srt.py` produces the canonical `<name>_RO.srt`. Words stay at `PROJECT/<name>_words.json`.
- The `verify` step compares the corrected `PROJECT/<name>_RO.srt` with the final video already known to the run (`--final-video`, or the `--media` file): the last cue's end time must be within 2 s of the video duration and the SRT must not be older than the video. A mismatch exits 2 with one `[error]` line naming both times (or saying the subtitles are older) and aborts the chain before translation, metadata and the kit, so a cut removed or added after the subtitles were exported is caught. With no final video (`--fcproj`/`--from-edit` without `--final-video`) the step is skipped with reason `no final video`.
- The chain stops at the review step unless `--resume` is given: review `PROJECT/<name>_RO.srt`, then re-run with `--resume`. The `--json` summary then has `"stopped_at": "review"` and the exit code is still 0. The kit is the last step, so it is only built after the review has passed.
- A step whose output exists is skipped; `--force` re-runs every step (still stopping at the review without `--resume`). A child exit code other than 0 aborts the chain: the message names the step and the child's stderr tail, the step is recorded `failed` and the exit code is 2 when the failed step reported a usage error, else 3.
- `--json` prints a summary: `project`, `name`, `steps` (name, status, outputs), `stopped_at`, `outputs` (`srt_raw`, `words_json`, `srt_ro`, `srt_en`, `thumbnail`, `metadata`), `wall_time_s` and `llm_cost_usd` (the sum of the children's reported costs plus the `LLM cost: $X.XXXX` line from `translate_srt.py`). With `--kit` it also carries `kit` (`output`, `video`, `title`, `checks`).
- The state file `PROJECT/publish_state.json` is rewritten atomically after every step and records the run history; the skip decision is the output-existence rule, not the state file.
- Errors exit 2 with a message on stderr: missing or conflicting source flags, `--words-dir` missing for fcproj/from-edit or given with `--media`, `--title`/`--keyword`/`--theme` given without `--photo` (or `--photo` without them), `--final-video`/`--kit-loudness`/`--kit-title` given without `--kit`, a verify mismatch (the SRT end time or date does not match the final video), a child step failing, or a project folder that is not writable.

## Step 5: the manual-upload kit

`--kit` adds the `kit` step as the last step of the chain. It builds `PROJECT/publish-kit/` for a manual YouTube Studio upload: the final video, `<name>_RO.srt`, `<name>_EN.srt`, `thumbnail.jpg` and `studio.md` with one copy-ready block per Studio field plus a checklist in Studio order. No API upload and no FilmCraft caption-track import.

```bash
.venv/bin/python scripts/publish.py --project DIR --context FILE \
  (--fcproj PROJ --sequence NAME | --from-edit EDIT.json | --media FILE) \
  --words-dir DIR --resume --kit [--final-video FILE] [--kit-loudness] [--kit-title TITLE] --json
```

- `--final-video` is the finished edit's video file to put in the kit. It defaults to the `--media` file when the source is `--media`; with `--fcproj`/`--from-edit` it is required (exit 2 naming the flag).
- `--kit-loudness` runs `scripts/loudness.py --video <final video> --json` as a subprocess first and puts its `_loud` output in the kit as the video; without it the kit gets a plain copy of `--final-video`. A loudness failure fails the kit step with the tool's message tail.
- `--kit-title` is the chosen Studio title (default: the first `metadata.json` title). `--title` stays the thumbnail flag and is still only valid with `--photo`.
- `studio.md` holds: the video filename to upload; a Studio-order checklist (upload → title → description → thumbnail → subtitles RO + EN as subtitles, not closed captions → visibility private first → playlist/category/language → pinned comment → end screen/cards → publish); the title in a fenced block with the other two options below; the RO and EN descriptions in fenced blocks with the chapters appended at the end (blank line, then one `MM:SS Title` line per chapter); the tags comma-separated on one line with `(N/500 characters)` in the heading; the pinned comment; and short reminders (playlist, category, video language Romanian, default caption language RO, made-for-kids no, visibility private).
- The kit is written only after every check passes; a failing check exits 2 with one `[error]` line per problem on stderr and leaves no `publish-kit/` behind. The checks: title over 100 characters (names the length); either description with chapters over 5000 characters (names which and the length); missing `<name>_EN.srt` (names the path); thumbnail missing, over 2 MB or not 1280x720 (names the file, size or dimensions); tags line over 500 characters (names the length); missing `--final-video` or a video file that does not exist (names the flag/path); missing `metadata.json` (names the path and says to run the metadata step first).
- The kit step is skipped when `PROJECT/publish-kit/studio.md` exists; `--force` re-runs it. Re-running overwrites the kit folder contents (derived artefacts). The final video is copied, never moved or overwritten.

## What this step does not do

- Step 1 never transcribes or re-runs Whisper; word times come only from the given JSON files. Step 2 is the initial transcription only — it does not correct (`correct_srt.py`), translate, or force-align the transcript.
- Step 3 writes only the draft; it never uploads, renders a thumbnail or translates the transcript (that is `translate_srt.py`).
- Step 5 never uploads to YouTube or TikTok (no API, no OAuth) and never imports subtitles into a FilmCraft caption track; it only copies the chain's outputs and writes `studio.md` for a manual Studio upload.
- Neither step styles subtitles, reads video tracks, caption tracks or markers, or renders anything.
