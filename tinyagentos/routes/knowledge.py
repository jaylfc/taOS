from __future__ import annotations

"""API routes for the Knowledge Base Service.

All routes live under /api/knowledge/. The router reads state from
``request.app.state``:

- ``knowledge_store``   — KnowledgeStore instance
- ``ingest_pipeline``   — IngestPipeline instance
- ``http_client``       — shared httpx.AsyncClient

Access control
--------------
All item/search routes require authentication (``Depends(get_current_user)``).
Admins see all items; regular members see only their own.
Legacy rows (``user_id == ''``) are treated as admin-only.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


# ------------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------------

class IngestRequest(BaseModel):
    url: str
    title: str = ""
    text: str = ""
    categories: list[str] = []
    source: str = "unknown"


class SearchRequest(BaseModel):
    query: str
    mode: str = "keyword"  # "keyword" or "semantic"
    limit: int = 20


class SubscriptionRequest(BaseModel):
    agent_name: str
    category: str
    auto_ingest: bool = False


class RuleRequest(BaseModel):
    pattern: str
    match_on: str
    category: str
    priority: int = 0


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _scope_user_id(user: dict[str, Any]) -> str | None:
    """Return the user_id filter to apply for list/search queries.

    Admins get ``None`` (no filter — see all items including legacy rows).
    Regular members get their user_id as the filter.
    """
    if user.get("is_admin"):
        return None
    return str(user.get("id") or "")


# ------------------------------------------------------------------
# Ingest
# ------------------------------------------------------------------

@router.post("/api/knowledge/ingest")
async def ingest(
    request: Request,
    body: IngestRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Submit a URL or pre-provided text for ingest.

    Returns immediately with the new item id and status='pending'.
    The pipeline runs in the background.
    """
    pipeline = request.app.state.ingest_pipeline
    user_id = str(current_user.get("id") or "")
    try:
        item_id = await pipeline.submit_background(
            url=body.url,
            title=body.title,
            text=body.text,
            categories=body.categories,
            source=body.source,
            user_id=user_id,
        )
        return {"id": item_id, "status": "pending"}
    except Exception as exc:
        logger.exception("ingest failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------
# Items — CRUD
# ------------------------------------------------------------------

@router.get("/api/knowledge/items")
async def list_items(
    request: Request,
    source_type: str | None = None,
    status: str | None = None,
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """List knowledge items with optional filters."""
    store = request.app.state.knowledge_store
    filter_user_id = _scope_user_id(current_user)
    items = await store.list_items(
        source_type=source_type,
        status=status,
        category=category,
        limit=limit,
        offset=offset,
        user_id=filter_user_id,
    )
    return {"items": items, "count": len(items)}


@router.get("/api/knowledge/items/{item_id}/snapshots")
async def list_snapshots(
    request: Request,
    item_id: str,
    limit: int = 20,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """List monitoring snapshots for an item."""
    store = request.app.state.knowledge_store
    filter_user_id = _scope_user_id(current_user)
    item = await store.get_item(item_id, user_id=filter_user_id)
    if item is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    snapshots = await store.list_snapshots(item_id, limit=limit)
    return {"snapshots": snapshots}


@router.get("/api/knowledge/items/{item_id}")
async def get_item(
    request: Request,
    item_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Fetch a single knowledge item by id."""
    store = request.app.state.knowledge_store
    filter_user_id = _scope_user_id(current_user)
    item = await store.get_item(item_id, user_id=filter_user_id)
    if item is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return item


@router.delete("/api/knowledge/items/{item_id}")
async def delete_item(
    request: Request,
    item_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Delete a knowledge item."""
    store = request.app.state.knowledge_store
    # Fetch without user filter to check existence, then enforce ownership.
    item = await store.get_item(item_id)
    if item is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    # Ownership check: admin always allowed; member only their own items.
    if not current_user.get("is_admin"):
        owner = item.get("user_id", "")
        caller = str(current_user.get("id") or "")
        if owner != caller:
            return JSONResponse({"error": "forbidden"}, status_code=403)
    deleted = await store.delete_item(item_id)
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"status": "deleted", "id": item_id}


# ------------------------------------------------------------------
# Search
# ------------------------------------------------------------------

@router.get("/api/knowledge/search")
async def search(
    request: Request,
    q: str = "",
    mode: str = "keyword",
    limit: int = 20,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Search the knowledge base by keyword (FTS5) or semantic (QMD vectors)."""
    store = request.app.state.knowledge_store
    filter_user_id = _scope_user_id(current_user)

    if mode == "semantic":
        http_client = request.app.state.http_client
        qmd_base = request.app.state.qmd_client.base_url
        try:
            resp = await http_client.post(
                f"{qmd_base}/vsearch",
                json={"query": q, "limit": limit, "collection": "knowledge"},
                timeout=60,
            )
            resp.raise_for_status()
            raw_results = resp.json().get("results", [])
            # Post-filter: restrict to caller's items (or all for admin).
            if filter_user_id is not None:
                # Resolve each result id against the store to check ownership.
                scoped = []
                for result in raw_results:
                    result_id = result.get("id") if isinstance(result, dict) else None
                    if result_id:
                        item = await store.get_item(result_id, user_id=filter_user_id)
                        if item is not None:
                            scoped.append(result)
                    else:
                        # If the result has no id we cannot scope it — skip.
                        pass
                raw_results = scoped
            return {"results": raw_results, "mode": "semantic"}
        except Exception as exc:
            logger.warning("QMD vsearch failed, falling back to FTS: %s", exc)
    results = await store.search_fts(q, limit=limit, user_id=filter_user_id)
    return {"results": results, "mode": "keyword"}


# ------------------------------------------------------------------
# Category rules
# ------------------------------------------------------------------

@router.get("/api/knowledge/rules")
async def list_rules(
    request: Request,
    _user: dict[str, Any] = Depends(get_current_user),
):
    """List all category rules."""
    store = request.app.state.knowledge_store
    return {"rules": await store.list_rules()}


@router.post("/api/knowledge/rules")
async def create_rule(
    request: Request,
    body: RuleRequest,
    _user: dict[str, Any] = Depends(get_current_user),
):
    """Create a new category rule."""
    store = request.app.state.knowledge_store
    rule_id = await store.add_rule(
        pattern=body.pattern,
        match_on=body.match_on,
        category=body.category,
        priority=body.priority,
    )
    return {"id": rule_id, "status": "created"}


@router.delete("/api/knowledge/rules/{rule_id}")
async def delete_rule(
    request: Request,
    rule_id: int,
    _user: dict[str, Any] = Depends(get_current_user),
):
    """Delete a category rule."""
    store = request.app.state.knowledge_store
    deleted = await store.delete_rule(rule_id)
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"status": "deleted", "id": rule_id}


# ------------------------------------------------------------------
# Agent subscriptions
# ------------------------------------------------------------------

@router.get("/api/knowledge/subscriptions")
async def list_subscriptions(
    request: Request,
    agent_name: str | None = None,
    _user: dict[str, Any] = Depends(get_current_user),
):
    """List agent knowledge subscriptions."""
    store = request.app.state.knowledge_store
    return {"subscriptions": await store.list_subscriptions(agent_name=agent_name)}


@router.post("/api/knowledge/subscriptions")
async def set_subscription(
    request: Request,
    body: SubscriptionRequest,
    _user: dict[str, Any] = Depends(get_current_user),
):
    """Upsert an agent subscription for a category."""
    store = request.app.state.knowledge_store
    await store.set_subscription(
        agent_name=body.agent_name,
        category=body.category,
        auto_ingest=body.auto_ingest,
    )
    return {"status": "ok"}


@router.delete("/api/knowledge/subscriptions/{agent_name}/{category}")
async def delete_subscription(
    request: Request,
    agent_name: str,
    category: str,
    _user: dict[str, Any] = Depends(get_current_user),
):
    """Remove an agent subscription."""
    store = request.app.state.knowledge_store
    deleted = await store.delete_subscription(agent_name=agent_name, category=category)
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"status": "deleted"}
