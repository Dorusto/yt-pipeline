# VISION — AI Director

Where yt-pipeline is going next: from a set of scripts that process an already-edited clip to an AI editor that understands raw footage, takes a brief in plain language and produces an edit that opens in Kdenlive.

Target: beat DJI Mimo / GoPro Quik on action footage and Opus Clip / Descript on talking-head, without subscriptions or cloud lock-in.

Prior art and reasoning: [`research/prior-art.md`](research/prior-art.md). Status per phase: [`ROADMAP.md`](ROADMAP.md), section *AI Director track*.

---

## 1. Two modes, one engine

| Mode | Input | Output | Competes with |
|---|---|---|---|
| **A — Action / travel** | folders of raw clips (Osmo Action 4, A7 III, phone) | highlight film, reels, Shorts | DJI Mimo, GoPro Quik |
| **B — Talking-head** | OBS recordings, camera talking-head | cleaned long-form cut, chapters, Shorts, metadata drafts | Opus Clip, Descript |

Both share one core: **analyze once → index → many edits from different briefs.**

## 2. Principles

1. **Every tool is independent.** Each stage is a standalone script in `scripts/` with its own skill in `.claude/skills/`, exactly like the existing tools. A human runs it from the terminal, an agent runs it through the skill, the web UI calls the same command. No tool imports another tool's code. Shared code lives only in `scripts/common/` (schemas, config, model clients).
2. **Same contract for humans and agents.** Files and flags in, files out. `--help` with examples. `--json` prints a machine-readable summary. Safe to re-run: steps already done are skipped.
3. **Files are the interface.** Stages talk through JSON/YAML on disk, validated against schemas in `schemas/`. Any stage can be re-run alone.
4. **The edit list is `fragments.yaml`.** The director writes the format that `kdenlive_from_fragments.py` already reads, so the proven Kdenlive generator (Generation 5 structure, AV groups, DJI stream indices) is reused, not rebuilt. Extra fields (score, reason, slot) are allowed; the generator ignores unknown keys or is extended in the same change.
5. **Originals are read-only.** All outputs go to a project workspace next to the footage.
6. **Model backends are config, not code.** `VisionModel`, `Transcriber`, `Director` are interfaces with a mock, a cloud and a local implementation.

## 3. Architecture

```
ingest → analyze → index → direct → fragments.yaml → kdenlive / ffmpeg preview → review UI
```

1. **Ingest** — scan a folder, read metadata (time, fps, resolution, streams, GPS if present), make 720p proxies.
2. **Analyze** (cached per file), as separate tools — details in [`research/camera-motion-and-quality.md`](research/camera-motion-and-quality.md):
   - **motion**: camera trajectory from OpenCV optical flow (gyro data when the file has it), split of continuous takes into motion phases (still → ramp → steady move → ramp → still), moves labelled with the CameraBench vocabulary (pan, tilt, roll, dolly/zoom, truck, pedestal, steadiness, speed), steady range + handles per move;
   - **quality**: blur, exposure, shake, obstruction, setup/teardown junk → keep/discard per segment, always with a reason;
   - **describe**: vision model only on kept segments — 3 frames in order with timestamps and the motion label → description, semantic motion (orbit, tracking, reveal), subject, scene;
   - audio: transcript (existing Whisper flow), RMS energy;
   - music: beats, downbeats, energy.
3. **Index** — one `shotlog.json` per project. Score per shot = weighted fusion, weights per mode in config, starting from `0.40·vision + 0.35·audio_energy + 0.25·text`. Every candidate carries **score + hook + reason**. Overlap > 50% keeps the higher score.
4. **Direct** — three-step loop:
   - **Screenwriter** → `shot_plan.json` (structure, beats, durations from brief + music);
   - **Editor** → `fragments.yaml` (exact source timecodes per slot);
   - **Reviewer** → checks against brief and quality rules, sends fixes back (max N rounds), approves.
   - Dry run (plan only) always available.
5. **Output** — `kdenlive_from_fragments.py` + `srt_from_fragments.py` for the editable project; ffmpeg for a quick low-res preview render.
6. **Review UI** — pick folder, write brief, ranked clips, preview, approve / reject / regenerate per slot. Calls the same scripts.

## 4. Feature backlog

P0 = first usable version, P1 = next, P2 = later.

