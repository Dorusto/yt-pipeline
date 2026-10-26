# Agent setup

How to drive this pipeline from natural language with Claude Code, opencode or any
tool that reads the Agent Skills format, instead of remembering script flags. The
repo ships 35 skills in `.claude/skills/`, grouped into five plugins in one
marketplace, `yt-pipeline`. Every skill calls the tools through the `yt` entry
point, so a skill works on its own once `yt` is on `PATH`.

## 1. What you get

The 35 skills are grouped into five plugins, each installable alone:

| Plugin | Skills | What it covers |
|:---|:---|:---|
| `yt-director` | 19 | the recap flow: ingest, motion, quality, describe, similarity, index, router, director, llm_director, render, thumbs, explain, beats, music_pick, edit_drop, eval_edit, mode_b, rough_cut, notebook |
| `yt-publish` | 5 | transcript, publish, recap_meta, thumbnail, audio_clean |
| `yt-shorts` | 2 | shorts, reframe |
| `yt-editor` | 6 | filmcraft, export_kdenlive, finish_pip, graphics, matte, motion |
| `yt-admin` | 3 | costs, watchdog, eval |

The skills describe what the tools actually do, including their gotchas. Read the
skill before trusting a README claim; where they disagree, the skill follows the
code. The full table, with the related plugin each skill names, is in section 9.

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

## 5. The `yt` entry point

`bin/yt` puts `yt` on `PATH`. `yt <tool> [args]` runs any tool from any folder, for
example:

```bash
yt doctor --source <folder>
yt recap --source <folder> --brief <brief>
```

`yt --help` lists the tools; `yt --json` and `yt --help --json` print the same
JSON (the resolved root, Python and tool list). The entry point resolves the
repository root from `YT_PIPELINE_HOME` (else by walking up from the script to
the folder holding `scripts/` and `requirements.txt`) and the interpreter for
the tools from `YT_PIPELINE_VENV`, else `<root>/.venv/bin/python`, else the
plugin's own virtual environment under `~/.claude/plugins/data/`, else `python3`.
`bin/yt` is a thin launcher that finds a Python and runs `scripts/yt.py`.

Put `bin/yt` on `PATH` once, for example with a symlink:

```bash
mkdir -p ~/.local/bin
ln -s "$(pwd)/bin/yt" ~/.local/bin/yt
```

Make sure `~/.local/bin` is on `PATH`. From then on `yt <tool>` works from any
folder.

## 6. Claude Code plugin

The repository is a Claude Code marketplace, `yt-pipeline`, with five plugins.
Add the marketplace once, from any folder:

```bash
claude plugin marketplace add Dorusto/yt-pipeline
```

Then install one plugin alone, for example the director:

```bash
claude plugin install yt-director@yt-pipeline
```

The skills then load from any folder as `/yt-director:yt-recap`,
`/yt-director:yt-director` and so on, and `yt` is on the Bash tool's `PATH`. On
the first session a `SessionStart` hook creates the plugin's own virtual
environment from `requirements.txt` under `~/.claude/plugins/data/`; later
sessions reuse it. Install the other plugins the same way
(`yt-publish@yt-pipeline`, `yt-shorts@yt-pipeline`, `yt-editor@yt-pipeline`,
`yt-admin@yt-pipeline`).

### Per-skill install

A skill also works on its own, without the plugin: copy one skill folder into
`~/.claude/skills/` and it is discovered from any folder.

```bash
cp -r .claude/skills/yt-recap ~/.claude/skills/
```

It works alone because it calls the tools only through `yt <tool>`, so it needs
the `yt` entry point on `PATH` (section 5) and nothing else.

`claude plugin validate .` checks the marketplace file and every entry. This is a
manual step for the owner: the build host has no `claude` CLI.

## Typed tools (MCP)

Each plugin ships a small MCP server, `scripts/yt_mcp.py`, declared in its
marketplace entry and started with the plugin's own `--plugin` value, so it only
sees that plugin's tools. The server runs over stdio and exposes four typed
tools: `yt_list`, `yt_describe`, `yt_run` and `yt_preview`. The plugin → tools
grouping comes from the marketplace and the skills, so a new tool appears without
a code change.

