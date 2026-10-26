# D0 report — Foundation

Branch `d0-foundation`. Scope: `scripts/common/`, `schemas/`, pytest suite, CPU-only path, config and LLM client. All tests run on the build LXC (no GPU, no API key, no network).

## Works

- `scripts/common/` — `notify.py` (ntfy via stdlib), `ffmpeg_tools.py` (encoder pick with `libx264` fallback), `schema.py` (JSON-Schema validation), `config.py` (config loader), `llm.py` (OpenRouter client + mock), `__init__.py`.
- `schemas/fragments.schema.json` — validates `fragments.yaml`/JSON before generation; extra keys (`score`, `reason`, `slot`) allowed. Verified with valid/invalid fixtures in `tests/test_schema.py`.
- `tests/` — 89 tests, all passing offline: `.venv/bin/python -m pytest -q` → `89 passed`. Covers schema, encoder selection, notify CLI, rough-cut retiming, shorts helpers, config loader, LLM budget accounting.
- CPU-only path — `shorts_generator.py` no longer imports `torch`/`whisperx` at startup (`tests/test_shorts_helpers.py::test_module_imports_without_torch_or_whisperx`), picks `h264_nvenc` only when `nvidia-smi` reports a GPU and falls back to `libx264` (`tests/test_ffmpeg_tools.py`, incl. `test_this_machine_has_a_usable_encoder`).
- CLI contract — `shorts_generator.py`, `kdenlive_from_fragments.py`, `srt_from_fragments.py`, `notify.py` all gained `--json` and non-interactive error handling (exit 1 with a clear message instead of prompting).
- `config.yaml.example` + git-ignored `config.yaml` — OpenRouter model id per role (`architect`, `coder`, `coder_fallback`, `vision`, `vision_fallback`), per-run `llm_budget_usd: 5.0`, `llm_backend: openrouter|mock`. Verified: `git check-ignore config.yaml` → ignored, `config.yaml.example` not ignored; `PYTHONPATH=scripts .venv/bin/python -c "from common.config import load_config, model_for; ..."` → `deepseek/deepseek-v4.1-flash 5.0 openrouter`.
- `scripts/common/llm.py` — `complete()` charges `usage.cost` per call, raises `BudgetExceeded` (with spent and budget in the message) before the next call once `llm_budget_usd` is spent; `MockClient` for offline runs; `make_client()` picks the backend from config. Verified end-to-end with the mock smoke script (budget 0.02, cost 0.01/call → third call refused, `spent: 0.02 remaining: 0.0`).
- End-to-end on sample media: `kdenlive_from_fragments.py` + `srt_from_fragments.py` exercised through `tests/test_fragments_tools.py` on the tiny fixtures (retimed output checked block by block); `shorts_generator.py` helpers (`parse_time`, SRT parsing, karaoke ASS) exercised in `tests/test_shorts_helpers.py`. No visual output in D0 — render frames are a D1+ concern.

## Mocked / not verified

- `OpenRouterClient` against the real API — every LLM test uses an injected fake `client=`; no live call was made (no `OPENROUTER_API_KEY` on the build machine). The connection shape (openai SDK + `base_url` + `usage.cost`) follows the pattern already proven by `translate_srt.py`.
- WhisperX forced alignment — the active path now interpolates word timing from the SRT; `run_forced_alignment`/`generate_plain_ass` are lazy-imported and unused. Not verified against audio in D0.
- ntfy delivery — `tests/test_notify.py` stubs `urlopen`; a real POST to the project server was only smoke-tested via `--dry-run`.
- Cumulative subtitle drift between `kdenlive_from_fragments.py` and `srt_from_fragments.py` (frame-inclusive vs `out - in`) — still unverified, listed in ROADMAP known issues.

## the owner, check this

- `.venv/bin/python -m pytest -q` → expect `89 passed` (offline, no GPU, no key).
- `cat config.yaml.example` → are the five roles and `llm_budget_usd: 5.0` the right starting point? The D1 bake-off only edits `vision` here.
- `.venv/bin/python scripts/notify.py "D0 report ready" --dry-run` → notification wiring before the real ping.
- `git log --oneline master..d0-foundation` → 5 commits: the wip pass (kept as-is), D0-01 config, D0-02 llm client, and this docs pass.
- `docs/DECISIONS.md` → the `jsonschema` rationale is corrected (it was added as a dependency by D0, not already declared) and two new entries document config + LLM client.
