"""taOSmd client — in-process Python API and HTTP client for answer_context.

Provides a unified interface for calling taOSmd's answer_context function,
which builds the memory context an agent sees: dated session blocks,
oldest first, original turn order, token budget, nothing cut mid-item,
dropped items reported.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class AnswerContextResult:
    """Result from answer_context call."""

    context: str
    """The assembled context string with dated session blocks."""

    dropped: int
    """Number of items dropped due to token budget."""

    budget_tokens: int
    """Token budget that was allocated."""

    used_tokens: int
    """Tokens actually used in the response."""

    layers: dict[str, int]
    """Token counts per layer (l0, l1, l2, l3)."""

    depth: str
    """Depth used (minimal, standard, deep, auto)."""

    intent: str
    """Classified intent of the query."""

    latency_ms: float
    """Time taken to assemble context."""


class TaOSmdClient:
    """Client for calling taOSmd's answer_context API.

    Supports both in-process (when taOSmd is importable) and HTTP
    (when taOSmd runs as a separate service on port 8085) modes.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = "http://localhost:8085",
        in_process: bool = False,
    ) -> None:
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        self._in_process = in_process
        self._context_assembler = None

        if in_process:
            try:
                from taosmd.context_assembler import ContextAssembler

                # In-process mode requires taOSmd stores to be initialized
                # This is a placeholder - actual initialization happens in app.py
                self._context_assembler = ContextAssembler()
            except ImportError:
                logger.warning("taOSmd not available for in-process mode, falling back to HTTP")
                self._in_process = False

    async def answer_context(
        self,
        query: str,
        agent: str,
        budget_tokens: int,
        now: float | None = None,
        reasoning_reserve: int = 1024,
        depth: str = "standard",
        user_name: str | None = None,
        project: str | None = None,
        system_info: dict | None = None,
    ) -> AnswerContextResult:
        """Call taOSmd's answer_context to build agent memory context.

        Args:
            query: The search query / current user message.
            agent: The agent's canonical name/identity.
            budget_tokens: Token budget (share of model's context window).
            now: Current timestamp (defaults to time.time()).
            reasoning_reserve: Tokens to reserve for model reasoning.
            depth: Retrieval depth (minimal, standard, deep, auto).
            user_name: Optional user name for L0 identity.
            project: Optional project fingerprint for scoped search.
            system_info: Optional hardware info for L0.

        Returns:
            AnswerContextResult with context, dropped count, and metadata.
        """
        if now is None:
            now = time.time()

        if self._in_process and self._context_assembler:
            return await self._answer_context_in_process(
                query=query,
                agent=agent,
                budget_tokens=budget_tokens,
                now=now,
                reasoning_reserve=reasoning_reserve,
                depth=depth,
                user_name=user_name,
                project=project,
                system_info=system_info,
            )

        return await self._answer_context_http(
            query=query,
            agent=agent,
            budget_tokens=budget_tokens,
            now=now,
            reasoning_reserve=reasoning_reserve,
            depth=depth,
            user_name=user_name,
            project=project,
            system_info=system_info,
        )

    async def _answer_context_in_process(
        self,
        query: str,
        agent: str,
        budget_tokens: int,
        now: float,
        reasoning_reserve: int,
        depth: str,
        user_name: str | None,
        project: str | None,
        system_info: dict | None,
    ) -> AnswerContextResult:
        """Call answer_context via in-process taOSmd ContextAssembler."""
        try:
            # Reserve tokens for reasoning from the budget
            effective_budget = max(512, budget_tokens - reasoning_reserve)

            result = await self._context_assembler.assemble(
                query=query,
                agent_name=agent,
                user_name=user_name,
                project=project,
                system_info=system_info,
                depth=depth,
                max_total_tokens=effective_budget,
            )

            return AnswerContextResult(
                context=result["context"],
                dropped=0,  # In-process assembler doesn't report dropped yet
                budget_tokens=budget_tokens,
                used_tokens=result.get("total_tokens", 0),
                layers=result.get("layers", {}),
                depth=result.get("depth", depth),
                intent=result.get("intent", "general"),
                latency_ms=result.get("latency_ms", 0.0),
            )
        except Exception:
            logger.exception("In-process answer_context failed for agent %s", agent)
            return AnswerContextResult(
                context="",
                dropped=0,
                budget_tokens=budget_tokens,
                used_tokens=0,
                layers={},
                depth=depth,
                intent="error",
                latency_ms=0.0,
            )

    async def _answer_context_http(
        self,
        query: str,
        agent: str,
        budget_tokens: int,
        now: float,
        reasoning_reserve: int,
        depth: str,
        user_name: str | None,
        project: str | None,
        system_info: dict | None,
    ) -> AnswerContextResult:
        """Call answer_context via HTTP to taOSmd service."""
        if self._http is None:
            logger.warning("No HTTP client available for taOSmd answer_context")
            return AnswerContextResult(
                context="",
                dropped=0,
                budget_tokens=budget_tokens,
                used_tokens=0,
                layers={},
                depth=depth,
                intent="error",
                latency_ms=0.0,
            )

        try:
            payload = {
                "query": query,
                "agent": agent,
                "budget_tokens": budget_tokens,
                "now": now,
                "reasoning_reserve": reasoning_reserve,
                "depth": depth,
            }
            if user_name:
                payload["user_name"] = user_name
            if project:
                payload["project"] = project
            if system_info:
                payload["system_info"] = system_info

            resp = await self._http.post(
                f"{self._base_url}/answer_context",
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

            return AnswerContextResult(
                context=data.get("context", ""),
                dropped=data.get("dropped", 0),
                budget_tokens=data.get("budget_tokens", budget_tokens),
                used_tokens=data.get("used_tokens", 0),
                layers=data.get("layers", {}),
                depth=data.get("depth", depth),
                intent=data.get("intent", "general"),
                latency_ms=data.get("latency_ms", 0.0),
            )
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "taOSmd answer_context HTTP error for agent %s: %s",
                agent,
                exc.response.status_code,
            )
            return AnswerContextResult(
                context="",
                dropped=0,
                budget_tokens=budget_tokens,
                used_tokens=0,
                layers={},
                depth=depth,
                intent="error",
                latency_ms=0.0,
            )
        except Exception:
            logger.exception("taOSmd answer_context failed for agent %s", agent)
            return AnswerContextResult(
                context="",
                dropped=0,
                budget_tokens=budget_tokens,
                used_tokens=0,
                layers={},
                depth=depth,
                intent="error",
                latency_ms=0.0,
            )

    async def close(self) -> None:
        """Close the HTTP client if we own it."""
        if self._http is not None:
            await self._http.aclose()


