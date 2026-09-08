"""The shared projects.db must never be wedged by one store's failed write.

``app.py`` opens eight stores on a single ``projects.db``, each with its own
aiosqlite connection.  sqlite3 issues an implicit ``BEGIN`` before the first DML
on a connection, so a store that raised -- or was cancelled -- between that DML
and ``commit()`` left its connection inside an open transaction forever, holding
the WAL write lock.  Every other store then failed with
``sqlite3.OperationalError: database is locked`` until the controller was
restarted (production, 2026-09-02: six minutes of 500s on task create and
claim_task).

Each test here wedges store A the way one of those paths does and then requires
store B -- a different store on the same file -- to complete a normal write.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from unittest.mock import AsyncMock

import pytest

from tinyagentos.projects import tx as tx_module
from tinyagentos.projects.doc_review_store import DocReviewStore
from tinyagentos.projects.project_store import ProjectConflict, ProjectStore
from tinyagentos.projects.task_store import ProjectTaskStore

# Generous enough that a healthy write always lands, long enough that a wedged
# connection surfaces its real "database is locked" rather than a bare timeout.
_SIBLING_WRITE_TIMEOUT = 10.0


async def _project_store(tmp_path) -> ProjectStore:
    store = ProjectStore(tmp_path / "projects.db")
    await store.init()
    return store


async def _task_store(tmp_path) -> ProjectTaskStore:
    store = ProjectTaskStore(tmp_path / "projects.db")
    await store.init()
    return store


async def _sibling_write_succeeds(store_b: ProjectTaskStore) -> None:
    """Store B must be able to write while store A's connection stays open."""
    task = await asyncio.wait_for(
        store_b.create_task("prj-1", "sibling write", "jay"),
        _SIBLING_WRITE_TIMEOUT,
    )
    assert task["title"] == "sibling write"


@pytest.mark.asyncio
async def test_failed_insert_does_not_wedge_sibling_store(tmp_path):
    """A DML that raises (duplicate slug) must not keep the write lock."""
    store_a = await _project_store(tmp_path)
    store_b = await _task_store(tmp_path)

    await store_a.create_project("Alpha", "alpha", "jay")
    with pytest.raises(ProjectConflict):
        # The INSERT itself fails, after sqlite3 has already opened the
        # transaction: exactly the "raises between DML and commit" shape.
        await store_a.create_project("Beta", "alpha", "jay")

    await _sibling_write_succeeds(store_b)
    assert store_a._db.in_transaction is False


@pytest.mark.asyncio
async def test_exception_between_dml_and_commit_does_not_wedge_sibling_store(
    tmp_path, monkeypatch
):
    """Any failure at the DML -> commit boundary must roll back, not linger."""
    store_a = await _task_store(tmp_path)
    store_b = await _task_store(tmp_path)
    task = await store_a.create_task("prj-1", "seed", "jay")

    async def _failing_commit(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store_a._db, "commit", _failing_commit)
    with pytest.raises(sqlite3.OperationalError):
        await store_a.update_task(task["id"], title="edited")

    await _sibling_write_succeeds(store_b)
    assert store_a._db.in_transaction is False


@pytest.mark.asyncio
async def test_cancellation_between_dml_and_commit_does_not_wedge_sibling_store(
    tmp_path, monkeypatch
):
    """A cancelled request (client disconnect) must roll back its own write."""
    store_a = await _task_store(tmp_path)
    store_b = await _task_store(tmp_path)
    task = await store_a.create_task("prj-1", "seed", "jay")

    never = asyncio.Event()

    async def _hanging_commit(*args, **kwargs):
        # Suspends the store exactly between its UPDATE and its commit, so the
        # cancellation below lands inside the open transaction.
        await never.wait()

    monkeypatch.setattr(store_a._db, "commit", _hanging_commit)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            store_a.update_task(task["id"], title="edited"), 0.05
        )

    await _sibling_write_succeeds(store_b)
    assert store_a._db.in_transaction is False


