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

import pytest

from tinyagentos.projects import tx as tx_module
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
