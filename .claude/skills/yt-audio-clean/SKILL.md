---
name: yt-audio-clean
description: Measure a clip's ambient sound — per-2-second RMS, wind and broadband noise per clip — and clean the audio with ffmpeg (wind high-pass, RNNoise/afftdn noise reduction, loudnorm loudness) without touching the video. Use when the user asks how noisy or windy a clip is, wants the wind rumble or hiss removed, wants the ambient sound louder/quieter or normalised to a loudness target, or wants a cleaned audio file next to the footage. Trigger phrases - "curăță sunetul", "scoate vântul din audio", "cât de zgomotos e clipul", "normalizează volumul", "clean the audio", "remove wind noise", "denoise this clip", "loudness normalize". Not for choosing music or SFX (that is the render's audio mix), not for transcript/whisper and not for video quality (that is `quality`).
compatibility: Run with the `yt` entry point on PATH. Needs the repo .venv with numpy and ffmpeg/ffprobe on the PATH; CPU only, no GPU and no network. Reads the input read-only and writes only --out and --report. RNNoise (`arnndn`) needs a local .rnnn model; without one it uses `afftdn`. It never re-encodes video and never calls a model service.
---

# yt-audio-clean

The D2 audio station. It measures the ambient sound of a clip and, unless `--analyse-only` is given, cleans it in one ffmpeg pass. On action footage the ambient sound is part of the shot, so this tool measures and cleans it, it never mutes it. All commands run through `yt <tool>`.

## Step 1: measure

```bash
yt audio_clean /path/to/clip.mp4 \
  --report /path/to/workspace/audio_report.json --analyse-only --json
```

- `INPUT` is a video or audio file, read-only.
- The audio is decoded to **mono 48 kHz** PCM in a temporary folder (never next to the source) and measured in numpy.
- The measurements come out in the `--json` summary and, when given, in `--report PATH` (the same object).
- `--analyse-only` measures and reports; it writes no cleaned audio.

## Step 2: clean

```bash
yt audio_clean /path/to/clip.mp4 \
  --out /path/to/workspace/clip_clean.m4a \
  --report /path/to/workspace/audio_report.json --json
```

- `--out` is required unless `--analyse-only`. The container decides the codec: `.wav` → `pcm_s16le`, `.m4a` → `aac`, `.mp3` → `libmp3lame`, `.flac` → `flac`.
- The cleaned file keeps the source sample rate and channel layout. Video is never re-encoded (`-vn`); the source file is never modified.
- The output is written to `<out>.tmp<ext>` and moved into place with `os.replace`; the report is written the same way. A failed pass leaves no file at `--out`.
- The tool re-measures the cleaned output and reports it as `after` next to `before`, so the effect of the clean is visible.

## The filters

One ffmpeg audio pass is built in this order:

- `highpass=f=<wind-hz>` — only when wind is detected (`wind_ratio >= 0.55` on at least half of the 2 s windows) **or** `--wind-hz` was given explicitly above 0. `--wind-hz 0` disables it. Default corner `150 Hz`.
- `arnndn=m=<model>` **or** `afftdn=nr=12` — the noise backend chosen with `--denoiser`.
- `loudnorm=I=<target>:TP=-1.5:LRA=11` — unless `--no-loudnorm`. Default target `-16 LUFS` (`--target-lufs`).

`applied` lists the filter names that actually ran (`highpass`, `arnndn`/`afftdn`, `loudnorm`).

## Denoiser and the RNNoise model

- `--denoiser {arnndn,afftdn,none}`. Left at its default, the tool uses `arnndn` when a model exists, else falls back to `afftdn` with a warning.
- `--model PATH` is the RNNoise `.rnnn`/`.rnnu` model. The default is `~/.cache/yt-pipeline/models/arnndn.rnnn` when it exists.
- Asking for `--denoiser arnndn` with no model is an error: one `[error] ...` line naming `--model`, exit 2. No crash.
- `--denoiser none` applies only the high-pass and loudnorm.
- The tool never downloads a model. The default model cache is `~/.cache/yt-pipeline/models/arnndn.rnnn`; D2-2 fetched `marathon-prescription` from `GregorR/rnnoise-models` (rnnoise-nu format, models not subject to copyright per that repo's README) there. Without a model the tool still works, using `afftdn`. The same cleanup chain is shared with `render.py` (`scripts/common/ambience.py`), which runs it on the render's ambient bus.

## What the fields mean

- `before` and `after` are the same measurement object, or `null` for a missing stream.
- `duration_s` and `sample_rate` describe the analysis signal (mono 48 kHz).
- `rms_dbfs` is the overall RMS in dBFS; the clip is `quiet` when it is below `-45 dBFS`.
- `segments` tiles the clip into fixed `2.0 s` windows (`start_s`, `end_s`, `rms_dbfs`, and the window's own `wind_ratio`).
- `wind.wind_ratio` is the share of the `20 Hz–8 kHz` energy that sits below `--wind-hz` (`20 Hz–wind-hz`), overall and per window. `wind.wind_detected` is true when a window clears **0.55** and at least half the windows do. The threshold is the constant `WIND_RATIO_THRESHOLD`.
- `noise.noise_floor_dbfs` is the **10th percentile** of the per-window RMS in dBFS; `noise.snr_db` is the mean window RMS minus that floor. `noise.noise_detected` is true when `snr_db < 12`.
- `verdict` is one word: `clean`, `windy`, `noisy`, `windy+noisy` or `quiet`.
- `applied` lists the filters run, `warnings` lists fallbacks (e.g. the afftdn fallback or a missing audio stream), `wall_time_s` is the wall time and `llm_cost_usd` is always `0.0` (no model service is called).

## Re-run safety and errors

- When the requested outputs already exist and are newer than the input, and `--force` is not given, nothing is written and the run reports `status: "skipped"`.
- A file with no audio stream is not an error: `before` and `after` are `null` and a warning is added, exit 0. If `--out` was requested it cannot be produced, so that is an error (exit 2).
- On a usage error (missing input, `--out` missing without `--analyse-only`, unreadable file, unsupported container) it prints one `[error] ...` line to stderr and exits 2; on a runtime failure (ffmpeg or ffprobe failing after the run started) it exits 3. It never asks questions.

## Notes

- This is the ambient-sound station of D2; per-segment audio flags in `shotlog.json`, music/SFX and the render's audio mix are later work.
- The input and the source footage are read-only. Write `--out` and `--report` into the project workspace.
