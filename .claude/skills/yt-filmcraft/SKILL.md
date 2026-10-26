---
name: yt-filmcraft
description: Bridge the FilmCraft edit and the publish flow without needing FilmCraft installed. Read a .fcproj project's timeline markers into markers.json, apply the loudness recipe or a colour recipe to the camera clips. Use when the user asks to pull the markers out of a FilmCraft project, wants the redo list outside the editor, wants the project's voice clips loudness-matched, wants a video normalised to the YouTube loudness targets, or wants the camera clips' colour evened out between sessions. Trigger phrases - "scoate markerele din proiectul FilmCraft", "fă markers.json din fcproj", "lista de refăcut din editor", "normalizează sunetul proiectului", "aplică rețeta de loudness", "read the FilmCraft markers", "markers.json from fcproj", "loudness match the project", "normalise this video to -14 LUFS", "normalizează culoarea clipurilor", "potrivește culoarea sesiunilor", "even out the colour between sessions", "colour recipe", "white balance per session". Not for `director`/`llm_director`, `render` or `audio-clean`.
compatibility: Run with the `yt` entry point on PATH. Needs the repo .venv and ffmpeg/ffprobe. The markers tool reads the .fcproj read-only. The loudness --video backend needs only ffmpeg; the --fcproj backend needs filmcraft-cli (from $FILMCRAFT_CLI or PATH) and never modifies the input project unless --in-place is given. The colour tool needs filmcraft-cli too and never modifies the input project unless --in-place is given.
---

# yt-filmcraft

Two standalone tools that bridge the FilmCraft edit and the publish flow. FilmCraft
itself need not be installed: the markers tool only reads the project JSON, and the
loudness `--video` backend is pure ffmpeg. No model is called; `llm_cost_usd` is
always `0.0`.

## Timeline markers (`fcproj_markers.py`)

Reads the sequence-level markers of a `.fcproj` project (read-only) and writes two
files next to it: `markers.json` in the shape `graphics_plan.py --markers` already
consumes, and `todo.md` holding only the Rose markers — the redo list outside the
editor.

```bash
yt fcproj_markers project.fcproj --json
yt fcproj_markers project.fcproj --sequence Main \
  --out markers.json --todo todo.md
```

