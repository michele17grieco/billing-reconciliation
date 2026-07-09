# Power BI — "Reconciliation Control Tower"

Two-page report connected to the pipeline output in `../output/`:

| File | Purpose |
|---|---|
| `exceptions.csv` | one row per exception (type, €, aging, severity, detection method) |
| `match_rate_trend.csv` | monthly payments / matched / match-rate |
| `top_customers_at_risk.csv` | top 10 customers by € at risk |
| `metrics.json` | model precision/recall for the performance panel |

Palette: neutral blue/grey, **red reserved for alerts only**. Titles speak business, not
tech ("€214k at risk across 2,767 flagged transactions").

## Page 1 — Executive
- **KPI cards:** Match rate `99.7%` · € at risk `€214k` · Open exceptions `2,767` · Avg aging (days)
- **Bar — € at risk by anomaly type** (sorted desc; unapplied cash leads)
- **Line — monthly match-rate trend**
- **Bar — top 10 customers by € at risk**

## Page 2 — Operational
- **Exceptions table** with slicers: type, segment, aging bucket, severity — drill to row level, sorted by `priority_rank`
- **Matrix — anomaly type × detection method** (rule / ML) showing where each exception came from
- **Model-performance panel** — precision / recall per system from `metrics.json`; makes the
  rules-vs-ML-vs-combined trade-off explicit (rare in portfolios, communicates rigour)

## Suggested DAX
```DAX
Match Rate   = DIVIDE(SUM(trend[matched]), SUM(trend[payments]))
€ at Risk    = SUM(exceptions[amount_at_risk])
Open Excepts = DISTINCTCOUNT(exceptions[entity_id])
```

> `.pbix` is binary and lives outside version control; rebuild by pointing Power BI Desktop
> at the four files above. The case-study page (`../site/case-study.html`) embeds a live
> HTML rendering of the same Control Tower for a quick preview.
