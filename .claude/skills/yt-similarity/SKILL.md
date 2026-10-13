---
name: yt-similarity
description: Embed every quality segment of a workspace and list the pairs whose cosine similarity reaches a threshold, so the director can refuse near-duplicate shots. Use when the user asks which shots look the same, wants near-duplicate segments found, wants a similarity threshold applied across clips, or needs the embeddings behind the diversity rule. Trigger phrases - "care cadre seamănă", "găsește segmentele duplicate", "similarity between segments", "ce cadre sunt aproape identice", "evită cadrele repetate". Not for camera-motion geometry (that is `motion`), quality defects and roles (that is `quality`) or semantic content (that is `describe`).
compatibility: Run from the repository root. Needs the repo .venv with opencv-python-headless; CPU only. Reads quality/<stem>.json and the clips read-only, writes similarity.json into the workspace. Frames are held in memory only and never written to disk. The default mobilenet backend downloads a 14 MB ONNX model once to ~/.cache/yt-pipeline/models/; use --backend hist for a fully offline run with no download.
---

# yt-similarity

Embed every quality segment of a workspace and list the visually similar pairs before any editing decision. The director uses the pairs to refuse near-duplicate shots ("no two similar segments in the edit unless the brief asks for it, never back to back"). The output is **one file per workspace** (`WORKSPACE/similarity.json`), not per clip, because pairs cross clips. **Frames are never written to disk** — the repo is public. All commands run from the repository root.

## Step 1: embed a workspace

```bash
.venv/bin/python scripts/similarity.py --workspace /path/to/workspace
```

- `--workspace` is required and must hold `quality/`. `DIR/similarity.json` is written there, created if missing. The source clips are never written to.
- `--backend` is `mobilenet` (default) or `hist`. `mobilenet` runs MobileNetV2 ONNX through `cv2.dnn` and gives the widest same-scene vs cross-scene separation; `hist` is the handcrafted HSV/gradient/luma fallback for an offline run with no model and no download.
- `--model-path` (default `~/.cache/yt-pipeline/models/mobilenetv2-12.onnx`) is the ONNX file; `--model-url` is where it is downloaded from once when missing. A failed download exits 1 and names `--backend hist` as the offline path.
- `--threshold` (default 0.90) is the cosine similarity at or above which a pair is listed.
- `--clips` limits the run to comma-separated 4-digit counters or stem substrings (e.g. `--clips 0072,0081`). Without it every `quality/*.json` is processed.
- `--force` recomputes even when `similarity.json` is up to date. Without it, an output newer than every quality file it was built from is skipped and counted as `skipped`.
- On any error (missing workspace, unreadable quality file, missing clip, missing model with no download, write failure) it prints one `[error] ...` line to stderr and exits 1. It never asks questions.

## Step 2: read the result

```bash
.venv/bin/python scripts/similarity.py --workspace /path/to/workspace --json
```

The `--json` output is a single object, e.g. `{"segments": 90, "pairs": 109, "backend": "mobilenet", "model": "mobilenetv2-12.onnx", "threshold": 0.9, "wall_time_s": 53.84, "llm_cost_usd": 0.0, "output": ".../similarity.json", "skipped": false}`. Read `DIR/similarity.json` for the embeddings and the full pair list; it is validated against `schemas/similarity.schema.json` and written atomically, so a run leaves a complete valid file or none.

## What the fields mean

- `segments` has one entry per quality segment, in clip order. `key` is `<clip counter>/<segment id>` (e.g. `0081/s7`), `clip` is the full clip file name, and `embedding` is the L2-normalised segment vector rounded to 4 decimals. Every quality segment gets an embedding — there is no junk filter here, the director decides what to use.
- `pairs` lists every pair with `similarity >= threshold`, most similar first, then by `a`, `b`. `a` and `b` are segment keys and are always different. `similarity` is the dot product of the two L2-normalised embeddings.
- `backend` and `model` record what produced the embeddings (`model` is `none` for `hist`); `threshold` is the value used. `llm_cost_usd` is always `0.0` — this tool calls no model service.
- The three frames per segment are sampled at `t_i = start_s + (i + 0.5) / 3 * (end_s - start_s)`, the same interior spacing as `describe`, and resized to 640 px width. The images themselves are never stored.

## Notes

- Label, never delete: the tool only embeds and lists pairs. It never cuts, trims or ranks.
- This is the similarity stage of D1b, after the CPU-only `ingest`, `motion`, `quality` and `describe`; its output feeds the director's diversity rule and the shotlog.
- For an offline run with no network and no download use `--backend hist`.
