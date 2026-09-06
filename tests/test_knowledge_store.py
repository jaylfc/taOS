from __future__ import annotations
import json
import time
import pytest
import pytest_asyncio
from pathlib import Path
from tinyagentos.knowledge_store import KnowledgeStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = KnowledgeStore(tmp_path / "knowledge.db", media_dir=tmp_path / "knowledge-media")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_add_and_get_item(store):
    item_id = await store.add_item(
        source_type="article",
        source_url="https://example.com/post",
        title="Test Article",
        author="tester",
        content="Full text of the article goes here.",
        summary="A brief summary.",
        categories=["Tech"],
        tags=["python"],
        metadata={"word_count": 8},
    )
    assert item_id  # non-empty string
    item = await store.get_item(item_id)
    assert item is not None
    assert item["title"] == "Test Article"
    assert item["source_type"] == "article"
    assert item["status"] == "pending"
    assert item["categories"] == ["Tech"]
    assert item["tags"] == ["python"]


@pytest.mark.asyncio
async def test_get_item_not_found(store):
    item = await store.get_item("nonexistent-id")
    assert item is None


@pytest.mark.asyncio
async def test_update_status(store):
    item_id = await store.add_item(
        source_type="article",
        source_url="https://example.com/post2",
        title="Another Article",
        author="tester",
        content="Content.",
        summary="Summary.",
        categories=[],
        tags=[],
        metadata={},
    )
    await store.update_status(item_id, "ready")
    item = await store.get_item(item_id)
    assert item["status"] == "ready"


@pytest.mark.asyncio
async def test_list_items(store):
    for i in range(3):
        await store.add_item(
            source_type="article",
            source_url=f"https://example.com/{i}",
            title=f"Article {i}",
            author="tester",
            content="Content.",
            summary="Summary.",
            categories=["Tech"],
            tags=[],
            metadata={},
        )
    items = await store.list_items(limit=10)
    assert len(items) == 3


@pytest.mark.asyncio
async def test_delete_item(store):
    item_id = await store.add_item(
        source_type="article",
        source_url="https://example.com/del",
        title="To Delete",
        author="tester",
        content="Content.",
        summary="Summary.",
        categories=[],
        tags=[],
        metadata={},
    )
    deleted = await store.delete_item(item_id)
    assert deleted is True
    assert await store.get_item(item_id) is None


@pytest.mark.asyncio
async def test_search_fts(store):
    await store.add_item(
        source_type="article",
        source_url="https://example.com/async",
        title="Async Python Guide",
        author="dev",
        content="asyncio event loop coroutine await",
        summary="Guide to async Python.",
        categories=["Tech"],
        tags=[],
        metadata={},
    )
    await store.add_item(
        source_type="article",
        source_url="https://example.com/rust",
        title="Rust Memory Safety",
        author="dev",
        content="ownership borrowing lifetimes",
        summary="Guide to Rust.",
        categories=["Tech"],
        tags=[],
        metadata={},
    )
    results = await store.search_fts("asyncio")
    assert len(results) == 1
    assert results[0]["title"] == "Async Python Guide"


@pytest.mark.asyncio
async def test_snapshot_roundtrip(store):
    item_id = await store.add_item(
        source_type="reddit",
        source_url="https://reddit.com/r/test/comments/abc",
        title="Thread",
        author="u/tester",
        content="Original text.",
        summary="Summary.",
        categories=[],
        tags=[],
        metadata={},
    )
    snap_id = await store.add_snapshot(
        item_id, "deadbeef",
        diff_json={"new_comments": 2},
        metadata_json={"upvotes": 100},
    )
    assert snap_id > 0
    snaps = await store.list_snapshots(item_id)
    assert len(snaps) == 1
    assert snaps[0]["content_hash"] == "deadbeef"
    assert snaps[0]["diff_json"]["new_comments"] == 2


@pytest.mark.asyncio
async def test_category_rules_crud(store):
    rule_id = await store.add_rule(
        pattern="LocalLLaMA", match_on="subreddit", category="AI/ML", priority=10
    )
    assert rule_id > 0
    rules = await store.list_rules()
    assert len(rules) == 1
    assert rules[0]["category"] == "AI/ML"
    deleted = await store.delete_rule(rule_id)
    assert deleted is True
    assert await store.list_rules() == []