@pytest.mark.asyncio
async def test_publish_failure_leaves_the_committed_write_in_place(
    tmp_path, monkeypatch
):
    """Event publishing stays outside the transaction.

    ``_publish`` runs after the write has committed, so a subscriber that blows
    up must neither undo the write nor hold the lock -- an invariant this
    refactor must not quietly invert by pulling the publish inside ``tx()``.
    """
    store_a = await _task_store(tmp_path)
    store_b = await _task_store(tmp_path)
    task = await store_a.create_task("prj-1", "seed", "jay")

    async def _exploding_publish(*args, **kwargs):
        raise RuntimeError("subscriber exploded")

    monkeypatch.setattr(store_a, "_publish", _exploding_publish)
    with pytest.raises(RuntimeError):
        await store_a.update_task(task["id"], title="edited")

    await _sibling_write_succeeds(store_b)
    assert store_a._db.in_transaction is False
    assert (await store_a.get_task(task["id"]))["title"] == "edited"


@pytest.mark.asyncio
async def test_concurrent_writes_on_one_store_all_land(tmp_path):
    """A burst of concurrent writes on one connection must all commit.

    The production incident opened with seven task creates inside one second.
    A connection can only be inside one transaction at a time, so the writes
    have to queue on it -- otherwise the second BEGIN fails with "cannot start
    a transaction within a transaction".
    """
    store = await _task_store(tmp_path)

    created = await asyncio.gather(
        *(store.create_task("prj-1", f"burst {i}", "jay") for i in range(7))
    )

    assert len({task["id"] for task in created}) == 7
    assert len(await store.list_tasks("prj-1")) == 7


@pytest.mark.asyncio
async def test_cancellation_while_begin_is_queued_does_not_wedge_sibling_store(
    tmp_path,
):
    """A write cancelled while its BEGIN is still queued must roll back too.

    ``BEGIN IMMEDIATE`` is the statement that waits: while another connection
    holds the write lock it blocks for the whole ``busy_timeout``.  aiosqlite has
    already handed it to the connection's worker thread by then, and that thread
    runs it whether or not the awaiting task is still around -- so a request
    cancelled at THIS boundary opened the transaction after the coroutine that
    would have rolled it back was gone.  Same wedge as a cancellation between
    the DML and the commit, one statement earlier.
    """
    holder = await _task_store(tmp_path)
    victim = await _task_store(tmp_path)
    onlooker = await _task_store(tmp_path)

    # Take the write lock so the victim's BEGIN has to wait for it.
    await holder._db.execute("BEGIN IMMEDIATE")

    pending = asyncio.ensure_future(
        victim.create_task("prj-1", "cancelled mid-BEGIN", "jay")
    )
    # Long enough that the victim is parked inside BEGIN IMMEDIATE.
    await asyncio.sleep(0.1)
    pending.cancel()

    # Release the lock so the victim's queued BEGIN lands on its worker thread,
    # which is exactly the race: the statement outlives its awaiting task.
    await holder._db.rollback()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending, _SIBLING_WRITE_TIMEOUT)

    # aiosqlite runs one statement at a time on the connection's worker thread,
    # so awaiting anything on that connection drains whatever the cancellation
    # left queued -- the BEGIN, and the rollback that has to follow it.  Without
    # this the assertions below race the worker thread and pass by luck.
    await victim._db.execute("SELECT 1")

    await _sibling_write_succeeds(onlooker)
    assert victim._db.in_transaction is False


