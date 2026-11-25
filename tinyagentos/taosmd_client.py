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
from pathlib import Path
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


# Try to import the real taosmd API functions (available in taosmd >= 0.5.0 / master 6ae23ee6)
try:
    from taosmd import answer_context as _taosmd_answer_context
    from taosmd import default_budget_tokens as _taosmd_default_budget_tokens
    _TAOSMD_HAS_REAL_API = True
except ImportError:
    _TAOSMD_HAS_REAL_API = False
    _taosmd_answer_context = None
    _taosmd_default_budget_tokens = None


def default_budget_tokens(window: int, used_tokens: int, reasoning_reserve: int = 0) -> int:
    """Calculate the token budget for memory context.

    Uses taosmd.default_budget_tokens when available (taosmd >= 0.5.0),
    otherwise falls back to local implementation.

    Args:
        window: The model's total context window size.
        used_tokens: Tokens already used by system prompt, tools, and conversation history.
        reasoning_reserve: Tokens to reserve for model reasoning output (from model recipe).

    Returns:
        Token budget for memory context, floored at 512.
    """
    if _TAOSMD_HAS_REAL_API and _taosmd_default_budget_tokens is not None:
        return _taosmd_default_budget_tokens(window, used_tokens, reasoning_reserve=reasoning_reserve)

    # Local fallback implementation (matches taosmd.default_budget_tokens behavior)
    if window <= 0:
        return 4000  # Default when window unknown
    budget = window - used_tokens - reasoning_reserve
    return max(512, budget)


