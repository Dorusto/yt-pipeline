---
name: yt-shorts
description: Generate vertical 9:16 Shorts / Reels (608x1080) with burnt-in karaoke-style subtitles from segments of a finished 1920x1080 video and its corrected Romanian .srt, using the real word times of a timeline `_words.json` when given (brand font and colours, phrase-based line breaks). Also ranks Shorts candidates from a rough cut (`shorts_rank.py` → `shorts_candidates.json`) and cuts them with `shorts_generator.py --candidates`. Use when the user wants to cut Shorts or Reels, make vertical clips, turn moments of a finished video into Shorts, or rank the best Shorts spans of an edit. Trigger phrases - "generează shorts pentru clipul X", "taie Shorts din video", "fă reels verticale cu subtitrări", "generate shorts from this video", "care sunt cele mai bune momente de Shorts", "rankează candidații de Shorts", "rank the Shorts candidates". For Short titles and descriptions see the yt-transcript skill, step 5 (it asks first).
compatibility: Run from the repository root. Needs ffmpeg; uses h264_nvenc with an NVIDIA GPU, h264_videotoolbox on macOS, otherwise falls back to libx264 (works on CPU). Needs the repo .venv with requirements.txt installed. torch and whisperx are no longer needed (lazy import, only for the unused forced-alignment path).
---

# yt-shorts

Cut segments of a finished 1920x1080 video into vertical 608x1080 clips with burnt-in subtitles. All commands run from the repository root.

## Prerequisites

- NVIDIA GPU optional. The render selects `h264_nvenc` when an NVIDIA GPU is detected (`nvidia-smi`) and it is available in ffmpeg, `h264_videotoolbox` on macOS, otherwise `libx264`. If the chosen encoder fails at render time it retries once with `libx264` (unless already using it). Force one with `--encoder`.
- The repo `.venv` with `requirements.txt` installed. `torch` and `whisperx` are imported lazily inside the unused forced-alignment path, so they are not needed for the active pipeline.
- Source video is 1920x1080 (the crop is a 608x1080 window that follows the subject).
- A corrected Romanian `.srt` that matches the timeline of that same video (see the yt-transcript skill). Subtitles from a different edit burn the wrong text.
- The caption look comes from `brand.json` (repo root): the `fonts.caption` family and size, the `colors.ink`/`colors.warm`/`colors.night` palette. A missing brand file or font is a hard error naming the path and the key; the tool never hardcodes a font or colour.

## Step 1: get the segments from the user

Segments are the user's decision: a name plus start and end time on the video's timeline. Do not invent them. If the user has none, ask.

Write them to `scripts/shorts_config.yaml` (git-ignored, local):

```yaml
video: "Clip.mp4"
srt: "Clip_RO.srt"

karaoke:
  safe_zone_frac: 0.33     # caption box bottom, fraction of the frame height above the bottom
  scale_active_word: false # gentle ASS scale (106%) on the active word

layout: face               # face (default) or split

split:
  face_h_frac: 0.45    # face window height as a fraction of the source height
  face_cell_frac: 0.33 # share of the 9:16 output height for the face cell
  screen_h_frac: 0.75  # screen window height as a fraction of the source height
  screen_x_frac: 0.5   # screen window centre x as a fraction of the source width
  screen_y_frac: 0.5   # screen window centre y as a fraction of the source height

segments:
  - name: "Hook"
    start: "00:00:00"
    end: "00:00:54"
  - name: "Delegation"
    start: "00:05:06"
    end: "00:05:46"
    x_offset: 800
  - name: "ScreenDemo"
    start: "00:06:00"
    end: "00:06:40"
    layout: split
```

