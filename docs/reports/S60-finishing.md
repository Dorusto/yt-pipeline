# S60 Mode B finishing

## Lot 1d — Review fixes for lot 1c + additions

**Works**
- The `docs/ROADMAP.md` picture-in-picture line now names lots 1, 1b and 1c.
- `finish_pip.py` `--json` `ranges[].placed`/`keyframed` now reflect the state the
  run leaves behind: a `--bridge`/`--project` run that places and animates reports
  both true, computed from the run outcome (never a mid-run inspect), so an empty
  `effects` key on the placed item does not hide a successful animate apply; a dry
  run and a run without `--bridge`/`--project` keep the input state.
- The animate steps file counts as keyframe evidence only when applied: a new
  `finish_pip.animate.applied` marker is written after a successful animate apply,
  removed on every regeneration and on a failed apply (which also deletes the steps
  file), so a run that only wrote the file for a manual apply never prints
  `nothing to do` from it.
- `serve.py --check` without an inbox warns with the shared three-ways message and
  still validates the root and the bind (summary `inbox` null, exit 0); serving
  without `--check` still stops with the usage error.
- The general no-personal-setup rule is in `docs/RULES.md` (Safety rules) with its
  DECISIONS entry; the README quickstart, the `yt-recap`/`yt-shorts` skills and the
  user-guide Shorts tab line are corrected, and the lot 7b/8b report claims fixed.
- Verified: `.venv/bin/python -m pytest tests/test_finish_pip.py tests/test_serve.py -q`
  = 157 passed, 1 skipped; full suite
  `.venv/bin/python -m pytest tests/ -q -m "not needs_melt and not needs_footage"`
  = 1809 passed, 2 skipped, 1 deselected; `scripts/check_public.py` clean on the 12
  changed files; `grep -n "~/media" README.md` empty; both `--help` run fine.

**Mocked / not verified**
- The `--bridge`/`--project` place and animate runs are stubbed in the tests
  (`FakeRun`, monkeypatched `run_steps`/`inspect_sequence`); no live FilmCraft was
  used here.

**the owner, check this**
- `finish_pip.py` now reports `keyframed: true` after a successful `--bridge` run
  even when the placed item's `effects` key is empty — the state is taken from the
  run, not a re-inspect (`scripts/finish_pip.py` `apply_run_state`).
- `serve.py --check` runs on a machine with no inbox configured and only warns
  (`scripts/serve.py` `main`).


## Lot 7b — Review fixes for lot 7

**Works**
- `scripts/common/overlap.py` holds `overlap_seconds`/`overlap_fraction` (overlap over the
  shorter span); `shorts_rank.py`, `index.py`, `director.py`, `llm_director.py` and
  `graphics_plan.py` use them and keep no copy of their own.
- The Shorts tab of `serve.py` (the phone review) shows every candidate's `title` and
  `description`; the `SHORTS_CANDIDATES` fixture in `tests/test_serve.py` is the 0.2
  schema (`schema_version` 0.2, `title` + `description` on every row).
- A `description` that is not exactly 2 sentences is a warning — in the `--json`
  `warnings` and in the printed list (`warning: candidate N: description should be
  exactly 2 sentences (found K)`) — never a rejection; the schema still rejects empty and
  over-400-character descriptions.
- Intro rule (user decision): `is_standalone(start_s)` is `start_s >= 60 s`, no
  exceptions — the intro block never becomes a candidate whatever the span's end, and the
  "different cut" clause is gone. `docs/RULES.md`, `docs/DECISIONS.md`, the `yt-shorts`
  skill and the lot 7 line below carry it; the table test drops the old `10-65`/`50-95`
  keepers and a new test drops a 50-85 span that crosses the 60 s mark.
- `test_reference_final_project` requires at most 5 written candidates and no pair over
  the 20 % overlap cap (it required exactly 5).
- Verified: 3 new tests in `tests/test_shorts_rank.py` plus one table case; the full suite
  `.venv/bin/python -m pytest tests/ -q -m "not needs_melt and not needs_footage"` =
  1805 passed, 2 skipped, 1 deselected; `scripts/check_public.py` clean on the 13 changed
  files. Synthetic smoke (`/tmp/opencode/s60lot7b`, mock backend, 120 s edit with
  sentences 20-91 s): 9 generated spans, 7 dropped by the intro rule, 2 scored, 1 written
  and 1 dropped by the overlap cap; the ranked list prints before `written:`.

