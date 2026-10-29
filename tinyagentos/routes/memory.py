"""Memory routes — per-agent and user memory over the shared qmd serve.

Every memory operation is an HTTP call to the host ``qmd.service``
process on :7832. That process exposes ``/search``, ``/vsearch``,
``/browse``, ``/collections``, ``/ingest``, and ``/delete-chunk`` and
each call accepts an optional ``dbPath`` that selects which SQLite
file to operate on. TinyAgentOS resolves the ``dbPath`` based on the
calling scope:

- ``agent=foo``  → ``data/agent-memory/foo/index.sqlite``
- no agent       → a dedicated taOS user index
  (``<data>/user-qmd-index/index.sqlite``), never qmd's shared default

This is the load-bearing piece of per-agent memory isolation — each
agent reads and writes its own index, so Agent A cannot see Agent B's
memory and Agent A's deletions cannot trample anyone else's data. See
``docs/design/framework-agnostic-runtime.md``.

§4.4 trace-context propagation: all outbound memory calls inject W3C
``traceparent`` and ``X-TaOS-Conversation-Id`` headers so the memory
backend (qmd / taosmd) can nest its spans under the caller's OTel trace.
The headers are built by ``build_trace_context_headers()``.
"""
from __future__ import annotations

import logging
from datetime import datetime, UTC
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from tinyagentos.agent_db import find_agent
from tinyagentos.agent_token_auth import check_agent_scope
from tinyagentos.auth_context import current_user, require_agent_owner_or_admin
from tinyagentos.otel.trace_context import build_trace_context_headers
from tinyagentos.routes.delegation import _resolve_agent_name

logger = logging.getLogger(__name__)

router = APIRouter()


