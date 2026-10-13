# CLAUDE.md — yt-pipeline

Context and rules for Claude Code when working on this project.

## What this project does

CLI pipeline for YouTube clip processing: transcription → correction → translation → metadata → 9:16 shorts with karaoke subtitles.

## Key files

| File | Role |
|:---|:---|
| `docs/ROADMAP.md` | Milestones and status |
| `docs/ARCHITECTURE.md` | System design, flow, stack |
| `docs/DECISIONS.md` | Technical decision log — read before proposing alternatives |
| `shorts_generator.py` | Generates 9:16 shorts — main active script |
| `analyze_srt.py` | Main video metadata + per-short metadata (`--shorts-config`) |
| `translate_srt.py` | RO→EN translation — pass `.mp4` as 2nd arg to save in video folder |
| `correct_srt.py` | Find/replace corrections from `corrections.txt` |
| `shorts_config.yaml` | Local config (git-ignored) — segments, optional `x_offset` per segment |
| `kdenlive_from_fragments.py` | Generates a `.kdenlive` rough-cut project from a fragments YAML (raw footage → editable timeline) |
| `srt_from_fragments.py` | Generates a subtitle file retimed to match a rough-cut assembled by the script above |
| `daily_summary_srt.py` | Groups raw `.srt` transcripts in a folder by day (parsed from filename, e.g. `DJI_YYYYMMDDHHMMSS_...`) and generates a full narrative summary per day (DeepSeek) — memory aid, not filtered for editing angle |
| `.claude/skills/` | Agent skills (`yt-transcript`, `yt-shorts`, `yt-rough-cut`) — keep in sync with the scripts, setup in `docs/AGENT_SETUP.md` |
| `docs/VISION.md` | AI Director plan — modes, principles, architecture, phases D0–D6, model routing |
| `docs/research/prior-art.md` | Open-source and market research behind the AI Director plan |
| `docs/research/rendering-and-audio.md` | Render engines, web editor (OpenCut), music/SFX/clean-up without ComfyUI — read before any output, graphics or audio task |
| `docs/guides/user-guide.md` | How to use the AI Director: flow, commands, briefs, how it learns |
| `docs/RISKS.md` | Risk register with mitigations — check before planning a phase |
| `docs/guides/shot-grammar.md` | Filming and editing techniques the tools must detect and the director may use — read before any motion, quality, describe or director task |

## Working rules

1. **Read `docs/DECISIONS.md`** before proposing an alternative approach — it may have already been considered and rejected.
2. **Update `docs/DECISIONS.md`** after any significant architectural decision.
3. **Update `docs/ROADMAP.md`** after completing a milestone or changing priorities.
4. **GitHub commits** → backdate after 18:00, non-round times (e.g. 19:43:17). Repo is public.
5. **Code and documentation** → in English. No inline comments in code (self-documenting names); short docstrings on public functions and modules are allowed — they help the agents that read code they did not write.
6. **`--skip-alignment`** → use only when the JSON is already correct and only ASS + video re-render is needed.
7. **Skills** → when a script's arguments, outputs or behavior change, update the matching skill in `.claude/skills/` in the same change.

## Building the AI Director (phases D0–D5)

Read `docs/VISION.md` before starting any D-phase task. These rules add to the working rules above.

**Roles.** Doru is product owner and reviewer: he steps in when a phase is ~70–80% done, plus one mandatory checkpoint at the end of D1. The agents build end-to-end and verify their own work. Conversation with Doru in Romanian; everything in the repo in English.

**Autonomy.**
- Decide reversible things yourself, record them in `docs/DECISIONS.md` (existing format), move on.
- Stop and ask only for: anything irreversible or expensive (overwriting footage, paid APIs beyond OpenRouter model calls, downloads > 20 GB, changing an interface Doru approved), a pure taste/product choice between equal options, or a phase checkpoint.
- Before any new library, model or format: check current version, maintenance, license and fit; compare at least two options; log the decision.
- Reuse before building. Prefer maintained libraries and the existing scripts (`kdenlive_from_fragments.py` is the optional Kdenlive export adapter, not something new; the edit list is `edit.json`).
- Footage is read-only. Outputs go to the project workspace.
- Mock what you cannot run here (no GPU on the build machine) and say so in the phase report.
- LLM spend: respect the per-run budget cap in config; stop and report when reached.

