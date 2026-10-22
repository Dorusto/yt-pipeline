# S60 Mode B finishing

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
