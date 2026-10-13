# VISION — AI Director

Where yt-pipeline is going next: from a set of scripts that process an already-edited clip to an AI editor that understands raw footage, takes a brief in plain language and produces an edit you preview and adjust in the browser, rendered on the homelab. Kdenlive stays available as an optional export.

Target: beat DJI Mimo / GoPro Quik on action footage and Opus Clip / Descript on talking-head, without subscriptions or cloud lock-in.

Prior art and reasoning: [`research/prior-art.md`](research/prior-art.md), [`research/ai-editors-features.md`](research/ai-editors-features.md) and [`research/rendering-and-audio.md`](research/rendering-and-audio.md). Status per phase: [`ROADMAP.md`](ROADMAP.md), section *AI Director track*.

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
4. **The edit list is `edit.json`.** One schema (`schemas/edit.schema.json`) is the single source of truth for an edit: video slots (source, in/out, score, reason), audio (music, SFX, ambience, levels), captions and graphics overlays (template + props). Everything else is an adapter that reads it: ffmpeg render, browser preview, graphics engine, and the optional Kdenlive export (`edit.json` → `fragments.yaml` → the existing `kdenlive_from_fragments.py`, reused, not rebuilt). No tool depends on Kdenlive being installed.
5. **Originals are read-only.** All outputs go to a project workspace next to the footage.
6. **Model backends are config, not code.** `VisionModel`, `Transcriber`, `Director`, `GraphicsEngine`, `MusicModel` are interfaces with a mock, a cloud and/or a local implementation.
7. **One tool for every kind of video.** Vacation, tutorial, presentation and talking-head use the same stations; a router picks which stations run and with which settings from the brief and the detected content type.

## 3. Architecture

```
ingest → analyze → index → direct → edit.json → { web preview + light edits | ffmpeg render | graphics engine | Kdenlive export (optional) }
```

1. **Ingest** — scan a folder, read metadata (time, fps, resolution, streams, GPS if present), make 720p proxies.
2. **Analyze** (cached per file), as separate tools — details in [`research/camera-motion-and-quality.md`](research/camera-motion-and-quality.md):
   - **motion**: camera trajectory from OpenCV optical flow (gyro data when the file has it), split of continuous takes into motion phases (still → ramp → steady move → ramp → still), moves labelled with the CameraBench vocabulary (pan, tilt, roll, dolly/zoom, truck, pedestal, steadiness, speed), steady range + handles per move;
   - **quality**: measured defects (blur, exposure, shake, obstruction, horizon) and a role per segment (move, hold, transition in/out, setup, teardown, accidental) — labels, never deletions; techniques catalogued in [`guides/shot-grammar.md`](guides/shot-grammar.md);
   - **describe**: vision model on all segments except confident setup/teardown/accidental — 3 frames in order with timestamps and the motion label → description, semantic motion (orbit, tracking, reveal), subject, scene;
   - audio: transcript (existing Whisper flow), RMS energy;
   - music: beats, downbeats, energy.
3. **Index** — one `shotlog.json` per project. Score per shot = weighted fusion, weights per mode in config, starting from `0.40·vision + 0.35·audio_energy + 0.25·text`. Every candidate carries **score + hook + reason**. Overlap > 50% keeps the higher score.
4. **Direct** — three-step loop:
   - **Screenwriter** → `shot_plan.json` (structure, beats, durations from brief + music);
   - **Editor** → `edit.json` (exact source timecodes per slot);
   - **Reviewer** → checks against brief and quality rules, sends fixes back (max N rounds), approves.
   - Dry run (plan only) always available.
   - A **router** in front of the loop reads the brief and the content type (vacation, tutorial, presentation, talking-head) and chooses the stations: e.g. beat sync and music for a trip recap, filler cut + chapters + graphics for a tutorial.
