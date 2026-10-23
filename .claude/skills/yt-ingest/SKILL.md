---
name: yt-ingest
description: Scan a footage folder and record per-clip metadata — duration, fps, resolution, streams, creation time and whether DJI gyro/IMU data is present — plus one row per still photo (JPEG/HEIC) with its EXIF capture time, into ingest.json for the AI Director pipeline. Use when the user asks to scan the footage folder, ingest metadata, check whether clips have gyro, inventory the still photos, or make an inventory of raw clips. Trigger phrases - "scanează folderul de footage", "ingest metadata", "verifică dacă are gyro", "ce poze sunt în folder", "scan the footage folder", "fă inventarul clipurilor". Not for analysing motion, quality or content, and not for generating proxies or editing.
compatibility: Run from the repository root. Needs the repo .venv and ffprobe on the PATH. Reads the source folder read-only and writes ingest.json into the workspace. Non-recursive; scans .mp4, .mov, .m4v clips and .jpg, .jpeg, .heic still photos only.
---

# yt-ingest

Build a machine-readable inventory of a folder of raw clips and still photos before any analysis. One `ffprobe` pass per video records duration, fps, resolution, every stream, the creation time and whether the clip still carries DJI gyro/IMU telemetry; one Pillow pass per photo records its size, orientation, format and EXIF capture time. All commands run from the repository root.

## Step 1: scan the folder

```bash
.venv/bin/python scripts/ingest.py /path/to/footage --workspace /path/to/workspace
```

- `SOURCE_DIR` is scanned non-recursively: clips with an `.mp4`, `.mov` or `.m4v` extension and still photos with an `.jpg`, `.jpeg` or `.heic` extension (any letter case) count, each group sorted by file name. Subfolders are ignored.
- `--workspace` is required. It is created if missing and `DIR/ingest.json` is written there. The source folder is never written to.
- Re-running is safe: it rescans and rewrites `ingest.json` from scratch. Nothing else is touched.
- On any error (missing folder, not a directory, neither a clip nor a photo found, ffprobe or photo decode failure) it prints one `[error] ...` line to stderr and exits 1. It never asks questions.
- A folder with only photos (no clips) scans fine: `clip_count` is 0 and `photos` holds the stills.
- A successful run prints a short summary: clip count, photo count, total duration and how many clips have gyro.

## Step 2: read the result

```bash
.venv/bin/python scripts/ingest.py /path/to/footage --workspace /path/to/workspace --json
```

The `--json` output is a single object, e.g. `{"clips": 21, "photos": 3, "output": "/abs/workspace/ingest.json", "gyro_clips": 0, "total_duration_s": 749.5, "wall_time_s": 3.2, "llm_cost_usd": 0.0}`. Read `ingest.json` when the per-clip or per-photo detail is needed.

`ingest.json` is validated against `schemas/ingest.schema.json` before it is written, so a run either leaves a complete, valid file or none at all.

## What the fields mean

- `duration_s` comes from the container, then the primary video stream, then `nb_frames / fps`.
- The primary video stream is the first real video stream with a width and height; attached-picture thumbnails are skipped.
- `fps` comes from `r_frame_rate`; `null` for `0/0`.
- `creation_time` is taken first from `creation_time` tags in the format or any stream (`creation_time_source: "metadata"`), otherwise parsed from a `YYYYMMDD_HHMMSS` (or 14 consecutive digits) pattern in the file name (`creation_time_source: "filename"`), otherwise `null`.
- `gyro.present` is true only when a stream has codec tag `djmd` or a handler name mentioning DJI meta, gyro, gyroscope or motion, and reports more than one sample. DJI cameras write IMU samples to that stream only when in-camera stabilisation was off; Mimo exports and proxies usually drop it. A present stream carries a short `gyro.evidence` string.
- `photo_count` and `photos[]` describe the stills found next to the clips, in file-name order. Each row carries `file`, `path`, `size_bytes`, `width`, `height`, EXIF `orientation`, `format`, `warnings` and the capture time. `creation_time` is ISO 8601 from EXIF `DateTimeOriginal` (falling back to `DateTime`) and `creation_time_source` is `"exif"`; both are `null` when the photo has no usable capture time. Photos are read with Pillow (plus pillow-heif for HEIC); their pixels are never copied.
- Per-clip and top-level `warnings` are human-readable notes; they are empty lists when there is nothing to report.

## Notes

- This is the first stage of the AI Director analysis pipeline (D1a). It only reads metadata; it does not score or describe footage.
- Do not point it at `<source-folder>` unless asked — that folder is for source footage and the tool treats whatever it is given as read-only.
- For later stages see `docs/VISION.md`; the output is meant to be read by the motion and quality tools.
