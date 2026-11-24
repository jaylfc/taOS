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

Two consequences of one connection per store carrying both the reads and the
writes, each handled here rather than at the call sites:

* A read on that connection would see the connection's OWN uncommitted rows.
  aiosqlite runs every statement on the connection's single worker thread, so
  a read issued while another task holds an open transaction is executed
  between that transaction's statements -- and can return rows that then roll
  back.  ``read()`` (and ``ProjectsDBStore._read``) queues such a read behind
  the transaction on the same per-connection lock; a read from the task that
  OWNS the transaction still runs straight through, because a write must be
  able to read what it has just written.
* Effects that follow a write -- an event, an audit row -- are correct after
  the outermost ``tx()`` commits and wrong before it.  A nested scope joins the
  transaction already running and returns without committing, so a mutation
  that emits right after its own ``async with self._tx():`` block would emit
  while the enclosing transaction can still roll back.  ``after_commit()``
  queues those effects on the outermost transaction: flushed once it commits,
  dropped when it rolls back.
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

# The task that currently holds each connection's transaction.  The lock above
# is not re-entrant, so a store method that writes through the same connection
# from inside an open transaction -- a write calling another write -- would wait
# on a lock its own task already holds, and wait forever.  That is a worse
# failure than the one this module removes, so a nested scope in the SAME task
# JOINS the transaction already running instead of starting a second one: the
# outermost scope owns the single COMMIT or ROLLBACK, which is also what makes
# the pair atomic.
_ACTIVE_TX: "weakref.WeakKeyDictionary[object, asyncio.Task]" = (
    weakref.WeakKeyDictionary()
)

# Effects queued by the transaction currently open on each connection -- events
# and audit rows, which are only true once the write they describe has
# committed.  Owned by the OUTERMOST scope: a nested scope joins the open
# transaction and returns without committing, so anything it emits on its way
# out would otherwise announce a write the outer scope can still roll back.
_PENDING_EFFECTS: "weakref.WeakKeyDictionary[object, list]" = (
    weakref.WeakKeyDictionary()
)


def after_commit(db, effect) -> bool:
    """Queue *effect* until the transaction open on *db* commits.

    *effect* is a zero-argument coroutine function.  Returns True when it was
    queued and False when there is no transaction on this connection owned by
    the calling task -- in which case the caller must run it itself, right
    away, exactly as it did before.
    """
    current = asyncio.current_task()
    if _ACTIVE_TX.get(db) is not current:
        return False
    pending = _PENDING_EFFECTS.get(db)
    if pending is None:
        return False
    pending.append(effect)
    return True


