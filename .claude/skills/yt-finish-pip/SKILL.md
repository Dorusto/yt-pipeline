---
name: yt-finish-pip
description: Matte the camera ranges that cover a FilmCraft sequence's screen spans and write the filmcraft-cli steps that place the matted person as a bottom-right picture-in-picture with an eased entry and a short anticipation exit, snapped to the screen span edges. Use when the user wants the camera picture-in-picture over the screen recording, wants the camera matted and placed over the screen spans, wants the finish_pip steps files, or wants the PiP finishing run against the live FilmCraft or a headless project file. Trigger phrases - "pune camera peste ecran", "picture-in-picture peste screen", "matează camera pentru PiP", "fă finish_pip", "camera PiP over the screen spans", "matte the camera for the picture-in-picture", "finish_pip.place.jsonl", "PiP bottom-right cu intrare/ieșire". Not for removing a background without placing it (that is `matte`), not for building the edit (that is `director`/`llm_director`), not for rendering an MP4 (that is `render`) and not for the intro layout, SFX or music (later lots).
compatibility: Run from the repository root. Needs the repo .venv with torch + torchvision for matte.py (CPU is fine) and ffmpeg/ffprobe on PATH. The place/animate phases need filmcraft-cli (from $FILMCRAFT_CLI or PATH) and either a running FilmCraft for --bridge or a .fcproj file for --project. Makes no LLM calls, so the run costs 0 USD.
---

# yt-finish-pip

The by-hand finishing placed the matted camera as a picture-in-picture over the
screen spans with an eased entry and exit. This tool does that from one command:
it reads a FilmCraft sequence inspect JSON, finds the spans where the screen track
has video content, mattes the camera ranges that cover them (padded and merged) and
writes the `filmcraft-cli` steps that place the mattes bottom-right over each span.

## Step 1: save the sequence inspect JSON

With the project open in FilmCraft (or headless), save the active sequence:

```bash
filmcraft-cli --bridge 127.0.0.1:7714 inspect sequence > sequence.json
```

## Step 2: run the tool

```bash
.venv/bin/python scripts/finish_pip.py --sequence sequence.json --camera-dir camera/ --dry-run
.venv/bin/python scripts/finish_pip.py --sequence sequence.json --camera-dir camera/ --json
.venv/bin/python scripts/finish_pip.py --sequence sequence.json --camera-dir camera/ --bridge 127.0.0.1:7714
.venv/bin/python scripts/finish_pip.py --sequence sequence.json --camera-dir camera/ --project project.fcproj
```

- `--sequence` is the inspect JSON (read-only). `--camera-dir` holds the camera
  source files named by the sequence items (read-only). Both are required.
- `--bridge HOST:PORT` runs the place and animate phases against the live
  FilmCraft; `--project FILE` runs them against a headless `.fcproj` file
  (`filmcraft-cli --project FILE --save ...`). The two are mutually exclusive
  (exit 2 when both are given). With neither, the tool only mattes and plans and
  warns that the place steps need `--bridge` or `--project`.
- `--matte-dir` (default `matte/`) holds the matte `.mov` files; `--out-dir`
  (default `.`) holds the steps files. Both are created if missing.
- `--pad-s` (default `0.5`) pads each camera range per side, clamped at 0.
  `--merge-gap-s` (default `2.0`) merges ranges of the same source under that gap
  into one `matte.py` call.
- `--scale` (default `45`) is the PiP scale in percent of the frame; `--scale-small`
  (default `36`) is used over a full-screen explainer scene, a screen span whose
  screen item name matches `--small-over` (default `(?i)(explainer|explica|grafic|anim)`).
- `--force` re-runs `matte.py` even when the matte output already exists.
- `--dry-run` prints the plan (spans, edge checks, camera ranges with their
  `matted`/`placed`/`keyframed` state, placements and keyframe poses) and writes
  nothing. `--json` prints one summary object.

## What it does

1. **Screen spans.** The screen track is the video track whose item names classify
   as screen (`common.media.clip_kind`); the camera track is the one whose items
   classify as camera. Disabled items and matte exports are ignored. Only video
   clips make a span (`common.media.is_video_name`): a still image, a PNG/JPG
   graphic or a nameless generator item on the screen track never makes a span and
   never classifies a track. A span is a maximal run of enabled screen video items;
   items touching or overlapping (gap under 0.05 s) merge, a real gap ends the span.
   No screen track or no span → an empty plan, exit 0.
