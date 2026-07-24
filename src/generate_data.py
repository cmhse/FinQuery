"""
Generates a synthetic 3-year GL dataset into a local SQLite file.

Run: python src/generate_data.py

Design notes (read this before touching the model):
- `version` values are 'budget_original', 'forecast_mar', 'forecast_jun', 'actual'.
  The spec's schema table lists version as one of budget_original/forecast/actual,
  but also requires >=2 reforecast snapshots per year so "how did the forecast
  change" questions are answerable. Two distinct forecast labels are the simplest
  way to satisfy both — flag this to reviewers as a deliberate elaboration of the
  spec, not an oversight.
- Revenue and COGS are only booked to Sales cost centers (CC-300, CC-310), which
  is how GL data usually looks in practice and keeps gross-margin math meaningful.
- Each cost center carries a fixed per-account-type bias (e.g. "Engineering - West
  always runs ~5% over Opex budget") so variance analysis has real, explainable
  signal instead of pure noise.
- forecast_mar and forecast_jun are blends of budget_original and actual, with the
  June snapshot weighted closer to actual — models forecasts improving in
  accuracy as the year progresses.
"""

import random
import sqlite3
from pathlib import Path

SEED = 42
YEARS = [2023, 2024, 2025]
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "finquery.db"

DEPARTMENTS = ["Engineering", "Marketing", "Sales"]
REGIONS = ["West", "East", "EMEA"]

COST_CENTERS = [
    {"cost_center_id": "CC-100", "cost_center_name": "Engineering - West", "department": "Engineering", "region": "West"},
    {"cost_center_id": "CC-110", "cost_center_name": "Engineering - East", "department": "Engineering", "region": "East"},
    {"cost_center_id": "CC-200", "cost_center_name": "Marketing - West", "department": "Marketing", "region": "West"},
    {"cost_center_id": "CC-210", "cost_center_name": "Marketing - EMEA", "department": "Marketing", "region": "EMEA"},
    {"cost_center_id": "CC-300", "cost_center_name": "Sales - East", "department": "Sales", "region": "East"},
    {"cost_center_id": "CC-310", "cost_center_name": "Sales - EMEA", "department": "Sales", "region": "EMEA"},
]

