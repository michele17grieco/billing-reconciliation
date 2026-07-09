-- ======================================================================================
-- 05_final.sql  —  merge the rule register and the ML flags into one exceptions view
--
-- Runs AFTER ml/anomaly_detection.py has written ml_flags. Produces exceptions_final,
-- the single table the dashboard reads. Detection method collapses to rule / ml / both
-- so the "Reconciliation Control Tower" can show where each exception came from.
-- ======================================================================================

DROP TABLE IF EXISTS exceptions_final;
CREATE TABLE exceptions_final AS
WITH asof AS (
    SELECT MAX(d) AS run_date FROM (
        SELECT MAX(invoice_date) AS d FROM clean_invoices
        UNION ALL
        SELECT MAX(payment_date) FROM clean_payments
    )
),
combined AS (
    SELECT entity_type, entity_id, invoice_ref, customer_id,
           exception_type, amount_at_risk, ref_date, detection_method
    FROM rule_flags
    UNION ALL
    SELECT entity_type, entity_id, invoice_ref, customer_id,
           exception_type, amount_at_risk, ref_date, detection_method
    FROM ml_flags
),
-- one row per (entity, exception_type); if both a rule and ML fired, mark it 'both'
dedup AS (
    SELECT
        entity_type, entity_id, invoice_ref, customer_id, exception_type,
        MAX(amount_at_risk) AS amount_at_risk,
        MIN(ref_date)       AS ref_date,
        CASE
            WHEN COUNT(DISTINCT detection_method) > 1 THEN 'both'
            ELSE MAX(detection_method)
        END                 AS detection_method
    FROM combined
    GROUP BY entity_type, entity_id, invoice_ref, customer_id, exception_type
)
SELECT
    d.*,
    CAST(julianday((SELECT run_date FROM asof)) - julianday(d.ref_date) AS INT) AS aging_days,
    CASE
        WHEN d.amount_at_risk >= 500 THEN 'High'
        WHEN d.amount_at_risk >= 100 THEN 'Medium'
        ELSE 'Low'
    END AS severity,
    ROW_NUMBER() OVER (ORDER BY d.amount_at_risk DESC) AS priority_rank
FROM dedup d;

CREATE INDEX idx_excfinal_type   ON exceptions_final(exception_type);
CREATE INDEX idx_excfinal_method ON exceptions_final(detection_method);
