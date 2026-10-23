# Brand marks

Three flat SVG marks for the web app, own work, released under the repository
licence (AGPL-3.0-or-later, see [`../../LICENSE`](../../LICENSE)):

- `option-1.svg` — clapperboard
- `option-2.svg` — viewfinder brackets
- `option-3.svg` — focus reticle

Each is a 48×48 square drawn in two theme colours (`--sea`, `--stone`) written
as CSS variables with hex fallbacks, so a mark adapts to the light and dark
theme when inlined and still renders standalone as the favicon.

Pick the one this instance uses with `brand.logo` in the config (default
`option-1`), or `--brand-logo` / `YT_PIPELINE_BRAND_LOGO`. `GET /brand` shows
all three.
