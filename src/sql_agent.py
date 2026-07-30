"""
Core SQL-generation pipeline (Part 3c-3g of the spec).

Pipeline stages, each a separate function so they can be tested/pointed to
independently:
  - RUN_SQL_QUERY_TOOL / get_sql_query  - tool-calling SQL generation (3c, 3d)
  - validate_sql                        - read-only safety check (3e)
  - execute_sql                         - runs against SQLite (3f)
  - answer_question                     - self-correcting retry loop (3g)

Every attempt made inside answer_question (question, SQL generated, success/fail,
error if any) is logged to logs/query_log.jsonl - this log is what the eval
harness (Part 4) reads to compute baseline-vs-retry accuracy.
"""

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from src.claude_client import MODEL, get_client
from src.prompt import SYSTEM_PROMPT

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "finquery.db"
LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "query_log.jsonl"

# Read-only enforcement (3e): reject any query containing a write/DDL keyword,
# case-insensitive, matched on word boundaries so it can't be dodged by
# concatenation tricks but also doesn't false-positive on substrings like
# "updated_at" appearing in a column name.
FORBIDDEN_KEYWORDS = ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE")
_FORBIDDEN_PATTERN = re.compile(
    r"\b(" + "|".join(FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE
)

RUN_SQL_QUERY_TOOL = {
    "name": "run_sql_query",
    "description": (
        "Execute a single read-only SQL query against the FinQuery general-ledger "
        "SQLite database and return the result rows."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A single read-only SQLite SELECT query.",
            }
        },
        "required": ["query"],
    },
}


def _extract_tool_use(response) -> tuple[str, str]:
    """Pull (tool_use_id, sql) out of a forced tool_choice response."""
    for block in response.content:
        if block.type == "tool_use" and block.name == "run_sql_query":
            return block.id, block.input["query"]
    raise ValueError("Claude did not return a run_sql_query tool call")


def get_sql_query(question: str) -> str:
    """Single-shot SQL generation, no retry - question in, SQL out."""
    client = get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[RUN_SQL_QUERY_TOOL],
        tool_choice={"type": "tool", "name": "run_sql_query"},
        messages=[{"role": "user", "content": question}],
    )
    _, sql = _extract_tool_use(response)
    return sql


def validate_sql(query: str) -> str | None:
    """Read-only enforcement. Returns an error string if the query is rejected, else None."""
    match = _FORBIDDEN_PATTERN.search(query)
    if match:
        return (
            f"Query rejected: contains forbidden keyword '{match.group(1).upper()}'. "
            "Only read-only SELECT queries are permitted."
        )
    return None


def execute_sql(query: str) -> dict[str, Any]:
    """Runs query against the SQLite DB. Never raises - failures come back as a dict."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            df = pd.read_sql_query(query, conn)
        return {"success": True, "data": df}
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        return {"success": False, "error": str(exc)}


def _log_attempt(entry: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = dict(entry)
    if isinstance(record.get("data"), pd.DataFrame):
        record["data"] = None  # don't bloat the log with result rows
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def answer_question(question: str, max_retries: int = 2) -> dict[str, Any]:
    """
    Self-correcting retry loop (3g):
      1. Generate SQL, validate, execute.
      2. On failure, send the error back to Claude as a tool_result and ask it
         to revise the query, then retry.
      3. Cap at max_retries additional attempts; return a graceful failure.
      4. Log every attempt (question, sql, success/fail, error) for the eval harness.

    max_retries=0 reproduces the "baseline" (no self-correction) pipeline used
    for the eval harness's before/after accuracy comparison.
    """
    client = get_client()
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    attempts: list[dict[str, Any]] = []

    last_sql = None
    last_error = None

    for attempt_num in range(max_retries + 1):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=[RUN_SQL_QUERY_TOOL],
            tool_choice={"type": "tool", "name": "run_sql_query"},
            messages=messages,
        )
        tool_use_id, sql = _extract_tool_use(response)
        messages.append({"role": "assistant", "content": response.content})
        last_sql = sql

        error = validate_sql(sql)
        if error is None:
            result = execute_sql(sql)
        else:
            result = {"success": False, "error": error}

        attempt_record = {
            "question": question,
            "attempt": attempt_num + 1,
            "sql": sql,
            "success": result["success"],
            "error": result.get("error"),
        }
        attempts.append(attempt_record)
        _log_attempt(attempt_record)

        if result["success"]:
            return {
                "success": True,
                "question": question,
                "sql": sql,
                "data": result["data"],
                "attempts": attempts,
            }

        last_error = result["error"]
        if attempt_num < max_retries:
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": (
                                f"That query failed with this error: {last_error}\n"
                                "Revise the SQL to fix the problem and call "
                                "run_sql_query again."
                            ),
                            "is_error": True,
                        }
                    ],
                }
            )

    return {
        "success": False,
        "question": question,
        "sql": last_sql,
        "error": last_error,
        "attempts": attempts,
    }
