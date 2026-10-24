# Playbook — the standard structure and the tools

This is the playbook every instance starts from. It holds the structure every
edit follows and the tools the director can call. It carries nothing
channel-specific: the owner's wording, style and lessons live in the instance's
own settings and notebook.

## The structure every edit follows

- The brief is the contract: its length, its order and its pacing.
- A move and the hold it lands on are one phrase; never cut out of a move and
  into its own landing.
- Hero shots and complete semantic moves keep their whole window, capped at the
  hero cap; a static hero hold is a static shot.
- Ordinary shots follow the pacing; a cut shorter than its window is centred on
  the shot's best moment (`peak_s`).
- No two slots from the same take back to back unless their windows touch or
  both are camera moves at least 8 s apart, in time order.
- No two visually similar shots next to each other.
- Photos are slots: 2–4 s, a Ken Burns move, no window and no landing.
- A long take is several moments: cover its arc, not two neighbours from one end.
- Do not pad: fewer slots beat weak shots.

## The tools you can call

Every tool is `yt <tool>`.

### Find problems

- `yt quality` — blur, exposure, shake, obstruction, horizon and the role of each segment.
- `yt motion` — camera motion, steady moves and the handles to cut on.
- `yt beats` — a track's tempo, beats and downbeats (missed beats).
- `yt loudness` — a clip's loudness.
- `yt similarity` — near-duplicate shots and photos.
- `yt eval_edit` — check an edit against expectations (gaps in the edit).
- `yt eval_labels` — score the motion and quality labels against ground truth.
- `yt doctor` — check the environment and the keys.

### Create

- `yt ingest` — scan a footage folder.
- `yt describe` — describe segments and photos.
- `yt index` — build the shotlog.
- `yt director` — the rule-based edit.
- `yt llm_director` — the LLM edit.
- `yt music_pick` — pick a track.
- `yt render` — render the MP4.
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
