"""
End-to-end reconciliation pipeline — reproducible with a single command.

    python run_pipeline.py [--skip-generate]

Steps:
    1. generate synthetic data              (data/generate_data.py)
    2. load CSVs into SQLite                 (db.load_raw)
    3. SQL: cleaning -> matching -> rules -> exceptions   (sql/01..04)
    4. ML: Isolation Forest anomaly scoring  (ml/anomaly_detection.py)
    5. SQL: merge rule + ML exceptions       (sql/05_final.sql)
    6. evaluate vs. ground truth             (eval/evaluate.py)
    7. export exceptions + KPIs for Power BI (output/*.csv)

Synthetic data only — no proprietary information used.
"""
from __future__ import annotations

import argparse
import time

import pandas as pd

import config as C
import db as D
from data import generate_data
from ml import anomaly_detection
from eval import evaluate

SQL_STEPS = ["01_cleaning.sql", "02_matching.sql", "03_rules.sql", "04_exceptions.sql"]


def _banner(msg: str):
    print(f"\n\033[1m▶ {msg}\033[0m")


def main(skip_generate: bool = False):
    t0 = time.time()

    if not skip_generate:
        _banner("1/7  Generating synthetic data")
        generate_data.main()
    else:
        print("· skipping data generation (using existing CSVs)")

    con = D.connect()

    _banner("2/7  Loading CSVs into SQLite")
    D.load_raw(con)
    print(f"  db -> {C.DB_PATH}")

    _banner("3/7  Running SQL reconciliation layer")
    for step in SQL_STEPS:
        D.run_sql_file(con, C.SQL_DIR / step)
        print(f"  ran sql/{step}")

    _banner("4/7  ML anomaly detection (Isolation Forest)")
    anomaly_detection.run(con)

    _banner("5/7  Merging rule + ML exceptions")
    D.run_sql_file(con, C.SQL_DIR / "05_final.sql")
    print("  ran sql/05_final.sql")

    _banner("6/7  Evaluating against ground truth")
    evaluate.run(con)

    _banner("7/7  Exporting for Power BI")
    export(con)

    con.close()
    print(f"\n\033[1m✔ Pipeline complete in {time.time() - t0:.1f}s\033[0m")


def export(con):
    """Flat files the Power BI 'Reconciliation Control Tower' connects to."""
    C.OUTPUT_DIR.mkdir(exist_ok=True)
    D.table(con, "exceptions_final").to_csv(C.OUTPUT_DIR / "exceptions.csv", index=False)

    # monthly match-rate trend
    pay = D.table(con, "clean_payments")
    matches = D.table(con, "payment_matches")[["payment_id"]]
    pay["matched"] = pay["payment_id"].isin(matches["payment_id"]).astype(int)
    pay["month"] = pd.to_datetime(pay["payment_date"]).dt.strftime("%Y-%m")
    trend = (pay.groupby("month")
                .agg(payments=("payment_id", "count"), matched=("matched", "sum"))
                .assign(match_rate=lambda d: (d["matched"] / d["payments"]).round(4))
                .reset_index())
    trend.to_csv(C.OUTPUT_DIR / "match_rate_trend.csv", index=False)

    # top customers by € at risk
    exc = D.table(con, "exceptions_final")
    top = (exc.groupby("customer_id")["amount_at_risk"].sum()
              .sort_values(ascending=False).head(10).round(0).reset_index())
    top.to_csv(C.OUTPUT_DIR / "top_customers_at_risk.csv", index=False)
    print(f"  wrote exceptions.csv, match_rate_trend.csv, top_customers_at_risk.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-generate", action="store_true",
                    help="reuse existing CSVs instead of regenerating")
    main(**vars(ap.parse_args()))
