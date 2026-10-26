# S54 — FilmCraft export spike (lot 1)

Draft report for the owner to confirm. Scope: can `edit.json` be exported to an
editable FilmCraft `.fcproj` without FilmCraft installed at export time?

## What works

- `export_filmcraft.py` writes an `edit.json` edit list to a `.fcproj` project
  (route `json`) using the exact envelope, item, track and clip shapes a real
  FilmCraft save produces. No FilmCraft is needed at export time; the tool is
  unit-testable and runs in CI.
- Verified by the architect on 2026-10-21:
  `export_filmcraft.py tests/fixtures/filmcraft/edit.json --out proj.fcproj
  --graphics-plan tests/fixtures/filmcraft/graphics_plan.json --graphics-dir DIR
  --json` → `slots_placed: 2, photos_placed: 1, overlays_placed: 1,
  markers: {green: 3, rose: 0, mango: 1}, missing_media: 3 (warnings),
  route: "json"`.
- Round trip: `subs_from_edit.py --from-edit` vs `--fcproj ... --sequence Main`
  on the same word JSONs → **identical** `_words.json` (4 words, 3 cues) and
  equal SRT modulo stem.
- Headless open: `filmcraft-cli inspect project --project proj.fcproj` → exit 0;
  root children show `camera A.mp4` (Movie), `screen B.mp4` (Movie), `pic.jpg`
  (Still Image), `Main` (Sequence), `g1.mov` (Movie).
- `.venv/bin/python -m pytest tests/ -q` → **1399 passed** (19 of them the new
  export tests); `scripts/check_public.py` over the new files → `ok`.
- Marker shape confirmed live: `{id, start, duration, name, comment,
  kind: "Comment", color}` with `Green`/`Rose`/`Mango` accepted.
- Media is referenced by path, never embedded; a missing path with
  `offline: false` opens fine (offline media), `offline: true` is refused.
  Ticks: 254 016 000 000 per second.
- **Marker interpretation (for the owner to confirm):** "a marker per edit decision
  that has a reason, Green sections / Rose to redo / Mango animation" = one
  marker per used slot with a reason (Green) at the slot's timeline in-point,
  REPLACED by a Rose marker when the slot is flagged for a human check
  (`centred: true` or speed ramp — "to redo"), plus one Mango marker per placed
  overlay ("animation"). Rejected `decisions[]` have no timeline position and
  get no marker. `fcproj_markers.py` already maps the colours into
  `markers.json`/`todo.md` (Rose = the redo list).

## What breaks / mocked

1. Ken Burns / Motion keyframes for photo slots are not exported (warning).
2. `schema_version` 15 is hardcoded — the main churn risk.
3. The tool lays slots back-to-back at real time while `subs_from_edit.py
   --from-edit` applies crossfade overlap, so an edit with crossfades would
   diverge in the round trip; the test fixture is cut-only so both agree.
4. `python3` (system) has no `jsonschema`; runs need `.venv/bin/python`.
5. No real footage and no real FilmCraft GUI open was tested here — headless
   `inspect` only.

## FilmCraft version

FilmCraft **0.9.0**, AppImage extracted to `/root/.local/opt/filmcraft/`
(binary `usr/bin/filmcraft-cli`, symlinked to `/root/.local/bin/filmcraft-cli`;
NOT inside the repo). Licence MIT OR Apache-2.0. `filmcraft-cli commands` lists
701 commands.

## Format stability risk

The real `.fcproj` envelope as written by a real save is `"format":
"filmcraft.project"` (dot), `"schema_version": 15` (the docs said 13 — 0.9.0
writes 15), `"generator": "FilmCraft 0.9.0"`, compact JSON, with
`project.next_id` present. The S51 fixture `tests/fixtures/publish/fixture.fcproj`
uses `"format": "filmcraft-project"` (hyphen) + `schema_version: 1` and is
**refused** by the real app (`not a FilmCraft project: format is
'filmcraft-project', expected 'filmcraft.project'`). `subs_from_edit.py` and
`fcproj_markers.py` still parse it; per the brief this is a report finding, not
fixed in this lot. The fixture's item shapes are also thinner than a real save
(missing `interpret/markers/offline` on media, `container` in `info`, full
`settings` blocks on project and sequence, track
`transitions/locked/sync_lock/enabled/muted/solo/channels/volume_db/pan`, clip
`label/speed/enabled/effects`). The hardcoded `schema_version` 15 is the main
churn risk if FilmCraft bumps it.

## Route comparison

- **Route A (write the `.fcproj` JSON directly) won.** It needs no FilmCraft at
  export time (CI has none) and is unit-testable; the exact shapes a real save
  produces are embedded and verified by headless open.
- **Route B (drive `filmcraft-cli`: `file.newProject`, `file.import`,
  `file.newSequence`, `timeline.place`, `markers.add`, `sequence.addTracks`,
  `file.save`)** was tried hands-on and produced a valid project, but
  `file.import` FAILS on a missing media file (`No such file or directory`, no
  item created), which contradicts the tool contract (missing media must be a
  warning, projects open with offline media). Route B stays useful for
  inspecting/round-tripping.

## Recommendation

Adopt route A. Format churn is contained in one module (`export_filmcraft.py`),
and the headless open plus the round trip prove the file is editable. Kdenlive
export stays the fallback until the owner opens a real project on his machine.

## Proposed DECISIONS entry

## [2026-10-21] FilmCraft export writes the .fcproj JSON directly (S54 lot 1)

**Context:** The publish flow needs an editable FilmCraft project from an
`edit.json` edit list. Two routes were possible: write the `.fcproj` JSON
directly, or drive the headless `filmcraft-cli` to build the project. FilmCraft
0.9.0 is not installed in CI, and the tool contract says missing media must be a
warning (projects open with offline media).

**Options:** (A) write the `.fcproj` JSON directly with the exact shapes a real
FilmCraft 0.9.0 save produces; (B) drive `filmcraft-cli` (`file.newProject`,
`file.import`, `file.newSequence`, `timeline.place`, `markers.add`,
`sequence.addTracks`, `file.save`).

**Decision:** A. `scripts/export_filmcraft.py` writes the project JSON (route
`json`) with the real envelope (`format: "filmcraft.project"`,
`schema_version: 15`, `generator`, `project.next_id`), media referenced by path,
Camera 1 / Screen 1 / Audio 1 / Graphics 1 tracks, one Green marker per used
slot with its reason (replaced by a Rose "to redo" marker when the slot is
flagged for a human check), one Mango marker per placed overlay, and an atomic
re-run-safe write. Missing media is a warning. Route B was tried hands-on and
produced a valid project, but `file.import` fails on a missing media file, which
contradicts the contract; it stays useful for inspecting/round-tripping.

**Consequence:** `python3 scripts/export_filmcraft.py edit.json --out
project.fcproj [--graphics-plan FILE] [--graphics-dir DIR] [--sequence NAME]
[--width W] [--height H] [--fps N] [--screen FILE ...] [--json]` produces a
project the real app opens headlessly (`filmcraft-cli inspect`) and that round
trips identically through `subs_from_edit.py`. `schema_version` 15 is hardcoded
and is the main churn risk; Ken Burns keyframes and speed ramps are not exported
(warned). Kdenlive export stays the fallback until a real project is opened in
the GUI.
