---
name: yt-render
description: Render an edit.json edit list to an MP4 with ffmpeg — a 720p preview from the proxy clips in sources[] or a full-resolution final from the originals with --originals-dir. It cuts each video slot, scales and pads it to the frame, joins the slots in timeline order with crossfades or hard cuts and keeps each slot's own ambient audio. Use when the user asks to render the edit, preview the edit list, render the final from the originals, turn edit.json into an MP4, or make a recap. Trigger phrases - "randează editul", "fă preview-ul din edit.json", "randează finalul din original", "render the edit", "render final from originals", "720p recap", "render edit.json". Not for captions/ASS burn-in, graphics, music/SFX mixing or Kdenlive export.
compatibility: Run from the repository root. Needs the repo .venv and ffmpeg/ffprobe on the PATH. CPU-only (libx264). Reads edit.json and the clips it names read-only; writes the MP4 to --out. Captions, graphics, music and SFX are warned about and ignored.
---

# yt-render

The output adapter: turn an `edit.json` edit list into an MP4, either a preview-quality 720p cut from the proxy clips in `sources[]` or a full-resolution final from the originals when `--originals-dir` is given. It cuts each video slot, scales and pads it to the requested frame, joins the slots in timeline order with crossfades or hard cuts and keeps each slot's own ambient audio. All commands run from the repository root.

## Step 1: render

```bash
.venv/bin/python scripts/render.py --edit /path/to/workspace/edit.json \
  --out /path/to/workspace/recap_720p.mp4
```

- `--edit` is validated against `schemas/edit.schema.json` before anything is encoded.
- Each slot's `clip` is resolved in this order: `--originals-dir/<file>` when the file exists, then `sources[].file` → `sources[].path`, then `--media-dir DIR`. An unresolvable clip is an error.
- `--originals-dir DIR` is the folder of full-resolution originals. It is resolved before `sources[]`; when a clip is missing there the tool falls back to `sources[]` and warns once. The originals are opened read-only and never written to. `--out` must not be inside `--originals-dir`.
- `--width` and `--height` are optional. With no `--originals-dir` the frame defaults to 1280x720. With `--originals-dir` any omitted dimension comes from the native size of the first resolved source (probed with `ffprobe`). The video is always exactly the requested size.
- `--xfade-s` (default 0.3) is the crossfade length used when a slot's `transition.duration_s` is 0. A slot's own `duration_s` wins when it is greater than 0. A `cut` join, or a fade that resolves to 0, is a hard cut.
- `--force` rewrites even when the output is up to date.
- Re-running is safe: when `--out` exists and is newer than `--edit`, nothing is written and the run reports `status: "skipped"`.
- The MP4 is encoded to `<out>.tmp.mp4`, probed, then moved into place with `os.replace`. A failed encode leaves no file at `--out`.
- On any error (unreadable or invalid edit, unresolvable clip, `--out` inside the originals folder, ffmpeg failure, write failure) it prints one `[error] ...` line to stderr and exits 1.

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

The `--json` output is a single object, e.g. `{"status": "written", "out": "/abs/recap_720p.mp4", "duration_s": 5.72, "slots": 2, "width": 1280, "height": 720, "warnings": [], "originals_dir": null, "source": "sources", "wall_time_s": 2.03, "llm_cost_usd": 0.0}`. `status` is `written` or `skipped`; `duration_s` and the resolution come from an `ffprobe` pass over the encoded file. `originals_dir` is the folder or `null`, and `source` is `originals` when the run used `--originals-dir`, else `sources`. This tool never calls a model, so `llm_cost_usd` is always `0.0`.

## What is out of scope

- Captions/ASS burn-in and graphics overlays: warned about and ignored.
- Music and SFX mixing: warned about and ignored; the render keeps each slot's own ambience.
- Speed ramps, loudness normalisation and Kdenlive export.

## Notes

- Sources are read-only. Never point `--out` at `~/media/yt-test/proxy` or `~/media/yt-test/raw`; write into the project workspace.
- The edit list is the single source of truth (`schemas/edit.schema.json`); this tool is one adapter that reads it.
- For the analysis stages that feed the director see `docs/VISION.md` and the `yt-ingest`, `yt-motion`, `yt-quality` skills.
