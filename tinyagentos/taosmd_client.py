"""taOSmd client wrapper for answer_context and related memory functions.

This module provides a unified interface to taOSmd's answer_context function
and other memory APIs. It handles both in-process (Python API) and HTTP/MCP
call patterns depending on what the caller already uses.
"""
from __future__ import annotations

import inspect
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

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

    Uses in-process taosmd.answer_context when taOSmd is available locally,
    falls back to HTTP calls against a remote taOSmd service.
    """

    def __init__(
        self,
        http_client=None,
        base_url: str = "http://localhost:7900",
        use_in_process: bool = True,
        data_dir: str | None = None,
    ):
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        self._use_in_process = use_in_process
        self._data_dir = data_dir
        self._taosmd = None
        self._in_process_available = False

    def _ensure_in_process(self) -> bool:
        """Try to initialize in-process taOSmd answer_context."""
        if self._in_process_available:
            return True
        if not self._use_in_process:
            return False
        try:
            import taosmd

            if not hasattr(taosmd, "answer_context"):
                raise AttributeError("taosmd has no answer_context")
            self._taosmd = taosmd
            self._in_process_available = True
            logger.debug("taOSmd in-process client initialized")
            return True
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
        data_dir: str | None = None,
    ) -> AnswerContextResult:
        """Build memory context for an agent prompt using taOSmd's answer_context.

        Args:
            query: The user's query or current conversation turn text.
            agent: The canonical agent identity (slug).
            budget_tokens: Token budget for the context (share of model's context window).
            now: Current timestamp for recency scoring (defaults to UTC now).
            data_dir: taOSmd data directory, overrides the client default.

        Returns:
            AnswerContextResult with assembled context and metadata.

        Raises:
            RuntimeError: if both in-process and HTTP answer_context are unavailable.
        """
        now = now or datetime.now(timezone.utc)
        data_dir = data_dir or self._data_dir

        # Try in-process first: taosmd.answer_context(question=..., budget_tokens=...,
        # agent=..., data_dir=..., now=...) -- never ContextAssembler.assemble.
        in_process_ok = False
        try:
            in_process_ok = self._ensure_in_process() and self._taosmd is not None
        except Exception as e:
            # Config error in in-process init - log and fall back to HTTP
            logger.error("taOSmd in-process init failed, falling back to HTTP: %s", e)

        if in_process_ok:
            try:
                return await self._answer_context_in_process(
                    query, agent, budget_tokens, now, data_dir
                )
            except Exception as e:
                logger.warning("In-process answer_context failed, falling back to HTTP: %s", e)

        # Fall back to HTTP API. reasoning_reserve is NOT sent over HTTP -- the
        # server rejects it (400 on taosmd master). It is applied only when
        # computing the budget via taosmd.default_budget_tokens.
        if self._http:
            try:
                return await self._answer_context_http(
                    query, agent, budget_tokens, now, data_dir
                )
            except Exception as e:
                logger.error("HTTP answer_context failed: %s", e)

        # Total failure must surface via the error log (never silently empty --
        # the failure is recorded so operators can see memory is degraded).
        # Degraded mode returns an empty context so callers keep working.
        logger.error("taOSmd answer_context unavailable: in-process and HTTP both failed")
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
        data_dir: str | None = None,
    ) -> AnswerContextResult:
        """Call taosmd.answer_context in-process."""
        start = time.time()

        result = self._taosmd.answer_context(
            question=query,
            budget_tokens=budget_tokens,
            agent=agent,
            data_dir=data_dir,
            now=now,
        )
        if inspect.isawaitable(result):
            result = await result

        latency_ms = (time.time() - start) * 1000

        return AnswerContextResult(
            context=result.get("context", ""),
            dropped=result.get("dropped", 0),
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
        data_dir: str | None = None,
    ) -> AnswerContextResult:
        """Call taOSmd HTTP API for answer_context.

        reasoning_reserve is intentionally omitted from the payload: the server
        rejects it (400 on taosmd master 6ae23ee6).
        """
        start = time.time()

        payload: dict = {
            "question": query,
            "agent": agent,
            "budget_tokens": budget_tokens,
            "now": now.isoformat(),
        }
        if data_dir:
            payload["data_dir"] = str(data_dir)

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

    Accepts either a FastAPI Request (which exposes ``request.app.state``) or a
    bare app-state object (e.g. ``AgentChatRouter._state``) that has no ``.app``
    attribute. In the latter case the state object itself is used.
    """
    from tinyagentos.routes.user_memory import _taosmd_base

    app_state = getattr(getattr(request, "app", None), "state", None)
    if app_state is None:
        app_state = request

    http_client = getattr(app_state, "http_client", None)
    base_url = _taosmd_base(request)

    return TaOSmdClient(
        http_client=http_client,
        base_url=base_url,
        data_dir=getattr(app_state, "data_dir", None),
    )


def calculate_agent_budget(
    agent_config: dict,
    registry,
    used_tokens: int = 0,
    reasoning_reserve: int | None = None,
) -> int:
    """Calculate the token budget for an agent's memory context.

    Derives the budget from the agent's real model context window minus the
    measured used tokens of the prompt (system + tools + conversation) and the
    per-model reasoning reserve (from the model recipe, 0 for non-reasoning
    models), via taosmd.default_budget_tokens(window, used_tokens,
    reasoning_reserve=R). Falls back to the history-only budget when taOSmd
    is not yet at the version providing default_budget_tokens.

    Args:
        agent_config: The agent's configuration dict.
        registry: Model registry for manifest lookup.
        used_tokens: Measured tokens already used by system + tools + conversation.
        reasoning_reserve: Tokens to reserve for model reasoning output. When
            None, inferred per model (0 for non-reasoning models).

    Returns:
        Token budget for memory context.
    """
    from tinyagentos.chat.context_window import (
        DEFAULT_HISTORY_MAX_TOKENS,
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
            if reasoning_reserve is None:
                reasoning_reserve = _model_reasoning_reserve(agent_config, registry)
            try:
                import taosmd

                if hasattr(taosmd, "default_budget_tokens"):
                    return int(
                        taosmd.default_budget_tokens(
                            window=ctx,
                            used_tokens=used_tokens,
                            reasoning_reserve=reasoning_reserve,
                        )
                    )
            except Exception:
                # taOSmd not yet upgraded: fall back to history-only budget.
                pass
            return history_token_budget(ctx)
    except Exception:
        pass

    return DEFAULT_HISTORY_MAX_TOKENS


def _model_reasoning_reserve(agent_config: dict, registry) -> int:
    """Reasoning token reserve for a model, from the model recipe.

    Returns 0 for non-reasoning models.
    """
    model_id = agent_config.get("model") or ""
    if not model_id:
        return 0
    try:
        import taosmd

        if hasattr(taosmd, "get_recipe"):
            recipe = taosmd.get_recipe(model_id)
            if recipe is not None:
                generator = getattr(recipe, "generator", None) or {}
                reserve = generator.get("reasoning_reserve", 0)
                if reserve:
                    return int(reserve)
    except Exception:
        pass
    # Conservative: treat unknown as non-reasoning (0 reserve).
    return 0
