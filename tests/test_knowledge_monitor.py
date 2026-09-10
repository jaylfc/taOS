from __future__ import annotations
import time
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
from tinyagentos.knowledge_store import KnowledgeStore
from tinyagentos.knowledge_monitor import MonitorService, compute_next_interval


@pytest_asyncio.fixture
async def store(tmp_path):
    s = KnowledgeStore(tmp_path / "knowledge.db", media_dir=tmp_path / "media")
    await s.init()
    yield s
    await s.close()


# --- Smart decay logic ---

def test_decay_on_no_change():
    """No change detected: multiply interval by decay_rate, floor at 86400."""
    new_interval = compute_next_interval(
        current_interval=3600,
        decay_rate=1.5,
        changed=False,
        base_frequency=3600,
        stop_after_days=30,
    )
    assert new_interval == int(3600 * 1.5)


def test_reset_on_change():
    """Change detected: reset to base_frequency."""
    new_interval = compute_next_interval(
        current_interval=7200,
        decay_rate=1.5,
        changed=True,
        base_frequency=3600,
        stop_after_days=30,
    )
    assert new_interval == 3600


def test_floor_at_24_hours():
    """Interval must never exceed 86400 seconds (24 hours floor for the next poll gap)."""
    new_interval = compute_next_interval(
        current_interval=80000,
        decay_rate=2.0,
        changed=False,
        base_frequency=3600,
        stop_after_days=30,
    )
    assert new_interval == 86400


def test_stop_after_idle_threshold():
    """stop_after_days no longer stops polling — interval is clamped, never None."""
    new_interval = compute_next_interval(
        current_interval=86400 * 29,
        decay_rate=2.0,
        changed=False,
        base_frequency=3600,
        stop_after_days=30,
    )
    # Sub-daily source (base_frequency < 86400) caps at 24h; never returns None
    assert new_interval is not None
    assert new_interval > 0
    assert new_interval == 86400


def test_decay_floors_at_30_days():
    """Decay should never push interval beyond 30 days (2592000 seconds)."""
    new_interval = compute_next_interval(
        current_interval=2000000,
        decay_rate=2.0,
        changed=False,
        base_frequency=86400,
        stop_after_days=0,
    )
    assert new_interval is not None
    assert new_interval <= 2592000, f"Interval {new_interval} exceeds 30-day floor"


def test_decay_does_not_stop_automatically():
    """Items should never stop polling — interval stays at floor, never becomes 0."""
    new_interval = compute_next_interval(
        current_interval=2592000,
        decay_rate=2.0,
        changed=False,
        base_frequency=86400,
        stop_after_days=14,
    )
    assert new_interval is not None, "Interval should not be None (stopped)"
    assert new_interval > 0, "Interval should not be zero (stopped)"
    assert new_interval == 2592000, "Interval should stay at 30-day floor"


def test_pinned_item_uses_base_frequency():
    """Pinned items always return base_frequency regardless of change."""
    new_interval = compute_next_interval(
        current_interval=86400,
        decay_rate=2.0,
        changed=False,
        base_frequency=3600,
        stop_after_days=30,
        pinned=True,
    )
    assert new_interval == 3600


# --- Due-for-poll detection ---

@pytest.mark.asyncio
async def test_items_due_for_poll(store):
    """Items whose last_poll + current_interval <= now should be returned as due."""
    # Item with last_poll far in the past
    item_id = await store.add_item(
        source_type="reddit",
        source_url="https://reddit.com/r/test/comments/abc",
        title="Thread",
        author="u/tester",
        content="text",
        summary="summary",
        categories=[],
        tags=[],
        metadata={},
        status="ready",
        monitor={"frequency": 3600, "decay_rate": 1.5, "stop_after_days": 30,
                  "pinned": False, "last_poll": time.time() - 7200, "current_interval": 3600},
    )
    svc = MonitorService(store=store, http_client=AsyncMock())
    due = await svc.get_due_items()
    assert any(d["id"] == item_id for d in due)


@pytest.mark.asyncio
async def test_items_not_due_yet(store):
    """Items polled recently should not appear in due list."""
    item_id = await store.add_item(
        source_type="reddit",
        source_url="https://reddit.com/r/test/comments/xyz",
        title="Recent Thread",
        author="u/tester",
        content="text",
        summary="summary",
        categories=[],
        tags=[],
        metadata={},
        status="ready",
        monitor={"frequency": 3600, "decay_rate": 1.5, "stop_after_days": 30,
                  "pinned": False, "last_poll": time.time(), "current_interval": 3600},
    )
    svc = MonitorService(store=store, http_client=AsyncMock())
    due = await svc.get_due_items()
    assert not any(d["id"] == item_id for d in due)


