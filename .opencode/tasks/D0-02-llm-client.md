# D0-02 — `scripts/common/llm.py`: OpenRouter client with per-run spend budget + mock backend

## Goal

Add `scripts/common/llm.py`: a small LLM client for OpenRouter that accumulates USD spend from the `usage` block of every response, refuses further calls with a clear error once the per-run budget from `config.yaml` is reached, and ships a mock backend so tests and dry runs work with no network and no API key.

## Files to create or change

- `scripts/common/llm.py` — create
- `tests/test_llm.py` — create
- `README.md` — change: one line in the "Foundation (AI Director, phase D0)" section listing `llm.py`

## Rules that apply

These rules come from `CLAUDE.md`, `docs/VISION.md` and `docs/DECISIONS.md` and apply in full:

1. **Code and documentation → in English. No inline comments (self-documenting code).** A module docstring and one-line docstrings on public functions/classes are fine — same style as the existing `scripts/common/notify.py` and `scripts/common/schema.py`.
2. **Shared code only in `scripts/common/`.** Vision principle 1: "No tool imports another tool's code. Shared code lives only in `scripts/common/` (schemas, config, model clients)." This is a shared model client, not a tool script: no CLI, no skill, no `--help`/`--json`.
3. **Model backends are config, not code.** Vision principle 6: "`VisionModel`, `Transcriber`, `Director` are interfaces with a mock, a cloud and a local implementation." The backend is chosen by `llm_backend` in `config.yaml` (`openrouter` or `mock`); the model id per role is chosen by the `models` map in `config.yaml`. Nothing in this module hard-codes a model id.
4. **LLM spend rule (CLAUDE.md, Autonomy):** "LLM spend: respect the per-run budget cap in config; stop and report when reached." The cap is `llm_budget_usd` in `config.yaml` (per run = per process). `null` means no limit.
5. **DECISIONS 2026-10-12, "Models via OpenRouter; vision starts in the cloud, decided by a bake-off"** — all cloud calls go through OpenRouter. **DECISIONS 2026-09-22** — every script in this repo connects with the `openai` Python SDK, `base_url="https://openrouter.ai/api/v1"`, API key from the `OPENROUTER_API_KEY` environment variable. Reuse that same stack; do not add a new HTTP dependency (the `openai` package is already in `requirements.txt`).
6. **Never print or log secrets, API keys or tokens.** The API key must never appear in error messages, reprs or logs.
7. **The build LXC has no GPU, no network guarantee and no API key.** Every test must pass offline with no `OPENROUTER_API_KEY` set.
8. **Do not edit** `CLAUDE.md`, `docs/VISION.md`, `docs/DECISIONS.md`, `docs/ROADMAP.md`, `schemas/`, `.opencode/`, `.claude/skills/`, `scripts/common/config.py` or any file not listed above. The architect writes the decision log.
9. **Definition of done (every task):** tests pass; ran end-to-end on a sample invocation; no source file modified; no comments in code; README updated if behavior changed.
10. No source footage or files outside the repository may be modified.

## Interface

### `scripts/common/config.py` — already exists (D0-01), use it as-is

```python
class ConfigError(RuntimeError)
def load_config(path=None) -> dict   # validated mapping; keys: models, llm_budget_usd, llm_backend
def model_for(config: dict, role: str) -> str   # ConfigError if role unknown
```

`llm_budget_usd` is `None` (no limit) or a number `>= 0`. `llm_backend` is `"openrouter"` or `"mock"`.

### `scripts/common/llm.py` — API

```python
class LLMError(RuntimeError):
    """Base error for the LLM client."""

class BudgetExceeded(LLMError):
    """Raised before a call when the per-run budget is already spent."""

class LLMResponse:            # dataclass
    text: str
    model: str
    role: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float

class LLMClient:
    def __init__(self, budget_usd=None, config=None): ...
    spent_usd: float          # accumulated cost, starts at 0.0
    budget_usd: float | None
    @property
    def remaining_usd(self) -> float | None   # None if no budget; else max(0, budget - spent)
    def complete(self, role, messages, model=None, **kwargs) -> LLMResponse: ...

class OpenRouterClient(LLMClient):
    def __init__(self, budget_usd=None, config=None, api_key=None,
                 base_url="https://openrouter.ai/api/v1", client=None): ...

class MockClient(LLMClient):
    def __init__(self, budget_usd=None, config=None, cost_per_call=0.0, text="mock response"): ...

def make_client(config=None) -> LLMClient: ...
```

Behavior:

- **`complete(role, messages, model=None, **kwargs)`**:
  1. Budget check first: if `budget_usd is not None and spent_usd >= budget_usd`, raise `BudgetExceeded` without making any call. The message must name the budget and the amount spent, e.g. `LLM budget reached: spent $5.00 of $5.00 (llm_budget_usd); refusing further calls`.
  2. Resolve the model: the `model` argument if given, else `model_for(self.config, role)`. Roles are the config keys (`architect`, `coder`, `coder_fallback`, `vision`, `vision_fallback`); unknown roles raise `ConfigError` from `model_for`.
  3. Call the backend with `messages` (OpenAI chat format: list of `{"role": ..., "content": ...}`) and any extra kwargs (e.g. `temperature`, `max_tokens`) passed through.
  4. Extract `prompt_tokens`, `completion_tokens` and `cost_usd` from the response `usage` block. `cost_usd` is `usage.cost` (OpenRouter returns it in USD credits). If `usage` or `cost` is missing, treat the cost as `0.0` but still return the token counts (0 if missing).
  5. `spent_usd += cost_usd`, then return `LLMResponse`.
