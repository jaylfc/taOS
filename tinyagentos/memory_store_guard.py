"""Memory store guard: ask the real ``taosmd migrate`` CLI what shape the store is in.

taOS never judges the on-disk format itself. It runs the taOSmd command line and
maps the exit code, following the contract taOSmd documents
(``taosmd.store_probe`` and ``taosmd migrate --help``):

``taosmd migrate --probe`` (read only, changes nothing):

    0   current   every database is at the format this build writes
    10  old       a schema step is pending or the vectors are still JSON. A
                  supported state: served as is, nothing is converted on open
    11  unknown   unreadable, a live writer or an uncheckpointed WAL, rows that
                  contradict the marker
    12  newer     written by a newer build than this one

``taosmd migrate --convert-vectors`` (explicit, one way, backup kept):

    0   converted or already current
    1   unexpected error (the message names the backup when there is one)
    2   refused, store untouched (another process is using it)
    3   unknown or newer format, store untouched
    4   failed and restored (store identical to before)

Anything else, or a CLI that cannot run, is reported as such. Nothing here
guesses: an answer the guard does not understand is said plainly and the store
is not touched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROBE_CURRENT = 0
PROBE_OLD = 10
PROBE_UNKNOWN = 11
PROBE_NEWER = 12

CONVERT_OK = 0
CONVERT_UNEXPECTED = 1
CONVERT_REFUSED = 2
CONVERT_UNKNOWN_OR_NEWER = 3
CONVERT_RESTORED = 4

PROBE_TIMEOUT_S = 60.0
CONVERT_TIMEOUT_S = 3600.0

#: Follow-up the person can take, named so the UI and agents act on the same word.
NEXT_NONE = "none"
NEXT_CONVERT_OPTIONAL = "convert_optional"
NEXT_FREE_THE_STORE = "free_the_store"
NEXT_UPDATE_TAOS = "update_taos"
NEXT_REPAIR_TAOSMD = "repair_taosmd"
NEXT_CHECK_BY_HAND = "check_by_hand"


@dataclass
class StoreStatus:
    """What the probe found, in words a person can act on."""

    state: str  # current | old | unknown | newer | unavailable | unexpected
    exit_code: int | None
    can_serve: bool
    message: str
    next_step: str
    convert_available: bool = False
    databases: list[dict[str, Any]] = field(default_factory=list)
    checked_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConvertOutcome:
    """Result of an explicit vector conversion."""

    ok: bool
    state: str  # converted | already_current | refused | unknown_format | restored | error | unavailable
    exit_code: int | None
    message: str
    backup_path: str | None = None
    rows_converted: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryStoreGuardError(RuntimeError):
    """Raised at start-up when the store must not be opened. The text says what to do."""


def _cli(data_dir: Path, *args: str) -> list[str]:
    return [sys.executable, "-m", "taosmd", "--data-dir", str(data_dir), "migrate", *args]


async def _run(argv: list[str], timeout: float) -> tuple[int | None, str, str, str | None]:
    """Run the CLI. Returns ``(exit_code, stdout, stderr, problem)``; problem is None on a normal run."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return None, "", "", f"the taosmd command could not be started ({type(exc).__name__}: {exc})"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()  # our own child, by handle
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.error("taosmd migrate did not exit after kill (pid %s)", proc.pid)
        return None, "", "", f"the taosmd command did not finish within {int(timeout)} seconds"
    return (
        proc.returncode,
        (out or b"").decode(errors="replace"),
        (err or b"").decode(errors="replace"),
        None,
    )


def _has_store_files(data_dir: Path) -> bool:
    """True when any database the taOSmd migration registry tracks exists in ``data_dir``.

    A data dir without any is a first install: taOSmd's own probe calls that
    ``current``, so the subprocess is skipped. If the registry cannot be read the
    probe runs anyway.
    """
    try:
        from taosmd.migrations import DB_FILES
    except Exception:  # noqa: BLE001 - run the real probe and let it report
        return True
    if not data_dir.is_dir():
        return False
    return any((data_dir / name).exists() for name in DB_FILES.values())


def _db_rows(report: Any) -> list[dict[str, Any]]:
    if not isinstance(report, dict):
        return []
    rows = []
    for d in report.get("databases") or []:
        if isinstance(d, dict):
            rows.append({
                "db": d.get("db"),
                "state": d.get("state"),
                "reason": d.get("reason") or "",
                "path": d.get("path") or "",
            })
    return rows


