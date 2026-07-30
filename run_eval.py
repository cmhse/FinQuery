"""
Evaluation harness (Part 4 of the spec).

Runs every question in eval_questions.json through the pipeline twice:
  - baseline:  src.sql_agent.answer_question(question, max_retries=0)
  - retry:     src.sql_agent.answer_question(question, max_retries=2)

and grades each run's result DataFrame against a hand-computed expected_value
(see eval_questions.json - those values were computed directly against
data/finquery.db, independent of the LLM pipeline, so grading never depends on
the model "agreeing with itself"). Prints per-question pass/fail for both
runs plus the aggregate accuracy stat, and writes a JSON report.

Usage:
    python run_eval.py
"""

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from src.sql_agent import answer_question

REPO_ROOT = Path(__file__).resolve().parent
QUESTIONS_PATH = REPO_ROOT / "eval_questions.json"
REPORT_PATH = REPO_ROOT / "logs" / "eval_report.json"


def _lookup(series: pd.Series, key: str):
    """
    Look up a group value, tolerating date-grouping granularity the model is
    free to choose. 'What was actual in November 2024' can legitimately come
    back grouped by the full date ('2024-11-01') or by strftime('%Y-%m', ...)
    ('2024-11') - both are correct SQL, so an exact-match-only lookup would
    penalize a valid query for a formatting choice the schema doesn't pin
    down. Falls back to a year-month prefix match before giving up.
    """
    if key in series.index:
        return series[key]
    prefix_matches = [v for idx, v in series.items() if str(idx).startswith(key[:7])]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    raise KeyError(key)


def _reduce(df: pd.DataFrame, grading: dict[str, Any]):
    """
    Collapse a SQL result DataFrame down to the single scalar the question
    asked for, per the question's declarative `grading` spec. Deliberately
    matches group values (e.g. 'actual', 'budget_original', '2024-11-01')
    rather than column names, since the model is free to name/alias columns
    however it likes - only the *values* it groups by are constrained by the
    schema's fixed vocabulary (version labels, dates, account_type labels).
    """
    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    non_numeric_cols = [c for c in df.columns if c not in numeric_cols]

    # If the model already computed a single final value (e.g. did the
    # subtraction/division itself in SQL), trust it rather than force-fitting
    # the group-based reduction below.
    if len(df) == 1 and len(numeric_cols) == 1 and grading["reduce"] not in ("scalar", "scalar_or_scaled"):
        return float(df[numeric_cols[0]].iloc[0])

    reduce_type = grading["reduce"]
    if reduce_type in ("scalar", "scalar_or_scaled"):
        if not numeric_cols or df.empty:
            raise ValueError("no numeric column to read a scalar from")
        return float(df[numeric_cols[0]].iloc[0])

    if not non_numeric_cols:
        raise ValueError("expected a grouping column but result has none")
    group_col = non_numeric_cols[0]
    value_col = numeric_cols[0]
    series = df.set_index(group_col)[value_col]

    if reduce_type == "diff":
        return float(_lookup(series, grading["minuend"]) - _lookup(series, grading["subtrahend"]))
    if reduce_type == "pct_diff":
        a, b = _lookup(series, grading["minuend"]), _lookup(series, grading["subtrahend"])
        return float((a - b) / b)
    if reduce_type == "ratio":
        return float(_lookup(series, grading["numerator"]) / _lookup(series, grading["denominator"]))
    if reduce_type == "margin":
        num, sub = _lookup(series, grading["numerator"]), _lookup(series, grading["subtract"])
        return float((num - sub) / num)

    raise ValueError(f"unknown reduce type: {reduce_type}")


def grade(result: dict[str, Any], eq: dict[str, Any]) -> dict[str, Any]:
    """Grade one pipeline result against one eval_questions.json entry."""
    if not result["success"]:
        return {"correct": False, "reason": f"pipeline failed: {result.get('error')}"}
    try:
        actual_value = _reduce(result["data"], eq["grading"])
    except (KeyError, ValueError) as exc:
        return {"correct": False, "reason": f"could not grade result shape: {exc}"}

    expected = eq["expected_value"]
    tolerance = eq.get("tolerance", 0)

    def _within(value: float) -> bool:
        if tolerance == 0:
            return value == expected
        if eq["expected_type"] == "dollar":
            return abs(value - expected) <= max(abs(expected) * tolerance, 0.01)
        return abs(value - expected) <= tolerance

    # scalar_or_scaled: the metrics-layer note for this question is a
    # single-step arithmetic transform (e.g. run-rate = latest_month * 12).
    # The system prompt's few-shot pattern otherwise teaches "return raw
    # values, let the app combine them" for anything needing more than one
    # SQL result, so a model applying that same instinct here returns the
    # raw value rather than doing the multiplication in SQL - both are a
    # correct reading of the instructions, so accept either.
    if eq["grading"]["reduce"] == "scalar_or_scaled":
        scale = eq["grading"]["scale"]
        correct = _within(actual_value) or _within(actual_value * scale)
    else:
        correct = _within(actual_value)

    return {"correct": correct, "actual_value": actual_value, "reason": None}


def run_pass(questions: list[dict[str, Any]], max_retries: int, label: str) -> dict[str, Any]:
    print(f"\n=== Running {label} pass (max_retries={max_retries}) ===")
    per_question = []
    for eq in questions:
        result = answer_question(eq["question"], max_retries=max_retries)
        outcome = grade(result, eq)
        attempts_used = len(result.get("attempts", []))
        per_question.append(
            {
                "id": eq["id"],
                "category": eq["category"],
                "correct": outcome["correct"],
                "attempts_used": attempts_used,
                "reason": outcome.get("reason"),
            }
        )
        status = "PASS" if outcome["correct"] else "FAIL"
        print(f"  [{status}] {eq['id']} ({attempts_used} attempt(s)) - {eq['question']}")
        if not outcome["correct"] and outcome.get("reason"):
            print(f"         reason: {outcome['reason']}")

    n_correct = sum(1 for r in per_question if r["correct"])
    accuracy = n_correct / len(questions)
    print(f"{label} accuracy: {n_correct}/{len(questions)} = {accuracy:.1%}")
    return {"label": label, "accuracy": accuracy, "n_correct": n_correct, "n_total": len(questions), "results": per_question}


def main():
    parser = argparse.ArgumentParser(description="Run the FinQuery eval harness.")
    parser.add_argument("--max-retries", type=int, default=2, help="retries for the 'retry-enabled' pass")
    args = parser.parse_args()

    questions = json.loads(QUESTIONS_PATH.read_text())

    baseline = run_pass(questions, max_retries=0, label="baseline (no retry)")
    retry = run_pass(questions, max_retries=args.max_retries, label="retry-enabled")

    fixed_by_retry = [
        r_retry["id"]
        for r_base, r_retry in zip(baseline["results"], retry["results"])
        if not r_base["correct"] and r_retry["correct"]
    ]

    print("\n=== Summary ===")
    print(f"Baseline accuracy:      {baseline['accuracy']:.1%} ({baseline['n_correct']}/{baseline['n_total']})")
    print(f"Retry-enabled accuracy: {retry['accuracy']:.1%} ({retry['n_correct']}/{retry['n_total']})")
    print(f"Questions fixed by the retry loop: {fixed_by_retry or 'none'}")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {"baseline": baseline, "retry": retry, "fixed_by_retry": fixed_by_retry},
            indent=2,
        )
    )
    print(f"\nFull report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
