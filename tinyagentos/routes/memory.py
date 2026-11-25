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
import re
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import (
    agent_bound_name,
    current_user,
    is_host_caller,
    is_legacy_owner,
    require_agent_owner_or_admin,
)
from tinyagentos.otel.trace_context import build_trace_context_headers
from tinyagentos.qmd_service import make_index_dir, qmd_honours_dbpath

logger = logging.getLogger(__name__)

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    mode: str = "keyword"
    agent: str | None = None
    collection: str | None = None
    limit: int = 20
    # Optional W3C trace context — callers (adapters, librarian) pass the
    # active conversation_id so the memory backend can nest its spans.
    conversation_id: str | None = None


async def _authorize_agent_memory(
    request: Request, agent: str | None, *, allow_bound: bool = True,
) -> str | None:
    """Authorise access to *agent*'s memory and return the agent to use.

    Runs BEFORE ``_agent_db_path`` (which creates the agent's directory), so a
    refused request touches nothing on disk.

    - Agent-bound local token: fenced to its own agent (it maps to the admin
      user, so an owner check alone would let agent A read agent B). An omitted
      agent defaults to the bound one. ``allow_bound=False`` (delete) refuses it
      outright: deletion is human-only.
    - Unbound host local token: admin-equivalent, unchanged.
    - Signed-in session: the agent's owner or an admin, via the same helper and
      identifier form as ``routes/agent_workspace``.
    - Without an agent the request targets the shared user index and no
      per-agent ownership applies.
    """
    if getattr(request.state, "via", None) in ("local_token", "agent_token"):
        bound = getattr(request.state, "agent_name", None)
        if bound:
            if not allow_bound:
                raise HTTPException(status_code=403, detail="Memory deletion is human-only")
            if agent is not None and agent.strip() != bound:
                raise HTTPException(status_code=403, detail="forbidden")
            return bound
        return agent
    if not agent or is_legacy_owner(request):
        return agent
    # Authorise the exact component _agent_db_path will use.
    await require_agent_owner_or_admin(request, current_user(request), agent.strip())
    return agent


async def _require_qmd_dbpath(request: Request) -> None:
    """503 when qmd cannot honour ``dbPath``: it would answer from ONE shared index.

    Called after authorisation and BEFORE ``_agent_db_path`` so a refusal
    creates nothing on disk. An unreachable qmd (probe result unknown) falls
    through to the route's normal 502 handling.
    """
    if await qmd_honours_dbpath(request.app.state) is False:
        raise HTTPException(
            status_code=503,
            detail="memory service needs updating: qmd cannot keep agent and personal memory apart",
        )


def _qmd_base(request: Request) -> str:
    """URL of the shared host qmd serve process."""
    return request.app.state.qmd_client.base_url


_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _user_index_path(request: Request) -> Path:
    """The index for a request that names no agent (the single no-agent resolver).

    Keyed ONLY on the authenticated caller, never on a request parameter:

    - an admin session and the unbound host token keep the install-wide
      ``user-qmd-index/index.sqlite`` (no data migration);
    - any other signed-in person gets ``user-qmd-index/<user_id>/index.sqlite``,
      so one member can never see or delete another member's entries, nor the
      admin's;
    - no identity at all is refused (there is no anonymous index).

    The root sits beside, not inside, ``agent_memory_dir``, and agent names are
    single path components under ``agent_memory_dir``, so no agent name can
    address a person's index and no person's index is an agent's.
    """
    root = request.app.state.agent_memory_dir.parent / "user-qmd-index"
    if agent_bound_name(request):
        # A bound agent token defaults to its own agent before reaching here.
        raise HTTPException(status_code=403, detail="forbidden")
    if (
        is_host_caller(request)
        or getattr(request.state, "is_admin", False)
        or is_legacy_owner(request)
    ):
        return root / "index.sqlite"
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authentication required")
    if not isinstance(uid, str) or not _USER_ID_RE.match(uid):
        raise HTTPException(status_code=403, detail="forbidden")
    target = root / uid / "index.sqlite"
    if not target.resolve().is_relative_to(root.resolve()):
        raise HTTPException(status_code=403, detail="forbidden")
    return target


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
        target = _user_index_path(request)
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
    make_index_dir(target.parent, base if agent else base.parent / "user-qmd-index")
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
    agent = await _authorize_agent_memory(request, agent)
    http_client = request.app.state.http_client
    params: dict = {"limit": limit, "offset": offset}
    if collection:
        params["collection"] = collection
    await _require_qmd_dbpath(request)
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
    is intentionally not supported here — agents do not share memory.
    Aggregating across agents is a separate concern that belongs in a
    future ``/api/memory/all`` endpoint, gated by user permission.
    """
    body.agent = await _authorize_agent_memory(request, body.agent)
    await _require_qmd_dbpath(request)
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
    agent_name = await _authorize_agent_memory(request, agent_name)
    http_client = request.app.state.http_client
    await _require_qmd_dbpath(request)
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
    agent = await _authorize_agent_memory(request, agent, allow_bound=False)
    http_client = request.app.state.http_client
    await _require_qmd_dbpath(request)
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