def estimate_reasoning_reserve(model_id: str, manifest: Any | None = None) -> int:
    """Estimate reasoning reserve for a model.

    Reasoning models (qwen3, o1, deepseek-r1, etc.) need extra tokens for
    chain-of-thought. Non-reasoning models get 0.

    Args:
        model_id: The model identifier.
        manifest: Optional model manifest for additional hints.

    Returns:
        Reasoning reserve in tokens.
    """
    model_lower = model_id.lower()
    # Known reasoning model families
    reasoning_families = [
        "qwen3", "o1", "deepseek-r1", "deepseekr1", "r1-",
        "nemotron", "reasoning", "think", "cot"
    ]
    for fam in reasoning_families:
        if fam in model_lower:
            # Check manifest for explicit reasoning reserve
            if manifest and hasattr(manifest, "reasoning_reserve"):
                return int(getattr(manifest, "reasoning_reserve", 0) or 0)
            return 4096  # Default reserve for reasoning models
    return 0


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
        data_dir: Path | str | None = None,
    ):
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        self._use_in_process = use_in_process
        self._context_assembler = None
        self._in_process_available = False
        self._data_dir = Path(data_dir) if data_dir else None

    def _ensure_in_process(self) -> bool:
        """Try to initialize in-process taOSmd ContextAssembler."""
        if self._in_process_available:
            return True
        if not self._use_in_process:
            return False
        try:
            from taosmd import ContextAssembler
            from taosmd import context_assembler as default_assembler

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
        data_dir: Path | str | None = None,
    ) -> AnswerContextResult:
        """Build memory context for an agent prompt using taOSmd's answer_context.

        Args:
            query: The user's query or current conversation turn text.
            agent: The canonical agent identity (slug).
            budget_tokens: Token budget for the context (share of model's context window).
            now: Current timestamp for recency scoring (defaults to UTC now).
            reasoning_reserve: Tokens to reserve for model reasoning output.
            data_dir: Root data directory for agent memory stores.

        Returns:
            AnswerContextResult with assembled context and metadata.
        """
        now = now or datetime.now(timezone.utc)
        data_dir = data_dir or self._data_dir

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
                    query, agent, budget_tokens, now, reasoning_reserve, data_dir
                )
            except Exception as e:
                logger.warning("In-process answer_context failed, falling back to HTTP: %s", e)

        # Fall back to HTTP API
        if self._http:
            try:
                return await self._answer_context_http(
                    query, agent, budget_tokens, now
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
        data_dir: Path | str | None = None,
    ) -> AnswerContextResult:
        """Call taOSmd answer_context in-process using agent-isolated stores.

        Uses the real taosmd.answer_context API when available (taosmd >= 0.5.0).
        Falls back to ContextAssembler.assemble for older versions.
        """
        start = time.time()

        # Use the real taosmd.answer_context API if available
        if _TAOSMD_HAS_REAL_API and _taosmd_answer_context is not None:
            try:
                result = await _taosmd_answer_context(
                    question=query,
                    budget_tokens=budget_tokens,
                    agent=agent,
                    data_dir=data_dir,
                    now=now,
                )
                latency_ms = (time.time() - start) * 1000

                return AnswerContextResult(
                    context=result.get("context", ""),
                    dropped=result.get("dropped", 0),
                    layers=result.get("layers", {}),
                    total_tokens=result.get("total_tokens", 0),
                    intent=result.get("intent", "general"),
                    latency_ms=latency_ms,
                )
            except Exception as e:
                logger.warning("taosmd.answer_context failed, falling back to ContextAssembler: %s", e)

        # Fallback: Use shared assembler if available (for backward compat / tests)
        # Otherwise create agent-isolated stores
        if self._context_assembler and not data_dir:
            assembler = self._context_assembler
        else:
            # Create agent-isolated stores
            from taosmd import Archive as ArchiveStore
            from taosmd import KnowledgeGraph as TemporalKnowledgeGraph
            from taosmd import SessionCatalog
            from taosmd.context_assembler import ContextAssembler

            agent_dir = Path(data_dir) / "agent-memory" / agent if data_dir else None

            # Initialize stores for this agent
            kg = None
            archive = None
            catalog = None
            if agent_dir:
                try:
                    kg = TemporalKnowledgeGraph(db_path=agent_dir / "knowledge-graph.db")
                    archive = ArchiveStore(db_path=agent_dir / "archive.db")
                    catalog = SessionCatalog(db_path=agent_dir / "session-catalog.db")
                except Exception as e:
                    logger.warning("Failed to initialize agent stores for %s: %s", agent, e)

            # Create ContextAssembler with agent stores
            assembler = ContextAssembler(
                kg=kg,
                archive=archive,
                catalog=catalog,
                qmd_base_url=self._base_url.replace("7900", "7832"),  # qmd port
                http_client=self._http,
            )

        # Call the ContextAssembler with the full budget.
        # The ContextAssembler does not have a reasoning_reserve parameter;
        # reasoning_reserve is accounted for in the budget calculation via default_budget_tokens.
        result = await assembler.assemble(
            query=query,
            agent_name=agent,
            max_total_tokens=budget_tokens,
            depth="standard",
        )

        latency_ms = (time.time() - start) * 1000

        # Get dropped count from ContextAssembler result if available.
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
    ) -> AnswerContextResult:
        """Call taOSmd HTTP API for answer_context.

        Note: reasoning_reserve is NOT sent over HTTP (server rejects it with 400).
        It should only be passed to default_budget_tokens for budget calculation.
        """
        start = time.time()

        payload = {
            "query": query,
            "agent": agent,
            "budget_tokens": budget_tokens,
            "now": now.isoformat(),
            # reasoning_reserve is intentionally omitted - server returns 400
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


def _get_app_state(obj) -> Any:
    """Extract app.state from a Request or return the object if it's already app.state."""
    # Handle FastAPI Request objects (have .app.state)
    if hasattr(obj, "app") and hasattr(obj.app, "state"):
        return obj.app.state
    # Handle objects that are already app.state or have .state directly
    if hasattr(obj, "state"):
        return obj.state
    # Assume it's already the state object
    return obj


def get_taosmd_client(request) -> TaOSmdClient:
    """Get a TaOSmdClient configured for the current request context.

    Uses the request's http_client and taOSmd URL configuration.
    Accepts either a FastAPI Request, app.state, or an object with .state.
    """
    from tinyagentos.routes.user_memory import _taosmd_base

    state = _get_app_state(request)
    http_client = getattr(state, "http_client", None)
    base_url = _taosmd_base(state)  # Pass state directly, _taosmd_base handles it

    # Get data_dir from state if available
    data_dir = getattr(state, "data_dir", None)

    return TaOSmdClient(http_client=http_client, base_url=base_url, data_dir=data_dir)


def calculate_agent_budget(agent_config: dict, registry, used_tokens: int = 0) -> int:
    """Calculate the token budget for an agent's memory context.

    Derives budget from the agent's configured model context window,
    using default_budget_tokens with measured used_tokens and reasoning_reserve.

    Args:
        agent_config: The agent's configuration dict.
        registry: Model registry for manifest lookup.
        used_tokens: Measured tokens of system prompt + tools + conversation history.

    Returns:
        Token budget for memory context.
    """
    from tinyagentos.cluster.model_resolver import _find_model_manifest

    model_id = agent_config.get("model") or ""
    if not model_id:
        return 4000  # DEFAULT_HISTORY_MAX_TOKENS

    try:
        manifest = _find_model_manifest(registry, model_id)
        ctx = int(getattr(manifest, "context_window", 0) or 0) if manifest else 0
        if ctx > 0:
            reasoning_reserve = estimate_reasoning_reserve(model_id, manifest)
            # If used_tokens not provided, estimate from system prompt reserve
            if used_tokens <= 0:
                from tinyagentos.chat.context_window import SYSTEM_PROMPT_RESERVE
                used_tokens = SYSTEM_PROMPT_RESERVE
            return default_budget_tokens(ctx, used_tokens, reasoning_reserve)
    except Exception as e:
        logger.warning("Failed to calculate agent budget for %s: %s", model_id, e)

    return 4000  # DEFAULT_HISTORY_MAX_TOKENS