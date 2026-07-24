# Project Spec: Copilot-Powered FP&A Natural Language Query Tool

## One-line description
A Streamlit app where a user asks plain-English FP&A questions (variance, margin, forecast changes) about a synthetic general-ledger dataset, and Claude translates the question into validated SQL, executes it, self-corrects on failure, and returns results with auto-generated charts and finance commentary.

## Why this project exists (context for Claude Code, not a build instruction)
This is a passion project combining data science and FP&A workflows. Goal: demonstrate DS skills (Python, SQL) applied to a genuine FP&A workflow, with defensible "agentic AI" behavior (self-correcting retry loop) and real finance logic (not generic BI buzzwords). Every design choice should favor being explainable and defensible over being flashy.

---

## Tech stack
- Python 3.11+
- SQLite (local file DB)
- Anthropic Python SDK (`anthropic`), model: `claude-sonnet-4-5`
- Streamlit for UI
- pandas for data handling
- plotly (or matplotlib) for charts
- pytest for the eval harness

---

## Part 1: Database schema

Three core tables, designed to look like a real chart-of-accounts / GL structure, not generic sales data.

### `dim_account`
| column | type | notes |
|---|---|---|
| account_code | TEXT PK | e.g. "5010" |
| account_name | TEXT | e.g. "Marketing Opex" |
| account_type | TEXT | one of: Revenue, COGS, Opex, Headcount |
| account_category | TEXT | sub-grouping, e.g. "Salaries", "Travel", "Software" |

### `dim_cost_center`
| column | type | notes |
|---|---|---|
| cost_center_id | TEXT PK | e.g. "CC-100" |
| cost_center_name | TEXT | e.g. "Engineering - West" |
| department | TEXT | e.g. "Engineering", "Marketing", "Sales" |
| region | TEXT | e.g. "West", "East", "EMEA" |

### `gl_data`
| column | type | notes |
|---|---|---|
| date | TEXT | first of month, YYYY-MM-DD |
| cost_center_id | TEXT FK | -> dim_cost_center |
| account_code | TEXT FK | -> dim_account |
| version | TEXT | one of: 'budget_original', 'forecast', 'actual' |
| amount | REAL | in USD |

**Important modeling detail:** the `version` field is what enables three-way variance (original budget vs. reforecast vs. actual) — this is the key structural feature that separates this from a generic budget-vs-actual demo. Generate at least 2 reforecast snapshots per year (e.g., a March reforecast and a June reforecast) so "how did the forecast change" questions are answerable.

### Synthetic data generation requirements
- 3 years of monthly data
- ~6 cost centers across ~3 departments and ~3 regions
- ~15-20 accounts spanning all 4 account_types
- Realistic seasonality (e.g., Q4 revenue bump, summer travel dip)
- Actuals should deviate from budget/forecast in plausible, non-random ways (some cost centers consistently run over budget, some under — not pure noise) so variance analysis has real signal
- Write this as a standalone `generate_data.py` script that outputs to a SQLite file, so the dataset is reproducible

---

## Part 2: Finance metrics layer

Build this as an explicit Python module (`metrics.py`) — a dictionary or set of functions — NOT left for Claude to infer on the fly. Claude's system prompt should reference this layer by name and be instructed to use these exact definitions when a question maps to one of them.

Required metrics:
- **Budget variance ($)**: `actual - budget`
- **Budget variance (%)**: `(actual - budget) / budget`
- **Forecast variance ($/%)**: same as above but `actual` vs `forecast` version, or `forecast` vs `budget_original`
- **Gross margin %**: `(revenue - COGS) / revenue`
- **Opex ratio**: `opex / revenue`
- **YoY growth**: `(current_period - same_period_last_year) / same_period_last_year`
- **MoM growth**: `(current_month - prior_month) / prior_month`
- **Run-rate**: `latest_month_actual * 12`
- **Headcount cost per FTE**: `total_headcount_cost / fte_count` (if you add an FTE count field — optional, note as a stretch item)

Each metric definition should be documented with its formula in plain text so it can be injected into the Claude system prompt verbatim.

---

## Part 3: Core pipeline (build in this order)

### 3a. Schema description generator
A function/constant that renders the schema (tables + column meanings + relationships) as a text block for the system prompt. Include explicit notes on ambiguous conventions (e.g., "always filter version = 'actual' unless the question asks about budget or forecast specifically").

### 3b. Few-shot examples
2-3 hardcoded (question, correct SQL) pairs covering: a simple aggregation, a variance calculation, and a three-way version comparison. Embed in the system prompt.

