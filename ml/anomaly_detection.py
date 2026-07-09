"""
Unsupervised anomaly detection with an Isolation Forest.

The rule layer nails *known* error shapes (duplicates, gross rate errors, unmatched cash)
but is blind to subtle ones that sit under fixed thresholds — a fuel surcharge computed
at the wrong percentage, a customer who quietly short-pays by ~1%. This model learns the
normal shape of an invoice and isolates the ones that don't fit, with no labels.

Features (per invoice, all derived from the SQL layer):
    rate_dev_pct     billed vs rate-card expected total
    surcharge_dev    actual surcharge/base ratio vs the month's expected fuel %
    amount_z         total amount z-scored within (segment, service)
    weight_z         billed weight z-scored within (segment, service)
    payment_ratio    amount paid / invoice total (1.0 if unpaid; flagged separately)
    pay_vs_terms     days-to-pay minus the customer's agreed terms
    is_unpaid        no matched payment

Writes two tables:
    invoice_scores   every invoice with its anomaly score + flag
    ml_flags         flagged invoices, in the shared exceptions schema
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

sys.path.append(str(Path(__file__).resolve().parents[1]))
import config as C          # noqa: E402
import db as D              # noqa: E402

FEATURES = ["rate_dev_pct", "surcharge_dev", "amount_z", "weight_z",
            "payment_ratio", "pay_vs_terms", "is_unpaid"]


def build_features(con) -> pd.DataFrame:
    inv = D.table(con, "invoice_expected")
    matches = D.table(con, "payment_matches")

    # best payment per invoice (closest amount) for the payment-behaviour features
    matches = matches.sort_values(
        "amount_paid", key=lambda s: (s - matches["total_amount"]).abs()
    )
    best = matches.drop_duplicates("matched_invoice_id", keep="first")
    best = best.rename(columns={"matched_invoice_id": "invoice_id"})[
        ["invoice_id", "amount_paid", "payment_date"]
    ]
    df = inv.merge(best, on="invoice_id", how="left")

    # pricing signals
    df["rate_dev_pct"] = (df["total_amount"] - df["expected_total"]) / df["expected_total"]
    surcharge_ratio = df["fuel_surcharge"] / df["base_amount"].replace(0, np.nan)
    df["surcharge_dev"] = surcharge_ratio - df["expected_fuel_pct"]

    # distributional signals within peer group (segment x service)
    grp = df.groupby(["segment", "service"])
    df["amount_z"] = grp["total_amount"].transform(lambda s: (s - s.mean()) / (s.std() + 1e-9))
    df["weight_z"] = grp["billed_weight_kg"].transform(lambda s: (s - s.mean()) / (s.std() + 1e-9))

    # payment-behaviour signals
    df["is_unpaid"] = df["amount_paid"].isna().astype(int)
    df["payment_ratio"] = (df["amount_paid"] / df["total_amount"]).fillna(1.0)
    days_to_pay = (pd.to_datetime(df["payment_date"]) - pd.to_datetime(df["invoice_date"])).dt.days
    df["pay_vs_terms"] = (days_to_pay - df["payment_terms_days"]).fillna(0.0)

    df["surcharge_dev"] = df["surcharge_dev"].fillna(0.0)
    return df


def run(con) -> pd.DataFrame:
    df = build_features(con)

    X = StandardScaler().fit_transform(df[FEATURES].to_numpy())
    model = IsolationForest(
        n_estimators=C.IFOREST_N_ESTIMATORS,
        contamination=C.IFOREST_CONTAMINATION,
        random_state=C.SEED,
        n_jobs=-1,
    )
    model.fit(X)
    df["ml_flag"] = (model.predict(X) == -1).astype(int)
    # higher score = more anomalous (score_samples returns higher for inliers)
    df["ml_score"] = -model.score_samples(X)

    # persist full scores
    scores = df[["invoice_id", "customer_id", "ml_score", "ml_flag"]]
    scores.to_sql("invoice_scores", con, if_exists="replace", index=False)

    # € at risk = the larger of the pricing error and any settlement gap
    pricing_err = (df["total_amount"] - df["expected_total"]).abs()
    settle_err = (df["amount_paid"] - df["total_amount"]).abs().fillna(0.0)
    amount_at_risk = np.maximum(pricing_err, settle_err).round(2)

    flags = df[df["ml_flag"] == 1].copy()
    ml_flags = pd.DataFrame({
        "entity_type": "invoice",
        "entity_id": flags["invoice_id"],
        "invoice_ref": flags["invoice_id"],
        "customer_id": flags["customer_id"],
        "exception_type": "ml_anomaly",
        "amount_at_risk": amount_at_risk[flags.index].values,
        "ref_date": flags["invoice_date"],
        "detection_method": "ml",
    })
    ml_flags.to_sql("ml_flags", con, if_exists="replace", index=False)
    con.commit()

    print(f"  Isolation Forest flagged {len(ml_flags):,} / {len(df):,} invoices "
          f"({len(ml_flags)/len(df)*100:.2f}%)")
    return ml_flags


if __name__ == "__main__":
    con = D.connect()
    run(con)
    con.close()
