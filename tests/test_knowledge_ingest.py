from __future__ import annotations
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from tinyagentos.knowledge_store import KnowledgeStore
from tinyagentos.knowledge_ingest import IngestPipeline, resolve_source_type


# --- URL resolution ---

def test_resolve_reddit():
    assert resolve_source_type("https://reddit.com/r/LocalLLaMA/comments/abc") == "reddit"
    assert resolve_source_type("https://www.reddit.com/r/Python/comments/xyz/") == "reddit"


def test_resolve_youtube():
    assert resolve_source_type("https://www.youtube.com/watch?v=abc123") == "youtube"
    assert resolve_source_type("https://youtu.be/abc123") == "youtube"


def test_resolve_x():
    assert resolve_source_type("https://x.com/user/status/123") == "x"
    assert resolve_source_type("https://twitter.com/user/status/456") == "x"


def test_resolve_github():
    assert resolve_source_type("https://github.com/some/repo") == "github"
    assert resolve_source_type("https://github.com/org/repo/issues/1") == "github"


def test_resolve_article_fallback():
    assert resolve_source_type("https://news.ycombinator.com/item?id=123") == "article"
    assert resolve_source_type("https://blog.example.com/some-post") == "article"


# --- IngestPipeline ---

@pytest_asyncio.fixture
async def store(tmp_path):
    s = KnowledgeStore(tmp_path / "knowledge.db", media_dir=tmp_path / "media")
    await s.init()
    yield s
    await s.close()


@pytest.fixture
def mock_http():
    client = AsyncMock()
    # Default: return minimal HTML for article fetch
    response = MagicMock()
    response.status_code = 200
    response.text = "<html><body><article><p>This is the main article content with enough text to pass the quality threshold.</p></article></body></html>"
    response.raise_for_status = MagicMock()
    client.get = AsyncMock(return_value=response)
    return client


@pytest_asyncio.fixture
async def pipeline(store, mock_http):
    notif = AsyncMock()
    notif.emit_event = AsyncMock()
    cat_engine = AsyncMock()
    cat_engine.categorise = AsyncMock(return_value=["Tech"])
    p = IngestPipeline(
        store=store,
        http_client=mock_http,
        notifications=notif,
        category_engine=cat_engine,
        qmd_base_url="",  # QMD disabled for unit tests
        llm_base_url="",  # LLM disabled for unit tests
    )
    return p


@pytest.mark.asyncio
async def test_ingest_creates_pending_item(pipeline, store):
    item_id = await pipeline.submit(
        url="https://example.com/article",
        title="",
        text="",
        categories=[],
        source="test",
    )
    assert item_id
    item = await store.get_item(item_id)
    assert item is not None
    assert item["status"] in ("pending", "processing", "ready", "error")
    assert item["source_url"] == "https://example.com/article"


@pytest.mark.asyncio
async def test_ingest_article_sets_ready(pipeline, store):
    item_id = await pipeline.submit(
        url="https://example.com/article",
        title="",
        text="",
        categories=[],
        source="test",
    )
    # Run the pipeline inline (not background) for testing
    await pipeline.run(item_id)
    item = await store.get_item(item_id)
    assert item["status"] == "ready"
    assert item["source_type"] == "article"


@pytest.mark.asyncio
async def test_ingest_with_text_override(pipeline, store):
    """When text is provided directly, skip HTTP download."""
    item_id = await pipeline.submit(
        url="https://example.com/article",
        title="My Title",
        text="Pre-provided content that is long enough to pass quality checks.",
        categories=["Tech"],
        source="share-sheet",
    )
    await pipeline.run(item_id)
    item = await store.get_item(item_id)
    assert item["status"] == "ready"
    assert "Pre-provided content" in item["content"]


@pytest.mark.asyncio
async def test_ingest_notifies_on_ready(pipeline, store):
    item_id = await pipeline.submit(
        url="https://example.com/article",
        title="",
        text="",
        categories=[],
        source="test",
    )
    await pipeline.run(item_id)
    pipeline._notifications.emit_event.assert_awaited()


@pytest.mark.asyncio
async def test_ingest_sets_error_on_failure(pipeline, store):
    pipeline._http_client.get = AsyncMock(side_effect=Exception("network failure"))
    item_id = await pipeline.submit(
        url="https://example.com/failing",
        title="",
        text="",
        categories=[],
        source="test",
    )
    await pipeline.run(item_id)
    item = await store.get_item(item_id)
    assert item["status"] == "error"


# ------------------------------------------------------------------
# Task 9: LLM + QMD integration tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_summarise_called_when_llm_url_set(store):
    """When llm_base_url is set, _summarise should be called and stored."""
    from tinyagentos.knowledge_ingest import IngestPipeline

    llm_response = AsyncMock()
    llm_response.status_code = 200
    llm_response.json = MagicMock(return_value={"text": "This is a generated summary."})
    llm_response.raise_for_status = MagicMock()

    article_response = AsyncMock()
    article_response.status_code = 200
    article_response.text = "<html><body><p>Long enough article body text content here for testing purposes.</p></body></html>"
    article_response.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    # First call is article fetch, second call is LLM summarise
    mock_http.get = AsyncMock(return_value=article_response)
    mock_http.post = AsyncMock(return_value=llm_response)

    notif = AsyncMock()
    notif.emit_event = AsyncMock()
    cat_engine = AsyncMock()
    cat_engine.categorise = AsyncMock(return_value=["Tech"])

    pipeline = IngestPipeline(
        store=store,
        http_client=mock_http,
        notifications=notif,
        category_engine=cat_engine,
        qmd_base_url="",  # disable embed for this test
        llm_base_url="http://localhost:8080",
    )

    item_id = await pipeline.submit(
        url="https://example.com/summarise-test",
        title="",
        text="",
        categories=[],
        source="test",
    )
    await pipeline.run(item_id)

    item = await store.get_item(item_id)
    assert item["summary"] == "This is a generated summary."


