---
name: yt-edit-drop
description: Remove shots from a finished edit and render it again as a new version — takes the edit of one version, drops the shots named by their number on the edit timeline, renders the preview again from the proxies and keeps the result as the next version, with no model call. Use when the user asks to remove a shot from a finished edit, drop a bad shot, cut a shot out of a version, or make a new version without one shot. Trigger phrases - "scoate un cadru din montaj", "taie un shot din edit", "elimină cadrul 7", "fă o versiune fără cadrul X", "remove a shot from the edit", "drop shot 7", "new version without that shot". Not for building the edit (that is `director`/`llm_director`), not for rendering alone (that is `render`) and not for the phone bin icon (that is `serve.py`, `POST /<project>/drop-shot`).
compatibility: Run from the repository root. Needs the repo .venv and the workspace's `versions/` folder. No model is called, so llm_cost_usd is always 0.0. Reads the base version's edit.json and writes a new version; the originals are never touched.
---

# yt-edit-drop

Remove shots from a finished edit and render it again as a new version. It takes the edit of one version (the latest with a video by default), removes the shots named by their number on the edit timeline (1 is the first shot), renders the preview again from the proxies and keeps the result as the next version. Nothing else changes: no model call, the order and cuts of the other shots stay.

## Command

```bash
.venv/bin/python scripts/edit_drop.py --workspace <workspaces>/<project> --drop 7
.venv/bin/python scripts/edit_drop.py --workspace <workspaces>/<project> --drop 2 --drop 7 --version v002 --json
.venv/bin/python scripts/edit_drop.py --workspace <workspaces>/<project> --drop 7 --dry-run
```

- `--workspace DIR` is required: the project workspace with `versions/`.
- `--drop N` is required and repeatable: the shot number on the edit timeline to remove (1 = first).
- `--version vNNN` starts from that version instead of the latest one with a video.
- `--dry-run` says what would be removed and writes nothing.
- `--json` prints one JSON summary on stdout.

## JSON fields

`{"tool": "scripts/edit_drop.py", "ok": true, "base": "v001", "removed": ["<shot id>", ...], "note": "removed shot 7 (clip.mp4 12.0-14.0 s) of v001", "shots_left": 12, "version": "v002", "dry_run": false, "wall_time_s": 0.0, "llm_cost_usd": 0.0}`

`version` is `null` on a dry run. A failure prints one `{"ok": false, "error": "...", "exit_code": N}` object on stdout and the human `[error]` line on stderr.

## Exit codes

`0` done; `2` usage or input error (nothing written: bad shot number, no version, no version with a video); `3` the render failed (the workspace is left as it was).

## Notes

- The render runs from a temporary edit file; `workspace/edit.json` is replaced only after the render succeeded, so a failed render leaves the workspace unchanged.
- The new version is written by `common.versions.snapshot`; the base version is never modified.
- The web app's Shots-tab bin icon calls this tool through `POST /<project>/drop-shot`.
