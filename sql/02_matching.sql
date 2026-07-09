-- ======================================================================================
-- 02_matching.sql  —  reconcile payments to invoices
--
--   exact : payment references a real invoice AND pays within +-1% of its total
--   fuzzy : reference missing/wrong -> best candidate by customer + amount (+-1%),
--           tie-broken by nearest date, using ROW_NUMBER() to keep one match/payment
--   none  : no candidate -> unapplied-cash candidate (handled by the rule layer)
--
-- Tolerances mirror config.py: PAYMENT_TOLERANCE = 1%, FUZZY_AMOUNT_TOLERANCE = 1%.
-- ======================================================================================

DROP TABLE IF EXISTS payment_matches;
CREATE TABLE payment_matches AS
WITH exact AS (
    -- match on the invoice REFERENCE; the amount check is a separate rule (short/over pay)
    SELECT
        p.payment_id,
        i.invoice_id                    AS matched_invoice_id,
        'exact'                         AS match_type,
        p.customer_id,
        p.payment_date,
        p.amount_paid,
        i.total_amount,
        i.invoice_date
    FROM clean_payments p
    JOIN clean_invoices i
      ON i.invoice_id = p.invoice_id
),
remaining AS (
    SELECT * FROM clean_payments
    WHERE payment_id NOT IN (SELECT payment_id FROM exact)
),
fuzzy_ranked AS (
    SELECT
        r.payment_id,
        i.invoice_id                    AS matched_invoice_id,
        r.customer_id,
        r.payment_date,
        r.amount_paid,
        i.total_amount,
        i.invoice_date,
        ROW_NUMBER() OVER (
            PARTITION BY r.payment_id
            ORDER BY ABS(r.amount_paid - i.total_amount) ASC,
                     ABS(julianday(r.payment_date) - julianday(i.invoice_date)) ASC
        ) AS rn
    FROM remaining r
    JOIN clean_invoices i
      ON i.customer_id = r.customer_id
     AND ABS(r.amount_paid - i.total_amount) <= i.total_amount * 0.01
     AND i.invoice_id NOT IN (SELECT matched_invoice_id FROM exact)
),
fuzzy AS (
    SELECT
        payment_id, matched_invoice_id, 'fuzzy' AS match_type,
        customer_id, payment_date, amount_paid, total_amount, invoice_date
    FROM fuzzy_ranked
    WHERE rn = 1
)
SELECT payment_id, matched_invoice_id, match_type,
       customer_id, payment_date, amount_paid, total_amount, invoice_date
FROM exact
UNION ALL
SELECT payment_id, matched_invoice_id, match_type,
       customer_id, payment_date, amount_paid, total_amount, invoice_date
FROM fuzzy;

CREATE INDEX idx_pm_payment ON payment_matches(payment_id);
CREATE INDEX idx_pm_invoice ON payment_matches(matched_invoice_id);

-- Payments that matched nothing at all (unapplied-cash candidates).
DROP TABLE IF EXISTS unmatched_payments;
CREATE TABLE unmatched_payments AS
SELECT p.*
FROM clean_payments p
WHERE p.payment_id NOT IN (SELECT payment_id FROM payment_matches);
