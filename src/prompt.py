"""
System prompt assembly for the SQL-generation agent (Part 3a + 3b of the spec).

Three pieces get concatenated into one system prompt:
  1. SCHEMA_DESCRIPTION  - tables/columns/relationships + ambiguous-convention notes
  2. metrics.METRICS_PROMPT_BLOCK - the finance formulas, injected verbatim (Part 2)
  3. FEW_SHOT_BLOCK      - hardcoded (question, SQL) pairs

Keeping this in one place means the schema text, the metrics definitions, and the
few-shot examples all live next to the system prompt they're injected into, rather
than being reconstructed ad hoc wherever a Claude call is made.
"""

from src.metrics import METRICS_PROMPT_BLOCK

SCHEMA_DESCRIPTION = """\
Database schema (SQLite) - three tables:

dim_account (account_code TEXT PRIMARY KEY, account_name TEXT, account_type TEXT, account_category TEXT)
  - account_type is one of: 'Revenue', 'COGS', 'Opex', 'Headcount'
  - account_category is a sub-grouping within account_type, e.g. 'Travel', 'Software', 'Salaries'

dim_cost_center (cost_center_id TEXT PRIMARY KEY, cost_center_name TEXT, department TEXT, region TEXT)
  - department is one of: 'Engineering', 'Marketing', 'Sales'
  - region is one of: 'West', 'East', 'EMEA'

gl_data (date TEXT, cost_center_id TEXT, account_code TEXT, version TEXT, amount REAL)
  - date is the first of the month, 'YYYY-MM-DD', one row per (date, cost_center_id, account_code, version)
  - cost_center_id references dim_cost_center.cost_center_id
  - account_code references dim_account.account_code
  - version is one of: 'budget_original', 'forecast_mar', 'forecast_jun', 'actual'
  - amount is USD

Relationships: gl_data.cost_center_id -> dim_cost_center.cost_center_id,
gl_data.account_code -> dim_account.account_code. Join through gl_data for any
question that needs account_type, account_category, department, or region.

Ambiguous conventions - read before writing SQL:
- Always filter version = 'actual' unless the question explicitly asks about budget
  or forecast. Never mix versions in a single SUM/AVG without an explicit reason
  (e.g. a variance calculation) - mixing them silently produces a meaningless total.
- "Budget" means version = 'budget_original'.
- "Forecast" is ambiguous by itself: it means whichever of 'forecast_mar' /
  'forecast_jun' is more recent relative to the date range in question, unless the
  user names one specifically. A question about "how the forecast changed" should
  compare 'forecast_mar' vs 'forecast_jun' (or either vs 'budget_original').
- Revenue and COGS accounts (account_type IN ('Revenue', 'COGS')) are only booked to
  Sales cost centers in this dataset - a margin question does not need to filter by
  cost center for that reason.
- gl_data has no year/month columns - filter on date with strftime('%Y', date) /
  strftime('%m', date), or a literal date range (dates are always YYYY-MM-01).
"""

FEW_SHOT_EXAMPLES = [
    {
        "question": "What was total actual Opex in 2024?",
        "sql": (
            "SELECT SUM(g.amount) AS total_opex "
            "FROM gl_data g "
            "JOIN dim_account a ON a.account_code = g.account_code "
            "WHERE a.account_type = 'Opex' "
            "AND g.version = 'actual' "
            "AND strftime('%Y', g.date) = '2024';"
        ),
    },
    {
        "question": "What was the budget variance in dollars for Engineering in Q1 2024?",
        "sql": (
            "SELECT g.version, SUM(g.amount) AS total_amount "
            "FROM gl_data g "
            "JOIN dim_cost_center c ON c.cost_center_id = g.cost_center_id "
            "WHERE c.department = 'Engineering' "
            "AND g.version IN ('actual', 'budget_original') "
            "AND g.date BETWEEN '2024-01-01' AND '2024-03-01' "
            "GROUP BY g.version;"
            # Application code then applies metrics.budget_variance_dollar(actual, budget)
            # to the two returned rows - the SQL fetches the raw pieces, Python does the math.
        ),
    },
    {
        "question": "How did the June reforecast for Marketing Opex compare to the March reforecast and the original budget, for 2024?",
        "sql": (
            "SELECT g.version, SUM(g.amount) AS total_amount "
            "FROM gl_data g "
            "JOIN dim_account a ON a.account_code = g.account_code "
            "JOIN dim_cost_center c ON c.cost_center_id = g.cost_center_id "
            "WHERE a.account_type = 'Opex' "
            "AND c.department = 'Marketing' "
            "AND g.version IN ('budget_original', 'forecast_mar', 'forecast_jun') "
            "AND strftime('%Y', g.date) = '2024' "
            "GROUP BY g.version;"
        ),
    },
]


def _render_few_shot_block() -> str:
    lines = [
        "Few-shot examples - follow these SQL patterns for similar questions:",
        "",
    ]
    for i, ex in enumerate(FEW_SHOT_EXAMPLES, start=1):
        lines.append(f"Example {i}")
        lines.append(f"Q: {ex['question']}")
        lines.append(f"SQL: {ex['sql']}")
        lines.append("")
    return "\n".join(lines).rstrip()


FEW_SHOT_BLOCK = _render_few_shot_block()

SYSTEM_PROMPT_INSTRUCTIONS = """\
You are a SQL analyst for an FP&A (financial planning & analysis) team. You
translate plain-English finance questions into a single read-only SQLite query
against the schema below, using the finance metrics layer's exact definitions
whenever a question maps to one of them - never invent your own formula for a
metric that's already defined there. Always call the run_sql_query tool with
your query; never answer in plain text.

Return one query per call. If a metric needs pieces from more than one version
(e.g. actual vs budget), select the raw amounts grouped by version and let the
application code apply the formula - do not try to compute a percentage variance
inside SQL.
"""


def build_system_prompt() -> str:
    """Schema + metrics layer + few-shot, concatenated for verbatim injection."""
    return "\n\n".join(
        [
            SYSTEM_PROMPT_INSTRUCTIONS,
            SCHEMA_DESCRIPTION,
            METRICS_PROMPT_BLOCK,
            FEW_SHOT_BLOCK,
        ]
    )


SYSTEM_PROMPT = build_system_prompt()
