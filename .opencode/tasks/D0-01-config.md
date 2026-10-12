# D0-01 — Project config: OpenRouter model ids per role + per-run LLM budget

## Goal

Add the project-wide AI Director configuration: a committed `config.yaml.example` and a git-ignored `config.yaml` at the repository root, listing OpenRouter model ids per role and `llm_budget_usd` (max USD one run may spend on LLM calls), plus `scripts/common/config.py` to load and validate that file.

## Files to create or change

- `config.yaml.example` — create (committed template)
- `config.yaml` — create (git-ignored local copy, same content as the example)
- `scripts/common/config.py` — create (loader + validation)
- `tests/test_config.py` — create
- `.gitignore` — change: add `config.yaml`
- `README.md` — change: mention `config.yaml` in the "Foundation (AI Director, phase D0)" section (2–3 lines)

## Rules that apply

These rules come from `CLAUDE.md`, `docs/VISION.md` and `docs/DECISIONS.md` and apply in full:

1. **Code and documentation → in English. Comments in code → none (self-documenting code).**
2. **Shared code only in `scripts/common/`.** Vision principle 1: "Every tool is independent. Each stage is a standalone script in `scripts/` with its own skill in `.claude/skills/`, exactly like the existing tools. A human runs it from the terminal, an agent runs it through the skill, the web UI calls the same command. No tool imports another tool's code. Shared code lives only in `scripts/common/` (schemas, config, model clients)." This task creates shared config code, so it lives in `scripts/common/`; it is not a tool script and needs no skill and no CLI.
3. **Files are the interface.** Vision principle 3: "Files are the interface. Stages talk through JSON/YAML on disk, validated against schemas in `schemas/`. Any stage can be re-run alone." Re-running or reading the config twice must be safe and give the same result.
4. **Model backends are config, not code.** Vision principle 6: "`VisionModel`, `Transcriber`, `Director` are interfaces with a mock, a cloud and a local implementation." Which model serves which role is a config value, never hard-coded in later stages.
5. **Model routing (Vision, section 6, decided 2026-10-12).** The model ids are OpenRouter slugs (the part after `openrouter.ai/models/`):

   | Role | Model | Via |
   |---|---|---|
   | Architect / reviewer agent | MiMo V2.6 Pro | OpenRouter |
   | Coder agent | DeepSeek V4.1 Flash (fallback GLM 5.3 Flash) | OpenRouter |
   | Vision, now | DeepSeek V4.1 Flash or GLM 5.3 Flash (image input), chosen by the D1 bake-off | OpenRouter |
   | Vision, later or for private footage | Qwen3.8 27B 4-bit on the RTX 3090 via vLLM | local OpenAI-compatible endpoint |

   "Do not switch coder models mid-task. Vision backend is a config value." Concretely: `xiaomi/mimo-v2.6-pro`, `deepseek/deepseek-v4.1-flash`, `z-ai/glm-5.3-flash`.
6. **DECISIONS 2026-10-12, "Models via OpenRouter; vision starts in the cloud, decided by a bake-off"**: "Architect MiMo V2.6 Pro, coder DeepSeek V4.1 Flash (GLM 5.3 Flash as fallback). Vision: DeepSeek V4.1 Flash or GLM 5.3 Flash (both accept images), chosen in D1 by running both on the same ~10 test clips." and "Keeping the backend a config value means the bake-off result, not a guess, decides."
7. **DECISIONS 2026-10-12, "D0 foundation: `scripts/common/`, `schemas/`, local pytest, and a CPU-only render path"**: "A minimal `common/` (`notify`, `ffmpeg_tools`, `schema`) plus one schema for `fragments.yaml` covers what D1 actually touches." This task adds `config.py` to that same minimal package.
8. **The build LXC has no GPU and no nvenc.** Anything GPU-bound needs a CPU fallback or a mock for tests. All tests in this task must pass with no GPU, no network and no API key.
9. **Never print or log secrets, API keys or tokens.** The config file holds no secrets — API keys stay in environment variables (`OPENROUTER_API_KEY`).
10. **No source footage or files outside the repository may be modified.**
11. **Do not edit** `CLAUDE.md`, `docs/VISION.md`, `docs/DECISIONS.md`, `docs/ROADMAP.md`, `schemas/`, `.opencode/`, `.claude/skills/` or any file not listed above. The architect writes the decision log.
12. **Definition of done (every task):** tests pass; ran end-to-end on sample inputs; outputs validated; no source file modified; no comments in code; README updated if behavior changed.

## Interface

### `config.yaml.example` / `config.yaml` — exact content