5. **Output** — adapters that read `edit.json`:
   - `render`: ffmpeg cuts, transitions, speed, ASS captions and audio mix; preview at 720p from proxies, final at full quality from the originals on the homelab;
   - `graphics`: story elements (titles, maps, counters, browser/terminal mockups, charts driven by the transcript) through a `GraphicsEngine` — Remotion (reusing Hasan's MIT kit, with paid services swapped for ours) or HyperFrames (Apache-2.0), chosen by a bake-off at D4;
   - audio stations: `audio_clean` (RNNoise via ffmpeg + loudness), `music` (YuE2 / ACE-Step / Stable Audio Open), `sfx` (own CC0 library + Stable Audio Open), `beats` — plain Python tools, no ComfyUI;
   - `export_kdenlive` (optional): `edit.json` → `fragments.yaml` → `kdenlive_from_fragments.py` + `srt_from_fragments.py`.
6. **Web editor** — phone first: pick folder, write brief, ranked clips, play the edit from proxies, reorder / trim / swap a slot, approve / reject / regenerate, ask for the final render. Calls the same scripts. Not a general timeline editor; OpenCut (MIT) is the candidate for full manual editing once it gets a project import API.

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
| Browser preview + light edits from `edit.json` | Opus Clip editor, Descript | A+B | P0 |
| Kdenlive project output (optional export) | existing generator | A+B | P1 |
| Story graphics driven by what is said (titles, maps, mockups, charts) | Hasan's Remotion kit, Descript | A+B | P1 (D4) |
| Text-based editing (delete words → cut) | Descript | B | P1 |
| Reframe 16:9 → 9:16 following the subject | Opus Clip, ClipsAI | A+B | P1 |
| Audio clean-up (wind, noise, loudness) | Descript Studio Sound | A+B | P1 |
| Speed ramps, themes / style presets | Quik | A | P1 |
| Search footage by text ("serpentine at sunset") | Lighthouse, TimeLens2 | A+B | P1 |
| Audience signal for Shorts (YouTube "Most replayed") | Opus Clip workflow | B | P2 |
| Frame grab, event grouping by day/place | Quik | A | P2 |
| Project log: summary, topics, B-roll catalogue | Eddie AI | A+B | P0 (D1b) |
| Automatic B-roll over talking-head | Eddie AI, Descript | B | P1 (D4) |
| Speaker detection, take/topic chunking, retakes | CutBack Selects | B | P1 (D4) |
| Multicam and audio sync without markers | CutBack Selects | A+B | P1 |
| Overnight edit on the homelab, draft ready by morning | Insta360 AI Director, Eddie AI | A+B | P1 |
| Feedback loop: approve / reject trains our own scorer | Opus Clip | A+B | P1 |
| Faces and smiles preference | DJI LightCut | A | P2 |
| Export to Premiere / Resolve via OpenTimelineIO | CutBack, Eddie AI | A+B | P2 |

Out of scope for now: social posting beyond YouTube/PeerTube, team features, generative video footage. Upload, voice dubbing and the rest of packaging are D6, not out of scope (Doru's vault project `70_ai-youtube-automation-pipeline/20_TODO.md`, chunks 3–7).

Lesson from the market: a large share of AI-picked clips get discarded, so a fast reject/regenerate loop matters more than a perfect first pick.

## 5. Phases (AI Director track)

Named D0–D5 so they don't collide with the existing Milestones 1–7 in the roadmap.

| Phase | Scope | Done when | Human checkpoint |
|---|---|---|---|
| **D0** | Foundation: `scripts/common/`, `schemas/`, pytest with tiny fixtures, CPU-only path (libx264 fallback, no torch import unless needed), known-issue fixes that block automation | tests green on the LXC without a GPU | — |
| **D1a** | `ingest`, `motion`, `quality` (CPU only, no model cost) + the two budget fixes from the PR #4 review | per-clip timeline of moves, transition candidates, setup/teardown and defects, for Doru's test clips | **⛔ Doru checks the timelines against what he filmed** |
| **D1b** | `describe` + `index` → `shotlog.json`; vision bake-off (DeepSeek vs GLM) on kept segments of 5 varied clips | shotlog for a real folder + bake-off report | **⛔ Doru judges description quality** |
| **D2** | Router + director (screenwriter / editor / reviewer) → `edit.json` → ffmpeg render (preview + final), music beats; Kdenlive export adapter | 3-min Kotor recap from a brief, playable as MP4; also opens in Kdenlive | at ~80% |
| **D3** | Web editor v1, phone first: brief, ranked clips, preview from proxies, reorder / trim / swap, approve / reject / regenerate, final render on the homelab | full Mode A flow from the phone, no terminal, no Kdenlive | at ~80% |
| **D4** | Mode B on the same index: absorb `edit_workflow.py` (fillers, chapters, auto-editor), Shorts ranking feeding `shorts_generator.py`; graphics station (`GraphicsEngine` bake-off Remotion vs HyperFrames, Hasan's kit ported) | one real talking-head or tutorial video processed, with automatic graphics | at ~80% |
| **D5** | P1 features | per feature | batch review |
| **D6** | Packaging and publishing, each a standalone tool: thumbnail with a vision model + brand kit (`brand.json`), audio clean-up (RNNoise) + SFX library + music (YuE2 / ACE-Step, licence-checked), EN voice dubbing (F5-TTS / XTTS), background removal (MatAnyone), upload as private draft (`yt_upload`), PeerTube as the canonical source | per tool | batch review |

**Publish gate:** when D2 works on the Kotor footage, the devlog clip (vault `2026-39_pipeline-montaj-automatizat`) is filmed and published before D3 starts — Doru's anti-sinking rule: no next chunk before the previous one is filmed and published.

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
