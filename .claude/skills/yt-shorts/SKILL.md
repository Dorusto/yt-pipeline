---
name: yt-shorts
description: Generate vertical 9:16 Shorts / Reels (608x1080) with burnt-in karaoke-style subtitles from segments of a finished 1920x1080 video and its corrected Romanian .srt. Use when the user wants to cut Shorts or Reels, make vertical clips, or turn moments of a finished video into Shorts. Trigger phrases - "generează shorts pentru clipul X", "taie Shorts din video", "fă reels verticale cu subtitrări", "generate shorts from this video". For Short titles and descriptions see the yt-transcript skill, step 5 (it asks first).
compatibility: Run from the repository root. Needs an NVIDIA GPU, ffmpeg with h264_nvenc, and the repo .venv with requirements.txt installed.
---

# yt-shorts

Cut segments of a finished 1920x1080 video into vertical 608x1080 clips with burnt-in subtitles. All commands run from the repository root.

## Prerequisites

- NVIDIA GPU and an ffmpeg build with `h264_nvenc`. The render step fails without them.
- The repo `.venv` with `requirements.txt` installed. `whisperx` and `torch` are imported at startup even though word alignment is not run, so startup is slow and both must be installed.
- Source video is 1920x1080 (the crop is a fixed 608-pixel-wide window).
- A corrected Romanian `.srt` that matches the timeline of that same video (see the yt-transcript skill). Subtitles from a different edit burn the wrong text.

## Step 1: get the segments from the user

Segments are the user's decision: a name plus start and end time on the video's timeline. Do not invent them. If the user has none, ask.

Write them to `scripts/shorts_config.yaml` (git-ignored, local):

```yaml
video: "Clip.mp4"
srt: "Clip_RO.srt"

segments:
  - name: "Hook"
    start: "00:00:00"
    end: "00:00:54"
  - name: "Delegation"
    start: "00:05:06"
    end: "00:05:46"
    x_offset: 800
```

- `start` and `end` are `HH:MM:SS` (seconds may have decimals). End is not checked against start.
- `name` becomes part of file names (`Short<N>-<name>.mp4`, `auto/<name>_karaoke.ass`). Use no spaces or slashes.
- `x_offset` is optional: pixels from the left edge of the 1920-wide frame to the left edge of the crop, from 0 to 1312. Values outside that range make ffmpeg fail. A manual offset skips face detection for that segment.
- `video:` and `srt:` must be filled in or the script refuses to run, but the `video:` value is not used for anything. Do not add an `audio:` key; audio is not needed, and a missing audio file triggers an interactive prompt.
- If `scripts/shorts_config.yaml` does not exist, the first run creates a template and exits with code 0 without rendering anything. Config validation errors also exit with code 0, so read the output instead of trusting the exit code.

## Step 2: run

```bash
.venv/bin/python scripts/shorts_generator.py --video /abs/path/Clip.mp4 --srt /abs/path/Clip_RO.srt
```

- Always pass both `--video` and `--srt`. Without `--video` the script asks for the path with `input()`; without `--srt` it looks in the video folder, `../subtitles/` and `..`, and asks interactively if it finds nothing. An interactive prompt hangs a non-interactive agent.
- `--skip-alignment` is accepted but does nothing. Do not use it.
- Keep the video path free of `:`, `,` and `'`. The path of the generated subtitle file is passed to ffmpeg inside a `-vf` filter string, so these characters are likely to break it (inferred from the code, not tested).
- Output goes next to the video: `shorts/Short<N>-<name>.mp4` (N is the segment's position in the config) and `auto/<name>_karaoke.ass`. Existing files with the same names are overwritten without asking.

## Step 3: check what it printed, per segment

- `face detected in X/Y sampled frames`: face detection samples one frame every 2 seconds with a frontal-face detector. A low ratio means the crop may miss the speaker. With no face at all it prints a warning and centers the crop. Fix by setting `x_offset` for that segment: the face centre's X position in the 1920-wide frame minus 304.
- `N cuvinte`: the number of subtitle words found for the segment. 0 means no subtitle line falls inside the segment's time range. The Short is rendered with no subtitles. Check the segment times against the `.srt`.
- Confirm each output exists and its duration is close to end minus start: `ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 shorts/Short1-Hook.mp4`.
- You cannot judge framing or subtitle look from the files. Ask the user to watch the Shorts.

## How the subtitles behave

- Text comes from the corrected `.srt`. Entries that straddle a segment boundary are trimmed at the nearest sentence end, so a Short does not start or end with half a sentence.
- Three words per line, the current word highlighted in yellow.
- Word timing is an even split inside each `.srt` line, not aligned to the audio. On lines with long pauses the highlight can drift from the speech.
