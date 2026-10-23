---
name: yt-edit-drop
description: Remove shots from a finished edit or make one longer or shorter, and render it again as a new version — takes the edit of one version, drops the shots named by their number on the edit timeline or changes a shot's length by ±1 s within its source clip, renders the preview again from the proxies and keeps the result as the next version, with no model call. Use when the user asks to remove a shot from a finished edit, drop a bad shot, cut a shot out of a version, make a new version without one shot, make a shot longer or shorter, or trim a shot's length. Trigger phrases - "scoate un cadru din montaj", "taie un shot din edit", "elimină cadrul 7", "fă o versiune fără cadrul X", "fă cadrul 3 mai lung", "scurtează cadrul 3", "remove a shot from the edit", "drop shot 7", "make shot 3 longer", "trim shot 3", "new version without that shot". Not for building the edit (that is `director`/`llm_director`), not for rendering alone (that is `render`) and not for the phone strip controls (that is `serve.py`, `POST /<project>/drop-shot` and `POST /<project>/trim-shot`).
compatibility: Run from the repository root. Needs the repo .venv and the workspace's `versions/` folder. No model is called, so llm_cost_usd is always 0.0. Reads the base version's edit.json and writes a new version; the originals are never touched.
---

# yt-edit-drop

Remove shots from a finished edit or change a shot's length, and render it again as a new version. It takes the edit of one version (the latest with a video by default), removes the shots named by their number on the edit timeline (1 is the first shot) or changes a shot's length by a signed number of seconds, renders the preview again from the proxies and keeps the result as the next version. Nothing else changes: no model call, the order and cuts of the other shots stay.

## Command

```bash
.venv/bin/python scripts/edit_drop.py --workspace <workspaces>/<project> --drop 7
.venv/bin/python scripts/edit_drop.py --workspace <workspaces>/<project> --drop 2 --drop 7 --version v002 --json
.venv/bin/python scripts/edit_drop.py --workspace <workspaces>/<project> --drop 7 --dry-run
.venv/bin/python scripts/edit_drop.py --workspace <workspaces>/<project> --trim 3:+1
.venv/bin/python scripts/edit_drop.py --workspace <workspaces>/<project> --trim 3:-1 --version v002 --json
```

- `--workspace DIR` is required: the project workspace with `versions/`.
- `--drop N` is repeatable: the shot number on the edit timeline to remove (1 = first).
- `--trim N:DELTA` is repeatable: change shot N's length by DELTA seconds, e.g. `3:+1` or `3:-1`.
- Exactly one of `--drop` / `--trim` is required; giving both (or neither) is a usage error.
- `--version vNNN` starts from that version instead of the latest one with a video.
- `--dry-run` says what would change and writes nothing.
- `--json` prints one JSON summary on stdout.

## Trim rules

- A longer shot (`DELTA > 0`) extends its out edge up to the source clip's duration (`sources[].duration_s` for the entry whose `file` equals the slot's `clip`), then takes the rest from the in edge down to 0. An unknown clip duration or a shot that already uses the whole clip is a usage error.
- A shorter shot (`DELTA < 0`) trims the out edge by `|DELTA|`, with a floor of `in_s + 1.0 s`. A shot already at the floor is a usage error.
- A photo slot cannot be trimmed; a change that cannot move is a usage error.
- The new `in_s` / `out_s` are rounded to 3 decimals.

## JSON fields

`{"tool": "scripts/edit_drop.py", "ok": true, "base": "v001", "removed": ["<shot id>", ...], "trimmed": [{"shot": 3, "delta": 1.0, "in_s": 2.0, "out_s": 5.0}], "note": "removed shot 7 (clip.mp4 12.0-14.0 s) of v001", "shots_left": 12, "version": "v002", "dry_run": false, "wall_time_s": 0.0, "llm_cost_usd": 0.0}`

`removed` stays `[]` for a trim and `trimmed` stays `[]` for a drop. `version` is `null` on a dry run. A failure prints one `{"ok": false, "error": "...", "exit_code": N}` object on stdout and the human `[error]` line on stderr.

## Exit codes

`0` done; `2` usage or input error (nothing written: bad shot number, no version, no version with a video, a trim that cannot move); `3` the render failed (the workspace is left as it was).

## Notes

- The render runs from a temporary edit file; `workspace/edit.json` is replaced only after the render succeeded, so a failed render leaves the workspace unchanged.
- After the render the tool regenerates the thumbnails (`thumbs.py`); a thumbnail failure is a warning, not a failure of the run.
- The new version is written by `common.versions.snapshot`; the base version is never modified.
- The web app's Shots-tab bin icon calls this tool through `POST /<project>/drop-shot`; the strip's "− 1 s" / "+ 1 s" control calls it through `POST /<project>/trim-shot`.
