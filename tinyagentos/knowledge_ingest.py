from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx
    from tinyagentos.knowledge_store import KnowledgeStore
    from tinyagentos.knowledge_categories import CategoryEngine
    from tinyagentos.notifications import NotificationStore

logger = logging.getLogger(__name__)

# Quality threshold: minimum chars for readability extraction to count as success
_MIN_CONTENT_CHARS = 100

# Limit concurrent background ingest tasks to prevent resource exhaustion.
_INGEST_SEMAPHORE_SLOTS = 5

# Per-source default monitor config
_DEFAULT_MONITOR: dict[str, dict] = {
    "reddit":  {"frequency": 3600,  "decay_rate": 1.5, "stop_after_days": 30,  "pinned": False, "last_poll": 0, "current_interval": 3600},
    "x":       {"frequency": 1800,  "decay_rate": 2.0, "stop_after_days": 14,  "pinned": False, "last_poll": 0, "current_interval": 1800},
    "github":  {"frequency": 21600, "decay_rate": 1.5, "stop_after_days": 60,  "pinned": False, "last_poll": 0, "current_interval": 21600},
    "youtube": {"frequency": 86400, "decay_rate": 2.0, "stop_after_days": 30,  "pinned": False, "last_poll": 0, "current_interval": 86400},
    "article": {"frequency": 86400, "decay_rate": 2.0, "stop_after_days": 14,  "pinned": False, "last_poll": 0, "current_interval": 86400},
    "file":    {"frequency": 0,     "decay_rate": 1.0, "stop_after_days": 0,   "pinned": False, "last_poll": 0, "current_interval": 0},
    "manual":  {"frequency": 0,     "decay_rate": 1.0, "stop_after_days": 0,   "pinned": False, "last_poll": 0, "current_interval": 0},
}


def resolve_source_type(url: str) -> str:
    """Identify the content platform from a URL.

    Returns one of: reddit, youtube, x, github, article.
    """
    url_lower = url.lower()
    if re.search(r"(^|[./])reddit\.com/", url_lower):
        return "reddit"
    if re.search(r"(^|[./])youtube\.com/watch|youtu\.be/", url_lower):
        return "youtube"
    if re.search(r"(^|[./])(x\.com|twitter\.com)/", url_lower):
        return "x"
    if re.search(r"(^|[./])github\.com/", url_lower):
        return "github"
    return "article"


def _extract_text_readability(html: str) -> str:
    """Very lightweight readability extraction: strip tags, collapse whitespace.

    A proper implementation would use a library like ``readability-lxml``.
    This stub is sufficient for unit-tested pipeline flow; swap in a real
    extractor in production without changing the interface.
    """
    # Remove script and style blocks
    html = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Strip tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