```yaml
# AI Director configuration — copy to config.yaml and edit.
# config.yaml is git-ignored; config.yaml.example is the committed template.
# Model ids are OpenRouter slugs (the part after openrouter.ai/models/).

models:
  architect: xiaomi/mimo-v2.6-pro
  coder: deepseek/deepseek-v4.1-flash
  coder_fallback: z-ai/glm-5.3-flash
  vision: deepseek/deepseek-v4.1-flash
  vision_fallback: z-ai/glm-5.3-flash

# Maximum USD one run may spend on OpenRouter calls. The LLM client refuses
# further calls once this is reached. Omit or use null for no limit.
llm_budget_usd: 5.0

# Backend used by scripts/common/llm.py: openrouter or mock.
llm_backend: openrouter
```

`config.yaml` must be byte-identical to `config.yaml.example` when first created.

Semantics:

- `models` — mapping role name → OpenRouter model slug. Roles above are the ones decided so far; later phases may add keys. `vision` may be switched to `z-ai/glm-5.3-flash` after the D1 bake-off — that is a config edit, not a code change.
- `llm_budget_usd` — per **run** (one process), not per day or per month. `0` or a positive number, or `null`/omitted for no limit.
- `llm_backend` — `openrouter` (real API, needs `OPENROUTER_API_KEY` in the environment) or `mock` (offline, for tests and dry runs).

### `scripts/common/config.py` — API

```python
class ConfigError(RuntimeError):
    """Raised when the config file is missing, unreadable or invalid."""

REPO_ROOT: Path          # Path(__file__).resolve().parents[2]
CONFIG_PATH: Path        # REPO_ROOT / "config.yaml"
EXAMPLE_PATH: Path       # REPO_ROOT / "config.yaml.example"

def resolve_config_path(path=None) -> Path:
    """Explicit path, else config.yaml, else config.yaml.example.
    Raises ConfigError if none exists."""

def load_config(path=None) -> dict:
    """YAML mapping from resolve_config_path(path), validated.
    Raises ConfigError on unreadable file, non-mapping root, or invalid values."""

def model_for(config: dict, role: str) -> str:
    """config["models"][role]. Raises ConfigError listing known roles if absent."""
```

Validation rules (any violation → `ConfigError` with a message naming the offending key):

- Root must be a mapping.
- `models` is required: a mapping, every key and value a non-empty string.
- `llm_budget_usd` is required: `null`, or an `int`/`float` that is `>= 0` and finite. Booleans are not numbers here.
- `llm_backend` is required: exactly `"openrouter"` or `"mock"`.
- Unknown extra keys are allowed and ignored (forward compatibility).

Exit codes: not a CLI — this is a library module. Errors are exceptions.

### `.gitignore`

Add `config.yaml` on its own line. `config.yaml.example` must NOT be ignored.

## Acceptance checks

Tests to add in `tests/test_config.py` (the existing `tests/conftest.py` already puts `scripts/` on `sys.path`, so `import common.config` works):

- valid fixture loads and `model_for(config, "vision")` returns `deepseek/deepseek-v4.1-flash`
- `load_config(path)` with an explicit path to a valid temp file works
- explicit path that does not exist raises `ConfigError`
- missing `models`, empty `models: {}`, empty-string model value, missing `llm_budget_usd`, negative budget, `llm_budget_usd: true` (bool), missing `llm_backend`, invalid `llm_backend: foo` each raise `ConfigError`
- `llm_budget_usd: null` loads and gives `None`
- unknown extra top-level key is ignored (no error)
- `model_for` with an unknown role raises `ConfigError`
- fallback order: monkeypatch `CONFIG_PATH` and `EXAMPLE_PATH` to temp files; when the first is missing, the second is read; when both are missing, `ConfigError`
- the committed `config.yaml.example` itself loads cleanly via `load_config(EXAMPLE_PATH)` and matches the documented content (roles and budget present)

Commands to run and expected output:

```bash
cd /root/projects/yt-pipeline
.venv/bin/python -m pytest -q
```

Expected: all tests pass (the existing 48 plus the new ones).

```bash
git check-ignore config.yaml
```

Expected: prints `config.yaml`, exit code 0.

```bash
git check-ignore config.yaml.example; echo $?
```

Expected: prints `1` (not ignored).

```bash
PYTHONPATH=scripts .venv/bin/python -c "from common.config import load_config, model_for; c = load_config(); print(model_for(c, 'vision'), c['llm_budget_usd'], c['llm_backend'])"
```

Expected: `deepseek/deepseek-v4.1-flash 5.0 openrouter`

## Out of scope

- `scripts/common/llm.py`, the OpenRouter client and its mock backend (next task, D0-02).
- A JSON Schema for `config.yaml` in `schemas/`.
- Migrating `translate_srt.py`, `daily_summary_srt.py` or `analyze_srt.py` onto this config (they keep their hard-coded model for now).
- Any CLI, skill or `--help`/`--json` surface (this is shared library code, not a tool).
- Any edit to `docs/DECISIONS.md`, `docs/ROADMAP.md` or `CLAUDE.md`.