| Feature | Inspired by | Mode | Prio |
|---|---|---|---|
| Brief in plain language | Descript Underlord, BIGVU | A+B | P0 |
| Best-shot selection from visual understanding | Quik / Mimo, done smarter | A | P0 |
| Quality filters: blur, exposure, shake, duplicates, wind | — (Mimo weakness) | A | P0 |
| Beat-synced cuts | Quik, Mimo | A | P0 |
| Ranked candidates with score + reason, sortable | Opus Clip | A+B | P0 |
| Fast approve / reject / regenerate per slot | Opus Clip review | A+B | P0 |
| Silence + filler cut, chapters | Descript, `edit_workflow.py` | B | P0 |
| Kdenlive project output | existing generator | A+B | P0 |
| Text-based editing (delete words → cut) | Descript | B | P1 |
| Reframe 16:9 → 9:16 following the subject | Opus Clip, ClipsAI | A+B | P1 |
| Audio clean-up (wind, noise, loudness) | Descript Studio Sound | A+B | P1 |
| Speed ramps, themes / style presets | Quik | A | P1 |
| Search footage by text ("serpentine at sunset") | Lighthouse, TimeLens2 | A+B | P1 |
| Audience signal for Shorts (YouTube "Most replayed") | Opus Clip workflow | B | P2 |
| Frame grab, event grouping by day/place | Quik | A | P2 |

Out of scope for now: auto-upload and social posting, team features, generative video, voice cloning.

Lesson from the market: a large share of AI-picked clips get discarded, so a fast reject/regenerate loop matters more than a perfect first pick.

## 5. Phases (AI Director track)

Named D0–D5 so they don't collide with the existing Milestones 1–7 in the roadmap.

| Phase | Scope | Done when | Human checkpoint |
|---|---|---|---|
| **D0** | Foundation: `scripts/common/`, `schemas/`, pytest with tiny fixtures, CPU-only path (libx264 fallback, no torch import unless needed), known-issue fixes that block automation | tests green on the LXC without a GPU | — |
| **D1a** | `ingest`, `motion`, `quality` (CPU only, no model cost) + the two budget fixes from the PR #4 review | per-clip timeline of moves and discarded junk, with reasons, for Doru's test clips | **⛔ Doru checks the timelines against what he filmed** |
| **D1b** | `describe` + `index` → `shotlog.json`; vision bake-off (DeepSeek vs GLM) on kept segments of 5 varied clips | shotlog for a real folder + bake-off report | **⛔ Doru judges description quality** |
| **D2** | Director (screenwriter / editor / reviewer) → `fragments.yaml` → Kdenlive + preview | 3-min recap from a brief opens in Kdenlive | at ~80% |
| **D3** | Web UI v1 | full Mode A flow without a terminal | at ~80% |
| **D4** | Mode B on the same index: absorb `edit_workflow.py` (fillers, chapters, auto-editor), Shorts ranking feeding `shorts_generator.py` | one real talking-head video processed | at ~80% |
| **D5** | P1 features | per feature | batch review |

Do not start D1b before the D1a checkpoint, nor D2 before the D1b checkpoint.

## 6. Model routing (decided 2026-10-12)

| Role | Model | Via |
|---|---|---|
| Architect / reviewer agent | MiMo V2.6 Pro | OpenRouter |
| Coder agent | DeepSeek V4.1 Flash (fallback GLM 5.3 Flash) | OpenRouter |
| Vision, now | DeepSeek V4.1 Flash or GLM 5.3 Flash (image input), chosen by the D1 bake-off | OpenRouter |
| Vision, later or for private footage | Qwen3.8 27B 4-bit on the RTX 3090 via vLLM | local OpenAI-compatible endpoint |

Do not switch coder models mid-task. Vision backend is a config value.

## 7. Notes for builders

- `.gitignore` ignores `*.json`; `opencode.json`, `schemas/*.json` and `tests/fixtures/**/*.json` are re-included.
- The build LXC has no GPU and no nvenc. Anything GPU-bound needs a CPU fallback or a mock for tests.
- Test media: Doru's own short clips, transcoded to 720p H.264, fetched by a script, never committed (the repo is public).
- `edit_workflow.py` lives outside this repo; its behavior is documented in the vault (`DOCUMENTATIE_editare_claude.md`). Older standalone copies of some scripts exist under `~/Proiecte-AI/YouTube/` and have drifted (see DECISIONS 2026-09-06). This repo is the one to build on.
