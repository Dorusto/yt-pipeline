# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-10-26

### Added

- Director: the shot length limits (`director.shot_limits`, 1.5 to 8 s by default, one row per content type) are in `config.yaml`; the length asked is a guide, the beat and the director's own length come first, and a shot asked longer than the limit stays long only when the director gives an `over_limit_reason`.
- Director: a review prompt (`prompts/director_review.md`) for the notes round, so the review call answers with a review and not with a plan.
- Web app: the Director tab folds the shot-by-shot list and the look-alike list; the Debug tab has no link for now (the page stays at `?tab=debug`).
- Agent entry point: `yt <tool> [args]` runs any tool from any folder, and `yt --help --json` lists the tools as JSON.
- Claude Code plugin: five plugins in one marketplace `yt-pipeline` (`yt-director`, `yt-publish`, `yt-shorts`, `yt-editor`, `yt-admin`), each installable alone; every skill works on its own through `yt <tool>`.
- Typed tools (MCP): each plugin ships `scripts/yt_mcp.py` with `yt_list`, `yt_describe`, `yt_run` and `yt_preview`.
- Director agent mode: `llm_director.py --prepare` writes the director prompt and payload, `--apply` validates `director_plan.json` and runs the editor, and `--prepare --review` writes the pictures and the report and the host agent writes `director_review.json`, so a host agent can direct the edit on its own subscription with no OpenRouter key.
- Director chat in the Director tab: the director's notes in plain words, a message in and a reply out with a cost/time estimate, and a Make-the-new-version button.
- The director is honest about its tools: when it cannot do something it says what it can do instead and the request is logged as a missing tool.
- Per-instance style settings: `channel.style` (`title_style`, `description_order`, `pinned_comment_voice`).
- Learning notebook: a per-instance file of at most 40 lessons, shown at the bottom of the project page; after each new version one cheap model call writes at most 3 lessons.
- Fresh-container smoke test: `deploy/smoke_plugin.sh` and a checklist in `docs/AGENT_SETUP.md`.
- Third-party licences: `NOTICE.md` and `docs/licences.md`.
- Beats: an optional Beat This! downbeat engine (`beats.engine`, `numpy` | `beat_this`, default `numpy`; `--engine` or `YT_PIPELINE_BEATS_ENGINE` overrides it), so the cuts can land on the strongest beat of the bar.
- Release: pushing a tag closes the open issues of the milestone named like the tag and the milestone itself.
- Quality: faces are counted per segment and the whip pattern is marked, and the score uses both — a fast move next to a short hold on people scores low, while a deliberate move is not punished for its shake.
- Director speed: `director.reasoning_effort` (`low`, `medium`, `high` or `off`, default `medium`) and `--reasoning-effort` set how much hidden reasoning the model spends on every `llm_director` call, sent as OpenRouter's `reasoning` body; the spend ledger records the reasoning tokens of each call.
- Web app: a **Director** tab holds the chat with the director and, under it, the collapsible notebook.
- Web app: a **Stop** button on the progress card of a running edit stops the run and its child processes and leaves the previous version as the current one.
- Director: the rule director takes the owner's explicit keys (`--add-shots`/`--drop-shots`, comma-separated and repeatable) and the `--json` summary reports `applied_keys`/`not_applied_keys`; the web app says "The director could not make this edit; a simpler editor made it." and lists which requested changes were applied and which were not when the shown edit is a rule fallback.
- Director rewrite: the code editor `scripts/director_editor.py` turns a `director_plan.json` into `edit.json` and an `editor_report.json`, cutting the windows around the director's moments, on the beat, and never changing the shots.
- Web app: a **Debug** tab (last, muted) shows the shot list, the raw plan, the editor report, the rounds and the model calls.

### Changed

