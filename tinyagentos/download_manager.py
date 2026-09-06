from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from tinyagentos.clients.retry import with_retry

logger = logging.getLogger(__name__)

# A read timeout is what turns a half-open connection — a Wi-Fi drop, a NAT
# table eviction, a CDN edge that stops sending — into an error instead of a
# task that sits at status="downloading" forever behind a progress bar frozen
# at 63%. It bounds the gap BETWEEN chunks, not the total transfer, so a slow
# but alive multi-gigabyte pull is unaffected.
DOWNLOAD_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)

# Retry budget for a single transfer. Longer than the inference-client default
# because the thing being retried is a multi-gigabyte pull, not a chat call:
# waiting seconds for a mirror to recover is cheap next to restarting one.
DOWNLOAD_MAX_ATTEMPTS = 4
DOWNLOAD_RETRY_BASE_DELAY = 1.0
DOWNLOAD_RETRY_MAX_DELAY = 30.0

# How long a finished (complete/error) task stays queryable before it is
# dropped. The models UI polls /api/models/downloads for a while after a
# transfer ends; anything older than this is history nobody reads, and
# keeping it means every model ever downloaded stays resident for the
# lifetime of the process.
TASK_RETENTION_SECONDS = 3600.0

_TERMINAL_STATUSES = ("complete", "error")


class _RangeRestart(Exception):
    """Raised when the server rejects our resume offset with a 416.

    The stage file holds at least as many bytes as the server is willing to
    serve — a stale or over-long ``.part``. Restarting from zero recovers;
    letting the 416 escape as a 4xx would wedge that model permanently.
    """


# Failures worth another attempt. httpx.TransportError covers timeouts,
# connection resets and protocol errors; a bad URL (4xx) is not in it and must
# surface immediately. with_retry adds 5xx responses on top of this tuple.
DOWNLOAD_RETRY_ON = (httpx.TransportError, _RangeRestart)


def _hash_prefix(sha, path: Path, length: int) -> None:
    """Feed the first ``length`` bytes of ``path`` into ``sha``.

    Runs in a worker thread — the prefix of a resumed model download can be
    tens of gigabytes and must never block the event loop.
    """
    remaining = length
    with open(path, "rb") as f:
        while remaining > 0:
            chunk = f.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            sha.update(chunk)
            remaining -= len(chunk)


def _content_range_total(header: str | None) -> int:
    """Full resource size from a ``Content-Range: bytes X-Y/Z`` header.

    Returns 0 when the header is absent or the total is the unknown ``*``,
    which leaves _validate_download's size check disabled rather than
    comparing against a wrong number.
    """
    if not header or "/" not in header:
        return 0
    total = header.rsplit("/", 1)[1].strip()
    return int(total) if total.isdigit() else 0


@dataclass
class DownloadTask:
    id: str
    url: str
    dest: Path
    total_bytes: int = 0
    downloaded_bytes: int = 0
    status: str = "pending"  # pending | downloading | complete | error
    error: str = ""
    started_at: float = 0
    completed_at: float = 0


