#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep a small SQLite ledger of what the project costs and earns.

Usage:
    python scripts/costs.py --ledger <costs.db> add --kind donation --amount 10 --date 2026-10-01
    python scripts/costs.py --ledger <costs.db> import-jobs --workspaces <workspaces>
    python scripts/costs.py --ledger <costs.db> import-dev --from <run-spend.json>
    python scripts/costs.py --ledger <costs.db> import-donations --from <donations.csv>
    python scripts/costs.py --ledger <costs.db> totals --json
    python scripts/costs.py --ledger <costs.db> list --kind model_job

The ledger is a SQLite file outside the repository. Its path comes from
``--ledger``, else ``YT_PIPELINE_COSTS_DB``, else ``costs.path`` in the config,
else a usage error naming those three ways. Five kinds are tracked:
``model_job``, ``dev_spend``, ``fixed_monthly``, ``investment`` and
``donation``. Imports are idempotent by ``source`` (the project name for model
jobs, the date for build-agent spend), so re-running never duplicates a row.
No model is called, so ``llm_cost_usd`` is always 0.0.
"""

import argparse
import csv
import json
import math
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from common.config import ConfigError, costs_db, load_config, no_costs_message, upkeep_percent
from common.costs import KINDS, compute_totals, connect, insert_entry, list_entries, upsert_by_source
from common.errors import report_error
from common.exit_codes import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from common.llm import LLMError, read_ledger
from common.spend import read_spend_ledger
from common.summary import summary_fields

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


class CostsError(RuntimeError):
    """Base error for the costs ledger tool."""


class CostsUsageError(CostsError):
    """Raised for a bad flag, kind, date or month, or a missing ledger path."""


class CostsRuntimeError(CostsError):
    """Raised when SQLite or the filesystem fails after the run started."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="costs.py",
        description=(
            "Keep a SQLite ledger of model jobs, build-agent spend, fixed monthly "
            "costs, investments and donations, with idempotent imports and a totals view."
        ),
        epilog=(
            "examples:\n"
            "  python scripts/costs.py --ledger <costs.db> add --kind donation --amount 10 --date 2026-10-01\n"
            "  python scripts/costs.py --ledger <costs.db> import-jobs --workspaces <workspaces>\n"
            "  python scripts/costs.py --ledger <costs.db> import-dev --from <run-spend.json>\n"
            "  python scripts/costs.py --ledger <costs.db> import-opencode --db <opencode.db> --match yt-pipeline\n"
            "  python scripts/costs.py --ledger <costs.db> import-donations --from <donations.csv>\n"
            "  python scripts/costs.py --ledger <costs.db> totals --json\n"
            "  python scripts/costs.py --ledger <costs.db> list --kind model_job\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--ledger",
        default=None,
        help=(
            "SQLite ledger file; pass it here, set the YT_PIPELINE_COSTS_DB "
            "environment variable, or set costs.path in the config"
        ),
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="append one manual entry")
    add.add_argument("--kind", required=True, help=f"one of: {', '.join(KINDS)}")
    add.add_argument("--amount", type=float, required=True, help="amount in USD, greater than 0")
    add.add_argument("--date", default=None, help="entry date YYYY-MM-DD (default: today)")
    add.add_argument("--note", default="", help="free-text note")
    add.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    jobs = subparsers.add_parser("import-jobs", help="import model-job spend from workspaces")
    jobs.add_argument("--workspaces", required=True, help="folder holding one workspace per project")
    jobs.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    dev = subparsers.add_parser("import-dev", help="import build-agent spend from a run-spend file")
    dev.add_argument("--from", dest="from_file", required=True, help="run-spend JSON file")
    dev.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    agents = subparsers.add_parser(
        "import-opencode",
        help="import build-agent spend per day from opencode's session database, only for this project's folders",
    )
    agents.add_argument("--db", required=True, help="opencode.db (opencode's session database)")
    agents.add_argument(
        "--match", action="append", required=True,
        help="a folder name part that marks this project's sessions (repeatable), for example yt-pipeline",
    )
    agents.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    donations = subparsers.add_parser(
        "import-donations", help="import donations from a CSV export"
    )
    donations.add_argument("--from", dest="from_file", required=True, help="donations CSV file")
    donations.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    totals = subparsers.add_parser("totals", help="sum the ledger and compute the balance")
    totals.add_argument("--month", default=None, help="limit the sums to one month YYYY-MM")
    totals.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    listing = subparsers.add_parser("list", help="list ledger entries")
    listing.add_argument("--kind", default=None, help=f"filter by kind: {', '.join(KINDS)}")
    listing.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    return parser


def resolve_ledger(args):
    """Resolve the ledger path or raise the three-ways usage error."""
    config = load_config()
    path = costs_db(args.ledger, config)
    if path is None:
        raise CostsUsageError(no_costs_message("--ledger"))
    return path