def get_taosmd_client(request) -> TaOSmdClient:
    """Get or create a TaOSmdClient from app state.

    The client is created lazily and cached on app.state.
    """
    if not hasattr(request.app.state, "taosmd_client"):
        http_client = getattr(request.app.state, "http_client", None)
        taosmd_url = getattr(request.app.state, "taosmd_url", None) or "http://localhost:8085"
        in_process = getattr(request.app.state, "taosmd_in_process", False)

        request.app.state.taosmd_client = TaOSmdClient(
            http_client=http_client,
            base_url=taosmd_url,
            in_process=in_process,
        )
    return request.app.state.taosmd_client


async def answer_context(
    query: str,
    agent: str,
    budget_tokens: int,
    now: float | None = None,
    reasoning_reserve: int = 1024,
    depth: str = "standard",
    user_name: str | None = None,
    project: str | None = None,
    system_info: dict | None = None,
    *,
    request=None,
    client: TaOSmdClient | None = None,
) -> AnswerContextResult:
    """Convenience function to call answer_context.

    Can be called with either a request (to get client from app.state)
    or an explicit client instance.
    """
    if client is None:
        if request is None:
            raise ValueError("Either request or client must be provided")
        client = get_taosmd_client(request)

    return await client.answer_context(
        query=query,
        agent=agent,
        budget_tokens=budget_tokens,
        now=now,
        reasoning_reserve=reasoning_reserve,
        depth=depth,
        user_name=user_name,
        project=project,
        system_info=system_info,
    )