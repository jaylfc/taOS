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
from contextlib import asynccontextmanager

from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def tx(db, store: str = "projects.db"):
    """Run the wrapped writes in one explicit transaction on *db*.

    ``store`` names the owning store in the rollback log line.  BEGIN IMMEDIATE
    takes the write lock up front: a DEFERRED transaction that upgrades from a
    read to a write can fail with ``SQLITE_BUSY_SNAPSHOT``, which
    ``PRAGMA busy_timeout`` does not retry.
    """
    if db is None:
        raise RuntimeError(f"{store}: store is not initialised (call init() first)")
    await db.execute("BEGIN IMMEDIATE")
    try:
        yield db
        await db.commit()
    except BaseException as exc:
        logger.error(
            "projects.db transaction rolled back in %s: %s: %s",
            store, type(exc).__name__, exc,
        )
        await _rollback(db, store)
        raise


async def _rollback(db, store: str) -> None:
    """Roll *db* back, surviving the cancellation that may have caused this.

    When the failure IS a cancellation, a bare ``await db.rollback()`` can be
    cancelled again before it reaches the connection's worker thread -- the
    write lock would then outlive the very error path that exists to release
    it.  Shielding lets the rollback finish even if this coroutine is torn down
    first.
    """
    task = asyncio.ensure_future(db.rollback())
    task.add_done_callback(_log_rollback_failure)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # The shielded rollback keeps running to completion; the original
        # exception is re-raised by the caller.
        pass
    except Exception:
        logger.error("projects.db rollback failed in %s", store, exc_info=True)


def _log_rollback_failure(task: "asyncio.Future") -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("projects.db rollback failed: %r", exc)


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
