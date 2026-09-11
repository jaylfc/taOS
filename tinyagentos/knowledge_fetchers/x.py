from __future__ import annotations

"""X (Twitter) fetcher for TinyAgentOS knowledge pipeline.

Provides async functions to fetch tweets via yt-dlp, reconstruct threads,
stitch thread text, and extract metadata.  Also provides XWatchStore for
persisting author-watch configurations.
"""

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from tinyagentos.base_store import BaseStore
from tinyagentos.db_migrations import apply_wal_pragmas

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

_YTDLP_NOT_FOUND_MSG = "yt-dlp not installed -- install the optional media extra"

# ---------------------------------------------------------------------------
# yt-dlp fetcher
# ---------------------------------------------------------------------------

async def fetch_tweet_ytdlp(url: str) -> dict:
    """Fetch a tweet via yt-dlp and return a normalised dict.

    Runs ``yt-dlp --dump-single-json --no-download <url>`` in a subprocess and
    parses the JSON output.  Raises RuntimeError if yt-dlp is not installed,
    exits with a non-zero code, or if the JSON cannot be parsed.

    Args:
        url: Full tweet URL, e.g. https://twitter.com/user/status/12345

    Returns:
        Dict with keys: id, author, handle, text, likes, reposts, views,
        created_at, media

    Raises:
        RuntimeError: If yt-dlp is not installed or the fetch fails.
    """
    ytdlp = shutil.which("yt-dlp")
    if ytdlp is None:
        raise RuntimeError(_YTDLP_NOT_FOUND_MSG)

    try:
        proc = await asyncio.create_subprocess_exec(
            ytdlp,
            "--dump-single-json",
            "--no-download",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            raise RuntimeError(f"yt-dlp failed for {url!r}: {err}")

        data = json.loads(stdout.decode(errors="replace"))[0]
    except (json.JSONDecodeError, FileNotFoundError, OSError) as exc:
        raise RuntimeError(f"fetch_tweet_ytdlp error for {url}: {exc}") from exc

    # yt-dlp fields for tweets
    tweet_id: str = str(data.get("id", ""))
    text: str = data.get("description", "") or data.get("title", "") or ""
    author: str = data.get("uploader", "") or ""
    handle_raw: str = data.get("uploader_id", "") or ""
    if not handle_raw:
        uploader_url: str = data.get("uploader_url", "") or ""
        handle_raw = uploader_url.rstrip("/").rsplit("/", 1)[-1]
    handle: str = handle_raw
    likes: int = int(data.get("like_count", 0) or 0)
    reposts: int = int(data.get("repost_count", 0) or 0)
    views: int = int(data.get("view_count", 0) or 0)
    timestamp: float = float(data.get("timestamp", 0) or 0)

    # Media: collect thumbnails and any direct video/image URLs
    media: list[dict] = []
    if data.get("url"):
        ext = (data.get("ext") or "").lower()
        media_type = "video" if ext in ("mp4", "mov", "webm") else "image"
        media.append({"type": media_type, "url": data["url"]})
    for thumb in data.get("thumbnails", []):
        if isinstance(thumb, dict) and thumb.get("url"):
            media.append({"type": "image", "url": thumb["url"]})

    return {
        "id": tweet_id,
        "author": author,
        "handle": handle,
        "text": text,
        "likes": likes,
        "reposts": reposts,
        "views": views,
        "created_at": timestamp,
        "media": media,
    }


# ---------------------------------------------------------------------------
# Cookie-authed GraphQL fetch (placeholder for v1)
# ---------------------------------------------------------------------------

async def fetch_tweet_cookies(
    tweet_id: str,
    cookies: dict,
    http_client: "httpx.AsyncClient",
) -> dict | None:
    """Fetch a tweet via X GraphQL API using browser cookies.

    v1 placeholder -- always returns None.  Cookie integration via Agent
    Browsers is planned for a future release.

    Future implementation notes:
    -----------------------------------------------------------------------
    GraphQL endpoint:
        GET https://twitter.com/i/api/graphql/<queryId>/TweetDetail

    Required headers:
        Authorization: Bearer <bearer_token>   # public bearer token
        x-csrf-token: <ct0 cookie value>
        Cookie: auth_token=<auth_token>; ct0=<ct0>

    Query variables (JSON-encoded as query param):
        {"focalTweetId": "<tweet_id>", "referrer": "tweet", "count": 20,
         "includePromotedContent": true, "withCommunity": true}
    -----------------------------------------------------------------------
    """
    return None


# ---------------------------------------------------------------------------
# Thread reconstruction
# ---------------------------------------------------------------------------

async def reconstruct_thread(
    tweet_id: str,
    cookies: dict | None,
    http_client: "httpx.AsyncClient",
) -> list[dict]:
    """Reconstruct a tweet thread.

    v1 (no cookie auth): returns a single tweet fetched via yt-dlp.

    Future (with cookies): walk reply chain up (conversation_id) and down
    (replies via GraphQL) to assemble a full ordered thread.

    Args:
        tweet_id: Numeric tweet ID.
        cookies: Browser cookies dict or None.
        http_client: Shared httpx.AsyncClient.

    Returns:
        List of tweet dicts in thread order.
    """
    url = f"https://twitter.com/i/web/status/{tweet_id}"
    tweet = await fetch_tweet_ytdlp(url)
    if tweet is None:
        return []
    return [tweet]


# ---------------------------------------------------------------------------
# Thread text stitching
# ---------------------------------------------------------------------------

def stitch_thread_text(tweets: list[dict]) -> str:
    """Join tweet texts with double newlines, prefixed by @handle.

    Returns the ``content`` field suitable for a KnowledgeItem.

    Args:
        tweets: List of tweet dicts, each with 'handle' and 'text' keys.

    Returns:
        Multi-line string of stitched thread content.
    """
    parts: list[str] = []
    for tweet in tweets:
        handle = tweet.get("handle", "")
        text = tweet.get("text", "")
        prefix = f"@{handle}" if handle else ""
        if prefix:
            parts.append(f"{prefix}\n{text}")
        else:
            parts.append(text)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------

def extract_metadata(tweet: dict) -> dict:
    """Return a metadata dict suitable for storage in KnowledgeItem.metadata.

    Args:
        tweet: Tweet dict from fetch_tweet_ytdlp.

    Returns:
        Dict with: likes, reposts, views, handle, created_at.
    """
    return {
        "likes": tweet.get("likes", 0),
        "reposts": tweet.get("reposts", 0),
        "views": tweet.get("views", 0),
        "handle": tweet.get("handle", ""),
        "created_at": tweet.get("created_at", 0),
    }


# ---------------------------------------------------------------------------
# Author watch SQLite store
# ---------------------------------------------------------------------------

WATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS x_author_watches (
    handle TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    filters_json TEXT NOT NULL DEFAULT '{}',
    frequency INTEGER NOT NULL DEFAULT 1800,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_check REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
"""

_DEFAULT_DB_PATH = Path("data/x-watches.db")

class XWatchStore(BaseStore):
    """Persistent store for X author watches backed by SQLite.

    Args:
        db_path: Path to the SQLite database file. Defaults to
            ``data/x-watches.db`` relative to the ``data_dir``.
        engine: Optional database engine. Defaults to SQLite.
    """

    SCHEMA = WATCH_SCHEMA

    async def _post_init(self) -> None:
        """Create schema after connection is established."""
        await self._db.executescript(self.SCHEMA)
        await self._db.commit()

    async def create_watch(
        self,
        handle: str,
        filters: dict | None = None,
        frequency: int = 1800,
        user_id: str | None = None,
    ) -> dict:
        """Create a new author watch.

        Args:
            handle: X handle without the leading @.
            filters: Optional filters dict.
            frequency: Check interval in seconds (default 1800).
            user_id: Optional user ID for ownership. If not provided, the store will
                     attempt to use a default from context or raise an error.

        Returns:
            The created watch dict.

        Raises:
            ValueError: If a watch for this handle already exists.
            RuntimeError: If user_id is required but not provided.
        """
        if user_id is None:
            raise RuntimeError("XWatchStore requires user_id for ownership")
        
        filters_json = json.dumps(filters or {})
        created_at = time.time()
        try:
            await self._db.execute(
                """
                INSERT INTO x_author_watches
                    (handle, user_id, filters_json, frequency, enabled, last_check, created_at)
                VALUES (?, ?, ?, ?, 1, 0, ?)
                """,
                (handle.lstrip("@"), user_id, filters_json, frequency, created_at),
            )
            await self._db.commit()
        except Exception as e:
            raise ValueError(f"Watch for @{handle} already exists") from e
        return await self.get_watch(handle.lstrip("@"))

    async def list_watches(self, user_id: str | None = None) -> list[dict]:
        """Return all author watches as a list of dicts.
        
        If user_id is provided, only returns watches for that user.
        If user_id is None, returns all watches (admin view).
        """
        if user_id is None:
            sql = "SELECT * FROM x_author_watches ORDER BY created_at DESC"
            params = []
        else:
            sql = "SELECT * FROM x_author_watches WHERE user_id = ? ORDER BY created_at DESC"
            params = [user_id]
        
        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def get_watch(self, handle: str) -> dict | None:
        """Return a single watch by handle, or None if not found."""
        async with self._db.execute(
            "SELECT * FROM x_author_watches WHERE handle = ?",
            (handle.lstrip("@"),),
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_dict(row) if row else None

    async def update_watch(self, handle: str, updates: dict) -> dict | None:
        """Update an existing watch.

        Allowed keys in ``updates``: filters, frequency, enabled, last_check.

        Returns:
            Updated watch dict, or None if handle not found.
        """
        existing = await self.get_watch(handle)
        if existing is None:
            return None

        allowed = {"filters", "frequency", "enabled", "last_check"}
        set_clauses: list[str] = []
        params: list = []

        for key, value in updates.items():
            if key not in allowed:
                continue
            if key == "filters":
                set_clauses.append("filters_json = ?")
                params.append(json.dumps(value))
            else:
                set_clauses.append(f"{key} = ?")
                params.append(value)

        if not set_clauses:
            return existing

        params.append(handle.lstrip("@"))
        await self._db.execute(
            f"UPDATE x_author_watches SET {', '.join(set_clauses)} WHERE handle = ?",
            params,
        )
        await self._db.commit()
        return await self.get_watch(handle)

    async def delete_watch(self, handle: str) -> bool:
        """Delete a watch by handle.

        Returns:
            True if a row was deleted, False if handle was not found.
        """
        cursor = await self._db.execute(
            "DELETE FROM x_author_watches WHERE handle = ?",
            (handle.lstrip("@"),),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_dict(row: tuple) -> dict:
        """Convert SQLite row to dict, handling filters_json."""
        d = dict(row)
        try:
            d["filters"] = json.loads(d.pop("filters_json", "{}") or "{}")
        except (json.JSONDecodeError, KeyError):
            d["filters"] = {}
        return d