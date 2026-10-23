# AGENTS.md — instructions for every coding agent

yt-pipeline turns raw clips and photos into a finished video: transcription,
correction, translation, metadata, 9:16 Shorts and the AI Director
(ingest → motion → quality → describe → index → director → render). Doru is the
product owner and reviewer; talk to him in Romanian, write everything in the repo
in English.

## Read first

- [`docs/RULES.md`](docs/RULES.md) — every rule in force, with the `DECISIONS.md`
  date it comes from. Read it plus the last 10 `docs/DECISIONS.md` entries before
  any work; open the full log only when the history of a decision matters.
- [`docs/VISION.md`](docs/VISION.md) — the AI Director plan (modes, principles,
  phases D0–D6, model routing).
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — milestones and the current phase status.
- [`.claude/skills/`](.claude/skills/) — one skill per tool. Read the skill before
  trusting a README claim; where they disagree, the skill follows the code.

## Topic rules

- [`docs/agents/code-style.md`](docs/agents/code-style.md) — how code is written here.
- [`docs/agents/testing.md`](docs/agents/testing.md) — how tests are written and run.
- [`docs/agents/conventions.md`](docs/agents/conventions.md) — branches, commits, docs and files.

## Key files

| File | Role |
|:---|:---|
| `docs/ROADMAP.md` | Milestones and status |
| `docs/ARCHITECTURE.md` | System design, flow, stack |
| `docs/DECISIONS.md` | Technical decision log — read before proposing alternatives |
| `docs/RULES.md` | Rules in force |
| `docs/RELEASES.md` | Released versions and the stable files |
| `docs/RELEASING.md` | How a version is made |
| `docs/guides/tools.md` | Tool-by-tool reference |
| `docs/guides/user-guide.md` | How to use the AI Director |
| `docs/AGENT_SETUP.md` | Agent setup, skills, watchdog |

Claude Code reads [`CLAUDE.md`](CLAUDE.md), which points here.