def command_add(connection, args, started):
    if args.kind not in KINDS:
        raise CostsUsageError(
            f"unknown kind '{args.kind}'; valid kinds: {', '.join(KINDS)}"
        )
    amount = args.amount
    if amount is None or not math.isfinite(amount) or amount <= 0:
        raise CostsUsageError("--amount must be a finite number greater than 0")
    entry_date = args.date if args.date is not None else date.today().isoformat()
    if not DATE_RE.match(entry_date):
        raise CostsUsageError(f"--date must match YYYY-MM-DD, got '{entry_date}'")
    entry_id = insert_entry(connection, entry_date, args.kind, amount, args.note, None)
    entry = {
        "id": entry_id,
        "date": entry_date,
        "kind": args.kind,
        "amount_usd": round(float(amount), 2),
        "note": args.note,
        "source": None,
    }
    return {"ok": True, "entry": entry, **summary_fields(started)}


def command_import_jobs(connection, args, started):
    workspaces = Path(args.workspaces).expanduser()
    if not workspaces.is_dir():
        raise CostsUsageError(f"workspaces folder not found: {workspaces}")
    imported = []
    for project in sorted(path for path in workspaces.iterdir() if path.is_dir()):
        ledger_file = project / "llm_spend.json"
        if not ledger_file.is_file():
            continue
        try:
            total = read_ledger(ledger_file)["total_usd"]
        except LLMError as exc:
            print(f"[warn] skipping {project.name}: {exc}", file=sys.stderr)
            continue
        entry_date = datetime.fromtimestamp(ledger_file.stat().st_mtime).date().isoformat()
        _, action = upsert_by_source(
            connection,
            "model_job",
            project.name,
            entry_date,
            total,
            "imported from llm_spend.json",
        )
        imported.append(
            {"project": project.name, "total_usd": round(float(total), 2), "action": action}
        )
    return {
        "ok": True,
        "imported": imported,
        "count": len(imported),
        **summary_fields(started),
    }