- `yt_list` → the tools of the plugin, one line each.
- `yt_describe` with `{"tool": "render"}` → the tool's `--help` text, whether
  it supports `--json`, and its `--json` output contract (the example object and
  exit codes, read from the tool's skill; `null` when the skill documents none).
- `yt_run` with `{"tool": "doctor", "args": ["--source", "<folder>"]}` → the
  tool's `--json` object, or the structured `{"ok": false, ...}` error.
- `yt_preview` with `{"path": "<workspace>"}` → the poster or a frame as an
  image.

## 7. opencode

Run `opencode` in the repo root. It reads `.claude/skills/<name>/SKILL.md`
(project Claude-compatible) and `~/.claude/skills/<name>/SKILL.md` (global
Claude-compatible), and `AGENTS.md` as project instructions; `CLAUDE.md` points
to `AGENTS.md` for Claude Code. Whether opencode also reads the Claude Code
plugin cache is not verified on this host (no agent-skills plugin is installed),
so this guide makes no claim either way; install the skills for opencode by
copying them into `.claude/skills/` or `~/.claude/skills/` (section 8). See the
opencode skills doc: https://opencode.ai/docs/skills/.

Ask the agent to list its skills. You should see all 35.

## 8. Other Agent Skills tools

Any tool that reads the Agent Skills format works the same way: copy a skill
folder into the tool's skills directory.

```bash
cp -r .claude/skills/yt-recap <tool-skills-dir>/
```

The skill calls `yt <tool>` and needs the `yt` entry point on `PATH` (section 5).
Each `SKILL.md` has `name` and `description` frontmatter; `name` matches the
folder name and is lowercase alphanumeric with single hyphens.

## 9. Skills by plugin

One row per plugin. The "Related plugin" column names the other plugin a skill in
this plugin points to (a "see also" or prerequisite pointer, not a tool call —
every skill calls tools through `yt <tool>`).

| Plugin | Skills | Related plugin |
|:---|:---|:---|
| `yt-director` | `yt-recap`, `yt-director`, `yt-llm-director`, `yt-beats`, `yt-music-pick`, `yt-edit-drop`, `yt-render`, `yt-thumbs`, `yt-explain`, `yt-ingest`, `yt-index`, `yt-describe`, `yt-quality`, `yt-similarity`, `yt-eval-edit`, `yt-mode-b`, `yt-rough-cut`, `yt-router`, `yt-notebook` | `yt-editor` (`yt-render` → `yt-motion`), `yt-publish` (`yt-recap` → `yt-transcript`), `yt-admin` (`yt-eval-edit` → `yt-eval`) |
| `yt-publish` | `yt-transcript`, `yt-publish`, `yt-recap-meta`, `yt-thumbnail`, `yt-audio-clean` | `yt-director` (`yt-thumbnail` → `yt-render`), `yt-shorts` (`yt-thumbnail` → `yt-shorts`) |
| `yt-shorts` | `yt-shorts`, `yt-reframe` | `yt-publish` (`yt-shorts` → `yt-transcript`) |
| `yt-editor` | `yt-filmcraft`, `yt-export-kdenlive`, `yt-finish-pip`, `yt-graphics`, `yt-matte`, `yt-motion` | `yt-director` (`yt-export-kdenlive` → `yt-ingest`, `yt-index`, `yt-director`) |
| `yt-admin` | `yt-costs`, `yt-watchdog`, `yt-eval` | — |

No plugin ships another's skills and no `dependencies` field is set: the pointers
are "see also" notes, and a dependency would create the cycles `yt-director` ↔
`yt-editor` and `yt-publish` ↔ `yt-shorts`.

Tool-level dependencies are separate from the skill pointers: a skill can call a
tool whose own skill lives in another plugin, for example `yt-render` (plugin
`yt-director`) runs `render.py`, which calls `reframe.py`, whose skill
`yt-reframe` is in `yt-shorts`. Every plugin ships the whole `scripts/` folder,
so the tools are present even when the other plugin is not installed; the pointer
only tells the reader where the tool's skill is.

## 10. Smoke test

This checks the setup end to end without any video or GPU. It uses
`examples/sample_RO.srt`, a ten-line fake subtitle file that contains the word
"chezve" twice.

```bash
cp scripts/corrections_example.txt scripts/corrections.txt
echo "chezve|cezve" >> scripts/corrections.txt
yt correct_srt examples/sample_RO.srt /tmp/sample_RO_fixed.srt
```

On a fresh clone without `yt` on `PATH`, use the interpreter directly:

```bash
.venv/bin/python scripts/correct_srt.py examples/sample_RO.srt /tmp/sample_RO_fixed.srt
```

Expected: `Corecturi încărcate: 1`, `Termeni înlocuiți: 1`, and "cezve" in the output file. The second number counts distinct terms found, not occurrences.

```bash
yt translate_srt /tmp/sample_RO_fixed.srt /tmp/sample_EN.srt
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

## 11. Fresh-container smoke test

A repeatable check that the plugin installs in a fresh container with no personal
setup and turns a folder of clips into a recap. The build host has no container
runtime, so run this by hand in a fresh container. `deploy/smoke_plugin.sh`
automates steps 2-5 below; the checklist is the manual version.

1. Start a fresh container with no personal setup: no `~/.claude`, no
   `YT_PIPELINE_*` variables and no config file. Expected: a clean shell where
   `yt` is not found and no yt-pipeline config exists.
2. Install from the install steps (section 3): clone, `uv venv`,
   `uv pip install -r requirements.txt`. Expected: the install finishes with no
   error.
3. Put `yt` on `PATH` (section 5). Expected: `yt --help` lists the tools.
4. Install the plugin (section 6). Expected: `claude plugin validate .` passes
   and the skills load as `/yt-director:yt-recap` and so on.
5. Run the script:
   `deploy/smoke_plugin.sh --source <clips-folder> --brief "<brief>"`.
   Expected: the recap MP4 is written under the workspace and the script prints
   its path.
6. Confirm no personal setup was needed: every setting came from a flag, an
   environment variable or the config file.

The script clones the repository, builds the virtual environment, links `yt`
into `~/.local/bin`, checks the entry point, installs the plugin when `claude` is
on `PATH` and runs `yt recap`. `--dry-run` prints the steps and changes nothing;
`--skip-plugin` tests the `yt` entry point alone. Exit codes: `0` success, `2` a
usage error (a bad flag or a missing `--source`/`--brief`), `3` a step failed
after work started.

## 12. opencode build agents

Four project agents ship in `.opencode/agents/`, used to build the AI Director
(see `docs/VISION.md`), not to run the pipeline. Every agent runs on the same
model, `deepseek/deepseek-v4.1-flash`; the reasoning level is set per agent with
a `reasoning:` block in the agent file (`effort: max` for `architect`, `coder`
and `coder-max` and `high` for `specwriter`).

| Agent | Mode | Model (OpenRouter) | Reasoning | Role |
|:---|:---|:---|:---|:---|
| `architect` | primary (Tab to select) | `deepseek/deepseek-v4.1-flash` | `max` | plans a phase, writes task briefs to `.opencode/tasks/`, delegates, audits, commits on phase branches |
| `coder` | subagent | `deepseek/deepseek-v4.1-flash` | `max` | implements one brief, never commits |
| `coder-max` | subagent | `deepseek/deepseek-v4.1-flash` | `max` | implements one brief for a hard lot, with the deepest reasoning; slower and costlier than `coder` |
| `specwriter` | primary | `deepseek/deepseek-v4.1-flash` | `high` | writes and maintains `docs/SPEC.md`, never code |

`opencode.json` in the repo root registers the OpenRouter model and the shared
permissions. The architect hands a brief to `coder-max` only when the lot says
"hard"; every other lot goes to `coder`. Loop guards (D2-16): `steps: 150` for
the architect and `steps: 80` for the coder (at the limit opencode makes the
agent stop and summarise what is left), `doom_loop: deny` (an unattended run
cannot answer an "agent seems stuck" prompt, so the repeat is refused instead of
hanging), and the watchdog's hard stops below. To change the reasoning level,
edit the `reasoning:` block in the agent file. Host-specific folders (for example
a media or cache directory under the agent's home) go in the global opencode
config (`~/.config/opencode/opencode.json`), never in the tracked one.

To run the pipeline itself, the default `build` agent is enough: it already
loads the skills.

## 13. Files that stay local

These are git-ignored and never appear on a fresh clone: `scripts/corrections.txt` (copy it from `scripts/corrections_example.txt`), `scripts/shorts_config.yaml` (created from a template on the first Shorts run), `.env`, and media and generated files (`*.mp4`, `*.mp3`, `*.wav`, `*.json`, `*.ass`).

## 14. Keeping skills in sync

When a script's arguments, outputs or behavior change, update the matching skill in `.claude/skills/` in the same change. See `AGENTS.md`, Working rules.

## 15. Public-repo guard

The repo is public. Once per clone run `git config core.hooksPath .githooks`: every commit then runs `scripts/check_public.py`, which refuses footage, images, subtitles, model weights, files over 1 MB, API keys and embedded base64 images. `python3 scripts/check_public.py --all` scans everything tracked.

## 16. Notifications (ntfy)

Agents (and humans) can post a short status message to the project's ntfy topic when a phase report is ready or when blocked.

```bash
yt notify "D0 report ready: docs/reports/D0-REPORT.md"
echo "blocked on the vision model" | yt notify - --title Blocked
yt notify "test" --dry-run --json
```

The server URL comes from `NTFY_URL` or the config key `ntfy_url`; there is no built-in default, so set one of them (a private address stays out of this public repository — the docs use `<ntfy-url>`). `--json` prints a machine-readable result (`sent`, `url`, `wall_time_s`, `llm_cost_usd`, and `error` or `status`); `--dry-run` resolves the target without sending.

### Watchdog

`scripts/agent_watchdog.py` alerts only when something is wrong: today's OpenRouter spend above a threshold, or an `opencode` process running with no file changes for hours. Run it from a separate checkout of `master`, so it never touches the agent's working tree, and schedule it with cron:

```bash
git -C <repo> worktree add --detach <watchdog-checkout> origin/master
mkdir -p ~/.cache/yt-pipeline
( crontab -l 2>/dev/null; echo '*/15 * * * * cd <watchdog-checkout>/scripts && python3 agent_watchdog.py --repo <repo> --kill-over-usd 11 --kill-no-commit-hours 1.5 --spend-usd 10 >> ~/.cache/yt-pipeline/watchdog.log 2>&1' ) | crontab -
```

`--spend-usd X` is the daily spend threshold for the alert (default 3); `--stall-hours H` is the stall alert after H hours with an `opencode` process running and no file change (default 2); `--no-commit-hours H` is the alert after H hours with no commit (default 1.5, measured from the later of the last commit and the agent start, so a fresh run is not flagged); `--kill-over-usd X` stops every `opencode` process when today's OpenRouter spend passes X dollars (the single hard daily ceiling); `--kill-no-commit-hours H` stops it after H hours without a commit. The label is derived from the newest task brief in `<repo>/.opencode/tasks/` (review and audit notes skipped), else the tmux session running the agent, else no prefix — no per-lot flag. The run-spend ledger (`~/.cache/yt-pipeline/run-spend.json`) still measures the current lot's spend, which the stop notification reports; it is no longer a stop threshold. A stop is claimed only after the process is re-checked, so the alert says "Stopped the agent" only when it really ended. The two kill flags are off unless given. The agent's uncommitted changes stay in the working tree; save them on a branch before starting another run. These are the only watchdog thresholds; `docs/RULES.md` points here.

Update it later with `git -C <watchdog-checkout> checkout --detach origin/master` after a `git fetch`.

## Troubleshooting

- `KeyError: 'OPENROUTER_API_KEY'`: the variable is not set in the shell that started the agent.
- `FileNotFoundError` for `corrections.txt`: copy `scripts/corrections_example.txt` to `scripts/corrections.txt`.
- The agent does not list the skills: it was started outside the repo. Restart it in the repo root.
- `yt: command not found`: `bin/yt` is not on `PATH`. Add the symlink from section 5, or run the tool with `.venv/bin/python scripts/<tool>.py`.
