---
name: yt-transcript
description: Fix recurring Whisper mistakes in a Romanian .srt using a corrections list, and translate a Romanian .srt to English via OpenRouter. Use when the user asks to correct or clean a transcript or subtitles, apply corrections.txt, or translate / generate English subtitles for a video. Trigger phrases - "corectează transcriptul", "corectează subtitrarea", "traduce subtitrarea", "fă subtitrarea în engleză", "correct the srt", "translate the subtitles". Also offers, only after asking, the deprecated transcript-based metadata generator.
compatibility: Run from the repository root. Needs Python 3, the repo .venv with openai installed, and OPENROUTER_API_KEY for translation.
---

# yt-transcript

Fix and translate subtitle files (.srt) for a YouTube clip. All commands run from the repository root.

## Before you start

- Input: a Romanian .srt, usually raw Whisper output. Transcription itself is not done by these scripts; see README.md, Workflow, step 2.
- Never overwrite the input. Give every step its own output path. If an output file already exists, ask before replacing it.

## Step 1: correct recurring transcription errors

```bash
.venv/bin/python scripts/correct_srt.py raw.srt Clip_RO.srt
```

- Reads `scripts/corrections.txt` (git-ignored, personal). If it is missing, the script prints a warning and falls back to `scripts/corrections_example.txt` (0 active corrections), so it never crashes and always writes the output. Copy the example to `scripts/corrections.txt` to add your own corrections.
- Without the second argument the output is `<input>_corectat.srt`.
- Matching is a plain, case-sensitive text replace over the whole file, timestamps included. Do not add very short patterns.
- A line `*wrong|correct` also fixes the capitalised variant (first letter only): `*pasnicii|paznicii` fixes `Pasnicii` → `Paznicii` too. A pattern that already starts with a capital letter gets no variant. `--capitalised` turns the variant on for every line, `*` or not.
- It prints `Corecturi încărcate: N` and `Termeni înlocuiți: M`. M counts distinct wrong terms that were found, not occurrences. N = 0 means the list is empty; M = 0 with N > 0 means nothing matched.
- `--json` prints one JSON summary (`ok`, `output`, `corrections`, `changes`, `wall_time_s`, `llm_cost_usd`) and moves the human lines to stderr.

## Step 2: human review (do not skip silently)

The corrected .srt is the single source of truth for everything downstream: translation and Shorts subtitles. Ask the user to review it and fix remaining errors directly in the file before translating, and continue only when they confirm. Errors they find repeatedly go into `scripts/corrections.txt` (`wrong|correct`, one per line), so the list grows over time.

## Step 3: translate RO to EN

Needs `OPENROUTER_API_KEY` in the environment for real runs (a missing key prints `[error] ...` and exits 2) and uses a small amount of API credit. Tell the user before running.

```bash
# explicit output path (recommended)
.venv/bin/python scripts/translate_srt.py Clip_RO.srt Clip_EN.srt

# or save next to the video
.venv/bin/python scripts/translate_srt.py Clip_RO.srt /path/to/Clip.mp4
```

- Naming: with a `.mp4` second argument (any letter case) the file is written next to the video as `<input name>_EN.srt`, so `Clip_RO.srt` becomes `Clip_RO_EN.srt`, not `Clip_EN.srt`. The same suffix rule applies when the second argument is omitted. Pass an explicit output path when the exact name matters.
- Batches of 20 blocks. The file is written only after all batches succeed, so an API error leaves no partial file.
- A multi-line block becomes a single line in English. Blocks with no text are dropped.
- The model id comes from the `translate` role in `config.yaml` (`models:`), and the deprecated `analyze_srt.py` from the `analyze` role; a missing role prints `[error] ...` and exits 2; a budget or model error exits 3.
- `--json` prints one JSON summary (`ok`, `output`, `segments`, `wall_time_s`, `llm_cost_usd`) and moves the human lines to stderr.

## Step 4: verify the translation

- Same number of blocks in both files: `grep -c -- '-->' Clip_RO.srt Clip_EN.srt`.
- Untranslated blocks: when the model skips a line, the script silently keeps the Romanian text. Compare the two files and report blocks whose English text is identical to the Romanian, ignoring names and numbers.
- Timestamps must be unchanged.

## Step 5 (optional): metadata from the transcript with analyze_srt.py

Always ask the user first. Never run it on your own initiative. Say something like:

"analyze_srt.py can generate a title, description, chapters and Shorts candidates from the transcript. This use is deprecated (docs/DECISIONS.md, 2026-09-06): it sees only the transcript, not decisions already made about the clip (angle, what was cut, which moments are meant to be Shorts), so its output often needs manual fixes. The alternative is that I write the metadata from the clip's own structure. Which do you prefer?"

Run it only after an explicit yes. It needs `OPENROUTER_API_KEY`, uses API credit, and both paths must be absolute.

```bash
.venv/bin/python scripts/analyze_srt.py /abs/path/Clip_RO.srt /abs/path/Clip.mp4 --select none
```

- Sends the first 80,000 characters of the transcript to the model; the rest is ignored. The output is in Romanian.
- Writes next to the video: `<srt name>_video_metadata.txt` (title, description, chapters, tags) and `<srt name>_shorts_candidates.txt` (ranked candidates with timestamps).
- After listing candidates it asks which ones to cut. Pass `--select 1,3,5|all|none` — use `none` so nothing is cut: the cut option produces square 1080x1080 clips (`shorts/short_NN_*.mp4`), not the 9:16 Shorts made by `scripts/shorts_generator.py`. Without `--select` and without a TTY the run is a usage error (exit 2) naming `--select`; on a TTY it prompts.
- `--json` prints one JSON summary (`ok`, `mode`, `video_metadata`, `shorts_candidates`, `candidates`, `selected`, `wall_time_s`, `llm_cost_usd`) and moves the human lines to stderr.
- The video file must exist, but it is only used for the output folder and for cutting.

Per-short metadata, once `scripts/shorts_config.yaml` has its segments: ask the user the same way, then

```bash
.venv/bin/python scripts/analyze_srt.py /abs/path/Clip_RO.srt /abs/path/Clip.mp4 [youtube_url] --shorts-config scripts/shorts_config.yaml
```

It writes `shorts/<segment name>_metadata.txt` next to the video, one per segment. Without a URL, descriptions contain the placeholder `[LINK VIDEO PRINCIPAL]`; a `youtube_url` key in the config fills it.
