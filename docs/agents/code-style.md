# Code style

The rules in force live in [`docs/RULES.md`](../RULES.md); this file says how they
apply while writing code here.

- Code and documentation in English; no inline comments; short docstrings on
  public functions and modules are allowed.
- Every tool is a standalone script in `scripts/` with `--help`, `--json` and
  re-run safety; shared code only in `scripts/common/`; no tool imports another
  tool.
- Exit codes come from `scripts/common/exit_codes.py` (`EXIT_OK`, `EXIT_ATTENTION`,
  `EXIT_USAGE`, `EXIT_RUNTIME`); a failure with `--json` is one
  `{"ok": false, "error": "...", "exit_code": N}` object on stdout through
  `scripts/common/errors.py` `report_error`.
- A setting comes from a flag, else an environment variable, else the config file,
  else a clear error naming those three ways; no personal paths, no private IPs.
- No blocking prompt: interactive `input()` runs only on a TTY and every prompt
  has a flag alternative.
- Files validated against `schemas/` are the interfaces; a schema change stays
  with the architect.
