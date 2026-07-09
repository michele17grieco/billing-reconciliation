-- ======================================================================================
-- 04_exceptions.sql  —  the rule-based exceptions register
--
-- Turns raw rule hits into an ops-ready table: aging, aging buckets, severity and a
-- priority rank by € at risk. The ML layer appends to this in 05_final.sql, after the
-- Isolation Forest has scored the invoices.
--
-- "As of" date = the latest transaction in the dataset (stand-in for the run date).
-- ======================================================================================

DROP TABLE IF EXISTS exceptions;
CREATE TABLE exceptions AS
WITH asof AS (
    SELECT MAX(d) AS run_date FROM (
        SELECT MAX(invoice_date) AS d FROM clean_invoices
        UNION ALL
        SELECT MAX(payment_date) FROM clean_payments
    )
),
enriched AS (
    SELECT
        r.entity_type,
        r.entity_id,
        r.invoice_ref,
        r.customer_id,
        r.exception_type,
        r.amount_at_risk,
        r.ref_date,
        r.detection_method,
        CAST(julianday((SELECT run_date FROM asof)) - julianday(r.ref_date) AS INT)
                                                    AS aging_days
    FROM rule_flags r
)
SELECT
    e.*,
    CASE
        WHEN e.aging_days <= 30  THEN '0-30'
        WHEN e.aging_days <= 60  THEN '31-60'
        WHEN e.aging_days <= 90  THEN '61-90'
        ELSE '90+'
    END                                             AS aging_bucket,
    CASE
        WHEN e.amount_at_risk >= 500 THEN 'High'
        WHEN e.amount_at_risk >= 100 THEN 'Medium'
        ELSE 'Low'
    END                                             AS severity,
    ROW_NUMBER() OVER (ORDER BY e.amount_at_risk DESC) AS priority_rank
FROM enriched e;

CREATE INDEX idx_exceptions_type ON exceptions(exception_type);