@pytest.mark.asyncio
async def test_poll_item_updates_monitor_config(store):
    """After a poll, last_poll is updated and current_interval reflects decay."""
    item_id = await store.add_item(
        source_type="article",
        source_url="https://example.com/article",
        title="Article",
        author="",
        content="original content",
        summary="summary",
        categories=[],
        tags=[],
        metadata={},
        status="ready",
        monitor={"frequency": 86400, "decay_rate": 2.0, "stop_after_days": 14,
                  "pinned": False, "last_poll": 0, "current_interval": 86400},
    )
    response = AsyncMock()
    response.status_code = 200
    response.text = "original content"  # no change
    response.raise_for_status = AsyncMock()
    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=response)

    svc = MonitorService(store=store, http_client=mock_http)
    await svc.poll_item(item_id)

    item = await store.get_item(item_id)
    assert item["monitor"]["last_poll"] > 0
    # No change -> interval decays
    assert item["monitor"]["current_interval"] == int(86400 * 2.0)


# ------------------------------------------------------------------
# S2-19: size cap and content-type gate on monitor fetches
# ------------------------------------------------------------------


class _TrackedResponse:
    """Mock response that tracks how many bytes are consumed."""

    def __init__(self, chunks, content_type="text/html"):
        self._chunks = list(chunks)
        self._all_text = b"".join(self._chunks).decode("utf-8", errors="replace")
        self.status_code = 200
        self.headers = {"content-type": content_type}
        self.is_redirect = False
        self.bytes_read = 0

    def raise_for_status(self):
        pass

    async def aiter_bytes(self, chunk_size=8192):
        for chunk in self._chunks:
            self.bytes_read += len(chunk)
            yield chunk

    @property
    def text(self):
        self.bytes_read = len(self._all_text.encode("utf-8"))
        return self._all_text

    @property
    def encoding(self):
        return "utf-8"


@pytest.mark.asyncio
async def test_fetch_article_rejects_oversized_body(store):
    """Monitor fetch must not buffer a body larger than the cap."""
    chunk_size = 8192
    num_chunks = 2000  # ~15 MB total, over the 10 MB default cap
    chunks = [b"x" * chunk_size] * num_chunks
    resp = _TrackedResponse(chunks, content_type="text/html")

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=resp)

    item_id = await store.add_item(
        source_type="article",
        source_url="https://example.com/large",
        title="Article",
        author="",
        content="original content",
        summary="summary",
        categories=[],
        tags=[],
        metadata={},
        status="ready",
        monitor={"frequency": 86400, "decay_rate": 2.0, "stop_after_days": 14,
                  "pinned": False, "last_poll": 0, "current_interval": 86400},
    )
    svc = MonitorService(store=store, http_client=mock_http)
    new_content, changed = await svc._fetch_article(await store.get_item(item_id))

    assert new_content == ""
    assert changed is False
    assert resp.bytes_read <= 10 * 1024 * 1024 + chunk_size, (
        f"Oversized body should not be fully buffered, but {resp.bytes_read} bytes were read"
    )


@pytest.mark.asyncio
async def test_fetch_article_rejects_non_text_content_type(store):
    """Monitor fetch must reject non-text content-types."""
    resp = _TrackedResponse([b"binary data"], content_type="application/octet-stream")

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=resp)

    item_id = await store.add_item(
        source_type="article",
        source_url="https://example.com/bin",
        title="Article",
        author="",
        content="original content",
        summary="summary",
        categories=[],
        tags=[],
        metadata={},
        status="ready",
        monitor={"frequency": 86400, "decay_rate": 2.0, "stop_after_days": 14,
                  "pinned": False, "last_poll": 0, "current_interval": 86400},
    )
    svc = MonitorService(store=store, http_client=mock_http)
    new_content, changed = await svc._fetch_article(await store.get_item(item_id))

    assert new_content == ""
    assert changed is False
    assert resp.bytes_read == 0, "Non-text response should not be buffered at all"