- `--sequence` is required when the project has more than one sequence item; otherwise the only one is used.
- `--out` (default `<project dir>/markers.json`) is an object with `schema_version`, `source`, `sequence` and a `markers` array. Each marker carries `time_s` (ticks / 254016000000, rounded to milliseconds), `timecode` (`HH:MM:SS:FF` at the sequence frame rate), `name`, `comment`, `colour` (the file's `color` label as written) and `kind`. All markers, every colour, sorted by `time_s`.
- `--todo` (default `<project dir>/todo.md`) is rewritten every run (re-run safe): one `- [ ] HH:MM:SS:FF (s s) — name: comment` line per Rose marker, or a one-line note when there are none.
- Clip-level markers (`timeline clip .markers`) are out of scope: they are counted and reported as `clip_markers_ignored`, never mapped.
- `--json` reports `output`, `todo`, `sequence`, `markers`, `rose`, `clip_markers_ignored`, `wall_time_s`, `llm_cost_usd`.
- Exit codes: `0`, `2` user error (missing file, not a FilmCraft project, unknown `--sequence`, no sequence, bad marker shape), `3` runtime failure (ffmpeg or `filmcraft-cli` failing, missing binary).

## Loudness (`loudness.py`)

The audio recipe as one command, with two backends.

### Without an editor (`--video`)

An ffmpeg two-pass `loudnorm` to the YouTube targets (`I=-14 LUFS`, `TP=-1 dBTP`,
`LRA 11` by default). Pass 1 measures, pass 2 applies the measured values with
`linear=true`, copies the video stream and re-encodes the audio as AAC 320k.

```bash
yt loudness --video clip.mp4 --json
yt loudness --video clip.mp4 --out clip_loud.mp4 \
  --target-i -14 --target-tp -1 --target-lra 11
```

- `--out` defaults to `<stem>_loud.mp4` (or the input's own `.mov` suffix). Writing onto the input is refused (exit 2).
- `--json` adds `measured: {input_i, input_tp, input_lra, input_thresh}`, `target: {i, tp, lra}`, `output`, `backend: "ffmpeg"`, `wall_time_s`, `llm_cost_usd`.

### In the editor (`--fcproj`)

Applies the Essential Sound recipe to the voice clips of one audio track through the
headless `filmcraft-cli`: Dialogue type, Auto-Match loudness and Clarity Dynamics
medium (`clarity.enabled`, `clarity.dynamics.on`, `clarity.dynamics.amount: 5`).

```bash
yt loudness --fcproj project.fcproj --sequence Main --json
yt loudness --fcproj project.fcproj --dry-run
yt loudness --fcproj project.fcproj --in-place
```

- The binary comes from `$FILMCRAFT_CLI` or `filmcraft-cli` on PATH; missing → exit 3 naming both (not on `--dry-run`, which prints without needing the binary).
- The input project is never modified unless `--in-place` is given; without it the tool copies it to `--out` (default `<stem>_loudness.fcproj`) and runs against the copy. The copy path is re-run safe (it overwrites the derived copy only).
- Voice clips are the `enabled` timeline clips of the audio track named `--track` (default `Audio 1`); their item ids are the `clips` parameter. No such clip → exit 2 naming the track.
- Three commands run (`essentialSound.setType`, `essentialSound.autoMatch`, `essentialSound.set`), then `essentialSound.inspect` prints the measured and final loudness (`measured_lufs`, `target_lufs`, `match_gain`). A non-zero CLI exit or an `{"ok": false}` reply → exit 3 naming the command.
- `--dry-run` prints the exact command lines and skips the copy, without needing `filmcraft-cli` installed; `--json` then has `commands` and `dry_run: true`.
- `--json` reports `output`, `backend: "filmcraft"`, `sequence`, `track`, `clips`, `measured` (or `null` on `--dry-run`), `target_lufs`, `commands`, `wall_time_s`, `llm_cost_usd`.

## Colour (`colour.py`)

The basic colour recipe as one command: measures each recording session's average
white balance, exposure and light contrast on a few frames of the camera clips and
applies one Lumetri Basic Correction per session (temperature, tint, exposure,
contrast) to every camera clip of that session, through the headless
`filmcraft-cli`. No model is called; `llm_cost_usd` is always `0.0`.

```bash
yt colour --fcproj project.fcproj --sequence Main --json
yt colour --fcproj project.fcproj --dry-run
yt colour --fcproj project.fcproj --in-place
```

- The binary comes from `$FILMCRAFT_CLI` or `filmcraft-cli` on PATH; missing → exit 3 naming both (not on `--dry-run`, which prints without needing the binary).
- The input project is never modified unless `--in-place` is given; without it the tool copies it to `--out` (default `<stem>_colour.fcproj`) and runs against the copy. `--out` equal to the input is refused (exit 2).
- The camera clips are the `enabled` timeline clips of the video track named `--track` (default `Camera 1`); a clip whose media file is missing is a warning and is skipped. No enabled clip → exit 2 naming the track.
- Clips are sorted by capture time (the `YYYY-MM-DD HH-MM-SS` in the file name, else the file mtime) and grouped into sessions: a gap over `--session-gap-min` (default 180) starts a new session. Each session is measured on `--frames-per-clip` (default 3) frames per clip and gets one correction toward `--target-luma` (default 0.5) and `--target-contrast` (default 0.18); a session with no readable frame is a warning and is skipped, and no session at all → exit 2. `--target-luma`/`--target-contrast` outside `(0, 1]` (including 0) are usage errors (exit 2), not tracebacks.
- The recipe is grey-world white balance (temperature, tint), exposure toward the target luma and contrast toward the target contrast, each clamped to the Lumetri Basic Correction range.
- One `filmcraft-cli run` script applies the corrections: `effects.apply` only when the clip has no `lumetri` effect yet (it is not idempotent), then four `effects.setParam` (temperature, tint, exposure, contrast) per clip. A non-zero CLI exit or an `{"ok": false}` reply → exit 3 naming the command.
- `--dry-run` prints the exact JSONL lines and skips the copy, without needing `filmcraft-cli` installed; `--json` then has `commands` and `dry_run: true`.
- `--json` reports `output`, `backend: "filmcraft"`, `sequence`, `track`, `sessions` (each with `key`, `clips`, `frames`, `measured`, `correction`), `clips`, `commands`, `dry_run`, `warnings`, `wall_time_s`, `llm_cost_usd`.

## Export an edit to FilmCraft (`export_filmcraft.py`)

Turns an `edit.json` edit list into an editable FilmCraft `.fcproj` project. The
tool writes the project JSON directly (route `json`), using the exact envelope,
item, track and clip shapes a real FilmCraft save produces — no FilmCraft is
needed at export time, so it runs in CI and is unit-testable. Media is
referenced by path, never embedded.

```bash
yt export_filmcraft edit.json --out project.fcproj --json
yt export_filmcraft edit.json --out project.fcproj \
  --graphics-plan graphics_plan.json --graphics-dir overlays/ --sequence Main
yt export_filmcraft edit.json --out project.fcproj \
  --chapters chapters.json
yt export_filmcraft edit.json --out project.fcproj \
  --shorts-candidates shorts_candidates.json
```

- `--out` is required and written atomically (temp file + `os.replace`), so re-running is safe; an `--out` that would overwrite a source file is refused (exit 2).
- `--graphics-plan` places the `approved: true` entries as overlays; `--graphics-dir` holds the rendered `<entry id>.mov` files. `--sequence` (default `Main`), `--width`/`--height`/`--fps` (default `1920`/`1080`/`25`) set the sequence. `--screen FILE` (repeatable) marks a source as screen capture; a clip whose name contains `screen` is treated as screen too.
- `--chapters FILE` loads a `chapters.json` (validated against `schemas/chapters.schema.json`; a bad file is exit 2) and adds one Green section marker per chapter at `seconds_to_ticks(start_s)`, with `name` = the chapter title and `comment` = its summary. Without the flag the output is unchanged.
- `--shorts-candidates FILE` loads a `shorts_candidates.json` (validated against `schemas/shorts_candidates.schema.json`; a bad file is exit 2) and adds one Mango marker per top-3 candidate (by `rank`) at `seconds_to_ticks(start_s)`, with `name` = `short <rank>` and `comment` = `score <score>: <reason>`. Without the flag the output is unchanged.
- Track layout: `Camera 1` (video slots), `Screen 1` (screen slots), `Audio 1` (each video slot's own audio), `Graphics 1` (overlays) above the picture.
- Markers: one Green marker per used slot with a reason, at the slot's timeline in-point; a slot flagged for a human check (`centred: true` or a speed ramp) gets a Rose "to redo" marker **instead of** its Green; one Mango marker per placed overlay; with `--chapters`, one Green section marker per chapter; with `--shorts-candidates`, one Mango marker per top-3 candidate. Rejected `decisions[]` have no timeline position and get no marker. `fcproj_markers.py` maps the colours into `markers.json`/`todo.md` (Rose = the redo list).
- Missing media is a **warning**, not an error: the project still opens with offline media. Photo slots export as stills with a "Ken Burns not exported" warning; speed ramps export at real time with a warning.
- `filmcraft-cli` is only needed to OPEN or inspect the result (resolved from `$FILMCRAFT_CLI` else PATH); the export itself never calls it.
- `--json` reports `output`, `sequence`, `tracks`, `slots_placed`, `photos_placed`, `overlays_placed`, `markers` (`green`/`rose`/`mango`, plus `sections` when `--chapters` is given and `shorts` when `--shorts-candidates` is given), `missing_media`, `warnings`, `route: "json"`, `wall_time_s`, `llm_cost_usd` (`0.0`).
- On this machine the scripts need the repo `.venv` — the system `python3` has no `jsonschema`.

## What is out of scope

- Importing subtitles into the FilmCraft caption track.
- Mapping clip-level markers to timeline time (reported, not mapped).
- Changing `graphics_plan.py`, `publish.py` or any schema.
- Running the real FilmCraft app: the `--fcproj` backend is tested against a stub binary via `FILMCRAFT_CLI`.

## Notes

- Sources are read-only. Write `--out` and `--todo` into the project workspace, never into the footage folder.
- For measuring or cleaning ambient sound (wind, noise, loudness) use `audio-clean`; this tool only applies the loudness recipe.