@pytest.mark.asyncio
async def test_embed_called_when_qmd_url_set(store):
    """When qmd_base_url is set, the /ingest endpoint should be called with collection=knowledge."""
    from tinyagentos.knowledge_ingest import IngestPipeline

    qmd_response = AsyncMock()
    qmd_response.status_code = 200
    qmd_response.raise_for_status = AsyncMock()

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(side_effect=Exception("no HTTP in this test"))
    mock_http.post = AsyncMock(return_value=qmd_response)

    notif = AsyncMock()
    notif.emit_event = AsyncMock()
    cat_engine = AsyncMock()
    cat_engine.categorise = AsyncMock(return_value=[])

    pipeline = IngestPipeline(
        store=store,
        http_client=mock_http,
        notifications=notif,
        category_engine=cat_engine,
        qmd_base_url="http://localhost:7832",
        llm_base_url="",
    )

    item_id = await pipeline.submit(
        url="https://example.com/embed-test",
        title="Embed Test",
        text="Content long enough to trigger embedding pipeline call here.",
        categories=[],
        source="test",
    )
    await pipeline.run(item_id)

    # Verify /ingest was called on the QMD base URL
    calls = [str(call) for call in mock_http.post.call_args_list]
    assert any("ingest" in c for c in calls)


# ------------------------------------------------------------------
# Ingest backpressure semaphore (#659)
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_semaphore_is_created_with_default_slots(pipeline):
    """Pipeline should have a Semaphore with the default slot count."""
    import asyncio
    assert isinstance(pipeline._semaphore, asyncio.Semaphore)
    # Default is 5 as defined by _INGEST_SEMAPHORE_SLOTS.
    assert pipeline._semaphore._value == 5


@pytest.mark.asyncio
async def test_semaphore_custom_max_concurrent(store, mock_http):
    """max_concurrent kwarg controls the Semaphore slot count."""
    from tinyagentos.knowledge_ingest import IngestPipeline
    notif = AsyncMock()
    notif.emit_event = AsyncMock()
    cat_engine = AsyncMock()
    cat_engine.categorise = AsyncMock(return_value=[])
    p = IngestPipeline(
        store=store,
        http_client=mock_http,
        notifications=notif,
        category_engine=cat_engine,
        max_concurrent=2,
    )
    assert p._semaphore._value == 2


@pytest.mark.asyncio
async def test_semaphore_limits_concurrent_tasks(store, mock_http):
    """At most max_concurrent tasks run the pipeline body simultaneously."""
    import asyncio
    from tinyagentos.knowledge_ingest import IngestPipeline

    active: list[int] = []
    peak: list[int] = []

    original_run = None

    async def counting_run(self, item_id: str) -> None:
        active.append(1)
        peak.append(len(active))
        await asyncio.sleep(0)  # yield to let other tasks start
        active.pop()
        # call real pipeline logic via store update only
        await self._store.update_status(item_id, "ready")

    notif = AsyncMock()
    notif.emit_event = AsyncMock()
    cat_engine = AsyncMock()
    cat_engine.categorise = AsyncMock(return_value=[])

    p = IngestPipeline(
        store=store,
        http_client=mock_http,
        notifications=notif,
        category_engine=cat_engine,
        max_concurrent=2,
    )

    # Submit 6 items
    ids = []
    for i in range(6):
        item_id = await p.submit(
            url=f"https://example.com/batch-{i}",
            title=f"Batch {i}",
            text="Enough content to proceed.",
            categories=["Test"],
            source="test",
        )
        ids.append(item_id)

    # Patch run so we can count concurrency
    with patch.object(IngestPipeline, "run", counting_run):
        tasks = [asyncio.create_task(p._run_safe(item_id)) for item_id in ids]
        await asyncio.gather(*tasks)

    assert max(peak) <= 2, f"Peak concurrency {max(peak)} exceeded semaphore limit 2"


@pytest.mark.asyncio
async def test_categories_from_caller_are_preserved(store):
    """When categories are provided at submit time, they bypass the engine."""
    from tinyagentos.knowledge_ingest import IngestPipeline

    notif = AsyncMock()
    notif.emit_event = AsyncMock()
    cat_engine = AsyncMock()
    cat_engine.categorise = AsyncMock(return_value=["Wrong"])  # should not be called

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(side_effect=Exception("no HTTP"))

    pipeline = IngestPipeline(
        store=store,
        http_client=mock_http,
        notifications=notif,
        category_engine=cat_engine,
        qmd_base_url="",
        llm_base_url="",
    )

    item_id = await pipeline.submit(
        url="https://example.com/precategorised",
        title="Pre-categorised",
        text="Content long enough for the pipeline to keep.",
        categories=["AI/ML", "Rockchip"],
        source="test",
    )
    await pipeline.run(item_id)

    cat_engine.categorise.assert_not_awaited()
    item = await store.get_item(item_id)
    assert "AI/ML" in item["categories"]
    assert "Rockchip" in item["categories"]