class RememberRequest(BaseModel):
    text: str
    agent: str | None = None
    title: str | None = None

    @field_validator("text")
    @classmethod
    def _validate_text(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("text must not be empty after stripping")
        if len(stripped) > 8000:
            raise ValueError("text must be at most 8000 characters after stripping")
        return stripped


def _local_token_caller(request: Request) -> tuple[bool, str | None]:
    """Check if the request is from a bound local token.

    Returns (True, agent_name) when request.state.via == "local_token" and
    request.state.agent_name is set (bound agent token).
    Returns (True, None) when request.state.via == "local_token" but no
    agent_name is bound (host/unbound token).
    Returns (False, None) otherwise.
    """
    if getattr(request.state, "via", None) == "local_token":
        return True, getattr(request.state, "agent_name", None)
    return False, None


class SearchRequest(BaseModel):
    query: str
    mode: str = "keyword"
    agent: str | None = None
    collection: str | None = None
    limit: int = 20
    # Optional W3C trace context — callers (adapters, librarian) pass the
    # active conversation_id so the memory backend can nest its spans.
    conversation_id: str | None = None


def _qmd_base(request: Request) -> str:
    """URL of the shared host qmd serve process."""
    return request.app.state.qmd_client.base_url


def _agent_db_path(request: Request, agent: str | None) -> str:
    """Resolve the SQLite path for a memory index. ALWAYS taOS-owned.

    - ``agent`` given → that agent's per-agent index
      (``agent_memory_dir/<agent>/index.sqlite``), matching what the deployer
      bind-mounts into the agent's container at ``/memory``.
    - no ``agent`` (user/default scope) → a DEDICATED taOS user index, never
      qmd's *shared default*. The qmd serve is shared across frameworks and its
      default collection may belong to another one (e.g. openclaw's workspace),
      so omitting ``dbPath`` would query/return that foreign data. We therefore
      always pin an explicit taOS-owned ``dbPath`` (an empty index returns no
      results, which is correct — never another framework's data).

    Security: ``agent`` is caller-controlled and becomes a filesystem path
    component, so it must be a SINGLE plain component. Multi-segment values
    are rejected outright rather than sanitized: no legitimate agent name
    contains a separator, and accepting ``a/b`` would only widen the surface.
    """
    base: Path = request.app.state.agent_memory_dir
    if not agent:
        target = base.parent / "user-qmd-index" / "index.sqlite"
    else:
        agent = agent.strip()
        if (
            not agent
            or agent in (".", "..")
            or "/" in agent
            or "\\" in agent
            or "\x00" in agent
        ):
            raise HTTPException(status_code=400, detail="invalid agent name")
        target = base / agent / "index.sqlite"
        # Belt for anything the component check cannot see (e.g. a symlinked
        # entry under agent_memory_dir): the resolved path must stay inside it.
        if not target.resolve().is_relative_to(base.resolve()):
            raise HTTPException(status_code=400, detail="invalid agent name")
    target.parent.mkdir(parents=True, exist_ok=True)
    return str(target)


@router.get("/api/memory/browse")
async def memory_browse(
    request: Request,
    agent: str | None = None,
    collection: str | None = None,
    limit: int = 20,
    offset: int = 0,
    conversation_id: str | None = None,
):
    """Browse memory chunks via qmd serve GET /browse."""
    # Check for local token FIRST (before check_agent_scope which treats any Bearer as registry JWT)
    is_local, bound_agent = _local_token_caller(request)
    if is_local:
        if bound_agent is not None:
            # Bound agent: fence to own namespace
            if agent is not None and agent != bound_agent:
                raise HTTPException(status_code=403, detail="Agent can only browse its own memory")
            agent = bound_agent
        # Unbound (host) token: keep existing admin behaviour (agent param respected as-is)
    else:
        # Registry JWT path
        agent_canonical_id = await check_agent_scope(request, "memory_read")
        
        # For registry-JWT agents, restrict to own namespace
        if agent_canonical_id is not None:
            # If an agent name was provided in the query, ensure it matches the calling agent's own
            # First resolve the canonical_id to the config agent name
            agent_config_name = await _resolve_agent_name(request, agent_canonical_id) or agent_canonical_id
            
            if agent is not None and agent != agent_config_name:
                raise HTTPException(status_code=403, detail="Agent can only browse its own memory")
            # Use the resolved config agent name for the agent namespace
            agent = agent_config_name
    
    http_client = request.app.state.http_client
    params: dict = {"limit": limit, "offset": offset}
    if collection:
        params["collection"] = collection
    params["dbPath"] = _agent_db_path(request, agent)
    headers = build_trace_context_headers(conversation_id=conversation_id)
    try:
        resp = await http_client.get(f"{_qmd_base(request)}/browse", params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        chunks = data.get("chunks", [])
        if agent:
            for c in chunks:
                c["agent"] = agent
        return {"chunks": chunks}
    except Exception as e:
        logger.warning("qmd /browse failed: %s", e)
        return JSONResponse({"chunks": [], "error": str(e)}, status_code=502)


async def _qmd_search(request: Request, query: str,
                      collection: str | None, limit: int,
                      db_path: str | None,
                      conversation_id: str | None = None) -> list[dict]:
    """Keyword (BM25) search via qmd serve GET /search."""
    http_client = request.app.state.http_client
    params: dict = {"q": query, "limit": limit}
    if collection:
        params["collection"] = collection
    if db_path:
        params["dbPath"] = db_path
    headers = build_trace_context_headers(conversation_id=conversation_id)
    resp = await http_client.get(f"{_qmd_base(request)}/search", params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])


async def _qmd_vsearch(request: Request, query: str,
                       collection: str | None, limit: int,
                       db_path: str | None,
                       conversation_id: str | None = None) -> list[dict]:
    """Semantic (vector) search via qmd serve POST /vsearch.

    The query is embedded inside the qmd serve process using whichever
    backend that process is configured with (rkllama on NPU in our
    deployment).
    """
    http_client = request.app.state.http_client
    payload: dict = {"query": query, "limit": limit}
    if collection:
        payload["collection"] = collection
    if db_path:
        payload["dbPath"] = db_path
    headers = build_trace_context_headers(conversation_id=conversation_id)
    resp = await http_client.post(f"{_qmd_base(request)}/vsearch", json=payload, headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])


