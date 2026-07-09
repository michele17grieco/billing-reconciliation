"""
SQLite helpers: load the raw CSVs into the database and run the SQL layer.

Only two things happen in Python here:
  * mixed date-format parsing (not tractable in pure SQLite SQL), and
  * bulk-loading the CSVs into `raw_*` tables.

Everything downstream — cleaning, matching, duplicate detection, rate revalidation
and the final exceptions table — is expressed in SQL under ./sql, which is exactly the
skill set finance/ops reconciliation roles look for.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

import config as C


def connect() -> sqlite3.Connection:
    C.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(C.DB_PATH)
    con.execute("PRAGMA journal_mode = WAL;")
    return con


def _parse_dates(s: pd.Series) -> pd.Series:
    """Parse a column that mixes YYYY-MM-DD, DD/MM/YYYY, MM-DD-YYYY, DD.MM.YYYY."""
    out = pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")
    for fmt in ("%d/%m/%Y", "%m-%d-%Y", "%d.%m.%Y"):
        mask = out.isna()
        if not mask.any():
            break
        out.loc[mask] = pd.to_datetime(s[mask], format=fmt, errors="coerce")
    return out.dt.strftime("%Y-%m-%d")


def load_raw(con: sqlite3.Connection) -> None:
    """Load every CSV artefact into a raw_* table, parsing dates to ISO on the way in."""
    customers = pd.read_csv(C.CUSTOMERS_CSV)
    rate_card = pd.read_csv(C.RATE_CARD_CSV)
    fuel = pd.read_csv(C.FUEL_INDEX_CSV)
    invoices = pd.read_csv(C.INVOICES_CSV, dtype={"invoice_id": str})
    payments = pd.read_csv(C.PAYMENTS_CSV, dtype={"invoice_id": str, "payment_id": str})

    invoices["invoice_date"] = _parse_dates(invoices["invoice_date"])
    payments["payment_date"] = _parse_dates(payments["payment_date"])
    payments["invoice_id"] = payments["invoice_id"].fillna("")

    customers.to_sql("raw_customers", con, if_exists="replace", index=False)
    rate_card.to_sql("rate_card", con, if_exists="replace", index=False)
    fuel.to_sql("fuel_index", con, if_exists="replace", index=False)
    invoices.to_sql("raw_invoices", con, if_exists="replace", index=False)
    payments.to_sql("raw_payments", con, if_exists="replace", index=False)
    con.commit()


def run_sql_file(con: sqlite3.Connection, path: Path) -> None:
    con.executescript(path.read_text())
    con.commit()


def table(con: sqlite3.Connection, name: str) -> pd.DataFrame:
    return pd.read_sql(f"SELECT * FROM {name}", con)
