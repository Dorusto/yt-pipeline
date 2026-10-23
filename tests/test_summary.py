import time

from common.summary import summary_fields


def test_summary_fields_reports_wall_time_and_llm_cost():
    fields = summary_fields(time.monotonic() - 1.0, 0.01234567)
    assert set(fields) == {"wall_time_s", "llm_cost_usd"}
    assert fields["wall_time_s"] >= 1.0
    assert fields["llm_cost_usd"] == 0.01234567


def test_summary_fields_defaults_to_zero_cost():
    fields = summary_fields(time.monotonic())
    assert fields["llm_cost_usd"] == 0.0
    assert fields["wall_time_s"] >= 0.0
