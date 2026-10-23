# RELEASING — how a version is made

Rules for the owner and for every agent. A version is a tag on `master` plus a GitHub Release; the stable list lives in [`RELEASES.md`](RELEASES.md). Plain English, short.

## Before a version

1. The owner merges into `master` everything that goes in; nothing uncommitted.
2. The full test suite passes, with no ignored files.
3. Real test: the owner uses the flow from the web app on real footage and confirms it works. What did not pass the real test ships as experimental, never as stable.
4. Every feature that passed the real test has an automated regression test on synthetic data that fixes its behaviour.
5. Release notes in [`CHANGELOG.md`](../CHANGELOG.md): new, stable, experimental, known problems.
6. Tag `vX.Y.Z` on `master` and a GitHub Release. A tag is never moved or deleted.

## After a version

7. The stable list lives in [`RELEASES.md`](RELEASES.md). An agent changes a stable tool only in a lot that names it, with the owner's approval; the reviewer flags any unannounced change to a stable file.
8. A fix to a released version is a new patch version (`vX.Y.Z+1`), never a change to the old one.
9. The pilot host runs tags only; it is updated with the update script, which rolls back to the previous tag when the smoke test after the update fails.

## Versioning

`0.x.y` while the app is a pilot; minor for new features, patch for fixes; `1.0.0` only when the holiday flow is stable for other people.

## Guards that enforce it

- The pre-commit hook (public check).
- CI on every push (tests and public check).
- Branch protection on `master` (merge only with green CI, set by the owner on GitHub).
- The stable-files check (planned: CI fails when a stable file changes without the approval marker the protocol names).
