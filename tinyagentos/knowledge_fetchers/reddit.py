from __future__ import annotations

"""Reddit fetcher for TinyAgentOS knowledge pipeline.

Provides async functions to fetch Reddit threads, subreddit listings,
saved posts, and helpers to format content as flat markdown.
"""

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs

if TYPE_CHECKING:
    import httpx

_USER_AGENT = "TinyAgentOS/1.0"
_REDDIT_WWW = "https://www.reddit.com"
_REDDIT_OAUTH = "https://oauth.reddit.com"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RedditPost:
    id: str
    subreddit: str
    title: str
    author: str
    selftext: str
    score: int
    upvote_ratio: float
    num_comments: int
    created_utc: float
    url: str
    permalink: str
    flair: str
    is_self: bool


@dataclass
class RedditComment:
    id: str
    author: str
    body: str
    score: int
    created_utc: float
    depth: int
    parent_id: str
    replies: list["RedditComment"]
    edited: bool | float | None
    distinguished: str | None


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _normalise_url(url: str, token: str | None = None) -> str:
    """Return a .json URL ready for the Reddit API.

    - Strips query params and fragments.
    - Appends .json if not present.
    - Adds ?limit=500.
    - Switches host to oauth.reddit.com when token is provided.
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path.endswith(".json"):
        path = path + ".json"

    host = _REDDIT_OAUTH.replace("https://", "") if token else "www.reddit.com"
    clean = urlunparse(("https", host, path, "", "limit=500", ""))
    return clean


# ---------------------------------------------------------------------------
# Comment tree builder
# ---------------------------------------------------------------------------

_MAX_COMMENT_DEPTH = 2000
_MAX_COMMENT_COUNT = 10_000


def _normalise_edited(edited_raw) -> bool | float | None:
    """Normalise the Reddit ``edited`` field.

    Reddit returns:
      - ``False``  — comment was not edited.
      - ``True``   — legacy boolean meaning "edited, no timestamp".
      - a number   — epoch timestamp of the edit.

    ``bool`` is a subclass of ``int``, so ``isinstance(True, (int, float))``
    is true.  We must check ``bool`` *first* to avoid coercing ``True`` to
    ``float(True)`` == ``1.0`` (the 1970 epoch).
    """
    if isinstance(edited_raw, bool):
        return None if edited_raw else False
    if isinstance(edited_raw, (int, float)):
        return float(edited_raw)
    return False


def _build_comment_from_data(data: dict, depth: int) -> RedditComment:
    """Build a RedditComment from raw data (without recursing into replies)."""
    author = data.get("author", "[deleted]") or "[deleted]"
    body = data.get("body", "[deleted]") or "[deleted]"
    if author in ("", None):
        author = "[deleted]"
    if body in ("", None):
        body = "[deleted]"

    return RedditComment(
        id=data.get("id", ""),
        author=author,
        body=body,
        score=int(data.get("score", 0)),
        created_utc=float(data.get("created_utc", 0.0)),
        depth=depth,
        parent_id=data.get("parent_id", ""),
        replies=[],
        edited=_normalise_edited(data.get("edited", False)),
        distinguished=data.get("distinguished"),
    )


def _parse_comment(data: dict, depth: int = 0) -> RedditComment:
    """Parse a comment data dict into a RedditComment.

    Uses an explicit stack instead of recursion so that arbitrarily deep
    comment trees (e.g. 2 000-level chains) do not exhaust the Python stack.
    """
    root = _build_comment_from_data(data, depth)
    stack: list[tuple[RedditComment, dict]] = [(root, data)]
    count = 0
    while stack:
        node, node_data = stack.pop()
        count += 1
        if count >= _MAX_COMMENT_COUNT:
            break
        if node.depth >= _MAX_COMMENT_DEPTH:
            continue
        replies_raw = node_data.get("replies")
        if isinstance(replies_raw, dict):
            for child in replies_raw.get("data", {}).get("children", []):
                if child.get("kind") == "t1":
                    child_data = child["data"]
                    child_comment = _build_comment_from_data(child_data, node.depth + 1)
                    node.replies.append(child_comment)
                    stack.append((child_comment, child_data))
                # "more" nodes inside replies are skipped (stub)
    return root


def _parse_comments_listing(listing: dict) -> list[RedditComment]:
    """Parse the second element of a Reddit thread .json response."""
    comments: list[RedditComment] = []
    for child in listing.get("data", {}).get("children", []):
        kind = child.get("kind")
        if kind == "t1":
            comments.append(_parse_comment(child["data"]))
        elif kind == "more":
            # Stub: create placeholder comments for "more" nodes
            more_data = child.get("data", {})
            stub = RedditComment(
                id=more_data.get("id", "more"),
                author="[more]",
                body=f"[{more_data.get('count', 0)} more replies not loaded]",
                score=0,
                created_utc=0.0,
                depth=int(more_data.get("depth", 0)),
                parent_id=more_data.get("parent_id", ""),
                replies=[],
                edited=False,
                distinguished=None,
            )
            comments.append(stub)
    return comments


# ---------------------------------------------------------------------------
# Public fetch functions
# ---------------------------------------------------------------------------

async def fetch_thread(
    url: str,
    http_client: "httpx.AsyncClient",
    token: str | None = None,
) -> tuple[RedditPost, list[RedditComment]]:
    """Fetch a Reddit thread and return (post, comments).

    Args:
        url: Any valid Reddit thread URL.
        http_client: Shared httpx.AsyncClient.
        token: Optional OAuth bearer token; switches to oauth.reddit.com.

    Returns:
        Tuple of (RedditPost, list[RedditComment]).
    """
    json_url = _normalise_url(url, token)
    headers: dict[str, str] = {"User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = await http_client.get(json_url, headers=headers, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()

    # data[0] = post listing, data[1] = comments listing
    post_children = data[0]["data"]["children"]
    post_data = post_children[0]["data"]

    post = RedditPost(
        id=post_data["id"],
        subreddit=post_data.get("subreddit", ""),
        title=post_data.get("title", ""),
        author=post_data.get("author", "[deleted]") or "[deleted]",
        selftext=post_data.get("selftext", "") or "",
        score=int(post_data.get("score", 0)),
        upvote_ratio=float(post_data.get("upvote_ratio", 0.0)),
        num_comments=int(post_data.get("num_comments", 0)),
        created_utc=float(post_data.get("created_utc", 0.0)),
        url=post_data.get("url", ""),
        permalink=post_data.get("permalink", ""),
        flair=post_data.get("link_flair_text", "") or "",
        is_self=bool(post_data.get("is_self", False)),
    )

    comments = _parse_comments_listing(data[1])
    return post, comments


async def fetch_subreddit(
    subreddit: str,
    sort: str,
    after: str | None,
    http_client: "httpx.AsyncClient",
    token: str | None = None,
) -> tuple[list[RedditPost], str | None]:
    """Fetch a subreddit listing.

    Args:
        subreddit: Subreddit name (without r/ prefix).
        sort: One of hot, new, top, rising.
        after: Pagination cursor from a previous call.
        http_client: Shared httpx.AsyncClient.
        token: Optional OAuth bearer token.

    Returns:
        Tuple of (list[RedditPost], next_after_cursor or None).
    """
    base = _REDDIT_OAUTH if token else _REDDIT_WWW
    params: dict[str, str] = {"limit": "25"}
    if after:
        params["after"] = after

    query = urlencode(params)
    url = f"{base}/r/{subreddit}/{sort}.json?{query}"

    headers: dict[str, str] = {"User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = await http_client.get(url, headers=headers, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()

    listing_data = data.get("data", {})
    children = listing_data.get("children", [])
    next_after: str | None = listing_data.get("after") or None

    posts: list[RedditPost] = []
    for child in children:
        if child.get("kind") != "t3":
            continue
        pd = child["data"]
        posts.append(RedditPost(
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
        ))

    return posts, next_after


async def fetch_saved(
    token: str,
    http_client: "httpx.AsyncClient",
    after: str | None = None,
) -> tuple[list[RedditPost], str | None]:
    """Fetch the authenticated user's saved posts.

    Args:
        token: OAuth bearer token (required).
        http_client: Shared httpx.AsyncClient.
        after: Pagination cursor.

    Returns:
        Tuple of (list[RedditPost], next_after_cursor or None).
    """
    params: dict[str, str] = {"limit": "25"}
    if after:
        params["after"] = after

    query = urlencode(params)
    url = f"{_REDDIT_OAUTH}/user/me/saved?{query}"

    headers = {
        "User-Agent": _USER_AGENT,
        "Authorization": f"Bearer {token}",
    }

    resp = await http_client.get(url, headers=headers, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()

    listing_data = data.get("data", {})
    children = listing_data.get("children", [])
    next_after: str | None = listing_data.get("after") or None

    posts: list[RedditPost] = []
    for child in children:
        if child.get("kind") != "t3":
            continue
        pd = child["data"]
        posts.append(RedditPost(
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
        ))

    return posts, next_after


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _flatten_comment(comment: RedditComment, lines: list[str]) -> None:
    """Flatten a comment tree into text lines (iterative to avoid RecursionError)."""
    stack: list[RedditComment] = [comment]
    count = 0
    while stack:
        node = stack.pop()
        count += 1
        if count >= _MAX_COMMENT_COUNT:
            break
        indent = "  " * node.depth
        lines.append(f"{indent}**{node.author}** (score: {node.score})")
        for body_line in node.body.splitlines():
            lines.append(f"{indent}{body_line}")
        lines.append("")
        for reply in reversed(node.replies):
            stack.append(reply)


def flatten_to_text(post: RedditPost, comments: list[RedditComment]) -> str:
    """Format a Reddit post + comment tree as markdown text.

    Returns a string with:
    - # title
    - blank line
    - selftext (if present)
    - ---
    - comments indented by depth
    """
    lines: list[str] = []
    lines.append(f"# {post.title}")
    lines.append("")
    if post.selftext:
        lines.append(post.selftext)
        lines.append("")
    lines.append("---")
    lines.append("")
    for comment in comments:
        _flatten_comment(comment, lines)
    return "\n".join(lines)


def extract_metadata(post: RedditPost) -> dict:
    """Return a metadata dict suitable for storage in KnowledgeItem.metadata."""
    return {
        "subreddit": post.subreddit,
        "score": post.score,
        "upvote_ratio": post.upvote_ratio,
        "num_comments": post.num_comments,
        "created_utc": post.created_utc,
        "flair": post.flair,
        "is_self": post.is_self,
    }
