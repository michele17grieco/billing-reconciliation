-- ======================================================================================
-- 03_rules.sql  —  deterministic business-rule checks (the "known error" catchers)
--
-- Produces rule_flags(entity_type, entity_id, invoice_ref, customer_id, exception_type,
--                     amount_at_risk, ref_date, detection_method='rule')
-- plus invoice_expected (rate-card revalidation) reused by the ML feature build.
--
-- Fixed thresholds (config.py): rate 2%, payment 1%. Deliberately "banal": precise on
-- gross errors, blind to subtle ones — that gap is what the ML layer is there to close.
-- ======================================================================================

-- --------------------------------------------------------------------------------------
-- Rate-card revalidation: recompute the expected amount from the official price list.
-- --------------------------------------------------------------------------------------
DROP TABLE IF EXISTS invoice_expected;
CREATE TABLE invoice_expected AS
SELECT
    i.invoice_id,
    i.customer_id,
    i.invoice_date,
    i.segment,
    i.service,
    i.zone,
    i.billed_weight_kg,
    i.base_amount,
    i.fuel_surcharge,
    i.total_amount,
    i.payment_terms_days,
    rc.rate_per_kg * i.billed_weight_kg                               AS expected_base,
    f.fuel_surcharge_pct                                             AS expected_fuel_pct,
    (rc.rate_per_kg * i.billed_weight_kg) * (1.0 + f.fuel_surcharge_pct) AS expected_total
FROM clean_invoices i
JOIN rate_card rc
  ON rc.rate_card_id = i.rate_card_id
 AND rc.service      = i.service
 AND rc.zone         = i.zone
JOIN fuel_index f
  ON f.month = i.invoice_month;

CREATE INDEX idx_invoice_expected ON invoice_expected(invoice_id);

-- --------------------------------------------------------------------------------------
-- Assemble all rule hits.
-- --------------------------------------------------------------------------------------
DROP TABLE IF EXISTS rule_flags;
CREATE TABLE rule_flags AS

-- (1) DUPLICATE INVOICES ---------------------------------------------------------------
-- Same customer, identical total, invoice dates within 1 day. Self-join keeps the later
-- invoice_id of each pair as the duplicate (the earlier one is treated as the original).
WITH duplicates AS (
    SELECT
        'invoice'                       AS entity_type,
        a.invoice_id                    AS entity_id,
        a.invoice_id                    AS invoice_ref,
        a.customer_id,
        'duplicate_invoice'             AS exception_type,
        a.total_amount                  AS amount_at_risk,
        a.invoice_date                  AS ref_date
    FROM clean_invoices a
    JOIN clean_invoices b
      ON b.customer_id = a.customer_id
     AND b.total_amount = a.total_amount
     AND b.invoice_id < a.invoice_id
     AND ABS(julianday(a.invoice_date) - julianday(b.invoice_date)) <= 1
),

-- (2) RATE DISCREPANCY -----------------------------------------------------------------
-- Billed total departs from the rate-card expectation by more than 2%.
rate_errors AS (
    SELECT
        'invoice'                       AS entity_type,
        invoice_id                      AS entity_id,
        invoice_id                      AS invoice_ref,
        customer_id,
        'rate_discrepancy'              AS exception_type,
        ROUND(ABS(total_amount - expected_total), 2) AS amount_at_risk,
        invoice_date                    AS ref_date
    FROM invoice_expected
    WHERE ABS(total_amount - expected_total) > expected_total * 0.02
),

-- (3) PAYMENT DISCREPANCY (short / over pay) -------------------------------------------
-- A matched payment settling more than 1% away from the invoice total.
payment_discrepancies AS (
    SELECT
        'payment'                       AS entity_type,
        m.payment_id                    AS entity_id,
        m.matched_invoice_id            AS invoice_ref,
        m.customer_id,
        'short_or_over_pay'             AS exception_type,
        ROUND(ABS(m.amount_paid - m.total_amount), 2) AS amount_at_risk,
        m.payment_date                  AS ref_date
    FROM payment_matches m
    WHERE ABS(m.amount_paid - m.total_amount) > m.total_amount * 0.01
),

-- (4) UNAPPLIED CASH -------------------------------------------------------------------
-- Payment that could not be matched to any invoice (missing / wrong reference).
unapplied AS (
    SELECT
        'payment'                       AS entity_type,
        u.payment_id                    AS entity_id,
        NULL                            AS invoice_ref,
        u.customer_id,
        'unapplied_cash'                AS exception_type,
        ROUND(u.amount_paid, 2)         AS amount_at_risk,
        u.payment_date                  AS ref_date
    FROM unmatched_payments u
)

SELECT *, 'rule' AS detection_method FROM duplicates
UNION ALL
SELECT *, 'rule' AS detection_method FROM rate_errors
UNION ALL
SELECT *, 'rule' AS detection_method FROM payment_discrepancies
UNION ALL
SELECT *, 'rule' AS detection_method FROM unapplied;
