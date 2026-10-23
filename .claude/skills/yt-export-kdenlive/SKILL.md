---
name: yt-export-kdenlive
description: Export an edit.json edit list to the fragments.yaml that kdenlive_from_fragments.py reads, then build a .kdenlive project in one more command. One fragment per video slot, in timeline order, carrying the slot id, score and reason; optional --source-dir points the project at the full-quality originals and --kdenlive-out names the project path. Use when the user asks to open the edit in Kdenlive, export the edit list for Kdenlive, turn edit.json into fragments.yaml, or build a rough cut in Kdenlive from the director's edit. Trigger phrases - "exportă editul în Kdenlive", "fă fragments.yaml din edit.json", "deschide montajul în Kdenlive", "export the edit for Kdenlive", "edit.json to fragments.yaml", "build the Kdenlive project from the edit". Not for selecting the cut windows (that is `director`/`llm_director`), rendering an MP4 (that is `render`), or analysing footage (`ingest`/`motion`/`quality`/`describe`).
compatibility: Run from the repository root. Needs the repo .venv and PyYAML. Reads edit.json read-only and writes fragments.yaml; the optional Kdenlive project is written by kdenlive_from_fragments.py. Kdenlive itself need not be installed to export.
---

# yt-export-kdenlive

The optional Kdenlive adapter: turn the director's `edit.json` edit list into the
`fragments.yaml` that `kdenlive_from_fragments.py` already consumes, so the edit
opens in Kdenlive one command later. The edit list stays `edit.json`; this tool
only reads it. No model is called and Kdenlive need not be installed.

## Step 1: export the edit list

```bash
.venv/bin/python scripts/edit_to_fragments.py --edit /path/to/workspace/edit.json \
  --out /path/to/workspace/fragments.yaml
```

- `--edit` is validated against `schemas/edit.schema.json` before anything is written; an invalid document is an error.
- `--out` is validated against `schemas/fragments.schema.json` before it is written. It holds `source_dir`, `output` and one fragment per `video[]` slot, in timeline order.
- `--source-dir DIR` is the folder the fragments point at. It should hold the **full-quality originals** when the edit's `sources[]` paths point at proxies. Default: the directory of the first `sources[].path`; fall back to `.`.
- `--kdenlive-out PATH` is the `output:` field of the YAML, the `.kdenlive` project the generator should write. Default: `--out` with the suffix replaced by `.kdenlive`.
- `--force` rewrites even when the output is up to date.
- Re-running is safe: when `--out` exists and is newer than `--edit`, nothing is written and the run reports `status: "skipped"`. The YAML is written atomically.
- On any error it prints one `[error] ...` line to stderr and exits `2` (bad flags, missing/invalid edit, schema violation) or `3` (runtime failure after work started: disk write). It never asks questions.

Each fragment carries `file` (the slot's `clip`), `in`, `out`, and the extra fields `label` (slot id, score to two decimals and the first words of the reason), `score`, `reason` and `slot`. The generators ignore the extra fields.

## Timecode convention

`kdenlive_from_fragments.py` treats a fragment's `out` timecode as **frame-inclusive**: `dur_frames = out_frames - in_frames + 1`. `edit.json`'s `out_s` is **exclusive**. To keep the fragment length equal to the slot length, this tool writes `out` as `timecode(out_s - 1 / FPS)` at the consumer's frame rate (`FPS = 25`), clamped so it never lands before `in`, rounded to milliseconds. Do not hand-edit the timecodes to "fix" this.

## Step 2: build the Kdenlive project

```bash
.venv/bin/python scripts/edit_to_fragments.py --edit /path/to/workspace/edit.json \
  --out /path/to/workspace/fragments.yaml \
  --source-dir <source-folder> \
  --kdenlive-out <workspace>/recap.kdenlive

.venv/bin/python scripts/kdenlive_from_fragments.py /path/to/workspace/fragments.yaml
```

The generator probes each source clip, writes the `.kdenlive` project named in `output:` and (unless `--skip-melt`) validates it with `melt`. Kdenlive opens the project with one AV-linked clip per fragment.

## Step 3: read the result

```bash
.venv/bin/python scripts/edit_to_fragments.py --edit /path/to/workspace/edit.json \
  --out /path/to/workspace/fragments.yaml --json
```

The `--json` output is a single object with `status` (`written` or `skipped`), `out`, `fragments` (count), `source_dir`, `kdenlive_out`, `total_duration_s`, `warnings`, `wall_time_s` and `llm_cost_usd`. This tool never calls a model, so `llm_cost_usd` is always `0.0`.

## What is out of scope

- Changing `kdenlive_from_fragments.py`, `srt_from_fragments.py` or `schemas/fragments.schema.json`.
- Selecting the cut windows, rendering an MP4, and graphics/captions/music mixing.
- A Kdenlive installation check: the export works without Kdenlive.

## Notes

- Sources are read-only. Write `--out` and `--kdenlive-out` into the project workspace, never into the footage folder.
- The edit list is the single source of truth (`schemas/edit.schema.json`); this tool is one adapter that reads it. For the analysis stages that feed the director see `docs/VISION.md` and the `yt-ingest`, `yt-motion`, `yt-quality`, `yt-index`, `yt-director` skills.
- A slot with `middle_speed` (speed ramp of a camera move) is exported at real time, because Kdenlive fragments cannot express the ramp: the summary carries a warning naming the slots, and the project runs longer than the render.
