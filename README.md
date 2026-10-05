# FinQuery

FP&A teams burn a surprising amount of time on questions that are conceptually
simple but mechanically annoying: "what was our budget variance in Marketing
last quarter," "how did the forecast move between March and June," "what's
our run-rate." Answering them usually means someone who knows SQL (or Excel
well enough to fake it) goes and writes the query, checks it against the
chart of accounts, and reformats the result for a close packet — repeated
every month, for every stakeholder who doesn't have that SQL person on
speed-dial. FinQuery is a small Streamlit app that lets someone ask that
question in plain English and get back a validated SQL query, the result
table, an appropriate chart, and (for variance-shaped questions) a one-line
explanation of what drove the number — against a synthetic but realistic
general-ledger dataset.

## How it works

A user's question goes to Claude along with a system prompt built from three
pieces: a description of the GL schema (including the ambiguous conventions
a human analyst would just "know," like "budget variance always means vs.
`budget_original`"), an explicit finance metrics layer with the exact
formulas for margin/variance/growth/run-rate, and a few hand-picked
(question, SQL) examples. Claude responds via a forced tool call
(`run_sql_query`), never free text, so there's no SQL-out-of-prose parsing to
get wrong. The query is checked against a read-only keyword denylist, run
against the SQLite database, and — if it fails — the error is fed back to
Claude in a follow-up turn so it can revise and retry, up to a configurable
cap. A separate, rule-based (non-LLM) function inspects the shape of the
result and picks a chart type; a second Claude call generates a short
FP&A-style commentary line for variance questions.

## Key technical decisions

- **Schema-grounded prompting.** The system prompt is generated from a single
  source of truth (`src/prompt.py`) that documents not just table/column
  names but the conventions a new analyst would need explained — e.g. always
  filter to `version = 'actual'` unless the question says otherwise. This is
  meant to be pointed at directly in review, not reverse-engineered from
  prompt strings scattered through the codebase.
- **Tool-calling for structured output**, with `tool_choice` forced to
  `run_sql_query`. This avoids regex-parsing SQL out of a text response and
  makes "did the model actually produce a query" a type-level guarantee
  rather than a string-matching problem.
- **Self-correcting retry loop.** `answer_question()` in `src/sql_agent.py`
  is the one piece of behavior in this project that's genuinely agentic —
  everything else is a deterministic pipeline stage. On a failed query, the
  error is sent back to Claude as a `tool_result` with `is_error: true`
  alongside the original conversation, and it gets another shot, capped at
  `max_retries`. Every attempt (question, SQL, success/failure, error) is
  logged to `logs/query_log.jsonl` and by the eval harness, which is what
  produces the baseline-vs-retry accuracy stat below.
- **Deterministic, non-AI chart selection.** `src/chart_selector.py` picks a
  chart type purely from the shape of the result DataFrame (a single scalar,
  a date column + a metric, a category breakdown, or a waterfall/bridge
  question) — never by asking Claude. This is a deliberate choice: it's
  explainable, testable without any API calls, and doesn't burn a request on
  a decision that's really just "does this look like a time series."
- **A dedicated finance metrics layer** (`src/metrics.py`), not left for the
  model to infer. Budget/forecast variance, gross margin %, opex ratio,
  YoY/MoM growth, and run-rate are all defined once as plain-text formulas
  and Python functions, and the system prompt is told to use those exact
  definitions rather than invent its own math for a metric with a name it
  half-recognizes.

## Accuracy results

`run_eval.py` runs the 25 hand-written questions in `eval_questions.json`
(each with a ground-truth value computed directly against the database,
independent of the LLM) through the pipeline twice — once with
`max_retries=0` (baseline) and once with the retry loop enabled — and reports
accuracy for both, plus which questions were only fixed by the retry loop.

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # or: ant auth login
python run_eval.py
```

**Actual result, run against `claude-sonnet-5`:**

```
Baseline accuracy:      100.0% (25/25)
Retry-enabled accuracy: 100.0% (25/25)
Questions fixed by the retry loop: none
```

Three of the twenty-five questions were deliberately written to invite a
first-attempt mistake — a self-join SQLite would reject as an ambiguous
column reference unless every selected column is qualified (for a
month-over-month comparison), and a filter a naive model might write against
a nonexistent `year` column instead of `strftime('%Y', date)` (for
year-over-year growth). In practice, the retry loop never fired at all: every
one of the 25 questions, including those three, produced a correct query on
the very first attempt in both passes. The schema-grounded system prompt and
few-shot examples appear to be doing enough work up front that this
particular eval set doesn't exercise the self-correction path — which says
more about this model's baseline SQL competence on a well-documented schema
than it does about the retry loop being unnecessary. The loop is still there
as a safety net for cases this eval didn't happen to hit (schema drift,
odder phrasing, a genuinely ambiguous multi-table join); a fuller eval set
aimed at reliably reproducing the retry-loop-saves-the-day story would need
harder failure modes than these three.

A full per-question report is written to `logs/eval_report.json`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python src/generate_data.py
```

This writes `data/finquery.db` (gitignored, regenerate anytime — the
generator is seeded, so output is reproducible).

You'll also need an Anthropic API key for anything that calls Claude
(the app, the eval harness):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Running it

**The app:**

```bash
streamlit run app.py
```

Opens a chat UI with example-question buttons in the sidebar, a toggle to
reveal the generated SQL, the result table, the chart, and commentary where
applicable.

**The eval harness:**

```bash
python run_eval.py
```

Runs both the baseline and retry-enabled passes described above. Use
`python run_eval.py --max-retries N` to change the retry cap for the
retry-enabled pass (default 2).

## Stretch / future work

Not yet built — listed here rather than left implicit:

- **Scenario/what-if module** using NPV/IRR-style recalculation.
- **Threshold-based compliance flagging** (e.g. cost centers over budget by
  more than 10%) with an auto-generated summary.
