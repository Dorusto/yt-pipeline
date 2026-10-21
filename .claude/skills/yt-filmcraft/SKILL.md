---
name: yt-filmcraft
description: Bridge the FilmCraft edit and the publish flow without needing FilmCraft installed. Read a .fcproj project's timeline markers into markers.json (the graphics_plan.py contract) and the Rose markers into todo.md, or apply the loudness recipe — in-editor through the headless filmcraft-cli (Essential Sound Dialogue + Auto-Match + Clarity Dynamics) on a copy of the project, or without an editor as an ffmpeg two-pass loudnorm on a video file. Use when the user asks to pull the markers out of a FilmCraft project, wants the redo list outside the editor, wants the project's voice clips loudness-matched, or wants a video normalised to the YouTube loudness targets. Trigger phrases - "scoate markerele din proiectul FilmCraft", "fă markers.json din fcproj", "lista de refăcut din editor", "normalizează sunetul proiectului", "aplică rețeta de loudness", "read the FilmCraft markers", "markers.json from fcproj", "loudness match the project", "normalise this video to -14 LUFS". Not for importing subtitles into the FilmCraft caption track (out of scope), not for choosing the cut (that is `director`/`llm_director`), not for rendering (that is `render`) and not for measuring or cleaning ambient sound (that is `audio-clean`).
compatibility: Run from the repository root. Needs the repo .venv and ffmpeg/ffprobe. The markers tool reads the .fcproj read-only. The loudness --video backend needs only ffmpeg; the --fcproj backend needs filmcraft-cli (from $FILMCRAFT_CLI or PATH) and never modifies the input project unless --in-place is given.
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
.venv/bin/python scripts/fcproj_markers.py project.fcproj --json
.venv/bin/python scripts/fcproj_markers.py project.fcproj --sequence Main \
  --out markers.json --todo todo.md
```

- `--sequence` is required when the project has more than one sequence item; otherwise the only one is used.
- `--out` (default `<project dir>/markers.json`) is an object with `schema_version`, `source`, `sequence` and a `markers` array. Each marker carries `time_s` (ticks / 254016000000, rounded to milliseconds), `timecode` (`HH:MM:SS:FF` at the sequence frame rate), `name`, `comment`, `colour` (the file's `color` label as written) and `kind`. All markers, every colour, sorted by `time_s`.
- `--todo` (default `<project dir>/todo.md`) is rewritten every run (re-run safe): one `- [ ] HH:MM:SS:FF (s s) — name: comment` line per Rose marker, or a one-line note when there are none.
- Clip-level markers (`timeline clip .markers`) are out of scope: they are counted and reported as `clip_markers_ignored`, never mapped.
- `--json` reports `output`, `todo`, `sequence`, `markers`, `rose`, `clip_markers_ignored`, `wall_time_s`, `llm_cost_usd`.
- Exit codes: `0`, `2` user error (missing file, not a FilmCraft project, unknown `--sequence`, no sequence, bad marker shape), `1` unexpected.

## Loudness (`loudness.py`)

The audio recipe as one command, with two backends.

### Without an editor (`--video`)

An ffmpeg two-pass `loudnorm` to the YouTube targets (`I=-14 LUFS`, `TP=-1 dBTP`,
`LRA 11` by default). Pass 1 measures, pass 2 applies the measured values with
`linear=true`, copies the video stream and re-encodes the audio as AAC 320k.

```bash
.venv/bin/python scripts/loudness.py --video clip.mp4 --json
.venv/bin/python scripts/loudness.py --video clip.mp4 --out clip_loud.mp4 \
  --target-i -14 --target-tp -1 --target-lra 11
