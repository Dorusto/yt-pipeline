# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Beats: `beats.py` places the beat grid by dynamic-programming beat tracking (Ellis 2007) after a fine best-phase refinement of the coarse period, so the grid follows a drifting tempo instead of a rigid period; the `beats.json` shape and the CLI are unchanged.
- Web app: the Watch tab no longer shows the "Compare with v1 v2 …" row (the `?with=` side-by-side view stays), and the text after Yes reads "Shot N is getting X s longer/shorter, new version in about 15 s...".
- Director: for a chronological brief the repairs no longer break the time order — after a repair the edit and its plan are re-sorted by capture/source time before the beat snap, so a swapped-in top star lands at its place in the day.
- Director: after a chronological re-sort every slot's transition is set again (first cut, the rest crossfade, hard cuts with a beat grid), so a re-sorted edit no longer keeps a cut in the middle.
- Web app: the project page no longer shows the director reviewer box; the verdict stays in `edit.json`.
- Web app: the Watch tab no longer links the Why?/Shots pages (they stay reachable by URL) and drops the clip/photo colour legend; `config.yaml.example` uses neutral `channel.fixed` placeholders.

## [0.2.0] - 2026-10-24

yt-pipeline v0.2.0 is the first release with the publish flow and the phone editing
controls. On the project page you can now make a shot a second longer or shorter,
and the director cuts on the beat of the music. The publish kit is its own step, so
changing only the thumbnail no longer re-runs the whole chain, and the metadata
prompt writes better titles and descriptions. The Costs page shows donations and
warns before a run that would pass your model credit, and the app can run on a
friend's own instance.

### Added

- Publish: `publish_kit.py` is the manual-upload kit as its own standalone tool — it reads the chain's existing outputs (`metadata.json`, the RO/EN SRTs, the thumbnail and the final video) and can be re-run alone after changing only the thumbnail, without re-running or overwriting any other step.
- Publish: `thumbnail_concepts.py` writes `thumbnail_concepts.json` and a Romanian `thumbnail_concepts.md` from the confirmed title, the context note and the corrected `.srt` (2-3 concepts, model role `metadata`), and `thumbnail.py --concepts`/`--pick` reads them directly.
- Publish: `publish.py` gains a `verify` step between corrections and the human-review stop — it compares the corrected SRT's end time and file date with the final video and stops with a clear message (exit 2) before translation, metadata and the kit when they no longer match.
- Subtitles: a corrections line `*wrong|correct` also fixes the capitalised variant (first letter only), and `correct_srt.py --capitalised` turns the variant on for every line; a pattern that already starts with a capital letter gets no variant.
- Costs: `costs.py import-donations --from <donations.csv>` imports donations from a CSV export (required `date`, `amount`; optional `note`, `id`), idempotent by `source`, skipping bad rows with a warning.
- Web app: the Upload page shows a low-credit warning banner when the OpenRouter key's remaining credit is under `limits.credit_warn_usd`, before a run that would pass the key limit.
- Repo automations: Dependabot opens one grouped weekly pull request per ecosystem (Python requirements and GitHub Actions), checked by CI like any pull request; `AGENTS.md` is the shared instruction file for every coding agent, pointing to `docs/RULES.md`, the skills and the topic files in `docs/agents/` (code style, testing, conventions), with `CLAUDE.md` pointing to it.
- Web app: a shot on the strip can be made longer or shorter (a "− 1 s" / "+ 1 s" control next to the bin icon); presses on the same shot add up into one running total, so `+1 +1 +1` asks to make the shot 3 s longer and Yes submits that total as the delta, rendered as one new version with `edit_drop.py --trim` and no model call.
- Publish: `metadata.py` gains `--title` — the chosen title is handed to the model in a `## Chosen title` section and used verbatim as the first title.
- FilmCraft: `colour.py` applies a basic colour recipe to the camera clips of a project — one Lumetri Basic Correction per recording session (white balance, exposure, contrast), measured on a few frames, on a copy unless `--in-place`.
- Repo checks: `tests/test_no_duplicate_helpers.py`, `tests/test_tool_contract.py` and `tests/test_docs_in_step.py` make the rules a program can check fail in the suite before review (no new duplicated helper, every tool answers `--help` and reports its summary fields, every skill names an existing tool and every tool is named), and `docs/agents/rules-checked.md` records which rules a test checks and which depend on review alone.

