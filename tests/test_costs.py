import json

import pytest
import yaml

import common.config as config
import costs
from common.costs import compute_totals, connect, insert_entry
from common.exit_codes import EXIT_OK, EXIT_USAGE


def run_json(capsys, *argv):
    code = costs.main(list(argv))
    captured = capsys.readouterr()
    assert code == EXIT_OK, captured.err
    return json.loads(captured.out)


def add(capsys, ledger, kind, amount, day="2026-10-01"):
    return run_json(
        capsys,
        "--ledger",
        str(ledger),
        "add",
        "--kind",
        kind,
        "--amount",
        str(amount),
        "--date",
        day,
        "--json",
    )


def test_add_each_kind_then_list_and_totals(tmp_path, capsys):
    ledger = tmp_path / "costs.db"
    amounts = {
        "model_job": 1.5,
        "dev_spend": 2.0,
        "fixed_monthly": 3.0,
        "investment": 4.0,
        "donation": 5.0,
    }
    for kind, amount in amounts.items():
        summary = add(capsys, ledger, kind, amount)
        assert summary["entry"]["kind"] == kind
        assert summary["entry"]["amount_usd"] == amount
        assert summary["entry"]["source"] is None
    listing = run_json(capsys, "--ledger", str(ledger), "list", "--json")
    assert listing["count"] == 5
    assert {entry["kind"] for entry in listing["entries"]} == set(amounts)
    totals = run_json(capsys, "--ledger", str(ledger), "totals", "--json")
    for kind, amount in amounts.items():
        assert totals["by_kind"][kind] == amount


def test_import_jobs_idempotent(tmp_path, capsys):
    ledger = tmp_path / "costs.db"
    workspaces = tmp_path / "workspaces"
    for name, total in (("proj-a", 1.25), ("proj-b", 2.5)):
        project = workspaces / name
        project.mkdir(parents=True)
        (project / "llm_spend.json").write_text(
            json.dumps(
                {"total_usd": total, "entries": [{"cost_usd": total}]}
            ),
            encoding="utf-8",
        )
    first = run_json(
        capsys, "--ledger", str(ledger), "import-jobs", "--workspaces", str(workspaces), "--json"
    )
    assert first["count"] == 2
    assert all(row["action"] == "created" for row in first["imported"])
    totals_first = run_json(capsys, "--ledger", str(ledger), "totals", "--json")
    second = run_json(
        capsys, "--ledger", str(ledger), "import-jobs", "--workspaces", str(workspaces), "--json"
    )
    assert second["count"] == 2
    assert all(row["action"] == "updated" for row in second["imported"])
    totals_second = run_json(capsys, "--ledger", str(ledger), "totals", "--json")
    assert totals_first["model_total_usd"] == totals_second["model_total_usd"] == 3.75
    listing = run_json(capsys, "--ledger", str(ledger), "list", "--kind", "model_job", "--json")
    assert listing["count"] == 2


def test_import_dev_idempotent(tmp_path, capsys):
    ledger = tmp_path / "costs.db"
    spend = tmp_path / "run-spend.json"
    spend.write_text(
        json.dumps(
            [
                {"ts": "2026-10-01T10:00:00Z", "cost_usd": 0.5},
                {"ts": "2026-10-01T12:00:00+00:00", "cost_usd": 0.25},
                {"ts": "2026-10-02T09:00:00Z", "cost_usd": 1.0},
                {"ts": "not-a-date", "cost_usd": 9.0},
                {"cost_usd": 9.0},
            ]
        ),
        encoding="utf-8",
    )
    first = run_json(
        capsys, "--ledger", str(ledger), "import-dev", "--from", str(spend), "--json"
    )
    assert first["count"] == 2
    totals_first = run_json(capsys, "--ledger", str(ledger), "totals", "--json")
    second = run_json(
        capsys, "--ledger", str(ledger), "import-dev", "--from", str(spend), "--json"
    )
    assert second["count"] == 2
    totals_second = run_json(capsys, "--ledger", str(ledger), "totals", "--json")
    assert totals_first["development_total_usd"] == totals_second["development_total_usd"] == 1.75
    listing = run_json(capsys, "--ledger", str(ledger), "list", "--kind", "dev_spend", "--json")
    assert listing["count"] == 2
    assert {entry["source"] for entry in listing["entries"]} == {"2026-10-01", "2026-10-02"}


def test_totals_balance_math(tmp_path, capsys):
    ledger = tmp_path / "costs.db"
    add(capsys, ledger, "fixed_monthly", 3.0, day="2026-10-01")
    for kind, amount in (
        ("donation", 100.0),
        ("model_job", 10.0),
        ("dev_spend", 5.0),
        ("investment", 20.0),
    ):
        add(capsys, ledger, kind, amount, day="2026-12-01")
    totals = run_json(
        capsys, "--ledger", str(ledger), "totals", "--month", "2026-12", "--json"
    )
    assert totals["fixed_months"] == 3
    assert totals["fixed_total_usd"] == 9.0
    assert totals["balance_usd"] == 100.0 - (10.0 + 5.0 + 20.0 + 9.0)


