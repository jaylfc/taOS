import pytest
import pytest_asyncio

from tinyagentos.feedback_store import MAX_BODY_LEN, MAX_SCREENSHOT_LEN, FeedbackStore


@pytest_asyncio.fixture
async def store(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.db")
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_create_returns_all_fields(store):
    row = await store.create(
        user_id="user-1",
        type="bug",
        title="Something broke",
        body="Details here",
        app="myapp",
    )
    assert row["user_id"] == "user-1"
    assert row["type"] == "bug"
    assert row["title"] == "Something broke"
    assert row["body"] == "Details here"
    assert row["app"] == "myapp"
    assert row["screenshot"] == ""
    assert "id" in row
    assert "created_at" in row


@pytest.mark.asyncio
async def test_create_with_screenshot(store):
    row = await store.create(
        user_id="user-1",
        type="bug",
        title="Screenshot test",
        body="body",
        screenshot="data:image/png;base64,AAAA",
    )
    assert row["screenshot"] == "data:image/png;base64,AAAA"


@pytest.mark.asyncio
async def test_create_generates_unique_ids(store):
    row1 = await store.create(user_id="u", type="bug", title="t1", body="b1")
    row2 = await store.create(user_id="u", type="bug", title="t2", body="b2")
    assert row1["id"] != row2["id"]


@pytest.mark.asyncio
async def test_get_by_id_returns_full_row(store):
    created = await store.create(
        user_id="user-1",
        type="feature",
        title="Add dark mode",
        body="please",
        screenshot="data:image/png;base64,BBBB",
        app="settings",
    )
    fetched = await store.get_by_id(created["id"], "user-1")
    assert fetched is not None
    assert fetched["id"] == created["id"]
    assert fetched["user_id"] == "user-1"
    assert fetched["type"] == "feature"
    assert fetched["title"] == "Add dark mode"
    assert fetched["body"] == "please"
    assert fetched["screenshot"] == "data:image/png;base64,BBBB"
    assert fetched["app"] == "settings"
    assert fetched["has_screenshot"] is True


@pytest.mark.asyncio
async def test_get_by_id_wrong_user_returns_none(store):
    created = await store.create(user_id="user-1", type="bug", title="t", body="b")
    result = await store.get_by_id(created["id"], "user-2")
    assert result is None


@pytest.mark.asyncio
async def test_get_by_id_nonexistent_returns_none(store):
    result = await store.get_by_id("nonexistent-id", "user-1")
    assert result is None


@pytest.mark.asyncio
async def test_get_by_id_has_screenshot_false_when_empty(store):
    created = await store.create(user_id="u", type="bug", title="t", body="b", screenshot="")
    fetched = await store.get_by_id(created["id"], "u")
    assert fetched["has_screenshot"] is False


@pytest.mark.asyncio
async def test_list_for_user_returns_items_most_recent_first(store):
    r1 = await store.create(user_id="user-1", type="bug", title="old", body="b1")
    r2 = await store.create(user_id="user-1", type="bug", title="new", body="b2")
    rows = await store.list_for_user("user-1")
    assert len(rows) == 2
    assert rows[0]["id"] == r2["id"]
    assert rows[0]["title"] == "new"
    assert rows[1]["id"] == r1["id"]
    assert rows[1]["title"] == "old"


@pytest.mark.asyncio
async def test_list_for_user_excludes_screenshot_blob(store):
    await store.create(
        user_id="user-1",
        type="bug",
        title="t",
        body="b",
        screenshot="data:image/png;base64,SECRET",
    )
    rows = await store.list_for_user("user-1")
    assert len(rows) == 1
    assert "screenshot" not in rows[0]
    assert rows[0]["has_screenshot"] is True


@pytest.mark.asyncio
async def test_list_for_user_scoped_to_user(store):
    await store.create(user_id="user-1", type="bug", title="u1-item", body="b")
    await store.create(user_id="user-2", type="bug", title="u2-item", body="b")
    rows = await store.list_for_user("user-1")
    assert len(rows) == 1
    assert rows[0]["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_list_for_user_empty_when_no_feedback(store):
    rows = await store.list_for_user("nobody")
    assert rows == []


@pytest.mark.asyncio
async def test_list_for_user_has_screenshot_false(store):
    await store.create(user_id="u", type="bug", title="t", body="b", screenshot="")
    rows = await store.list_for_user("u")
    assert rows[0]["has_screenshot"] is False


@pytest.mark.asyncio
async def test_list_for_user_returns_expected_keys(store):
    await store.create(user_id="u", type="bug", title="t", body="b")
    rows = await store.list_for_user("u")
    assert len(rows) == 1
    expected_keys = {"id", "user_id", "type", "title", "body", "app", "created_at", "has_screenshot"}
    assert set(rows[0].keys()) == expected_keys


@pytest.mark.asyncio
async def test_create_default_app(store):
    row = await store.create(user_id="u", type="bug", title="t", body="")
    assert row["app"] == ""


@pytest.mark.asyncio
async def test_multiple_types(store):
    await store.create(user_id="u", type="bug", title="bug report", body="b")
    await store.create(user_id="u", type="feature", title="feature req", body="f")
    await store.create(user_id="u", type="question", title="question", body="q")
    rows = await store.list_for_user("u")
    assert len(rows) == 3
    types = {r["type"] for r in rows}
    assert types == {"bug", "feature", "question"}


@pytest.mark.asyncio
async def test_constants_are_sane():
    assert MAX_SCREENSHOT_LEN == 4_000_000
    assert MAX_BODY_LEN == 20_000