class IngestPipeline:
    """Async pipeline that downloads, summarises, embeds, and stores content.

    Call ``submit()`` to create a pending item and return its id immediately.
    Call ``run(item_id)`` to execute the pipeline synchronously (useful in
    tests or when the caller wants to await completion). In production, use
    ``submit_background(url, ...)`` which fires ``run()`` as an asyncio task.
    """

    def __init__(
        self,
        store: "KnowledgeStore",
        http_client: "httpx.AsyncClient",
        notifications: "NotificationStore",
        category_engine: "CategoryEngine",
        qmd_base_url: str = "",
        llm_base_url: str = "",
        max_concurrent: int = _INGEST_SEMAPHORE_SLOTS,
    ) -> None:
        self._store = store
        self._http_client = http_client
        self._notifications = notifications
        self._category_engine = category_engine
        self._qmd_base_url = qmd_base_url
        self._llm_base_url = llm_base_url
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def submit(
        self,
        url: str,
        title: str = "",
        text: str = "",
        categories: list[str] | None = None,
        source: str = "unknown",
    ) -> str:
        """Create a pending KnowledgeItem and return its id.

        Does not start the pipeline. Call ``run(item_id)`` or
        ``submit_background(...)`` to actually process the item.
        """
        source_type = resolve_source_type(url)
        monitor_config = dict(_DEFAULT_MONITOR.get(source_type, _DEFAULT_MONITOR["article"]))
        item_id = await self._store.add_item(
            source_type=source_type,
            source_url=url,
            title=title or url,
            author="",
            content=text,
            summary="",
            categories=categories or [],
            tags=[],
            metadata={"ingest_source": source},
            status="pending",
            monitor=monitor_config,
        )
        return item_id

    async def submit_background(
        self,
        url: str,
        title: str = "",
        text: str = "",
        categories: list[str] | None = None,
        source: str = "unknown",
    ) -> str:
        """Submit and immediately fire ``run()`` as a background asyncio task."""
        item_id = await self.submit(url=url, title=title, text=text, categories=categories, source=source)
        asyncio.create_task(self._run_safe(item_id))
        return item_id

    async def _run_safe(self, item_id: str) -> None:
        """Wrapper that enforces backpressure and catches all exceptions."""
        async with self._semaphore:
            try:
                await self.run(item_id)
            except Exception as exc:
                logger.exception("IngestPipeline background task failed for %s: %s", item_id, exc)
                await self._store.update_status(item_id, "error")

    async def run(self, item_id: str) -> None:
        """Execute all pipeline steps for an existing pending item."""
        item = await self._store.get_item(item_id)
        if item is None:
            logger.error("IngestPipeline.run: item %s not found", item_id)
            return

        await self._store.update_status(item_id, "processing")

        try:
            # Step 1: download content if not already provided
            content = item["content"]
            title = item["title"] if item["title"] != item["source_url"] else ""
            author = item["author"]
            metadata = dict(item["metadata"])

            if not content:
                content, title, author, metadata = await self._download(
                    item["source_type"], item["source_url"], title, metadata
                )

            # Step 2: categorise
            categories = item["categories"] or []
            if not categories:
                categories = await self._category_engine.categorise(
                    source_type=item["source_type"],
                    source_url=item["source_url"],
                    title=title or item["source_url"],
                    summary="",
                    metadata=metadata,
                )

            # Step 3: summarise via LLM (best-effort, non-fatal)
            summary = await self._summarise(title, content)

            # Step 4: embed via QMD (best-effort, non-fatal)
            await self._embed(item_id, title, content)

            # Step 5: write final data and mark ready
            await self._store.update_item(
                item_id,
                title=title or item["source_url"],
                author=author,
                content=content,
                summary=summary,
                categories=categories,
                metadata=metadata,
            )
            await self._store.update_status(item_id, "ready")

            # Step 6: notify subscribed agents
            await self._notify(item_id, title, categories)

        except Exception as exc:
            logger.exception("IngestPipeline.run failed for %s: %s", item_id, exc)
            await self._store.update_status(item_id, "error")

    # ------------------------------------------------------------------
    # Download step
    # ------------------------------------------------------------------

    async def _download(
        self, source_type: str, url: str, title: str, metadata: dict
    ) -> tuple[str, str, str, dict]:
        """Download content from the source. Returns (content, title, author, metadata).

        Currently implements article download via HTTP + readability extraction.
        Other source types return empty content so the pipeline can still proceed
        with whatever the caller provided (or mark the item as needing a platform
        adapter that will be added in later build steps).
        """
        if source_type == "article":
            return await self._download_article(url, title, metadata)
        if source_type == "reddit":
            from tinyagentos.knowledge_fetchers.reddit import (
                fetch_thread, flatten_to_text, extract_metadata,
            )
            post, comments = await fetch_thread(url, self._http_client)
            content = flatten_to_text(post, comments)
            new_meta = extract_metadata(post)
            metadata.update(new_meta)
            return content, post.title, post.author, metadata
        if source_type == "youtube":
            from tinyagentos.knowledge_fetchers.youtube import fetch
            result = await fetch(url)
            metadata.update(result.get("metadata", {}))
            return (
                result.get("content", ""),
                result.get("title", title),
                result.get("author", ""),
                metadata,
            )
        if source_type == "github":
            from tinyagentos.knowledge_fetchers.github import (
                parse_github_url, fetch_repo, fetch_issue, fetch_releases, extract_metadata as gh_meta,
            )
            owner, repo, content_type, number = parse_github_url(url)
            if content_type == "issue" or content_type == "pull":
                data = await fetch_issue(owner, repo, number, None, self._http_client)
                metadata.update(gh_meta(data, content_type))
                body = data.get("body", "")
                comments = "\n\n".join(c.get("body", "") for c in data.get("comments", []))
                return f"{body}\n\n---\n\n{comments}", data.get("title", title), data.get("author", ""), metadata
            elif content_type == "releases":
                releases = await fetch_releases(owner, repo, None, self._http_client)
                if releases:
                    r = releases[0]
                    metadata.update(gh_meta(r, "release"))
                    return r.get("body", ""), r.get("name", title), r.get("author", ""), metadata
            else:
                data = await fetch_repo(owner, repo, None, self._http_client)
                metadata.update(gh_meta(data, "repo"))
                return data.get("readme_content", ""), data.get("name", title), f"{owner}", metadata
        if source_type == "x":
            from tinyagentos.knowledge_fetchers.x import (
                fetch_tweet_ytdlp, stitch_thread_text, extract_metadata as x_meta,
            )
            tweet = await fetch_tweet_ytdlp(url)
            if tweet:
                metadata.update(x_meta(tweet))
                return tweet.get("text", ""), title, tweet.get("author", ""), metadata
            return "", title, "", metadata
        return "", title, "", metadata

    async def _download_article(
        self, url: str, title: str, metadata: dict
    ) -> tuple[str, str, str, dict]:
        """Fetch an article URL and extract readable text."""
        resp = await self._http_client.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text
        content = _extract_text_readability(html)
        if len(content) < _MIN_CONTENT_CHARS:
            logger.warning("Readability extraction returned short content for %s (%d chars)", url, len(content))
            # Screenshot fallback would go here in Phase 2
        # Try to extract title from <title> tag if not provided
        if not title:
            m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
            if m:
                title = m.group(1).strip()
        return content, title, "", metadata

    # ------------------------------------------------------------------
    # Summarise step
    # ------------------------------------------------------------------

    async def _summarise(self, title: str, content: str) -> str:
        """Request a 2-3 sentence summary from the LLM. Returns empty string on failure."""
        if not self._llm_base_url or not content:
            return ""
        truncated = content[:4000]  # stay within typical context limits
        prompt = (
            f"Summarise the following content in 2-3 sentences. "
            f"Be specific about what the content covers and who it is useful for.\n\n"
            f"Title: {title}\n\nContent:\n{truncated}"
        )
        try:
            resp = await self._http_client.post(
                f"{self._llm_base_url}/generate",
                json={"prompt": prompt, "max_tokens": 150},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("text", data.get("content", "")).strip()
        except Exception as exc:
            logger.warning("Summarise LLM call failed: %s", exc)
            return ""

    # ------------------------------------------------------------------
    # Embed step
    # ------------------------------------------------------------------

    async def _embed(self, item_id: str, title: str, content: str) -> None:
        """Send content to QMD for vector embedding into the 'knowledge' collection."""
        if not self._qmd_base_url or not content:
            return
        text_to_embed = f"{title}\n\n{content}"
        # Chunk if content is very long (simple fixed-size chunking)
        chunk_size = 2000
        chunks = [text_to_embed[i:i + chunk_size] for i in range(0, len(text_to_embed), chunk_size)]
        for seq, chunk in enumerate(chunks):
            try:
                await self._http_client.post(
                    f"{self._qmd_base_url}/ingest",
                    json={
                        "collection": "knowledge",
                        "path": f"knowledge/{item_id}/chunk_{seq}",
                        "title": title,
                        "body": chunk,
                    },
                    timeout=60,
                )
            except Exception as exc:
                logger.warning("QMD embed failed for item %s chunk %d: %s", item_id, seq, exc)

    # ------------------------------------------------------------------
    # Notify step
    # ------------------------------------------------------------------

    async def _notify(self, item_id: str, title: str, categories: list[str]) -> None:
        """Emit a notification for agents subscribed to the item's categories."""
        subs = await self._store.subscribers_for_categories(categories)
        if not subs:
            await self._notifications.emit_event(
                "knowledge.item.ready",
                title=f"New knowledge item: {title}",
                message=f"Item {item_id} is ready. Categories: {', '.join(categories) or 'none'}",
            )
            return
        for sub in subs:
            await self._notifications.emit_event(
                "knowledge.item.ready",
                title=f"New knowledge item for {sub['agent_name']}: {title}",
                message=f"Item {item_id} matches subscribed category '{sub['category']}'. auto_ingest={sub['auto_ingest']}",
            )
