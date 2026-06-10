from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

USER_ID = "user"  # Single-user for now

# Fixed agent namespace for user memory in taosmd.
_TAOSMD_AGENT = "user-memory"


def _store(request: Request):
    return request.app.state.user_memory


def _taosmd_base(request: Request) -> str:
    """Base URL for the taosmd HTTP API.

    Reads from app.state.taosmd_url when set (tests can override),
    otherwise falls back to the local default.
    """
    return getattr(request.app.state, "taosmd_url", None) or "http://localhost:7900"


# ---------------------------------------------------------------------------
# Helper: map taosmd hit shape → user_memory_chunks shape
# ---------------------------------------------------------------------------

def _hit_to_chunk(hit: dict) -> dict:
    # Live taosmd hit shape (verified against /search?mode=bm25 on the #25
    # unification deploy): content arrives in "text" and the chunk id
    # round-trips as metadata.source_id — neither appears top-level.
    meta = hit.get("metadata") or {}
    return {
        "hash": hit.get("id") or meta.get("source_id") or hit.get("source_id") or "",
        "collection": meta.get("collection", "snippets"),
        "title": meta.get("title", ""),
        "content": hit.get("content") or hit.get("text", ""),
        "metadata": meta,
        "created_at": hit.get("timestamp") or hit.get("created_at"),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/user-memory/stats")
async def get_stats(request: Request):
    stats = await _store(request).get_stats(USER_ID)
    return JSONResponse(stats)


@router.get("/api/user-memory/settings")
async def get_settings(request: Request):
    settings = await _store(request).get_settings(USER_ID)
    return JSONResponse(settings)


@router.put("/api/user-memory/settings")
async def update_settings(request: Request):
    body = await request.json()
    await _store(request).update_settings(USER_ID, body)
    return JSONResponse({"ok": True})


@router.get("/api/user-memory/search")
async def search(request: Request, q: str, collection: str | None = None, limit: int = 20):
    """Search user memory.

    Proxies to taosmd /search?mode=bm25 when available; falls back to the
    local SQLite FTS5 store if taosmd is unreachable or returns an error.
    """
    base = _taosmd_base(request)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            params: dict = {"q": q, "agent": _TAOSMD_AGENT, "limit": limit, "mode": "bm25"}
            if collection:
                params["collection"] = collection
            resp = await client.get(f"{base}/search", params=params)
            if resp.status_code == 200:
                data = resp.json()
                hits = data.get("hits", [])
                results = [_hit_to_chunk(h) for h in hits]
                return JSONResponse({"results": results, "query": q, "backend": "taosmd"})
    except Exception:
        logger.debug("taosmd search unavailable, falling back to SQLite")

    results = await _store(request).search(USER_ID, q, collection, limit)
    return JSONResponse({"results": results, "query": q, "backend": "sqlite"})


@router.get("/api/user-memory/agent-search")
async def agent_search(request: Request, q: str, agent_name: str, limit: int = 10):
    """Read-only search endpoint for agents with user memory permission."""
    config = request.app.state.config
    agents = getattr(config, "agents", None)
    if agents is None and isinstance(config, dict):
        agents = config.get("agents", [])
    agents = agents or []
    agent = next((a for a in agents if a.get("name") == agent_name), None)
    if not agent or not agent.get("can_read_user_memory"):
        return JSONResponse(
            {"error": "Agent does not have user memory access"},
            status_code=403,
        )
    results = await _store(request).search(USER_ID, q, limit=limit)
    return JSONResponse({"results": results, "query": q, "agent": agent_name})


@router.get("/api/user-memory/browse")
async def browse(request: Request, collection: str | None = None, limit: int = 50):
    chunks = await _store(request).browse(USER_ID, collection, limit)
    return JSONResponse({"chunks": chunks})


@router.post("/api/user-memory/save")
async def save(request: Request):
    body = await request.json()
    content = body.get("content", "")
    title = body.get("title", "")
    collection = body.get("collection", "snippets")
    metadata = body.get("metadata", {})
    if not content:
        return JSONResponse({"error": "content required"}, status_code=400)

    # Always write to SQLite for browse/stats/delete compatibility.
    h = await _store(request).save_chunk(USER_ID, content, title, collection, metadata)

    # Also ingest to taosmd when available (best-effort, non-fatal).
    base = _taosmd_base(request)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{base}/ingest",
                json={
                    "text": content,
                    "agent": _TAOSMD_AGENT,
                    # Caller metadata first so the server-owned keys win —
                    # source_id is taosmd's dedup key and must not be
                    # overridable from the request body.
                    "metadata": {**metadata, "collection": collection, "title": title, "source_id": h},
                },
            )
            if resp.status_code >= 400:
                logger.debug(
                    "taosmd ingest returned %s — chunk saved to SQLite only",
                    resp.status_code,
                )
    except Exception:
        logger.debug("taosmd ingest unavailable — chunk saved to SQLite only")

    return JSONResponse({"ok": True, "hash": h})


