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


# ---------------------------------------------------------------------------
# Route-level proxy tests
# ---------------------------------------------------------------------------

import json
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import ASGITransport, AsyncClient as HttpxAsyncClient
import pytest_asyncio


@pytest_asyncio.fixture
async def mem_client(app, tmp_data_dir):
    """Admin HTTP client with user_memory store initialised."""
    store = app.state.user_memory
    if store._db is None:
        await store.init()
    metrics = app.state.metrics
    if metrics._db is None:
        await metrics.init()
    app.state.auth.setup_user("admin", "Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=record["id"], long_lived=True)
    app.state._startup_complete = True
    transport = ASGITransport(app=app)
    async with HttpxAsyncClient(
        transport=transport, base_url="http://test", cookies={"taos_session": token}
    ) as c:
        yield c, store
    await store.close()
    await metrics.close()


@pytest.mark.asyncio
async def test_search_falls_back_to_sqlite_when_taosmd_unreachable(app, mem_client, tmp_data_dir):
    client, store = mem_client
    await store.save_chunk("user", "fallback content", "T", "snippets")
    app.state.taosmd_url = "http://localhost:19999"
    try:
        resp = await client.get("/api/user-memory/search", params={"q": "fallback"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["backend"] == "sqlite"
        assert any("fallback" in r["content"] for r in data["results"])
    finally:
        app.state.taosmd_url = None


@pytest.mark.asyncio
async def test_search_uses_taosmd_when_available(mem_client, tmp_data_dir):
    client, store = mem_client
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "hits": [{"content": "taosmd result", "metadata": {"collection": "snippets"}}]
    }
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("tinyagentos.routes.user_memory.httpx.AsyncClient", return_value=mock_client):
        resp = await client.get("/api/user-memory/search", params={"q": "test"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["backend"] == "taosmd"
    assert data["results"][0]["content"] == "taosmd result"


@pytest.mark.asyncio
async def test_save_writes_to_sqlite_and_ingest_to_taosmd(mem_client, tmp_data_dir):
    client, store = mem_client
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

    with patch("tinyagentos.routes.user_memory.httpx.AsyncClient", return_value=mock_client):
        resp = await client.post(
            "/api/user-memory/save",
            json={"content": "dual write", "title": "T", "collection": "snippets"},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    # SQLite should have the chunk too
    chunks = await store.browse("user")
    assert any(c["content"] == "dual write" for c in chunks)


@pytest.mark.asyncio
async def test_save_succeeds_even_when_taosmd_unreachable(app, mem_client, tmp_data_dir):
    client, store = mem_client
    app.state.taosmd_url = "http://localhost:19999"
    try:
        resp = await client.post(
            "/api/user-memory/save",
            json={"content": "resilient", "collection": "notes"},
        )
        assert resp.status_code == 200
        chunks = await store.browse("user")
        assert any(c["content"] == "resilient" for c in chunks)
    finally:
        app.state.taosmd_url = None


@pytest.mark.asyncio
async def test_migrate_returns_503_when_taosmd_unreachable(app, mem_client, tmp_data_dir):
    client, _ = mem_client
    app.state.taosmd_url = "http://localhost:19999"
    try:
        resp = await client.post("/api/user-memory/migrate")
        assert resp.status_code == 503
    finally:
        app.state.taosmd_url = None


@pytest.mark.asyncio
async def test_browse_offset_pages_through_rows(store):
    for i in range(5):
        await store.save_chunk("user", f"chunk number {i}", f"T{i}", "snippets")
    first = await store.browse("user", limit=2, offset=0)
    second = await store.browse("user", limit=2, offset=2)
    third = await store.browse("user", limit=2, offset=4)
    assert len(first) == 2 and len(second) == 2 and len(third) == 1
    hashes = {c["hash"] for c in first + second + third}
    assert len(hashes) == 5  # no overlap between pages


@pytest.mark.asyncio
async def test_save_metadata_cannot_override_source_id(mem_client, tmp_data_dir):
    """Caller-supplied metadata must not clobber the server-owned dedup key."""
    client, _ = mem_client
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

    with patch("tinyagentos.routes.user_memory.httpx.AsyncClient", return_value=mock_client):
        resp = await client.post(
            "/api/user-memory/save",
            json={
                "content": "override attempt",
                "collection": "snippets",
                "metadata": {"source_id": "evil", "collection": "other", "custom": "kept"},
            },
        )
    assert resp.status_code == 200
    saved_hash = resp.json()["hash"]
    sent_meta = mock_client.post.call_args.kwargs["json"]["metadata"]
    assert sent_meta["source_id"] == saved_hash
    assert sent_meta["collection"] == "snippets"
    assert sent_meta["custom"] == "kept"


@pytest.mark.asyncio
async def test_migrate_error_response_does_not_leak_exception_text(mem_client, tmp_data_dir):
    """The /migrate 500 path must not reflect raw exception text to callers."""
    client, store = mem_client
    await store.save_chunk("user", "to migrate", "T", "snippets")
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=MagicMock(status_code=200))  # health OK
    mock_client.post = AsyncMock(side_effect=RuntimeError("http://internal-taosmd:7900 boom"))

    with patch("tinyagentos.routes.user_memory.httpx.AsyncClient", return_value=mock_client):
        resp = await client.post("/api/user-memory/migrate")
    assert resp.status_code == 500
    assert "internal-taosmd" not in resp.text
    assert resp.json()["error"] == "taosmd ingest failed"
