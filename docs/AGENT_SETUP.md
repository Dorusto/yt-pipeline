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

Shorts need a real 1920x1080 video and a matching subtitle file. An NVIDIA GPU is optional: the render uses `h264_nvenc` when a GPU is present and falls back to `libx264` otherwise. The rough-cut scripts need real footage with per-file `.srt` transcripts.

The AI Director foundation (phase D0) has an offline suite that runs on a CPU-only machine (no GPU, no API key):

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass, including notifications, encoder selection, schema validation and the rough-cut retiming.

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

## 8. opencode agents

Two project agents ship in `.opencode/agents/`, used to build the AI Director (see `docs/VISION.md`), not to run the pipeline:

| Agent | Mode | Model (OpenRouter) | Role |
|:---|:---|:---|:---|
| `architect` | primary (Tab to select) | `xiaomi/mimo-v2.6-pro` | plans a phase, writes task briefs to `.opencode/tasks/`, delegates, audits, commits on phase branches |
| `coder` | subagent | `deepseek/deepseek-v4.1-flash` | implements one brief, never commits |

`opencode.json` in the repo root registers the three OpenRouter models and the shared permissions. Loop guards (D2-16): `steps: 150` for the architect and `steps: 80` for the coder (at the limit opencode makes the agent stop and summarise what is left), `doom_loop: deny` (an unattended run cannot answer an "agent seems stuck" prompt, so the repeat is refused instead of hanging), and the watchdog's hard stops below. To swap a model, change the `model:` line in the agent file. `z-ai/glm-5.3-flash` is the fallback coder.

To run the pipeline itself, the default `build` agent is enough: it already loads the skills.

## 9. Files that stay local

These are git-ignored and never appear on a fresh clone: `scripts/corrections.txt` (copy it from `scripts/corrections_example.txt`), `scripts/shorts_config.yaml` (created from a template on the first Shorts run), `.env`, and media and generated files (`*.mp4`, `*.mp3`, `*.wav`, `*.json`, `*.ass`).

## 10. Keeping skills in sync

When a script's arguments, outputs or behavior change, update the matching skill in `.claude/skills/` in the same change. See `CLAUDE.md`, Working rules.

## 10b. Public-repo guard

The repo is public. Once per clone run `git config core.hooksPath .githooks`: every commit then runs `scripts/check_public.py`, which refuses footage, images, subtitles, model weights, files over 1 MB, API keys and embedded base64 images. `python3 scripts/check_public.py --all` scans everything tracked.

## 11. Notifications (ntfy)

Agents (and humans) can post a short status message to the project's ntfy topic when a phase report is ready or when blocked.

```bash
.venv/bin/python scripts/notify.py "D0 report ready: docs/reports/D0-REPORT.md"
echo "blocked on the vision model" | .venv/bin/python scripts/notify.py - --title Blocked
.venv/bin/python scripts/notify.py "test" --dry-run --json
```

The server URL comes from `NTFY_URL` or the config key `ntfy_url`; there is no built-in default, so set one of them (a private address stays out of this public repository — the docs use `<ntfy-url>`). `--json` prints a machine-readable result; `--dry-run` resolves the target without sending.

### Watchdog

`scripts/agent_watchdog.py` alerts only when something is wrong: today's OpenRouter spend above a threshold, or an `opencode` process running with no file changes for hours. Run it from a separate checkout of `master`, so it never touches the agent's working tree, and schedule it with cron:

```bash
git -C ~/projects/yt-pipeline worktree add --detach ~/yt-watchdog origin/master
mkdir -p ~/.cache/yt-pipeline
( crontab -l 2>/dev/null; echo '*/15 * * * * cd ~/yt-watchdog/scripts && python3 agent_watchdog.py --repo ~/projects/yt-pipeline --run-cap 6 --lot "S59" --kill-over-usd 11 --kill-no-commit-hours 1.5 --spend-usd 10 >> ~/.cache/yt-pipeline/watchdog.log 2>&1' ) | crontab -
```

`--spend-usd X` is the daily spend threshold for the alert (default 3); `--stall-hours H` is the stall alert after H hours with an `opencode` process running and no file change (default 2); `--no-commit-hours H` is the alert after H hours with no commit (default 1.5, measured from the later of the last commit and the agent start, so a fresh run is not flagged); `--kill-over-usd X` stops every `opencode` process when today's OpenRouter spend passes X dollars; `--kill-no-commit-hours H` stops it after H hours without a commit; `--run-cap X --lot NAME` is the hard cap for the current agent run (spend since the run started, from the run-spend ledger) with an ntfy `Budget` at 80 % of the cap and a stop at the cap with `Blocked` — update the `--lot` label when a new run starts. `--lot NAME` prefixes every alert with the lot name; without it the watchdog uses the tmux session running the agent, else no prefix. A stop is claimed only after the process is re-checked, so the alert says "Stopped the agent" only when it really ended. The kill flags and the run cap are off unless given. The agent's uncommitted changes stay in the working tree; save them on a branch before starting another run. These are the only watchdog thresholds; `docs/RULES.md` points here.

Update it later with `git -C ~/yt-watchdog checkout --detach origin/master` after a `git fetch`.

## Troubleshooting

- `KeyError: 'OPENROUTER_API_KEY'`: the variable is not set in the shell that started the agent.
- `FileNotFoundError` for `corrections.txt`: copy `scripts/corrections_example.txt` to `scripts/corrections.txt`.
- The agent does not list the skills: it was started outside the repo. Restart it in the repo root.
