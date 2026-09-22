# Agent setup

How to drive this pipeline from natural language with Claude Code or opencode, instead of remembering script flags. The repo ships three skills in `.claude/skills/`; both tools read that folder.

## 1. What you get

| Skill | Say something like | Scripts behind it |
|:---|:---|:---|
| `yt-transcript` | "correct the transcript", "translate the subtitles to English" | `correct_srt.py`, `translate_srt.py` (optionally `analyze_srt.py`, only after asking) |
| `yt-shorts` | "generate Shorts from this video" | `shorts_generator.py` |
| `yt-rough-cut` | "build a rough cut from these fragments", "summarise the transcripts per day" | `kdenlive_from_fragments.py`, `srt_from_fragments.py`, `daily_summary_srt.py` |

The skills describe what the scripts actually do, including their gotchas. Read the skill before trusting a README claim; where they disagree, the skill follows the code.

## 2. System requirements

- An NVIDIA GPU and an ffmpeg build with `h264_nvenc` (Shorts rendering). Check with `ffmpeg -hide_banner -encoders | grep nvenc`.
- `ffprobe` (ships with ffmpeg) and `melt` from the MLT framework (rough-cut generation). Check with `command -v ffprobe melt`.
- `uv` for the Python environment.
- `openai-whisper` installed with pipx, only if you also transcribe from this repo (see README, Workflow, step 2).
- Kdenlive 23.04 or newer, only to open generated rough-cut projects.
- An OpenRouter account and API key for translation, day summaries and the optional metadata generator.

## 3. Install

```bash
git clone https://github.com/Dorusto/yt-pipeline.git
cd yt-pipeline
uv venv
uv pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu124
```

## 4. API key

The scripts read `OPENROUTER_API_KEY` from the environment only. A `.env` file is not loaded automatically.

```bash
export OPENROUTER_API_KEY=your-key-here
```

Put it in your shell profile. Never commit it.

## 5. Start your agent

Skills are discovered from the folder you start the agent in, upward to the repository root. Start it inside the repo.

- **Claude Code:** run `claude` in the repo root. To use it from another folder, start with `claude --add-dir /path/to/yt-pipeline`.
- **opencode:** run `opencode` in the repo root. It reads `.claude/skills/` and, when no `AGENTS.md` exists, `CLAUDE.md` as project instructions.

Ask the agent to list its skills. You should see `yt-transcript`, `yt-shorts` and `yt-rough-cut`.

## 6. Smoke test

This checks the setup end to end without any video or GPU. It uses `examples/sample_RO.srt`, a ten-line fake subtitle file that contains the word "chezve" twice.

```bash
cp scripts/corrections_example.txt scripts/corrections.txt
echo "chezve|cezve" >> scripts/corrections.txt
.venv/bin/python scripts/correct_srt.py examples/sample_RO.srt /tmp/sample_RO_fixed.srt
```

Expected: `Corecturi încărcate: 1`, `Termeni înlocuiți: 1`, and "cezve" in the output file. The second number counts distinct terms found, not occurrences.

```bash
.venv/bin/python scripts/translate_srt.py /tmp/sample_RO_fixed.srt /tmp/sample_EN.srt
grep -c -- '-->' /tmp/sample_RO_fixed.srt /tmp/sample_EN.srt
```

Expected: `Segmente găsite: 10`, then 10 in both counts. Translation uses a few cents of API credit.

To test the skills themselves, ask the agent in plain words: "correct and translate examples/sample_RO.srt". It should load `yt-transcript` and run the same steps.

Shorts need a real 1920x1080 video, an NVIDIA GPU and a matching subtitle file. The rough-cut scripts need real footage with per-file `.srt` transcripts. Neither has an offline smoke test yet.

## 7. Typical session

Two common paths. In both, you make the creative decisions and the agent runs the scripts and checks the results.

### A. A finished, exported clip: subtitles and Shorts

1. **Correct the transcript.** Say: "Correct the transcript in /path/raw.srt and save it as Clip_RO.srt." The agent copies `scripts/corrections_example.txt` to `scripts/corrections.txt` if the file is missing, runs the correction and reports how many terms it replaced.
2. **Review.** The agent stops and asks you to review the corrected file. Fix the remaining errors yourself, then tell it to continue. Errors you keep finding go into `scripts/corrections.txt`.
3. **Translate.** Say: "Translate Clip_RO.srt to English." The agent tells you before running that this uses API credit. Afterwards it checks that both files have the same number of blocks and reports lines that were left in Romanian.
4. **Shorts.** Say: "Generate Shorts from /path/Clip.mp4." The agent asks you for the segments (a name, a start and an end for each). It writes the config, runs the generator with the video and subtitle paths, then reports, per segment, how many sampled frames had a face and how many subtitle words were found. It asks you to watch the results.
5. **Metadata, optional.** The agent may offer to generate a title, description and chapters from the transcript with `analyze_srt.py`. It explains that this method is deprecated and what the alternative is, and runs it only if you say yes.

### B. Raw footage: rough cut

1. **Summary per day, optional.** Say: "Summarise the raw transcripts per day." The agent warns you about the summary's limits (Romanian motorcycle-trip prompt, first 80,000 characters per day) before you decide.
2. **You choose the moments.** Tell the agent which quotes go in and in what order. It finds the timecodes in the raw `.srt` files and asks whether you want padding around each one.
3. **Fragment list.** The agent writes `fragments.yaml` with the timecodes in the exact format the scripts need.
4. **Kdenlive project.** Say: "Generate the Kdenlive project." If the output file already exists, the agent asks whether you edited it by hand since the last generation. The script keeps a timestamped backup of the previous version.
5. **Retimed subtitles.** Say: "Generate the retimed subtitles." The agent asks you to check the last fragments after import, because the timing of long fragment lists is not verified.
6. **Import.** You import the subtitle file in Kdenlive (Project, Subtitles, Import Subtitle File). It cannot be done from the command line.

### What always stays with you

- Choosing the segments and the fragments.
- Reviewing the corrected subtitles.
- Watching the Shorts and the rough cut.
- Approving anything that spends API credit or overwrites a file.

## 8. Optional: a dedicated opencode agent

Not shipped. The default opencode agent already loads the skills. A project agent only presets a model and permissions. To make one, create `.opencode/agents/yt-pipeline.md`:

```markdown
---
description: Runs the yt-pipeline skills on a video project
mode: primary
model: provider/model-id
---
Use the yt-transcript, yt-shorts and yt-rough-cut skills. Ask before running anything that spends API credit or overwrites files.
```

Replace `provider/model-id` with a model you have access to.

## 9. Files that stay local

These are git-ignored and never appear on a fresh clone: `scripts/corrections.txt` (copy it from `scripts/corrections_example.txt`), `scripts/shorts_config.yaml` (created from a template on the first Shorts run), `.env`, and media and generated files (`*.mp4`, `*.mp3`, `*.wav`, `*.json`, `*.ass`).

## 10. Keeping skills in sync

When a script's arguments, outputs or behavior change, update the matching skill in `.claude/skills/` in the same change. See `CLAUDE.md`, Working rules.

## Troubleshooting

- `KeyError: 'OPENROUTER_API_KEY'`: the variable is not set in the shell that started the agent.
- `FileNotFoundError` for `corrections.txt`: copy `scripts/corrections_example.txt` to `scripts/corrections.txt`.
- The agent does not list the skills: it was started outside the repo. Restart it in the repo root.
