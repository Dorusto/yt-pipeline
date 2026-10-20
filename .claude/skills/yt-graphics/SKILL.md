---
name: yt-graphics
description: Render branded graphic overlays (lower third, counter, callout) as ProRes 4444 .mov files with alpha at timeline resolution, ready to drop over the edit in the editor. The engine is HyperFrames (Apache-2.0); the look comes from brand.json. Use when the user wants a lower third, a counter or a callout overlay, a transparent graphic for the edit, or to render the approved entries of a graphics plan. Trigger phrases - "randeaza un lower third", "graphic overlay", "counter overlay", "callout animat", "graphics.py", "ProRes overlay cu alpha", "lower third cu brandul", "render the graphics plan". Not for planning where the graphics go (that is the graphics plan tool, next task), not for placing the overlay on a timeline (the editor's job), not for captions/ASS burn-in (that is the render) and not for still thumbnails (that is `thumbnail`).
compatibility: Run from the repository root. Needs Node.js 22+ and npx on PATH for the default `--backend hyperframes` (the pinned HyperFrames CLI is fetched by npx, never vendored) and ffmpeg/ffprobe for `--backend mock` and the alpha check. Makes no LLM calls, so the run costs 0 USD.
---

# yt-graphics

Render one branded graphic overlay as a ProRes 4444 `.mov` with alpha, at the
timeline resolution, so the editor can drop it over the edit. The composition
HTML per template lives in `assets/graphics/`; `graphics.py` fills it with the
brand values from `brand.json` and the entry props and renders it with the
HyperFrames CLI. Placing the overlay on a timeline is the editor's job — this
tool only produces the file.

## Step 1: render one overlay

```bash
.venv/bin/python scripts/graphics.py --template lower_third \
  --props '{"name":"Doru","role":"creator"}' --out /tmp/lt.mov --json
.venv/bin/python scripts/graphics.py --template counter \
  --props '{"number":"42","label":"clips"}' --out /tmp/counter.mov --duration 3
.venv/bin/python scripts/graphics.py --template callout \
  --props '{"text":"echo hello","style":"terminal"}' --out /tmp/callout.mov
```

- `--template` is `lower_third`, `counter` or `callout`.
- `--props` is a JSON object; the keys per template are below. A missing or
  empty required key, an unknown key or an unknown template exits 2 naming the
  problem.
- `--out` is the output `.mov` (ProRes 4444 with alpha). Re-running overwrites
  it (a derived artifact).
- `--width` / `--height` / `--fps` / `--duration` set the canvas; defaults
  `1920`, `1080`, `30`, `3.0`. `--duration` must be `> 0` and `<= 15`.
- `--brand` is the brand kit JSON (default `brand.json` at the repo root).
- `--backend` is `hyperframes` (default) or `mock`.
- `--json` prints one summary line: `output`, `template`, `width`, `height`,
  `fps`, `duration_s`, `backend`, `wall_time_s`, `llm_cost_usd` (always 0.0).
  Without it, one human line with the path and the template.

### Props per template

| Template | Required | Optional |
|:---|:---|:---|
| `lower_third` | `name`, `role` | — |
| `counter` | `number` (string or number, kept as written), `label` | — |
| `callout` | `text` | `style`: `text` (default) or `terminal` |

## Step 2: render an approved graphics plan

```bash
.venv/bin/python scripts/graphics.py --plan graphics_plan.json --out-dir overlays/ --json
```

`graphics_plan.json` is validated against `schemas/graphics_plan.schema.json`
(FIXED). Only entries with `approved: true` are rendered, one file per entry:
`<out-dir>/<id>.mov` (e.g. `g1.mov`). Each entry carries its own `time_s`,
`duration_s`, `template` and `props`; `duration_s` becomes the clip duration and
`time_s` is recorded in the summary (the engine does not place anything on a
timeline). A plan with zero approved entries exits 0 with an empty `outputs`
list and a one-line notice. A rejected entry is never rendered and its props are
never validated (it may hold a half-finished draft). A bad approved entry exits
2 naming the entry id.

The `--json` summary in plan mode has `outputs` (a list of
`{id, template, output, time_s, duration_s}`), `width`, `height`, `fps`,
`backend`, `wall_time_s`, `llm_cost_usd`.

## Brand rules

Every look value comes from `brand.json` (validated by
`schemas/brand.schema.json`) through `--brand`; `graphics.py` holds no colour,
font family or size constant. The templates read `colors.warm` (accent),
`colors.ink` (foreground), `colors.night` (plate), `colors.muted`,
`fonts.title.family` / `fonts.kicker.family` and their `size_px` /
`tracking_em`. Font paths resolve relative to the brand file, exactly like
`thumbnail.py`; a missing font file is a hard error (exit 2) naming the path and
the brand key, never a silent fallback.

## What the output is

A ProRes 4444 `.mov` with alpha, video only, at the requested canvas. The
composition leaves `html`/`body` unpainted so the MOV carries real alpha; the
tool verifies the pixel format with ffprobe after every render and fails loudly
(exit 1) when it is not `yuva444p10le` or `yuva444p12le` (ffmpeg >= 6.0 reports
ProRes 4444 as 12-bit). `--backend mock` is deterministic and offline: it writes
the composed `index.html` next to the output plus a transparent MOV produced by
ffmpeg, for tests and runs without Node.

## HyperFrames dependency

The CLI is pinned: `npx --yes hyperframes@0.8.181`. Node.js 22+ and npx are a
machine dependency like ffmpeg, never vendored. The HyperFrames agent skills are
not committed (the published set is ~19 MB, ~3.2 MB of markdown, over the 2 MB
limit, and carries no licence file beside it). Install them with:

```bash
GIT_CLONE_PROTECTION_ACTIVE=0 npx skills add heygen-com/hyperframes --all
```

Pinned upstream commit: `908f2d095bba4e2b97363c5533e2702f1e92505d`
(`https://github.com/heygen-com/hyperframes/tree/main/skills`). Set
`HYPERFRAMES_SKIP_SKILLS=1` so later `skills update` runs leave a pinned install
alone. The runtime code does not depend on the skill files being present.

## What is not its job

- Planning where the graphics go (`graphics_plan.py`, the next task) — this tool
  only renders the approved entries.
- Placing the overlay on a timeline, rendering it into the edit, Kdenlive or
  FilmCraft import — the editor's job.
- Captions/ASS burn-in (that is the render) and still thumbnails (that is
  `thumbnail`).
