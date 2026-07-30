"""
Shared Anthropic client + model constant for the SQL agent and commentary modules.

The project spec pins the model to `claude-sonnet-4-5`. That ID is now a legacy
Sonnet generation - `claude-sonnet-5` is the current release in the same tier
(same cost class, current-generation quality), so it's used here instead. Flag
this as a deliberate substitution, not an oversight, if reviewing against the
spec verbatim.

The client is constructed lazily (not at import time) so modules that only need
validate_sql / execute_sql / chart selection can be imported and unit-tested
without an ANTHROPIC_API_KEY set.
"""

import functools

import anthropic

MODEL = "claude-sonnet-5"


@functools.lru_cache(maxsize=1)
def get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic()