@pytest.mark.asyncio
async def test_cancelled_handoff_rollback_still_frees_the_lock_and_says_so(caplog):
    """A rollback that is itself cancelled must not keep the connection lock.

    When ``tx()`` hands the lock to a shielded rollback, that rollback owns the
    release.  Something else cancelling it -- loop shutdown, a watchdog -- must
    still free the lock: holding it forever would block every later write on
    the store with no cure but a restart, which is the symptom this module
    exists to remove.  A surviving transaction is the lesser evil, so it is
    logged at ERROR with the store name rather than left silent.
    """
    lock = asyncio.Lock()
    await lock.acquire()
    rollback = asyncio.get_running_loop().create_future()
    rollback.cancel()

    with caplog.at_level(logging.ERROR, logger=tx_module.logger.name):
        tx_module._finish_rollback(rollback, "ProjectTaskStore", lock)

    assert lock.locked() is False
    assert "rollback was itself cancelled in ProjectTaskStore" in caplog.text


@pytest.mark.asyncio
async def test_nested_write_on_the_same_store_joins_the_open_transaction(tmp_path):
    """A write called from inside another write must not wait on its own lock.

    The per-connection lock is not re-entrant, so without the join a store
    method that writes through the same connection while a transaction is open
    -- a write calling another write, which is what a hook or a helper method
    does -- waits forever on a lock its own task already holds.  That is a
    worse failure than the wedge this module removes, so a nested scope in the
    same task joins the transaction already running.
    """
    store = await _task_store(tmp_path)

    async with store._tx():
        task = await asyncio.wait_for(
            store.create_task("prj-1", "nested write", "jay"), 5.0
        )

    assert (await store.get_task(task["id"]))["title"] == "nested write"
    assert store._db.in_transaction is False


@pytest.mark.asyncio
async def test_failure_after_a_nested_write_rolls_the_whole_nest_back(tmp_path):
    """The outermost scope owns the commit, so the nest is one transaction."""
    store = await _task_store(tmp_path)

    with pytest.raises(RuntimeError):
        async with store._tx():
            await store.create_task("prj-1", "doomed", "jay")
            raise RuntimeError("outer failed after the nested write")

    assert await store.list_tasks("prj-1") == []
    assert store._db.in_transaction is False


@pytest.mark.asyncio
async def test_park_and_disown_land_together_or_not_at_all(tmp_path):
    """Parking a claimed task must not commit half of itself.

    Parked is terminal, so a claimer left on a parked row makes the card look
    held by an agent that can never release it.  The status write and the
    disown are one transaction, so a failure at the second one takes the first
    back with it.
    """
    store = await _task_store(tmp_path)
    task = await store.create_task("prj-1", "seed", "jay")
    await store.claim_task(task["id"], "worker-1")

    real_execute = store._db.execute

    def failing_execute(sql, *args, **kwargs):
        if "claimed_by = NULL" in sql:

            async def _boom():
                raise sqlite3.OperationalError("disk I/O error")

            return _boom()
        return real_execute(sql, *args, **kwargs)

    store._db.execute = failing_execute
    with pytest.raises(sqlite3.OperationalError):
        await store.park_task(task["id"], "system")
    store._db.execute = real_execute

    fetched = await store.get_task(task["id"])
    assert fetched["status"] == "claimed"
    assert fetched["claimed_by"] == "worker-1"
    assert store._db.in_transaction is False


@pytest.mark.asyncio
async def test_duplicate_project_name_is_refused_under_concurrency(tmp_path):
    """The name check must read inside the transaction that inserts.

    ``projects.name`` has no unique index -- the check is a query, so outside
    the transaction two concurrent creates both pass it and both insert.
    """
    store = await _project_store(tmp_path)

    results = await asyncio.gather(
        store.create_project("Alpha", "alpha-1", "jay"),
        store.create_project("alpha", "alpha-2", "jay"),
        return_exceptions=True,
    )

    conflicts = [r for r in results if isinstance(r, ProjectConflict)]
    assert len(conflicts) == 1
    assert len(await store.list_projects()) == 1


