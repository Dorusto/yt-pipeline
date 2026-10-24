---
name: yt-render
description: Render an edit.json edit list to an MP4 with ffmpeg — a 720p preview from the proxy clips in sources[] or a full-resolution final from the originals with --originals-dir. It cuts each video slot, scales and pads it to the frame, renders each photo slot as a still with a Ken Burns move (portrait photos on a blurred background, never black bars), joins the slots in timeline order with crossfades or hard cuts, keeps each slot's own ambient audio and cleans the ambient bus like audio_clean (wind high-pass, RNNoise/afftdn, loudnorm; --no-audio-clean for the raw mix). A music track (--music FILE or edit.json audio.music) is looped to the edit length, gained, ducked under the cleaned ambient bus with sidechaincompress and faded out at the end. Use when the user asks to render the edit, preview the edit list, render the final from the originals, turn edit.json into an MP4, mix a music track under the ambience, or make a recap. Trigger phrases - "randează editul", "fă preview-ul din edit.json", "randează finalul din original", "pune muzică sub ambiență", "mix the music under the ambience", "render the edit", "render final from originals", "720p recap", "render edit.json". Not for captions/ASS burn-in, graphics, SFX mixing or Kdenlive export.
compatibility: Run from the repository root. Needs the repo .venv and ffmpeg/ffprobe on the PATH. CPU-only (libx264). Reads edit.json and the clips, photos and music it names read-only; writes the MP4 to --out and any HEIC→JPEG copy under the workspace. Captions, graphics and SFX are warned about and ignored.
---

# yt-render

The output adapter: turn an `edit.json` edit list into an MP4, either a preview-quality 720p cut from the proxy clips in `sources[]` or a full-resolution final from the originals when `--originals-dir` is given. It cuts each video slot, scales and pads it to the requested frame, renders each photo slot as a still with a Ken Burns move, joins the slots in timeline order with crossfades or hard cuts and keeps each slot's own ambient audio. All commands run from the repository root.

## Step 1: render

```bash
.venv/bin/python scripts/render.py --edit <workspace>/edit.json \
  --out <workspace>/recap_720p.mp4
```

- `--edit` is validated against `schemas/edit.schema.json` before anything is encoded.
- Each video slot's `clip` is resolved in this order: `--originals-dir/<file>` when the file exists, then `sources[].file` → `sources[].path`, then `--media-dir DIR`. An unresolvable clip is an error.
- Each photo slot (`kind: "photo"`) is resolved the same way from its `photo` file name: `--originals-dir/<file>` first, then a `sources[]` row with `media_type: "photo"`, then any `sources[]` row with that file, then `--media-dir DIR`. An unresolvable photo is an error.
- `--originals-dir DIR` is the folder of full-resolution originals. It is resolved before `sources[]`; when a clip or photo is missing there the tool falls back to `sources[]` and warns once. The originals are opened read-only and never written to. `--out` must not be inside `--originals-dir`.
- `--width` and `--height` are optional. With no `--originals-dir` the frame defaults to 1280x720. With `--originals-dir` any omitted dimension comes from the native size of the first resolved source (probed with `ffprobe`); when the first slot is a photo its own oriented size is used. The video is always exactly the requested size.
- `--xfade-s` (default 0.3) is the crossfade length used when a slot's `transition.duration_s` is 0. A slot's own `duration_s` wins when it is greater than 0. A `cut` join, or a fade that resolves to 0, is a hard cut.
- **Ambient cleanup (default on).** The mixed ambient bus runs the same chain `audio_clean.py` builds — wind `highpass` (`--wind-hz`, default 150, 0 disables), `arnndn` (RNNoise) or `afftdn` noise reduction and `loudnorm` (`--target-lufs`, default -16) — before it is encoded. `--no-audio-clean` keeps the raw mix. `--denoiser {arnndn,afftdn,none}` picks the backend (default: `arnndn` when the model file exists, else `afftdn` with a warning), `--model PATH` points at the RNNoise `.rnnn` model (default `~/.cache/yt-pipeline/models/arnndn.rnnn`). Asking for `--denoiser arnndn` without a model is an error (exit 1). The `--json` summary carries `audio_clean: {enabled, denoiser, filters}`.
- **Music (optional).** `--music FILE` mixes a track under the cleaned ambient bus; `--music-gain-db` (default -12.0) sets its gain. See the Music section below.
- `--force` rewrites even when the output is up to date.
- Re-running is safe: when `--out` exists and is newer than `--edit` (and newer than the music file, when one is set), nothing is written and the run reports `status: "skipped"`.
- The MP4 is encoded to `<out>.tmp.mp4`, probed, then moved into place with `os.replace`. A failed encode leaves no file at `--out`.
- On any error (unreadable or invalid edit, unresolvable clip or photo, `--out` inside the originals folder, ffmpeg failure, write failure) it prints one `[error] ...` line to stderr and exits 1.

