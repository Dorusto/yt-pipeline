---
title: yt-pipeline AI Director — user guide
date: 2026-10-14
---

# Using the AI Director

You give it a folder of raw clips and a brief in plain words. It analyses the footage once, picks and cuts the shots, and renders a video you can watch on the phone, open in Kdenlive or render at full quality. Every step is a separate command, so a human or an agent can run, inspect or re-run any one of them.

## Quickstart

```bash
git clone https://github.com/Dorusto/yt-pipeline && cd yt-pipeline
uv venv && uv pip install -r requirements.txt
export OPENROUTER_API_KEY=sk-or-v1-...            # key from https://openrouter.ai/keys
.venv/bin/python scripts/doctor.py --source ~/media/trip/raw
.venv/bin/python scripts/recap.py --source ~/media/trip/raw --workspace ~/media/trip/workspace \
    --brief "90 s, chronological, calm start, ambient sound only"
# -> ~/media/trip/workspace/recap_720p.mp4 (plus edit.json, shotlog.html and explain.html)
# full quality from the originals + a Kdenlive project:
.venv/bin/python scripts/recap.py --source ~/media/trip/raw --workspace ~/media/trip/workspace --final --brief "90 s, chronological, calm start, ambient sound only"
# with music (auto picks a track from ~/media/music by the brief and the shot tags):
.venv/bin/python scripts/recap.py --source ~/media/trip/raw --brief "90 s, chronological" --music auto
# re-running skips finished stations; a new brief only redoes the edit
# the web app for the phone:
.venv/bin/python scripts/serve.py --workspaces ~/media/projects --inbox ~/media/inbox
```

The long station-by-station section below is what `recap.py` chains for you.

## The picture

```mermaid
flowchart LR
  subgraph you[You]
    F[Folder of clips<br/>SD card / phone]
    B["Brief<br/>'90 s, chronological,<br/>calm start, ambient sound'"]
    R[Watch, approve,<br/>trim, correct]
  end
  subgraph analyse[Analyse once — cached]
    I[ingest] --> M[motion] --> Q[quality] --> D[describe<br/>vision model] --> S[similarity] --> X[index<br/>shotlog + contact sheet]
  end
  subgraph edit[Edit — as many times as you like]
    RT[router] --> DIR[director<br/>screenwriter → editor → reviewer] --> E[(edit.json)]
  end
  subgraph out[Output]
    P[720p preview] 
    FIN[full-res final]
    K[Kdenlive project]
  end
  F --> I
  B --> RT
  X --> DIR
  E --> P & FIN & K
  P --> R
  R -. corrections .-> X
```

Analysis runs once per folder and is cached; a new brief only re-runs the right-hand side, which takes about a minute and a few cents.

## Today (command line on the homelab)

Run everything from the repository root, `~/projects/yt-pipeline`. `WS` is the workspace folder next to the footage; the originals are never modified.

```bash
WS=~/media/trip/workspace
.venv/bin/python scripts/ingest.py ~/media/trip/proxy --workspace $WS
.venv/bin/python scripts/motion.py ~/media/trip/proxy --workspace $WS
.venv/bin/python scripts/quality.py ~/media/trip/proxy --workspace $WS
.venv/bin/python scripts/describe.py --workspace $WS          # vision model, ~2 cents per minute of footage
.venv/bin/python scripts/similarity.py --workspace $WS
.venv/bin/python scripts/index.py --workspace $WS             # shotlog.json + shotlog.html
.venv/bin/python scripts/llm_director.py --workspace $WS --brief "about 90 seconds, chronological, calm start, more energy towards the end, ambient sound only"
.venv/bin/python scripts/explain.py --workspace $WS                    # explain.html: why the edit looks the way it does
.venv/bin/python scripts/render.py --edit $WS/edit.json --out $WS/recap_720p.mp4
.venv/bin/python scripts/render.py --edit $WS/edit.json --out $WS/recap_final.mp4 --originals-dir ~/media/trip/raw
.venv/bin/python scripts/edit_to_fragments.py --edit $WS/edit.json --out $WS/fragments.yaml --source-dir ~/media/trip/raw --kdenlive-out $WS/recap.kdenlive
```

