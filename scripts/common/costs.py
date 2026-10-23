# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared costs-ledger logic used by ``scripts/costs.py`` (the CLI) and ``scripts/serve.py`` (the Costs page).

No tool imports another tool: both read the same ledger through this module.
"""

import sqlite3
from datetime import date

KINDS = ("model_job", "dev_spend", "fixed_monthly", "investment", "donation")

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  kind TEXT NOT NULL,
  amount_usd REAL NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  source TEXT
)
"""


def connect(path):
    """Open the ledger, creating the entries table on first use."""
    connection = sqlite3.connect(str(path))
    connection.execute(SCHEMA)
    connection.commit()
    return connection


def insert_entry(connection, entry_date, kind, amount, note, source):
    """Insert one entry and return its id."""
    cursor = connection.execute(
        "INSERT INTO entries (date, kind, amount_usd, note, source) VALUES (?, ?, ?, ?, ?)",
        (entry_date, kind, float(amount), note, source),
    )
    connection.commit()
    return cursor.lastrowid


def upsert_by_source(connection, kind, source, entry_date, amount, note):
    """Update the entry with this kind and source, or insert it; return (id, action)."""
    row = connection.execute(
        "SELECT id FROM entries WHERE kind=? AND source=?", (kind, source)
    ).fetchone()
    if row is None:
        return insert_entry(connection, entry_date, kind, amount, note, source), "created"
    connection.execute(
        "UPDATE entries SET amount_usd=?, date=?, note=? WHERE id=?",
        (float(amount), entry_date, note, row[0]),
    )
    connection.commit()
    return row[0], "updated"


def compute_totals(connection, month=None):
    """Sum the ledger per kind and compute the balance, without ``ok`` or summary fields."""
    rows = connection.execute("SELECT date, kind, amount_usd FROM entries").fetchall()
    selected = rows if month is None else [row for row in rows if row[0].startswith(month + "-")]
    by_kind = {kind: 0.0 for kind in KINDS}
    for _, kind, amount in selected:
        if kind in by_kind:
            by_kind[kind] += float(amount)
    fixed_dates = [row[0] for row in rows if row[1] == "fixed_monthly"]
    monthly_running = sum(
        (float(row[2]) for row in rows if row[1] == "fixed_monthly"), 0.0
    )
    if fixed_dates:
        first = min(fixed_dates)
        first_year, first_month = int(first[:4]), int(first[5:7])
        if month is not None:
            ref_year, ref_month = int(month[:4]), int(month[5:7])
        else:
            today = date.today()
            ref_year, ref_month = today.year, today.month
        fixed_months = (ref_year - first_year) * 12 + (ref_month - first_month) + 1
    else:
        fixed_months = 0
    fixed_total = monthly_running * fixed_months
    balance = by_kind["donation"] - (
        by_kind["model_job"] + by_kind["dev_spend"] + by_kind["investment"] + fixed_total
    )
    return {
        "month": month,
        "by_kind": {kind: round(value, 2) for kind, value in by_kind.items()},
        "monthly_running_usd": round(float(monthly_running), 2),
        "development_total_usd": round(by_kind["dev_spend"], 2),
        "model_total_usd": round(by_kind["model_job"], 2),
        "donations_usd": round(by_kind["donation"], 2),
        "investments_usd": round(by_kind["investment"], 2),
        "fixed_months": fixed_months,
        "fixed_total_usd": round(float(fixed_total), 2),
        "balance_usd": round(balance, 2),
    }


def list_entries(connection, kind=None):
    """The ledger entries as dicts, oldest first, optionally filtered by kind."""
    columns = "id, date, kind, amount_usd, note, source"
    if kind is None:
        rows = connection.execute(f"SELECT {columns} FROM entries ORDER BY id").fetchall()
    else:
        rows = connection.execute(
            f"SELECT {columns} FROM entries WHERE kind=? ORDER BY id", (kind,)
        ).fetchall()
    return [
        {
            "id": row[0],
            "date": row[1],
            "kind": row[2],
            "amount_usd": round(float(row[3]), 2),
            "note": row[4],
            "source": row[5],
        }
        for row in rows
    ]
