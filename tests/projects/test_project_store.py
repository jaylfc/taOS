import pytest
import pytest_asyncio

from tinyagentos.projects.project_store import ProjectStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ProjectStore(tmp_path / "projects.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_create_and_get_project(store):
    p = await store.create_project(
        name="Tax Prep 2026",
        slug="tax-prep-2026",
        description="annual filing",
        created_by="user-1",
    )
    assert p["id"].startswith("prj-")
    assert p["name"] == "Tax Prep 2026"
    assert p["slug"] == "tax-prep-2026"
    assert p["status"] == "active"
    assert p["created_by"] == "user-1"

    again = await store.get_project(p["id"])
    assert again == p


@pytest.mark.asyncio
async def test_create_project_rejects_duplicate_slug(store):
    await store.create_project(name="A", slug="dup", created_by="u")
    with pytest.raises(ValueError):
        await store.create_project(name="B", slug="dup", created_by="u")


@pytest.mark.asyncio
async def test_list_projects_filter_by_status(store):
    a = await store.create_project(name="A", slug="a", created_by="u")
    b = await store.create_project(name="B", slug="b", created_by="u")
    await store.set_status(b["id"], "archived")

    active = await store.list_projects(status="active")
    archived = await store.list_projects(status="archived")
    assert [p["id"] for p in active] == [a["id"]]
    assert [p["id"] for p in archived] == [b["id"]]


@pytest.mark.asyncio
async def test_update_project(store):
    p = await store.create_project(name="A", slug="a", created_by="u")
    await store.update_project(p["id"], name="A2", description="hello")
    again = await store.get_project(p["id"])
    assert again["name"] == "A2"
    assert again["description"] == "hello"
    assert again["updated_at"] >= p["updated_at"]


@pytest.mark.asyncio
async def test_add_remove_member(store):
    p = await store.create_project(name="A", slug="a", created_by="u")
    await store.add_member(
        p["id"],
        member_id="agent-1",
        member_kind="native",
        role="member",
    )
    await store.add_member(
        p["id"],
        member_id="agent-2-clone",
        member_kind="clone",
        source_agent_id="agent-2",
        memory_seed="snapshot",
    )
    members = await store.list_members(p["id"])
    assert len(members) == 2
    by_id = {m["member_id"]: m for m in members}
    assert by_id["agent-2-clone"]["memory_seed"] == "snapshot"
    assert by_id["agent-2-clone"]["source_agent_id"] == "agent-2"

    await store.remove_member(p["id"], "agent-1")
    members = await store.list_members(p["id"])
    assert [m["member_id"] for m in members] == ["agent-2-clone"]


@pytest.mark.asyncio
async def test_log_activity(store):
    p = await store.create_project(name="A", slug="a", created_by="u")
    await store.log_activity(p["id"], actor_id="u", kind="project.created", payload={"name": "A"})
    await store.log_activity(p["id"], actor_id="u", kind="member.added", payload={"member_id": "agent-1"})
    rows = await store.list_activity(p["id"])
    assert [r["kind"] for r in rows] == ["member.added", "project.created"]
    assert rows[0]["payload"] == {"member_id": "agent-1"}


@pytest.mark.asyncio
async def test_backfill_clear_lead_does_not_repromote_on_restart(tmp_path):
    """A Lead the owner deliberately cleared (lead_member_id set NULL, legacy
    is_lead still set) must not be re-promoted by the one-shot backfill when the
    store is re-initialized. The backfill clears is_lead after migrating, so a
    second init sees nothing to promote."""
    from tinyagentos.projects.project_store import ProjectStore

    db_path = tmp_path / "proj-repromote.db"
    s = ProjectStore(db_path)
    await s.init()
    try:
        project = await s.create_project(name="A", slug="a", created_by="u")
        pid = project["id"]
        await s.add_member(pid, member_id="agent-old-lead", member_kind="native")
        # Simulate the legacy is_lead flag being set (the pre-epic state).
        await s._db.execute(
            "UPDATE project_members SET is_lead = 1 WHERE project_id = ? AND member_id = ?",
            (pid, "agent-old-lead"),
        )
        await s._db.commit()
        # Re-run init so the backfill sees the legacy is_lead flag and migrates it.
        await s.init()
        again = await s.get_project(pid)
        assert again["lead_member_id"] == "agent-old-lead"

        # Owner clears the Lead.
        await s.set_lead(pid, None)
    finally:
        await s.close()

    # Second store init (a restart): the legacy flag must have been cleared by
    # the first backfill, so nothing promotes a Lead.
    s2 = ProjectStore(db_path)
    await s2.init()
    try:
        restarted = await s2.get_project(pid)
        assert restarted["lead_member_id"] is None
        members = await s2.list_members(pid)
        assert members[0]["is_lead"] == 0
    finally:
        await s2.close()


@pytest.mark.asyncio
async def test_backfill_only_runs_for_null_lead(tmp_path):
    """Projects that already have a Lead (lead_member_id set) are untouched by the
    backfill, and projects with no is_lead flag are never promoted."""
    from tinyagentos.projects.project_store import ProjectStore

    s = ProjectStore(tmp_path / "proj-no-reprompt.db")
    await s.init()
    try:
        # Project with a proper lead already set, no legacy flag.
        p1 = await s.create_project(name="A", slug="a", created_by="u")
        await s.add_member(p1["id"], member_id="agent-a", member_kind="native")
        await s.set_lead(p1["id"], "agent-a")

        # Project with no lead and no flag: must stay NULL.
        p2 = await s.create_project(name="B", slug="b", created_by="u")
        await s.add_member(p2["id"], member_id="agent-b", member_kind="native")

        # Re-init to force a backfill pass.
        await s.init()

        assert (await s.get_project(p1["id"]))["lead_member_id"] == "agent-a"
        assert (await s.get_project(p2["id"]))["lead_member_id"] is None
    finally:
        await s.close()