def command_import_dev(connection, args, started):
    source_file = Path(args.from_file).expanduser()
    if not source_file.is_file():
        raise CostsUsageError(f"run-spend file not found: {source_file}")
    try:
        entries = read_spend_ledger(source_file)
    except ValueError as exc:
        raise CostsUsageError(f"could not read run-spend file {source_file}: {exc}") from exc
    per_day = {}
    for entry in entries:
        stamp = entry.get("ts")
        cost = entry.get("cost_usd")
        if not isinstance(stamp, str) or isinstance(cost, bool) or not isinstance(cost, (int, float)):
            continue
        try:
            day = datetime.fromisoformat(stamp.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            continue
        per_day[day] = per_day.get(day, 0.0) + float(cost)
    imported = []
    for day in sorted(per_day):
        _, action = upsert_by_source(
            connection, "dev_spend", day, day, per_day[day], "imported from run-spend"
        )
        imported.append(
            {"date": day, "cost_usd": round(per_day[day], 2), "action": action}
        )
    return {
        "ok": True,
        "imported": imported,
        "count": len(imported),
        **summary_fields(started),
    }


def command_import_opencode(connection, args, started):
    db_file = Path(args.db).expanduser()
    if not db_file.is_file():
        raise CostsUsageError(f"opencode database not found: {db_file}")
    try:
        source = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
        try:
            rows = source.execute("SELECT directory, cost, time_created FROM session").fetchall()
        finally:
            source.close()
    except sqlite3.Error as exc:
        raise CostsUsageError(f"could not read opencode database {db_file}: {exc}") from exc
    per_day = {}
    for directory, cost, created in rows:
        if not directory or not cost or not any(part in directory for part in args.match):
            continue
        day = datetime.fromtimestamp((created or 0) / 1000, timezone.utc).date().isoformat()
        per_day[day] = per_day.get(day, 0.0) + float(cost)
    imported = []
    for day in sorted(per_day):
        _, action = upsert_by_source(
            connection, "dev_spend", f"opencode:{day}", day, per_day[day],
            "build agents (opencode sessions of this project)",
        )
        imported.append({"date": day, "cost_usd": round(per_day[day], 2), "action": action})
    return {"ok": True, "imported": imported, "count": len(imported), **summary_fields(started)}


def csv_cell(row, index):
    """The trimmed cell at ``index``, or '' when the column is absent or short."""
    if index is None or index >= len(row):
        return ""
    return row[index].strip()


def command_import_donations(connection, args, started):
    source_file = Path(args.from_file).expanduser()
    if not source_file.is_file():
        raise CostsUsageError(f"donations CSV not found: {source_file}")
    imported = []
    skipped = 0
    try:
        with source_file.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                raise CostsUsageError(f"donations CSV is empty: {source_file}") from None
            columns = {name.strip().lower(): index for index, name in enumerate(header)}
            for required in ("date", "amount"):
                if required not in columns:
                    raise CostsUsageError(
                        f"donations CSV is missing the '{required}' column: {source_file}"
                    )
            date_index = columns.get("date")
            amount_index = columns.get("amount")
            note_index = columns.get("note")
            id_index = columns.get("id")
            for row in reader:
                entry_date = csv_cell(row, date_index)
                raw_amount = csv_cell(row, amount_index)
                note = csv_cell(row, note_index)
                donation_id = csv_cell(row, id_index)
                if not DATE_RE.match(entry_date):
                    print(
                        f"[warn] skipping row {reader.line_num}: bad date '{entry_date}'",
                        file=sys.stderr,
                    )
                    skipped += 1
                    continue
                try:
                    amount = float(raw_amount.lstrip("$\u20ac").replace(",", ""))
                except ValueError:
                    amount = None
                if amount is None or not math.isfinite(amount) or amount <= 0:
                    print(
                        f"[warn] skipping row {reader.line_num}: bad amount '{raw_amount}'",
                        file=sys.stderr,
                    )
                    skipped += 1
                    continue
                source = "donation:" + (
                    donation_id if donation_id else f"{entry_date}:{amount:.2f}:{note}"
                )
                _, action = upsert_by_source(
                    connection, "donation", source, entry_date, amount, note or "imported donation"
                )
                imported.append(
                    {"date": entry_date, "amount_usd": round(amount, 2), "action": action}
                )
    except (OSError, csv.Error) as exc:
        raise CostsUsageError(f"could not read donations CSV {source_file}: {exc}") from exc
    return {
        "ok": True,
        "imported": imported,
        "count": len(imported),
        "skipped": skipped,
        **summary_fields(started),
    }


def command_totals(connection, args, started):
    month = args.month
    if month is not None and not MONTH_RE.match(month):
        raise CostsUsageError(f"--month must match YYYY-MM, got '{month}'")
    totals = compute_totals(connection, month, upkeep_percent=upkeep_percent())
    return {"ok": True, **totals, **summary_fields(started)}


def command_list(connection, args, started):
    if args.kind is not None and args.kind not in KINDS:
        raise CostsUsageError(
            f"unknown kind '{args.kind}'; valid kinds: {', '.join(KINDS)}"
        )
    entries = list_entries(connection, args.kind)
    return {"ok": True, "entries": entries, "count": len(entries), **summary_fields(started)}


def emit(command, summary, json_mode):
    if json_mode:
        print(json.dumps(summary))
        return
    if command == "add":
        entry = summary["entry"]
        print(
            f"added {entry['kind']} ${entry['amount_usd']:.2f} on {entry['date']} (id {entry['id']})"
        )
    elif command == "import-jobs":
        for row in summary["imported"]:
            print(f"{row['action']} {row['project']}: ${row['total_usd']:.2f}")
        print(f"imported {summary['count']} project(s)")
    elif command == "import-dev":
        for row in summary["imported"]:
            print(f"{row['action']} {row['date']}: ${row['cost_usd']:.2f}")
        print(f"imported {summary['count']} day(s)")
    elif command == "import-opencode":
        for row in summary["imported"]:
            print(f"{row['action']} {row['date']}: ${row['cost_usd']:.2f}")
        print(f"imported {summary['count']} day(s)")
    elif command == "import-donations":
        for row in summary["imported"]:
            print(f"{row['action']} {row['date']}: ${row['amount_usd']:.2f}")
        print(f"imported {summary['count']} donation(s)")
    elif command == "totals":
        print(f"month: {summary['month'] or 'all'}")
        for kind, value in summary["by_kind"].items():
            print(f"  {kind}: ${value:.2f}")
        print(
            f"  fixed monthly: ${summary['monthly_running_usd']:.2f}"
            f" x {summary['fixed_months']} = ${summary['fixed_total_usd']:.2f}"
        )
        print(f"  balance: ${summary['balance_usd']:.2f}")
    elif command == "list":
        for entry in summary["entries"]:
            print(
                f"{entry['id']:>4}  {entry['date']}  {entry['kind']:<14}"
                f" ${entry['amount_usd']:.2f}  {entry['note']}"
            )
        print(f"{summary['count']} entries")


def main(argv=None):
    args = build_parser().parse_args(argv)
    started = time.monotonic()
    try:
        ledger = resolve_ledger(args)
    except (CostsUsageError, ConfigError) as exc:
        return report_error(exc, EXIT_USAGE, args.json)
    try:
        connection = connect(ledger)
    except (sqlite3.Error, OSError) as exc:
        return report_error(
            CostsRuntimeError(f"could not open ledger {ledger}: {exc}"), EXIT_RUNTIME, args.json
        )
    try:
        if args.command == "add":
            summary = command_add(connection, args, started)
        elif args.command == "import-jobs":
            summary = command_import_jobs(connection, args, started)
        elif args.command == "import-dev":
            summary = command_import_dev(connection, args, started)
        elif args.command == "import-opencode":
            summary = command_import_opencode(connection, args, started)
        elif args.command == "import-donations":
            summary = command_import_donations(connection, args, started)
        elif args.command == "totals":
            summary = command_totals(connection, args, started)
        else:
            summary = command_list(connection, args, started)
    except CostsUsageError as exc:
        return report_error(exc, EXIT_USAGE, args.json)
    except (sqlite3.Error, OSError) as exc:
        return report_error(
            CostsRuntimeError(f"ledger {ledger} failed: {exc}"), EXIT_RUNTIME, args.json
        )
    finally:
        connection.close()
    emit(args.command, summary, args.json)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
