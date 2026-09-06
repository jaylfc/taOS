import pytest

from tinyagentos.board_audit import BoardAuditLog


async def _log(tmp_path):
    s = BoardAuditLog(tmp_path / "audit.db")
    await s.init()
    return s


@pytest.mark.asyncio
async def test_record_and_history_ordered(tmp_path):
    s = await _log(tmp_path)
    e1 = await s.record("tsk-1", "claimed", actor="@kilo", from_status="open", to_status="claimed", ts="2026-01-01T00:00:00+00:00")
    e2 = await s.record("tsk-1", "merged", actor="@taOS-dev", from_status="claimed", to_status="done", ts="2026-01-01T00:00:00+00:00")
    hist = await s.history("tsk-1")
    assert [h["id"] for h in hist] == [e1, e2]  # insertion order, stable even on equal ts
    assert hist[0]["event"] == "claimed" and hist[0]["actor"] == "@kilo"
    assert hist[1]["to_status"] == "done"
    await s.close()


@pytest.mark.asyncio
async def test_append_only_no_mutate_or_delete(tmp_path):
    s = await _log(tmp_path)
    await s.record("tsk-1", "open")
    # The store exposes no update/delete API: recording the same task again keeps both.
    await s.record("tsk-1", "open")
    assert len(await s.history("tsk-1")) == 2
    assert not hasattr(s, "delete")
    assert not hasattr(s, "update")
    await s.close()


@pytest.mark.asyncio
async def test_history_scoped_per_task(tmp_path):
    s = await _log(tmp_path)
    await s.record("tsk-1", "open")
    await s.record("tsk-2", "open")
    assert len(await s.history("tsk-1")) == 1
    assert await s.history("tsk-missing") == []
    await s.close()


@pytest.mark.asyncio
async def test_all_since_filters_by_ts(tmp_path):
    s = await _log(tmp_path)
    await s.record("tsk-1", "old", ts="2026-01-01T00:00:00+00:00")
    await s.record("tsk-1", "new", ts="2026-06-01T00:00:00+00:00")
    recent = await s.all_since("2026-03-01T00:00:00+00:00")
    assert [r["event"] for r in recent] == ["new"]
    await s.close()


@pytest.mark.asyncio
async def test_record_requires_task_and_event(tmp_path):
    s = await _log(tmp_path)
    with pytest.raises(ValueError):
        await s.record("", "open")
    with pytest.raises(ValueError):
        await s.record("tsk-1", "")
    await s.close()


@pytest.mark.asyncio
async def test_get_returns_event_or_none(tmp_path):
    s = await _log(tmp_path)
    eid = await s.record("tsk-1", "open")
    assert (await s.get(eid))["event"] == "open"
    assert await s.get("ba-missing") is None
    await s.close()


@pytest.mark.asyncio
async def test_recent_for_project_scoped_and_newest_first(tmp_path):
    s = await _log(tmp_path)
    await s.record("tsk-a", "task.created", project_id="prj-1", to_status="open")
    await s.record("tsk-b", "task.created", project_id="prj-2", to_status="open")
    await s.record("tsk-a", "task.closed", project_id="prj-1", to_status="closed")
    feed = await s.recent_for_project("prj-1")
    # Only prj-1 events, newest first.
    assert [e["event"] for e in feed] == ["task.closed", "task.created"]
    assert all(e["project_id"] == "prj-1" for e in feed)
    await s.close()


@pytest.mark.asyncio
async def test_recent_for_project_respects_limit(tmp_path):
    s = await _log(tmp_path)
    for i in range(5):
        await s.record(f"tsk-{i}", "task.created", project_id="prj-1", to_status="open")
    assert len(await s.recent_for_project("prj-1", limit=2)) == 2
    await s.close()


@pytest.mark.asyncio
async def test_record_stores_detail_json(tmp_path):
    s = await _log(tmp_path)
    eid = await s.record("tsk-1", "task.created", project_id="prj-1", detail={"title": "Ship it"})
    ev = await s.get(eid)
    assert ev["detail"] == {"title": "Ship it"}
    await s.close()


@pytest.mark.asyncio
async def test_post_init_migrates_old_schema(tmp_path):
    """An existing board_audit table without project_id/detail is upgraded in place."""
    import aiosqlite

    db = tmp_path / "old.db"
    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute(
            "CREATE TABLE board_audit (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, "
            "event TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '', from_status TEXT, "
            "to_status TEXT, ts TEXT NOT NULL)"
        )
        await conn.execute(
            "INSERT INTO board_audit (id, task_id, event, ts) VALUES ('ba-old', 'tsk-z', 'task.created', '2026-01-01T00:00:00+00:00')"
        )
        await conn.commit()

    s = BoardAuditLog(db)
    await s.init()  # _post_init should ALTER in the new columns
    ev = await s.get("ba-old")
    assert ev["project_id"] == ""
    assert ev["detail"] == {}
    # New writes carry project_id and are queryable by the feed.
    await s.record("tsk-new", "task.created", project_id="prj-9", to_status="open")
    assert len(await s.recent_for_project("prj-9")) == 1
    await s.close()
