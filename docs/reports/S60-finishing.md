# S60 Mode B finishing

## Lot 8 — Music library

**Works**
- The music library folder has no personal default anywhere: it comes from
  `--library` (`scripts/music_pick.py`) or `--music-dir` (`scripts/recap.py`,
  `scripts/serve.py`), else `YT_PIPELINE_MUSIC_LIBRARY`, else `music.library` in
  the config (`common.config.music_library`), and with none set the tools that
  need it stop with a usage error naming those three ways (`recap.py` fails
  before the stations run; `serve.py` starts with no tracks and warns with the
  same message, so the web app still runs without music). `config.yaml.example`
  documents `music.library`; the user guide, the `yt-music-pick` and `yt-recap`
  skills and the RULES music line write `<music-library>`.
- `scripts/music_pick.py --list [--json]` reports every track with `file`,
  `path`, `duration_s`, `bpm`, `licence_status` (`sidecar` / `library` /
  `missing`) and `licence_file` (a `<stem>.license`/`.licence` or
  `<file>.license`/`.licence` sidecar, else the library's
  `LICENSE`/`LICENCE`/`LICENSE.txt`/`LICENCE.txt`/`COPYING`, else nothing); a
  track that cannot be measured keeps nulls and the listing still succeeds.
  Nothing in any tool downloads music.
- Verified: 16 new tests (`tests/test_config.py` 4, `tests/test_music_pick.py` 6,
  `tests/test_recap.py` 3, `tests/test_serve.py` 3) and the full suite
  `.venv/bin/python -m pytest tests/ -q -m "not needs_melt and not needs_footage"`
  = 1786 passed, 2 skipped, 1 deselected; `scripts/check_public.py` clean on the
  14 changed files.
- Synthetic end-to-end (`/tmp/opencode/mlib8`, two generated WAVs, one sidecar
  licence and one library licence): `--list` prints both rows with `sidecar` and
  `library`, `--list --json` carries path/duration/BPM/licence per track, pick
  mode through `YT_PIPELINE_MUSIC_LIBRARY` writes `music_choice.json`, and a run
  with no library at all exits 1 with the three-ways message.

**Mocked / not verified**
- No real music library and no model call; the licence check is file-name based
  only, and the BPM numbers on the synthetic WAVs are meaningless (the
  measurement path itself is `beats.py`, unchanged).

**Doru, check this**
- The rule and the tool split are in `docs/DECISIONS.md` (2026-10-22, S60 lot 8)
  and `docs/RULES.md` (music line) — confirm the `serve.py` choice: warn and
  serve no tracks instead of exiting when no library is configured.
- `docs/guides/user-guide.md` "Music library" section and
  `.claude/skills/yt-music-pick/SKILL.md` (`--list`, the three ways).
- The build machine keeps its folder in the git-ignored `config.yaml`
  (`music.library`); the repo never names it.
- Not touched: `~/media/inbox` (`serve.py --inbox`, phone flow) and the
  `~/media/trip` style examples in the user guide — the correction named the
  music paths; say if the no-personal-path rule must extend to them.
- Dated history entries in `docs/DECISIONS.md` and the old reports still quote
  the old default as it was then — kept as history; say if they should be
  scrubbed too.

## Lot 7 — Shorts quality

**Works**
- `scripts/shorts_rank.py`: candidates are standalone (`is_standalone` — never the edit's
  opening cut `start_s <= 0.5`, never a span only inside the first 60 s), the written top 5
  never overlap by more than 20 % (`overlap_s / min(duration_s) <= 0.2`, greedy in rank
  order, the list refills from the next distinct candidates), and every candidate carries
  `title` (max 60, hook first) and `description` from the same `director` pass
  (`SCHEMA_VERSION` 0.2, `schemas/shorts_candidates.schema.json` requires both).
  `--json` adds `dropped_overlap`/`dropped_intro`; without `--json` the ranked list prints
  before `written:`.
- Verified: `tests/test_shorts_rank.py` 64 passed; the full suite
  `.venv/bin/python -m pytest tests/ -q -m "not needs_melt and not needs_footage"` =
  1770 passed, 2 skipped, 1 deselected; `scripts/check_public.py` clean on the 10 changed
  files.
- Synthetic end-to-end (`/tmp/s60lot7`, 90 s testsrc2 + sine, mock backend): the rank run
  wrote 2 candidates (11 scored, 9 dropped by the overlap cap, 1 by the intro rule) and
  `shorts_generator.py --candidates` rendered `shorts/Short1-short1.mp4` (608x1080,
  59.0 s = `end_s - start_s`) and `shorts/Short2-short2.mp4` (608x1080, 29.0 s) with a
  karaoke `.ass` each; frame measurement shows the karaoke text burned at y 672-705 with
  the brand highlight and outline during a cue and no text in the cue gap.

**Mocked / not verified**
- The LLM pass ran on the mock backend only — no real model call, no real footage; the
  quality of the generated titles/descriptions is not judged here.

**Doru, check this**
- The brief's "never the first 60 s of the edit unless it is a different cut" is implemented
  as (a) never a candidate starting at the very beginning and (b) never a candidate that
  lies entirely inside the first 60 s, while a cut that starts later and runs past the 60 s
  mark is kept (`scripts/shorts_rank.py` `is_standalone`, table in
  `tests/test_shorts_rank.py` `test_is_standalone_table`) — confirm or correct.
- `docs/RULES.md` `shorts_candidates.json` line and `docs/DECISIONS.md` S60 lot 7 entry.
