# ROADMAP — yt-pipeline

## Current status

Pipeline fully functional for end-to-end clip processing. Tested on Lenea clip (June 2026): produced 4 Shorts with karaoke subtitles, per-short metadata, and main video metadata. Ready for the next clip.

**New (2026-08-16):** rough-cut assembly from raw footage — `kdenlive_from_fragments.py` + `srt_from_fragments.py`. Tested end-to-end on the Dolomiti clip (30 fragments, 9 source files, retimed subtitle import) — see `ARCHITECTURE.md` and `DECISIONS.md` for how it works and why several non-obvious choices were made.

---

## AI Director track (D0–D5) — started 2026-10-12

Plan and principles: [`VISION.md`](VISION.md). Research: [`research/prior-art.md`](research/prior-art.md).

| Phase | Scope | Status |
|:---|:---|:---|
| D0 | Foundation: `scripts/common/`, `schemas/`, tests, CPU-only path | ✅ done 2026-10-12 — see [`reports/D0-REPORT.md`](reports/D0-REPORT.md) |
| D1a | Ingest + motion + quality (CPU only): moves, motion phases, junk with reasons — **⛔ Doru checkpoint** | ✅ approved by Doru 2026-10-13 after D1a-2 (ground-truth eval 0.26 → 0.69, time-box reached) — see [`reports/D1a-REPORT.md`](reports/D1a-REPORT.md) and [`reports/D1a-CHECKPOINT.md`](reports/D1a-CHECKPOINT.md) |
| S1 | First crude end-to-end slice on the Kotor footage (D1a labels → minimal director → MP4) | ✅ 2026-10-13, reviewed by Doru — see [`reports/S1-REPORT.md`](reports/S1-REPORT.md) |
| D1b | Describe + index → `shotlog.json`, vision bake-off — **⛔ Doru checkpoint** | ✅ approved by Doru 2026-10-13 (descriptions correct; edit feedback carried into D2) — see [`reports/D1b-REPORT.md`](reports/D1b-REPORT.md) and [`reports/D1b-vision-bakeoff.md`](reports/D1b-vision-bakeoff.md) |
| D2 | Router + director → `edit.json` → ffmpeg render; Kdenlive export optional (Mode A) | ✅ 2026-10-14, reviewed by Doru ("much better"); **D2-2 done 2026-10-14** (content beats for long takes, important cap 6 s + windows end at the steady motion, geometry `accidental` final, `audio_clean` on the render's ambient bus + RNNoise model, recap re-rendered with the 0072 approach in); **D2-3 done 2026-10-14** (agent-ready: `proxy.py`, `recap.py` one-command entry point, `doctor.py`, `yt-recap` skill + quickstart — folder + brief → MP4 with no other command); **D2-4 done 2026-10-14** (phone flow, repo side: `inbox.py` inbox watcher → project per day, `serve.py` tailnet preview, `recap.py --notify`, `docs/guides/phone-flow.md` + Telegram phrases — Syncthing/Hermes installs stay Doru's steps); **D2-5 done 2026-10-14** (cross-platform: `filelock` ledger lock, `psutil` process check, Python pre-commit hook, `pathlib` paths, per-platform encoders with `h264_videotoolbox`, CI matrix ubuntu/macos/windows); **D2-6 done 2026-10-15** (photos as candidates with EXIF + Ken Burns + burst dedup, `--music`, beat-snapped cuts, music bed with the ambient under it — see [`reports/D2-6-REPORT.md`](reports/D2-6-REPORT.md)); **D2-7 done 2026-10-15** (explanations: `decisions[]` + `explain.html` + `--feedback`; web app in `serve.py`: Projects/Upload menu, streaming upload into the inbox + background recap, Recap/Why?/Shots tabs, Re-edit form, `--music auto` via `music_pick.py`, pacing table — see [`reports/D2-7-REPORT.md`](reports/D2-7-REPORT.md)); **D2-8 done 2026-10-15** (OpenCV-first: `describe` frame mosaic at scene changes and motion phase boundaries — 35 model images instead of 134 on the Kotor eval set, 15 % cheaper, eval not worse; `reframe.py` moving 9:16 crop path — YuNet + TrackerMIL + saliency — used by `shorts_generator.py` instead of the removed Haar path — see [`reports/D2-8-REPORT.md`](reports/D2-8-REPORT.md)) — router, LLM director loop (screenwriter/editor/reviewer), phrases + no jump cuts, hero lengths, describe summary/tags, shotlog.html, eval on the final labels, `audio_clean`, `beats`, final render from the originals, Kdenlive export — see [`reports/D2-REPORT.md`](reports/D2-REPORT.md), [`reports/D2-2-REPORT.md`](reports/D2-2-REPORT.md), [`reports/D2-3-REPORT.md`](reports/D2-3-REPORT.md), [`reports/D2-4-REPORT.md`](reports/D2-4-REPORT.md) and [`reports/D2-5-REPORT.md`](reports/D2-5-REPORT.md) |
| D3 | Web editor v1, phone first (preview, light edits, final render on the homelab) | 🔲 partly covered by D2-7's web app (upload, tabs, re-edit); the light edits (trim/approve/reject per shot) remain |
| D4 | Mode B on the same index (fillers, chapters, Shorts ranking) + graphics station | 🔲 |
| D5 | P1 features (reframe, captions, audio clean-up, speed ramps, themes, search) | 🔲 |
| D6 | Packaging and publishing: thumbnail + brand kit, audio/SFX/music, dubbing, background removal, upload, PeerTube | 🔲 partly done — thumbnail + brand kit (issue #48, pulled forward 2026-10-17) |

**D2-13 … D2-18 (2026-10-15):** versions + honest run state, smooth Ken Burns, exact length, heroes first, the model's edit repaired not discarded, Shots marks, best photo of a burst, still 9:16 crop without a face, explain thumbnails on moved workspaces, agent loop guards, running station + progress bar, Re-edit always makes a new edit — reports `D2-13`, `D2-14`, `D2-15`.

**D2-19 (2026-10-15):** edit expectations as a test (`eval/kotor-edit.yaml` + `scripts/eval_edit.py` + `yt-eval-edit` skill) and the LLM director keeps the top stars (`hero_missing` violation, repair swaps one in for the weakest non-star of a compatible length; `STAR_*` shared in `common/moves.py`) — see [`reports/D2-19-REPORT.md`](reports/D2-19-REPORT.md). Real kotor-demo re-edit passes `eval_edit.py` 3/3 with `meta.director.by: llm`.

**D6-48 (2026-10-17, pulled forward by Doru):** `scripts/thumbnail.py` + `brand.json` + `schemas/brand.schema.json` + `yt-thumbnail` skill — portrait + title → branded 1280x720 thumbnail (two themes, rembg `birefnet-portrait` cut-out, layered `thumbnail.psd` via psd-tools) — see [`reports/D6-48-REPORT.md`](reports/D6-48-REPORT.md). Doru runs it once on a real photo (the build LXC OOM-kills BiRefNet).

**S51 publish-kit sprint (issue #51, branch `sprint-publish-kit`):** lot 0 done 2026-10-20 (watchdog per-run spend cap); lot 1 done 2026-10-20 — the publish chain (`subs_from_edit.py`, `transcribe.py`, `metadata.py` + `prompts/metadata.md`, `publish.py`, `yt-publish` skill, doctor publish checks; `analyze_srt.py`/`translate_srt.py`/`daily_summary_srt.py` now take the model per role from the config through `common/llm.py`). Lots 2-4 open: `matte.py` (issue #50), HyperFrames graphics, licence + docs. One PR to master at the end of the sprint.

**Next (open from 2026-10-15):**
- Set the cron watchdog to `--run-cap 2` per run (the per-run cap shipped in S51 lot 0, 2026-10-20).
- Architect: switch back to `master` at the end of a run (D2-18 left the LXC checkout on its branch).
- Progress bar colour: theme the `<progress>` element (Chrome on Android ignores `accent-color` there).
- Check v4 of kotor-demo: Why? should say "Edited by the model" (D2-17 repair).
- Flaky Windows test `test_ledger_survives_concurrent_processes` (Windows CI parked by Doru for now).
- Running notice readability (Doru): split it into separate lines — "Now: editing (model)" big, "step 10 of 13 · 47 s" small, the bar, then the "previous version stays below" sentence apart.

- Feedback as a test (Doru, v4 review): `eval/kotor-edit.yaml` expectations (must-include shots, intentional moves, length) + `eval_labels.py`-style edit check; then, each checked by it: (a) DONE in D2-19 — the LLM director must keep top heroes (0072/s5, s7b1, s7b5 dropped by the model in v4), (b) understand intent before disqualifying: before a segment is penalised or made ineligible for a defect (shake, horizon, blur, …), check from context whether the "defect" is deliberate (e.g. a person held centred while the camera orbits = a planned move, not shake) — a general check using the vision verdict and subject-stability signals that can override geometry defects, never a per-shot rule; 0091/s1 is only the first test case, DECISIONS entry when designed, (c) 9:16 crop still jumps where a face appears only part of the time.
- D3: per-shot buttons on the Shots tab ("must be in" / "wrong label") write to the expectations file.

**Later, decided 2026-10-15 (nothing implemented yet):**
- D3: installable on the phone as a PWA (manifest + small service worker in `serve.py`), served over HTTPS with `tailscale serve` (`https://<name>.ts.net`); no Astro for the app — the UI stack for D3 is still chosen by a DECISIONS entry.
- After the pipeline clip is published (the clip is the launch): self-hosting community release — Docker image + `docker-compose.yml` (footage, projects, music volumes, one API key), model choice OpenRouter or local (Ollama/vLLM), login or a clear "behind Tailscale / an authenticating proxy" rule, licence (MIT or AGPL), README with screenshots, CasaOS/Unraid/Umbrel templates, r/selfhosted post, Immich album → recap integration. Docs site can be Astro Starlight.

**Publish gate:** D2 working on the Kotor footage → devlog clip filmed and published before D3.

Milestones 2–7 below are the older plan for the finished-clip pipeline. Where a D-phase covers the same ground (Milestone 7 → D4), the D-phase wins; the rest stay as they are.

---

## Scripts — current state

| Script | Status | What it does |
|:---|:---|:---|
| `correct_srt.py` | ✅ done | Find/replace corrections on raw Whisper SRT |
| `translate_srt.py` | ✅ done | RO→EN translation via `common/llm.py` (model role `translate`), saves next to video |
| `analyze_srt.py` | ✅ done | Main video metadata + short candidates + per-short metadata (`--shorts-config`), model role `analyze` |
| `shorts_generator.py` | ✅ done | 9:16 crop, WhisperX alignment, karaoke ASS, ffmpeg nvenc |
| `subs_from_edit.py` | ✅ done (S51) | FilmCraft project or `edit.json` + per-source word JSONs → timeline `_RO.srt` + `_words.json` |
| `transcribe.py` | ✅ done (S51) | Single Whisper run → SRT + `_words.json` (`--backend mock` offline) |
| `metadata.py` | ✅ done (S51) | Corrected RO SRT + context note → `metadata.md` draft (titles, RO+EN description with CTA rules, chapters, tags, pinned comment) |
| `publish.py` | ✅ done (S51) | Publish chain: subtitles → corrections → review stop → translate → thumbnail → metadata, with `publish_state.json` |
| `pipeline.py` | 🔲 planned | Orchestrator for all steps |

---

## Workflow (current — manual)

```
1. Export clip: video + audio from editor
2. whisper audio.mp3 --output_format srt   ← run manually via pipx
3. python scripts/correct_srt.py raw.srt           ← fix common errors
4. [Manual SRT review and fix]             ← critical — this is the source of truth
5. python scripts/translate_srt.py RO.srt video.mp4
6. python scripts/analyze_srt.py RO.srt video.mp4 ← main metadata + short candidates
7. Edit scripts/shorts_config.yaml with chosen segments
8. .venv/bin/python scripts/shorts_generator.py --video video.mp4
9. python scripts/analyze_srt.py RO.srt video.mp4 --shorts-config scripts/shorts_config.yaml
10. [Add youtube_url to config after main video upload]
11. Re-run step 9 to refresh all short metadata with the real link
```

---

## Milestone 1 — Repo unification ✅

- [x] `shorts_generator.py` — 9:16 crop, WhisperX alignment, karaoke ASS, ffmpeg nvenc
- [x] GitHub repo named `yt-pipeline`
- [x] All scripts in repo: `correct_srt.py`, `translate_srt.py`, `analyze_srt.py`
- [x] README with complete workflow
- [x] Manual `x_offset` override per segment
- [x] SRT boundary trimming at both start and end
- [x] Face detection logging
- [x] Audio optional — extracted from video if not provided
- [x] All output next to video (not next to SRT)
- [x] `analyze_srt.py --shorts-config` — per-short metadata
- [x] `youtube_url` in config auto-fills short descriptions
- [x] Short candidates saved to `{basename}_shorts_candidates.txt`

## Milestone 2 — Single Whisper run 🔲 ← highest value next

WhisperX forced alignment is the slowest step (~30s per segment). If word timestamps were saved on the first Whisper run, this entire step disappears.

- [ ] `transcribe.py` — run `whisper` once, save both `.srt` and `_words.json` simultaneously
- [ ] `shorts_generator.py` — if `_words.json` already exists for a segment, skip WhisperX entirely
- [ ] Benefit: alignment step goes from ~30s to ~0s per segment

## Milestone 3 — `pipeline.py` orchestrator 🔲

- [ ] Single command: transcribe → correct → translate → analyze → shorts → metadata
- [ ] `--from-step N` to resume from a specific step
- [ ] Unified config covering all steps

## Milestone 4 — Shorts quality 🔲

- [ ] Font and highlight color configurable from config
- [ ] `LINE_WORDS` configurable per project
- [ ] Quick preview (first 10s) before full render

## Milestone 5 — Upload 🔲

- [ ] Optional: YouTube Data API auto-upload with scheduled publish date

## Milestone 6 — Chapter-based clip splitting 🔲

Split a long recording into clips using chapter timestamps — no AI selection needed.

- [ ] Download source video with `yt-dlp`
- [ ] Parse chapter timestamps (manual input or from video description)
- [ ] Cut clips with `ffmpeg` at chapter boundaries

## Milestone 7 — Auto-detect highlight moments 🔲

Reuses most of Milestone 6's code. Claude decides what's worth clipping instead of manual chapter marks.

- [ ] Full transcript of the long source video
- [ ] Claude prompt: identify top moments (hooks, striking numbers, surprising statements)
- [ ] Output: timestamps per moment, feeds into the same cutting pipeline as Milestone 6

## Control / confirmation interface — undecided

No control/confirmation interface (approve-before-upload, edit title before publish, etc.) has been built or committed to. Decide only if an actual need appears once Milestones 5-7 exist — don't build preventively. If needed, extend the existing Majordom Telegram bot rather than building a separate one.

---

## Before next clip — action items

- [ ] **Segment names: no spaces** — `5 reguli AI` created `5 reguli AI_metadata.txt`. Use `5-reguli-AI` or `ReguliAI`. Affects filenames only, title in metadata can be anything.
- [ ] **`corrections.txt` is incomplete** — "înrăbdare" was not caught. Add every missed Whisper error after each clip so the list grows over time.
- [ ] **`youtube_url` workflow** — add URL to `shorts_config.yaml` after the main video is published, then re-run `analyze_srt.py --shorts-config` to refresh all short metadata with the real link.

## Known issues

- Face detection (Haar cascades) reliable on frontal face, well-lit — use manual `x_offset` otherwise
- `correct_srt.py` misses errors not in `corrections.txt` — always review SRT manually
- Segment names become filenames — avoid spaces (use `-` or `_`)
- `analyze_srt.py` requires absolute paths
- `shorts_generator.py` no longer runs WhisperX forced alignment: word timing is interpolated from the SRT. `--skip-alignment` is accepted but does nothing and `run_forced_alignment`/`generate_plain_ass` are unused; `torch`/`whisperx` are now imported lazily inside that unused path, so a normal run and the test suite work without them (D0, see `DECISIONS.md`). `--yes`, `--json` and `--encoder` were added; the encoder is `h264_nvenc` when a GPU is present and `libx264` otherwise.
- `shorts_generator.py` asks interactively when the config has an `audio:` key pointing to a missing file, or when `--srt` is not given and the subtitle file is not found
- Possible cumulative subtitle drift of about one frame per fragment between `kdenlive_from_fragments.py` (frame-inclusive entries) and `srt_from_fragments.py` (sums `out - in`). Unverified
- `daily_summary_srt.py` has a hard-coded Romanian prompt for a solo motorcycle trip and summarises only the first 80,000 characters of each day
- `ROADMAP.md`, `ARCHITECTURE.md` and `CLAUDE.md` still describe WhisperX alignment, and DeepSeek for `correct_srt.py` and `translate_srt.py`