# ------------------------------------------------------------------
# R2-12: FIRST POLL BUGS: polling limit, raw HTML overwrite, baseline on fail, stop_after_days
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_polls_all_ready_items(store):
    """R2-12 first bug: MonitorService.get_due_items uses limit=50, missing items beyond 50.

    list_items(status="ready") defaults to limit=50, so items 51+ never polled.
    """
    # Create 60 items with identical monitor configs so all become due at once
    item_count = 60
    item_ids = []
    now = time.time()
    for i in range(item_count):
        item_id = await store.add_item(
            source_type="article",
            source_url=f"https://example.com/article{i}",
            title=f"Article {i}",
            author="",
            content=f"Original content {i}",
            summary="summary",
            categories=[],
            tags=[],
            metadata={},
            status="ready",
            monitor={
                "frequency": 3600,
                "decay_rate": 1.5,
                "stop_after_days": 14,
                "pinned": False,
                "last_poll": 0,
                "current_interval": 3600,
                "last_hash": "",
            },
        )
        item_ids.append(item_id)

    svc = MonitorService(store=store, http_client=AsyncMock())
    due = await svc.get_due_items()

    # With bug: due contains at most 50 items (list_items default limit)
    # After fix: due should contain all 60 items
    assert len(due) == item_count, f"Expected {item_count} due items, got {len(due)}"


@pytest.mark.asyncio
async def test_monitor_does_not_overwrite_text_with_raw_html(store):
    """R2-12 second bug: First poll stores raw HTML over extracted text.

    _fetch_article returns (new_content, changed), but line 139-140 writes new_content
    (raw HTML) into item content, not the extracted text from the ingest extractor.
    """
    item_id = await store.add_item(
        source_type="article",
        source_url="https://example.com/article",
        title="Test Article",
        author="",
        content="original content",
        summary="summary",
        categories=[],
        tags=[],
        metadata={},
        status="ready",
        monitor={
            "frequency": 86400,
            "decay_rate": 2.0,
            "stop_after_days": 14,
            "pinned": False,
            "last_poll": 0,
            "current_interval": 86400,
            "last_hash": "",
        },
    )
    # Mock the HTTP client to return raw HTML that differs from original
    raw_html = "<html><body>Raw HTML content</body></html>"
    
    response = AsyncMock()
    response.status_code = 200
    response.headers = {"content-type": "text/html"}
    response.encoding = "utf-8"
    
    async def mock_aiter_bytes(chunk_size=8192):
        yield raw_html.encode("utf-8")
    
    response.aiter_bytes = mock_aiter_bytes
    response.raise_for_status = lambda: None
    
    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=response)

    svc = MonitorService(store=store, http_client=mock_http)

    # First poll: _fetch_article returns raw HTML
    await svc.poll_item(item_id)

    item = await store.get_item(item_id)
    # After fix: content is updated with extracted text, never raw HTML
    assert item["content"] != raw_html, "Content must not be raw HTML"
    assert "Raw HTML content" in item["content"], (
        f"Content should contain extracted text, got: {item['content']}"
    )


@pytest.mark.asyncio
async def test_monitor_does_not_update_baseline_on_failed_fetch(store):
    """R2-12 third bug: Failed fetch sets baseline hash to sha256("").

    _fetch_article returns ("", False) on failure. This results in
    content_hash = sha256("") being stored at line 128 and line 153.
    """
    item_id = await store.add_item(
        source_type="article",
        source_url="https://example.com/article",
        title="Test Article",
        author="",
        content="original content",
        summary="summary",
        categories=[],
        tags=[],
        metadata={},
        status="ready",
        monitor={
            "frequency": 86400,
            "decay_rate": 2.0,
            "stop_after_days": 14,
            "pinned": False,
            "last_poll": 0,
            "current_interval": 86400,
            "last_hash": "a" * 64,  # sha256 of "a" * 64
        },
    )
    # Mock a failed fetch
    response = AsyncMock()
    response.raise_for_status = AsyncMock(side_effect=Exception("Network error"))
    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=response)

    svc = MonitorService(store=store, http_client=mock_http)
    await svc.poll_item(item_id)

    item = await store.get_item(item_id)
    # Bug: last_hash becomes sha256("")
    # After fix: last_hash should remain unchanged (skip baseline update)
    assert item["monitor"]["last_hash"] == "a" * 64, "Baseline hash should not change on failure"


