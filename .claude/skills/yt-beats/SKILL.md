---
name: yt-beats
description: Detect a music file's tempo (BPM), beat and downbeat times and per-0.5 s energy curve with a pure-numpy spectral-flux/autocorrelation tool, and plan a beat-aligned cut grid for an edit of a given length so cuts land on the music. Use when the user asks for the tempo or BPM of a track, its beats/downbeats, when an edit's cuts should land to hit the music, or a beat grid for a given edit duration. Trigger phrases - "cât e BPM-ul piesei", "detectează beat-urile", "unde pică tăieturile pe muzică", "beat grid pentru 90 de secunde", "tempo of this track", "find the beats", "cut on the beat", "beat-aligned cut grid". Not for measuring or cleaning ambient sound (that is `audio-clean`), not for choosing music or SFX (that is the render's audio mix) and not for building the edit list (that is `director`).
compatibility: Run from the repository root. Needs the repo .venv with numpy and ffmpeg/ffprobe on the PATH; CPU only, no GPU and no network. Reads the music read-only, decodes it to mono 22.05 kHz PCM in a temp folder and writes only --out. It never calls a model service, so llm_cost_usd is 0.0. No new dependency: no librosa, only numpy + ffmpeg.
---

# yt-beats

The D2 `beats` station. It reads a music file and reports its tempo, beat and downbeat times and energy curve, and — when asked — a **cut grid** an edit can cut on. There is no music generation in D2; this tool only measures a track the brief already names. All commands run from the repository root.

## Step 1: measure a track

```bash
.venv/bin/python scripts/beats.py /path/to/song.mp3 --json
.venv/bin/python scripts/beats.py /path/to/song.mp3 --out /path/to/workspace/beats.json
```

- `MUSIC` is a music file, read-only (`.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg`).
- The audio is decoded to **mono 22.05 kHz** PCM in a temporary folder (never next to the source) and analysed in numpy.
- The `--json` summary is the same object written to `--out PATH`. Nothing is written without `--out`.

## Step 2: plan a cut grid

```bash
.venv/bin/python scripts/beats.py /path/to/song.mp3 --out beats.json \
  --target-duration-s 90 --slots 15
```

- `--target-duration-s` is the length of the edit in seconds. It is the switch for the cut grid (no target, no grid).
- `--slots N` is how many slots the grid should provide. Left out, it defaults to `clamp(round(target/6), 4, 24)` (so 90 s → 15). `--slots` without `--target-duration-s` is ignored with a warning.
- `--min-beat-s` / `--max-beat-s` are the plausible beat period range (defaults `0.25` / `2.0`).
- `--json` prints the summary, `--force` rewrites `--out` when it exists. It never asks questions.

## What the fields mean

- `duration_s` is the decoded length; `sample_rate` is fixed at 22050.
- `bpm` is the tempo clamped to `40–220`; `beat_period_s` is the fitted beat period in seconds.
- `beats` / `beat_s` are the count and times of the beat grid; `downbeats` / `downbeat_s` the count and times of the bar starts.
- `energy` is the RMS in dBFS per `0.5 s` window (`t_s`, `dbfs`); `energy_mean_dbfs` and `energy_peak_dbfs` summarise it. Silence floors at `-120 dBFS`.
- `cut_grid` is `null` unless `--target-duration-s` is given, else one row per slot: `{index, start_s, end_s, beats}`.
- `music_shorter_than_edit` is `true` when the track is shorter than the target (else `false`, `null` when no grid).
- `warnings` lists fallbacks (e.g. no onsets, a reduced slot count), `wall_time_s` is the wall time and `llm_cost_usd` is always `0.0`.

## How the measurements are made

The tool is **pure numpy** on audio decoded by ffmpeg; `librosa` is deliberately not used (it would pull in numba/scipy/soxr for a tempo and a beat grid).

- **Onsets.** An STFT (`1024` window, `256` hop), log magnitude and the positive spectral difference per frame give an onset envelope. It is peak-picked with a local-adaptive threshold (a frame above the mean of its ±10-frame neighbourhood by `0.5 * std`, at least `--min-beat-s` apart, strongest peaks first).
- **Tempo.** The onset envelope is autocorrelated over lags from `--min-beat-s` to `--max-beat-s`. The strongest local peak near the top is taken as the beat period, preferring the shorter of near-equal peaks so an accented bar does not read as a slower tempo.
- **Beat grid.** The detected onsets are snapped to the nearest grid position of the coarse period, then one least-squares linear fit through the onset indices gives the final period and phase; `beat_s` is that regular grid across the track. This keeps a 120 BPM track at exactly 0.5 s per beat even over a long file.
- **Downbeats.** A bar is assumed **4/4**. Beats are grouped in fours and the downbeats are every fourth beat, starting from the phase (0–3) whose summed onset strength is highest. Non-4/4 music is not supported yet.
- **Energy.** The RMS of every `0.5 s` window, in dBFS.

## The cut grid

- The grid is in **edit time** (not source time): it starts at `0`, ends exactly at the target duration and is strictly increasing.
- Every internal boundary is snapped to the nearest beat of the music; consecutive boundaries are at least one beat apart, so slot lengths land on whole beats (shot-grammar 2.3: "with music, lengths in whole beats or bars").
- When the music is longer than the edit, the grid uses the first `target_duration_s` seconds of the track.
- When the music is shorter than the edit, the grid covers the music and the last slot is longer (it absorbs the remainder up to the target); `music_shorter_than_edit: true` reports it.
- If the target holds fewer beats than `--slots`, the slot count is reduced with a warning. If no beat grid could be detected, `cut_grid` is `null` with a warning.

## Re-run safety and errors

- When `--out` already exists and is newer than the input and `--force` is not given, nothing is written and the run reports `status: "skipped"`.
- The music is read-only and `--out` is written to `<out>.tmp` then moved into place with `os.replace`.
- On any error (missing input, no audio stream, unreadable file, ffmpeg failure, a bad `--min-beat-s`/`--max-beat-s`, a non-positive target or slot count) it prints one `[error] ...` line to stderr and exits `1`.

## Notes

- The router enables this station only when the brief names a music file; beat sync and music generation are later work. Nothing here wires the grid into `director.py` yet.
- Write `--out` into the project workspace; never write next to the source footage.
