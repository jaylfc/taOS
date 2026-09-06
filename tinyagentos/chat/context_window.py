"""Rolling context-window builder for per-bridge chat context.

Takes a list of channel messages (oldest-first) and returns a trimmed window
respecting both a message count limit and a token budget. Drops oldest
messages first when trimming. System messages (slash-command echoes) are
excluded entirely.
"""
from __future__ import annotations

# History budget when the model context window is unknown (cloud/aliased).
DEFAULT_HISTORY_MAX_TOKENS = 4000
# Agent system prompt + tool definitions are roughly this large (#1740).
SYSTEM_PROMPT_RESERVE = 10000
# Leave room for the model to produce a reply.
RESPONSE_RESERVE = 1024
# Never starve history entirely, even on tiny windows.
MIN_HISTORY_MAX_TOKENS = 512


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def history_token_budget(context_window: int | None) -> int:
    """Compute the history ``max_tokens`` for :func:`build_context_window`.

    When *context_window* is unknown or falsy (cloud or aliased models with no
    local manifest), return the historical default of 4000 so today's behavior
    is preserved. Otherwise reserve room for the agent system prompt and a
    response, flooring at 512 so a tiny window still gets a usable slice.
    """
    if not context_window:
        return DEFAULT_HISTORY_MAX_TOKENS
    return max(
        MIN_HISTORY_MAX_TOKENS,
        int(context_window) - SYSTEM_PROMPT_RESERVE - RESPONSE_RESERVE,
    )


def build_context_window(messages: list[dict], *, limit: int, max_tokens: int) -> list[dict]:
    eligible = [m for m in messages if m.get("author_type") != "system"]
    if len(eligible) > limit:
        eligible = eligible[-limit:]
    while eligible and sum(estimate_tokens(m.get("content", "")) for m in eligible) > max_tokens:
        eligible = eligible[1:]
    return [
        {
            "author_id": m.get("author_id"),
            "author_type": m.get("author_type"),
            "content": m.get("content") or "",
        }
        for m in eligible
    ]