Every tool takes `--help` and `--json`. To review on the phone over Tailscale: `cd $WS && python3 -m http.server 8091 --bind 10.10.1.61`, then open `http://10.10.1.61:8091/shotlog.html` and the MP4.

## Writing a brief

Say what you want, not how to cut it. The router reads length, order, pacing, music and content type from it.

| You write | It does |
|---|---|
| "about 90 seconds, chronological, calm start, energy at the end" | target 90 s, shots in filming order, long holds first, shorter shots later |
| "3 minute recap, best moments first, music: ~/music/track.mp3" | ranks by score, cuts on the beats of the track |
| "tutorial, keep everything I say, chapters" | content type tutorial: silence and filler cut, chapters, graphics (D4) |

## The web app (phone + desktop)

`serve.py` is the app Doru uses from the phone. It is **tailnet-only** (one
bind address, never the wildcard) and it never exposes the footage beyond the
finished recap, the explanation and the contact sheet.

```bash
.venv/bin/python scripts/serve.py --workspaces ~/media/projects --inbox ~/media/inbox
# open http://<tailnet-ip>:8091/
```

- **Projects** lists every workspace with its status (`running` / `failed` /
  `done`, from `<workspace>/.run.json`) and links to what exists.
- **Upload** takes clips and photos straight from the phone browser: the files,
  a project folder name, the brief and an options form where **every field
  defaults to Auto** (length 30 s / 60 s / 90 s / 3 min, order chronological /
  best first, audio clean-up, format Auto (16:9) / 16:9 / 9:16 / both, music none / a track / auto from
  `~/media/music`, ambient level under the music, speed ramp Auto (off) / off / on). Clips play at normal speed unless **Speed ramp: on**, which lets the middle of long camera moves play up to 1.2x. When a long move has to be shortened, the cut is centred on its best moment (the model's pick, else the busiest second, else the middle). For a vertical version pick
  **Format: 9:16** (or **both**): the recap is cut to 720x1280 with a window
  that follows the subject (`recap_720p_9x16.mp4`). **Start** streams the files
  into `inbox/<folder>/` (allowlisted extensions, size caps, never overwrites,
  never outside the inbox) and starts `recap.py` in the background; the status
  shows on Projects.
- **A project page** has the tabs **Recap** (the player, and the vertical 9:16 player when the format was 9:16 or both), **Why?**
  (`explain.html` — the brief, the screenwriter plan in words, the timeline
  with a thumbnail per shot and the cut versus the candidate window, and every
  candidate used or rejected with the reason, by short id like `0071/s2`) and
  **Shots** (`shotlog.html`, the contact sheet).
- **Re-edit** on the project page repeats the options and adds a **feedback
  box**: plain words against those short ids ("drop 0071/s2, tunnel longer").
  It re-runs only the edit and the render — the analysis is reused. The
  feedback is appended to `feedback.jsonl` in the workspace.

`--music auto` picks the track from `~/media/music` by the brief's pace and
the shot tags, using the BPM and energy `beats.py` measures for every track
(`scripts/music_pick.py`, skill `yt-music-pick`).

## How it learns

The AI models do not change. What improves is data and rules on disk, all plain files you can read:

| What | Where | Grows by |
|---|---|---|
| Ground truth (what you say was really filmed) | `eval/*.yaml` in the repo, plus per-project corrections from the editor | one small entry per correction |
| Your approvals / rejections / trims | `<workspace>/feedback.jsonl` (append-only), summarised into a per-user profile | one line per action |
| Editing rules you asked for | `docs/guides/shot-grammar.md` and the director's rules; history in `docs/DECISIONS.md` | one rule per piece of feedback |

Sizes stay small: a correction is ~200 bytes, so 10 000 corrections are about 2 MB. No footage or frames are stored, only clip names and timecodes. See `docs/DECISIONS.md`, 2026-10-14, *Feedback storage and the rules digest*, for limits and compaction.

## Where to look

- What it can and cannot do yet: `docs/ROADMAP.md`
- Why it works this way: `docs/VISION.md`, `docs/DECISIONS.md`
- Filming tips that make the edit better: `docs/guides/shot-grammar.md`, section 4
- Known risks and how they are handled: `docs/RISKS.md`
