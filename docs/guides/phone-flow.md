---
title: Phone flow — film on the phone, recap on the LXC, watch on the phone
date: 2026-10-14
---

# Phone flow — film on the phone, recap on the LXC, watch on the phone

Film on the phone, get the clips onto the LXC, open one link and watch the
recap. **Two ways to get the clips over** — pick one:

1. **The web app (no setup).** Open `http://<agent-host>:8091/upload` in the
   phone browser, pick the clips and photos, type the project folder name and
   the brief, press **Start**. The files stream into `<inbox>/<folder>/`
   and `recap.py` starts in the background; the Projects page shows the
   status. This is the path the demo uses.
2. **Syncthing (optional, automatic).** Syncthing copies the camera folder
   into `<inbox>/`; `inbox.py` waits until the inbox is quiet, groups the
   clips per day and runs `recap.py` per day. Use it when you want the phone
   to sync by itself without opening a page.

Either way, when a recap finishes `recap.py` posts an ntfy message with a
preview link and you open that link in the phone browser over the tailnet.

The steps below are Doru's to run on his machines; the repo only ships the tools
and these instructions.

## What runs where

| Phone | LXC `<agent-host>` |
|:---|:---|
| Browser (Upload page, preview link, Re-edit) | `serve.py` on port 8091 (projects, upload, recap/why/shots) |
| ntfy app (subscribed to the topic) | ntfy server on port 8090 |
| Syncthing (send-only camera folder) — *optional* | Syncthing (receive-only → `<inbox>`) — *optional* |
|  | `inbox.py` (watches the inbox, runs `recap.py`) — *optional* |
|  | `recap.py` (analysis → edit → 720p MP4) |

## 0. LXC: the web app (the only step that is required)

```bash
cd <repo>
.venv/bin/python scripts/serve.py --workspaces <workspaces> --inbox <inbox>
```

Keep it running (see `deploy/yt-pipeline-web.service` and
[`pilot-host.md`](pilot-host.md)). Then from the
phone browser: **Upload** → pick files → folder name + brief → **Start**. The
project appears on **Projects** with a `running` badge and turns `done` when
the recap is ready. The project page's tabs are **Recap**, **Files**, **Cuts**
and **Shorts**; **Re-edit**
re-runs only the edit with your options and a feedback note, and keeps the
earlier versions (chips v1, v2, …). Any version except the
latest can be deleted from the **Recap** tab (its folder is removed for
good).

## 1. (Optional) Phone: install Syncthing

Android: *Syncthing-Fork* (F-Droid or Play Store) or the official *Syncthing*
app. iOS: *Möbius Sync* or *Synctrain*. Open it and note the device ID (you
paste it on the LXC in step 2). Keep the phone on the same network as the LXC
while pairing.

## 2. (Optional) LXC: install Syncthing

Doru runs these on the LXC:

```bash
sudo apt update && sudo apt install -y syncthing
systemctl --user enable --now syncthing
```

Open the web UI on `http://127.0.0.1:8384` (local only), add the phone as a
device (scan its QR code or paste its device ID) and accept the folder share.
If `systemctl --user` is unavailable in the LXC, run `syncthing` inside a tmux
session instead.

## 3. Share the camera folder

On the phone share the camera folder (e.g. `DCIM/Camera`) with the LXC. On the
LXC set that folder to **receive only** and point it at `<inbox>`. On the
phone set it to **send only**, so nothing the pipeline does can ever delete
clips from the phone. Optionally add ignore patterns (`.thumbnails`, `.*`).

## 4. Start the watcher and the server

Doru runs these on the LXC, in two terminals or two tmux windows:

```bash
cd <repo>
export OPENROUTER_API_KEY=...        # never commit it
.venv/bin/python scripts/inbox.py --inbox <inbox> --projects <projects> \
    --quiet-minutes 5 --notify --preview-base http://<agent-host>:8091
.venv/bin/python scripts/serve.py --workspaces <workspaces> --bind <agent-host> --port 8091
```

`--once` makes `inbox.py` do a single scan and exit instead of watching. Every
flag is documented in each tool's `--help`, which is the source of truth.

<details>
<summary>Optional: systemd user units (Doru's step — the repo does not touch systemd)</summary>

Create two files under `~/.config/systemd/user/` and enable them yourself:

```ini
# ~/.config/systemd/user/yt-inbox.service
[Unit]
Description=yt-pipeline inbox watcher
[Service]
WorkingDirectory=<repo>
Environment=OPENROUTER_API_KEY=...
ExecStart=<repo>/.venv/bin/python scripts/inbox.py \
    --inbox <inbox> --projects <projects> --notify \
    --preview-base http://<agent-host>:8091
Restart=on-failure
[Install]
WantedBy=default.target
```

```ini
# ~/.config/systemd/user/yt-serve.service
[Unit]
Description=yt-pipeline preview server
[Service]
WorkingDirectory=<repo>
ExecStart=<repo>/.venv/bin/python scripts/serve.py \
    --workspaces <workspaces> --bind <agent-host> --port 8091
Restart=on-failure
[Install]
WantedBy=default.target
```

Then `systemctl --user daemon-reload && systemctl --user enable --now yt-inbox yt-serve`.
Creating and enabling these units is Doru's step.

</details>

## 5. Phone: subscribe to ntfy

Install the *ntfy* app (Android Play / iOS App Store), add the self-hosted
server `<ntfy-url>` and subscribe to the topic in `NTFY_URL`
(default `opencode-doru`). When a recap finishes the message title is
`Recap ready (720p)` and the body carries the preview link.

## 6. Give Hermes the yt-recap skill

Copy `.claude/skills/yt-recap/` into Hermes' skills folder, or start Hermes with
this repo as its working directory (or the `--add-dir` equivalent). Hermes needs
the repo path, `.venv/bin/python`, `OPENROUTER_API_KEY`, `NTFY_URL` and network
access to `<agent-host>`. Then, in Telegram, send one of the phrases in the skill
("fă-mi un recap din clipurile de azi") and confirm Hermes answers with the
preview link. Enabling and maintaining Hermes itself is Doru's step.

## 7. Test the loop end to end

Copy a few clips into `<inbox>/2026-10-14/` (a `YYYY-MM-DD` folder names
the project day), wait `--quiet-minutes`, check the ntfy message and open the
preview link on the phone. A recap costs a few cents per run, capped by
`--budget-usd`.

## Troubleshooting

| Symptom | Check |
|:---|:---|
| No ntfy message | Is `serve.py` running? Is the phone on the tailnet? |
| Recap not starting | Inbox not quiet yet — lower `--quiet-minutes` or use `--once` |
| Link does not open | Phone not on Tailscale; `serve.py` binds the tailnet address only |
| Wrong project day | A `YYYY-MM-DD` folder wins over the filename date |
| Budget reached | See `llm_spend.json`; raise `--budget-usd` |

## Privacy

The preview URL is reachable only inside the tailnet (Tailscale), never from the
public internet — `serve.py` refuses the wildcard bind for that reason. The
synced inbox clips are read-only for the pipeline: it never modifies or deletes
them. Footage leaves the homelab only as frames to the configured vision model.