# ------------------------------------------------------------------
# RED-FIRST: R2-12 follow-up tests for the three remaining bugs
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_items_uses_limit_offset_in_sql(store):
    """R2-12 fix-forward: list_items must push LIMIT/OFFSET into SQL.

    The branch removed LIMIT/OFFSET from the SQL and slices in Python,
    so every caller loads the whole knowledge table.
    """
    # Populate with enough rows that Python-slicing would be expensive
    for i in range(60):
        await store.add_item(
            source_type="article",
            source_url=f"https://example.com/article{i}",
            title=f"Article {i}",
            author="",
            content=f"content {i}",
            summary="summary",
            categories=[],
            tags=[],
            metadata={},
            status="ready",
            monitor={"frequency": 86400, "decay_rate": 2.0, "stop_after_days": 14,
                     "pinned": False, "last_poll": 0, "current_interval": 86400},
        )

    executed_sqls = []
    original_execute = store._db.execute

    async def spy_execute(sql, params=None):
        executed_sqls.append(sql)
        return await original_execute(sql, params)

    store._db.execute = spy_execute

    try:
        result = await store.list_items(status="ready", limit=100, offset=0)
    finally:
        store._db.execute = original_execute

    list_sql_calls = [s for s in executed_sqls if "FROM knowledge_items" in s]
    assert list_sql_calls, "No SELECT from knowledge_items was executed"
    sql = list_sql_calls[-1]
    assert "LIMIT ?" in sql, f"SQL must contain LIMIT ?, got: {sql}"
    assert "OFFSET ?" in sql, f"SQL must contain OFFSET ?, got: {sql}"


@pytest.mark.asyncio
async def test_monitor_refreshes_content_with_extracted_text(store):
    """R2-12 fix-forward: poll must store extracted text, not raw HTML.

    _fetch_article returns raw HTML text bytes. The fix must run the same
    extractor the Library ingest path uses and store only the extracted text.
    """
    raw_html = "<html><head><title>X</title></head><body><p>Extracted text content here</p></body></html>"

    response = AsyncMock()
    response.status_code = 200
    response.headers = {"content-type": "text/html"}
    response.encoding = "utf-8"

    async def mock_aiter_bytes(chunk_size=8192):
        yield raw_html.encode("utf-8")

    response.aiter_bytes = mock_aiter_bytes
    response.raise_for_status = lambda: None

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=response)

    item_id = await store.add_item(
        source_type="article",
        source_url="https://example.com/article",
        title="Test Article",
        author="",
        content="old",
        summary="summary",
        categories=[],
        tags=[],
        metadata={},
        status="ready",
        monitor={
            "frequency": 86400,
            "decay_rate": 2.0,
            "stop_after_days": 14,
            "pinned": False,
            "last_poll": 0,
            "current_interval": 86400,
            "last_hash": "",
        },
    )
    svc = MonitorService(store=store, http_client=mock_http)
    await svc.poll_item(item_id)

    item = await store.get_item(item_id)
    assert item["content"] != raw_html, "Content must not be raw HTML"
    assert item["content"] != "old", "Content must be refreshed from the fetched HTML"
    assert "Extracted text content here" in item["content"], (
        f"Content should contain extracted text, got: {item['content']}"
    )


@pytest.mark.asyncio
async def test_stop_after_days_prevents_polling_old_items(store):
    """R2-12 fix-forward: items older than stop_after_days must not be polled and must be marked stopped."""
    old_ts = time.time() - (31 * 86400)  # 31 days ago
    item_id = await store.add_item(
        source_type="article",
        source_url="https://example.com/article",
        title="Old Article",
        author="",
        content="old content",
        summary="summary",
        categories=[],
        tags=[],
        metadata={},
        status="ready",
        monitor={
            "frequency": 86400,
            "decay_rate": 2.0,
            "stop_after_days": 30,
            "pinned": False,
            "last_poll": 0,
            "current_interval": 86400,
            "last_hash": "",
        },
    )
    # Override created_at to make the item old
    await store._db.execute(
        "UPDATE knowledge_items SET created_at = ? WHERE id = ?",
        (old_ts, item_id),
    )
    await store._db.commit()

    svc = MonitorService(store=store, http_client=AsyncMock())
    due = await svc.get_due_items()
    assert not any(d["id"] == item_id for d in due), "Old item should not be due for polling"

    item = await store.get_item(item_id)
    assert item["status"] == "stopped", f"Old item should be marked stopped, got {item['status']}"
