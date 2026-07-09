"""
Central configuration for the Billing Reconciliation & Anomaly Detection pipeline.

Everything that controls the size and "shape" of the synthetic dataset lives here so
the whole project is reproducible from a single seed. Change SEED to get a different
(but equally valid) world; change the ANOMALY_RATES to make the problem easier/harder.

Synthetic data only — no proprietary information used. The scenario is modelled on
generic high-volume logistics billing, not on any real company's processes or numbers.
"""

from pathlib import Path

# --------------------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------------------
SEED = 42

# --------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
SQL_DIR = ROOT / "sql"
DB_PATH = OUTPUT_DIR / "reconciliation.db"

# CSV artefacts produced by the generator
CUSTOMERS_CSV = DATA_DIR / "customers.csv"
RATE_CARD_CSV = DATA_DIR / "rate_card.csv"
FUEL_INDEX_CSV = DATA_DIR / "fuel_index.csv"
INVOICES_CSV = DATA_DIR / "invoices.csv"
PAYMENTS_CSV = DATA_DIR / "payments.csv"
GROUND_TRUTH_CSV = DATA_DIR / "ground_truth.csv"  # hidden labels, used only in eval

# --------------------------------------------------------------------------------------
# Dataset size
# --------------------------------------------------------------------------------------
N_CUSTOMERS = 500
N_INVOICES = 50_000          # ~12 months of billing
PAYMENT_RATE = 0.97          # share of invoices that receive a payment (rest = unpaid)
PERIOD_START = "2024-01-01"
PERIOD_MONTHS = 12

# --------------------------------------------------------------------------------------
# Business reference data
# --------------------------------------------------------------------------------------
SEGMENTS = ["SMB", "Mid", "Enterprise"]
SEGMENT_WEIGHTS = [0.55, 0.32, 0.13]

SERVICES = ["Express", "Standard", "Freight"]
SERVICE_WEIGHTS = [0.30, 0.50, 0.20]

ZONES = [1, 2, 3, 4, 5]
RATE_CARDS = ["A", "B", "C"]          # A = list price, B/C = negotiated discounts
PAYMENT_TERMS = [15, 30, 60]

# Countries -> home currency (drives the "wrong currency label" data-quality issue)
COUNTRIES = {
    "IT": "EUR", "DE": "EUR", "FR": "EUR", "ES": "EUR", "NL": "EUR",
    "US": "USD", "GB": "USD",  # GB modelled in USD here purely to create FX mix
}
FX_TO_EUR = {"EUR": 1.0, "USD": 0.92}

PAYMENT_METHODS = ["Bank transfer", "Direct debit", "Card", "Cheque"]
METHOD_WEIGHTS = [0.55, 0.25, 0.12, 0.08]

# --------------------------------------------------------------------------------------
# Anomaly injection rates (share of invoices/payments), the project's ground truth.
# Total ≈ 3% of transactions. Kept rare so the detection problem stays realistic.
# --------------------------------------------------------------------------------------
ANOMALY_RATES = {
    "duplicate_invoice":   0.005,   # same customer / amount / date +-1 day
    "rate_discrepancy":    0.010,   # billed amount != rate card (wrong rate or zone)
    "fuel_surcharge_error":0.005,   # wrong fuel surcharge % (subtle: small € impact)
    "short_or_over_pay":   0.007,   # partial / excess payment
    "unapplied_cash":      0.003,   # payment with missing / wrong invoice reference
}

# --------------------------------------------------------------------------------------
# Data-quality "dirt" (not scored as anomalies, but forces genuine cleaning)
# --------------------------------------------------------------------------------------
DIRTY_CUSTOMER_ID_RATE = 0.04   # payments referencing customer_id with case/space noise
WRONG_CURRENCY_LABEL_RATE = 0.02  # invoices whose currency label is swapped
MIXED_DATE_FORMAT = True          # emit dates in several formats in the raw CSVs

# --------------------------------------------------------------------------------------
# Rule-engine thresholds (deliberately "banal" fixed thresholds).
# The narrative: these are precise on gross errors but miss subtle ones -> ML complements.
# --------------------------------------------------------------------------------------
RATE_TOLERANCE = 0.02      # flag invoice if |billed - expected| / expected > 2%
PAYMENT_TOLERANCE = 0.01   # a payment within +-1% of invoice total counts as matched
FUZZY_AMOUNT_TOLERANCE = 0.01  # fuzzy invoice<->payment match window on amount

# --------------------------------------------------------------------------------------
# Isolation Forest
# --------------------------------------------------------------------------------------
IFOREST_CONTAMINATION = 0.03   # expected anomaly share -> matches injected rate
IFOREST_N_ESTIMATORS = 300
