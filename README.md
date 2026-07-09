# Automated Billing Reconciliation & Anomaly Detection

Rule-based + machine-learning pipeline that reconciles high-volume logistics invoices to
payments, flags billing anomalies, and ranks the resulting exceptions by financial impact.

> **Synthetic data — no proprietary information used.** The scenario is modelled on
> generic high-volume billing/logistics operations, not on any real company's processes
> or numbers. The dataset is fully generated in code and reproducible from a fixed seed.

---

## The problem

In high-volume logistics billing, invoice-to-payment reconciliation is often a manual,
spreadsheet-driven process. Billing errors — duplicate invoices, incorrect rates,
misapplied fuel surcharges, short payments, unapplied cash — routinely slip through,
eroding margin and inflating days-sales-outstanding.

This project builds an end-to-end reconciliation engine on a realistic synthetic dataset
of **50,250 invoices** and **48,779 payments** across **500 customers** over 12 months,
with **~3% of transactions deliberately seeded with six real-world anomaly types** that
serve as hidden ground truth for evaluation.

## The result (headline)

| System | Precision | Recall | F1 |
|---|---:|---:|---:|
| Rules only | **99.4%** | 83.3% | 0.91 |
| ML only | 18.8% | 19.0% | 0.19 |
| **Rules + ML** | 53.2% | **93.8%** | 0.68 |

The rule engine is near-perfect on *known* error shapes but is structurally blind to one
whole class — **fuel-surcharge errors: 0% recall with rules, 62.8% with ML**. Adding the
model lifts overall recall from 83% to **94%**, at a precision cost that a reviewer
absorbs by working the exception queue in € order.

**Key insight:** machine learning *complements* business rules in financial controls — it
does not replace them. Rules deliver explainability and precision on known patterns; the
anomaly model extends coverage to the errors that fixed thresholds cannot see.

The pipeline reconciles ~99,000 transactions and produces **2,767 ranked exceptions
totalling €214k at risk in ~16 seconds**, fully reproducible with one command.

---

## Architecture

```
CSV (synthetic)  ─►  SQLite raw_*  ─►  SQL cleaning ─► matching ─► rules ─► exceptions
                                                                        │
                                          Isolation Forest (Python) ────┤
                                                                        ▼
                                            exceptions_final ─► evaluation vs ground truth
                                                             └► Power BI "Control Tower"
```

Most of the reconciliation logic lives in **SQL** (cleaning, exact/fuzzy matching,
duplicate detection via window functions, rate-card revalidation, the exceptions
register) — the skill set finance/ops reconciliation roles actually look for. Python owns
data generation, date parsing, the ML layer, and evaluation.

### Data model

```mermaid
erDiagram
    customers  ||--o{ invoices : "billed to"
    invoices   ||--o{ payments : "settled by"
    rate_card  ||--o{ invoices : "prices"
    fuel_index ||--o{ invoices : "surcharge %"

    customers { string customer_id PK; string segment; int payment_terms_days; string rate_card_id }
    rate_card { string rate_card_id; string service; int zone; float rate_per_kg }
    fuel_index { string month; float fuel_surcharge_pct }
    invoices  { string invoice_id PK; string customer_id FK; float total_amount }
    payments  { string payment_id PK; string invoice_id FK; float amount_paid }
```

---

## Anomalies (the seeded ground truth)

| Type | Rate | Detected by | Recall (rules / ML / combined) |
|---|---:|---|---|
| Rate discrepancy (wrong zone/rate) | 1.0% | rules (rate-card revalidation) | 100% / 19% / **100%** |
| Duplicate invoice (±1 day) | 0.5% | rules (window function) | 100% / 2% / **100%** |
| Short / over payment | 0.7% | rules + ML | 100% / 8% / **100%** |
| Unapplied cash (bad/no reference) | 0.3% | rules (unmatched) | 100% / — / **100%** |
| **Fuel-surcharge error (subtle)** | 0.5% | **ML only** | **0% / 63% / 63%** |
| Overdue / never paid | natural | rules (aging) | — |

Fuel-surcharge errors are deliberately tuned to stay *under* the rules' 2% total-amount
threshold while leaving the surcharge/base ratio visibly wrong — the exact gap the
Isolation Forest is there to close.

To keep the data realistic and the cleaning genuine, the raw feeds also carry mixed date
formats, `customer_id` case/whitespace noise on payments, and swapped currency labels.

---

## Repository layout

```
config.py                 single source of truth (seed, sizes, rates, thresholds)
data/generate_data.py     synthetic data + hidden ground truth
db.py                     CSV → SQLite loader + SQL runner
sql/
  01_cleaning.sql         key normalisation, currency fix
  02_matching.sql         exact + fuzzy invoice↔payment matching (window functions)
  03_rules.sql            duplicates, rate revalidation, payment & cash checks
  04_exceptions.sql       rule exceptions register (aging, severity, priority)
  05_final.sql            merge rule + ML exceptions
ml/anomaly_detection.py   Isolation Forest scoring
eval/evaluate.py          precision / recall / F1 vs ground truth
run_pipeline.py           one-command end-to-end run
dashboard/                Power BI "Reconciliation Control Tower" spec + preview
output/                   generated DB, exceptions.csv, metrics.json (git-ignored)
```

## Run it

```bash
pip install -r requirements.txt
python run_pipeline.py          # generate → SQL → ML → evaluate → export  (~16s)
```

Outputs land in `output/`: `reconciliation.db`, `exceptions.csv` (Power BI source),
`metrics.json`, and the evaluation tables printed above. Re-run with `--skip-generate` to
reuse the existing CSVs. Everything is deterministic via `SEED` in `config.py`.

---

## The case study (write-up)

**Automated Billing Reconciliation & Anomaly Detection**

*Context.* In high-volume logistics billing, invoice-to-payment reconciliation is often a
manual, spreadsheet-driven process, and billing errors routinely slip through and erode
margin. Drawing on experience in billing operations and financial reconciliations, I built
an end-to-end reconciliation engine on a realistic synthetic dataset of 50,250 invoices and
48,779 payments, with 3% of transactions seeded with six real-world anomaly types as ground
truth.

*Process.* I generated the dataset in Python (pandas, Faker), modelling customer segments,
rate cards, payment terms, and messy real-world data-quality issues such as inconsistent
date formats and dirty payment references. The core reconciliation logic lives in SQL:
exact and fuzzy invoice-to-payment matching, duplicate detection via window functions, and
rate-card revalidation to flag pricing discrepancies. On top of the rule layer, I trained
an Isolation Forest on per-invoice features (deviation from rate card, surcharge ratio,
customer payment behaviour) to catch subtle anomalies that fixed-threshold rules miss.
Results feed a two-page Power BI "Reconciliation Control Tower" that ranks exceptions by
financial impact and aging.

*Results.* Because the anomalies were seeded, I could rigorously evaluate detection. The
rule layer reached 99.4% precision and 83.3% recall but missed the entire subtle
fuel-surcharge class; the combined rules + ML pipeline reached 93.8% recall (from 83.3%),
recovering 63% of those surcharge errors, while precision falls to 53% — a queue a reviewer
works in € order. The pipeline reconciles ~99,000 transactions in ~16 seconds and surfaces
€214k of revenue at risk across 2,767 exceptions ranked by impact.

*Takeaway.* The project's key insight is that machine learning complements — rather than
replaces — business rules in financial controls: rules deliver explainability and precision
on known error patterns, while anomaly detection extends coverage to the unknown ones. All
code, SQL, and the data generator are in this repo, fully reproducible with a single command.