class DownloadManager:
    def __init__(self, torrent_settings_store=None):
        self._tasks: dict[str, DownloadTask] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._torrent_settings_store = torrent_settings_store
        # Lazy-instantiated torrent downloader — created on first use so
        # TinyAgentOS installs without libtorrent still boot. The
        # TorrentDownloader import raises TorrentNotAvailable if the
        # Python binding is missing.
        self._torrent = None

    def _get_torrent_downloader(self):
        if self._torrent is not None:
            return self._torrent
        try:
            from tinyagentos.torrent_downloader import TorrentDownloader

            settings = None
            if self._torrent_settings_store is not None:
                settings = self._torrent_settings_store.load()
            self._torrent = TorrentDownloader(settings=settings)
            return self._torrent
        except Exception as exc:
            logger.debug("torrent downloader unavailable: %s", exc)
            return None

    def apply_torrent_settings(self, settings) -> None:
        """Hot-apply new torrent settings to a running libtorrent session.
        No-op if the session hasn't been started yet — next call to
        _get_torrent_downloader will read the fresh store."""
        if self._torrent is not None:
            self._torrent.apply_settings(settings)

    def start_download(
        self,
        download_id: str,
        url: str,
        dest: Path,
        expected_sha256: str | None = None,
        magnet: str | None = None,
        license_allows_redistribution: bool = False,
        web_seeds: list[str] | None = None,
    ) -> DownloadTask:
        """Start a model download.

        If a ``magnet`` URI is supplied AND the variant's licence
        allows redistribution AND libtorrent is installed, the torrent
        swarm is tried first. On peer timeout / SHA mismatch / any
        torrent error the download falls back transparently to HTTP
        using ``url``. The caller sees a single DownloadTask either
        way and never has to branch on transport.

        ``web_seeds`` are the manifest's BEP-19 HTTP seeds (typically the
        HuggingFace resolve URLs) that the swarm rides as a correctness
        fallback. They are passed straight through to the torrent path.
        """
        self._prune_tasks()
        task = DownloadTask(id=download_id, url=url, dest=dest)
        self._tasks[download_id] = task
        self._running[download_id] = asyncio.create_task(
            self._download_with_fallback(
                task,
                expected_sha256=expected_sha256,
                magnet=magnet,
                license_allows_redistribution=license_allows_redistribution,
                web_seeds=web_seeds,
            )
        )
        return task

    def start_installer_task(self, download_id: str, coro) -> DownloadTask:
        """Track a model install driven by a backend-specific installer
        coroutine (e.g. ``RkllamaInstaller.install()``, which pulls the
        weight via the backend's own ``/api/pull`` instead of a raw HTTP/
        torrent transfer) using the same DownloadTask the caller already
        polls via :meth:`get_progress`.

        ``coro`` is either a plain coroutine (no progress reporting -- the
        task's ``percent`` stays at 0 until it finishes), or a callable
        accepting a single ``on_progress(completed, total)`` callback and
        returning the coroutine to run. The callable form lets an installer
        that streams incremental progress (e.g. rkllama's ndjson ``/api/pull``)
        update this task's ``downloaded_bytes``/``total_bytes`` as it goes.
        """
        self._prune_tasks()
        task = DownloadTask(id=download_id, url="", dest=Path())
        self._tasks[download_id] = task

        def _on_progress(completed: int, total: int) -> None:
            task.downloaded_bytes = completed
            task.total_bytes = total

        if callable(coro) and not asyncio.iscoroutine(coro):
            coro = coro(_on_progress)

        self._running[download_id] = asyncio.create_task(self._run_installer(task, coro))
        return task

    async def _run_installer(self, task: DownloadTask, coro) -> None:
        task.status = "downloading"
        task.started_at = time.time()
        try:
            result = await coro
        except Exception as exc:  # noqa: BLE001 — surface as a task error, never raise into the poll loop
            task.status = "error"
            task.error = str(exc)
            logger.error("Installer task failed for %s: %s", task.id, exc)
            return
        if not result.get("success"):
            task.status = "error"
            task.error = result.get("error", "install failed")
            return
        task.status = "complete"
        task.completed_at = time.time()

    def get_progress(self, download_id: str) -> DownloadTask | None:
        return self._tasks.get(download_id)

    def list_active(self) -> list[DownloadTask]:
        return [t for t in self._tasks.values() if t.status in ("pending", "downloading")]

    def list_all(self) -> list[DownloadTask]:
        return list(self._tasks.values())

    async def _validate_download(
        self,
        task: DownloadTask,
        expected_sha256: str | None = None,
        computed_sha256: str | None = None,
    ) -> str | None:
        """Check a finished download before it is marked complete.

        Returns None if the download is valid, or an error message
        describing why it isn't. Applies to both the torrent and HTTP
        paths so neither can mark a task complete when nothing (or the
        wrong thing) was actually written to disk.
        """
        if not task.dest.exists() or task.dest.stat().st_size == 0:
            return "download produced no data"
        if task.total_bytes and task.dest.stat().st_size != task.total_bytes:
            return "size mismatch"
        if expected_sha256:
            digest = computed_sha256
            if digest is None:
                # Fallback for a caller that did not stream the hash. Reading a
                # potentially multi-GB model is offloaded to a thread so it
                # never blocks the event loop.
                digest = await asyncio.to_thread(
                    lambda: hashlib.sha256(task.dest.read_bytes()).hexdigest()
                )
            # Hex digests are case-insensitive; a caller passing an uppercase
            # expected value must not be treated as a mismatch.
            if digest.lower() != expected_sha256.lower():
                return "SHA256 mismatch"
        return None

    async def _download_with_fallback(
        self,
        task: DownloadTask,
        *,
        expected_sha256: str | None = None,
        magnet: str | None = None,
        license_allows_redistribution: bool = False,
        web_seeds: list[str] | None = None,
    ) -> None:
        """Hybrid download path: swarm first, HTTP fallback.

        Torrent path is only attempted if the caller provided a magnet
        AND the manifest allowed redistribution AND libtorrent is
        installed. Any torrent-side failure (peer timeout, sha mismatch,
        runtime error) logs a warning and falls through to the regular
        HTTP path so the user always gets their model.
        """
        torrent = None
        if (
            magnet
            and license_allows_redistribution
            and expected_sha256
            and (torrent := self._get_torrent_downloader()) is not None
        ):
            # Headless passkey fetch: if this host has joined an account mesh,
            # the controller token unlocks the private taOSnet tracker. A null
            # passkey (not joined, no passkey yet, revoked, or offline) leaves
            # the download on the web-seed baseline. Never raises.
            from tinyagentos.taosnet.passkey_client import (
                fetch_passkey,
                get_controller_token,
            )

            passkey = await fetch_passkey(get_controller_token())
            task.status = "downloading"
            task.started_at = time.time()
            try:
                def _progress(t):
                    task.total_bytes = t.total_bytes
                    task.downloaded_bytes = t.downloaded_bytes

                await torrent.download(
                    task_id=task.id,
                    magnet_or_torrent=magnet,
                    dest=task.dest,
                    expected_sha256=expected_sha256,
                    progress_cb=_progress,
                    passkey=passkey,
                    web_seeds=web_seeds,
                )
                # torrent.download() already SHA-verified the file internally
                # (and raised on mismatch), so re-hashing here would just re-read
                # a multi-GB file to no benefit. Only the cheap non-empty / size
                # floor is needed on this path.
                error = await self._validate_download(task)
                if error:
                    task.dest.unlink(missing_ok=True)
                    task.status = "error"
                    task.error = error
                    logger.error(
                        "Torrent download for %s produced an invalid result (%s)",
                        task.id,
                        error,
                    )
                    return
                task.status = "complete"
                task.completed_at = time.time()
                logger.info("Downloaded %s via torrent swarm", task.id)
                return
            except Exception as exc:
                logger.warning(
                    "Torrent download for %s failed (%s) — falling back to HTTP",
                    task.id,
                    exc,
                )
                # Reset state so the HTTP path starts clean
                task.downloaded_bytes = 0
                task.total_bytes = 0
                task.status = "pending"
                task.error = ""

        await self._download(task, expected_sha256)

    async def _download(self, task: DownloadTask, expected_sha256: str | None = None):
        """HTTP transfer, staged through a ``.part`` file.

        Nothing is ever written to ``task.dest`` until a complete, validated
        body is on disk, so a failure can never leave a corrupt weight sitting
        at the canonical path where every later "is this model installed?"
        existence check would take it for the real thing. The stage file
        survives a failure on purpose: it is what the next attempt — this
        process or the next boot — resumes from.
        """
        task.status = "downloading"
        task.started_at = time.time()
        part = task.dest.with_name(task.dest.name + ".part")
        promoted = False
        try:
            task.dest.parent.mkdir(parents=True, exist_ok=True)
            digest = await with_retry(
                lambda: self._stream_to_part(task, part),
                max_attempts=DOWNLOAD_MAX_ATTEMPTS,
                base_delay=DOWNLOAD_RETRY_BASE_DELAY,
                max_delay=DOWNLOAD_RETRY_MAX_DELAY,
                retry_on=DOWNLOAD_RETRY_ON,
            )
            part.replace(task.dest)
            promoted = True
            error = await self._validate_download(task, expected_sha256, computed_sha256=digest)
            if error:
                # The bytes are wrong, so the promoted file is worthless: this
                # attempt just replaced task.dest with a bad copy, and that
                # copy (not the now-gone .part) is what has to go.
                task.dest.unlink(missing_ok=True)
                task.status = "error"
                task.error = error
            else:
                task.status = "complete"
                task.completed_at = time.time()
        except Exception as e:
            # Only clean up task.dest when THIS attempt is the one that put a
            # (possibly bad) file there. _stream_to_part writes exclusively to
            # `part`, so a failure before promotion leaves task.dest exactly as
            # it was -- which, for a re-download of an already-installed model,
            # is a perfectly good file. Deleting it here would destroy a valid
            # install over a transient failure of the NEW attempt.
            if promoted:
                task.dest.unlink(missing_ok=True)
            task.status = "error"
            task.error = str(e)
            logger.error("Download failed for %s: %s", task.id, e)

    async def _stream_to_part(self, task: DownloadTask, part: Path) -> str:
        """One HTTP attempt, appending into the ``.part`` stage file.

        Returns the SHA-256 hex digest of everything the stage file holds.
        Whatever an interrupted attempt already wrote is asked for with a
        ``Range`` header rather than thrown away, so a 40 GB model that died
        at 39 GB does not restart from zero. A server that ignores the header
        (answering 200 instead of 206) restarts the file cleanly instead of
        concatenating a second copy onto the first.
        """
        resume_from = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
        sha = hashlib.sha256()
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            async with client.stream("GET", task.url, headers=headers) as resp:
                if resp.status_code == 416 and resume_from:
                    part.unlink(missing_ok=True)
                    raise _RangeRestart(
                        f"server rejected resume offset {resume_from}; restarting"
                    )
                resp.raise_for_status()
                resumed = resume_from > 0 and resp.status_code == 206
                if not resumed:
                    resume_from = 0
                else:
                    # Re-hash the prefix already on disk so the streamed digest
                    # covers the whole file, not just this attempt's tail.
                    await asyncio.to_thread(_hash_prefix, sha, part, resume_from)
                # Content-Length is the size of the ON-THE-WIRE body — and on a
                # 206 that is only the REMAINING bytes, with the full size in
                # the Content-Range total. When the response is content-encoded
                # (gzip/br/deflate/zstd), httpx's aiter_bytes() transparently
                # decompresses, so the bytes we write to disk are LARGER than
                # Content-Length. Treating that as the expected on-disk size
                # would make _validate_download flag a perfectly good download
                # as a "size mismatch" and delete it, so leave total_bytes at 0
                # (unknown) for encoded responses and rely on the SHA check.
                if resp.headers.get("content-encoding"):
                    task.total_bytes = 0
                elif resumed:
                    task.total_bytes = _content_range_total(resp.headers.get("content-range"))
                else:
                    total = resp.headers.get("content-length")
                    task.total_bytes = int(total) if total else 0
                task.downloaded_bytes = resume_from
                with open(part, "ab" if resumed else "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        sha.update(chunk)
                        task.downloaded_bytes += len(chunk)
        return sha.hexdigest()

    def _prune_tasks(self) -> None:
        """Drop finished tasks older than the retention window.

        Without this every download ever started stays in ``_tasks`` (and its
        asyncio.Task in ``_running``) for the lifetime of the process, and
        /api/models/downloads returns the lot. Tasks still pending or
        downloading are never pruned however long they have been running.
        """
        cutoff = time.time() - TASK_RETENTION_SECONDS
        for download_id, task in list(self._tasks.items()):
            if task.status not in _TERMINAL_STATUSES:
                continue
            finished_at = task.completed_at or task.started_at
            if finished_at and finished_at > cutoff:
                continue
            del self._tasks[download_id]
            self._running.pop(download_id, None)
