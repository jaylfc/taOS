import pytest
import pytest_asyncio

from tinyagentos.projects.project_store import ProjectConflict, ProjectStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ProjectStore(tmp_path / "projects.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_create_project_stores_and_returns_project(store):
    p = await store.create_project(
        name="Launch Pad",
        slug="launch-pad",
        description="initial project",
        created_by="owner-1",
        user_id="user-1",
    )
    assert p["id"].startswith("prj-")
    assert p["name"] == "Launch Pad"
    assert p["slug"] == "launch-pad"
    assert p["description"] == "initial project"
    assert p["status"] == "active"
    assert p["created_by"] == "owner-1"
    assert p["user_id"] == "user-1"
    assert isinstance(p["created_at"], float)
    assert isinstance(p["updated_at"], float)


@pytest.mark.asyncio
async def test_get_project_by_id_missing_returns_none(store):
    assert await store.get_project("prj-doesnotexist") is None


@pytest.mark.asyncio
async def test_get_project_by_slug(store):
    await store.create_project(name="Alpha", slug="alpha", created_by="u")
    p = await store.get_project_by_slug("alpha")
    assert p is not None
    assert p["name"] == "Alpha"


@pytest.mark.asyncio
async def test_get_project_by_name_is_case_insensitive(store):
    await store.create_project(name="Alpha", slug="alpha", created_by="u")
    p = await store.get_project_by_name("ALPHA")
    assert p is not None
    assert p["slug"] == "alpha"


@pytest.mark.asyncio
async def test_create_project_duplicate_slug_raises_project_conflict(store):
    await store.create_project(name="A", slug="dup", created_by="u")
    with pytest.raises(ProjectConflict) as exc_info:
        await store.create_project(name="B", slug="dup", created_by="u")
    assert exc_info.value.field == "slug"
    assert exc_info.value.taken == "dup"


@pytest.mark.asyncio
async def test_create_project_duplicate_name_raises_project_conflict(store):
    await store.create_project(name="Same", slug="same", created_by="u")
    with pytest.raises(ProjectConflict) as exc_info:
        await store.create_project(name="Same", slug="other", created_by="u")
    assert exc_info.value.field == "name"
    assert exc_info.value.taken == "Same"


@pytest.mark.asyncio
async def test_list_projects_empty_store_returns_empty_list(store):
    assert await store.list_projects() == []


@pytest.mark.asyncio
async def test_list_projects_none_status_returns_all(store):
    a = await store.create_project(name="A", slug="a", created_by="u")
    b = await store.create_project(name="B", slug="b", created_by="u")
    await store.set_status(b["id"], "deleted")
    all_projects = await store.list_projects(status=None)
    ids = {p["id"] for p in all_projects}
    assert ids == {a["id"], b["id"]}


@pytest.mark.asyncio
async def test_list_for_user_empty_user_id_returns_empty_list(store):
    await store.create_project(name="A", slug="a", created_by="u", user_id="user-1")
    assert await store.list_for_user("") == []
    assert await store.list_for_user("nonexistent") == []


@pytest.mark.asyncio
async def test_list_for_user_scopes_by_user_id(store):
    p1 = await store.create_project(name="A", slug="a", created_by="u", user_id="user-1")
    p2 = await store.create_project(name="B", slug="b", created_by="u", user_id="user-2")
    user1_projects = await store.list_for_user("user-1")
    assert [p["id"] for p in user1_projects] == [p1["id"]]
    user2_projects = await store.list_for_user("user-2")
    assert [p["id"] for p in user2_projects] == [p2["id"]]


@pytest.mark.asyncio
async def test_list_for_user_respects_status_filter(store):
    p = await store.create_project(name="A", slug="a", created_by="u", user_id="user-1")
    await store.set_status(p["id"], "archived")
    active = await store.list_for_user("user-1", status="active")
    assert active == []
    archived = await store.list_for_user("user-1", status="archived")
    assert [pr["id"] for pr in archived] == [p["id"]]


@pytest.mark.asyncio
async def test_update_project_changes_fields(store):
    p = await store.create_project(name="A", slug="a", created_by="u")
    await store.update_project(p["id"], name="A2", description="new desc")
    again = await store.get_project(p["id"])
    assert again["name"] == "A2"
    assert again["description"] == "new desc"
    assert again["updated_at"] >= p["updated_at"]


@pytest.mark.asyncio
async def test_update_project_preserves_untouched_fields(store):
    p = await store.create_project(
        name="A",
        slug="a",
        description="orig desc",
        created_by="u",
        settings={"key": "value"},
    )
    await store.update_project(p["id"], name="A2")
    again = await store.get_project(p["id"])
    assert again["description"] == "orig desc"
    assert again["settings"] == {"key": "value"}


@pytest.mark.asyncio
async def test_update_project_noop_when_no_fields(store):
    p = await store.create_project(name="A", slug="a", created_by="u")
    original_updated_at = p["updated_at"]
    await store.update_project(p["id"])
    again = await store.get_project(p["id"])
    assert again["name"] == "A"
    assert again["updated_at"] == original_updated_at


@pytest.mark.asyncio
async def test_set_status_deleted_excludes_from_active_list(store):
    p = await store.create_project(name="A", slug="a", created_by="u")
    await store.set_status(p["id"], "deleted")
    assert await store.get_project(p["id"]) is not None
    assert await store.list_projects(status="active") == []
    deleted = await store.list_projects(status="deleted")
    assert len(deleted) == 1
    assert deleted[0]["id"] == p["id"]
    assert deleted[0]["status"] == "deleted"
    assert deleted[0]["deleted_at"] is not None


@pytest.mark.asyncio
async def test_set_status_invalid_raises_value_error(store):
    p = await store.create_project(name="A", slug="a", created_by="u")
    with pytest.raises(ValueError, match="invalid status"):
        await store.set_status(p["id"], "gone")


@pytest.mark.asyncio
async def test_set_lead_missing_project_raises_key_error(store):
    with pytest.raises(KeyError):
        await store.set_lead("prj-missing", "member-1")


@pytest.mark.asyncio
async def test_set_lead_non_member_raises_key_error(store):
    p = await store.create_project(name="A", slug="a", created_by="u")
    with pytest.raises(KeyError):
        await store.set_lead(p["id"], "member-not-in-project")


@pytest.mark.asyncio
async def test_set_lead_and_clear_lead(store):
    p = await store.create_project(name="A", slug="a", created_by="u")
    await store.add_member(p["id"], member_id="agent-1", member_kind="native")
    await store.set_lead(p["id"], "agent-1")
    assert (await store.get_project(p["id"]))["lead_member_id"] == "agent-1"
    await store.set_lead(p["id"], None)
    assert (await store.get_project(p["id"]))["lead_member_id"] is None


@pytest.mark.asyncio
async def test_add_member_invalid_kind_raises_value_error(store):
    p = await store.create_project(name="A", slug="a", created_by="u")
    with pytest.raises(ValueError, match="invalid member_kind"):
        await store.add_member(p["id"], member_id="x", member_kind="bot")


@pytest.mark.asyncio
async def test_remove_member_does_not_raise_on_missing(store):
    p = await store.create_project(name="A", slug="a", created_by="u")
    await store.remove_member(p["id"], "never-added")
    assert await store.list_members(p["id"]) == []


@pytest.mark.asyncio
async def test_log_and_list_activity(store):
    p = await store.create_project(name="A", slug="a", created_by="u")
    await store.log_activity(p["id"], actor_id="u", kind="created", payload={"v": 1})
    await store.log_activity(p["id"], actor_id="u", kind="updated", payload={"v": 2})
    rows = await store.list_activity(p["id"])
    kinds = [r["kind"] for r in rows]
    assert kinds == ["updated", "created"]
    assert rows[0]["payload"] == {"v": 2}


@pytest.mark.asyncio
async def test_list_activity_empty_returns_empty_list(store):
    p = await store.create_project(name="A", slug="a", created_by="u")
    assert await store.list_activity(p["id"]) == []
