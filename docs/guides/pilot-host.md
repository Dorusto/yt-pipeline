---
title: Pilot host — one container per person, one released tag
date: 2026-10-23
---

# Pilot host — one container per person, one released tag

Each person (the owner included) gets one small container running the same
released tag. A person reaches only their own container through a Tailscale
node share; the inbox and the workspaces live on a NAS share mounted into the
container. The owner sets up the Tailscale share, the NAS share, the mount and
the env file; `deploy/update.sh` does the rest.

Placeholders in this guide: `<container>`, `<nas-share>`, `<tag>`, `<repo>`,
`<tailnet-ip>`. Nothing here is a real address or a real key.

## What the owner sets up (once per person)

1. **Container.** One small container per person, the same image, the same
   released tag. The container runs `serve.py` (the web app) and the pipeline.
2. **Tailscale node share.** Share only that container's node with the person,
   so they reach `http://<container>:8091/` and nothing else on the tailnet.
3. **NAS share.** One share per person on the NAS, mounted into the container
   at `<nas-share>`. The inbox and the workspaces live there, so the footage
   survives a container rebuild.
4. **Mount.** Mount `<nas-share>` into the container (for example at
   `/srv/yt-pipeline`).
5. **Env file.** `/etc/yt-pipeline/env` (not in the repo, no secrets in git):

   ```ini
   YT_PIPELINE_WORKSPACES=/srv/yt-pipeline/workspaces
   YT_PIPELINE_INBOX=/srv/yt-pipeline/inbox
   YT_PIPELINE_COSTS_DB=/srv/yt-pipeline/costs.db
   YT_PIPELINE_REQUESTS=/srv/yt-pipeline/requests.jsonl
   OPENROUTER_API_KEY=<key>
   ```

6. **Service.** Copy `deploy/yt-pipeline-web.service` to
   `/etc/systemd/system/`, replace `<repo>` with the clone path, then
   `systemctl daemon-reload && systemctl enable --now yt-pipeline-web`.

## What the script does

`deploy/update.sh <tag>` updates the container to a released tag:

1. fetch the tags and check out `<tag>`;
2. install `requirements.txt` (with `requirements-constraints.txt` when it
   exists);
3. run the smoke test: `serve.py --check` and an HTTP GET of `/upload`
   expecting 200;
4. restart the `yt-pipeline-web` service.

On a failed smoke test it checks out the previous tag kept in `.deployed-tag`,
restarts the service and exits non-zero. `--dry-run` prints the steps and
changes nothing.

```bash
cd <repo>
sudo deploy/update.sh <tag>
SMOKE_URL=http://<container>:8091/upload sudo deploy/update.sh <tag>
deploy/update.sh <tag> --dry-run
```

## Storage limit per person

Give each person a quota on their NAS share (for example 100 GB) and a
container disk size that fits the pipeline's temporary files. The upload page
and the costs page show the usage ("34 GB of 100 GB used") and warn over 80 %;
an upload that would pass the limit is refused before any byte is written. The
limit comes from `limits.storage_gb` (`--storage-gb`, else
`YT_PIPELINE_STORAGE_GB`, else the config); with none set no limit is shown.

## One OpenRouter key per instance

Each instance has its own OpenRouter key, with its own credit limit set in
OpenRouter, so one person's runs cannot spend another person's credit. The key
goes in the instance's `/etc/yt-pipeline/env` (`OPENROUTER_API_KEY`), never in
the repo. The upload page and the costs page show the key's remaining credit
("Model credit: $3.20 left of $5.00") and warn under `limits.credit_warn_usd`
(default 1.0); a key with no limit shows "no credit limit set". The costs page
of each instance shows that instance's own spend.

## Project costs on every instance

The owner's instance serves `GET /costs.json` (the totals from
`compute_totals`: per kind, monthly, development, donations, balance; no notes,
no requests). An instance with `costs.project_totals_url`
(`--project-totals-url`, else `YT_PIPELINE_PROJECT_TOTALS_URL`, else the
config) fetches it and its costs page shows two parts: "Project costs
(everyone)" from that URL and "This instance" from its own ledger and projects.
A failed fetch shows the part as unavailable.
