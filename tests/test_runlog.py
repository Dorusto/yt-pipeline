import json
from datetime import datetime

import pytest

from common.runlog import RUN_MARKER, last_run_lines, last_run_start, spend_between


def epoch(text):
    return datetime.fromisoformat(text).timestamp()


def test_last_run_lines_returns_lines_after_the_last_marker(tmp_path):
    (tmp_path / "run.log").write_text(
        f"{RUN_MARKER} 2026-01-01 10:00:00 ===\nold\n"
        f"{RUN_MARKER} 2026-01-01 11:00:00 ===\nnew one\nnew two\n",
        encoding="utf-8",
    )
    assert last_run_lines(tmp_path) == ["new one", "new two"]


def test_last_run_start_parses_the_marker(tmp_path):
    (tmp_path / "run.log").write_text(
        f"{RUN_MARKER} 2026-01-01 11:00:00 ===\n", encoding="utf-8"
    )
    start = last_run_start(tmp_path)
    assert isinstance(start, float)


def test_last_run_start_without_a_marker_is_none(tmp_path):
    (tmp_path / "run.log").write_text("no marker here\n", encoding="utf-8")
    assert last_run_start(tmp_path) is None


def test_spend_between_sums_and_filters_by_role(tmp_path):
    (tmp_path / "llm_spend.json").write_text(
        json.dumps(
            {
                "entries": [
                    {"ts": "2026-01-01T10:00:00+00:00", "role": "director", "cost_usd": 0.1},
                    {"ts": "2026-01-01T10:00:01+00:00", "role": "metadata", "cost_usd": 0.2},
                    {"ts": "2026-01-01T12:00:00+00:00", "role": "director", "cost_usd": 0.4},
                ]
            }
        ),
        encoding="utf-8",
    )
    start = epoch("2026-01-01T10:00:00+00:00")
    end = epoch("2026-01-01T11:00:00+00:00")
    assert spend_between(tmp_path, start, end) == pytest.approx(0.3)
    assert spend_between(tmp_path, start, end, role="director") == pytest.approx(0.1)
    assert spend_between(tmp_path, start, end, role="metadata") == pytest.approx(0.2)
    assert spend_between(tmp_path, start, end, role="vision") is None


def test_spend_between_missing_ledger_is_none(tmp_path):
    assert spend_between(tmp_path, 0.0) is None
