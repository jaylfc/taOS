import pytest
import pytest_asyncio
from unittest.mock import patch
from tinyagentos.user_memory import UserMemoryStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = UserMemoryStore(tmp_path / "user_mem.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_save_and_search(store):
    h = await store.save_chunk("user", "This is a test snippet about python", "Python Note", "snippets")
    assert h
    results = await store.search("user", "python")
    assert len(results) == 1
    assert results[0]["content"] == "This is a test snippet about python"


@pytest.mark.asyncio
async def test_browse_by_collection(store):
    await store.save_chunk("user", "Note one", "Title 1", "notes")
    await store.save_chunk("user", "Snippet one", "Title 2", "snippets")
    notes = await store.browse("user", collection="notes")
    assert len(notes) == 1
    assert notes[0]["collection"] == "notes"


@pytest.mark.asyncio
async def test_stats(store):
    await store.save_chunk("user", "a", "1", "notes")
    await store.save_chunk("user", "b", "2", "notes")
    await store.save_chunk("user", "c", "3", "snippets")
    stats = await store.get_stats("user")
    assert stats["total"] == 3
    assert stats["collections"]["notes"] == 2


@pytest.mark.asyncio
async def test_delete(store):
    h = await store.save_chunk("user", "delete me", "gone", "snippets")
    assert await store.delete_chunk("user", h) is True
    results = await store.search("user", "delete")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_settings(store):
    settings = await store.get_settings("user")
    assert settings["capture_notes"] is True
    await store.update_settings("user", {"capture_notes": False})
    settings = await store.get_settings("user")
    assert settings["capture_notes"] is False


# ------------------------------------------------------------------
# FTS5 injection fix (#659)
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fts5_double_quote_does_not_break_search(store):
    """A bare double-quote in the query must not raise or return wrong rows."""
    await store.save_chunk("user", "safe content here", "Safe Title", "snippets")
    # Prior to the fix this raised an OperationalError (malformed FTS5 query).
    results = await store.search("user", '"')
    # No crash; may return 0 results but must not raise.
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_fts5_operator_keywords_are_literal(store):
    """FTS5 operators like AND/OR/NOT must not alter the query semantics."""
    await store.save_chunk("user", "alpha content", "A", "snippets")
    await store.save_chunk("user", "beta content", "B", "snippets")
    # "alpha AND beta" as a phrase should match neither row (phrase not present).
    results = await store.search("user", "alpha AND beta")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_fts5_star_operator_is_literal(store):
    """A trailing * must not be treated as a prefix wildcard."""
    await store.save_chunk("user", "hello world", "Hello", "snippets")
    # "hell*" as a phrase literal should not match "hello"; would match if
    # the star were interpreted as a wildcard by FTS5.
    results = await store.search("user", "hell*")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_fts5_injection_does_not_escape_user_filter(store):
    """Injected column filter syntax must not leak data from another user."""
    await store.save_chunk("victim", "secret data", "Secret", "snippets")
    await store.save_chunk("attacker", "normal data", "Normal", "snippets")
    # Without the fix, a query like 'x" OR "secret' could be crafted so the
    # FTS MATCH returns rows regardless of the user_id WHERE clause.  With the
    # fix the whole string is treated as a single phrase and matches nothing.
    results = await store.search("attacker", 'x" OR "secret')
    assert all(r["content"] != "secret data" for r in results)
