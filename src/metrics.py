"""
Finance metrics layer (Part 2 of the spec).

Each metric has an explicit formula and Python function — Claude is never
asked to invent a formula on the fly. `METRICS_PROMPT_BLOCK` renders the
definitions as plain text for injection into the SQL-generation system
prompt, so the model is told to use these exact definitions whenever a
question maps to one of them (e.g. "margin", "variance", "run rate").

All compute functions take pre-aggregated scalars (the SQL query does the
SUM/GROUP BY over gl_data; Python then applies the exact formula) and
return None instead of raising on divide-by-zero, so callers can render
"N/A" rather than crash.
"""


def _safe_ratio(numerator, denominator):
    if denominator == 0:
        return None
    return numerator / denominator


def budget_variance_dollar(actual, budget):
    """actual - budget"""
    return actual - budget


def budget_variance_pct(actual, budget):
    """(actual - budget) / budget"""
    return _safe_ratio(actual - budget, budget)


def forecast_variance_dollar(actual, forecast):
    """actual - forecast   (forecast = forecast_mar, forecast_jun, or budget_original)"""
    return actual - forecast


def forecast_variance_pct(actual, forecast):
    """(actual - forecast) / forecast"""
    return _safe_ratio(actual - forecast, forecast)


def gross_margin_pct(revenue, cogs):
    """(revenue - cogs) / revenue"""
    return _safe_ratio(revenue - cogs, revenue)


def opex_ratio(opex, revenue):
    """opex / revenue"""
    return _safe_ratio(opex, revenue)


def yoy_growth(current_period, same_period_last_year):
    """(current_period - same_period_last_year) / same_period_last_year"""
    return _safe_ratio(current_period - same_period_last_year, same_period_last_year)


def mom_growth(current_month, prior_month):
    """(current_month - prior_month) / prior_month"""
    return _safe_ratio(current_month - prior_month, prior_month)


def run_rate(latest_month_actual):
    """latest_month_actual * 12"""
    return latest_month_actual * 12


def headcount_cost_per_fte(total_headcount_cost, fte_count):
    """total_headcount_cost / fte_count

    Stretch metric: gl_data has no FTE count field, so this can only be
    computed if a caller supplies fte_count from elsewhere. Included for
    completeness of the metrics layer, not currently wired to real data.
    """
    return _safe_ratio(total_headcount_cost, fte_count)


# Registry drives both the system-prompt text block and (optionally) direct
# lookup/dispatch by key elsewhere in the app.
METRICS = {
    "budget_variance_dollar": {
        "name": "Budget variance ($)",
        "formula": "actual - budget",
        "notes": "budget = version 'budget_original'.",
        "fn": budget_variance_dollar,
    },
    "budget_variance_pct": {
        "name": "Budget variance (%)",
        "formula": "(actual - budget) / budget",
        "notes": "budget = version 'budget_original'.",
        "fn": budget_variance_pct,
    },
    "forecast_variance_dollar": {
        "name": "Forecast variance ($)",
        "formula": "actual - forecast",
        "notes": "forecast = version 'forecast_mar' or 'forecast_jun' (most recent reforecast unless the question specifies one), or compare 'forecast_mar'/'forecast_jun' against 'budget_original' to see how the forecast itself moved.",
        "fn": forecast_variance_dollar,
    },
    "forecast_variance_pct": {
        "name": "Forecast variance (%)",
        "formula": "(actual - forecast) / forecast",
        "notes": "Same version rules as forecast variance ($).",
        "fn": forecast_variance_pct,
    },
    "gross_margin_pct": {
        "name": "Gross margin %",
        "formula": "(revenue - cogs) / revenue",
        "notes": "revenue = SUM(amount) where dim_account.account_type = 'Revenue'; cogs = SUM(amount) where account_type = 'COGS'. Same version/date/cost_center scope for both.",
        "fn": gross_margin_pct,
    },
    "opex_ratio": {
        "name": "Opex ratio",
        "formula": "opex / revenue",
        "notes": "opex = SUM(amount) where account_type = 'Opex'.",
        "fn": opex_ratio,
    },
    "yoy_growth": {
        "name": "YoY growth",
        "formula": "(current_period - same_period_last_year) / same_period_last_year",
        "notes": "Compare the same month/quarter one year apart, same version (usually 'actual').",
        "fn": yoy_growth,
    },
    "mom_growth": {
        "name": "MoM growth",
        "formula": "(current_month - prior_month) / prior_month",
        "notes": "Adjacent calendar months, same version.",
        "fn": mom_growth,
    },
    "run_rate": {
        "name": "Run-rate",
        "formula": "latest_month_actual * 12",
        "notes": "latest_month_actual = most recent month with version = 'actual'.",
        "fn": run_rate,
    },
    "headcount_cost_per_fte": {
        "name": "Headcount cost per FTE",
        "formula": "total_headcount_cost / fte_count",
        "notes": "Stretch metric — gl_data has no fte_count field yet, not derivable from the current schema.",
        "fn": headcount_cost_per_fte,
    },
}


def render_metrics_prompt_block():
    """Plain-text rendering of METRICS for verbatim injection into the system prompt."""
    lines = ["Finance metrics layer — use these exact definitions when a question maps to one of them:"]
    for metric in METRICS.values():
        lines.append(f"- {metric['name']}: {metric['formula']}")
        if metric["notes"]:
            lines.append(f"  {metric['notes']}")
    return "\n".join(lines)


METRICS_PROMPT_BLOCK = render_metrics_prompt_block()


if __name__ == "__main__":
    print(METRICS_PROMPT_BLOCK)