```

- `--out` defaults to `<stem>_loud.mp4` (or the input's own `.mov` suffix). Writing onto the input is refused (exit 2).
- `--json` adds `measured: {input_i, input_tp, input_lra, input_thresh}`, `target: {i, tp, lra}`, `output`, `backend: "ffmpeg"`, `wall_time_s`, `llm_cost_usd`.

### In the editor (`--fcproj`)

Applies the Essential Sound recipe to the voice clips of one audio track through the
headless `filmcraft-cli`: Dialogue type, Auto-Match loudness and Clarity Dynamics
medium (`clarity.enabled`, `clarity.dynamics.on`, `clarity.dynamics.amount: 5`).

```bash
.venv/bin/python scripts/loudness.py --fcproj project.fcproj --sequence Main --json
.venv/bin/python scripts/loudness.py --fcproj project.fcproj --dry-run
.venv/bin/python scripts/loudness.py --fcproj project.fcproj --in-place
```

- The binary comes from `$FILMCRAFT_CLI` or `filmcraft-cli` on PATH; missing → exit 2 naming both (not on `--dry-run`, which prints without needing the binary).
- The input project is never modified unless `--in-place` is given; without it the tool copies it to `--out` (default `<stem>_loudness.fcproj`) and runs against the copy. The copy path is re-run safe (it overwrites the derived copy only).
- Voice clips are the `enabled` timeline clips of the audio track named `--track` (default `Audio 1`); their item ids are the `clips` parameter. No such clip → exit 2 naming the track.
- Three commands run (`essentialSound.setType`, `essentialSound.autoMatch`, `essentialSound.set`), then `essentialSound.inspect` prints the measured and final loudness (`measured_lufs`, `target_lufs`, `match_gain`). A non-zero CLI exit or an `{"ok": false}` reply → exit 1 naming the command.
- `--dry-run` prints the exact command lines and skips the copy, without needing `filmcraft-cli` installed; `--json` then has `commands` and `dry_run: true`.
- `--json` reports `output`, `backend: "filmcraft"`, `sequence`, `track`, `clips`, `measured` (or `null` on `--dry-run`), `target_lufs`, `commands`, `wall_time_s`, `llm_cost_usd`.

## Export an edit to FilmCraft (`export_filmcraft.py`)

Turns an `edit.json` edit list into an editable FilmCraft `.fcproj` project. The
tool writes the project JSON directly (route `json`), using the exact envelope,
item, track and clip shapes a real FilmCraft save produces — no FilmCraft is
needed at export time, so it runs in CI and is unit-testable. Media is
referenced by path, never embedded.

```bash
.venv/bin/python scripts/export_filmcraft.py edit.json --out project.fcproj --json
.venv/bin/python scripts/export_filmcraft.py edit.json --out project.fcproj \
  --graphics-plan graphics_plan.json --graphics-dir overlays/ --sequence Main
```

- `--out` is required and written atomically (temp file + `os.replace`), so re-running is safe; an `--out` that would overwrite a source file is refused (exit 2).
- `--graphics-plan` places the `approved: true` entries as overlays; `--graphics-dir` holds the rendered `<entry id>.mov` files. `--sequence` (default `Main`), `--width`/`--height`/`--fps` (default `1920`/`1080`/`25`) set the sequence. `--screen FILE` (repeatable) marks a source as screen capture; a clip whose name contains `screen` is treated as screen too.
- Track layout: `Camera 1` (video slots), `Screen 1` (screen slots), `Audio 1` (each video slot's own audio), `Graphics 1` (overlays) above the picture.
- Markers: one Green marker per used slot with a reason, at the slot's timeline in-point; a slot flagged for a human check (`centred: true` or a speed ramp) gets a Rose "to redo" marker **instead of** its Green; one Mango marker per placed overlay. Rejected `decisions[]` have no timeline position and get no marker. `fcproj_markers.py` maps the colours into `markers.json`/`todo.md` (Rose = the redo list).
- Missing media is a **warning**, not an error: the project still opens with offline media. Photo slots export as stills with a "Ken Burns not exported" warning; speed ramps export at real time with a warning.
- `filmcraft-cli` is only needed to OPEN or inspect the result (resolved from `$FILMCRAFT_CLI` else PATH); the export itself never calls it.
- `--json` reports `output`, `sequence`, `tracks`, `slots_placed`, `photos_placed`, `overlays_placed`, `markers` (`green`/`rose`/`mango`), `missing_media`, `warnings`, `route: "json"`, `wall_time_s`, `llm_cost_usd` (`0.0`).
- On this machine the scripts need `.venv/bin/python` — the system `python3` has no `jsonschema`.

## What is out of scope

- Importing subtitles into the FilmCraft caption track.
- Mapping clip-level markers to timeline time (reported, not mapped).
- Changing `graphics_plan.py`, `publish.py` or any schema.
- Running the real FilmCraft app: the `--fcproj` backend is tested against a stub binary via `FILMCRAFT_CLI`.

## Notes

- Sources are read-only. Write `--out` and `--todo` into the project workspace, never into the footage folder.
- For measuring or cleaning ambient sound (wind, noise, loudness) use `audio-clean`; this tool only applies the loudness recipe.
