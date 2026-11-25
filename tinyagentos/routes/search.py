from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request

from tinyagentos.session_turns import stamp_turn

router = APIRouter()
logger = logging.getLogger(__name__)


async def _capture_search_query(
    user_memory, query: str, user_id: str, session_turns=None,
) -> None:
    """Fire-and-forget search capture. Never raises.

    Searches are one session per user (``search:<user_id>``); each capture is a
    turn in it, owned by that user.
    """
    if not user_memory or not query:
        return
    try:
        settings = await user_memory.get_settings(user_id)
        if not settings.get("capture_searches"):
            return
        fields = await stamp_turn(
            session_turns, f"search:{user_id}", owner=user_id,
        )
        await user_memory.save_chunk(
            user_id,
            content=query,
            title=f"Search: {query[:50]}",
            collection="searches",
            metadata={"query": query, "source": "global_search", **fields},
        )
    except Exception as e:  # pragma: no cover
        logger.debug(f"search capture failed: {e}")


@router.get("/api/search")
async def global_search(request: Request, q: str = "", limit: int = 5):
    """Search across all platform data."""
    if not q or len(q) < 2:
        return {"results": [], "query": q}

    # Capture search into user memory (async, non-blocking)
    user_memory = getattr(request.app.state, "user_memory", None)
    if user_memory:
        uid = getattr(request.state, "user_id", None)
        if uid:
            asyncio.create_task(_capture_search_query(
                user_memory, q, str(uid),
                session_turns=getattr(request.app.state, "session_turns", None),
            ))
        else:
            logger.warning("search capture skipped: no authenticated user")

    query = q.lower()
    results = []

    # 1. Agents
    config = request.app.state.config
    for agent in config.agents:
        if query in agent.get("name", "").lower():
            results.append({
                "type": "agent",
                "title": agent["name"],
                "subtitle": f"Agent — {agent.get('status', 'configured')}",
                "url": f"/workspace/{agent['name']}",
            })

    # 2. Apps from catalog
    registry = request.app.state.registry
    for app in registry.list_available():
        if query in app.name.lower() or query in (app.description or "").lower():
            results.append({
                "type": "app",
                "title": app.name,
                "subtitle": f"App — {app.type}",
                "url": "/store",
            })
            if len(results) >= limit * 3:
                break

    # 3. Messages
    try:
        msg_store = request.app.state.agent_messages
        messages = await msg_store.search(q, limit=limit)
        for msg in messages:
            preview = msg["message"][:80] + ("..." if len(msg["message"]) > 80 else "")
            results.append({
                "type": "message",
                "title": f"{msg['from']} → {msg['to']}",
                "subtitle": preview,
                "url": f"/workspace/{msg['from']}/messages",
            })
    except Exception:
        pass

    # 4. Shared folders
    try:
        folders = await request.app.state.shared_folders.list_folders()
        for folder in folders:
            if query in folder.get("name", "").lower() or query in folder.get("description", "").lower():
                results.append({
                    "type": "folder",
                    "title": folder["name"],
                    "subtitle": f"Shared Folder — {folder.get('description', '')}",
                    "url": "/shared-folders",
                })
    except Exception:
        pass

    # Deduplicate and limit
    seen = set()
    unique = []
    for r in results:
        key = (r["type"], r["title"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
        if len(unique) >= limit * 2:
            break

    return {"results": unique[:limit * 2], "query": q, "total": len(unique)}