async def probe(data_dir: Path | str, *, timeout: float = PROBE_TIMEOUT_S) -> StoreStatus:
    """Run ``taosmd migrate --probe --json`` on ``data_dir`` and map the exit code."""
    root = Path(data_dir)
    now = time.time()
    if not _has_store_files(root):
        return StoreStatus(
            state="current", exit_code=PROBE_CURRENT, can_serve=True, next_step=NEXT_NONE,
            message="No memory store exists yet, so there is nothing to check.",
            checked_at=now,
        )

    code, out, err, problem = await _run(_cli(root, "--probe", "--json"), timeout)
    if problem is not None:
        return StoreStatus(
            state="unavailable", exit_code=None, can_serve=False, next_step=NEXT_REPAIR_TAOSMD,
            message=(
                f"taOS could not check your memory store: {problem}. Nothing was changed and "
                "your memories are safe. Update taOS from Settings, Updates (that reinstalls "
                "taOSmd), then start taOS again."
            ),
            checked_at=now,
        )
    try:
        report = json.loads(out)
    except ValueError:
        report = None
    dbs = _db_rows(report)

    if code == PROBE_CURRENT:
        return StoreStatus(
            state="current", exit_code=code, can_serve=True, next_step=NEXT_NONE,
            message="Your memory store is up to date.", databases=dbs, checked_at=now,
        )
    if code == PROBE_OLD:
        vectors_old = any(d["db"] == "vector_memory" and d["state"] == "old" for d in dbs)
        message = (
            "Your memory store was written by an older taOSmd. It works as it is and "
            "nothing is lost: taOSmd brings its tables up to date when taOS opens it."
        )
        if vectors_old:
            message += (
                " Your vectors are still in the older format; taOSmd never converts them on "
                "its own. You can convert them to the newer, smaller format from Settings, "
                "Memory. That step is optional and one way, and a backup is kept."
            )
        return StoreStatus(
            state="old", exit_code=code, can_serve=True,
            next_step=NEXT_CONVERT_OPTIONAL if vectors_old else NEXT_NONE,
            convert_available=vectors_old, message=message, databases=dbs, checked_at=now,
        )
    if code == PROBE_UNKNOWN:
        return StoreStatus(
            state="unknown", exit_code=code, can_serve=False, next_step=NEXT_FREE_THE_STORE,
            message=(
                "taOS cannot tell what state your memory store is in, so it did not open it. "
                "Your memories were not touched. The usual cause is another taOS or taOSmd "
                "process still using the data folder, or one that stopped in the middle of a "
                "write. What to do: stop any other taOS or taOSmd on this machine, then start "
                "taOS again. If it still stops, run "
                f"`taosmd --data-dir {root} migrate --probe` and keep its output. Do not "
                "delete any .db file."
            ),
            databases=dbs, checked_at=now,
        )
    if code == PROBE_NEWER:
        return StoreStatus(
            state="newer", exit_code=code, can_serve=False, next_step=NEXT_UPDATE_TAOS,
            message=(
                "Your memory store was written by a newer taOSmd than this taOS includes. "
                "Opening it with this version could damage it, so taOS did not open it. "
                "Your memories were not touched. What to do: update taOS from Settings, "
                "Updates (or install the newer taOS release), then start taOS again."
            ),
            databases=dbs, checked_at=now,
        )
    tail = (err.strip().splitlines() or [""])[-1][:200]
    return StoreStatus(
        state="unexpected", exit_code=code, can_serve=False, next_step=NEXT_CHECK_BY_HAND,
        message=(
            f"The memory store check returned an answer taOS does not know (exit {code}"
            f"{': ' + tail if tail else ''}). Nothing was changed. What to do: update taOS "
            f"from Settings, Updates. If it persists, run `taosmd --data-dir {root} migrate "
            "--probe` and keep its output."
        ),
        databases=dbs, checked_at=now,
    )


#: taOSmd's own words (``taosmd.store_probe``) for a non-empty write-ahead log next to a database.
_WAL_REASON = "live writer or uncheckpointed WAL"


def _settle_wal_files(paths: list[str]) -> list[str]:
    """Checkpoint the write-ahead logs a killed process left behind. Returns the paths settled.

    A WAL holds committed writes that were not yet merged into the database file;
    merging them loses nothing. A checkpoint that reports ``busy`` means a live
    writer still holds the database, so that file is left exactly as it is.
    """
    import sqlite3

    settled: list[str] = []
    for path in paths:
        conn = None
        try:
            conn = sqlite3.connect(path, timeout=5.0)
            busy, _log, _done = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if not busy:
                settled.append(path)
        except sqlite3.Error as exc:
            logger.warning("could not settle the write-ahead log of %s: %s", path, exc)
        finally:
            if conn is not None:
                conn.close()
    return settled