### Changed

- Publish: the metadata prompt is fixed (the chosen title first, no invented links or repos, a plain first-person pinned comment, each description in its own language, English chapter titles) and every channel-specific wording string moved out of `scripts/metadata.py` and the prompts into `channel.fixed` in the config, one string per language and omitted when missing; `thumbnail_concepts.py` hands `channel.fixed.thumbnail_formula` to the model and `publish_kit.py` uses a chapter's `title_en` for the EN chapter lines.
- Publish: `publish.py --kit` is orchestration only — it calls `publish_kit.py` as its last step, and the kit links the final video (hard link, else symlink, else only its path in `studio.md`) instead of copying it, so the disk no longer holds two copies.
- Publish: `metadata.py` and `prompts/metadata.md` no longer carry the thumbnail section — the concepts live in their own tool and file, and `metadata.md` keeps only what is pasted into Studio.
- Watchdog: no per-lot flag — the alert label comes from the newest task brief in `.opencode/tasks/`, the two stop rules are no commit for N hours and one hard daily spend ceiling, and the stop notification reports the lot's cost.
- Cleanup lot (V020N lot n1): a failed ffprobe is `EXIT_RUNTIME` in `kdenlive_from_fragments.py` and `render.py`; `serve.py` and `recap_meta.py` share `earliest_creation_date`, `serve.py` writes feedback through `common.feedback`, `costs.py totals` and the Costs page share one `compute_totals` call, `upkeep_percent()`/`usd_to_eur()` raise a clear `ConfigError`, `common.openrouter` no longer pulls `openai`/`filelock`, `pytest-timeout` (120 s per test) is in the test requirements and CI, and `release.yml` starts `deploy.yml` after it creates the release.
- Docs: the README is now a one-screen front door (what it is, who it is for, a quickstart, where to go next); the tool-by-tool reference moved to `docs/guides/tools.md`, and the pilot host and costs sections link to their guides.
- Cleanup lot (V020N lot n15): the duplicated helpers moved into scripts/common/ (SRT parsing, read_text, now_iso, parse_child_json, format_size, the FilmCraft sequence/track/cli helpers, the minimum shot length, KIT_DIR_NAME, the credit helper and the drop/trim route body), and the docs drift fixed.
- Cleanup: the `--help` guard copied in the four legacy SRT tools moved into one helper in `scripts/common/cli.py`.

### Fixed

