---
name: yt-render
description: Render an edit.json edit list to an MP4 with ffmpeg — a 720p preview from the proxy clips in sources[] or a full-resolution final from the originals with --originals-dir. It cuts each video slot, scales and pads it to the frame, renders each photo slot as a still with a Ken Burns move (portrait photos on a blurred background, never black bars), joins the slots in timeline order with crossfades or hard cuts, keeps each slot's own ambient audio and cleans the ambient bus like audio_clean (wind high-pass, RNNoise/afftdn, loudnorm; --no-audio-clean for the raw mix). Use when the user asks to render the edit, preview the edit list, render the final from the originals, turn edit.json into an MP4, or make a recap. Trigger phrases - "randează editul", "fă preview-ul din edit.json", "randează finalul din original", "render the edit", "render final from originals", "720p recap", "render edit.json". Not for captions/ASS burn-in, graphics, music/SFX mixing or Kdenlive export.
compatibility: Run from the repository root. Needs the repo .venv and ffmpeg/ffprobe on the PATH. CPU-only (libx264). Reads edit.json and the clips and photos it names read-only; writes the MP4 to --out and any HEIC→JPEG copy under the workspace. Captions, graphics, music and SFX are warned about and ignored.
---

# yt-render

The output adapter: turn an `edit.json` edit list into an MP4, either a preview-quality 720p cut from the proxy clips in `sources[]` or a full-resolution final from the originals when `--originals-dir` is given. It cuts each video slot, scales and pads it to the requested frame, renders each photo slot as a still with a Ken Burns move, joins the slots in timeline order with crossfades or hard cuts and keeps each slot's own ambient audio. All commands run from the repository root.

## Step 1: render

```bash
.venv/bin/python scripts/render.py --edit /path/to/workspace/edit.json \
  --out /path/to/workspace/recap_720p.mp4
```

- `--edit` is validated against `schemas/edit.schema.json` before anything is encoded.
- Each video slot's `clip` is resolved in this order: `--originals-dir/<file>` when the file exists, then `sources[].file` → `sources[].path`, then `--media-dir DIR`. An unresolvable clip is an error.
- Each photo slot (`kind: "photo"`) is resolved the same way from its `photo` file name: `--originals-dir/<file>` first, then a `sources[]` row with `media_type: "photo"`, then any `sources[]` row with that file, then `--media-dir DIR`. An unresolvable photo is an error.
- `--originals-dir DIR` is the folder of full-resolution originals. It is resolved before `sources[]`; when a clip or photo is missing there the tool falls back to `sources[]` and warns once. The originals are opened read-only and never written to. `--out` must not be inside `--originals-dir`.
- `--width` and `--height` are optional. With no `--originals-dir` the frame defaults to 1280x720. With `--originals-dir` any omitted dimension comes from the native size of the first resolved source (probed with `ffprobe`); when the first slot is a photo its own oriented size is used. The video is always exactly the requested size.
- `--xfade-s` (default 0.3) is the crossfade length used when a slot's `transition.duration_s` is 0. A slot's own `duration_s` wins when it is greater than 0. A `cut` join, or a fade that resolves to 0, is a hard cut.
- **Ambient cleanup (default on).** The mixed ambient bus runs the same chain `audio_clean.py` builds — wind `highpass` (`--wind-hz`, default 150, 0 disables), `arnndn` (RNNoise) or `afftdn` noise reduction and `loudnorm` (`--target-lufs`, default -16) — before it is encoded. `--no-audio-clean` keeps the raw mix. `--denoiser {arnndn,afftdn,none}` picks the backend (default: `arnndn` when the model file exists, else `afftdn` with a warning), `--model PATH` points at the RNNoise `.rnnn` model (default `~/.cache/yt-pipeline/models/arnndn.rnnn`). Asking for `--denoiser arnndn` without a model is an error (exit 1). The `--json` summary carries `audio_clean: {enabled, denoiser, filters}`.
- `--force` rewrites even when the output is up to date.
- Re-running is safe: when `--out` exists and is newer than `--edit`, nothing is written and the run reports `status: "skipped"`.
- The MP4 is encoded to `<out>.tmp.mp4`, probed, then moved into place with `os.replace`. A failed encode leaves no file at `--out`.
- On any error (unreadable or invalid edit, unresolvable clip or photo, `--out` inside the originals folder, ffmpeg failure, write failure) it prints one `[error] ...` line to stderr and exits 1.

## Photo slots

A photo slot holds a still for its `duration_s` (2–4 s) with a slow Ken Burns move. The render:

- decodes the still once and holds it for exactly `duration_s` at the output fps;
- frames it to the output size: a photo whose aspect matches the frame is cover-scaled directly; a portrait photo, or one whose aspect differs, sits on a **blurred cover-scaled copy of itself** with the photo scaled to fit (contain) centred — **never black bars**;
- applies the `ken_burns` move with ffmpeg `zoompan`: `zoom_in`/`zoom_out` change the zoom around (`focus_x`, `focus_y`), `pan_left`/`pan_right` keep the zoom and slide the window toward the focus (normalised 0..1);
- gets silent audio (`anullsrc`) when ambience is enabled, exactly like a video slot without an audio stream, and joins its neighbours with the same transition logic as video slots.

HEIC photos are converted once to JPEG under `<workspace>/photos/` with `common.photos.ensure_jpeg` (ffmpeg has no HEIC decoder); JPEG inputs are read directly. Photos are read-only.

## Step 2: render the final from the originals

```bash
.venv/bin/python scripts/render.py --edit /path/to/workspace/edit.json \
  --out /path/to/workspace/recap_final.mp4 --originals-dir ~/media/yt-test/raw
```

- The originals folder holds the same file names as `sources[]`; each slot is cut from the original instead of the proxy.
- The output size defaults to the native size of the first resolved original (e.g. 1920x1080); override with `--width` and `--height`.
- Never write the result back into the originals folder — the tool refuses `--out` inside `--originals-dir`. Write into the project workspace.

## Step 3: read the result

```bash
.venv/bin/python scripts/render.py --edit /path/to/workspace/edit.json \
  --out /path/to/workspace/recap_720p.mp4 --json
```

The `--json` output is a single object, e.g. `{"status": "written", "out": "/abs/recap_720p.mp4", "duration_s": 5.72, "slots": 2, "width": 1280, "height": 720, "warnings": [], "originals_dir": null, "source": "sources", "wall_time_s": 2.03, "llm_cost_usd": 0.0}`. `status` is `written` or `skipped`; `duration_s` and the resolution come from an `ffprobe` pass over the encoded file. `slots` counts video and photo slots. `originals_dir` is the folder or `null`, and `source` is `originals` when the run used `--originals-dir`, else `sources`. This tool never calls a model, so `llm_cost_usd` is always `0.0`.

## What is out of scope

- Captions/ASS burn-in and graphics overlays: warned about and ignored.
- Music and SFX mixing: warned about and ignored; the render keeps each slot's own ambience.
- Speed ramps, loudness normalisation and Kdenlive export.

## Notes

- Sources are read-only. Never point `--out` at `~/media/yt-test/proxy` or `~/media/yt-test/raw`; write into the project workspace.
- The edit list is the single source of truth (`schemas/edit.schema.json`); this tool is one adapter that reads it.
- For the analysis stages that feed the director see `docs/VISION.md` and the `yt-ingest`, `yt-motion`, `yt-quality` skills.