@router.delete("/api/user-memory/chunk/{chunk_hash}")
async def delete_chunk(request: Request, chunk_hash: str):
    deleted = await _store(request).delete_chunk(USER_ID, chunk_hash)
    return JSONResponse({"ok": deleted})


@router.get("/api/user-memory/collections")
async def list_collections(request: Request):
    stats = await _store(request).get_stats(USER_ID)
    return JSONResponse({"collections": list(stats["collections"].keys())})


@router.post("/api/user-memory/migrate")
async def migrate_to_taosmd(request: Request):
    """One-time bulk migration: push all SQLite chunks to taosmd /ingest/batch.

    Returns counts of ingested and skipped items.  Safe to call multiple times
    (taosmd uses source_id for deduplication).  Returns 503 if taosmd is
    unreachable.
    """
    base = _taosmd_base(request)

    # Probe availability first.
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            health = await client.get(f"{base}/health")
            if health.status_code != 200:
                return JSONResponse({"error": "taosmd not healthy"}, status_code=503)
    except Exception:
        return JSONResponse({"error": "taosmd unreachable"}, status_code=503)

    # Page through the store so installs with >page_size chunks migrate fully.
    chunks: list[dict] = []
    page_size = 1000
    offset = 0
    while True:
        page = await _store(request).browse(USER_ID, limit=page_size, offset=offset)
        chunks.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    if not chunks:
        return JSONResponse({"ingested": 0, "skipped": 0, "total": 0})

    items = [
        {
            "text": c["content"],
            "id": c["hash"],
            # Stored metadata first so the server-owned keys win — source_id
            # is taosmd's dedup key and must not be overridable.
            "metadata": {
                **(c.get("metadata") or {}),
                "collection": c["collection"],
                "title": c["title"],
                "source_id": c["hash"],
            },
        }
        for c in chunks
    ]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{base}/ingest/batch",
                json={"agent": _TAOSMD_AGENT, "items": items},
            )
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            # /ingest/batch not yet deployed — fall back to one-at-a-time
            ingested = 0
            async with httpx.AsyncClient(timeout=30.0) as client:
                for item in items:
                    try:
                        one = await client.post(
                            f"{base}/ingest",
                            json={"text": item["text"], "agent": _TAOSMD_AGENT,
                                  "metadata": item["metadata"]},
                        )
                        if one.status_code < 400:
                            ingested += 1
                    except Exception:
                        pass
            result = {"ingested": ingested, "skipped": len(items) - ingested}
    except Exception:
        # Don't reflect raw exception text (it can carry the internal taosmd
        # host/URL) back to the caller; details go to the server log only.
        logger.warning("taosmd bulk ingest failed", exc_info=True)
        return JSONResponse({"error": "taosmd ingest failed"}, status_code=500)

    return JSONResponse({**result, "total": len(items)})
