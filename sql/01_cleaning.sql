-- ======================================================================================
-- 01_cleaning.sql  —  standardise the raw feeds into analysis-ready tables
--
-- Handles the "dirty" data injected by the generator:
--   * customer_id noise on payments (lower-case, stray spaces, "cust 00001")
--   * swapped currency labels on invoices (corrected from the customer master)
-- Dates already arrive as ISO from the loader (mixed formats parsed in Python).
-- ======================================================================================

DROP TABLE IF EXISTS clean_customers;
CREATE TABLE clean_customers AS
SELECT
    UPPER(REPLACE(TRIM(customer_id), ' ', ''))          AS customer_id,
    name,
    segment,
    country,
    currency                                            AS home_currency,
    payment_terms_days,
    rate_card_id
FROM raw_customers;

CREATE INDEX idx_clean_customers ON clean_customers(customer_id);

-- --------------------------------------------------------------------------------------
-- Invoices: normalise the key, and trust the customer master for the currency label.
-- Amounts are already in the EUR operating currency; the currency field is a data-quality
-- attribute only (some invoices carried a swapped label).
-- --------------------------------------------------------------------------------------
DROP TABLE IF EXISTS clean_invoices;
CREATE TABLE clean_invoices AS
SELECT
    i.invoice_id,
    c.customer_id,
    i.invoice_date,
    substr(i.invoice_date, 1, 7)                        AS invoice_month,
    i.service,
    i.billed_weight_kg,
    i.zone,
    i.base_amount,
    i.fuel_surcharge,
    i.total_amount,
    c.home_currency                                     AS currency,
    c.rate_card_id,
    c.segment,
    c.payment_terms_days
FROM raw_invoices i
JOIN clean_customers c
  ON c.customer_id = UPPER(REPLACE(TRIM(i.customer_id), ' ', ''));

CREATE INDEX idx_clean_invoices_id   ON clean_invoices(invoice_id);
CREATE INDEX idx_clean_invoices_cust ON clean_invoices(customer_id);

-- --------------------------------------------------------------------------------------
-- Payments: normalise the customer key and blank references -> NULL.
-- --------------------------------------------------------------------------------------
DROP TABLE IF EXISTS clean_payments;
CREATE TABLE clean_payments AS
SELECT
    p.payment_id,
    NULLIF(TRIM(p.invoice_id), '')                      AS invoice_id,
    UPPER(REPLACE(TRIM(p.customer_id), ' ', ''))        AS customer_id,
    p.payment_date,
    p.amount_paid,
    p.method
FROM raw_payments p;

CREATE INDEX idx_clean_payments_inv  ON clean_payments(invoice_id);
CREATE INDEX idx_clean_payments_cust ON clean_payments(customer_id);