def test_list_kind_filters(tmp_path, capsys):
    ledger = tmp_path / "costs.db"
    add(capsys, ledger, "donation", 1.0)
    add(capsys, ledger, "model_job", 2.0)
    listing = run_json(capsys, "--ledger", str(ledger), "list", "--kind", "donation", "--json")
    assert listing["count"] == 1
    assert listing["entries"][0]["kind"] == "donation"


def test_missing_ledger_usage_error(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(config.COSTS_DB_ENV, raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {"models": {"coder": "x"}, "llm_budget_usd": 5.0, "llm_backend": "mock"}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_PATH", cfg)
    monkeypatch.setattr(config, "EXAMPLE_PATH", tmp_path / "missing.example")
    code = costs.main(["totals", "--json"])
    assert code == EXIT_USAGE
    captured = capsys.readouterr()
    assert "--ledger" in captured.err
    assert config.COSTS_DB_ENV in captured.err
    assert "costs.path" in captured.err
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["exit_code"] == EXIT_USAGE


def test_broken_config_reports_the_config_error(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(config.COSTS_DB_ENV, raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("models: {}\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", cfg)
    monkeypatch.setattr(config, "EXAMPLE_PATH", tmp_path / "missing.example")
    code = costs.main(["totals", "--json"])
    assert code == EXIT_USAGE
    captured = capsys.readouterr()
    assert "models must not be empty" in captured.err
    assert "no costs ledger configured" not in captured.err
    payload = json.loads(captured.out)
    assert payload["ok"] is False and payload["exit_code"] == EXIT_USAGE


def test_json_summary_fields_on_every_subcommand(tmp_path, capsys):
    ledger = tmp_path / "costs.db"
    workspaces = tmp_path / "workspaces"
    (workspaces / "p").mkdir(parents=True)
    (workspaces / "p" / "llm_spend.json").write_text(
        json.dumps({"total_usd": 1.0, "entries": []}), encoding="utf-8"
    )
    spend = tmp_path / "run-spend.json"
    spend.write_text(
        json.dumps([{"ts": "2026-10-01T00:00:00Z", "cost_usd": 0.1}]), encoding="utf-8"
    )
    commands = [
        ["add", "--kind", "donation", "--amount", "1", "--date", "2026-10-01"],
        ["import-jobs", "--workspaces", str(workspaces)],
        ["import-dev", "--from", str(spend)],
        ["totals"],
        ["list"],
    ]
    for command in commands:
        summary = run_json(capsys, "--ledger", str(ledger), *command, "--json")
        assert summary["wall_time_s"] >= 0
        assert summary["llm_cost_usd"] == 0.0


def test_usage_errors(tmp_path, capsys):
    ledger = tmp_path / "costs.db"
    for argv in (
        ["add", "--kind", "nope", "--amount", "1", "--json"],
        ["add", "--kind", "donation", "--amount", "0", "--json"],
        ["add", "--kind", "donation", "--amount", "1", "--date", "01-10-2026", "--json"],
        ["totals", "--month", "2026", "--json"],
        ["list", "--kind", "nope", "--json"],
    ):
        code = costs.main(["--ledger", str(ledger), *argv])
        assert code == EXIT_USAGE
        assert "[error]" in capsys.readouterr().err


def test_help_lists_subcommands_and_examples(capsys):
    with pytest.raises(SystemExit) as excinfo:
        costs.main(["--help"])
    assert excinfo.value.code == EXIT_OK
    out = capsys.readouterr().out
    for command in ("add", "import-jobs", "import-dev", "totals", "list"):
        assert command in out
    assert "examples:" in out


def test_shared_compute_totals_matches_the_cli(tmp_path, capsys):
    ledger = tmp_path / "costs.db"
    add(capsys, ledger, "fixed_monthly", 3.0, day="2026-10-01")
    add(capsys, ledger, "donation", 100.0, day="2026-12-01")
    add(capsys, ledger, "model_job", 10.0, day="2026-12-01")
    connection = connect(ledger)
    try:
        totals = compute_totals(connection, "2026-12")
    finally:
        connection.close()
    assert totals["fixed_months"] == 3
    assert totals["fixed_total_usd"] == 9.0
    assert totals["balance_usd"] == 100.0 - (10.0 + 9.0)
    assert "ok" not in totals
    assert "wall_time_s" not in totals


def test_totals_includes_upkeep_and_balance_subtracts_it(tmp_path, capsys, monkeypatch):
    ledger = tmp_path / "costs.db"
    add(capsys, ledger, "model_job", 10.0, day="2026-12-01")
    add(capsys, ledger, "donation", 100.0, day="2026-12-01")
    monkeypatch.setenv(config.UPKEEP_PERCENT_ENV, "10")
    totals = run_json(
        capsys, "--ledger", str(ledger), "totals", "--month", "2026-12", "--json"
    )
    assert totals["upkeep_percent"] == 10.0
    assert totals["upkeep_usd"] == 1.0
    assert totals["total_cost_usd"] == 11.0
    assert totals["balance_usd"] == 100.0 - 11.0
