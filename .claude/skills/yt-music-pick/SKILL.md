---
name: yt-music-pick
description: Pick a music track from a library folder by the brief and the shot tags, using the beats.py BPM and energy of every track, and write the choice plus the beat grid. Use when the user wants music auto, asks which track fits an edit, wants a soundtrack chosen for a recap, or says "alege o piesă", "pick a track", "music auto", "pune muzică potrivită", "ce melodie se potrivește". Not for detecting the beats of one track (that is `beats`), mixing the track under the recap (that is `render`) or generating music (YuE2, D6).
compatibility: Run from the repository root. Needs the repo .venv; ffmpeg for beats.py; no model call and no spend. Reads the music library read-only (writes only the `.yt-beats` measurement cache when the library is writable) and the workspace shotlog when given; writes `music_choice.json` and, with `--workspace`, `beats.json`.
---

# yt-music-pick

Choose a soundtrack: every audio file in a library folder is measured with
`beats.py` (BPM, energy, length) and scored against the brief's pace words, the
shot tags and the target duration. The winner is written to
`music_choice.json`; with `--workspace` the chosen track's beat grid is also
written to `workspace/beats.json`, which is what the directors read for the
beat snap. `recap.py --music auto` runs this as one station. All commands run
from the repository root.

## Step 1: pick the track

```bash
.venv/bin/python scripts/music_pick.py --library ~/media/music \
    --brief "about 60 seconds, chronological, calm start" --workspace /path/to/workspace --json
```

- `--library DIR` (default `~/media/music`): only the audio files directly in
  the folder (`.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg`). Never recursive.
- `--brief TEXT` / `--brief-file PATH`: one of them is required — the brief
  decides the pace band.
- `--workspace DIR` (optional): the shotlog's tags/summary/subject/scenery join
  the matching words, and `workspace/beats.json` + `workspace/music_choice.json`
  are written there.
- `--target-duration-s N` (optional): a track that covers the edit length wins
  ties.
- `--out PATH` (default `WORKSPACE/music_choice.json`), `--force` (ignore the
  measurement cache), `--json`, `--help`.
- Exit 1 with one `[error]` line when the library is empty or nothing could be
  analysed.

## How it scores

| Signal | Weight | Rule |
|:---|:---:|:---|
| BPM vs the brief's pace | 0.35 | calm words → 70–100 BPM, energetic words → 110–150, else 85–135; 1.0 inside, 0 at 30 BPM outside |
| Mean energy (`beats.py`) | 0.25 | calm → −24 dBFS, energetic → −10, else −16; `max(0, 1 − d/12)` |
| Name vs tags | 0.25 | tokens of the file name ∩ brief + shot words; `min(1, matches/2)` |
| Length | 0.15 | 1.0 when the track covers `0.7 × target`, else the ratio |

Measurements are cached in `<library>/.yt-beats/<file>.json` (skipped when the
library is not writable). Ties break by file name.

## Step 2: read the result

The `--json` summary (and `music_choice.json`) is one object: `status`,
`library`, `tracks`, `chosen`, `path` (absolute), `why`, `scores[]` (per track:
`total`, `bpm`, `energy`, `tag`, `duration`, `bpm_value`, `energy_mean_dbfs`),
`wall_time_s`, `llm_cost_usd` (always 0.0), `out`. `why` is one plain line:
`bpm 128 in band 110-150 (energetic) · energy -11.2 dBFS · 2 tag matches · 214 s vs 60 s target`.

## Notes

- The library is read only: a track is never moved, renamed or rewritten.
- For the whole pipeline in one command use `recap.py --music auto`
  (`yt-recap`); this tool is the decision step it calls.
