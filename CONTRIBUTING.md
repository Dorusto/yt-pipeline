# Contributing to yt-pipeline

Thanks for wanting to help. This is a small project with a strict, agent-friendly workflow.

## Propose a change

1. Open an issue describing the problem or the feature before writing code.
2. Branch from `master` (`git checkout -b fix/short-name`).
3. Keep one change per pull request; do not mix unrelated fixes.
4. Run the tests before opening the PR: `.venv/bin/python -m pytest tests/ -q -m "not needs_melt and not needs_footage"`.
5. Open the PR against `master` and describe what changed and how you verified it.

Code, comments, commit messages and documentation are in English.

## Contributor License Agreement (CLA)

External contributions are accepted only with a signed CLA. The CLA grants the project copyright holder (Dorusto) the right to relicense your contribution, including under a different or commercial licence. This is what keeps dual licensing possible: the project is released under AGPL-3.0-or-later, and the same code can also be offered under a commercial licence.

Sign by email or by commenting the form below on the pull request. the owner records one signed CLA per contributor.

```
I grant the project copyright holder (Dorusto) the right to relicense my contribution under any licence.

Name:
Contact (email):
GitHub handle:
Date:
```

## Code rules

- English everywhere: code, comments, docs, commits.
- No inline comments in code; self-documenting names, short docstrings on public functions and modules.
- Tests pass: `.venv/bin/python -m pytest tests/ -q -m "not needs_melt and not needs_footage"`.
- A script change updates its skill in `.claude/skills/` in the same change.
- Never commit footage, frames, transcripts, subtitles, model weights or keys (`scripts/check_public.py` runs as a pre-commit hook and in CI).
- New tools are standalone scripts in `scripts/` with `--help`, `--json` and re-run safety; shared code only in `scripts/common/`.
