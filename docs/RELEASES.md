# RELEASES — released versions and what is stable

One section per version. Per version: the tools and behaviour marked **stable** (passed the real test, has a regression test) and **experimental** (everything else). The protocol is in [`RELEASING.md`](RELEASING.md).

## v0.1.0 (candidate, not released)

**Candidate until the owner confirms the real test.** Nothing here is tagged yet.

**Stable candidate** — the holiday flow in the web app:

- Upload with progress (drag and drop several clips, per-file progress).
- The recap run from the web app.
- The run notice with step times and model cost.
- Versions (each run keeps its edit and renders under `versions/vNNN/`).
- The shaky review policy.

**Experimental** — everything else:

- The publish kit.
- Metadata.
- Thumbnail.
- Shorts.
- The audit changes.
- Costs ledger.
- The Costs page and the feedback/requests form.
- Title, description and hashtags for a recap.
- The brief facts fields and the title rewrite (`brief_facts.json`, `POST /<project>/rewrite-meta`).
- The upload consent checkbox.
- Per-project delete from the project page.
- Per-instance feature switches (`--features` / `YT_PIPELINE_FEATURES` / `web.features`).
- The brief hint chips and the multi-line example placeholder.
- Pilot host install, update and rollback (`deploy/update.sh`, `deploy/yt-pipeline-web.service`, `docs/guides/pilot-host.md`).
- Instance limits (storage cap, model-credit read) and the shared project-costs view (`GET /costs.json`).
- The director's reviewer verdict on the project page and the "Ask the director" reply before a new version (llm_director.py --interpret-feedback, POST /<project>/feedback-reply).
- The Why? and Shots pages open with a summary and collapsed sections, and the two tabs sit under a Details group.
- The Files tab (see, add and leave out the files of a project, `excluded.json`).
- The web app's visual design (the "editing room" look: a warm charcoal base, an amber accent for primary actions, teal only for clip blocks, one token set in `scripts/common/theme.py` with a light and dark theme, the header logo slot).
- The poster thumbnails: the `thumbs` station (`scripts/thumbs.py`) writes `thumbs/poster.jpg` and `thumbs/shot-N.jpg`, the Projects page is a grid of poster cards and the shot strip shows each shot's thumbnail.
- The app logo and favicon (three SVG marks in assets/brand/, GET /brand, brand.logo).
- Continuous deployment to the pilot containers (`.github/workflows/deploy.yml`, the `PILOT_RUNNERS` repository variable).

**Stable files** — the files behind the stable candidate above. A change to one
needs the `Stable-change: approved` trailer on a commit in the range (see
[`RELEASING.md`](RELEASING.md)); the check is `scripts/check_stable.py`:

```stable-files
scripts/serve.py
scripts/recap.py
scripts/common/versions.py
scripts/quality.py
```
