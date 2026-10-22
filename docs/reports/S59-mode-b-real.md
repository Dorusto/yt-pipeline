# S59 — Mode B on the real clip (lots 1-4)

Report for Doru to confirm. Scope: issue #60 on branch `s59-mode-b-real` — the
real OBS folder layout and pairing (lot 1 + follow-ups), chapters and Shorts
from the final FilmCraft edit (lot 2), Shorts karaoke with the brand look and
`layout: split` (lot 3), docs (lot 4), plus the review fixes of the lots 1-3
audit.

## Works

- **Review answers** (follow-up): the flat layout
  ignores videos whose name carries no `YYYY-MM-DD HH-MM-SS` timestamp (silently);
  `find_words` uses `common.timeline_words.words_file_for`; a recorded
  `sync_offset_s` survives a manifest rewrite even when the hand-written `camera`
  path is unresolved; `render.py`/`serve.py` share `common.timeline_words.fade_for_join`
  (golden tests byte-identical), `rough_cut.py`/`eval_rough_cut.py` share
  `word_in_window`, `explain.py`/`index.py` share `common.frames.read_frame`;
  `shorts_rank.py --layout {face,split}` writes the candidates' `layout` key; the
  watchdog cron in `docs/AGENT_SETUP.md` is the live line.
- **Real folder layout and pairing** (lot 1, `aed018c` + follow-ups): `mode_b.py`
  accepts `camera/` + `ecran/`/`screen/` (names under `mode_b:` in the config) or
  the flat layout, pairs each camera with the screen whose name timestamp is
  nearest within `pair_tolerance_s` (greedy one-to-one, exact-stem fallback),
  runs unpaired cameras camera-only and warns for unpaired screens. Verified on
  a fixture with the real file names (40 camera / 32 screen style) and on the
  real stems of the agent-side eval copy `/root/media/eval/second-brain` (name
  strings only, empty media): **31 sessions, 27 paired, 4 camera-only, 0
  screen-only** (`tests/test_mode_b.py::test_reference_names_pairing`). Derived
  matte exports (`-Alpha`/`_matte`) are never candidates. The clip folder layout
  is `~/Videos/<YYYY-MM-name>/` with `camera/`, `ecran/`, `transcrieri/` and the
  `.fcproj` (e.g. `~/Videos/2026-09-second-brain`).
- **Sync by sound** (follow-up 1): `rough_cut.py` measures the camera-to-screen
  offset by voice cross-correlation (mono 16 kHz, ±5 s, first 60 s) and records
  `sync_offset_s` + `sync_offset_confidence` in `sessions.yaml` (new schema
  key); a manifest value (hand-written or recorded) always wins and `mode_b.py`
  keeps it on a manifest rewrite. Name timestamps plus container start time are
  the weak-correlation fallback.
- **Screen checks** (follow-up 2 + answers): a screen file that looks like a
  camera recording (YuNet face in ≥ 60 % of 8 samples, or mean frame similarity
  ≥ 0.85 to the paired camera) warns loudly and lands in `screen_suspects`;
  `mode_b.py FOLDER --check` runs only these checks and writes nothing. Exit
  codes: `0` all clear, `1` a suspect found, `2` the check could not run
  completely (face detector unavailable — the run warns
  `screen check incomplete: face detector unavailable: …` and sets
  `screen_check_incomplete` in `--json`) or a user error. The YuNet detector
  loads only after readable screen frames. Tested in both cases.
- **Chapters and Shorts from the final edit** (lot 2, `5b01fa0`): `chapters.py` /
  `shorts_rank.py` take the final FilmCraft project (`--fcproj --sequence
  --words-dir`) or the timeline `_words.json` (`--timeline-words`) on top of the
  rough cut; one word mapping in `common/timeline_words.py` (a Mode B pair is one
  window, words counted once). `metadata.py`/`publish.py --chapters` use
  `chapters.json` instead of asking the model again. On the real Second Brain
  timeline (866.09 s, 1955 words): 9 chapters all inside the timeline and 5
  candidates of 29-58 s each inside one chapter. In `--fcproj` mode the two files
  default to the clip's publish folder (parent of `--words-dir`), per Doru
  2026-10-21.