### 3c. Tool-calling setup
Define a `run_sql_query` tool with a single `query` string parameter. Use `tool_choice` to force Claude to respond via the tool (not free text). This avoids regex-parsing SQL out of prose.

### 3d. SQL generation function
`get_sql_query(question: str) -> str` — sends system prompt (schema + metrics layer + few-shot) + user question to Claude, extracts SQL from the tool-use block.

### 3e. Safety validation
Before execution, reject any query containing INSERT/UPDATE/DELETE/DROP/ALTER/CREATE (case-insensitive check). Read-only enforcement.

### 3f. Execution function
`execute_sql(query: str) -> dict` — runs against SQLite, returns `{"success": True, "data": df}` or `{"success": False, "error": str}`. Catch exceptions explicitly, don't let them crash the app.

### 3g. Self-correcting retry loop (the "agentic" piece)
`answer_question(question: str, max_retries=2)`:
1. Generate SQL, validate, execute.
2. If execution fails: send the error message + original question + schema back to Claude in a follow-up turn, ask it to revise the query, retry.
3. Cap at `max_retries` attempts, then return a graceful failure message.
4. Log every attempt (question, SQL generated, success/fail, error if any) for later analysis — this log is what produces the baseline-vs-improved accuracy stat.

### 3h. Variance commentary generation
For questions that resolve to a variance-type metric, add a second Claude call that takes the query result and generates a short (1-2 sentence) plain-English commentary in the style of an FP&A close packet — e.g., "Opex variance driven primarily by a $40K overage in Marketing, partially offset by savings in Engineering headcount." Keep this as a separate, clearly-labeled function so it's easy to point to in code review.

### 3i. Chart selection logic (deterministic, NOT AI-driven)
Rule-based function that inspects the shape of the result DataFrame and picks a chart type:
- Single scalar -> big number display
- Time series (date column + one metric) -> line chart
- Category breakdown -> bar chart
- Bridge/waterfall-type question (detect via keyword or explicit flag from the question classification step) -> waterfall chart

Document this explicitly as "rule-based, not LLM-based" in code comments — it's a deliberate design choice worth being able to explain.

---

## Part 4: Evaluation harness

Build `eval_questions.json` — 15-20 questions written BEFORE testing the pipeline, each with hand-computed ground truth (the correct final value/answer, not just SQL, since SQL phrasing can vary). Include:
- Simple aggregations (5-6)
- Variance questions using version field (5-6)
- Ambiguous/harder questions requiring the metrics layer (4-5)
- At least 2-3 designed to intentionally fail on the first attempt (to prove the retry loop matters) — e.g., ambiguous column references

Build `run_eval.py` (pytest or standalone script) that runs all questions through the pipeline twice: once with retry disabled (baseline), once with retry enabled. Output accuracy % for both, plus a log of which questions were fixed by the retry loop. This produces the exact resume stat: "improved accuracy from X% to Y%."

---

## Part 5: Streamlit UI

- Chat-style input box
- Display: the final answer, a toggle to reveal the generated SQL, the result table, the auto-selected chart, and (if applicable) the variance commentary
- Sidebar: brief description of the dataset/schema for context
- Optional: a few pre-loaded example questions as clickable buttons for easy demoing

---

## Part 6: Documentation

`README.md` should lead with the business problem, not the tech stack:
1. Opening paragraph: the FP&A pain point (manual SQL writing for ad hoc finance questions, monthly close reporting burden)
2. How it works (brief, one paragraph)
3. Key technical decisions worth highlighting: schema-grounded prompting, tool-calling for structured output, self-correcting retry loop, deterministic (non-AI) chart selection, dedicated metrics layer instead of letting the LLM invent formulas
4. Accuracy results (baseline vs. with retry loop) from the eval harness
5. Setup/run instructions
6. Stretch/future work section (scenario/what-if module, Microsoft 10-K case study) — fine to list as "not yet built" if you run out of time

---

## Explicitly out of scope for v1 (don't let Claude Code wander into these)
- Multi-user auth
- Cloud deployment infra beyond Streamlit Community Cloud
- Any write/update capability to the database
- A general-purpose (non-finance) query tool — keep it grounded to this schema

## Stretch goals (only after v1 works end-to-end)
- Scenario/what-if module using NPV/IRR-style recalculation
- Threshold-based compliance flagging (e.g., cost centers over budget by >10%) with auto-summary
- Second dataset built from Microsoft's public 10-K segment data as a demo case study