# eligible_departments=None means "all departments"
ACCOUNTS = [
    # Revenue
    {"account_code": "4010", "account_name": "Product Revenue", "account_type": "Revenue", "account_category": "Product", "eligible_departments": ["Sales"], "base_amount": 180_000, "growth": 0.07},
    {"account_code": "4020", "account_name": "Services Revenue", "account_type": "Revenue", "account_category": "Services", "eligible_departments": ["Sales"], "base_amount": 90_000, "growth": 0.05},
    {"account_code": "4030", "account_name": "Subscription Revenue", "account_type": "Revenue", "account_category": "Subscription", "eligible_departments": ["Sales"], "base_amount": 60_000, "growth": 0.12},
    # COGS
    {"account_code": "5000", "account_name": "COGS - Product", "account_type": "COGS", "account_category": "Product COGS", "eligible_departments": ["Sales"], "base_amount": 68_000, "growth": 0.07},
    {"account_code": "5010", "account_name": "COGS - Hosting & Delivery", "account_type": "COGS", "account_category": "Hosting COGS", "eligible_departments": ["Sales"], "base_amount": 18_000, "growth": 0.10},
    # Opex
    {"account_code": "6010", "account_name": "Travel & Entertainment", "account_type": "Opex", "account_category": "Travel", "eligible_departments": None, "base_amount": 8_000, "growth": 0.03},
    {"account_code": "6020", "account_name": "Software & Tools", "account_type": "Opex", "account_category": "Software", "eligible_departments": None, "base_amount": 12_000, "growth": 0.06},
    {"account_code": "6030", "account_name": "Facilities", "account_type": "Opex", "account_category": "Facilities", "eligible_departments": None, "base_amount": 15_000, "growth": 0.03},
    {"account_code": "6040", "account_name": "Professional Services", "account_type": "Opex", "account_category": "Professional Services", "eligible_departments": None, "base_amount": 10_000, "growth": 0.02},
    {"account_code": "6050", "account_name": "Office Supplies", "account_type": "Opex", "account_category": "Office Supplies", "eligible_departments": None, "base_amount": 3_000, "growth": 0.02},
    {"account_code": "6060", "account_name": "Training & Development", "account_type": "Opex", "account_category": "Training", "eligible_departments": None, "base_amount": 4_000, "growth": 0.03},
    {"account_code": "6070", "account_name": "Marketing Programs", "account_type": "Opex", "account_category": "Marketing Programs", "eligible_departments": ["Marketing"], "base_amount": 25_000, "growth": 0.05},
    # Headcount
    {"account_code": "7010", "account_name": "Salaries - Engineering", "account_type": "Headcount", "account_category": "Salaries", "eligible_departments": ["Engineering"], "base_amount": 140_000, "growth": 0.04},
    {"account_code": "7020", "account_name": "Salaries - Sales", "account_type": "Headcount", "account_category": "Salaries", "eligible_departments": ["Sales"], "base_amount": 110_000, "growth": 0.04},
    {"account_code": "7030", "account_name": "Salaries - Marketing", "account_type": "Headcount", "account_category": "Salaries", "eligible_departments": ["Marketing"], "base_amount": 95_000, "growth": 0.04},
    {"account_code": "7040", "account_name": "Benefits & Payroll Tax", "account_type": "Headcount", "account_category": "Benefits", "eligible_departments": None, "base_amount": 22_000, "growth": 0.04},
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS dim_account (
    account_code TEXT PRIMARY KEY,
    account_name TEXT NOT NULL,
    account_type TEXT NOT NULL,
    account_category TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_cost_center (
    cost_center_id TEXT PRIMARY KEY,
    cost_center_name TEXT NOT NULL,
    department TEXT NOT NULL,
    region TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gl_data (
    date TEXT NOT NULL,
    cost_center_id TEXT NOT NULL REFERENCES dim_cost_center(cost_center_id),
    account_code TEXT NOT NULL REFERENCES dim_account(account_code),
    version TEXT NOT NULL,
    amount REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gl_data_lookup ON gl_data (version, date, cost_center_id, account_code);
"""


def seasonal_multiplier(account_type, account_category, month):
    if account_type == "Revenue":
        return 1.20 if month in (10, 11, 12) else 1.0
    if account_category == "Travel":
        return 0.65 if month in (6, 7, 8) else 1.0
    return 1.0


def planning_seasonal_multiplier(account_type, account_category, month):
    """Flatter curve budget planners use — they under/overestimate the real swing."""
    if account_type == "Revenue":
        return 1.10 if month in (10, 11, 12) else 1.0
    if account_category == "Travel":
        return 0.85 if month in (6, 7, 8) else 1.0
    return 1.0


def build_cost_center_biases(rng):
    """Each cost center gets a fixed, plausible over/under-spend bias per account_type."""
    bias_choices = [-0.08, -0.04, 0.0, 0.05, 0.10]
    biases = {}
    for cc in COST_CENTERS:
        biases[cc["cost_center_id"]] = {
            account_type: rng.choice(bias_choices)
            for account_type in ("Revenue", "COGS", "Opex", "Headcount")
        }
    return biases


def account_applies_to(account, cost_center):
    eligible = account["eligible_departments"]
    return eligible is None or cost_center["department"] in eligible


def generate_gl_rows(rng):
    rows = []
    biases = build_cost_center_biases(rng)

    for year_idx, year in enumerate(YEARS):
        for month in range(1, 13):
            date_str = f"{year}-{month:02d}-01"
            for cc in COST_CENTERS:
                cc_id = cc["cost_center_id"]
                for account in ACCOUNTS:
                    if not account_applies_to(account, cc):
                        continue

                    code = account["account_code"]
                    atype = account["account_type"]
                    category = account["account_category"]
                    growth = (1 + account["growth"]) ** year_idx
                    bias = biases[cc_id][atype]

                    true_base = account["base_amount"] * growth * seasonal_multiplier(atype, category, month)
                    plan_base = account["base_amount"] * growth * planning_seasonal_multiplier(atype, category, month)

                    budget_original = round(plan_base * (1 + rng.gauss(0, 0.02)), 2)
                    actual = round(true_base * (1 + bias) * (1 + rng.gauss(0, 0.02)), 2)
                    forecast_mar = round(budget_original * 0.6 + actual * 0.4 + budget_original * rng.gauss(0, 0.015), 2)
                    forecast_jun = round(budget_original * 0.3 + actual * 0.7 + budget_original * rng.gauss(0, 0.01), 2)

                    for version, amount in (
                        ("budget_original", budget_original),
                        ("forecast_mar", forecast_mar),
                        ("forecast_jun", forecast_jun),
                        ("actual", actual),
                    ):
                        rows.append((date_str, cc_id, code, version, amount))
    return rows


def main():
    rng = random.Random(SEED)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)

        conn.executemany(
            "INSERT INTO dim_account VALUES (?, ?, ?, ?)",
            [(a["account_code"], a["account_name"], a["account_type"], a["account_category"]) for a in ACCOUNTS],
        )
        conn.executemany(
            "INSERT INTO dim_cost_center VALUES (?, ?, ?, ?)",
            [(c["cost_center_id"], c["cost_center_name"], c["department"], c["region"]) for c in COST_CENTERS],
        )

        rows = generate_gl_rows(rng)
        conn.executemany(
            "INSERT INTO gl_data (date, cost_center_id, account_code, version, amount) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        print(f"Wrote {len(rows)} gl_data rows, {len(ACCOUNTS)} accounts, {len(COST_CENTERS)} cost centers to {DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