@pytest.mark.asyncio
async def test_set_lead_validates_the_member_inside_the_transaction(tmp_path):
    """The membership check has to hold the write lock it decides under.

    Checked outside, a concurrent ``remove_member`` between the check and the
    pointer write leaves ``lead_member_id`` pointing at a member that no longer
    exists.  There is no deterministic single-task interleaving to demonstrate
    that -- the two writers are serialised by the connection lock -- so this
    asserts the property that closes it: the read happens with the
    transaction open.
    """
    store = await _project_store(tmp_path)
    project = await store.create_project("Alpha", "alpha", "jay")
    await store.add_member(project["id"], "agent-1", "native")

    seen: dict[str, bool] = {}
    real_get_member = store.get_member

    async def spying_get_member(*args, **kwargs):
        seen["in_transaction"] = store._db.in_transaction
        return await real_get_member(*args, **kwargs)

    store.get_member = spying_get_member
    await store.set_lead(project["id"], "agent-1")
    store.get_member = real_get_member

    assert seen["in_transaction"] is True
    assert (await store.get_project(project["id"]))["lead_member_id"] == "agent-1"


@pytest.mark.asyncio
async def test_a_read_never_sees_another_task_s_uncommitted_write(tmp_path):
    """A read must not observe a transaction that has not committed.

    Each store uses ONE connection for its reads and its writes, and sqlite
    shows a connection its own uncommitted changes.  aiosqlite runs every
    statement on that connection's single worker thread, so a read issued
    while another task holds an open transaction is executed between that
    transaction's statements and returns rows that may never commit.  The
    reader here runs while a rename is open and then rolls back: it must see
    the name that is actually in the database.
    """
    store = await _project_store(tmp_path)
    project = await store.create_project("Alpha", "alpha", "jay")

    wrote = asyncio.Event()
    finish = asyncio.Event()

    async def doomed_rename():
        with pytest.raises(RuntimeError):
            async with store._tx():
                await store._db.execute(
                    "UPDATE projects SET name = ? WHERE id = ?",
                    ("Renamed", project["id"]),
                )
                wrote.set()
                await finish.wait()
                raise RuntimeError("the rename fails after its write")

    writer = asyncio.create_task(doomed_rename())
    await wrote.wait()

    reader = asyncio.create_task(store.get_project(project["id"]))
    # Long enough that an ungated read reaches the connection and comes back
    # with the uncommitted row while the transaction is still open.
    await asyncio.sleep(0.05)
    finish.set()
    await writer

    seen = await asyncio.wait_for(reader, _SIBLING_WRITE_TIMEOUT)
    assert seen["name"] == "Alpha"


@pytest.mark.asyncio
async def test_effects_of_a_joined_write_are_discarded_when_the_nest_rolls_back(
    tmp_path,
):
    """Events and audit rows must wait for the OUTERMOST commit.

    ``tx()`` returns straight away for a nested scope in the same task -- the
    outermost scope owns the commit -- so a mutation that publishes after its
    own ``async with self._tx():`` block publishes while the enclosing
    transaction can still roll back.  The broker replay and the audit log then
    carry a transition the database never took.
    """
    broker = AsyncMock()
    audit = AsyncMock()
    store = ProjectTaskStore(tmp_path / "projects.db", broker=broker, audit=audit)
    await store.init()
    task = await store.create_task("prj-1", "seed", "jay")
    broker.reset_mock()
    audit.reset_mock()

    with pytest.raises(RuntimeError):
        async with store._tx():
            assert await store.park_task(task["id"], "system") is True
            raise RuntimeError("the outer scope fails after the nested park")

    assert (await store.get_task(task["id"]))["status"] == "open"
    published = [call.args[1].kind for call in broker.publish.call_args_list]
    assert published == []
    assert audit.record.await_count == 0
    await store.close()


@pytest.mark.asyncio
async def test_effects_of_a_joined_write_still_fire_once_the_nest_commits(tmp_path):
    """Deferral must not swallow the effects of a nest that DID commit."""
    broker = AsyncMock()
    audit = AsyncMock()
    store = ProjectTaskStore(tmp_path / "projects.db", broker=broker, audit=audit)
    await store.init()
    task = await store.create_task("prj-1", "seed", "jay")
    broker.reset_mock()
    audit.reset_mock()

    async with store._tx():
        assert await store.park_task(task["id"], "system") is True

    assert (await store.get_task(task["id"]))["status"] == "parked"
    published = [call.args[1].kind for call in broker.publish.call_args_list]
    assert "task.parked" in published
    assert audit.record.await_count == 1
    await store.close()


