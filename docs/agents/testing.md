# Testing

The rules in force live in [`docs/RULES.md`](../RULES.md); this file says how tests
are written and run here.

- Tests use synthetic data only; `tests/fixtures/` and `examples/` hold synthetic
  data, never real footage, frames, transcripts or private paths.
- While working, run only the tests of the files that changed; run the full suite
  once, right before the commit.
- `pytest.ini` sets `timeout = 120` per test, so a hung test fails with its name.
- CI runs the suite on ubuntu; the macOS and Windows jobs are parked. Tests marked
  `needs_melt` or `needs_footage` run on the ubuntu job only and are deselected
  elsewhere.
- Every feature that passed the owner's real test gets a regression test on
  synthetic data that fixes its behaviour.

## Definition of done (every task)

- Tests pass.
- Ran end-to-end on sample media; for anything visual, the output frames were
  looked at (through `scripts/describe.py` or a measurement script, never by eye).
- Outputs validated against `schemas/`.
- No source file modified.
- A `docs/DECISIONS.md` entry if something new was chosen; `docs/RULES.md` updated
  in the same commit if a rule changed.
- The skill and the README updated if behaviour changed.
