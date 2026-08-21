import pytest
import pytest_asyncio
from croniter import croniter

from tinyagentos.projects.routines_store import RoutineStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = RoutineStore(tmp_path / "routines.db")
    await s.init()
    s._clock = staticmethod(lambda: 43200.0)
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_create_cron_routine_computes_next_fire(store):
    r = await store.create_routine(
        project_id="prj-1",
        title="Daily standup task",
        created_by="user-1",
        cron_expr="0 3 * * *",
    )
    assert r["id"].startswith("rtn-")
    assert r["project_id"] == "prj-1"
    assert r["trigger_kind"] == "cron"
    assert r["cron_expr"] == "0 3 * * *"
    assert r["enabled"] == 1
    assert r["webhook_token"] is None
    assert r["last_fired"] is None
    assert r["next_fire"] == croniter("0 3 * * *", 43200.0).get_next(float)


@pytest.mark.asyncio
async def test_create_cron_routine_requires_cron_expr(store):
    with pytest.raises(ValueError):
        await store.create_routine(
            project_id="prj-1", title="Bad", created_by="u", trigger_kind="cron",
        )


@pytest.mark.asyncio
async def test_create_routine_rejects_bad_trigger_kind(store):
    with pytest.raises(ValueError):
        await store.create_routine(
            project_id="prj-1", title="Bad", created_by="u", trigger_kind="carrier-pigeon",
        )


@pytest.mark.asyncio
async def test_create_cron_routine_rejects_invalid_cron_expr(store):
    with pytest.raises(ValueError):
        await store.create_routine(
            project_id="prj-1", title="Bad", created_by="u", cron_expr="not a cron",
        )


@pytest.mark.asyncio
async def test_update_routine_rejects_invalid_cron_expr(store):
    r = await store.create_routine(
        project_id="prj-1", title="Original", created_by="u", cron_expr="0 3 * * *",
    )
    with pytest.raises(ValueError):
        await store.update_routine(r["id"], cron_expr="0 99 * * *")


@pytest.mark.asyncio
async def test_create_webhook_routine_generates_token(store):
    r = await store.create_routine(
        project_id="prj-1", title="Inbound hook", created_by="u", trigger_kind="webhook",
    )
    assert r["trigger_kind"] == "webhook"
    assert r["webhook_token"]
    assert r["next_fire"] is None


@pytest.mark.asyncio
async def test_create_api_routine_has_no_schedule_or_token(store):
    r = await store.create_routine(
        project_id="prj-1", title="API only", created_by="u", trigger_kind="api",
    )
    assert r["trigger_kind"] == "api"
    assert r["webhook_token"] is None
    assert r["next_fire"] is None


@pytest.mark.asyncio
async def test_get_by_webhook_token_matches(store):
    r = await store.create_routine(
        project_id="prj-1", title="Hook", created_by="u", trigger_kind="webhook",
    )
    found = await store.get_by_webhook_token(r["webhook_token"])
    assert found is not None
    assert found["id"] == r["id"]


@pytest.mark.asyncio
async def test_get_by_webhook_token_rejects_unknown(store):
    await store.create_routine(
        project_id="prj-1", title="Hook", created_by="u", trigger_kind="webhook",
    )
    assert await store.get_by_webhook_token("not-a-real-token") is None


@pytest.mark.asyncio
async def test_get_by_webhook_token_rejects_empty(store):
    # An empty token must never match the NULL webhook_token of cron/api rows.
    await store.create_routine(
        project_id="prj-1", title="Cron", created_by="u", cron_expr="0 3 * * *",
    )
    assert await store.get_by_webhook_token("") is None


@pytest.mark.asyncio
async def test_get_by_webhook_token_distinguishes_multiple_routines(store):
    a = await store.create_routine(
        project_id="prj-1", title="Hook A", created_by="u", trigger_kind="webhook",
    )
    b = await store.create_routine(
        project_id="prj-1", title="Hook B", created_by="u", trigger_kind="webhook",
    )
    assert (await store.get_by_webhook_token(a["webhook_token"]))["id"] == a["id"]
    assert (await store.get_by_webhook_token(b["webhook_token"]))["id"] == b["id"]


@pytest.mark.asyncio
async def test_get_by_webhook_token_rejects_disabled_routine(store):
    r = await store.create_routine(
        project_id="prj-1", title="Hook", created_by="u", trigger_kind="webhook",
    )
    await store.update_routine(r["id"], enabled=False)
    assert await store.get_by_webhook_token(r["webhook_token"]) is None


@pytest.mark.asyncio
async def test_list_routines_scoped_to_project(store):
    await store.create_routine(project_id="prj-1", title="A", created_by="u", trigger_kind="api")
    await store.create_routine(project_id="prj-2", title="B", created_by="u", trigger_kind="api")
    items = await store.list_routines("prj-1")
    assert len(items) == 1
    assert items[0]["title"] == "A"


@pytest.mark.asyncio
async def test_list_due_returns_only_past_due_enabled_cron_routines(store):
    now = 43200.0
    due = await store.create_routine(
        project_id="prj-1", title="Due", created_by="u", cron_expr="0 3 * * *",
    )
    await store._db.execute(
        "UPDATE routines SET next_fire = ? WHERE id = ?", (now - 10, due["id"])
    )
    await store._db.commit()

    not_due = await store.create_routine(
        project_id="prj-1", title="Not due", created_by="u", cron_expr="0 3 * * *",
    )
    await store._db.execute(
        "UPDATE routines SET next_fire = ? WHERE id = ?", (now + 100000, not_due["id"])
    )
    await store._db.commit()

    webhook = await store.create_routine(
        project_id="prj-1", title="Webhook", created_by="u", trigger_kind="webhook",
    )
    api_only = await store.create_routine(
        project_id="prj-1", title="Api", created_by="u", trigger_kind="api",
    )

    result = await store.list_due(now)
    ids = {r["id"] for r in result}
    assert ids == {due["id"]}
    assert webhook["id"] not in ids
    assert api_only["id"] not in ids