**Agent team.**
- The architect plans each task and writes a task brief; the coder implements one task at a time; the architect audits the result before marking it done.
- The coder only knows what is in its brief. The architect inlines the relevant rules, schemas and DECISIONS entries into every brief.
- Tasks touching more than 3 tightly coupled files, or any schema change, stay with the architect.
- Do not switch coder models in the middle of a task.

**Every new tool** is a standalone script in `scripts/` plus a skill in `.claude/skills/`, with `--help`, `--json` and re-run safety. No tool imports another tool; shared code only in `scripts/common/`.

**Definition of done (every task):** tests pass; ran end-to-end on sample media and, for anything visual, looked at output frames; outputs validated against `schemas/`; no source file modified; DECISIONS entry if something new was chosen; skill and README updated if behavior changed.

**Phase report** in `docs/reports/D<n>-REPORT.md`, three short lists: *Works* (what was verified, how), *Mocked / not verified*, *Doru, check this* (max 5 bullets, each with a path or command). Send a short notification (max 5 lines) when a phase report is ready or when blocked.

## Do not

- Re-install MediaPipe — OpenCV was chosen intentionally (see docs/DECISIONS.md).
- Run Whisper separately for word timestamps — WhisperX forced alignment is the source of truth.

## File structure

```
Export/
  video/      ← .mp4 input
  audio/      ← .mp3 input
  subtitles/  ← SRT files (raw, corrected RO, translated EN)
  auto/       ← _words.json, _karaoke.ass (auto-generated)
  shorts/     ← final output (generated next to video/)
```

## How to run

```bash
cd ~/Proiecte-AI/YouTube/yt-pipeline

# Generate shorts (reads scripts/shorts_config.yaml automatically)
.venv/bin/python scripts/shorts_generator.py --video ~/Videos/[Clip]/Export/video/[Clip].mp4

# Re-render only (skip WhisperX, reuse existing words JSON)
.venv/bin/python scripts/shorts_generator.py --video ... --skip-alignment

# Main video metadata
python scripts/analyze_srt.py ~/Videos/[Clip]/Export/subtitles/[Clip]_RO.srt ~/Videos/[Clip]/Export/video/[Clip].mp4

# Per-short metadata (after generating shorts)
python scripts/analyze_srt.py ~/Videos/[Clip]/Export/subtitles/[Clip]_RO.srt ~/Videos/[Clip]/Export/video/[Clip].mp4 --shorts-config scripts/shorts_config.yaml

# Translate SRT (saves next to video)
python scripts/translate_srt.py ~/Videos/[Clip]/Export/subtitles/[Clip]_RO.srt ~/Videos/[Clip]/Export/video/[Clip].mp4

# Rough-cut assembly from raw footage (pre-editing, see README.md)
python3 scripts/kdenlive_from_fragments.py fragments.yaml
python3 scripts/srt_from_fragments.py fragments.yaml transcripts_raw/ output.srt

# Daily narrative summary from raw transcripts (memory aid, not editing-angle-filtered)
python3 scripts/daily_summary_srt.py transcripts_raw/ rezumat_pe_zile.md
```

## shorts_config.yaml format

```yaml
video: "MyClip.mp4"
audio: "MyClip.mp3"
srt:   "MyClip_RO.srt"

segments:
  - name: "Hook"
    start: "00:00:00"
    end: "00:00:54"
    # no x_offset → auto face detection

  - name: "Piesa"
    start: "00:05:06"
    end: "00:05:46"
    x_offset: 800   # manual override, skips face detection
```