- Render: cut positions are rounded on the timeline (the cumulative time to the nearest frame, each clip's frame count the difference of the rounded cut positions), so a cut no longer drifts later and later across the edit.
- Reviewer fixes (V020N lot n16): `thumbnail_concepts.py` reports a model answer with an extra key as `EXIT_RUNTIME` (3) instead of a usage error; `costs.py` reads a lone thousands dot (`1.000` → 1000); `graphics_plan.py` keeps its unquoted context-file message; the Files tab uses the shared `format_size_short`; the four legacy SRT tools (`analyze_srt.py`, `correct_srt.py`, `daily_summary_srt.py`, `translate_srt.py`) answer `--help` with exit 0; the owner's name in the graphics examples becomes a placeholder.
- Reviewer fixes (V020N lot n14): `costs.py` accepts a decimal comma, a thousands dot and a UTF-8 BOM on a donations CSV header; `correct_srt.py` counts the capitalised variant in `Termeni înlocuiți` and a correction line with an empty wrong part is a usage error; `metadata.py` rejects an empty `--title` and the EN chapters section falls back to the RO title when `title_en` is missing; an unknown `channel.fixed` part name is a config error naming the key; `thumbnail_concepts.py` reports a wrong-shape model answer as `EXIT_RUNTIME`; `publish_kit.py` reports a failure as one `{"ok": false, ...}` object and its `--json` `output` is `publish-kit/studio.md`; `colour.py` rejects `--target-luma`/`--target-contrast` outside `(0, 1]` as usage errors; `llm_director.py` falls back to the nearest beat under the slot cap when the downbeat is over it; `.github/dependabot.yml` drops the `time`/`timezone` the default schedule does not need.
- The LLM client retries once when a model answer is empty or not valid JSON, charging both attempts to the budget; `metadata.py` uses it.
- Music: in the holiday flow a cut that starts a new structure beat now falls back to the nearest beat when the nearest downbeat is outside the snap window, so every cut lands on a beat.

## [0.1.2] - 2026-10-23

The Costs page no longer says "Paid" before you have made any edit: it shows 0,00 until the AI models are used. The model credit line now uses the same currency as the rest of the page.

### Fixed

- Web app: on a friend's instance with no model spend yet the headline shows the zero amount and "Your edits have not used any AI models yet" instead of "Paid".
- Web app: the "Model credit" line uses the page currency (euro with the configured rate) and the decimal comma, like every other amount.

## [0.1.1] - 2026-10-23

The Costs page on a friend's instance now starts with how to help cover the costs, then shows what your own edits have cost in AI models and what is still open, in red until it is paid back. The project's own costs follow, smaller and voluntary, and the donations come last.

### Changed

- Web app: on an instance with `costs.project_totals_url` the Costs page leads with the donation buttons, then the instance's model cost to cover (the OpenRouter key usage, or the per-clip spend, minus the donations in the instance's own ledger, with the key's credit limit), then the owner instance's cost lines as voluntary project costs, the per-clip table and the donation list. The owner's page is unchanged.

## [0.1.0] - 2026-10-23

yt-pipeline turns the clips from a trip into a short video. Upload your clips and photos from your phone or laptop, write a few words about the day, and the AI director makes the edit and explains its choices. You can leave a shot out, ask for another version, or ask the director what it would change before it starts. Every edit comes with a title, a description and hashtags ready to copy. The Costs page shows what the project costs and how to support it.

### Added

- Web app: one visual direction for every page — a shared token set (colour, type, spacing, radii) with a light and dark theme, a consistent button, field, strip, notice and tab-bar style, and a header slot for the logo.
- Web app: three flat SVG logo marks (assets/brand/), a GET /brand chooser, the brand.logo setting and the chosen mark as the SVG favicon and in the top bar.
- Web app: upload with progress and drag and drop, a run notice with live total time and the time of each step, and the model cost of the running edit and of each version.
- Mode B finishing: `finish_pip.py` places the camera picture-in-picture over the screen spans with an eased entry and exit.
- Music: the library path comes from a flag, an environment variable or the config, and `music_pick.py --list` reports each track's licence status.
- Shorts: `shorts_rank.py` writes standalone candidates with titles and descriptions.
- Costs: `costs.py` keeps a SQLite ledger of model jobs, build-agent spend, fixed monthly costs, investments and donations, with idempotent imports and a `totals` view.
- Costs: `costs.py import-opencode` imports build-agent spend per day from opencode's session database, only for this project's folders (`--db`, repeatable `--match`), idempotent by date.
- Web app: a read-only Costs page (per-clip model cost, ledger totals, donation link) and a feedback/requests form that appends one JSON line to the requests file.
- Recap metadata: `recap_meta.py` writes `recap_meta.json` (title, description, hashtags) from `edit.json`, the Recap tab shows it with a Copy button per field, and each version keeps its own text.
- Web app: four optional brief facts (place, when, who, occasion) on the Upload and Re-edit forms and in the Recap tab's Rewrite button, stored as `brief_facts.json` in the inbox source and the workspace and read as facts by the director prompt and `recap_meta.py`; the Rewrite button re-runs `recap_meta.py --force` for the shown version.
- Web app: an optional upload consent checkbox, a per-project delete (bin icon, removes the workspace and its inbox source), per-instance feature switches (`--features` / `YT_PIPELINE_FEATURES` / `web.features`, enforced with a 404) and brief hint chips with a multi-line example placeholder.
- Pilot host: `deploy/update.sh` updates a container to a released tag with a smoke test and a rollback, `deploy/yt-pipeline-web.service` is the systemd unit template, and `docs/guides/pilot-host.md` describes one container per person.
- Releasing: `scripts/check_stable.py` and the `stable-check` CI job fail a pull request that changes a file in the `stable-files` block of `docs/RELEASES.md` without the `Stable-change: approved` trailer.
- Web app: per-instance storage and model-credit limits on the Upload and Costs pages, and a shared project-costs view (`GET /costs.json`).
- Web app: the project page shows the director's reviewer verdict per version, and the Re-edit box has an "Ask the director" button that shows how the director reads a request with a cost/time estimate before a new version is made (llm_director.py --interpret-feedback).
- Web app: a Files tab that lists every clip and photo of a project, adds more files into the inbox source and leaves a file out (reversibly, `excluded.json`); every station skips the left-out names.
- Deploy: `.github/workflows/deploy.yml` deploys a published release (or a manual dispatch with a tag) to every pilot container in the `PILOT_RUNNERS` repository variable, each running `deploy/update.sh <tag>` on its own self-hosted runner.
- Web app: a `thumbs` station (`scripts/thumbs.py`) writes `thumbs/poster.jpg` (30 % of the recap) and `thumbs/shot-N.jpg` (the middle frame of each slot) after the render; the Projects page shows poster cards and the shot strip shows each shot's thumbnail.
- Release: `.github/workflows/release.yml` creates the GitHub Release from the version's `CHANGELOG.md` and `RELEASES.md` sections when a `v*` tag is pushed.

### Changed

- One exit-code table for every tool and one structured error object for every `--json` tool.
- No tool depends on one person's setup: settings come from a flag, an environment variable or the config, and docs use placeholders.
- Shared overlap math in `scripts/common/overlap.py`.
- Docs: the shaky review policy is the default (long shaky runs reach the director with their shake defect; `YT_PIPELINE_SHAKY=drop` restores the old upgrade), the `yt-edit-drop` skill, the `drop-shot` write route and the donation-links wording.
- The Why? and Shots pages now open with a plain-language summary and collapsed, grouped sections, and the two tabs moved under a Details group.
- Web app: the "editing room" redesign — a warm charcoal base with an amber accent for primary actions and teal only for clip blocks, one token set in `scripts/common/theme.py` shared by every page, and a footage-forward layout (poster cards, the player poster, shot thumbnails).
- Shared helpers extracted into `scripts/common/` (`jsonio`, `git`, `openrouter`, `sizes`, `runlog`, `feedback`, `html`, plus `used_shot_keys`/`REASON_GROUPS`/`probe_duration`/`extract_frame`); the web app's shotlog grouping is regenerated from `index.py` instead of a JavaScript overlay; new `yt-thumbs` skill.
- Review fixes (V010 lot fix2b): `recap_meta.py` reads the place from the brief in plain words and its limits from the schema, `check_stable.py` reads the highest released version, `storage_gb` rejects a non-positive value, `brand.logo` is validated once, `edit_drop.py` regenerates the thumbnails and `recap.py` checks every thumbnail, the web app shares one bin icon and one Yes/No helper, refuses the recap artifacts with the recap part off, disables Start until consent is ticked, caches a failed credit/totals fetch for two minutes with a 3 s timeout, shows the filming date on the project card and builds its fact fields from `FACT_LABELS`; `deploy/update.sh` takes the smoke URL from `YT_PIPELINE_SMOKE_URL` and installs with `uv pip` when available.

### Fixed

- `finish_pip.py` reports the state it leaves behind and clears the animate marker before writing the steps file.
- `shorts_rank.py` keeps candidates inside the edit after the 60 s intro.
