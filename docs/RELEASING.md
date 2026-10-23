# RELEASING — how a version is made

Rules for the owner and for every agent. A version is a tag on `master` plus a GitHub Release; the stable list lives in [`RELEASES.md`](RELEASES.md). Plain English, short.

## Before a version

1. The owner merges into `master` everything that goes in; nothing uncommitted.
2. The full test suite passes, with no ignored files.
3. Real test: the owner uses the flow from the web app on real footage and confirms it works. What did not pass the real test ships as experimental, never as stable.
4. Every feature that passed the real test has an automated regression test on synthetic data that fixes its behaviour.
5. Release notes in [`CHANGELOG.md`](../CHANGELOG.md): new, stable, experimental, known problems.
6. A coding agent writes "What's new" for the release page: 3 to 5 plain sentences for the people who use the app, no technical terms. The owner reads it before tagging.
7. Tag `vX.Y.Z` on `master` and a GitHub Release. A tag is never moved or deleted. Pushing the tag runs `.github/workflows/release.yml`, which creates the release page from the "What's new" text and the `CHANGELOG.md` and `RELEASES.md` sections of that version (no build artefacts).

## After a version

8. The stable list lives in [`RELEASES.md`](RELEASES.md). An agent changes a stable tool only in a lot that names it, with the owner's approval; the reviewer flags any unannounced change to a stable file.
9. A fix to a released version is a new patch version (`vX.Y.Z+1`), never a change to the old one.
10. The pilot host runs tags only; it is updated with the update script, which rolls back to the previous tag when the smoke test after the update fails.
11. Publishing the release triggers `.github/workflows/deploy.yml`: one job per pilot container in the `PILOT_RUNNERS` repository variable, each running `deploy/update.sh <tag>` on its own self-hosted runner. Watch the run in the Actions tab; `update.sh` rolls back to the previous tag when the smoke test fails. A release created with the workflow token does not start `deploy.yml` by itself, so `release.yml` starts it explicitly with `gh workflow run deploy.yml -f tag=$TAG` (permissions `actions: write`).

## Versioning

`0.x.y` while the app is a pilot; minor for new features, patch for fixes; `1.0.0` only when the holiday flow is stable for other people.

## Guards that enforce it

- The pre-commit hook (public check).
- CI on every push (tests and public check).
- Branch protection on `master` (merge only with green CI, set by the owner on GitHub).
- The stable-files check (`scripts/check_stable.py`, CI on pull requests): a diff that touches a file in the `stable-files` block of `docs/RELEASES.md` fails unless a commit in the range carries the trailer `Stable-change: approved`. Before the first tag (the latest version is a candidate) nothing is enforced.