@router.post("/api/memory/search")
async def memory_search(request: Request, body: SearchRequest):
    """Search memory using keyword or semantic search.

    If ``agent`` is set, searches that agent's per-agent index. Without
    an ``agent``, searches the default (user) index. Cross-agent search
    is intentionally not supported here -- agents do not share memory.
    Aggregating across agents is a separate concern that belongs in a
    future ``/api/memory/all`` endpoint, gated by user permission.
    """
    # Check for local token FIRST (before check_agent_scope which treats any Bearer as registry JWT)
    is_local, bound_agent = _local_token_caller(request)
    if is_local:
        if bound_agent is not None:
            # Bound agent: fence to own namespace
            if body.agent is not None and body.agent != bound_agent:
                raise HTTPException(status_code=403, detail="Agent can only search its own memory")
            body.agent = bound_agent
        # Unbound (host) token: keep existing admin behaviour (body.agent respected as-is)
    else:
        # Registry JWT path
        agent_canonical_id = await check_agent_scope(request, "memory_read")
        
        # For registry-JWT agents, restrict to own namespace
        if agent_canonical_id is not None:
            # If an agent name was provided in the body, ensure it matches the calling agent's own
            # First resolve the canonical_id to the config agent name
            agent_config_name = await _resolve_agent_name(request, agent_canonical_id) or agent_canonical_id
            
            if body.agent is not None and body.agent != agent_config_name:
                raise HTTPException(status_code=403, detail="Agent can only search its own memory")
            # Use the resolved config agent name for the agent namespace
            body.agent = agent_config_name
    
    db_path = _agent_db_path(request, body.agent)
    search_fn = _qmd_vsearch if body.mode == "semantic" else _qmd_search

    try:
        results = await search_fn(
            request, body.query, body.collection, body.limit, db_path,
            conversation_id=body.conversation_id,
        )
    except Exception as exc:
        logger.warning("qmd %s failed: %s", body.mode, exc)
        return JSONResponse({"results": [], "error": str(exc)}, status_code=502)

    if body.agent:
        for r in results:
            r["agent"] = body.agent
    return {"results": results}


