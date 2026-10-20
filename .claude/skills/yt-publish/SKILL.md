---
name: yt-publish
description: Build the publish kit for a finished edit — in this step, turn a FilmCraft project or an edit.json plus per-source Whisper word JSON files into a Romanian timeline .srt and a flat _words.json for the final cut, without transcribing again. Use when the user wants subtitles for an assembled edit, a timeline word list for the cut, or asks to map existing word timestamps onto the edit's clips. Trigger phrases - "fă subtitrarea pentru montaj", "subtitrări din proiectul FilmCraft", "words.json pentru edit", "subtitles from the edit", "timeline subtitles", "map words onto the cut". Not for transcribing audio (that is `yt-transcript`), not for translating subtitles (that is `yt-transcript`), not for building the edit (that is `director`/`llm_director`) and not for rendering (that is `render`).
compatibility: Run from the repository root with the repo .venv. No model calls, no new dependencies, CPU only. Reads a FilmCraft project (JSON, schema filmcraft-project) or an edit.json (schemas/edit.schema.json) and one Whisper word JSON per source clip. Originals are read-only; outputs are derived and overwritten on re-run.
---

# yt-publish

Build the publish kit for a finished edit. This step produces the timeline subtitles and the word list for the final cut from word timestamps that already exist, so nothing is transcribed again. Later steps of the sprint (transcribe, metadata, thumbnail, matte, graphics) will extend this skill.

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

## What this step does not do

- It never transcribes or re-runs Whisper; word times come only from the given JSON files.
- It does not translate, style subtitles, read video tracks, caption tracks or markers, or render anything.
