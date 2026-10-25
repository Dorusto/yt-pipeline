# Testing

The rules in force live in [`docs/RULES.md`](../RULES.md); this file says how tests
are written and run here.

- The synthetic-data rule and the CI matrix are in `docs/RULES.md` and are not
  repeated here.
- `pytest.ini` sets `timeout = 120` per test, so a hung test fails with its name.
- Every feature that passed the owner's real test gets a regression test on
  synthetic data that fixes its behaviour.
- The reference-material tests read the folder from `YT_PIPELINE_REFERENCE_DIR`
  and skip when it is unset, so the repo carries no personal path.

## Host-only tests

These tests never run in CI. CI installs `requirements-dev.txt`,
`opencv-python-headless`, `numpy`, `pyyaml`, `jsonschema`, `pysubs2`, `openai`,
`filelock`, `mcp`, `psutil`, `pillow`, `pillow-heif`, `psd-tools`,
`pytest-timeout`, plus `ffmpeg` and `melt` on the ubuntu job; it installs none of
the reference material, the optional external CLI or the optional models below.
They are kept as host checks, so the code they cover has no CI coverage.

- **Reference-material tests** (`YT_PIPELINE_REFERENCE_DIR` unset) —
  `tests/test_chapters.py::test_reference_final_project`,
  `tests/test_eval_rough_cut.py::test_reference_cross_check`,
  `tests/test_mode_b.py::test_reference_names_pairing`,
  `tests/test_shorts_rank.py::test_reference_final_project` — skip when
  `YT_PIPELINE_REFERENCE_DIR` is unset (the host's real recordings are never in
  the repo).
- **FilmCraft tests** (`filmcraft-cli` not installed) —
  `tests/test_export_filmcraft.py::test_headless_open`,
  `tests/test_finish_pip.py::test_smoke_keyframes_land_on_the_clip` — skip when
  `filmcraft-cli` is not installed (the optional external CLI is not in CI).
- **Optional model / package tests** —
  `tests/test_graphics.py::test_hyperframes_render_has_alpha` (HyperFrames CLI),
  `tests/test_similarity.py::test_mobilenet_embeddings_are_finite_and_normalised`
  (MobileNetV2 ONNX model),
  `tests/test_thumbnail.py::test_rembg_cutout_integration` (rembg
  `birefnet-portrait`),
  `tests/test_matte.py::test_integration_prores4444_with_alpha` (`torch`) — skip
  when the model or package is not in the cache; CI installs none of them.

The rest of each of these files runs in CI.

## Definition of done (every task)

- Tests pass.
- Ran end-to-end on sample media; for anything visual, the output frames were
  looked at (through `scripts/describe.py` or a measurement script, never by eye).
- Outputs validated against `schemas/`.
- No source file modified.
- A `docs/DECISIONS.md` entry if something new was chosen; `docs/RULES.md` updated
  in the same commit if a rule changed.
- The skill and the README updated if behaviour changed.
