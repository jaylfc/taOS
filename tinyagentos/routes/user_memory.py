from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from tinyagentos.auth_context import require_admin
from tinyagentos.session_turns import stamp_event
from tinyagentos.user_memory import LEGACY_USER_ID

logger = logging.getLogger(__name__)

router = APIRouter()

# Pre per-user partition; only the install owner still reads it (see
# UserMemoryStore).  Used here only for agent search when no owner exists.
USER_ID = LEGACY_USER_ID

# Fixed agent namespace for user memory in taosmd.
_TAOSMD_AGENT = "user-memory"


def _uid(request: Request) -> str:
    """The signed-in user's id; chunks are per person, never pooled."""
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authentication required")
    return str(uid)


def _store(request: Request):
    return request.app.state.user_memory


def _taosmd_base(request: Request) -> str:
    """Base URL for the taosmd HTTP API.

    Precedence: app.state.taosmd_url (explicit runtime override, used by
    tests) → config.memory_url (persisted setting) → default localhost.
    """
    explicit = getattr(request.app.state, "taosmd_url", None)
    if explicit:
        return explicit
    config = getattr(request.app.state, "config", None)
    if config and getattr(config, "memory_url", None):
        return config.memory_url
    return "http://localhost:7900"


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
    stats = await _store(request).get_stats(_uid(request))
    return JSONResponse(stats)


@router.get("/api/user-memory/settings")
async def get_settings(request: Request):
    settings = await _store(request).get_settings(_uid(request))
    return JSONResponse(settings)


@router.put("/api/user-memory/settings", dependencies=[Depends(require_admin)])
async def update_settings(request: Request):
    body = await request.json()
    await _store(request).update_settings(_uid(request), body)
    return JSONResponse({"ok": True})


@router.get("/api/user-memory/search")
async def search(request: Request, q: str, collection: str | None = None, limit: int = 20):
    """Search user memory.

    Proxies to taosmd /search?mode=bm25 when available; falls back to the
    local SQLite FTS5 store if taosmd is unreachable or returns an error.
    """
    base = _taosmd_base(request)
    try:
        client = request.app.state.http_client
        params: dict = {"q": q, "agent": _TAOSMD_AGENT, "limit": limit, "mode": "bm25"}
        if collection:
            params["collection"] = collection
        resp = await client.get(f"{base}/search", params=params, timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            hits = data.get("hits", [])
            results = [_hit_to_chunk(h) for h in hits]
            return JSONResponse({"results": results, "query": q, "backend": "taosmd"})
    except Exception:
        logger.debug("taosmd search unavailable, falling back to SQLite")

    results = await _store(request).search(_uid(request), q, collection, limit)
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
    primary = request.app.state.auth.get_primary_user()
    owner_id = primary["id"] if primary else USER_ID
    results = await _store(request).search(owner_id, q, limit=limit)
    return JSONResponse({"results": results, "query": q, "agent": agent_name})


@router.get("/api/user-memory/browse")
async def browse(request: Request, collection: str | None = None, limit: int = 50):
    chunks = await _store(request).browse(_uid(request), collection, limit)
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

    # Session fields: a manual save belongs to a session only when the caller
    # names one (then it sits in that session's latest turn); otherwise the
    # fields are written as explicit unknown.  Server-owned, so they win over
    # anything in the caller's metadata.
    if not isinstance(metadata, dict):
        metadata = {}
    named = body.get("session_id")
    owner = getattr(request.state, "user_id", None)
    metadata = {**metadata, **await stamp_event(
        getattr(request.app.state, "session_turns", None),
        named if isinstance(named, str) and named else None,
        owner=str(owner) if owner else None,
    )}

    # Always write to SQLite for browse/stats/delete compatibility.
    h = await _store(request).save_chunk(_uid(request), content, title, collection, metadata)

    # Also ingest to taosmd when available (best-effort, non-fatal).
    base = _taosmd_base(request)
    try:
        client = request.app.state.http_client
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
            timeout=5.0,
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
    deleted = await _store(request).delete_chunk(_uid(request), chunk_hash)
    return JSONResponse({"ok": deleted})


@router.get("/api/user-memory/collections")
async def list_collections(request: Request):
    stats = await _store(request).get_stats(_uid(request))
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
        client = request.app.state.http_client
        health = await client.get(f"{base}/health", timeout=5.0)
        if health.status_code != 200:
            return JSONResponse({"error": "taosmd not healthy"}, status_code=503)
    except Exception:
        return JSONResponse({"error": "taosmd unreachable"}, status_code=503)

    # Page through the store so installs with >page_size chunks migrate fully.
    chunks: list[dict] = []
    page_size = 1000
    offset = 0
    while True:
        page = await _store(request).browse(_uid(request), limit=page_size, offset=offset)
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
        client = request.app.state.http_client
        resp = await client.post(
            f"{base}/ingest/batch",
            json={"agent": _TAOSMD_AGENT, "items": items},
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            # /ingest/batch not yet deployed — fall back to one-at-a-time
            ingested = 0
            client = request.app.state.http_client
            for item in items:
                try:
                    one = await client.post(
                        f"{base}/ingest",
                        json={"text": item["text"], "agent": _TAOSMD_AGENT,
                              "metadata": item["metadata"]},
                        timeout=30.0,
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
