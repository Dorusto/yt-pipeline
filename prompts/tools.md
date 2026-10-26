# The tools the director can call

Every tool is `yt <tool>`. This is the director-facing capability list: the
tools that find problems and the tools that create. It carries nothing
channel-specific.

## Find problems

- `yt quality` — blur, exposure, shake, obstruction, horizon and the role of each segment.
- `yt motion` — camera motion, steady moves and the handles to cut on.
- `yt beats` — a track's tempo, beats and downbeats (missed beats).
- `yt loudness` — a clip's loudness.
- `yt similarity` — near-duplicate shots and photos.
- `yt eval_edit` — check an edit against expectations (gaps in the edit).

## Create

- `yt ingest` — scan a footage folder.
- `yt describe` — describe segments and photos.
- `yt index` — build the shotlog.
- `yt director` — the rule-based edit.
- `yt llm_director` — the director writes the plan, the code editor cuts it, the director reviews the cut (max 2 rounds).
- `yt director_editor` — cut the director's plan into the exact windows.
- `yt music_pick` — pick a track.
- `yt render` — render the MP4.
- `yt recap` — the whole recap flow in one command.
- `yt recap_meta` — the title, description and hashtags.
- `yt metadata` — the metadata draft.
- `yt thumbnail` — the YouTube thumbnail.
- `yt graphics_plan` — plan the graphic overlays.
- `yt graphics` — render the approved overlays.
- `yt explain` — explain.html.
- `yt thumbs` — the poster and the shot thumbnails.
- `yt edit_drop` — drop or trim a shot.
- `yt router` — route a brief.
- `yt audio_clean` — clean a clip's audio.
- `yt transcribe` — words and subtitles.
- `yt publish` — the publish kit.
- `yt shorts_rank` — rank the Shorts candidates.
- `yt shorts_generator` — cut the Shorts.
- `yt reframe` — a vertical crop path.
- `yt mode_b` — the Mode B rough cut.
- `yt rough_cut` — the rough-cut Kdenlive project.

## Learn

- `yt notebook` — the instance's lessons: read them, add one, remove a stale one, merge overlapping ones, propose moving a repeated sentence to `channel.fixed`.
