---
name: yt-publish
description: Build the publish kit for a finished edit — turn a FilmCraft project or an edit.json plus per-source Whisper word JSON files into a Romanian timeline .srt and a flat _words.json for the final cut, without transcribing again, or run one Whisper pass over a media file to produce a raw .srt and the per-source word JSON. Use when the user wants subtitles for an assembled edit, a timeline word list for the cut, asks to map existing word timestamps onto the edit's clips, or wants to transcribe a clip to .srt + word JSON. Trigger phrases - "fă subtitrarea pentru montaj", "subtitrări din proiectul FilmCraft", "words.json pentru edit", "subtitles from the edit", "timeline subtitles", "map words onto the cut", "transcrie clipul în srt", "transcribe the clip to words.json". Not for translating subtitles (that is `yt-transcript`), not for correcting them (`correct_srt.py`), not for building the edit (that is `director`/`llm_director`) and not for rendering (that is `render`).
compatibility: Run from the repository root with the repo .venv. Step 1 makes no model calls and needs no new dependencies; step 2 needs openai-whisper + torch on the real machine but runs offline with --backend mock. Reads a FilmCraft project (JSON, schema filmcraft-project) or an edit.json (schemas/edit.schema.json) and one Whisper word JSON per source clip. Originals are read-only; outputs are derived and overwritten on re-run.
---

# yt-publish

Build the publish kit for a finished edit. Step 1 produces the timeline subtitles and the word list for the final cut from word timestamps that already exist. Step 2 transcribes a source clip in one Whisper run when no word JSONs exist yet, and writes the raw `.srt` plus the per-source word JSON that step 1 consumes. Step 3 turns a corrected Romanian `.srt` plus a context note into a `metadata.md` publish draft. Later steps of the sprint (thumbnail, matte, graphics) will extend this skill.

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
- Errors exit 2 with a message on stderr and write nothing: missing or unreadable input, an ffmpeg extraction failure (names the input and the stderr tail), `whisper` not installed (`openai-whisper is not installed (pip install openai-whisper); use --backend mock for offline runs`), a model load failure, or an unknown `--backend`.
- Re-run safe: the outputs are derived files and are overwritten.

## Step 3: metadata draft

Turn a corrected Romanian `.srt` plus a context note into a `metadata.md` publish draft: 3 titles, a description in Romanian and English with the channel's fixed CTA wording, chapters from the timeline, tags and a pinned-comment proposal. A human reviews the draft; nothing is uploaded.

```bash
.venv/bin/python scripts/metadata.py SRT --context CONTEXT.md [--previous-url URL] [--repo-url URL] \
  [--out-dir DIR] [--name NAME] [--prompt PATH] [--json]
```

- `SRT` is the corrected Romanian `.srt` (timeline time). `--context` is required: a markdown note with the clip's angle and what the viewer leaves with.
- `--previous-url` links a relevant earlier clip; `--repo-url` links the clip's own code repo. Both are optional and are passed to the model in a `## Links` section.
- `--out-dir` is the output folder (default: the SRT's parent; created if missing). `--name` is used in the draft header and the `--json` summary (default: the SRT stem). It writes `<out-dir>/metadata.md`.
- `--prompt` is an alternative prompt file (default: `prompts/metadata.md`).
- The tool sends the prompt file text plus `## Context`, `## Transcript` (the SRT as `[HH:MM:SS] text` lines) and `## Links` to the `metadata` model role at `temperature=0.3`, and parses one JSON object back (a ```json fence is stripped).
- The channel-members sentence and the closing are fixed and never taken from the model: the members sentence substitutes `channel.members` from the config, and the closing is always last. Both appear verbatim in the RO and the EN description. The description order is intro → github line (omitted when null) → members sentence → previous line (omitted when null or when `--previous-url` was not given) → subscribe line → promo lines (each prefixed with its `[label]`) → closing.
- Chapters must be `HH:MM:SS` or `MM:SS` (normalized to `HH:MM:SS`), non-decreasing, inside the transcript, first one `00:00:00`.
- `--json` prints a summary: `output`, `titles`, `chapters`, `tags`, `wall_time_s` and `llm_cost_usd`.
- Errors exit 2 with a message on stderr and write nothing: missing or unreadable SRT/context, `channel.members` missing or empty in the config, a missing model role, budget or LLM errors, unparseable model JSON, a missing or invalid JSON key (named), or a bad chapter (named).
- Re-run safe: the output is a derived file and is overwritten.

## What this step does not do

- Step 1 never transcribes or re-runs Whisper; word times come only from the given JSON files. Step 2 is the initial transcription only — it does not correct (`correct_srt.py`), translate, or force-align the transcript.
- Step 3 writes only the draft; it never uploads, renders a thumbnail or translates the transcript (that is `translate_srt.py`).
- Neither step styles subtitles, reads video tracks, caption tracks or markers, or renders anything.