## Speed ramp (`middle_speed`)

The directors write `middle_speed` only when run with `--speed-ramp` (D2-12); by default every slot plays at normal speed and the render is a single trim.

A video slot with `middle_speed` above 1 (schema range 1.0-1.25, the directors write at most 1.2) is built from three trims of the same source window: the first quarter at real time, the middle half through `setpts=(PTS-STARTPTS)/s`, the last quarter at real time, joined with `concat`; the slot's ambient audio is cut the same way with `atempo=s` on the middle part, so it stays continuous. The on-screen length is `0.25L + 0.5L/s + 0.25L` (`common.moves.slot_screen_length`), and crossfade offsets and the music length use it. A slot without `middle_speed` (or 1.0) renders as one trim; cut positions are rounded on the timeline, so a slot whose length is not a whole number of frames may move by less than one frame.

## Vertical 9:16 (`--format`)

`--format 16x9` (default, nothing changes), `9x16` or `both`. For `9x16` each video slot gets a moving `crop` that follows its clip's reframe path: `scripts/reframe.py` runs once per source clip as a subprocess (on the proxy when one exists; the path is rescaled to the file that is rendered) and the crop path is cached as `<workspace>/reframe/<clip stem>_reframe.json` (reused while newer than the clip). The crop runs on source time before the trim, so a `middle_speed` ramp keeps the window on the subject. If reframe fails for a clip (for example the YuNet model cannot download) that clip gets a centred crop and a warning; the render still finishes. Photo slots keep the Ken Burns move: a portrait photo sits on the blurred background, a landscape photo is cropped to 9:16 around its `ken_burns.focus_x` subject position. Output is 720x1280 for a preview and 1080x1920 with `--originals-dir`; `--width/--height` override. With `both`, `--out` is the 16:9 file and the vertical one is written next to it as `<name>_9x16.mp4`; the JSON summary gets `format` and, for `both`, a `vertical` object. Both formats have the same duration.

## Photo slots

A photo slot holds a still for its `duration_s` (2–4 s) with a slow Ken Burns move. The render:

- decodes the still once and holds it for exactly `duration_s` at the output fps;
- frames it to the output size: a photo whose aspect matches the frame is cover-scaled directly; a portrait photo, or one whose aspect differs, sits on a **blurred cover-scaled copy of itself** with the photo scaled to fit (contain) centred — **never black bars**;
- applies the `ken_burns` move with ffmpeg `zoompan`: `zoom_in`/`zoom_out` change the zoom around (`focus_x`, `focus_y`), `pan_left`/`pan_right` keep the zoom and slide the window toward the focus (normalised 0..1);
- gets silent audio (`anullsrc`) when ambience is enabled, exactly like a video slot without an audio stream, and joins its neighbours with the same transition logic as video slots.

HEIC photos are converted once to JPEG under `<workspace>/photos/` with `common.photos.ensure_jpeg` (ffmpeg has no HEIC decoder); JPEG inputs are read directly. Photos are read-only.

## Music