- **Cost crossing the budget is allowed**: the cost of a call is only known after it, so a call may push `spent_usd` past `budget_usd`. Its result is still returned; the *next* call raises `BudgetExceeded`.
- **`budget_usd=0`** refuses the very first call (`0 >= 0`).
- **`OpenRouterClient`**: uses the `openai` SDK — `openai.OpenAI(api_key=..., base_url=base_url)`, then `client.chat.completions.create(model=..., messages=..., **kwargs)`. If `api_key` is not passed, read `os.environ["OPENROUTER_API_KEY"]`; if that is unset or empty, raise `LLMError` at construction with a message like `OPENROUTER_API_KEY is not set` (never include the key value anywhere). Accept an injected `client=` object (anything with `.chat.completions.create`) so tests can pass a fake and never touch the network. Response text is `response.choices[0].message.content`.
- **`MockClient`**: no network. `complete()` returns an `LLMResponse` with `text="mock response"` (or the `text` given at construction), the resolved model, `prompt_tokens=0`, `completion_tokens=0`, `cost_usd=cost_per_call` (default `0.0`), and charges the budget exactly like the real client.
- **`make_client(config=None)`**: `config = load_config()` when not given; reads `llm_budget_usd` and `llm_backend`; returns `MockClient` when the backend is `mock`, `OpenRouterClient` when it is `openrouter`. The returned client holds `config` so `complete()` can resolve roles.

Errors are exceptions, not exit codes — this is a library module, not a CLI.

### `README.md`

In the existing "Foundation (AI Director, phase D0)" bullet list, add one line for `scripts/common/llm.py` (OpenRouter client, per-run `llm_budget_usd` spend tracking, mock backend). Keep it to one bullet, same style as the neighbouring bullets.

## Acceptance checks

Tests to add in `tests/test_llm.py` (`tests/conftest.py` already puts `scripts/` on `sys.path`):

- `MockClient().complete("vision", [{"role": "user", "content": "hi"}])` returns an `LLMResponse` with text `"mock response"` and `cost_usd == 0.0`
- spend accumulates: `MockClient(cost_per_call=0.01)`, budget `0.05` → 5 calls succeed, `spent_usd == 0.05`, 6th call raises `BudgetExceeded`
- `BudgetExceeded` message contains both the spent amount and the budget
- `budget_usd=0` raises `BudgetExceeded` on the first call
- `budget_usd=None` never raises (10 calls fine)
- `remaining_usd` is `None` without budget, `0.0` at the cap, `max(0, ...)` after overshoot
- a call that crosses the budget still returns its result (budget `0.015`, `cost_per_call=0.01`: first call returns, second raises)
- role resolution: `MockClient(config=load_config())` (or a dict with a `models` map) resolves `role="vision"` to the configured model id; unknown role raises `ConfigError`
- `make_client` with a config whose `llm_backend` is `mock` returns a `MockClient`; with `openrouter` an `OpenRouterClient`
- `OpenRouterClient` with an injected fake `client=` (a small stub whose `chat.completions.create` returns an object with `choices[0].message.content` and `usage` carrying `cost`, `prompt_tokens`, `completion_tokens`) returns the text, charges the exact cost and exposes token counts
- `usage` without `cost` charges `0.0` and does not crash
- `OpenRouterClient` with no `OPENROUTER_API_KEY` in the environment raises `LLMError` mentioning `OPENROUTER_API_KEY` (monkeypatch the env var away), and the raised message never contains a key value
- the injected fake is used in every test — no test may open a socket

Commands to run and expected output:

```bash
cd /root/projects/yt-pipeline
.venv/bin/python -m pytest -q
```

Expected: all tests pass (the existing 66 plus the new ones).

```bash
PYTHONPATH=scripts .venv/bin/python - <<'PY'
from common.llm import MockClient, BudgetExceeded
c = MockClient(budget_usd=0.02, cost_per_call=0.01)
msgs = [{"role": "user", "content": "hi"}]
c.complete("vision", msgs)
c.complete("vision", msgs)
try:
    c.complete("vision", msgs)
    print("FAIL: no raise")
except BudgetExceeded as exc:
    print("ok:", exc)
print("spent:", c.spent_usd, "remaining:", c.remaining_usd)
PY
```

Expected: a line starting `ok: LLM budget reached:` mentioning `$0.02`, then `spent: 0.02 remaining: 0.0`.

## Out of scope

- Streaming responses, tool/function calling, retries, rate limiting.
- Migrating `translate_srt.py`, `daily_summary_srt.py` or `analyze_srt.py` onto this client.
- Any CLI wrapper, skill, or `--help`/`--json` surface.
- Any edit to `docs/DECISIONS.md`, `docs/ROADMAP.md`, `CLAUDE.md` or `schemas/`.
- Image/vision payloads beyond passing `messages` through unchanged (D1 concern).