@pytest.mark.asyncio
async def test_agent_subscriptions(store):
    await store.set_subscription("research-agent", "AI/ML", auto_ingest=True)
    await store.set_subscription("research-agent", "Rockchip", auto_ingest=False)
    subs = await store.list_subscriptions("research-agent")
    assert len(subs) == 2
    categories = {s["category"] for s in subs}
    assert categories == {"AI/ML", "Rockchip"}

    matching = await store.subscribers_for_categories(["AI/ML"])
    assert len(matching) == 1
    assert matching[0]["auto_ingest"] is True

    deleted = await store.delete_subscription("research-agent", "Rockchip")
    assert deleted is True
    subs = await store.list_subscriptions("research-agent")
    assert len(subs) == 1


@pytest.mark.asyncio
async def test_list_items_filter_by_category(store):
    await store.add_item(
        source_type="article",
        source_url="https://example.com/ai",
        title="AI Article",
        author="tester",
        content="content",
        summary="summary",
        categories=["AI/ML"],
        tags=[],
        metadata={},
    )
    await store.add_item(
        source_type="article",
        source_url="https://example.com/other",
        title="Other Article",
        author="tester",
        content="content",
        summary="summary",
        categories=["Other"],
        tags=[],
        metadata={},
    )
    results = await store.list_items(category="AI/ML")
    assert len(results) == 1
    assert results[0]["title"] == "AI Article"


# ------------------------------------------------------------------
# FTS5 injection fix (#659)
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_fts_bare_quote_does_not_raise(store):
    """A lone double-quote must not cause an OperationalError."""
    await store.add_item(
        source_type="article",
        source_url="https://example.com/safe",
        title="Safe",
        author="dev",
        content="safe content",
        summary="",
        categories=[],
        tags=[],
        metadata={},
    )
    results = await store.search_fts('"')
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_search_fts_operators_are_literal(store):
    """FTS5 boolean operators in query are not interpreted as operators."""
    await store.add_item(
        source_type="article",
        source_url="https://example.com/a",
        title="Alpha",
        author="dev",
        content="alpha text here",
        summary="",
        categories=[],
        tags=[],
        metadata={},
    )
    await store.add_item(
        source_type="article",
        source_url="https://example.com/b",
        title="Beta",
        author="dev",
        content="beta text here",
        summary="",
        categories=[],
        tags=[],
        metadata={},
    )
    # As a phrase "alpha AND beta" should not match either row.
    results = await store.search_fts("alpha AND beta")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_search_fts_prefix_wildcard_is_literal(store):
    """A trailing * must not be treated as a wildcard prefix operator."""
    await store.add_item(
        source_type="article",
        source_url="https://example.com/hello",
        title="Hello World",
        author="dev",
        content="hello world",
        summary="",
        categories=[],
        tags=[],
        metadata={},
    )
    results = await store.search_fts("hell*")
    assert len(results) == 0


# ------------------------------------------------------------------
# Category filter via json_each() (#659)
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_category_filter_no_false_positive_substring(store):
    """'AI' must not match a row whose category is 'AI/ML' via substring."""
    await store.add_item(
        source_type="article",
        source_url="https://example.com/ai-ml",
        title="AI/ML Article",
        author="tester",
        content="content",
        summary="",
        categories=["AI/ML"],
        tags=[],
        metadata={},
    )
    results = await store.list_items(category="AI")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_category_filter_no_false_positive_suffix(store):
    """'ML' must not match a row whose category is 'AI/ML' via substring."""
    await store.add_item(
        source_type="article",
        source_url="https://example.com/ai-ml-2",
        title="AI/ML Article 2",
        author="tester",
        content="content",
        summary="",
        categories=["AI/ML"],
        tags=[],
        metadata={},
    )
    results = await store.list_items(category="ML")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_category_filter_exact_match(store):
    """json_each filter returns the row whose categories contain the exact value."""
    await store.add_item(
        source_type="article",
        source_url="https://example.com/exact",
        title="Exact Match",
        author="tester",
        content="content",
        summary="",
        categories=["Rockchip", "AI/ML"],
        tags=[],
        metadata={},
    )
    results = await store.list_items(category="Rockchip")
    assert len(results) == 1
    assert results[0]["title"] == "Exact Match"
