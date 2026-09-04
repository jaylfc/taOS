"""Explicit transactions for the stores that share ``projects.db``.

Eight stores -- ProjectStore, ProjectTaskStore, ProjectElementStore,
ProjectCanvasStore, DocReviewStore, ProjectNotesStore, ProjectListsStore and
ProjectListEntriesStore -- are opened by ``app.py`` on the SAME
``data_dir/projects.db`` file, each with its own aiosqlite connection.

Python's sqlite3 driver issues an implicit ``BEGIN`` before the first DML on a
connection.  If the coroutine raises -- or is cancelled, which is what a client
disconnect does to a request task -- between that DML and ``commit()``, nothing
calls ``rollback()``: the connection keeps the WAL write lock forever and every
other connection on the file then fails with
``sqlite3.OperationalError: database is locked`` until the process restarts.
That is not hypothetical; on 2026-09-02 a single wedged connection took the
whole board API down (task create, claim_task, ...) for six minutes.

The mechanism here removes the failure mode rather than one of its sites:

* ``ProjectsDBStore`` opens its connection with ``isolation_level=None`` so the
  driver never starts a transaction behind the code's back.
* Every write goes through ``tx()``, which BEGINs explicitly, COMMITs on
  success, and ROLLBACKs on ANY exception -- ``BaseException``, so
  ``asyncio.CancelledError`` counts -- before re-raising.
* A rollback logs at ERROR with the store name, so the next leak shows up in
  the journal instead of silently locking the database.

Reads need no transaction and must stay outside ``tx()``; so must anything that
is not a database write (event publishing, audit records), which would
otherwise hold the write lock across an unrelated await.
"""
from __future__ import annotations

import asyncio
import logging
import weakref
from contextlib import asynccontextmanager

from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)

# One asyncio lock per connection.  A sqlite connection can only be inside one
# transaction at a time, so two coroutines writing through the same store (two
# concurrent requests, say) must queue rather than have the second one hit
# "cannot start a transaction within a transaction".  Keyed weakly so a closed
# store's lock goes away with it.
_CONNECTION_LOCKS: "weakref.WeakKeyDictionary[object, asyncio.Lock]" = (
    weakref.WeakKeyDictionary()
)


def _lock_for(db) -> asyncio.Lock:
    lock = _CONNECTION_LOCKS.get(db)
    if lock is None:
        lock = asyncio.Lock()
        _CONNECTION_LOCKS[db] = lock
    return lock


@asynccontextmanager
async def tx(db, store: str = "projects.db"):
    """Run the wrapped writes in one explicit transaction on *db*.

    ``store`` names the owning store in the rollback log line.  BEGIN IMMEDIATE
    takes the write lock up front: a DEFERRED transaction that upgrades from a
    read to a write can fail with ``SQLITE_BUSY_SNAPSHOT``, which
    ``PRAGMA busy_timeout`` does not retry.

    Only writes belong in here.  The block runs under the connection's write
    lock, so an unrelated await inside it would stall every other writer on
    that store.
    """
    if db is None:
        raise RuntimeError(f"{store}: store is not initialised (call init() first)")
    lock = _lock_for(db)
    await lock.acquire()
    # True once the rollback task owns the release (see _rollback).
    handed_off = False
    try:
        await db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            await db.commit()
        except BaseException as exc:
            logger.error(
                "projects.db transaction rolled back in %s: %s: %s",
                store, type(exc).__name__, exc,
            )
            handed_off = await _rollback(db, store, lock)
            raise
    finally:
        if not handed_off:
            lock.release()


async def _rollback(db, store: str, lock: asyncio.Lock) -> bool:
    """Roll *db* back, surviving the cancellation that may have caused this.

    When the failure IS a cancellation, a bare ``await db.rollback()`` can be
    cancelled again before it reaches the connection's worker thread -- the
    write lock would then outlive the very error path that exists to release
    it.  Shielding lets the rollback finish even if this coroutine is torn down
    first.

    Returns True when the shielded rollback outlived this coroutine and now
    owns releasing *lock*: the next writer must not BEGIN until the rollback
    has actually landed on the connection.
    """
    task = asyncio.ensure_future(db.rollback())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(lambda t: _finish_rollback(t, store, lock))
        return True
    except Exception:
        logger.error("projects.db rollback failed in %s", store, exc_info=True)
    return False


def _finish_rollback(task: "asyncio.Future", store: str, lock: asyncio.Lock) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error(
            "projects.db rollback failed in %s: %r", store, task.exception()
        )
    if lock.locked():
        lock.release()


class ProjectsDBStore(BaseStore):
    """Base class for every store that lives in the shared ``projects.db``.

    Subclasses MUST wrap each write in ``async with self._tx():`` -- with
    ``AUTOCOMMIT`` there is no implicit transaction, so an unwrapped
    multi-statement write would no longer be atomic.
    """

    AUTOCOMMIT = True

    def _tx(self):
        """Explicit transaction on this store's connection, named for the log."""
        return tx(self._db, type(self).__name__)
