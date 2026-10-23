---
name: yt-thumbs
description: Write the poster and per-shot thumbnails of a recap workspace — thumbs/poster.jpg at 30 % of the recap and thumbs/shot-N.jpg at the middle of each timeline slot, each scaled to --width pixels wide (320 by default), skipping thumbnails newer than the recap unless --force. Use when the user wants the poster or the shot thumbnails of a recap, wants the Projects-page poster or the shot strip to show frames, or asks to regenerate the thumbnails after a new render. Trigger phrases - "fă thumbnail-urile recapului", "posterul recapului", "pozele pentru fiecare cadru", "regenerează thumbnail-urile", "make the recap thumbnails", "poster and shot thumbnails", "thumbs.py". Not for the YouTube thumbnail (that is `thumbnail`), not for building the edit (that is `director`/`llm_director`) and not for rendering the MP4 (that is `render`).
compatibility: Run from the repository root. Needs ffmpeg and ffprobe on PATH; no model call and no spend. Reads WORKSPACE/edit.json and the recap video read-only, writes WORKSPACE/thumbs/poster.jpg and WORKSPACE/thumbs/shot-N.jpg. Safe to re-run; thumbnails newer than the recap are skipped unless --force.
---

# yt-thumbs

Write the **poster** and the **per-shot thumbnails** of a recap workspace: `WORKSPACE/thumbs/poster.jpg` is the frame at **30 % of the recap** and `WORKSPACE/thumbs/shot-N.jpg` is the frame at the **middle of timeline slot N**, each scaled to `--width` pixels wide (320 by default). The Projects page shows the poster on each card and the shot strip under the player shows each shot's thumbnail. No model is called. All commands run from the repository root.

## Step 1: write the thumbnails

```bash
.venv/bin/python scripts/thumbs.py --workspace /path/to/workspace
```

- `--workspace` is required. It must hold `edit.json` (the timeline the shot count and the slot middles come from) and a recap video. `WORKSPACE/thumbs/` is created if missing; the recap and `edit.json` are never written to.
- `--recap FILE` names the recap to read (relative to the workspace or absolute). Without it the tool uses `recap_720p.mp4`, else `recap_720p_9x16.mp4`.
- `--width` sets the thumbnail width in pixels (default 320); the height keeps the aspect ratio.
- `--force` rewrites even when the poster and every shot thumbnail are newer than the recap. Without it a fresh set is skipped and counted as `skipped`.
- On any error it prints one `[error] ...` line to stderr and exits `2` (bad flags, missing `edit.json`, no recap, unreadable `edit.json`) or `3` (runtime failure after work started: ffprobe or ffmpeg failed, or ffprobe returned no duration). It never asks questions.

## Step 2: read the result

```bash
.venv/bin/python scripts/thumbs.py --workspace /path/to/workspace --json
```

The `--json` output is a single object, e.g. `{"tool": "thumbs.py", "workspace": "...", "recap": ".../recap_720p.mp4", "poster": ".../thumbs/poster.jpg", "shots": 12, "skipped": false, "wall_time_s": 1.2, "llm_cost_usd": 0.0}`. `shots` is the number of timeline slots (one `shot-N.jpg` per slot), `skipped` is true when the existing thumbnails were newer than the recap, and `llm_cost_usd` is always `0.0` — this tool calls no model service.

## Notes

- The poster is the frame at `duration * 0.30`; each shot thumbnail is the frame at the middle of its slot (`(start + end) / 2`).
- The thumbnails are plain JPEG files in the workspace, served by the web app at `/<project>/thumbs/poster.jpg` and `/<project>/thumbs/shot-N.jpg`; they are generated output and are never committed.
- It never cuts, trims or deletes anything; it only reads the recap and writes the JPEGs.
