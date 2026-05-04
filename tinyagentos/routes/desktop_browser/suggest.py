"""Address-bar suggest endpoint.

Local-only autocomplete: merges per-(user, profile) history and bookmarks.
No online suggest API — keystrokes never leave the box. PR 6 will add
@<agent> agent suggestions; for PR 4 the @ prefix is a no-op.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from tinyagentos.auth import get_current_user
from tinyagentos.routes.desktop_browser import router


@router.get("/api/desktop/browser/suggest")
async def suggest(
    profile_id: str,
    q: str,
    request: Request,
    limit: int = 8,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> dict:
    """Return up to `limit` autocomplete suggestions for `q`."""
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    # Cap limit to avoid abuse — a caller could pass limit=1_000_000.
    limit = max(1, min(50, limit))

    if not q.strip():
        return {"suggestions": []}

    # @<agent> reserved for PR 6 — no-op for now.
    if q.startswith("@"):
        return {"suggestions": []}

    store = request.app.state.browser_store

    suggestions: list[dict] = []

    # Bookmarks (rank above history — explicit user intent)
    bookmarks = await store.list_bookmarks(
        user_id=user_id, profile_id=profile_id, query=q, limit=limit,
    )
    for bm in bookmarks:
        suggestions.append({
            "url": bm["url"],
            "title": bm["title"],
            "source": "bookmark",
            "score": bm["created_at"],
        })

    # History
    history = await store.search_history(
        user_id=user_id, profile_id=profile_id, query=q, limit=limit,
    )
    seen_urls = {s["url"] for s in suggestions}
    for h in history:
        if h["url"] in seen_urls:
            continue
        suggestions.append({
            "url": h["url"],
            "title": h["title"] or "",
            "source": "history",
            "score": h["visited_at"],
        })

    # Cap to limit
    return {"suggestions": suggestions[:limit]}
