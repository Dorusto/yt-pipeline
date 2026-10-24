# RELEASES — released versions and what is stable

One section per version. Per version: the tools and behaviour marked **stable** (passed the real test, has a regression test) and **experimental** (everything else). The protocol is in [`RELEASING.md`](RELEASING.md).

## v0.2.0 (2026-10-24)

The holiday flow from the phone is easier: on the project page you can make a shot
a second longer or shorter, and the director cuts on the beat of the music. The
publish flow is ready for a real upload: the publish kit is its own step (change
only the thumbnail and re-run just that), the metadata prompt writes better titles
and descriptions, and the subtitles fix the capitalised variants. The Costs page
shows donations and warns before a run that would pass your model credit, and the
app can run on a friend's own instance. The rendered cuts now stay on the beat:
the cut positions are rounded on the timeline, so a cut no longer drifts later
and later across the edit. The director's reviewer box is hidden from the project
page for now; it comes back in plain words with the director chat (#96).

**Stable candidate** — the holiday flow in the web app (as in v0.1.0), plus making
a shot longer or shorter on the project page (the "− 1 s" / "+ 1 s" buttons add up
into one running total, one new version per Yes).

**Experimental** — everything else, as in v0.1.0, plus:

- The publish kit as its own step, the thumbnail concepts and the metadata prompt fixes.
- The colour recipe (`colour.py`) — not tested on a real project.
- The donations import and the low-credit warning on the Upload page.
- The repo checks that make the rules fail in the test suite.

**Stable files** — the files behind the stable candidate above. A change to one
needs the `Stable-change: approved` trailer on a commit in the range (see
[`RELEASING.md`](RELEASING.md)); the check is `scripts/check_stable.py`:

```stable-files
scripts/serve.py
scripts/recap.py
scripts/common/versions.py
scripts/quality.py
```

## v0.1.2 (2026-10-23)

A patch for the Costs page: no "Paid" before any spend, and the credit line in the page currency. Stable and experimental lists as in v0.1.0.

```stable-files
scripts/serve.py
scripts/recap.py
scripts/common/versions.py
scripts/quality.py
```

## v0.1.1 (2026-10-23)

A patch for friends' instances: the Costs page shows their own model cost first and the project's costs as voluntary. Stable and experimental lists as in v0.1.0.

```stable-files
scripts/serve.py
scripts/recap.py
scripts/common/versions.py
scripts/quality.py
```

## v0.1.0 (2026-10-23)

Released on 2026-10-23 after the owner's real test on phone and laptop.

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
- The web app's visual design (the "editing room" look: a warm charcoal base, an amber accent for primary actions, teal only for clip blocks, one token set in `scripts/common/theme.py` with a light and dark theme).
- The poster thumbnails: the `thumbs` station (`scripts/thumbs.py`) writes `thumbs/poster.jpg` and `thumbs/shot-N.jpg`, the Projects page is a grid of poster cards and the shot strip shows each shot's thumbnail.
- The shared helpers extracted from `serve.py` and the tools (`scripts/common/jsonio.py`, `git.py`, `openrouter.py`, `sizes.py`, `runlog.py`, `feedback.py`, `html.py`, plus `used_shot_keys`/`REASON_GROUPS`/`probe_duration`/`extract_frame`), and the `yt-thumbs` skill for `scripts/thumbs.py`.
- `scripts/check_stable.py` has no skill (release tooling, like `scripts/check_public.py`).
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