A music track mixes **under the cleaned ambient bus** — ambient speech and sound always win. The track comes from `--music FILE` or, when the flag is absent, from `edit.json` `audio.music` (`{path, gain_db, fade_in_s, fade_out_s, start_s}`, all optional but `path`). `--music` overrides `audio.music.path`; `--music-gain-db` overrides `audio.music.gain_db`. Defaults: gain `-12.0` dB, fade-out `2.0` s at the very end, no fade-in, start at `0`. A relative `path` is resolved against the edit file's folder. The edit's cuts land on the beat grid (the directors snap them to the onset-aligned beats of `beats.py`), so the rendered cuts stay on the hit.

The mix graph, in one ffmpeg run:

- the track is resampled to 48 kHz stereo and **looped to the edit length** with `aloop` (a track shorter than the edit repeats; `looped: true` in the summary);
- it is trimmed to the edit length, gained with `volume`, and delayed by `start_s` when set;
- it is **ducked** under the ambient bus with `sidechaincompress` (music is the main input, the ambient bus the sidechain; threshold 0.03, ratio 8, attack 20 ms, release 400 ms), so the music drops whenever the ambience is loud;
- it is mixed with the ambient bus (`amix`, music at its gain, ambient at 1.0) and **faded out** with `afade=t=out` over the last `fade_out_s` seconds of the edit (`fade_in_s` fades the start when set).

When ambience is disabled (`audio.ambience.enabled: false`) there is nothing to duck under, so the track is mixed without `sidechaincompress` (`ducking: false`) but still loops and fades out. Photo slots produce silent `anullsrc` audio, so a music-only edit with photo slots works. The `--json` summary carries `music: {path, gain_db, ducking, looped}` or `music: null` when no track is set. A missing music file is an error (exit 1).

## Step 2: render the final from the originals

```bash
.venv/bin/python scripts/render.py --edit <workspace>/edit.json \
  --out <workspace>/recap_final.mp4 --originals-dir <source-folder>
```

- The originals folder holds the same file names as `sources[]`; each slot is cut from the original instead of the proxy.
- The output size defaults to the native size of the first resolved original (e.g. 1920x1080); override with `--width` and `--height`.
- Never write the result back into the originals folder — the tool refuses `--out` inside `--originals-dir`. Write into the project workspace.

## Step 3: read the result

```bash
.venv/bin/python scripts/render.py --edit <workspace>/edit.json \
  --out <workspace>/recap_720p.mp4 --json
```

The `--json` output is a single object, e.g. `{"status": "written", "out": "/abs/recap_720p.mp4", "duration_s": 5.72, "slots": 2, "width": 1280, "height": 720, "warnings": [], "originals_dir": null, "source": "sources", "audio_clean": {"enabled": true, "denoiser": "afftdn", "filters": ["highpass", "afftdn", "loudnorm"]}, "music": {"path": "/abs/track.mp3", "gain_db": -12.0, "ducking": true, "looped": true}, "wall_time_s": 2.03, "llm_cost_usd": 0.0}`. `status` is `written` or `skipped`; `duration_s` and the resolution come from an `ffprobe` pass over the encoded file. `slots` counts video and photo slots. `originals_dir` is the folder or `null`, and `source` is `originals` when the run used `--originals-dir`, else `sources`. `music` is `null` without a track, else the resolved path, gain, whether the track was ducked under the ambience and whether it was looped. This tool never calls a model, so `llm_cost_usd` is always `0.0`.

## What is out of scope

- Captions/ASS burn-in and graphics overlays: warned about and ignored.
- SFX mixing: warned about and ignored; the render keeps each slot's own ambience and the optional music track.
- Speed ramps, loudness normalisation beyond the ambient `loudnorm` and Kdenlive export.

## Notes

- Sources are read-only. Never point `--out` at the workspace proxies folder or `<source-folder>`; write into the project workspace.
- The edit list is the single source of truth (`schemas/edit.schema.json`); this tool is one adapter that reads it.
- For the analysis stages that feed the director see `docs/VISION.md` and the `yt-ingest`, `yt-motion`, `yt-quality` skills.