- **Shorts karaoke** (lot 3, `329954f`, `c5e1938`): word times from the timeline
  `_words.json` (SRT interpolation only without `--words`), corrections applied
  to the text, look from `brand.json` (caption font/colours, active word `warm`),
  phrase-based line breaks, captions above the Shorts UI (`karaoke.safe_zone_frac`
  → `MarginV`), `layout: split` stacks the face over the screen. **Visual check**
  (lot 3 acceptance): one 608x1080 sample per layout rendered from a synthetic
  1920x1080 clip, frame grabs in `examples/s59-shorts-frames/` — captions burn at
  rows 596-702 (two lines), the active word reads `warm` (1.7-3.2 k warm pixels
  per frame) and the safe band below (rows 720-1080) stays empty by design.
- **Review fixes** (commits `2c3dd0c`, `b2ac43a`):
  shared code only in `scripts/common/` (constants, `read_frame_pairs`, brand
  default + `parse_hex_color`, `word_in_window`, one word mapping incl. the
  crossfade overlap), `metadata.py --chapters` expands `~`, one mock config
  fixture, `chapters.json`/`shorts_candidates.json` publish-folder default,
  RULES/DECISIONS/skills in step with the code. Full suite **1642 tests green**
  (`-m "not needs_melt and not needs_footage"`).

## S59 follow-up 2 (2026-10-21)

- **Review fixes:** the watchdog thresholds live only in `docs/AGENT_SETUP.md` (the live
  cron line) and `docs/RULES.md` points there; `serve.py` has no crossfade constant of its
  own (`common.timeline_words.fade_for_join` carries the default; golden/render tests
  byte-identical); `explain.py`/`index.py` share one JPEG thumbnail encoder
  (`common.frames.thumbnail_data_uri`); the manifest sync offset survives an unresolved
  `camera` path (new test with a `..` path on both sides).
- **Docs:** the clip folder layout is `~/Videos/<YYYY-MM-name>/` (`camera/`, `ecran/`,
  `transcrieri/` and the `.fcproj`) in the user guide and in this report; the agent-side
  eval copy is named once as such.
- **Shorts layout policy:** `shorts_rank.py --layout-policy {auto,on,off}` (config
  `shorts_layout`, default `auto`; `--layout` the hard override) writes `layout:
  face|split` on every candidate — `split` only for a camera+screen session whose screen
  shows during the candidate (timeline signals via `common.media.clip_kind`, no model
  call). The web app has the **Shorts layout** setting (auto/on/off, also from config) and
  a **Shorts** tab with a per-candidate layout override
  (`POST /<project>/shorts-candidates`); camera-only projects offer `face` only.
- **Public repo hygiene:** no private IP addresses anywhere (docs use `<agent-host>` /
  `<ntfy-url>`); `notify.py` reads `NTFY_URL` or config `ntfy_url` with no private default;
  `check_public.py` fails on 10/8, 172.16/12, 192.168/16 outside `.gitignored` files.

**Open questions (Doru):**

1. `layout: split` still stacks the **face cell over the screen cell** (issue #60, S59 lot
   3c). Your note says "(screen on top, camera below)" — flip the two cells? (one
   filtergraph line + the docs).
2. The Shorts layout setting is recorded with the run options and is what `shorts_rank.py
   --layout-policy` takes; nothing runs `shorts_rank.py` from the web app yet. Should a
   station chain it (publish / mode_b), or do you run it by hand as today?

## Mocked / not verified

- LLM passes (`chapters`, `shorts_rank`, `metadata`) ran with `llm_backend:
  mock` here; the recorded Second Brain chapter/candidate numbers come from the
  lot 2 run with the real model.
- `transcribe.py` ran only with `--backend mock`; no Whisper run on this machine.
- The face detector in the screen checks is a stub in the tests; YuNet ran only
  on synthetic frames (the sample above goes through the real `reframe.py`
  fallback path, no face in a synthetic clip).
- No FilmCraft GUI open of the exported project (headless checks only) and no
  phone browser session for the Cuts tab in this sprint.
- The full `mode_b.py` chain on the real Second Brain recordings was never run
  here (originals are read-only): pairing was verified from the real file names
  and the lot 2 tools from the real `.fcproj` + word JSON files.

## Doru, check this

- The sample frames (karaoke + split layout): `examples/s59-shorts-frames/*.jpg`.
- Run the checks on the real folder: `python3 scripts/mode_b.py ~/Videos/2026-09-second-brain --check --json`.
- The devlog gate line and "Next" in `docs/ROADMAP.md` (lifted 2026-10-21 per your call).
- The new "Mode B on a finished clip" section in `docs/guides/user-guide.md`.
- `docs/DECISIONS.md` top entries (S59 follow-ups + review fixes; the lot 1 pairing entry was corrected in place).
