from __future__ import annotations

"""API routes for the Reddit browser and thread viewer.

All routes live under /api/reddit/. The router reads these from
``request.app.state``:

- ``http_client``  — shared httpx.AsyncClient for outbound Reddit calls
- ``secrets``      — SecretsStore; Reddit OAuth token stored as "REDDIT_TOKEN"
"""

import logging
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.knowledge_fetchers.reddit import (
    RedditPost,
    RedditComment,
    fetch_thread,
    fetch_subreddit,
    flatten_to_text,
    extract_metadata,
    _REDDIT_WWW,
    _USER_AGENT,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Name used to look up the Reddit OAuth token in SecretsStore
_REDDIT_TOKEN_SECRET = "REDDIT_TOKEN"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _get_token(request: Request) -> str | None:
    """Return the Reddit OAuth token from SecretsStore, or None if not stored."""
    try:
        secrets = request.app.state.secrets
        secret = await secrets.get(_REDDIT_TOKEN_SECRET)
        if secret:
            return secret["value"]
    except Exception as exc:
        logger.debug("Could not read Reddit token from secrets: %s", exc)
    return None


def _serialise_post(post: RedditPost) -> dict:
    return {
        "id": post.id,
        "subreddit": post.subreddit,
        "title": post.title,
        "author": post.author,
        "selftext": post.selftext,
        "score": post.score,
        "upvote_ratio": post.upvote_ratio,
        "num_comments": post.num_comments,
        "created_utc": post.created_utc,
        "url": post.url,
        "permalink": post.permalink,
        "flair": post.flair,
        "is_self": post.is_self,
    }


def _serialise_comment(comment: RedditComment) -> dict:
    return {
        "id": comment.id,
        "author": comment.author,
        "body": comment.body,
        "score": comment.score,
        "created_utc": comment.created_utc,
        "depth": comment.depth,
        "parent_id": comment.parent_id,
        "edited": comment.edited,
        "distinguished": comment.distinguished,
        "replies": [_serialise_comment(r) for r in comment.replies],
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/reddit/thread")
async def get_thread(request: Request, url: str):
    """Fetch a Reddit thread (post + comment tree).

    Query params:
        url  — any valid Reddit thread URL
    """
    if not url:
        return JSONResponse({"error": "url parameter is required"}, status_code=400)

    http_client = request.app.state.http_client
    token = await _get_token(request)

    try:
        post, comments = await fetch_thread(url, http_client, token=token)
    except Exception as exc:
        logger.exception("fetch_thread failed for %s: %s", url, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)

    return {
        "post": _serialise_post(post),
        "comments": [_serialise_comment(c) for c in comments],
        "text": flatten_to_text(post, comments),
        "metadata": extract_metadata(post),
    }


@router.get("/api/reddit/subreddit")
async def get_subreddit(
    request: Request,
    name: str,
    sort: str = "hot",
    limit: int = 25,
    after: str = "",
):
    """Fetch a subreddit listing.

    Query params:
        name   — subreddit name without r/ prefix
        sort   — hot | new | top | rising  (default: hot)
        limit  — number of posts (default: 25; ignored — Reddit caps at 25)
        after  — pagination cursor from previous response
    """
    if not name:
        return JSONResponse({"error": "name parameter is required"}, status_code=400)

    http_client = request.app.state.http_client
    token = await _get_token(request)

    try:
        posts, next_after = await fetch_subreddit(
            subreddit=name,
            sort=sort,
            after=after or None,
            http_client=http_client,
            token=token,
        )
    except Exception as exc:
        logger.exception("fetch_subreddit failed for r/%s: %s", name, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)

    return {
        "subreddit": name,
        "sort": sort,
        "posts": [_serialise_post(p) for p in posts],
        "after": next_after,
    }


@router.get("/api/reddit/search")
async def search_reddit(
    request: Request,
    q: str = "",
    subreddit: str = "",
    sort: str = "relevance",
    limit: int = 25,
):
    """Search Reddit posts.

    Query params:
        q          — search query
        subreddit  — restrict search to this subreddit (optional)
        sort       — relevance | hot | new | top (default: relevance)
        limit      — number of results (default: 25)
    """
    if not q:
        return JSONResponse({"error": "q parameter is required"}, status_code=400)

    http_client = request.app.state.http_client
    token = await _get_token(request)

    from urllib.parse import urlencode

    base = "https://oauth.reddit.com" if token else _REDDIT_WWW
    params: dict[str, Any] = {"q": q, "sort": sort, "limit": min(limit, 100)}
    if subreddit:
        params["restrict_sr"] = "true"
        search_path = f"{base}/r/{subreddit}/search.json"
    else:
        search_path = f"{base}/search.json"

    url = f"{search_path}?{urlencode(params)}"
    headers: dict[str, str] = {"User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = await http_client.get(url, headers=headers, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.exception("Reddit search failed for %r: %s", q, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)

    children = data.get("data", {}).get("children", [])
    next_after = data.get("data", {}).get("after") or None
    posts = []
    for child in children:
        if child.get("kind") != "t3":
            continue
        pd = child["data"]
        posts.append(_serialise_post(RedditPost(
            id=pd["id"],
            subreddit=pd.get("subreddit", ""),
            title=pd.get("title", ""),
            author=pd.get("author", "[deleted]") or "[deleted]",
            selftext=pd.get("selftext", "") or "",
            score=int(pd.get("score", 0)),
            upvote_ratio=float(pd.get("upvote_ratio", 0.0)),
            num_comments=int(pd.get("num_comments", 0)),
            created_utc=float(pd.get("created_utc", 0.0)),
            url=pd.get("url", ""),
            permalink=pd.get("permalink", ""),
            flair=pd.get("link_flair_text", "") or "",
            is_self=bool(pd.get("is_self", False)),
        )))

    return {
        "query": q,
        "subreddit": subreddit,
        "sort": sort,
        "posts": posts,
        "after": next_after,
    }


@router.get("/api/reddit/saved")
async def get_saved(request: Request, after: str = ""):
    """Fetch the authenticated user's saved Reddit posts.

    Requires a Reddit OAuth token stored in SecretsStore as REDDIT_TOKEN.
    """
    from tinyagentos.knowledge_fetchers.reddit import fetch_saved

    token = await _get_token(request)
    if not token:
        return JSONResponse(
            {"error": "Not authenticated. Store a Reddit OAuth token as REDDIT_TOKEN."},
            status_code=401,
        )

    http_client = request.app.state.http_client

    try:
        posts, next_after = await fetch_saved(
            token=token,
            http_client=http_client,
            after=after or None,
        )
    except Exception as exc:
        logger.exception("fetch_saved failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=502)

    return {
        "posts": [_serialise_post(p) for p in posts],
        "after": next_after,
    }


@router.get("/api/reddit/auth/status")
async def auth_status(request: Request):
    """Return Reddit OAuth authentication status.

    Returns:
        {authenticated: bool, username: str|None}

    If a REDDIT_TOKEN is stored, we attempt to call /api/v1/me to get
    the current username. On failure we return authenticated=False.
    """
    token = await _get_token(request)
    if not token:
        return {"authenticated": False, "username": None}

    http_client = request.app.state.http_client
    headers = {
        "User-Agent": _USER_AGENT,
        "Authorization": f"Bearer {token}",
    }
    try:
        resp = await http_client.get(
            "https://oauth.reddit.com/api/v1/me",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        username = data.get("name")
        return {"authenticated": True, "username": username}
    except Exception as exc:
        logger.debug("Reddit /api/v1/me failed: %s", exc)
        return {"authenticated": False, "username": None}
