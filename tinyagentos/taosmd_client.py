"""taOSmd client wrapper for answer_context and related memory functions.

This module provides a unified interface to taOSmd's answer_context function
and other memory APIs. It handles both in-process (Python API) and HTTP/MCP
call patterns depending on what the caller already uses.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AnswerContextResult:
    """Result from answer_context call."""
    context: str
    """The assembled context string for the agent prompt."""
    dropped: int
    """Number of memory items dropped due to token budget."""
    layers: dict[str, int]
    """Token counts per memory layer (l0, l1, l2, l3)."""
    total_tokens: int
    """Total tokens in the assembled context."""
    intent: str
    """Classified intent of the query."""
    latency_ms: float
    """Time taken to assemble context in milliseconds."""


class TaOSmdClient:
    """Client for taOSmd memory operations.

    Uses in-process Python API when taOSmd is available locally, falls back
    to HTTP calls when running against a remote taOSmd service.
    """

    def __init__(
        self,
        http_client=None,
        base_url: str = "http://localhost:7900",
        use_in_process: bool = True,
    ):
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        self._use_in_process = use_in_process
        self._context_assembler = None
        self._in_process_available = False

    def _ensure_in_process(self) -> bool:
        """Try to initialize in-process taOSmd ContextAssembler."""
        if self._in_process_available:
            return True
        if not self._use_in_process:
            return False
        try:
            from taosmd import ContextAssembler, context_assembler as default_assembler
            from taosmd import Archive as ArchiveStore
            from taosmd import KnowledgeGraph as TemporalKnowledgeGraph
            from taosmd import SessionCatalog

            # Use the default singleton if available, else create our own
            self._context_assembler = default_assembler or ContextAssembler()
            self._in_process_available = True
            logger.debug("taOSmd in-process client initialized")
            return True
        except ImportError:
            logger.debug("taOSmd not available for in-process calls")
            return False
        except Exception as e:
            # Log as error (not warning) so config issues are visible.
            # Re-raise to avoid silently falling back to HTTP with a broken in-process client.
            logger.error("Failed to initialize taOSmd in-process client: %s", e)
            raise

    async def answer_context(
        self,
        query: str,
        agent: str,
        budget_tokens: int,
        now: datetime | None = None,
        reasoning_reserve: int = 1024,
    ) -> AnswerContextResult:
        """Build memory context for an agent prompt using taOSmd's answer_context.

        Args:
            query: The user's query or current conversation turn text.
            agent: The canonical agent identity (slug).
            budget_tokens: Token budget for the context (share of model's context window).
            now: Current timestamp for recency scoring (defaults to UTC now).
            reasoning_reserve: Tokens to reserve for model reasoning output.

        Returns:
            AnswerContextResult with assembled context and metadata.
        """
        now = now or datetime.now(timezone.utc)

        # Try in-process first
        in_process_ok = False
        try:
            in_process_ok = self._ensure_in_process() and self._context_assembler
        except Exception as e:
            # Config error in in-process init - log and fall back to HTTP
            logger.error("taOSmd in-process init failed, falling back to HTTP: %s", e)

        if in_process_ok:
            try:
                return await self._answer_context_in_process(
                    query, agent, budget_tokens, now, reasoning_reserve
                )
            except Exception as e:
                logger.warning("In-process answer_context failed, falling back to HTTP: %s", e)

        # Fall back to HTTP API
        if self._http:
            try:
                return await self._answer_context_http(
                    query, agent, budget_tokens, now, reasoning_reserve
                )
            except Exception as e:
                logger.error("HTTP answer_context failed: %s", e)

        # Final fallback: return empty context with dropped=0
        logger.warning("taOSmd answer_context unavailable, returning empty context")
        return AnswerContextResult(
            context="",
            dropped=0,
            layers={},
            total_tokens=0,
            intent="unknown",
            latency_ms=0.0,
        )

    async def _answer_context_in_process(
        self,
        query: str,
        agent: str,
        budget_tokens: int,
        now: datetime,
        reasoning_reserve: int,
    ) -> AnswerContextResult:
        """Call taOSmd ContextAssembler.assemble in-process."""
        start = time.time()

        # Call the ContextAssembler with the full budget.
        # The ContextAssembler handles reasoning_reserve internally (same as HTTP path).
        result = await self._context_assembler.assemble(
            query=query,
            agent_name=agent,
            max_total_tokens=budget_tokens,
            depth="standard",
            reasoning_reserve=reasoning_reserve,
        )

        latency_ms = (time.time() - start) * 1000

        # Get dropped count from ContextAssembler result if available.
        # The ContextAssembler returns the actual number of items dropped due to budget.
        dropped = result.get("dropped", 0)

        return AnswerContextResult(
            context=result.get("context", ""),
            dropped=dropped,
            layers=result.get("layers", {}),
            total_tokens=result.get("total_tokens", 0),
            intent=result.get("intent", "general"),
            latency_ms=latency_ms,
        )

    async def _answer_context_http(
        self,
        query: str,
        agent: str,
        budget_tokens: int,
        now: datetime,
        reasoning_reserve: int,
    ) -> AnswerContextResult:
        """Call taOSmd HTTP API for answer_context."""
        start = time.time()

        payload = {
            "query": query,
            "agent": agent,
            "budget_tokens": budget_tokens,
            "now": now.isoformat(),
            "reasoning_reserve": reasoning_reserve,
        }

        resp = await self._http.post(
            f"{self._base_url}/answer_context",
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()

        latency_ms = (time.time() - start) * 1000

        return AnswerContextResult(
            context=data.get("context", ""),
            dropped=data.get("dropped", 0),
            layers=data.get("layers", {}),
            total_tokens=data.get("total_tokens", 0),
            intent=data.get("intent", "general"),
            latency_ms=latency_ms,
        )

    async def search_memory(
        self,
        query: str,
        agent: str,
        limit: int = 10,
        collection: str | None = None,
    ) -> list[dict]:
        """Search agent memory (raw search, not for agent prompts).

        This is for the Memory app, CLI search, and other non-prompt uses.
        """
        if self._ensure_in_process():
            # In-process search would go through taOSmd's search APIs
            pass

        if self._http:
            params = {"q": query, "agent": agent, "limit": limit}
            if collection:
                params["collection"] = collection
            resp = await self._http.get(
                f"{self._base_url}/search",
                params=params,
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("hits", data.get("results", []))

        return []


def get_taosmd_client(request) -> TaOSmdClient:
    """Get a TaOSmdClient configured for the current request context.

    Uses the request's http_client and taOSmd URL configuration.
    """
    from tinyagentos.routes.user_memory import _taosmd_base

    http_client = getattr(request.app.state, "http_client", None)
    base_url = _taosmd_base(request)

    return TaOSmdClient(http_client=http_client, base_url=base_url)


def calculate_agent_budget(agent_config: dict, registry) -> int:
    """Calculate the token budget for an agent's memory context.

    Derives budget from the agent's configured model context window,
    using a share (not the whole window) per the taOSmd spec.

    Args:
        agent_config: The agent's configuration dict.
        registry: Model registry for manifest lookup.

    Returns:
        Token budget for memory context.
    """
    from tinyagentos.chat.context_window import (
        DEFAULT_HISTORY_MAX_TOKENS,
        SYSTEM_PROMPT_RESERVE,
        RESPONSE_RESERVE,
        MIN_HISTORY_MAX_TOKENS,
        history_token_budget,
    )
    from tinyagentos.cluster.model_resolver import _find_model_manifest

    model_id = agent_config.get("model") or ""
    if not model_id:
        return DEFAULT_HISTORY_MAX_TOKENS

    try:
        manifest = _find_model_manifest(registry, model_id)
        ctx = int(getattr(manifest, "context_window", 0) or 0) if manifest else 0
        if ctx > 0:
            # Use a share of the window for memory (not the whole window)
            # history_token_budget already reserves for system prompt + response
            return history_token_budget(ctx)
    except Exception:
        pass

    return DEFAULT_HISTORY_MAX_TOKENS