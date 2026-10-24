# Testing

The rules in force live in [`docs/RULES.md`](../RULES.md); this file says how tests
are written and run here.

- The synthetic-data rule and the CI matrix are in `docs/RULES.md` and are not
  repeated here.
- `pytest.ini` sets `timeout = 120` per test, so a hung test fails with its name.
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
