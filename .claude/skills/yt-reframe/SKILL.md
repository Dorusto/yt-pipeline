---
name: yt-reframe
description: Follow the subject of a clip with a moving crop window and write a crop path JSON (time -> x, y) for any aspect (9:16 default) — OpenCV YuNet face detection per sampled frame, TrackerMIL between detections, a saliency map when there is no face, smoothed centre path with no jumps and a limited speed. Use when the user wants a clip reframed to vertical, a moving 9:16 crop, a crop path for a Shorts render, or asks where the subject is in frame over time. Trigger phrases - "reframează clipul", "crop vertical care urmărește subiectul", "crop path", "moving crop", "9:16 care urmărește fața", "reframe to vertical", "follow the subject". Not for cutting or rendering the short (that is `shorts_generator.py` / `yt-shorts`), not for describing content (that is `describe`) and not for camera-motion geometry (that is `motion`).
compatibility: Run from the repository root. Needs the repo .venv with opencv-python and numpy; CPU only. Reads the clip read-only, writes one JSON (default <video_dir>/auto/<stem>_reframe.json). The YuNet model downloads once to ~/.cache/yt-pipeline/models/ and never into the repository. Makes no LLM calls, so the run costs 0 USD.
---

# yt-reframe

Compute a crop path for a moving reframe of one clip: the crop window position
over time so a 16:9 source can be cut to any aspect while the subject stays in
frame. OpenCV-first: every sample is measured for free (face detection, tracking,
saliency); there is no model call and no spend.

## Step 1: write the crop path

```bash
.venv/bin/python scripts/reframe.py --video /path/to/clip.mp4 --json
```

- `--video` is required and read-only. `--out` sets the JSON path (default
  `<video_dir>/auto/<stem>_reframe.json`, `auto/` created if missing).
- `--start` / `--end` limit the range in seconds (default: the whole clip).
- `--aspect W:H` (default `9:16`) sets the target aspect. `crop_w`/`crop_h` are
  the largest even crop of that aspect that fits inside the frame
  (9:16 on 1920x1080 -> 608x1080; 1:1 -> 1080x1080).
- `--sample-fps` (default 4) is how often the subject is sampled.
- `--smooth` (default 0.5 s) is the EMA time constant of the centre path;
  `--max-speed` (default 0.35 frame widths per second) is the hard speed limit
  of the crop window — together they guarantee no jumps and no whip pans.
- `--model` points at a local YuNet ONNX (default
  `~/.cache/yt-pipeline/models/face_detection_yunet_2026may.onnx`, downloaded
  once from the OpenCV Zoo when missing).
- `--force` recomputes; without it a valid output newer than the video is
  skipped (re-run safety). Exit 0 on success or skip, 1 on error.

## How the subject is chosen

1. **face** — YuNet detects faces on a 640 px copy of every sample; the subject
   is the detection nearest the current subject centre (never "largest face",
   so the crop never jumps between people), else the largest detection.
2. **track** — between detections `cv2.TrackerMIL` follows the last box.
3. **saliency** — when there is no face and no track, a saliency map (spectral
   residual + frame difference, the bottom wake band down-weighted) gives the
   salient centre.
4. **hold** — when the saliency is flat or would jump more than 0.35 frame
   widths, the window keeps the last good centre instead of chasing the wake.

## What the output means

`schemas/reframe.schema.json` is the interface. Every point is
`{"t_s", "x", "y", "source", "confidence"}` with `x`,`y` the crop window's
**top-left corner in source pixels**, clamped so the window stays inside the
frame — ready for ffmpeg `crop=crop_w:crop_h:x:y`. `source` says what placed the
window (`face|track|saliency|hold`). The record also carries `crop_w`, `crop_h`,
`aspect`, `detector` (`yunet`), `tracker` (`mil`), `fallback` (`saliency`),
`warnings`, `wall_time_s` and `cost_usd` (always 0.0).

## Step 2: use the path

`shorts_generator.py` reads a crop path like this one for its moving crop when a
segment has no `x_offset` in `shorts_config.yaml` (an `x_offset` still overrides
with a fixed crop). To reframe by hand, feed the points into an ffmpeg `crop`
expression or into any editor that accepts a keyframed crop.

## Notes

- The tool never writes frames or footage anywhere; only the JSON.
- Boat footage: the wake is not a subject. When the saliency would chase it, the
  window holds — that is intended behaviour, not a bug.
