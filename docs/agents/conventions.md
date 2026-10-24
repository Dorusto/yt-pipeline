# Conventions

The rules in force live in [`docs/RULES.md`](../RULES.md); this file says how work
is organised here.

## Branches and commits

- Never edit on `master`; create or check out the task branch first.
- Commit after every finished step (a passing test, a fixed bug), even mid-task,
  with a `wip:` message if needed.
- Conventional commit messages; never push to `master`; open a pull request at the
  end of a phase.

## Files and footage

- The docs-to-update rule and the read-only-footage rule are in `docs/RULES.md`
  and are not repeated here.
- File structure: `Export/video/`, `Export/audio/`, `Export/subtitles/`,
  `Export/auto/`, `Export/shorts/`.

## AI Director roles

- Doru is product owner and reviewer; he steps in when a phase is ~70–80 % done,
  plus the mandatory D1 checkpoint.
- The architect plans each task and writes a brief; the coder implements one task
  at a time from the brief alone; the architect audits before marking it done.
- Stop and ask only for something irreversible or expensive, a pure taste choice
  between equal options, or a phase checkpoint.
- A phase report goes to `docs/reports/D<n>-REPORT.md` with three short lists:
  *Works*, *Mocked / not verified*, *Doru, check this* (max 5 bullets).

## Do not

- Re-install MediaPipe — OpenCV was chosen intentionally (see `docs/DECISIONS.md`).
- Run Whisper separately for word timestamps — WhisperX forced alignment is the
  source of truth.