async def _flush_effects(effects: list, store: str) -> None:
    """Run the queued effects of a transaction that committed.

    The write has landed; an effect that fails must not be able to unwind it or
    to swallow the ones behind it, so each failure is logged and the rest still
    run.  That matches how ``_record_audit`` already treats the audit log.
    """
    for effect in effects:
        try:
            await effect()
        except Exception:
            logger.error(
                "projects.db post-commit effect failed in %s", store, exc_info=True
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

    current = asyncio.current_task()
    if current is not None and _ACTIVE_TX.get(db) is current:
        # Already inside a transaction on this connection, in this task: join it
        # rather than deadlock on our own lock.  No BEGIN, no COMMIT, no
        # ROLLBACK here -- the outermost scope owns all three, so a failure
        # anywhere in the nest still unwinds the whole thing.
        yield db
        return

    lock = _lock_for(db)
    await lock.acquire()
    _ACTIVE_TX[db] = current
    # Effects queued by this scope and every scope nested in it.  Flushed only
    # after the COMMIT below, discarded with the transaction on any failure.
    effects: list = []
    _PENDING_EFFECTS[db] = effects
    committed = False
    # True once the rollback task owns the release (see _rollback).
    handed_off = False
    try:
        # The BEGIN is inside the rollback scope, not before it.  It is the
        # statement that WAITS -- while another connection holds the write lock
        # it blocks for the whole busy_timeout -- and aiosqlite has already
        # handed it to the connection's worker thread, which runs it whether or
        # not the awaiting task is still there.  A cancellation here therefore
        # opens a transaction nobody is left to close, which is the same wedge
        # this helper exists to prevent, one statement earlier.  The rollback
        # queues behind the BEGIN on that same worker thread, so it lands after
        # it either way; on a BEGIN that genuinely failed there is no
        # transaction and rollback() is a no-op.
        await db.execute("BEGIN IMMEDIATE")
        yield db
        await db.commit()
        committed = True
    except BaseException as exc:
        logger.error(
            "projects.db transaction rolled back in %s: %s: %s",
            store, type(exc).__name__, exc,
        )
        handed_off = await _rollback(db, store, lock)
        raise
    finally:
        _ACTIVE_TX.pop(db, None)
        _PENDING_EFFECTS.pop(db, None)
        if not handed_off:
            lock.release()
        # After the release, never before: an effect is not a database write,
        # and running it under the write lock would stall every other writer on
        # this store for the length of an unrelated await.  `committed` is
        # False on every failure path, which is what drops the queue.
        if committed and effects:
            await _flush_effects(effects, store)


@asynccontextmanager
async def read(db, store: str = "projects.db"):
    """Hold *db* still for the duration of a read.

    A store's reads and its writes share one connection, and sqlite shows a
    connection its own uncommitted rows.  Without this gate a read issued while
    another task has a transaction open on the connection is executed between
    that transaction's statements and returns rows that may never commit -- a
    dirty read that survives the rollback in whatever the caller did with it.

    The gate is the transaction lock itself, so the read simply queues behind
    the transaction.  A read from the task that OWNS the open transaction runs
    straight through: it must see the writes that transaction has already made,
    and the lock is not re-entrant, so waiting for it would deadlock.
    """
    if db is None:
        raise RuntimeError(f"{store}: store is not initialised (call init() first)")

    current = asyncio.current_task()
    if current is not None and _ACTIVE_TX.get(db) is current:
        yield db
        return

    lock = _lock_for(db)
    await lock.acquire()
    try:
        yield db
    finally:
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
    """Log how the handed-off rollback ended, then always free the lock.

    The release is unconditional on purpose.  Whatever happened to the
    rollback, holding the per-connection lock forever would block every later
    write on this store with no way back except a restart -- the exact symptom
    this module exists to remove.  A transaction that survived is the lesser
    evil, and both bad endings are logged at ERROR so the journal names the
    store instead of leaving the next lock-up unexplained.
    """
    if task.cancelled():
        logger.error(
            "projects.db rollback was itself cancelled in %s: the connection may "
            "still hold an open transaction",
            store,
        )
    elif task.exception() is not None:
        logger.error(
            "projects.db rollback failed in %s: %r", store, task.exception()
        )
    if lock.locked():
        lock.release()


class ProjectsDBStore(BaseStore):
    """Base class for every store that lives in the shared ``projects.db``.

    Subclasses MUST wrap each write in ``async with self._tx():`` -- with
    ``AUTOCOMMIT`` there is no implicit transaction, so an unwrapped
    multi-statement write would no longer be atomic -- and MUST run each read
    through ``async with self._read(...)`` rather than ``self._db.execute``, or
    it can come back with another task's uncommitted rows.
    """

    AUTOCOMMIT = True

    def _tx(self):
        """Explicit transaction on this store's connection, named for the log."""
        return tx(self._db, type(self).__name__)

    @asynccontextmanager
    async def _read(self, sql: str, params=()):
        """Run one SELECT, gated against another task's open transaction.

        Drop-in for ``async with self._db.execute(sql, params) as cur:``.  Keep
        the body to fetching and shaping rows: it runs under the connection's
        transaction lock, so an unrelated await inside it stalls every writer
        on this store.
        """
        async with read(self._db, type(self).__name__):
            async with self._db.execute(sql, params) as cur:
                yield cur

    async def _after_commit(self, effect) -> None:
        """Run *effect* now, or once the enclosing transaction commits.

        ``effect`` is a zero-argument coroutine function.  Outside a
        transaction -- where every mutation in these stores emits its events
        and audit rows -- it runs immediately, as it always has.  Inside one
        (a mutation called from another mutation, which joins rather than
        nests) it is queued on the outermost transaction and dropped if that
        transaction rolls back.
        """
        if not after_commit(self._db, effect):
            await effect()
