"""Shared ``--json`` summary keys: wall time and LLM cost (RULES, 2026-10-13)."""

import time


def summary_fields(started, llm_cost_usd=0.0):
    """Return the ``wall_time_s`` and ``llm_cost_usd`` keys every ``--json`` summary reports."""
    return {
        "wall_time_s": round(time.monotonic() - started, 3),
        "llm_cost_usd": round(float(llm_cost_usd), 8),
    }
