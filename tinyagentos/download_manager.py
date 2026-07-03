from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


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
    ) -> DownloadTask:
        """Start a model download.

        If a ``magnet`` URI is supplied AND the variant's licence
        allows redistribution AND libtorrent is installed, the torrent
        swarm is tried first. On peer timeout / SHA mismatch / any
        torrent error the download falls back transparently to HTTP
        using ``url``. The caller sees a single DownloadTask either
        way and never has to branch on transport.
        """
        task = DownloadTask(id=download_id, url=url, dest=dest)
        self._tasks[download_id] = task
        self._running[download_id] = asyncio.create_task(
            self._download_with_fallback(
                task,
                expected_sha256=expected_sha256,
                magnet=magnet,
                license_allows_redistribution=license_allows_redistribution,
            )
        )
        return task

    def start_installer_task(self, download_id: str, coro) -> DownloadTask:
        """Track a model install driven by a backend-specific installer
        coroutine (e.g. ``RkllamaInstaller.install()``, which pulls the
        weight via the backend's own ``/api/pull`` instead of a raw HTTP/
        torrent transfer) using the same DownloadTask the caller already
        polls via :meth:`get_progress`. These installers don't report
        incremental byte progress, so ``percent`` stays at 0 until the
        task finishes.
        """
        task = DownloadTask(id=download_id, url="", dest=Path())
        self._tasks[download_id] = task
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
            and (torrent := self._get_torrent_downloader()) is not None
        ):
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
        task.status = "downloading"
        task.started_at = time.time()
        sha = hashlib.sha256()
        try:
            task.dest.parent.mkdir(parents=True, exist_ok=True)
            async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
                async with client.stream("GET", task.url) as resp:
                    resp.raise_for_status()
                    # Content-Length is the size of the ON-THE-WIRE body. When
                    # the response is content-encoded (gzip/br/deflate/zstd),
                    # httpx's aiter_bytes() transparently decompresses, so the
                    # bytes we write to disk are LARGER than Content-Length.
                    # Treating that as the expected on-disk size would make
                    # _validate_download flag a perfectly good download as a
                    # "size mismatch" and delete it, so leave total_bytes at 0
                    # (unknown) for encoded responses and rely on the SHA check.
                    total = resp.headers.get("content-length")
                    if total and not resp.headers.get("content-encoding"):
                        task.total_bytes = int(total)
                    else:
                        task.total_bytes = 0
                    with open(task.dest, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
                            sha.update(chunk)
                            task.downloaded_bytes += len(chunk)
            error = await self._validate_download(task, expected_sha256, computed_sha256=sha.hexdigest())
            if error:
                task.dest.unlink(missing_ok=True)
                task.status = "error"
                task.error = error
            else:
                task.status = "complete"
                task.completed_at = time.time()
        except Exception as e:
            task.status = "error"
            task.error = str(e)
            logger.error(f"Download failed for {task.id}: {e}")
