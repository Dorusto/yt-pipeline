# ROADMAP — yt-pipeline

## Current status

Pipeline fully functional for end-to-end clip processing (transcription → correction → translation → metadata → 9:16 shorts) and for the AI Director track (D0–D2 done, D3–D6 open). The S51 publish-kit sprint is done and merged (PR #53, 2026-10-21; lots 0–6: watchdog per-run cap, publish chain, `matte.py`, HyperFrames graphics, licence + docs, FilmCraft bridge, `publish.py --kit`). The S54 FilmCraft-adapter sprint is done on branch `s54-filmcraft-spike` (lot 1 export spike, lot 2 web review polish); its PR is open for Doru. The S56 Mode B sprint is done on branch `s56-mode-b` (lot 1 `rough_cut.py` + sessions schema, lot 2 `eval_rough_cut.py` + tuning, lot 3 `mode_b.py` one-command flow); its PR is open for Doru.

**Publish gate:** D2 working on the Kotor footage → devlog clip filmed and published before D3.

The milestone plan that predates the D-phases is archived in [`archive/ROADMAP-2026-10.md`](archive/ROADMAP-2026-10.md).

---

## AI Director track (D0–D6) — started 2026-10-12

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

**S51 publish-kit sprint (issue #51, branch `sprint-publish-kit`):** lot 0 done 2026-10-20 (watchdog per-run spend cap); lot 1 done 2026-10-20 — the publish chain (`subs_from_edit.py`, `transcribe.py`, `metadata.py` + `prompts/metadata.md`, `publish.py`, `yt-publish` skill, doctor publish checks; `analyze_srt.py`/`translate_srt.py`/`daily_summary_srt.py` now take the model per role from the config through `common/llm.py`) plus the lot 1 addendum (2026-10-20: `metadata.md` writes a `## Thumbnail` section with 2-3 photo concepts mapping to `thumbnail.py`); lot 2 done 2026-10-20 — `matte.py` (issue #50 scope 1-2: RVM via `torch.hub` → ProRes 4444 `.mov` with alpha for a time range); lot 3 done 2026-10-21 — HyperFrames graphics (`graphics.py` + `graphics_plan.py`, DECISIONS 2026-10-21); lot 4 done 2026-10-21 — licence AGPL-3.0-or-later + CLA, docs cleaned; lot 5 done 2026-10-21 — FilmCraft bridge (`fcproj_markers.py` → `markers.json` + Rose `todo.md`, `loudness.py` two backends); lot 6 done 2026-10-21 — `publish.py --kit` publish kit for manual YouTube Studio upload (variant B per Doru's comment: no API upload, `studio.md` copy-ready blocks + loud checks). PR #53 merged 2026-10-21.

**S54 FilmCraft-adapter sprint (issue #54, branch `s54-filmcraft-spike`):** lot 1 done 2026-10-21 — the export spike (`scripts/export_filmcraft.py` writes `edit.json` → `.fcproj` directly, route A; route B's `filmcraft-cli` fails on missing media; round trip through `subs_from_edit.py` identical, project opens headless; report `docs/reports/S54-filmcraft-spike.md` carries a proposed DECISIONS entry for Doru); lot 2 done 2026-10-21 — Shots-tab review buttons ("must be in" / "wrong label" → `<workspace>/expectations.yaml`, the file `eval_edit.py` reads) and the run-progress bar themed from `brand.json` `colors.cool`. PR open, not merged.

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
| `metadata.py` | ✅ done (S51) | Corrected RO SRT + context note → `metadata.md` draft (titles, RO+EN description with CTA rules, chapters, tags, pinned comment, thumbnail photo concepts) |
| `publish.py` | ✅ done (S51) | Publish chain: subtitles → corrections → review stop → translate → thumbnail → metadata, with `publish_state.json` |
| `matte.py` | ✅ done (S51) | RVM background removal for a time range → ProRes 4444 `.mov` with alpha (`torch.hub`, CUDA or CPU) |
| `pipeline.py` | 🔲 planned | Orchestrator for all steps |

## Next

- Set the cron watchdog to `--run-cap 2` per run (the per-run cap shipped in S51 lot 0, 2026-10-20).
- Flaky Windows test `test_ledger_survives_concurrent_processes` (Windows CI parked by Doru for now).
- Mode B next: chapters, take/topic chunking, Shorts ranking (D4).
