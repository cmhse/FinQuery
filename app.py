"""
Streamlit UI (Part 5 of the spec).

Chat-style front end over the pipeline in src/: ask a question, see the
answer, the generated SQL (behind a toggle), the result table, the
rule-based chart, and - for variance-shaped questions - a short FP&A-style
commentary line.
"""

import streamlit as st

from src.chart_selector import build_chart
from src.commentary import generate_variance_commentary, is_variance_question
from src.prompt import SCHEMA_DESCRIPTION
from src.sql_agent import answer_question

st.set_page_config(page_title="FinQuery", page_icon="\U0001F4CA", layout="wide")

EXAMPLE_QUESTIONS = [
    "What was total actual revenue in 2024?",
    "What was the actual vs. budget variance in dollars for Marketing Opex in 2024?",
    "What was the gross margin percentage in 2024?",
    "How did the June reforecast compare to the March reforecast for Engineering Headcount in 2024?",
    "Which department had actual Opex furthest over budget in 2024?",
]

if "history" not in st.session_state:
    st.session_state.history = []
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None


def _format_dollar(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def render_result(question: str, result: dict) -> None:
    if not result["success"]:
        st.error(f"Couldn't get a working query after retrying: {result.get('error')}")
        with st.expander("Attempt log"):
            st.json(result["attempts"])
        return

    df = result["data"]
    chart_type, fig, value = build_chart(df, question)

    st.markdown("**Answer**")
    if chart_type == "big_number" and value is not None:
        st.metric(label="Result", value=_format_dollar(value) if abs(value) > 1 else f"{value:,.4f}")
    else:
        st.write("See the table and chart below.")

    with st.expander("Show generated SQL"):
        st.code(result["sql"], language="sql")
        if len(result["attempts"]) > 1:
            st.caption(
                f"Needed {len(result['attempts'])} attempt(s) - the first query "
                "failed and the model self-corrected after seeing the error."
            )

    st.markdown("**Result table**")
    st.dataframe(df, width="stretch")

    if chart_type in ("line", "bar", "waterfall") and fig is not None:
        st.markdown("**Chart**")
        st.plotly_chart(fig, width="stretch")

    if is_variance_question(question):
        st.markdown("**Commentary**")
        with st.spinner("Generating commentary..."):
            commentary = generate_variance_commentary(question, df)
        st.info(commentary)


def handle_question(question: str) -> None:
    with st.spinner("Generating SQL, running query, self-correcting on failure..."):
        result = answer_question(question)
    st.session_state.history.append({"question": question, "result": result})


with st.sidebar:
    st.header("About this dataset")
    st.markdown(
        "Synthetic 3-year general-ledger dataset: 6 cost centers across 3 "
        "departments and 3 regions, ~16 accounts spanning Revenue / COGS / "
        "Opex / Headcount, with realistic seasonality and per-cost-center "
        "budget bias so variance analysis has real signal."
    )
    with st.expander("Schema reference"):
        st.text(SCHEMA_DESCRIPTION)

    st.header("Try an example")
    for q in EXAMPLE_QUESTIONS:
        if st.button(q, key=f"example_{q}", width="stretch"):
            st.session_state.pending_question = q

    if st.session_state.history and st.button("Clear conversation"):
        st.session_state.history = []

st.title("FinQuery")
st.caption(
    "Ask a plain-English FP&A question. Claude translates it into validated "
    "SQL, executes it against the GL database, self-corrects on failure, and "
    "returns results with an auto-selected chart and finance commentary."
)

for turn in st.session_state.history:
    with st.chat_message("user"):
        st.write(turn["question"])
    with st.chat_message("assistant"):
        render_result(turn["question"], turn["result"])

if st.session_state.pending_question:
    question = st.session_state.pending_question
    st.session_state.pending_question = None
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        handle_question(question)
        render_result(question, st.session_state.history[-1]["result"])

user_question = st.chat_input("Ask a finance question, e.g. \"What was Q4 revenue vs budget?\"")
if user_question:
    with st.chat_message("user"):
        st.write(user_question)
    with st.chat_message("assistant"):
        handle_question(user_question)
        render_result(user_question, st.session_state.history[-1]["result"])
