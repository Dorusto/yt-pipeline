# D2-4 — phone flow, repo side: inbox → recap → tailnet preview → ntfy

Branch `d2-4-phone-flow`, four tasks (one commit each): `scripts/serve.py`,
`recap.py --notify`, `scripts/inbox.py` (one fix round: folder brief in farm
mode + quiet watch log), `docs/guides/phone-flow.md` + Telegram phrases.
Acceptance: 5 clips copied (never moved) from `~/media/yt-test/raw` into
`~/media/inbox-test/2026-10-14/`, `inbox.py --once` picked them up as one
project, the recap rendered, `serve.py` lists it and the ntfy link opens.
Phase LLM spend: **$0.043** (budget 1 USD).

## Works (verified in D2-4)

- **`scripts/serve.py`** — read-only HTTP preview, bind `<agent-host>:8091`
  (tailnet address; `0.0.0.0`/`::` refused with an error). Allowlist routes:
  index, one page per project, and only `recap_720p.mp4` / `recap_final.mp4` /
  `shotlog.html` — proxies, `edit.json` and `source/` return 404. Range
  requests return 206 (phone browsers need this to play video). Live: `/`,
  `/2026-10-14/`, preview and shotlog all **200** over `<agent-host>`; traversal
  404, POST 405. 18 tests.
- **`recap.py --notify`** — posts `Recap ready (720p)` / `Recap ready (cached)`
  to ntfy with the preview link `<preview-base>/<workspace>/recap_720p.mp4`;
  a failed run never notifies; a notify error warns but leaves exit 0. Summary
  gains `preview_url` + `notified`. Verified live: the ntfy message arrived on
  the project topic with the exact link that returns 200. 8 new tests.
- **`scripts/inbox.py`** — watches an inbox (read-only), groups clips per day
  (`YYYY-MM-DD` folder wins over the filename date, then mtime), waits for the
  inbox to be quiet (`--quiet-minutes`, default 5), runs `recap.py` with the
  day folder's `brief.txt` or a default brief, one workspace per day under
  `--projects`. Acceptance run: project `2026-10-14`, `brief_source: folder`,
  `recap_720p.mp4` (1280x720, 19.5 s, 4 slots, calm→energetic as briefed) in
  540 s for **$0.043**. Second run: `skipped (nothing new)` at $0. State in
  `<projects>/inbox_state.json`; a new clip, a changed brief or `--force`
  re-invokes recap. 34 tests.
- **`docs/guides/phone-flow.md` + skill phrases** — Doru's step-by-step
  (Syncthing phone send-only → LXC receive-only `~/media/inbox`, watcher +
  server commands, ntfy app, Hermes gets the `yt-recap` skill, troubleshooting,
  privacy). Every system command is labelled as Doru's step — the repo installs
  nothing. The `yt-recap` skill maps the Telegram phrases ("fă-mi un recap din
  clipurile de azi" → `inbox.py --once --notify`, "trimite-mi linkul" → the
  serve.py URL, etc.). Every flag cross-checked against the tools' `--help`.
- **Suite + safety:** 713 tests green, `scripts/check_public.py --all` clean
  (no footage, frames or thumbnails in git). All 21 originals in
  `~/media/yt-test/raw` verified unchanged by md5 before/after.

## Mocked / not verified

- Tests mock `run_recap` / `common.notify.send` and drive `serve.py` in
  threads; only the acceptance run exercised the real chain (one project, five
  clips).
- Syncthing, ntfy app, Hermes and the systemd user units are **not** installed
  here — `docs/guides/phone-flow.md` is written for Doru to run on his
  machines; the guide's commands are untested end-to-end on a real phone.
- The watch loop was tested through SIGINT in tests, not by leaving it running
  for days.

## Doru, check this

1. Read `docs/guides/phone-flow.md` — are the Syncthing + Hermes steps the
   ones you want to run for the demo?
2. Open `http://<agent-host>:8091/2026-10-14/` on the phone (tailnet) — does the
   preview play in the phone browser? (`serve.py` is running now.)
3. Watch `/root/media/projects/2026-10-14/recap_720p.mp4` (19.5 s, 5 clips,
   $0.043) — is this the phone-flow demo quality?
4. The ntfy message `Recap ready (720p)` on topic `opencode-doru` — tap the
   link from the phone: does it open?
5. `~/.config/systemd/user/*.service` sketches in the guide — keep as user
   units, or do you prefer tmux for the demo?
