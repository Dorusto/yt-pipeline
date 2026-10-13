---
name: yt-render
description: Render an edit.json edit list to a 720p MP4 with ffmpeg — cut each video slot from the proxy clip in sources[], scale and pad to 1280x720, join the slots in timeline order with crossfades or hard cuts and keep each slot's own ambient audio. Use when the user asks to render the edit, preview the edit list, turn edit.json into an MP4, or make a 720p recap. Trigger phrases - "randează editul", "fă preview-ul din edit.json", "render the edit", "720p recap", "render edit.json". Not for final-quality renders from originals, captions/ASS burn-in, graphics, music/SFX mixing or Kdenlive export.
compatibility: Run from the repository root. Needs the repo .venv and ffmpeg/ffprobe on the PATH. CPU-only (libx264). Reads edit.json and the clips it names read-only; writes the MP4 to --out. Captions, graphics, music and SFX are warned about and ignored.
---

# yt-render

The S1 output adapter: turn an `edit.json` edit list into a preview-quality 720p MP4. It cuts each video slot from the proxy clip named in `sources[]`, scales and pads it to the requested frame, joins the slots in timeline order with crossfades or hard cuts and keeps each slot's own ambient audio. All commands run from the repository root.

## Step 1: render

```bash
.venv/bin/python scripts/render.py --edit /path/to/workspace/edit.json \
  --out /path/to/workspace/recap_720p.mp4
```

- `--edit` is validated against `schemas/edit.schema.json` before anything is encoded.
- Each slot's `clip` is resolved through `sources[].file` → `sources[].path`; `--media-dir DIR` is a fallback for clip names that are not in `sources[]`. An unresolvable clip is an error.
- `--width` (default 1280) and `--height` (default 720) set the output frame; the video is always exactly that size.
- `--xfade-s` (default 0.3) is the crossfade length used when a slot's `transition.duration_s` is 0. A slot's own `duration_s` wins when it is greater than 0. A `cut` join, or a fade that resolves to 0, is a hard cut.
- `--force` rewrites even when the output is up to date.
- Re-running is safe: when `--out` exists and is newer than `--edit`, nothing is written and the run reports `status: "skipped"`.
- The MP4 is encoded to `<out>.tmp.mp4`, probed, then moved into place with `os.replace`. A failed encode leaves no file at `--out`.
- On any error (unreadable or invalid edit, unresolvable clip, ffmpeg failure, write failure) it prints one `[error] ...` line to stderr and exits 1.

## Step 2: read the result

```bash
.venv/bin/python scripts/render.py --edit /path/to/workspace/edit.json \
  --out /path/to/workspace/recap_720p.mp4 --json
```

The `--json` output is a single object, e.g. `{"status": "written", "out": "/abs/recap_720p.mp4", "duration_s": 5.72, "slots": 2, "warnings": [], "wall_time_s": 2.03, "llm_cost_usd": 0.0}`. `status` is `written` or `skipped`; `duration_s` and the resolution come from an `ffprobe` pass over the encoded file. This tool never calls a model, so `llm_cost_usd` is always `0.0`.

## What is out of scope

- Captions/ASS burn-in and graphics overlays: warned about and ignored.
- Music and SFX mixing: warned about and ignored; S1 renders ambience only.
- Speed ramps, loudness normalisation, final-quality renders from originals and Kdenlive export.

## Notes

- Sources are read-only. Never point `--out` at `~/media/yt-test/proxy` or `~/media/yt-test/raw`; write into the project workspace.
- The edit list is the single source of truth (`schemas/edit.schema.json`); this tool is one adapter that reads it.
- For the analysis stages that feed the director see `docs/VISION.md` and the `yt-ingest`, `yt-motion`, `yt-quality` skills.
