"""Agent memory recall: one call to taOSmd's real ``answer_context``.

``taosmd.answer_context(query, *, agent, data_dir, budget_tokens, now)`` returns
the dated, token-budgeted context an agent answers from. The budget comes from
``taosmd.default_budget_tokens(window, used_tokens, reasoning_reserve=R)``, the
only place taOSmd keeps the answer and reasoning reserves.

This module wraps that pair and adds one thing: it never hides what happened.
Every outcome is a :class:`Recall` with a status, and the caller (the chat
router) logs it, records it in the agent's trace and tells the agent when memory
could not be used. An empty context is never returned without a reason, and an
exception is never swallowed.

Statuses:

``ok``            context assembled
``no_hits``       the store had nothing for this question (normal)
``over_budget``   there were matches, but no session block fit the budget
``no_blocks``     matches exist but none is a session turn (for example only
                  knowledge graph facts, which ``answer_context`` leaves out by default)
``no_budget``     the model window leaves no room for memory after the prompt
``skipped``       nothing to ask (empty question) or the recipient has no store
``error``         ``answer_context`` raised; ``detail`` carries the exception
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import taosmd

logger = logging.getLogger(__name__)

#: Window assumed for a model whose context window taOS cannot read (aliased or
#: cloud models without a manifest). Deliberately modest; the log says it was assumed.
ASSUMED_WINDOW_TOKENS = 16384

#: Reasoning reserve R when the model manifest does not state one.
DEFAULT_REASONING_RESERVE = 0

NOTE_STATUSES = frozenset({"over_budget", "no_blocks", "no_budget", "error"})


@dataclass
class Recall:
    status: str
    context: str = ""
    detail: str = ""
    budget_tokens: int = 0
    window_tokens: int = 0
    window_assumed: bool = False
    tokens_used: int = 0
    hits_total: int = 0
    sessions_included: int = 0
    sessions_dropped: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.status == "ok" and bool(self.context)

    def agent_note(self) -> str:
        """One line for the agent when memory could not be used; empty when nothing needs saying."""
        if self.status not in NOTE_STATUSES:
            return ""
        return f"[memory unavailable for this turn: {self.detail}]"

    def trace_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "budget_tokens": self.budget_tokens,
            "window_tokens": self.window_tokens,
            "window_assumed": self.window_assumed,
            "tokens_used": self.tokens_used,
            "hits_total": self.hits_total,
            "sessions_included": self.sessions_included,
            "sessions_dropped": self.sessions_dropped,
        }


async def recall(
    question: str,
    *,
    agent: str,
    data_dir: Path | str,
    window_tokens: int | None,
    used_tokens: int,
    reasoning_reserve: int = DEFAULT_REASONING_RESERVE,
    now: datetime | None = None,
) -> Recall:
    """Assemble memory context for ``agent`` answering ``question``. Never raises for a recall problem."""
    if not (question or "").strip():
        return Recall(status="skipped", detail="there was no question to look up")
    if not agent:
        return Recall(status="skipped", detail="no agent identity to look memory up for")

    assumed = not window_tokens or window_tokens <= 0
    window = ASSUMED_WINDOW_TOKENS if assumed else int(window_tokens)
    budget = taosmd.default_budget_tokens(window, used_tokens, reasoning_reserve=reasoning_reserve)
    base = Recall(status="ok", budget_tokens=budget, window_tokens=window, window_assumed=assumed)
    if assumed:
        logger.info(
            "memory recall for %s: the model's context window is unknown, assuming %d tokens",
            agent, window,
        )
    if budget <= 0:
        base.status = "no_budget"
        base.detail = (
            f"the model's {window}-token window leaves no room for memory after the "
            f"{used_tokens} tokens already in the prompt"
        )
        logger.warning("memory recall for %s skipped: %s", agent, base.detail)
        return base

    try:
        result = await taosmd.answer_context(
            question,
            agent=agent,
            data_dir=data_dir,
            budget_tokens=budget,
            now=now or datetime.now(timezone.utc),
        )
    except Exception as exc:  # noqa: BLE001 - reported to the caller, never hidden
        logger.warning("memory recall for %s failed: %s: %s", agent, type(exc).__name__, exc, exc_info=True)
        base.status = "error"
        base.detail = f"taOSmd answer_context failed ({type(exc).__name__}: {exc})"
        return base

    base.context = result.get("context") or ""
    base.tokens_used = int(result.get("tokens_used") or 0)
    base.hits_total = int(result.get("hits_total") or 0)
    base.sessions_included = int(result.get("sessions_included") or 0)
    base.sessions_dropped = int(result.get("sessions_dropped") or 0)
    if base.context:
        if base.sessions_dropped:
            logger.info(
                "memory recall for %s: %d session(s) did not fit the %d-token budget",
                agent, base.sessions_dropped, budget,
            )
        return base

    if base.hits_total == 0:
        base.status = "no_hits"
        base.detail = "nothing in memory matched this question"
        logger.info("memory recall for %s: %s", agent, base.detail)
    elif base.sessions_dropped:
        base.status = "over_budget"
        base.detail = (
            f"{base.hits_total} memories matched but no session fit the {budget}-token budget"
        )
        logger.warning("memory recall for %s: %s", agent, base.detail)
    else:
        base.status = "no_blocks"
        base.detail = (
            f"{base.hits_total} matches were found but none is a conversation turn that can be shown"
        )
        logger.info("memory recall for %s: %s", agent, base.detail)
    return base
