"""
Chart-type selection (Part 3i of the spec).

Rule-based, NOT LLM-based: this is a deliberate design choice, not a shortcut.
A deterministic function that maps the *shape* of the result DataFrame to a
chart type is explainable in a code review ("here's exactly why this question
got a line chart") and testable without ever calling the model. Asking Claude
to pick a chart type would add latency, cost, and a source of nondeterminism
for a decision that's really just "does this look like a time series."

Rules, in priority order:
  1. Single scalar (1x1, or one row with exactly one numeric column) -> big_number
  2. Question flags a bridge/waterfall view (keyword) -> waterfall
  3. A date-like column plus a numeric column -> line (time series)
  4. A non-numeric (category) column plus a numeric column -> bar
  5. Anything else -> table (no chart)
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

WATERFALL_KEYWORDS = ("waterfall", "bridge", "walk from", "walk")


def _is_datetime_like(series: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    if pd.api.types.is_numeric_dtype(series):
        return False
    # Covers both legacy object-dtype string columns and pandas's newer
    # default string dtype ("str"/StringDtype) - gl_data.date comes back
    # from SQLite as plain "YYYY-MM-01" strings either way.
    try:
        pd.to_datetime(series, format="%Y-%m-%d", errors="raise")
        return True
    except (ValueError, TypeError):
        return False


def select_chart_type(df: pd.DataFrame, question: str = "") -> str:
    """Inspect df's shape (+ a keyword check on the question) and return one of:
    'big_number', 'line', 'bar', 'waterfall', 'table'. Pure function, no LLM call."""
    if df is None or df.empty:
        return "table"

    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    non_numeric_cols = [c for c in df.columns if c not in numeric_cols]

    if df.shape == (1, 1) or (len(df) == 1 and len(numeric_cols) == 1):
        return "big_number"

    if (
        any(k in question.lower() for k in WATERFALL_KEYWORDS)
        and numeric_cols
        and non_numeric_cols
    ):
        return "waterfall"

    date_cols = [c for c in non_numeric_cols if _is_datetime_like(df[c])]
    if date_cols and numeric_cols:
        return "line"

    if non_numeric_cols and numeric_cols:
        return "bar"

    return "table"


def build_chart(df: pd.DataFrame, question: str = ""):
    """
    Returns (chart_type, plotly.graph_objects.Figure | None, scalar_value | None).
    Only one of `figure` / `scalar_value` is populated, depending on chart_type.
    """
    chart_type = select_chart_type(df, question)

    if chart_type == "table":
        return chart_type, None, None

    if chart_type == "big_number":
        value = df.iloc[0, -1] if not df.empty else None
        return chart_type, None, value

    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    non_numeric_cols = [c for c in df.columns if c not in numeric_cols]
    y_col = numeric_cols[0]

    if chart_type == "line":
        date_col = next(
            (c for c in non_numeric_cols if _is_datetime_like(df[c])),
            non_numeric_cols[0],
        )
        fig = px.line(df.sort_values(date_col), x=date_col, y=y_col, markers=True)
        return chart_type, fig, None

    if chart_type == "bar":
        x_col = non_numeric_cols[0]
        fig = px.bar(df, x=x_col, y=y_col)
        return chart_type, fig, None

    if chart_type == "waterfall":
        x_col = non_numeric_cols[0]
        fig = go.Figure(go.Waterfall(x=df[x_col], y=df[y_col]))
        return chart_type, fig, None

    return "table", None, None
