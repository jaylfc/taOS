"""Memory routes — per-agent and user memory over the shared qmd serve.

Every memory operation is an HTTP call to the host ``qmd.service``
process on :7832. That process exposes ``/search``, ``/vsearch``,
``/browse``, ``/collections``, ``/ingest``, and ``/delete-chunk`` and
each call accepts an optional ``dbPath`` that selects which SQLite
file to operate on. TinyAgentOS resolves the ``dbPath`` based on the
calling scope:

- ``agent=foo``  → ``data/agent-memory/foo/index.sqlite``
- no agent       → the default user index (``~/.cache/qmd/index.sqlite``,
  served when ``dbPath`` is omitted)

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
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.otel.trace_context import build_trace_context_headers

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
    """
    base: Path = request.app.state.agent_memory_dir
    if not agent:
        target = base.parent / "user-qmd-index" / "index.sqlite"
    else:
        target = base / agent / "index.sqlite"
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
    http_client = request.app.state.http_client
    params: dict = {"limit": limit, "offset": offset}
    if collection:
        params["collection"] = collection
    db_path = _agent_db_path(request, agent)
    if db_path:
        params["dbPath"] = db_path
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
    http_client = request.app.state.http_client
    params: dict = {}
    db_path = _agent_db_path(request, agent_name)
    if db_path:
        params["dbPath"] = db_path
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
    http_client = request.app.state.http_client
    payload: dict = {"hash": content_hash}
    db_path = _agent_db_path(request, agent)
    if db_path:
        payload["dbPath"] = db_path
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
