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
.venv/bin/python scripts/music_pick.py --library <music-library> \
    --brief "about 60 seconds, chronological, calm start" --workspace /path/to/workspace --json
```

- `--library DIR`: the music library folder. There is no personal default — it
  comes from `--library`, else the `YT_PIPELINE_MUSIC_LIBRARY` environment
  variable, else `music.library` in the config; with none set the tool exits 1
  with `[error] no music library configured` naming those three ways. Only the
  audio files directly in the folder (`.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg`)
  are used. Never recursive. The folder is read only: no music is ever
  downloaded.
- `--brief TEXT` / `--brief-file PATH`: one of them is required — the brief
  decides the pace band.
- `--workspace DIR` (optional): the shotlog's tags/summary/subject/scenery join
  the matching words, and `workspace/beats.json` + `workspace/music_choice.json`
  are written there.
- `--target-duration-s N` (optional): a track that covers the edit length wins
  ties.
- `--out PATH` (default `WORKSPACE/music_choice.json`), `--force` (ignore the
  measurement cache), `--json`, `--help`.
- `--list` reports the library without choosing anything: one row per track
  with `file`, `path`, `duration_s`, `bpm` and the licence file status
  (`licence_status` `sidecar`/`library`/`missing` plus `licence_file`), as text
  or `--json` for an agent. A track that cannot be measured keeps nulls and the
  list still succeeds.
- Exit 1 with one `[error]` line when no library is configured, the library is
  empty or nothing could be analysed.

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

- The library is read only: a track is never moved, renamed or rewritten, and
  no tool downloads music.
- Licence file status per track: a `<stem>.license` / `<stem>.licence` (or
  `<file>.license` / `<file>.licence`) sidecar counts as `sidecar`, else the
  library's `LICENSE` / `LICENCE` / `LICENSE.txt` / `LICENCE.txt` / `COPYING`
  file counts as `library`, else `missing`.
- For the whole pipeline in one command use `recap.py --music auto`
  (`yt-recap`); this tool is the decision step it calls.
