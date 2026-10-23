# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Web app: one visual direction for every page — a shared token set (colour, type, spacing, radii) with a light and dark theme, a consistent button, field, strip, notice and tab-bar style, and a header slot for the logo.
- Web app: three flat SVG logo marks (assets/brand/), a GET /brand chooser, the brand.logo setting and the chosen mark as the SVG favicon and in the top bar.
- Web app: upload with progress and drag and drop, a run notice with live total time and the time of each step, and the model cost of the running edit and of each version.
- Mode B finishing: `finish_pip.py` places the camera picture-in-picture over the screen spans with an eased entry and exit.
- Music: the library path comes from a flag, an environment variable or the config, and `music_pick.py --list` reports each track's licence status.
- Shorts: `shorts_rank.py` writes standalone candidates with titles and descriptions.
- Costs: `costs.py` keeps a SQLite ledger of model jobs, build-agent spend, fixed monthly costs, investments and donations, with idempotent imports and a `totals` view.
- Web app: a read-only Costs page (per-clip model cost, ledger totals, donation link) and a feedback/requests form that appends one JSON line to the requests file.
- Recap metadata: `recap_meta.py` writes `recap_meta.json` (title, description, hashtags) from `edit.json`, the Recap tab shows it with a Copy button per field, and each version keeps its own text.
- Web app: an optional upload consent checkbox, a per-project delete (bin icon, removes the workspace and its inbox source), per-instance feature switches (`--features` / `YT_PIPELINE_FEATURES` / `web.features`, enforced with a 404) and brief hint chips with a multi-line example placeholder.
- Pilot host: `deploy/update.sh` updates a container to a released tag with a smoke test and a rollback, `deploy/yt-pipeline-web.service` is the systemd unit template, and `docs/guides/pilot-host.md` describes one container per person.
- Releasing: `scripts/check_stable.py` and the `stable-check` CI job fail a pull request that changes a file in the `stable-files` block of `docs/RELEASES.md` without the `Stable-change: approved` trailer.
- Web app: per-instance storage and model-credit limits on the Upload and Costs pages, and a shared project-costs view (`GET /costs.json`).
- Web app: the project page shows the director's reviewer verdict per version, and the Re-edit box has an "Ask the director" button that shows how the director reads a request with a cost/time estimate before a new version is made (llm_director.py --interpret-feedback).
- Web app: a Files tab that lists every clip and photo of a project, adds more files into the inbox source and leaves a file out (reversibly, `excluded.json`); every station skips the left-out names.
- Deploy: `.github/workflows/deploy.yml` deploys a published release (or a manual dispatch with a tag) to every pilot container in the `PILOT_RUNNERS` repository variable, each running `deploy/update.sh <tag>` on its own self-hosted runner.

### Changed

- One exit-code table for every tool and one structured error object for every `--json` tool.
- No tool depends on one person's setup: settings come from a flag, an environment variable or the config, and docs use placeholders.
- Shared overlap math in `scripts/common/overlap.py`.
- Docs: the shaky review policy is the default (long shaky runs reach the director with their shake defect; `YT_PIPELINE_SHAKY=drop` restores the old upgrade), the `yt-edit-drop` skill, the `drop-shot` write route and the donation-links wording.
- The Why? and Shots pages now open with a plain-language summary and collapsed, grouped sections, and the two tabs moved under a Details group.

### Fixed

- `finish_pip.py` reports the state it leaves behind and clears the animate marker before writing the steps file.
- `shorts_rank.py` keeps candidates inside the edit after the 60 s intro.