**Mocked / not verified**
- The LLM pass ran on the mock backend only; the Shorts tab HTML is covered by the tests,
  no phone or browser was used here.

**the owner, check this**
- The intro rule is now "a candidate starts at or after 60 s of the edit, no exceptions"
  (your decision in the lot 7 review) — say if the old "different cut" clause should come
  back (`scripts/shorts_rank.py` `is_standalone`).


## Lot 8b — Review fixes for lot 8

**Works**
- `serve.py build_recap_command` passes `--music-dir` (the resolved library) with
  `--music auto`, so the Upload page and the recap use the same library;
  `recap.py` resolves the library path again (`music_library(...).resolve()`).
- One "no music library configured" message in
  `common.config.no_music_library_message(flag_name)`, used by `music_pick.py`
  (`--library`) and by `recap.py`/`serve.py` (`--music-dir`);
  `common.config.no_inbox_message(flag_name)` is its inbox twin.
- `music_pick.py` catches `ConfigError` like the other tools: one `[error]`
  line, exit 2 (the tool's own errors stay exit 1); a broken config is never a
  traceback.
- `recap.py --music` help and the `yt-recap` skill name the three ways (skill
  `--json` summary lists `music_library`).
- `serve.py --inbox` has no personal default: `--inbox`, else
  `YT_PIPELINE_INBOX`, else `inbox.path` in the config, else a usage error
  naming those three ways (`config.yaml.example` documents `inbox.path`).
  User-guide and skill examples write `<trip-folder>`/`<workspaces>`/`<inbox>`;
  old DECISIONS entries and old reports stay as history.
- Verified: 15 new tests (`tests/test_config.py` 5, `tests/test_music_pick.py` 3,
  `tests/test_recap.py` 2, `tests/test_serve.py` 5) and the full suite
  `.venv/bin/python -m pytest tests/ -q -m "not needs_melt and not needs_footage"`
  = 1801 passed, 2 skipped, 1 deselected; `scripts/check_public.py` clean on the
  15 changed files. Synthetic smoke (`/tmp/opencode/l8b`): `--list --json` reads
  the library, `serve.py --check` without an inbox exits with the three-ways
  error, with `--inbox` the summary carries the inbox and the music library.

**Mocked / not verified**
- The web Upload → `recap.py` hand-off is covered by `build_recap_command` unit
  tests only; no full browser upload was run here.

**the owner, check this**
- `music_pick.py` exits 2 on a broken/missing config and 1 on its own errors
  (empty library, no track) — say if you want one code for both
  (`tests/test_music_pick.py::test_broken_config_is_one_error_line_and_exit_two`).
- `serve.py` now stops with a usage error when no inbox is configured
  (`--inbox` / `YT_PIPELINE_INBOX` / `inbox.path`), instead of defaulting to
  `~/media/inbox` (`docs/guides/user-guide.md` quickstart); `--check` warns with
  the same three-ways message and still validates the root and the bind without
  one.
- Remaining personal paths (`inbox.py --projects`, `docs/guides/phone-flow.md`,
  example strings in older scripts) wait for the audit sweep — see the S60 lot 8b
  entry in `docs/DECISIONS.md`.


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

**the owner, check this**
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
- `scripts/shorts_rank.py`: candidates are standalone (`is_standalone` — lot 7 shipped
  "never the edit's opening cut, never a span only inside the first 60 s"; lot 7b replaced
  it with "starts at or after 60 s of the edit", user decision), the written top 5
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

**the owner, check this**
- The intro rule asked here ("never the first 60 s of the edit unless it is a different
  cut") is decided and implemented since lot 7b: a candidate starts at or after 60 s of
  the edit, no exceptions (`scripts/shorts_rank.py` `is_standalone`, table in
  `tests/test_shorts_rank.py` `test_is_standalone_table`).
- `docs/RULES.md` `shorts_candidates.json` line and `docs/DECISIONS.md` S60 lot 7 entry.