async def check_at_startup(data_dir: Path | str) -> StoreStatus:
    """Probe before any store is opened; raise with the plain instructions when it must not open.

    A process that was killed mid-write leaves a non-empty write-ahead log, which
    the probe reports as unknown (exit 11). Refusing to start there would never
    heal, because nothing would open the database to merge it. When every unknown
    database is unknown for exactly that reason, the committed writes in the logs
    are merged first (a checkpoint, no data changes) and the store is probed
    again. A database a live process still holds is left alone and still refuses.
    """
    status = await probe(data_dir)
    if status.state == "unknown":
        unknown = [d for d in status.databases if d.get("state") == "unknown"]
        if unknown and all(d.get("reason") == _WAL_REASON and d.get("path") for d in unknown):
            settled = await asyncio.to_thread(_settle_wal_files, [d["path"] for d in unknown])
            if settled:
                logger.warning(
                    "memory store: merged the write-ahead log of %d database(s) left by a process "
                    "that stopped mid-write (committed writes only, nothing changed)", len(settled),
                )
                status = await probe(data_dir)
    if status.can_serve:
        if status.state == "old":
            logger.info("memory store check: %s", status.message)
        return status
    logger.error("memory store check refused start-up (%s): %s", status.state, status.message)
    raise MemoryStoreGuardError(status.message)


async def release_taosmd_stores(data_dir: Path | str) -> list[str]:
    """Close the stores ``taosmd.answer_context`` and ``taosmd.api`` keep open for ``data_dir``.

    taOSmd caches one set of open stores per data dir for the life of the process
    (``taosmd.api._stores_cache``; there is no public close). The conversion takes
    an exclusive lock on the vector store from another process, so while this
    process still holds the cached one it would always be refused. Releasing the
    cache lets the next recall reopen what it needs. Returns what was closed.
    """
    try:
        from taosmd import api as _api
    except Exception:  # noqa: BLE001 - nothing cached if taosmd cannot be imported
        return []
    cache = getattr(_api, "_stores_cache", None)
    lock = getattr(_api, "_stores_lock", None)
    if not isinstance(cache, dict):
        logger.warning("taosmd.api has no _stores_cache; cannot release cached stores before the conversion")
        return []
    key = _api._resolve_data_dir(Path(data_dir))
    closed: list[str] = []

    async def _release() -> None:
        stores = cache.pop(key, None)
        for name, store in (stores or {}).items():
            closer = getattr(store, "close", None)
            if closer is None:
                continue
            try:
                res = closer()
                if asyncio.iscoroutine(res):
                    await res
                closed.append(name)
            except Exception:  # noqa: BLE001 - logged; the conversion reports if the store is still held
                logger.warning("could not close cached taosmd store %r", name, exc_info=True)

    if lock is not None:
        async with lock:
            await _release()
    else:
        await _release()
    return closed


async def convert_vectors(data_dir: Path | str, *, timeout: float = CONVERT_TIMEOUT_S) -> ConvertOutcome:
    """Run ``taosmd migrate --convert-vectors --json``. The caller has the person's consent."""
    root = Path(data_dir)
    await release_taosmd_stores(root)
    code, out, err, problem = await _run(_cli(root, "--convert-vectors", "--json"), timeout)
    if problem is not None:
        return ConvertOutcome(
            ok=False, state="unavailable", exit_code=None,
            message=(
                f"The conversion could not run: {problem}. Nothing was changed. If it stopped "
                "part way, taOSmd restores the store from its backup; check Settings, Memory."
            ),
        )
    try:
        result = json.loads(out)
    except ValueError:
        result = {}
    if not isinstance(result, dict):
        result = {}
    backup = result.get("backup_path")
    detail = str(result.get("message") or "").strip()
    rows = int(result.get("rows_converted") or 0)

    if code == CONVERT_OK:
        done = result.get("status") == "already_current"
        return ConvertOutcome(
            ok=True, state="already_current" if done else "converted", exit_code=code,
            backup_path=backup, rows_converted=rows,
            message=detail or ("Your vectors are already in the newer format." if done else "Your memory vectors were converted."),
        )
    if code == CONVERT_REFUSED:
        return ConvertOutcome(
            ok=False, state="refused", exit_code=code, backup_path=backup,
            message=(
                "The conversion did not start because something else is using the memory "
                f"store. Nothing was changed. {detail} Stop other taOS or taOSmd processes "
                "that use this data folder and try again."
            ).strip(),
        )
    if code == CONVERT_UNKNOWN_OR_NEWER:
        return ConvertOutcome(
            ok=False, state="unknown_format", exit_code=code, backup_path=backup,
            message=f"The memory store is in a format taOSmd will not convert. Nothing was changed. {detail}".strip(),
        )
    if code == CONVERT_RESTORED:
        return ConvertOutcome(
            ok=False, state="restored", exit_code=code, backup_path=backup,
            message=(
                "The conversion failed and your memory store was restored exactly as it was. "
                f"{detail}"
            ).strip(),
        )
    tail = (err.strip().splitlines() or [""])[-1][:200]
    return ConvertOutcome(
        ok=False, state="error", exit_code=code, backup_path=backup,
        message=(
            f"The conversion stopped with an unexpected result (exit {code}). {detail} {tail} "
            + (f"A backup is at {backup}. " if backup else "")
            + "Do not delete any .db file."
        ).strip(),
    )