- `start` and `end` are `HH:MM:SS` (seconds may have decimals). End is not checked against start.
- `name` becomes part of file names (`Short<N>-<name>.mp4`, `auto/<name>_karaoke.ass`). Use no spaces or slashes.
- `x_offset` is optional: pixels from the left edge of the 1920-wide frame to the left edge of the crop, from 0 to 1312. Values outside that range make ffmpeg fail. A manual offset pins a fixed crop for that segment and skips subject tracking. It is ignored (with a warning) when the segment's layout is `split`.
- `karaoke:` is optional. `safe_zone_frac` (default 0.33) sets the caption box bottom as a fraction of the frame height above the bottom, so the caption sits above the Shorts UI; `scale_active_word` (default false) adds a gentle 106% ASS scale to the active word. A non-mapping `karaoke:` value is a user error (exit 1).
- `layout:` is optional: `face` (default) is the `reframe.py` face crop; `split` stacks the face region over the screen region of the same 16:9 frame (for a face-over-screen recording). It can be set per segment, per candidate (`shorts_candidates.json`), or top-level as the default; the first match wins. A value outside `face`/`split` is a user error (exit 1, naming the value).
- `split:` is optional and only used by `layout: split`. `face_h_frac`/`screen_h_frac` are the source window heights as fractions of the source height, `face_cell_frac` is the face cell's share of the 9:16 output height, and `screen_x_frac`/`screen_y_frac` are the screen window centre as fractions of the source frame. A non-mapping `split:` value is a user error.
- `video:` and `srt:` must be filled in or the script refuses to run, but the `video:` value is not used for anything. Do not add an `audio:` key; audio is not needed, and a missing audio file triggers an interactive prompt.
- If `scripts/shorts_config.yaml` does not exist, the first run creates a template and exits with code 0 without rendering anything. In interactive mode config validation errors also exit with code 0, so read the output instead of trusting the exit code. Run with `--yes` in an agent context: then a missing or incomplete config, or a missing input file, exits with code 1 instead of prompting.

## Ranked candidates (`shorts_rank.py`)

Instead of writing the segments by hand, `scripts/shorts_rank.py` ranks Shorts candidates from a rough cut or a final edit and writes `shorts_candidates.json`:

```bash
.venv/bin/python scripts/shorts_rank.py edit.json words/ --chapters chapters.json --json
.venv/bin/python scripts/shorts_rank.py --fcproj project.fcproj --sequence Main \
  --words-dir words/ --chapters chapters.json --json
.venv/bin/python scripts/shorts_rank.py --timeline-words edit_words.json --json
```

