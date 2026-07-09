"""
Synthetic billing dataset generator.

Produces five reference/working tables and one hidden ground-truth table:

    customers.csv     ~500 accounts (segment, country, terms, assigned rate card)
    rate_card.csv     official price list  (rate_card_id x service x zone -> rate/kg)
    fuel_index.csv     monthly fuel surcharge %  (the "correct" surcharge basis)
    invoices.csv      ~50,000 invoices over 12 months
    payments.csv      ~48,500 payments
    ground_truth.csv  hidden anomaly labels (invoice_id / payment_id -> anomaly_type)

Anomalies are injected at the rates in config.ANOMALY_RATES and recorded in
ground_truth.csv only. The working tables carry NO label, so the reconciliation
engine and the ML model see exactly what an analyst would see in production.

Run:  python data/generate_data.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

sys.path.append(str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402


# ======================================================================================
# Helpers
# ======================================================================================
def _weighted(rng: np.random.Generator, choices, weights, size):
    return rng.choice(choices, size=size, p=np.asarray(weights) / np.sum(weights))


def _format_date_mixed(rng: np.random.Generator, dates: pd.Series) -> pd.Series:
    """Emit dates in a mix of formats to force real date parsing downstream."""
    if not C.MIXED_DATE_FORMAT:
        return dates.dt.strftime("%Y-%m-%d")
    fmts = ["%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%d.%m.%Y"]
    picks = rng.choice(len(fmts), size=len(dates))
    out = [d.strftime(fmts[p]) for d, p in zip(dates, picks)]
    return pd.Series(out, index=dates.index)


# ======================================================================================
# Reference tables
# ======================================================================================
def make_rate_card(rng) -> pd.DataFrame:
    """Official price list. Card A = list price; B and C apply negotiated discounts."""
    rows = []
    # base list price (EUR/kg) by service, scaled up by zone distance
    base = {"Express": 3.2, "Standard": 1.6, "Freight": 0.9}
    discount = {"A": 1.00, "B": 0.90, "C": 0.82}
    for card in C.RATE_CARDS:
        for service in C.SERVICES:
            for zone in C.ZONES:
                rate = base[service] * (1 + 0.18 * (zone - 1)) * discount[card]
                rows.append(
                    dict(rate_card_id=card, service=service, zone=int(zone),
                         rate_per_kg=round(float(rate), 4))
                )
    return pd.DataFrame(rows)


def make_fuel_index(rng) -> pd.DataFrame:
    """Correct monthly fuel surcharge %, wandering around ~7%."""
    months = pd.date_range(C.PERIOD_START, periods=C.PERIOD_MONTHS, freq="MS")
    pct = 0.07 + np.cumsum(rng.normal(0, 0.004, size=len(months)))
    pct = np.clip(pct, 0.03, 0.12)
    return pd.DataFrame({
        "month": [m.strftime("%Y-%m") for m in months],
        "fuel_surcharge_pct": np.round(pct, 4),
    })


def make_customers(rng, fake: Faker) -> pd.DataFrame:
    countries = list(C.COUNTRIES.keys())
    seg = _weighted(rng, C.SEGMENTS, C.SEGMENT_WEIGHTS, C.N_CUSTOMERS)
    # bigger customers tend to negotiate better cards and longer terms
    card, terms = [], []
    for s in seg:
        if s == "Enterprise":
            card.append(rng.choice(["B", "C"], p=[0.4, 0.6]))
            terms.append(rng.choice([30, 60], p=[0.4, 0.6]))
        elif s == "Mid":
            card.append(rng.choice(["A", "B", "C"], p=[0.2, 0.5, 0.3]))
            terms.append(rng.choice([30, 60], p=[0.7, 0.3]))
        else:  # SMB
            card.append(rng.choice(["A", "B"], p=[0.75, 0.25]))
            terms.append(rng.choice([15, 30], p=[0.6, 0.4]))
    country = rng.choice(countries, size=C.N_CUSTOMERS)
    df = pd.DataFrame({
        "customer_id": [f"CUST{ i:05d}" for i in range(1, C.N_CUSTOMERS + 1)],
        "name": [fake.company() for _ in range(C.N_CUSTOMERS)],
        "segment": seg,
        "country": country,
        "currency": [C.COUNTRIES[c] for c in country],
        "payment_terms_days": terms,
        "rate_card_id": card,
    })
    return df


# ======================================================================================
# Invoices
# ======================================================================================
def make_invoices(rng, customers: pd.DataFrame, rate_card: pd.DataFrame,
                  fuel: pd.DataFrame):
    n = C.N_INVOICES
    rate_lookup = {(r.rate_card_id, r.service, r.zone): r.rate_per_kg
                   for r in rate_card.itertuples()}
    fuel_lookup = dict(zip(fuel["month"], fuel["fuel_surcharge_pct"]))

    # activity is skewed: Enterprise accounts generate far more invoices
    seg_activity = {"SMB": 1.0, "Mid": 3.0, "Enterprise": 9.0}
    w = customers["segment"].map(seg_activity).to_numpy()
    cust_idx = rng.choice(len(customers), size=n, p=w / w.sum())

    cust = customers.iloc[cust_idx].reset_index(drop=True)
    service = _weighted(rng, C.SERVICES, C.SERVICE_WEIGHTS, n)
    zone = rng.choice(C.ZONES, size=n)
    weight = np.round(rng.gamma(shape=2.0, scale=6.0, size=n) + 0.5, 2)  # kg

    # dates across the 12-month window
    start = pd.Timestamp(C.PERIOD_START)
    end = start + pd.DateOffset(months=C.PERIOD_MONTHS) - pd.Timedelta(days=1)
    span_days = (end - start).days
    inv_date = start + pd.to_timedelta(rng.integers(0, span_days + 1, size=n), unit="D")
    month = pd.Series(inv_date).dt.strftime("%Y-%m")

    # correct economics from the rate card
    rate = np.array([rate_lookup[(c, s, int(z))]
                     for c, s, z in zip(cust["rate_card_id"], service, zone)])
    base_amount = np.round(weight * rate, 2)
    fuel_pct = month.map(fuel_lookup).to_numpy()
    fuel_amount = np.round(base_amount * fuel_pct, 2)
    total_amount = np.round(base_amount + fuel_amount, 2)

    inv = pd.DataFrame({
        "invoice_id": [f"INV{i:07d}" for i in range(1, n + 1)],
        "customer_id": cust["customer_id"].to_numpy(),
        "invoice_date": pd.Series(inv_date),
        "service": service,
        "billed_weight_kg": weight,
        "zone": zone.astype(int),
        "base_amount": base_amount,
        "fuel_surcharge": fuel_amount,
        "total_amount": total_amount,
        "currency": cust["currency"].to_numpy(),
        # helper columns dropped before saving (used for payments + ground truth)
        "_rate_card_id": cust["rate_card_id"].to_numpy(),
        "_fuel_pct": fuel_pct,
        "_terms": cust["payment_terms_days"].to_numpy(),
    })

    labels: dict[str, str] = {}   # invoice_id / payment_id -> anomaly_type
    rng_state = rng

    # ---- INVOICE-SIDE ANOMALIES ------------------------------------------------------
    available = set(inv.index)

    def take(k):
        pick = rng_state.choice(sorted(available), size=k, replace=False)
        available.difference_update(pick.tolist())
        return pick

    # 1) rate discrepancy: bill using the WRONG zone rate (a gross, catchable error)
    k_rate = int(n * C.ANOMALY_RATES["rate_discrepancy"])
    idx = take(k_rate)
    for i in idx:
        wrong_zone = int(rng_state.choice([z for z in C.ZONES if z != inv.at[i, "zone"]]))
        card = inv.at[i, "_rate_card_id"]
        new_rate = rate_lookup[(card, inv.at[i, "service"], wrong_zone)]
        new_base = round(inv.at[i, "billed_weight_kg"] * new_rate, 2)
        new_fuel = round(new_base * inv.at[i, "_fuel_pct"], 2)
        inv.at[i, "base_amount"] = new_base
        inv.at[i, "fuel_surcharge"] = new_fuel
        inv.at[i, "total_amount"] = round(new_base + new_fuel, 2)
        labels[inv.at[i, "invoice_id"]] = "rate_discrepancy"

    # 2) fuel surcharge error: subtle -> surcharge % off by a small factor only,
    #    so the total stays close to correct and slips past a fixed 2% total check.
    k_fuel = int(n * C.ANOMALY_RATES["fuel_surcharge_error"])
    idx = take(k_fuel)
    for i in idx:
        # small multipliers keep the TOTAL within the rules' 2% band -> rules miss it,
        # while the surcharge/base ratio is clearly off -> Isolation Forest catches it.
        factor = rng_state.choice([0.75, 0.85, 1.15, 1.25])  # wrong surcharge multiplier
        new_fuel = round(inv.at[i, "fuel_surcharge"] * factor, 2)
        inv.at[i, "fuel_surcharge"] = new_fuel
        inv.at[i, "total_amount"] = round(inv.at[i, "base_amount"] + new_fuel, 2)
        labels[inv.at[i, "invoice_id"]] = "fuel_surcharge_error"

    # 3) duplicate invoice: clone a clean invoice (same customer/amount, date +-1 day)
    k_dup = int(n * C.ANOMALY_RATES["duplicate_invoice"])
    src = take(k_dup)
    dup_rows = []
    next_id = n + 1
    for i in src:
        row = inv.loc[i].copy()
        row["invoice_id"] = f"INV{next_id:07d}"
        next_id += 1
        row["invoice_date"] = row["invoice_date"] + pd.Timedelta(
            days=int(rng_state.choice([-1, 0, 1])))
        dup_rows.append(row)
        labels[row["invoice_id"]] = "duplicate_invoice"
    if dup_rows:
        inv = pd.concat([inv, pd.DataFrame(dup_rows)], ignore_index=True)

    return inv, labels


# ======================================================================================
# Payments
# ======================================================================================
def make_payments(rng, customers: pd.DataFrame, inv: pd.DataFrame, labels: dict):
    cust_terms = dict(zip(customers["customer_id"], customers["payment_terms_days"]))

    # which invoices get paid
    n_inv = len(inv)
    paid_mask = rng.random(n_inv) < C.PAYMENT_RATE
    paid = inv[paid_mask].reset_index(drop=True)

    terms = paid["customer_id"].map(cust_terms).to_numpy()
    delay = terms + rng.integers(-5, 20, size=len(paid))  # pay around terms, some late
    pay_date = pd.to_datetime(paid["invoice_date"]) + pd.to_timedelta(delay, unit="D")

    pay = pd.DataFrame({
        "payment_id": [f"PAY{i:07d}" for i in range(1, len(paid) + 1)],
        "invoice_id": paid["invoice_id"].to_numpy(),
        "customer_id": paid["customer_id"].to_numpy(),
        "payment_date": pay_date,
        "amount_paid": paid["total_amount"].to_numpy().astype(float),
        "currency": paid["currency"].to_numpy(),
        "method": _weighted(rng, C.PAYMENT_METHODS, C.METHOD_WEIGHTS, len(paid)),
    })

    idx_pool = set(pay.index)

    def take(k):
        pick = rng.choice(sorted(idx_pool), size=k, replace=False)
        idx_pool.difference_update(pick.tolist())
        return pick

    # 4) short / over payment
    k_sp = int(n_inv * C.ANOMALY_RATES["short_or_over_pay"])
    idx = take(min(k_sp, len(idx_pool)))
    for i in idx:
        # mix of gross short-pays (caught by rules) and subtle ones (ML territory)
        if rng.random() < 0.5:
            factor = rng.uniform(0.80, 0.95)      # gross short-pay
        else:
            factor = rng.choice([rng.uniform(0.965, 0.99),   # subtle short
                                 rng.uniform(1.01, 1.035)])  # subtle over
        pay.at[i, "amount_paid"] = round(pay.at[i, "amount_paid"] * factor, 2)
        labels[pay.at[i, "payment_id"]] = "short_or_over_pay"

    # 5) unapplied cash: valid payment, but the invoice reference is wrong/missing
    k_uc = int(n_inv * C.ANOMALY_RATES["unapplied_cash"])
    idx = take(min(k_uc, len(idx_pool)))
    for i in idx:
        if rng.random() < 0.5:
            pay.at[i, "invoice_id"] = ""                       # missing reference
        else:
            pay.at[i, "invoice_id"] = f"INV9{rng.integers(0, 999999):06d}"  # non-existent
        # lump-sum "on account" amount that maps to no single invoice -> genuinely
        # unmatchable, so neither exact nor fuzzy matching can re-link it
        pay.at[i, "amount_paid"] = round(float(rng.integers(2, 26)) * 100, 2)
        labels[pay.at[i, "payment_id"]] = "unapplied_cash"

    return pay


# ======================================================================================
# Dirt: currency-label swaps, customer_id noise, mixed date formats
# ======================================================================================
def add_dirt(rng, inv: pd.DataFrame, pay: pd.DataFrame):
    # wrong currency LABEL on some invoices (amount unchanged -> cleaning fixes label)
    m = rng.random(len(inv)) < C.WRONG_CURRENCY_LABEL_RATE
    inv.loc[m, "currency"] = inv.loc[m, "currency"].map({"EUR": "USD", "USD": "EUR"})

    # noisy customer_id references on payments (case / whitespace)
    m = rng.random(len(pay)) < C.DIRTY_CUSTOMER_ID_RATE
    def dirty(cid):
        r = rng.random()
        if r < 0.4:
            return cid.lower()
        if r < 0.7:
            return f" {cid} "
        return cid.replace("CUST", "cust ")
    pay.loc[m, "customer_id"] = pay.loc[m, "customer_id"].map(dirty)

    # mixed date formats in the raw CSVs
    inv["invoice_date"] = _format_date_mixed(rng, pd.to_datetime(inv["invoice_date"]))
    pay["payment_date"] = _format_date_mixed(rng, pd.to_datetime(pay["payment_date"]))
    return inv, pay


# ======================================================================================
# Main
# ======================================================================================
def main():
    C.DATA_DIR.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(C.SEED)
    Faker.seed(C.SEED)
    fake = Faker("it_IT")

    print("· reference tables …")
    rate_card = make_rate_card(rng)
    fuel = make_fuel_index(rng)
    customers = make_customers(rng, fake)

    print("· invoices …")
    inv, labels = make_invoices(rng, customers, rate_card, fuel)

    print("· payments …")
    pay = make_payments(rng, customers, inv, labels)

    print("· injecting data-quality dirt …")
    inv_out = inv.drop(columns=[c for c in inv.columns if c.startswith("_")]).copy()
    inv_out, pay = add_dirt(rng, inv_out, pay)

    # ground truth (hidden labels)
    gt = pd.DataFrame(
        [{"entity_id": k, "anomaly_type": v} for k, v in labels.items()]
    ).sort_values("entity_id")

    # persist
    customers.to_csv(C.CUSTOMERS_CSV, index=False)
    rate_card.to_csv(C.RATE_CARD_CSV, index=False)
    fuel.to_csv(C.FUEL_INDEX_CSV, index=False)
    inv_out.to_csv(C.INVOICES_CSV, index=False)
    pay.to_csv(C.PAYMENTS_CSV, index=False)
    gt.to_csv(C.GROUND_TRUTH_CSV, index=False)

    print("\nGenerated:")
    print(f"  customers   {len(customers):>7,}")
    print(f"  rate_card   {len(rate_card):>7,}")
    print(f"  invoices    {len(inv_out):>7,}")
    print(f"  payments    {len(pay):>7,}")
    print(f"  anomalies   {len(gt):>7,}  ({len(gt)/len(inv_out)*100:.2f}% of invoices)")
    print("\nAnomaly breakdown (ground truth):")
    print(gt["anomaly_type"].value_counts().to_string())


if __name__ == "__main__":
    main()
