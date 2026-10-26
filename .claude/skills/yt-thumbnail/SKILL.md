---
name: yt-thumbnail
description: Render a branded 1280x720 YouTube thumbnail from a portrait photo and a title with the DEPPSiT brand kit (brand.json) — cut-out portrait with an outer glow screen-blended onto a generated or photo background, title + keyword + kicker + #DEPPSiT tag in the brand fonts, optional layered thumbnail.psd (psd-tools). Two themes — tech (generated dark background, cool keyword) and creator (photo background, warm keyword). Use when the user asks for a YouTube thumbnail or cover from a photo, wants the branded DEPPSiT look, or wants the text on separate PSD layers for manual retouching. Trigger phrases - "fă thumbnail", "thumbnail cu brand", "generează thumbnail-ul", "cover pentru YouTube", "branded thumbnail". Not for cutting shorts (that is yt-shorts), not for the recap render (that is yt-render), not for background removal from video (that is MatAnyone / D6).
compatibility: Run with the `yt` entry point on PATH. CPU only, no GPU, no model calls (rembg runs a local ONNX model, ~973 MB downloaded once to ~/.rembg/models/, older rembg to ~/.u2net/; the birefnet-portrait cut-out needs about 8 GB free RAM); writes only the --out directory. psd-tools is needed for --psd; rembg is optional (--skip-cutout works without it).
---

# yt-thumbnail

One command turns a portrait photo + a title into the branded 1280x720 YouTube thumbnail: the portrait is cut out, given an outer glow and placed cut by the left/bottom frame edge; the title, keyword, kicker and `#DEPPSiT` tag are drawn in the brand fonts with drop shadows. Every size, colour and position comes from `brand.json` (validated against `schemas/brand.schema.json`), so the look is tunable without touching code.

## Step 1: tech theme (generated background)

```bash
yt thumbnail --photo photo.jpg --title "AI-UL MEU" --keyword "RULEAZĂ ACASĂ" \
    --kicker "// homelab · automatizări" --theme tech --out thumb/
```

- `--photo` (required) is the input portrait, read-only. `--title` (required) is one line; `--keyword` (required) is the big accented words, wrapped to at most 2 lines; `--kicker` is the small line above the title (skipped when empty). When the keyword wraps to two lines, the line spacing grows automatically so the comma below a Ș/Ț on the first line never touches the second line's ink (the brand `keyword_leading_px` is kept when it already leaves room).
- The tech theme generates the dark background (warm/cool glows, grid, vignette, grain). An optional `--background FRAME.jpg` replaces it with a cover-scaled, darkened frame.
- Writes `thumb/thumbnail.jpg` (1280x720, JPEG quality 92), atomically and re-run safe.

## Step 2: creator theme (photo background, required)

```bash
yt thumbnail --photo photo.jpg --title "AI-UL MEU" --keyword "RULEAZĂ ACASĂ" \
    --theme creator --background frame.jpg --out thumb/
```

- `--background` is REQUIRED for `creator`: the frame is cover-scaled to 1280x720, darkened and finished with the same vignette and grain.

## Step 3: PSD and cut-out options

```bash
yt thumbnail --photo photo.jpg --title "AI-UL MEU" --keyword "RULEAZĂ ACASĂ" \
    --theme tech --out thumb/ --psd --skip-cutout --brand brand.json --json
```

- `--psd` also writes `thumb/thumbnail.psd` with the layers bottom to top `background`, `portrait` (glow baked in), `title`, `keyword`, `kicker` (only when non-empty), `tag` — text rasterized and cropped to its alpha bbox, so each block can be moved in Photoshop.
- The cut-out uses rembg `birefnet-portrait` (first run downloads ~973 MB to `~/.rembg/models/`, older rembg to `~/.u2net/`); `--skip-cutout` uses the photo's own alpha instead (synthetic portraits, tests) and never calls rembg.
- `--brand PATH` points at another brand kit (default `brand.json` at the repo root); a missing font file is a hard error naming the path and the brand key — never a silent fallback.

## Step 4: concepts file

```bash
yt thumbnail --photo photo.jpg --concepts thumbnail_concepts.json \
    --pick 2 --out thumb/ --skip-cutout --json
```

- `--concepts FILE` takes the title, keyword, kicker and theme from a `thumbnail_concepts.json` (written by `thumbnail_concepts.py`, validated against `schemas/thumbnail_concepts.schema.json`) instead of the flags; `--pick N` (1-based, default 1) chooses the concept.
- With `--concepts`, `--title`, `--keyword`, `--kicker` and `--theme` must not be given (argparse error, exit 2). `--photo` and `--out` stay required.
- A missing, unreadable or invalid concepts file, or a `--pick` outside `1..len(concepts)`, is a usage error (exit 2) naming the valid range.

## `--json` keys

`generated_at`, `tool`, `photo`, `title`, `keyword`, `kicker`, `theme`, `concepts_file` (path or null), `concept` (the picked 1-based index or null), `brand`, `out_dir`, `thumbnail`, `psd` (path or null), `cutout_used`, `cutout_model`, `psd_writer` (always `psd-tools`), `font_size_title_px`, `font_size_keyword_px`, `keyword_lines`, `subject_bbox_px` (`left`/`top`/`width`/`height` of the placed portrait rect), `warnings`, `wall_time_s`, `llm_cost_usd` (always 0.0 — no model calls).

## Notes

- Exit 0 on success; exit 2 on a usage error (missing/unreadable input, invalid brand, missing font, `creator` without `--background`) and exit 3 when the cut-out model is missing or fails, each with one `[error] ...` line on stderr; argparse errors exit 2.
- The photo and background frame are read-only; only `--out` is written.