- Three mutually exclusive input modes: the rough cut (`EDIT` + `WORDS`), the final FilmCraft project (`--fcproj PROJ --sequence NAME --words-dir DIR`, with optional `--track`, default `Audio 1` — the same inputs as `subs_from_edit.py --fcproj`), or the timeline `_words.json` that `subs_from_edit.py` already writes (`--timeline-words FILE`). All three use the same mapping from `scripts/common/timeline_words.py`.
- `edit.json` is the edit list (validated against `schemas/edit.schema.json`); `words/` is a Whisper word JSON or a folder of them (`X.json` or `X_words.json`), the same convention `chapters.py` uses.
- The tool maps the kept words onto the timeline, splits them into sentences on pauses over 0.6 s and builds deterministic candidates: runs of consecutive sentences inside one chapter whose span is 20-60 s. A candidate is standalone: it is never the edit's own opening cut (it does not start at the very beginning) and never a span that lies entirely inside the first 60 s. Material from the opening may appear in a candidate cut differently — it starts later and runs past the 60 s mark to its payoff. One `director` model pass scores every candidate on hook and payoff and writes a `title` (max 60 characters, hook first) and a `description` (2 sentences, no payoff spoilers); the top 5 by `(hook + payoff) / 2` that do not overlap an already accepted candidate by more than 20 % are written to `shorts_candidates.json` (validated against `schemas/shorts_candidates.schema.json`). Skipped candidates are replaced by the next distinct ones, so the list still fills to 5 when the pool allows.
- `--chapters chapters.json` keeps every candidate inside one chapter and fills `chapter_start_s`/`chapter_title`; without it the whole timeline is one chapter and those fields are `null`.
- `--layout {face,split}` is the hard override: it writes that `layout` on every candidate (`face` is the `reframe.py` face crop, `split` stacks the face region over the screen region of the same 16:9 frame) and no signal is computed.
- `--layout-policy {auto,on,off}` decides the `layout` per candidate when `--layout` is not given (default `auto`, also read from the config key `shorts_layout`; the flag beats the config). `auto` writes `split` only when the session has both a camera clip (`camera …`) and a screen clip (`YYYY-MM-DD HH-MM-SS` or a name with `screen`) and a screen clip's timeline span overlaps the candidate, else `face`; `on` writes `split` for every candidate of a camera+screen session; `off` always writes `face`. Camera-only sessions and `--timeline-words` mode (no spans) are always `face`. A value outside `auto`/`on`/`off` (flag or config) is a user error (exit 2, naming the value). Under a policy every written candidate carries a `layout` key.
- `--out` (default `shorts_candidates.json` in the clip's publish folder — the parent of `--words-dir` — in `--fcproj` mode, else next to the primary input: the edit or the `--timeline-words` file) is written atomically; re-running is safe.
- Without `--json` the tool prints the ranked list before the `written:` line, one line per written candidate in rank order: rank, score, timeline range, duration and title (e.g. `1. 82.5  01:12.0-01:48.0 (36.0s) Cum am reparat greșeala din prima zi`).
- `--json` reports `outputs.candidates`, `candidates_scored`, `written`, `dropped_overlap` (scored candidates skipped by the 20 % cap), `dropped_intro` (generated candidates rejected by the standalone/intro rules), `layout` (`null` when `--layout` was not given), `layout_policy` (`auto`/`on`/`off`, always), `warnings`, `wall_time_s`, `llm_cost_usd`. `warnings` is empty in the final-project and timeline-words modes. Exit codes: `0`, `2` user error (missing/invalid edit or words, empty kept transcript, no standalone 20-60 s candidate, a slot whose word times would not map linearly, unparseable or invalid model output after one retry — a missing/invalid score row, a bad reason, a missing/empty/over-long title or description —, score rows for unknown candidate ids are ignored as model noise, LLM budget reached, invalid chapters file, a bad input-mode combination, a bad layout policy).

Then cut the ranked candidates with `--candidates`:

```bash
.venv/bin/python scripts/shorts_generator.py --video /abs/path/Clip.mp4 --srt /abs/path/Clip_RO.srt \
  --candidates shorts_candidates.json --yes
```

- With `--candidates`, the segments come from the file instead of `shorts_config.yaml`'s `segments:` (the tool's own pick): each candidate becomes `short<rank>` with its start/end and its optional `layout` (`face` or `split`). `video:`/`srt:` in the config are still required; `segments:` is not.
- **Caveat:** the candidate times are the timeline seconds of the edit they were ranked on. Run the generator against a video with that same timeline (e.g. the rendered rough cut or the FilmCraft export that kept its timing), otherwise the cuts land on the wrong moments.
- A bad candidates file is exit 1 with `[error] …`.

## Step 2: run

```bash
.venv/bin/python scripts/shorts_generator.py --video /abs/path/Clip.mp4 --srt /abs/path/Clip_RO.srt --yes
```