- Director: the film-crew flow writes the music track (`audio.music`) and the ambient level under it (`audio.ambient_under_music_db`) into `edit.json` when they come from flags, and `decisions[]` — one used row per plan shot (the director's purpose and why, the technique done or not) plus the editor's could-not-do items.
- Director: `--speed-ramp` reaches the code editor, which sets `middle_speed` on a move's plain cut (the middle half up to 1.2x, head and tail real time).
- Shared helpers: the shot-fact, beat-snap, move and window-similarity helpers live once in `scripts/common/` and both the director and the editor import them.
- Director: the chat goes through the director — `llm_director.py --chat` makes one director call with the last plan, the editor report, the rounds, the owner's notes and the notebook, and a decision the owner made is stored as an owner note and carried into the next run's feedback; the Director tab shows the director's idea, structure, per-shot reasons and the editor's report in plain words, and the **Why?** tab is replaced by the **Debug** tab (the `explain.html` artifact stays served).
- Director: the default flow is the film crew — one director call writes `director_plan.json`, the code editor cuts it into `edit.json` and `editor_report.json` without changing the shots, the director reviews the cut with one picture per shot (at most 2 rounds, `director_rounds.json` records every round), and the rule director is the fallback only when the model gives no usable plan.
- The recap's YouTube title and description are optional and off by default: `recap.py --meta` / `--no-meta` override `recap.meta` in the config (default false), the web Upload and Re-edit forms carry a "Title and description" option, and with it off the `recap_meta` station is skipped and the Recap tab shows no title/description block.
- The project page's reviewer box is replaced by the chat in the Director tab (the notebook too).
- The metadata prompt no longer carries a built-in pinned-comment voice; it comes from `channel.style.pinned_comment_voice`.
- `description_order` assembles the description in the configured order.
- Director: the director gets a shortlist of the eligible candidates (every star plus the top-K by score) instead of the whole pool, and `describe` runs its per-clip model calls in a small worker pool (`--workers`, default 4), so a first edit is faster.
- Web app: a friend's Costs page shows only that instance's own costs (its model cost to cover, its limits, its clips, the donation buttons and the line that anything paid above the model cost goes into building the app further) and no owner project totals or donations list; the UI label "New recap" becomes "New video".
- Notebook: lessons that do not fit are kept as `waiting` lessons (never dropped, at most 3) and the learn-pass cost is counted in the instance total model cost.
- Director: the director's shortlist now also sends every clip's best eligible segment, and the label score no longer rewards clean-but-dull shots (steadiness and a defect-free segment add nothing; only unsteady levels and real defects subtract).
- Web app: the chat with the director and the notebook moved out of the Recap tab and the bottom of the project page into the **Director** tab.
- Web app: the run progress card shows "Total so far" only when it differs from the current step's time, and a skipped step shows "already done" instead of "0 s".
- Director: a plain-word request is resolved against the shot descriptions and clip numbers and the reply names the shots it means; the reply's `can_do` is `all`/`part`/`none` and a reply that cannot do everything replaces "Make the new version" with a disabled "Answer the director first" button; two neighbouring slots that are beats of one segment, or a similar pair, keep only the better one.
- Director: the one jump-cut rule — a same-segment-beat or similar neighbour pair keeps only the better one unless the owner explicitly asked for both; the director never marks such a cut `deliberate` by itself.
- Web app: the Director tab's chat shows the last 3 exchanges; older ones fold under a closed "Earlier messages (N)" section, and the input box and the button sit directly under the newest exchange.
- Web app: "Delete this version" is a normal secondary button with the bin icon, and the first version can be deleted too while another version stays (only the latest and a version during a run are refused).
- Web app: the Director tab opens at the newest message (`#chat-latest`).
- Notebook: a lesson is a general rule and may not name a shot id, a clip number or a project name; the learn pass skips such a lesson with a `[warn]` line and still succeeds.
- Director: the neighbour check compares the frames inside each slot's `in_s..out_s` (the windows actually in the edit) with the same MobileNetV2 embedding, cached per window key in `similarity_windows.json`; whole-segment similarity stays in `similarity.json` for the menu the model sees.

### Removed

- Director: the three-role loop (screenwriter, editor, reviewer) and its prompts (`prompts/screenwriter.md`, `prompts/editor.md`, `prompts/reviewer.md`, `prompts/playbook.md`), the `shot_plan.json` schema and the old feedback-reply path (`--interpret-feedback`).
- Director: the star/group/auto-fill repairs (`hero_missing`, the star groups, the repair chain) and the `meta.director.review` writer.

### Fixed

- Run status trusts only our own live pid: a `.run.json` holding a live but unrelated pid reads done or stopped, never running.
- The four web-app tool calls (feedback reply, rewrite metadata, drop/trim shot, notebook delete) answer 504 with plain text when the tool does not finish inside its timeout.
- Version names are validated with `fullmatch`, so `v1\n`, `v00001` and `../v1` are refused.
- The notebook survives a concurrent edit: the learn pass re-reads the file under a lock after the model call and applies removals and additions by id.
- A version's model cost is stored in `version.json` at snapshot time, so deleting a middle version leaves the next version's cost unchanged.
- The owner's first name is out of the repository; `scripts/check_public.py` enforces it (the GitHub handle `Dorusto` stays).
- Stop button: the run's pid is killed only when its recorded start time or command line still matches, so a reused pid is never killed.
- `llm_director.py --prepare` together with `--force` is a usage error.
- `notebook.py --merge` with a repeated id is a usage error.
- An empty or duplicate `channel.style.description_order` is a config error; `recap_meta.py` no longer receives `description_order`.
- A defect the intent rule waives (shake on a deliberate move) is no longer given as "defect: ..." in the edit's reasons; the shot gets its real reason (low score / did not fit the length).
- An explicit request to add a named shot is followed even when the shot has minor defects — the reply says which defects it has; only an `accidental`/`setup`/`teardown` role or a whip short-hold stays refused.

## [0.2.0] - 2026-10-24

yt-pipeline v0.2.0 is the first release with the publish flow and the phone
editing controls. On the project page you can make a shot a second longer or
shorter and delete a single version you no longer want, and the director cuts
on the strong beat of the music. The publish kit is its own step and works
before the thumbnail exists, the metadata prompt writes better titles and
descriptions, and the description no longer needs the members' names. On a
CPU-only server the Romanian subtitles are usable (Whisper `medium` by
default). The Costs page shows donations and warns before a run that would
pass your model credit, and the app can run on a friend's own instance.

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
- Web app: the Recap tab can delete a single version (never the latest or the first) with an inline confirmation; its folder is removed for good.

### Changed

- Publish: the metadata prompt is fixed (the chosen title first, no invented links or repos, a plain first-person pinned comment, each description in its own language, English chapter titles) and every channel-specific wording string moved out of `scripts/metadata.py` and the prompts into `channel.fixed` in the config, one string per language and omitted when missing; `thumbnail_concepts.py` hands `channel.fixed.thumbnail_formula` to the model and `publish_kit.py` uses a chapter's `title_en` for the EN chapter lines.
- Publish: `publish.py --kit` is orchestration only — it calls `publish_kit.py` as its last step, and the kit links the final video (hard link, else symlink, else only its path in `studio.md`) instead of copying it, so the disk no longer holds two copies.
- Publish: `metadata.py` and `prompts/metadata.md` no longer carry the thumbnail section — the concepts live in their own tool and file, and `metadata.md` keeps only what is pasted into Studio.
- Watchdog: no per-lot flag — the alert label comes from the newest task brief in `.opencode/tasks/`, the two stop rules are no commit for N hours and one hard daily spend ceiling, and the stop notification reports the lot's cost.
- Cleanup lot (V020N lot n1): a failed ffprobe is `EXIT_RUNTIME` in `kdenlive_from_fragments.py` and `render.py`; `serve.py` and `recap_meta.py` share `earliest_creation_date`, `serve.py` writes feedback through `common.feedback`, `costs.py totals` and the Costs page share one `compute_totals` call, `upkeep_percent()`/`usd_to_eur()` raise a clear `ConfigError`, `common.openrouter` no longer pulls `openai`/`filelock`, `pytest-timeout` (120 s per test) is in the test requirements and CI, and `release.yml` starts `deploy.yml` after it creates the release.
- Docs: the README is now a one-screen front door (what it is, who it is for, a quickstart, where to go next); the tool-by-tool reference moved to `docs/guides/tools.md`, and the pilot host and costs sections link to their guides.
- Cleanup lot (V020N lot n15): the duplicated helpers moved into scripts/common/ (SRT parsing, read_text, now_iso, parse_child_json, format_size, the FilmCraft sequence/track/cli helpers, the minimum shot length, KIT_DIR_NAME, the credit helper and the drop/trim route body), and the docs drift fixed.
- Cleanup: the `--help` guard copied in the four legacy SRT tools moved into one helper in `scripts/common/cli.py`.
- Publish: `transcribe.py` defaults to `medium` on CPU for a non-English language (including `auto` or no language) and `small` only for an explicit `en` (turbo on GPU, tiny only for the mock backend), resolved from `--model`, `YT_PIPELINE_WHISPER_MODEL` or `transcribe.model` in the config; `publish.py` gains `--whisper-model` for the `--media` step.
- Doctor: `check_publish_config` requires `channel.members` only when a `members_sentence` string contains the `{members}` placeholder, matching `metadata.py`.
- Publish: `metadata.py` requires `channel.members` only when a `members_sentence` string contains the `{members}` placeholder, so a channel that does not thank members in the description runs without it.
- Publish: `publish_kit.py` treats `thumbnail.jpg` as optional — a missing thumbnail no longer fails the kit, the kit is built without it and the `studio.md` checklist says to make and upload one.
- Director: every timeline cut now prefers the nearest downbeat when it lies inside the slot's allowed length range, otherwise a half-bar beat (keeps the bar count even), otherwise the nearest beat; the slot records which grid it used in `beat_grid` and the `beat_snap` summary gains `downbeats`/`half_bar` counts.
- Beats: `beats.py` places the beat grid by dynamic-programming beat tracking (Ellis 2007) after a fine best-phase refinement of the coarse period, so the grid follows a drifting tempo instead of a rigid period; the `beats.json` shape and the CLI are unchanged.
- Web app: the Watch tab no longer shows the "Compare with v1 v2 …" row (the `?with=` side-by-side view stays), and the text after Yes reads "Shot N will be X s longer/shorter. Making the new version, ready in about 15 s...".
- Web app: the project page no longer shows the director reviewer box; the verdict stays in `edit.json`.
- Web app: the Watch tab no longer links the Why?/Shots pages (they stay reachable by URL) and drops the clip/photo colour legend; `config.yaml.example` uses neutral `channel.fixed` placeholders.

### Fixed

- Render: cut positions are rounded on the timeline (the cumulative time to the nearest frame, each clip's frame count the difference of the rounded cut positions), so a cut no longer drifts later and later across the edit.
- Reviewer fixes (V020N lot n16): `thumbnail_concepts.py` reports a model answer with an extra key as `EXIT_RUNTIME` (3) instead of a usage error; `costs.py` reads a lone thousands dot (`1.000` → 1000); `graphics_plan.py` keeps its unquoted context-file message; the Files tab uses the shared `format_size_short`; the four legacy SRT tools (`analyze_srt.py`, `correct_srt.py`, `daily_summary_srt.py`, `translate_srt.py`) answer `--help` with exit 0; the owner's name in the graphics examples becomes a placeholder.
- Reviewer fixes (V020N lot n14): `costs.py` accepts a decimal comma, a thousands dot and a UTF-8 BOM on a donations CSV header; `correct_srt.py` counts the capitalised variant in `Termeni înlocuiți` and a correction line with an empty wrong part is a usage error; `metadata.py` rejects an empty `--title` and the EN chapters section falls back to the RO title when `title_en` is missing; an unknown `channel.fixed` part name is a config error naming the key; `thumbnail_concepts.py` reports a wrong-shape model answer as `EXIT_RUNTIME`; `publish_kit.py` reports a failure as one `{"ok": false, ...}` object and its `--json` `output` is `publish-kit/studio.md`; `colour.py` rejects `--target-luma`/`--target-contrast` outside `(0, 1]` as usage errors; `llm_director.py` falls back to the nearest beat under the slot cap when the downbeat is over it; `.github/dependabot.yml` drops the `time`/`timezone` the default schedule does not need.
- The LLM client retries once when a model answer is empty or not valid JSON, charging both attempts to the budget; `metadata.py` uses it.
- Music: in the holiday flow a cut that starts a new structure beat now falls back to the nearest beat when the nearest downbeat is outside the snap window, so every cut lands on a beat.
- Beats: `beats.py` compensates the STFT window-centre latency (`ONSET_LATENCY_S`, ≈ 0.0174 s) in the onset and beat times, so the reported beat time is the start of the hit and the grid is no longer systematically late; the `beats.json` shape and the CLI are unchanged.
- Director: for a chronological brief the repairs no longer break the time order — after a repair the edit and its plan are re-sorted by capture/source time before the beat snap, so a swapped-in top star lands at its place in the day.
- Director: after a chronological re-sort every slot's transition is set again (first cut, the rest crossfade, hard cuts with a beat grid), so a re-sorted edit no longer keeps a cut in the middle.

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
