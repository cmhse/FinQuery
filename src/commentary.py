"""
Variance commentary generation (Part 3h of the spec).

Deliberately a separate module/function from src.sql_agent - the spec calls
this out explicitly ("Keep this as a separate, clearly-labeled function so
it's easy to point to in code review") because it's a second, distinct Claude
call: SQL generation turns a question into a query, this turns a query
*result* into a short plain-English explanation, in the style of an FP&A
close-packet note (e.g. "Opex variance driven primarily by a $40K overage in
Marketing, partially offset by savings in Engineering headcount.").

Whether a question gets commentary at all is decided by is_variance_question,
a plain keyword check - deliberately not an LLM call, so this classification
step is free, instant, and doesn't risk a request just to decide "should I
make another request."
"""

import pandas as pd

from src.claude_client import MODEL, get_client

VARIANCE_KEYWORDS = (
    "variance",
    "budget",
    "forecast",
    "over budget",
    "under budget",
    "vs.",
    " vs ",
    "versus",
    "compare",
    "compared",
    "reforecast",
    "against plan",
    "on plan",
)

COMMENTARY_SYSTEM_PROMPT = """\
You write one- to two-sentence variance commentary in the style of an FP&A
close packet - the kind of line a finance analyst would put next to a number
in a board deck. Given the original question and the query result, name the
main driver(s) of the variance in plain English with concrete dollar or
percent figures pulled from the data. Do not restate the question, do not
add caveats or disclaimers, and do not exceed two sentences.
"""


def is_variance_question(question: str) -> bool:
    """Rule-based (not LLM) classification of whether a question is variance-shaped."""
    lowered = question.lower()
    return any(keyword in lowered for keyword in VARIANCE_KEYWORDS)


def generate_variance_commentary(question: str, df: pd.DataFrame) -> str:
    """Second Claude call: query result -> short FP&A-style commentary."""
    client = get_client()
    result_text = df.to_string(index=False, max_rows=25)
    user_content = (
        f"Question: {question}\n\n"
        f"Query result:\n{result_text}\n\n"
        "Write the variance commentary."
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        system=COMMENTARY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    return next(
        (block.text for block in response.content if block.type == "text"), ""
    ).strip()