- Always pass both `--video` and `--srt`. Without `--video` the script asks for the path with `input()`; without `--srt` it looks in the video folder, `../subtitles/` and `..`, and asks interactively if it finds nothing. An interactive prompt hangs a non-interactive agent, so pass `--yes` to turn those prompts into hard errors (exit code 1).
- `--words FILE` burns the real word times of a timeline `_words.json` (the `subs_from_edit.py` output) instead of interpolating inside the `.srt` lines. For each segment it keeps the words whose midpoint lies in `[start, end)`, shifts them to segment time, merges hyphenated fragments and applies the corrections to the text (times unchanged). Without `--words` the SRT interpolation is used.
- `--corrections FILE` is the `wrong|correct` list applied to the `--words` text (default `scripts/corrections.txt`; a missing file falls back to `corrections_example.txt` with a warning). It has no effect on the SRT fallback, which already reads the corrected `.srt`.
- `--brand FILE` is the brand kit (default `brand.json` at the repo root). The caption font and colours come from it; a missing file or font is a hard error.
- `--json` prints a machine-readable summary (one line per run) and, together with `--yes`, makes the run fully non-interactive. The summary lists, per short: name, start, end, `layout` (`face` or `split`), x_offset (null for a tracked crop or a split layout), crop_path (the reframe JSON, null for a fixed `x_offset`), word count, `words_source` (`words.json` or `srt`), output path and the encoder actually used; at the top level `wall_time_s` and `llm_cost_usd` (always 0.0, no model calls).
- `--encoder libx264|h264_nvenc|h264_videotoolbox` forces an encoder; the `YT_VIDEO_ENCODER` environment variable does the same.
- `--skip-alignment` is accepted but does nothing. Do not use it.
- Keep the video path free of `:`, `,` and `'`. The path of the generated subtitle file is passed to ffmpeg inside a `-vf` filter string, so these characters are likely to break it (inferred from the code, not tested).
- Output goes next to the video: `shorts/Short<N>-<name>.mp4` (N is the segment's position in the config) and `auto/<name>_karaoke.ass`. Existing files with the same names are overwritten without asking.

## Step 3: check what it printed, per segment

- `crop path → <name>_reframe.json (N points, moving|fixed)`: the segment had no `x_offset`, so `scripts/reframe.py` tracked the subject (YuNet faces, TrackerMIL between detections, saliency when there is no face) and the crop window follows it. `moving` means the window pans during the Short; `fixed` means the subject stayed put. With no subject at all reframe holds the centre and the Short still renders. Pin a window by setting `x_offset` for that segment: the subject's X position in the 1920-wide frame minus 304.
- `N cuvinte`: the number of subtitle words found for the segment. 0 means no subtitle line falls inside the segment's time range. The Short is rendered with no subtitles. Check the segment times against the `.srt`.
- Confirm each output exists and its duration is close to end minus start: `ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 shorts/Short1-Hook.mp4`.
- You cannot judge framing or subtitle look from the files. Ask the user to watch the Shorts.

## The split layout (`layout: split`)

For a face-over-screen recording (a screen capture with the presenter's face in it) the 9:16 frame stacks two crops of the same 16:9 source instead of cropping around the face:

- the **face cell** (top, `face_cell_frac` of the output height) follows the `reframe.py` path: the face centre of each path point is the centre of the 608x1080 crop window, and the face window is clamped inside the source frame;
- the **screen cell** (bottom, the rest) is a static window centred at `screen_x_frac`/`screen_y_frac` of the source frame;
- both windows keep the cell's aspect (no distortion) and are scaled to 608 wide, then `vstack`ed.

`layout: split` still runs `reframe.py`; when it fails the face cell falls back to a still crop on the source centre (with a warning). `x_offset` is ignored in split layout (with a warning). The output is still 608x1080 and the `--json` summary reports `layout: "split"`.

## How the subtitles behave

- Text comes from the corrected `.srt`, or from the timeline `_words.json` when `--words` is given (with the corrections applied to the word text). Entries that straddle a segment boundary are trimmed at the nearest sentence end, so a Short does not start or end with half a sentence.
- Captions break on phrase boundaries: a word ending in `.`, `!`, `?` or `…` closes the phrase, and a pause over 0.6 s closes it too. Each phrase is packed into lines of at most 18 characters, at most 2 lines per caption; a longer phrase becomes several captions.
- The current word is highlighted in the brand's `colors.warm`; the rest of the caption is `colors.ink`. The font family and size come from `brand.json` (`fonts.caption`), scaled to the 1080-high ASS canvas.
- With `--words` the highlight follows the real word times of the timeline; without it, word timing is an even split inside each `.srt` line, not aligned to the audio, so on lines with long pauses the highlight can drift from the speech.