@pytest.mark.asyncio
async def test_list_due_excludes_disabled_routine(store):
    now = 43200.0
    r = await store.create_routine(
        project_id="prj-1", title="Due but disabled", created_by="u", cron_expr="0 3 * * *",
    )
    await store._db.execute(
        "UPDATE routines SET next_fire = ? WHERE id = ?", (now - 10, r["id"])
    )
    await store._db.commit()
    await store.update_routine(r["id"], enabled=False)

    result = await store.list_due(now)
    assert r["id"] not in {x["id"] for x in result}


@pytest.mark.asyncio
async def test_update_routine_changes_fields(store):
    r = await store.create_routine(
        project_id="prj-1", title="Original", created_by="u", cron_expr="0 3 * * *",
    )
    updated = await store.update_routine(r["id"], title="Renamed", body_template="new body")
    assert updated["title"] == "Renamed"
    assert updated["body_template"] == "new body"


@pytest.mark.asyncio
async def test_update_routine_cron_expr_recomputes_next_fire(store):
    r = await store.create_routine(
        project_id="prj-1", title="Original", created_by="u", cron_expr="0 3 * * *",
    )
    old_next = r["next_fire"]
    updated = await store.update_routine(r["id"], cron_expr="*/5 * * * *")
    assert updated["cron_expr"] == "*/5 * * * *"
    assert updated["next_fire"] != old_next


@pytest.mark.asyncio
async def test_update_routine_disable_clears_next_fire(store):
    r = await store.create_routine(
        project_id="prj-1", title="Original", created_by="u", cron_expr="0 3 * * *",
    )
    updated = await store.update_routine(r["id"], enabled=False)
    assert updated["enabled"] == 0
    assert updated["next_fire"] is None


@pytest.mark.asyncio
async def test_update_routine_reenable_recomputes_next_fire(store):
    r = await store.create_routine(
        project_id="prj-1", title="Original", created_by="u", cron_expr="0 3 * * *",
    )
    await store.update_routine(r["id"], enabled=False)
    updated = await store.update_routine(r["id"], enabled=True)
    assert updated["enabled"] == 1
    assert updated["next_fire"] is not None


@pytest.mark.asyncio
async def test_update_routine_returns_none_for_missing(store):
    assert await store.update_routine("rtn-missing", title="x") is None


@pytest.mark.asyncio
async def test_record_fire_advances_schedule(store):
    r = await store.create_routine(
        project_id="prj-1", title="Original", created_by="u", cron_expr="* * * * *",
    )
    first_next = r["next_fire"]
    fired_at = first_next + 1
    updated = await store.record_fire(r["id"], fired_at)
    assert updated["last_fired"] == fired_at
    assert updated["next_fire"] > first_next


@pytest.mark.asyncio
async def test_record_fire_on_webhook_routine_leaves_next_fire_none(store):
    r = await store.create_routine(
        project_id="prj-1", title="Hook", created_by="u", trigger_kind="webhook",
    )
    updated = await store.record_fire(r["id"], 43200.0)
    assert updated["next_fire"] is None
    assert updated["last_fired"] is not None


@pytest.mark.asyncio
async def test_claim_due_advances_schedule_and_returns_true(store):
    r = await store.create_routine(
        project_id="prj-1", title="Due", created_by="u", cron_expr="* * * * *",
    )
    first_next = r["next_fire"]
    fired_at = first_next + 1
    claimed = await store.claim_due(r["id"], first_next, fired_at)
    assert claimed is True
    after = await store.get_routine(r["id"])
    assert after["last_fired"] == fired_at
    assert after["next_fire"] > first_next


@pytest.mark.asyncio
async def test_claim_due_second_claim_of_same_instant_returns_false(store):
    """The double-fire guard: once a routine's next_fire has been advanced, a
    second claim for the SAME original next_fire must fail so the routine fires
    exactly once per due instant."""
    r = await store.create_routine(
        project_id="prj-1", title="Due once", created_by="u", cron_expr="* * * * *",
    )
    original_next = r["next_fire"]
    fired_at = original_next + 1
    first = await store.claim_due(r["id"], original_next, fired_at)
    second = await store.claim_due(r["id"], original_next, fired_at)
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_claim_due_ignores_non_cron_and_disabled(store):
    webhook = await store.create_routine(
        project_id="prj-1", title="Hook", created_by="u", trigger_kind="webhook",
    )
    assert await store.claim_due(webhook["id"], 0.0, 43200.0) is False

    cron = await store.create_routine(
        project_id="prj-1", title="Disabled", created_by="u", cron_expr="* * * * *",
    )
    original_next = cron["next_fire"]
    await store.update_routine(cron["id"], enabled=False)
    assert await store.claim_due(cron["id"], original_next, 43200.0) is False


@pytest.mark.asyncio
async def test_delete_routine(store):
    r = await store.create_routine(
        project_id="prj-1", title="Gone", created_by="u", trigger_kind="api",
    )
    assert await store.delete_routine(r["id"]) is True
    assert await store.get_routine(r["id"]) is None


@pytest.mark.asyncio
async def test_delete_nonexistent_routine(store):
    assert await store.delete_routine("rtn-missing") is False
