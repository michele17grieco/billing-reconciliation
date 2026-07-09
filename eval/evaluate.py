"""
Evaluate detection performance against the hidden ground truth.

Because the anomalies were seeded, we can measure precision / recall / F1 for three
systems and settle the project's central question empirically:

    (a) rules only      (b) ML only      (c) rules + ML in cascade

Everything is scored at a single canonical grain so the three systems are comparable:
  * invoice anomalies (duplicate, rate, fuel) key on invoice_id
  * short/over payments key on the invoice the payment settles (rules expose it via
    invoice_ref; the ML scores that invoice directly; ground truth is remapped to it)
  * unapplied cash keys on payment_id (payment-grain, rules-only by nature)

Writes output/metrics.json and output/eval_summary.csv, and prints the headline tables.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
import config as C          # noqa: E402
import db as D              # noqa: E402

INVOICE_TYPES = {"duplicate_invoice", "rate_discrepancy", "fuel_surcharge_error",
                 "short_or_over_pay"}


def _prf(detected: set, positives: set) -> dict:
    tp = len(detected & positives)
    fp = len(detected - positives)
    fn = len(positives - detected)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return dict(tp=tp, fp=fp, fn=fn,
                precision=round(precision, 4), recall=round(recall, 4), f1=round(f1, 4))


def canonical_ground_truth(con) -> pd.DataFrame:
    """Map every ground-truth label to its canonical detection key."""
    gt = pd.read_csv(C.GROUND_TRUTH_CSV)
    pay = D.table(con, "clean_payments")[["payment_id", "invoice_id"]]
    pay_to_inv = dict(zip(pay["payment_id"], pay["invoice_id"]))

    keys = []
    for row in gt.itertuples():
        t = row.anomaly_type
        if t == "short_or_over_pay":
            # settle-gap is attributed to the invoice the payment pays
            keys.append(pay_to_inv.get(row.entity_id, row.entity_id))
        else:
            keys.append(row.entity_id)   # invoice_id, or payment_id for unapplied_cash
    gt = gt.assign(key=keys)
    return gt


def detected_keys(flags: pd.DataFrame) -> set:
    """Collapse a flag table to canonical keys (invoice_ref when present, else entity)."""
    ref = flags["invoice_ref"].where(flags["invoice_ref"].notna(), flags["entity_id"])
    return set(ref.astype(str))


def run(con) -> dict:
    gt = canonical_ground_truth(con)
    positives = set(gt["key"].astype(str))

    rule_flags = D.table(con, "rule_flags")
    ml_flags = D.table(con, "ml_flags")

    rule_keys = detected_keys(rule_flags)
    ml_keys = detected_keys(ml_flags)
    combined_keys = rule_keys | ml_keys

    systems = {
        "Rules only": rule_keys,
        "ML only": ml_keys,
        "Rules + ML": combined_keys,
    }
    overall = {name: _prf(keys, positives) for name, keys in systems.items()}

    # ---- per anomaly type: recall of each system ------------------------------------
    per_type = {}
    for t, sub in gt.groupby("anomaly_type"):
        pos_t = set(sub["key"].astype(str))
        per_type[t] = {
            "count": len(pos_t),
            "rules_recall": round(len(rule_keys & pos_t) / len(pos_t), 4),
            "ml_recall": round(len(ml_keys & pos_t) / len(pos_t), 4),
            "combined_recall": round(len(combined_keys & pos_t) / len(pos_t), 4),
        }

    # ---- business context numbers ----------------------------------------------------
    exc = D.table(con, "exceptions_final")
    inv = D.table(con, "clean_invoices")
    pay = D.table(con, "clean_payments")
    matches = D.table(con, "payment_matches")

    match_rate = len(matches) / len(pay)
    total_at_risk = float(exc["amount_at_risk"].sum())
    by_type_risk = exc.groupby("exception_type")["amount_at_risk"].sum().round(0).to_dict()

    context = {
        "n_invoices": int(len(inv)),
        "n_payments": int(len(pay)),
        "match_rate": round(match_rate, 4),
        "n_exceptions": int(len(exc)),
        "total_at_risk_eur": round(total_at_risk, 0),
        "at_risk_by_type_eur": {k: float(v) for k, v in by_type_risk.items()},
        "anomaly_share": round(len(gt) / len(inv), 4),
    }

    metrics = {"overall": overall, "per_type": per_type, "context": context}

    C.OUTPUT_DIR.mkdir(exist_ok=True)
    (C.OUTPUT_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2))

    # tidy CSV for the dashboard / README
    rows = [{"system": s, **m} for s, m in overall.items()]
    pd.DataFrame(rows).to_csv(C.OUTPUT_DIR / "eval_summary.csv", index=False)

    _print(overall, per_type, context)
    return metrics


def _print(overall, per_type, context):
    print("\n" + "=" * 66)
    print("DETECTION PERFORMANCE  (vs. seeded ground truth)")
    print("=" * 66)
    print(f"{'System':<14}{'Precision':>11}{'Recall':>10}{'F1':>8}"
          f"{'TP':>7}{'FP':>7}{'FN':>6}")
    for name, m in overall.items():
        print(f"{name:<14}{m['precision']:>11.2%}{m['recall']:>10.2%}{m['f1']:>8.2f}"
              f"{m['tp']:>7}{m['fp']:>7}{m['fn']:>6}")

    print("\nRecall by anomaly type:")
    print(f"{'Type':<24}{'n':>6}{'Rules':>9}{'ML':>9}{'Combined':>11}")
    for t, m in sorted(per_type.items()):
        print(f"{t:<24}{m['count']:>6}{m['rules_recall']:>9.2%}"
              f"{m['ml_recall']:>9.2%}{m['combined_recall']:>11.2%}")

    print("\nBusiness context:")
    print(f"  match rate           {context['match_rate']:.2%}")
    print(f"  exceptions           {context['n_exceptions']:,}")
    print(f"  € at risk (total)    €{context['total_at_risk_eur']:,.0f}")


if __name__ == "__main__":
    con = D.connect()
    run(con)
    con.close()