2. **Camera ranges.** For every span, the source range of every enabled camera
   video item overlapping it is clipped to the span, mapped to source time, padded
   by `--pad-s` and merged per source under `--merge-gap-s`. A still image or a
   graphic on the camera track is not a camera piece and never makes a range or a
   placement. Each merged range is one `matte.py`
   call: `matte.py SOURCE --start A --end B --out <matte-dir>/<stem>_<A:07.2f>-<B:07.2f>.mov`
   (no `--ratio`; matte.py picks it from the input height). An existing output is
   skipped unless `--force`.
   A short piece at a span edge (clip straddling the edge or its start/end within
   0.05 s of it, overlap at most 1.0 s) is decided per piece: a **leftover** (the
   clip straddles the edge with an overlap under half its length, or the overlap is
   under 0.5 s) is a fragment of the surrounding full-screen stretch and is dropped;
   a **seam** (a real camera cut at the edge) keeps its own matte range and
   placement. Every edge piece is reported in the `edge checks:` section and in
   `edge_checks` with its padded source range, overlap, clip length, verdict and
   reason.
3. **Place phase.** One `timeline.place` per (span, camera piece), trimmed to the
   span, on one PiP track above the screen track: the first placement uses
   `"track": "new"`, the rest `"track": "V<n>"` with n = the sequence's video track
   count + 1. `audio: false`, `insert: false`. `sourceIn` is the offset of the
   clip's source start inside the matte, snapped to the frame grid with the
   sequence's own `settings.frame_rate` (`num`/`den`). Only the mattes of the
   placements that are actually new are imported (`filmcraft-cli --compact import`)
   to get their item ids, so a full re-run imports nothing.
4. **Animate phase.** All placements of one span share one pose: `--scale`
   (`--scale-small` on an explainer span) bottom-right, margin 5 % of the frame
   height. The entry (0.30 s) is a quick ease-out with a slight overshoot; the exit
   (0.25 s) is a short anticipation. Keyframes use `effects.setParam` (`motion`,
   `scale`/`position`, `keyframe: true`, `time` in timeline ticks) and
   `effects.setInterpolation` (`mediaTime` = the source-relative keyframe time =
   timeline − clip start + sourceIn). A placed matte item with no `clip` id is
   skipped with a warning instead of crashing.

## The two steps files

- `finish_pip.place.jsonl` — the `timeline.place` steps only.
- `finish_pip.animate.jsonl` — the `effects.setParam` / `effects.setInterpolation`
  steps. The engine assigns clip ids at run time (not predictable), so this file is
  written after the place run: with `--bridge` or `--project` the tool runs place,
  re-inspects, finds the placed clips by their matte names and writes and runs the
  animate file. With neither it writes no place file (the item ids would belong to a
  throwaway project) and prints the commands to run; a second invocation with
  `--sequence` pointing at the fresh inspect output writes the animate file from
  that JSON alone.

Re-run safe: existing matte outputs are skipped, already-placed PiP pieces are
skipped, only the new placements' mattes are imported, the steps files are
overwritten atomically. A range is `keyframed` when it is placed and every
placement shows the animate keyframes (a `motion` effect with non-empty `scale`
and `position` keyframes, or — when the inspect shape exposes no `effects` — a
`finish_pip.animate.jsonl` holding an `effects.setParam` step for the placed clip
id **with the `finish_pip.animate.applied` marker next to it**; the steps file
alone is written evidence, not applied evidence). When there is no new placement
and every range is already matted **and**
keyframed (vacuously true when there are no ranges at all), the plan prints
`nothing to do` instead of the placements and keyframes, and a non-dry-run writes
no steps files and prints `nothing to do`; `--force` never overrides
`nothing to do`. When the placements exist but the animate phase is missing or
failed, a re-run rewrites `finish_pip.animate.jsonl` (and runs it with
`--bridge`/`--project`, skipping the import and the place phase) instead of
returning `nothing to do`; regenerating the animate file clears the
`finish_pip.animate.applied` marker first, and a failed animate apply deletes the
steps file and the marker so the next run rewrites it. A second full run imports
nothing and reports `placements: 0`.

## `--json`

`ok`, `spans` (`start_s`/`end_s`/`explainer`), `ranges` (`source`/`start_s`/`end_s`/
`out`/`matted`/`placed`/`keyframed`), `placements` (the new ones), `edge_checks`
(`source`/`start_s`/`end_s`/`overlap_s`/`clip_s`/`verdict`/`reason`),
`nothing_to_do`, `steps_files`, `warnings`, `wall_time_s`, `llm_cost_usd` (always
`0.0`). On error: `{"ok": false, "error": ...}`.

Exit codes: `0` success, `2` user error (missing `--sequence`, unparseable sequence,
missing `--camera-dir`), `1` unexpected runtime failure (`matte.py` or
`filmcraft-cli` failing).

## Notes

- Sources are read-only. Write `--matte-dir` and `--out-dir` into the project
  workspace, never into the footage folder.
- The matte output name is the by-hand pass name (`<stem>_<A:07.2f>-<B:07.2f>.mov`),
  which `common.media.is_matte_export` matches, so a re-run finds its own mattes and
  the already-placed PiP pieces.
- For a plain background removal without placing it, use `matte`; for the edit list
  use `director`/`llm_director`; for the MP4 use `render`.