@router.get("/api/memory/collections/{agent_name}")
async def memory_collections(
    request: Request, agent_name: str, conversation_id: str | None = None,
):
    """List memory collections for an agent via qmd serve GET /collections."""
    # Check for local token FIRST (before check_agent_scope which treats any Bearer as registry JWT)
    is_local, bound_agent = _local_token_caller(request)
    if is_local:
        if bound_agent is not None:
            # Bound agent: fence to own namespace
            if agent_name != bound_agent:
                raise HTTPException(status_code=403, detail="Agent can only access its own collections")
            agent_name = bound_agent
        # Unbound (host) token: keep existing admin behaviour (agent_name path param respected as-is)
    else:
        # Registry JWT path
        agent_canonical_id = await check_agent_scope(request, "memory_read")
        
        # For registry-JWT agents, restrict to own namespace
        if agent_canonical_id is not None:
            # First resolve the canonical_id to the config agent name
            agent_config_name = await _resolve_agent_name(request, agent_canonical_id) or agent_canonical_id
            
            # Ensure the agent is requesting collections for itself only
            if agent_name != agent_config_name:
                raise HTTPException(status_code=403, detail="Agent can only access its own collections")
            # Use the resolved config agent name
            agent_name = agent_config_name
    
    http_client = request.app.state.http_client
    params: dict = {"dbPath": _agent_db_path(request, agent_name)}
    headers = build_trace_context_headers(conversation_id=conversation_id)
    try:
        resp = await http_client.get(f"{_qmd_base(request)}/collections", params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("qmd /collections failed: %s", e)
        return JSONResponse([], status_code=200)


@router.delete("/api/memory/chunk/{content_hash}")
async def memory_delete_chunk(
    request: Request, content_hash: str, agent: str | None = None,
    conversation_id: str | None = None,
):
    """Delete a chunk by hash via qmd serve POST /delete-chunk.

    Routes to the per-agent index when ``agent`` is set, otherwise to
    the default user index.
    """
    # Check for local token FIRST (before check_agent_scope which treats any Bearer as registry JWT)
    is_local, bound_agent = _local_token_caller(request)
    if is_local:
        if bound_agent is not None:
            # Bound agent: DELETE is not allowed (same as JWT agent)
            raise HTTPException(status_code=403, detail="Memory deletion is human-only")
        # Unbound (host) token: keep existing admin behaviour
    else:
        # Registry JWT path
        # For registry-JWT agents, DELETE is not allowed even with memory_read
        # This route remains human-only as per the spec
        agent_canonical_id = await check_agent_scope(request, "memory_read")
        if agent_canonical_id is not None:
            raise HTTPException(status_code=403, detail="Memory deletion is human-only")
    
    http_client = request.app.state.http_client
    payload: dict = {"hash": content_hash, "dbPath": _agent_db_path(request, agent)}
    headers = build_trace_context_headers(conversation_id=conversation_id)
    try:
        resp = await http_client.post(
            f"{_qmd_base(request)}/delete-chunk", json=payload, headers=headers, timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("qmd /delete-chunk failed: %s", e)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=502)


@router.post("/api/memory/remember")
async def memory_remember(request: Request, body: RememberRequest):
    """Write a memory entry via qmd serve POST /ingest.

    Caller resolution (in order):
    a) Bound local token: body.agent must match bound agent or be omitted.
    b) Registry JWT with memory_write scope: body.agent must match resolved agent or be omitted.
    c) Human/admin session or unbound host token: body.agent is REQUIRED.
    """
    # a) Check for bound local token FIRST
    is_local, bound_agent = _local_token_caller(request)
    if is_local:
        if bound_agent is not None:
            # Bound agent: fence to own namespace
            if body.agent is not None and body.agent != bound_agent:
                raise HTTPException(status_code=403, detail="Agent can only write its own memory")
            agent = bound_agent
        else:
            # Unbound (host) token: falls through to (c)
            agent = None
    else:
        # b) Registry JWT path
        agent_canonical_id = await check_agent_scope(request, "memory_write")
        if agent_canonical_id is not None:
            # Resolve canonical_id to config agent name (same as memory_search)
            agent_config_name = await _resolve_agent_name(request, agent_canonical_id) or agent_canonical_id
            if body.agent is not None and body.agent != agent_config_name:
                raise HTTPException(status_code=403, detail="Agent can only write its own memory")
            # Verify the config agent's registry_canonical_id matches the caller's canonical_id
            # Fails closed: config agent must exist and its registry_canonical_id must equal the caller's canonical_id
            config_agent = find_agent(request.app.state.config, agent_config_name)
            if config_agent is None or config_agent.get("registry_canonical_id") != agent_canonical_id:
                raise HTTPException(status_code=403, detail="Agent can only write its own memory")
            agent = agent_config_name
        else:
            # c) Human/admin session or unbound host token (no valid scope)
            agent = None

    # c) Human/admin session or unbound host token: body.agent REQUIRED
    if agent is None:
        if not (body.agent or "").strip():
            raise HTTPException(status_code=400, detail="agent is required")
        agent = body.agent.strip()
        # For human/admin sessions (not unbound host token), verify agent ownership
        if getattr(request.state, "via", None) != "local_token":
            user = current_user(request)
            await require_agent_owner_or_admin(request, user, agent)

    # Write through shared qmd serve /ingest (same pattern as import_data.py)
    http_client = request.app.state.http_client
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    payload = {
        "body": body.text,
        "path": f"remember/{timestamp}.md",
        "title": body.title or "remembered",
        "collection": "remember",
        "dbPath": _agent_db_path(request, agent),
    }
    try:
        resp = await http_client.post(
            f"{_qmd_base(request)}/ingest", json=payload, timeout=30,
        )
        resp.raise_for_status()
        return {"stored": True, "agent": agent}
    except Exception as exc:
        logger.warning("qmd /ingest failed: %s", exc)
        return JSONResponse({"stored": False, "error": str(exc)}, status_code=502)