@pytest.mark.asyncio
async def test_review_transition_is_validated_inside_the_transaction(tmp_path):
    """Two concurrent review transitions must not both pass the same check.

    ``set_review_state`` read the current state and validated the transition
    before opening its transaction, so two callers both saw
    ``awaiting_review``, both passed, and the second overwrote the first with a
    transition that was never legal from the state it landed on.
    """
    store = DocReviewStore(tmp_path / "projects.db")
    await store.init()
    await store.set_review_state("prj-1", "docs/a.md", "awaiting_review", "jay")

    results = await asyncio.gather(
        store.set_review_state("prj-1", "docs/a.md", "approved", "alice"),
        store.set_review_state("prj-1", "docs/a.md", "changes_requested", "bob"),
        return_exceptions=True,
    )

    refused = [r for r in results if isinstance(r, ValueError)]
    assert len(refused) == 1
    final = await store.get_review("prj-1", "docs/a.md")
    assert final["review_state"] in ("approved", "changes_requested")
    await store.close()


@pytest.mark.asyncio
async def test_renaming_a_project_onto_a_taken_name_is_refused(tmp_path):
    """``update_project`` has to run the same name check ``create_project`` does.

    ``projects.name`` has no unique index and the create path enforces
    case-insensitive uniqueness with a query, so a PATCH that renames straight
    onto another project's name left two rows sharing one name and
    ``get_project_by_name`` returning only one of them.
    """
    store = await _project_store(tmp_path)
    await store.create_project("Alpha", "alpha", "jay")
    beta = await store.create_project("Beta", "beta", "jay")

    with pytest.raises(ProjectConflict):
        await store.update_project(beta["id"], name="ALPHA")

    assert (await store.get_project(beta["id"]))["name"] == "Beta"
    # A project may still be renamed to a different case of its OWN name.
    await store.update_project(beta["id"], name="BETA")
    assert (await store.get_project(beta["id"]))["name"] == "BETA"
    await store.close()


@pytest.mark.asyncio
async def test_archive_refuses_an_item_unverified_after_the_check(tmp_path):
    """The archive invariant has to be enforced by the UPDATE, not before it.

    ``archive_checklist_item`` validated ``verified``/``reported`` before
    BEGIN IMMEDIATE and then archived unconditionally, so an
    ``update_checklist_item`` that cleared either flag in the gap archived an
    item that no longer satisfied the invariant -- and announced it.
    """
    store = await _task_store(tmp_path)
    task = await store.create_task("prj-1", "seed", "jay")
    item = await store.create_checklist_item(task["id"], "step", "jay")
    await store.update_checklist_item(item["id"], verified=True, reported=True)

    real_execute = store._db.execute
    state = {"raced": False}

    def racing_execute(sql, *args, **kwargs):
        # Clear `verified` in the last moment before the archiving UPDATE runs:
        # the window between the pre-read validation and the write.
        if not state["raced"] and "archived = 1" in sql:
            state["raced"] = True

            async def _unverify_then_archive():
                await real_execute(
                    "UPDATE task_checklist_items SET verified = 0 WHERE id = ?",
                    (item["id"],),
                )
                return await real_execute(sql, *args, **kwargs)

            return _unverify_then_archive()
        return real_execute(sql, *args, **kwargs)

    store._db.execute = racing_execute
    try:
        with pytest.raises(ValueError):
            await store.archive_checklist_item(item["id"])
    finally:
        store._db.execute = real_execute

    assert state["raced"] is True
    assert (await store.get_checklist_item(item["id"]))["archived"] == 0
    await store.close()
