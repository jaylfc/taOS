import asyncio
import hashlib
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from tinyagentos import download_manager
from tinyagentos.download_manager import DownloadManager, DownloadTask


# ---------------------------------------------------------------------------
# DownloadTask dataclass
# ---------------------------------------------------------------------------

class TestDownloadTask:
    def test_defaults(self):
        task = DownloadTask(id="t1", url="http://example.com/model.bin", dest=Path("/tmp/model.bin"))
        assert task.id == "t1"
        assert task.url == "http://example.com/model.bin"
        assert task.dest == Path("/tmp/model.bin")
        assert task.total_bytes == 0
        assert task.downloaded_bytes == 0
        assert task.status == "pending"
        assert task.error == ""
        assert task.started_at == 0
        assert task.completed_at == 0

    def test_custom_fields(self):
        task = DownloadTask(
            id="t2",
            url="http://example.com/x",
            dest=Path("/tmp/x"),
            total_bytes=100,
            status="downloading",
        )
        assert task.total_bytes == 100
        assert task.status == "downloading"


# ---------------------------------------------------------------------------
# DownloadManager: construction and torrent-settings wiring
# ---------------------------------------------------------------------------

class TestDownloadManagerInit:
    def test_no_arg_constructor(self):
        dm = DownloadManager()
        assert dm._tasks == {}
        assert dm._running == {}
        assert dm._torrent_settings_store is None
        assert dm._torrent is None

    def test_with_torrent_settings_store(self):
        store = MagicMock()
        dm = DownloadManager(torrent_settings_store=store)
        assert dm._torrent_settings_store is store


class TestApplyTorrentSettings:
    def test_no_op_when_torrent_not_instantiated(self):
        dm = DownloadManager()
        dm.apply_torrent_settings(MagicMock())

    def test_delegates_to_torrent(self):
        dm = DownloadManager()
        fake_torrent = MagicMock()
        dm._torrent = fake_torrent
        settings = MagicMock()
        dm.apply_torrent_settings(settings)
        fake_torrent.apply_settings.assert_called_once_with(settings)


class TestGetTorrentDownloader:
    def test_returns_cached_instance(self):
        dm = DownloadManager()
        cached = MagicMock()
        dm._torrent = cached
        assert dm._get_torrent_downloader() is cached

    def test_returns_none_when_import_raises(self):
        dm = DownloadManager()
        with patch("tinyagentos.torrent_downloader.TorrentDownloader", side_effect=ImportError("no libtorrent")):
            result = dm._get_torrent_downloader()
            assert result is None

    def test_returns_none_when_torrent_raises(self):
        dm = DownloadManager()
        with patch("tinyagentos.torrent_downloader.TorrentDownloader", side_effect=RuntimeError("nope")):
            result = dm._get_torrent_downloader()
            assert result is None

    def test_creates_with_settings_from_store(self):
        dm = DownloadManager()
        fake_settings = MagicMock()
        fake_store = MagicMock()
        fake_store.load.return_value = fake_settings
        dm._torrent_settings_store = fake_store

        mock_torrent_cls = MagicMock()
        with patch("tinyagentos.torrent_downloader.TorrentDownloader", mock_torrent_cls):
            result = dm._get_torrent_downloader()
        mock_torrent_cls.assert_called_once_with(settings=fake_settings)
        assert result is mock_torrent_cls.return_value

    def test_creates_without_settings_when_no_store(self):
        dm = DownloadManager()
        mock_torrent_cls = MagicMock()
        with patch("tinyagentos.torrent_downloader.TorrentDownloader", mock_torrent_cls):
            result = dm._get_torrent_downloader()
        mock_torrent_cls.assert_called_once_with(settings=None)


# ---------------------------------------------------------------------------
# start_download
# ---------------------------------------------------------------------------

class TestStartDownload:
    @pytest.mark.asyncio
    async def test_creates_task_and_stores_it(self, tmp_path):
        dm = DownloadManager()
        dest = tmp_path / "model.bin"
        task = dm.start_download("dl-1", "http://example.com/model.bin", dest)
        assert task.id == "dl-1"
        assert task.url == "http://example.com/model.bin"
        assert task.dest == dest
        assert task.status == "pending"
        assert "dl-1" in dm._tasks
        assert "dl-1" in dm._running
        dm._running["dl-1"].cancel()
        try:
            await dm._running["dl-1"]
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_returns_same_task_as_stored(self, tmp_path):
        dm = DownloadManager()
        dest = tmp_path / "model.bin"
        task = dm.start_download("dl-2", "http://example.com/m.bin", dest)
        assert dm.get_progress("dl-2") is task
        dm._running["dl-2"].cancel()
        try:
            await dm._running["dl-2"]
        except (asyncio.CancelledError, Exception):
            pass


# ---------------------------------------------------------------------------
# get_progress / list_active / list_all
# ---------------------------------------------------------------------------

class TestProgressAndListing:
    def test_get_progress_returns_none_for_unknown(self):
        dm = DownloadManager()
        assert dm.get_progress("nonexistent") is None

    def test_get_progress_returns_task(self):
        dm = DownloadManager()
        task = DownloadTask(id="x", url="http://x", dest=Path("/tmp/x"))
        dm._tasks["x"] = task
        assert dm.get_progress("x") is task

    def test_list_active_filters_completed_and_error(self):
        dm = DownloadManager()
        dm._tasks["a"] = DownloadTask(id="a", url="http://a", dest=Path("/tmp/a"), status="pending")
        dm._tasks["b"] = DownloadTask(id="b", url="http://b", dest=Path("/tmp/b"), status="downloading")
        dm._tasks["c"] = DownloadTask(id="c", url="http://c", dest=Path("/tmp/c"), status="complete")
        dm._tasks["d"] = DownloadTask(id="d", url="http://d", dest=Path("/tmp/d"), status="error")
        active = dm.list_active()
        ids = {t.id for t in active}
        assert ids == {"a", "b"}

    def test_list_active_empty_when_all_complete(self):
        dm = DownloadManager()
        dm._tasks["a"] = DownloadTask(id="a", url="http://a", dest=Path("/tmp/a"), status="complete")
        assert dm.list_active() == []

    def test_list_all_returns_everything(self):
        dm = DownloadManager()
        dm._tasks["a"] = DownloadTask(id="a", url="http://a", dest=Path("/tmp/a"), status="pending")
        dm._tasks["b"] = DownloadTask(id="b", url="http://b", dest=Path("/tmp/b"), status="complete")
        all_tasks = dm.list_all()
        assert len(all_tasks) == 2

    def test_list_all_empty(self):
        dm = DownloadManager()
        assert dm.list_all() == []


# ---------------------------------------------------------------------------
# _download (HTTP path) -- mocked httpx
# ---------------------------------------------------------------------------

class TestDownloadHttp:
    @pytest_asyncio.fixture
    def dm(self):
        return DownloadManager()

    def _make_async_context_manager_mock(self, content: bytes, content_length: int | None = None):
        """Build a mock that works as both `async with client.stream(...)` and
        `async with client` (the outer AsyncClient context manager).

        The code does::
            async with httpx.AsyncClient(...) as client:
                async with client.stream("GET", url) as resp:
                    ...
        """
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        if content_length is not None:
            mock_resp.headers = {"content-length": str(content_length)}
        else:
            mock_resp.headers = {}

        async def _aiter_bytes(chunk_size=65536):
            for i in range(0, len(content), chunk_size):
                yield content[i:i + chunk_size]

        mock_resp.aiter_bytes = _aiter_bytes
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        return mock_resp

    def _make_mock_client(self, mock_resp):
        """Build a mock httpx.AsyncClient that works as an async context manager
        whose `.stream()` method also returns an async context manager."""
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(return_value=mock_resp)
        return mock_client

    @pytest.mark.asyncio
    async def test_successful_download(self, dm, tmp_path):
        data = b"hello world" * 100
        dest = tmp_path / "out.bin"
        task = DownloadTask(id="dl", url="http://example.com/f.bin", dest=dest)
        mock_resp = self._make_async_context_manager_mock(data, len(data))
        mock_client = self._make_mock_client(mock_resp)

        with patch("tinyagentos.download_manager.httpx.AsyncClient", return_value=mock_client):
            await dm._download(task, expected_sha256=None)

        assert task.status == "complete"
        assert task.downloaded_bytes == len(data)
        assert task.total_bytes == len(data)
        assert task.completed_at > 0
        assert task.error == ""
        assert dest.read_bytes() == data

    @pytest.mark.asyncio
    async def test_download_with_correct_sha256(self, dm, tmp_path):
        data = b"test data"
        expected_hash = hashlib.sha256(data).hexdigest()
        dest = tmp_path / "out.bin"
        task = DownloadTask(id="dl", url="http://example.com/f.bin", dest=dest)
        mock_resp = self._make_async_context_manager_mock(data, len(data))
        mock_client = self._make_mock_client(mock_resp)

        with patch("tinyagentos.download_manager.httpx.AsyncClient", return_value=mock_client):
            await dm._download(task, expected_sha256=expected_hash)

        assert task.status == "complete"
        assert dest.exists()

    @pytest.mark.asyncio
    async def test_download_sha256_mismatch(self, dm, tmp_path):
        data = b"test data"
        dest = tmp_path / "out.bin"
        task = DownloadTask(id="dl", url="http://example.com/f.bin", dest=dest)
        mock_resp = self._make_async_context_manager_mock(data, len(data))
        mock_client = self._make_mock_client(mock_resp)

        with patch("tinyagentos.download_manager.httpx.AsyncClient", return_value=mock_client):
            await dm._download(task, expected_sha256="wronghash")

        assert task.status == "error"
        assert task.error == "SHA256 mismatch"
        assert not dest.exists()

    @pytest.mark.asyncio
    async def test_download_http_error(self, dm, tmp_path):
        dest = tmp_path / "out.bin"
        task = DownloadTask(id="dl", url="http://example.com/f.bin", dest=dest)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(side_effect=Exception("404 Not Found"))
        mock_resp.headers = {}
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(return_value=mock_resp)

        with patch("tinyagentos.download_manager.httpx.AsyncClient", return_value=mock_client):
            await dm._download(task, expected_sha256=None)

        assert task.status == "error"
        assert task.error == "404 Not Found"

    @pytest.mark.asyncio
    async def test_download_creates_parent_dirs(self, dm, tmp_path):
        data = b"x"
        dest = tmp_path / "sub" / "dir" / "out.bin"
        task = DownloadTask(id="dl", url="http://example.com/f.bin", dest=dest)
        mock_resp = self._make_async_context_manager_mock(data, 1)
        mock_client = self._make_mock_client(mock_resp)

        with patch("tinyagentos.download_manager.httpx.AsyncClient", return_value=mock_client):
            await dm._download(task, expected_sha256=None)

        assert dest.exists()
        assert dest.read_bytes() == data

    @pytest.mark.asyncio
    async def test_download_empty_body_marks_error_not_complete(self, dm, tmp_path):
        """A 0-byte response body (e.g. an error page served with a 200,
        or a stream that closes immediately) must not be reported as a
        complete download."""
        dest = tmp_path / "out.bin"
        task = DownloadTask(id="dl", url="http://example.com/f.bin", dest=dest)
        mock_resp = self._make_async_context_manager_mock(b"", None)
        mock_client = self._make_mock_client(mock_resp)

        with patch("tinyagentos.download_manager.httpx.AsyncClient", return_value=mock_client):
            await dm._download(task, expected_sha256=None)

        assert task.status == "error"
        assert task.error == "download produced no data"
        assert not dest.exists()

    @pytest.mark.asyncio
    async def test_download_no_content_length(self, dm, tmp_path):
        data = b"no content length"
        dest = tmp_path / "out.bin"
        task = DownloadTask(id="dl", url="http://example.com/f.bin", dest=dest)
        mock_resp = self._make_async_context_manager_mock(data, None)
        mock_client = self._make_mock_client(mock_resp)

        with patch("tinyagentos.download_manager.httpx.AsyncClient", return_value=mock_client):
            await dm._download(task, expected_sha256=None)

        assert task.status == "complete"
        assert task.total_bytes == 0
        assert task.downloaded_bytes == len(data)


# ---------------------------------------------------------------------------
# _download_with_fallback: torrent-first and fallback logic
# ---------------------------------------------------------------------------

class TestDownloadWithFallback:
    @pytest_asyncio.fixture
    def dm(self):
        return DownloadManager()

    def _make_http_mock(self, data: bytes):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.headers = {"content-length": str(len(data))}

        async def _aiter_bytes(chunk_size=65536):
            yield data

        mock_resp.aiter_bytes = _aiter_bytes
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(return_value=mock_resp)
        return mock_client

    @pytest.mark.asyncio
    async def test_falls_through_to_http_when_no_magnet(self, dm, tmp_path):
        data = b"fallback data"
        dest = tmp_path / "model.bin"
        task = DownloadTask(id="dl", url="http://example.com/m.bin", dest=dest)
        mock_client = self._make_http_mock(data)

        with patch("tinyagentos.download_manager.httpx.AsyncClient", return_value=mock_client):
            await dm._download_with_fallback(task, expected_sha256=None, magnet=None)

        assert task.status == "complete"
        assert dest.read_bytes() == data

    @pytest.mark.asyncio
    async def test_falls_through_when_license_disallows(self, dm, tmp_path):
        data = b"http only"
        dest = tmp_path / "model.bin"
        task = DownloadTask(id="dl", url="http://example.com/m.bin", dest=dest)
        mock_client = self._make_http_mock(data)

        with patch("tinyagentos.download_manager.httpx.AsyncClient", return_value=mock_client):
            await dm._download_with_fallback(
                task,
                expected_sha256=None,
                magnet="magnet:?xt=urn:btih:abc",
                license_allows_redistribution=False,
            )

        assert task.status == "complete"

    @pytest.mark.asyncio
    async def test_torrent_success_path(self, dm, tmp_path):
        dest = tmp_path / "model.bin"
        task = DownloadTask(id="dl", url="http://example.com/m.bin", dest=dest)
        data = b"z" * 999

        fake_torrent = AsyncMock()
        fake_progress_task = MagicMock()
        fake_progress_task.total_bytes = 999
        fake_progress_task.downloaded_bytes = 999

        async def mock_download(task_id, magnet_or_torrent, dest, expected_sha256, progress_cb, passkey=None, web_seeds=None):
            dest.write_bytes(data)
            progress_cb(fake_progress_task)

        fake_torrent.download = mock_download

        with patch.object(dm, "_get_torrent_downloader", return_value=fake_torrent):
            await dm._download_with_fallback(
                task,
                expected_sha256="e35df0beb994665801280a978d8997d6b41fb31797f29100352fda9fc499afe8",
                magnet="magnet:?xt=urn:btih:abc",
                license_allows_redistribution=True,
            )

        assert task.status == "complete"
        assert task.total_bytes == 999
        assert task.downloaded_bytes == 999
        assert task.completed_at > 0
        assert dest.read_bytes() == data

    @pytest.mark.asyncio
    async def test_torrent_success_reported_but_no_file_marks_error(self, dm, tmp_path):
        """Guards against the false-complete bug: the torrent swarm can
        report a clean finish via progress_cb while writing nothing (or
        an empty file) to dest. That must not be reported as complete."""
        dest = tmp_path / "model.bin"
        task = DownloadTask(id="dl", url="http://example.com/m.bin", dest=dest)

        fake_torrent = AsyncMock()
        fake_progress_task = MagicMock()
        fake_progress_task.total_bytes = 999
        fake_progress_task.downloaded_bytes = 999

        async def mock_download(task_id, magnet_or_torrent, dest, expected_sha256, progress_cb, passkey=None, web_seeds=None):
            # never writes dest, just reports progress as if it finished
            progress_cb(fake_progress_task)

        fake_torrent.download = mock_download

        with patch.object(dm, "_get_torrent_downloader", return_value=fake_torrent):
            await dm._download_with_fallback(
                task,
                expected_sha256="abc",
                magnet="magnet:?xt=urn:btih:abc",
                license_allows_redistribution=True,
            )

        assert task.status == "error"
        assert task.error == "download produced no data"
        assert not dest.exists()

    @pytest.mark.asyncio
    async def test_torrent_success_reported_but_empty_file_marks_error(self, dm, tmp_path):
        dest = tmp_path / "model.bin"
        task = DownloadTask(id="dl", url="http://example.com/m.bin", dest=dest)

        fake_torrent = AsyncMock()

        async def mock_download(task_id, magnet_or_torrent, dest, expected_sha256, progress_cb, passkey=None, web_seeds=None):
            dest.write_bytes(b"")

        fake_torrent.download = mock_download

        with patch.object(dm, "_get_torrent_downloader", return_value=fake_torrent):
            await dm._download_with_fallback(
                task,
                expected_sha256="abc",
                magnet="magnet:?xt=urn:btih:abc",
                license_allows_redistribution=True,
            )

        assert task.status == "error"
        assert task.error == "download produced no data"
        assert not dest.exists()

    @pytest.mark.asyncio
    async def test_torrent_failure_falls_back_to_http(self, dm, tmp_path):
        data = b"http fallback after torrent error"
        dest = tmp_path / "model.bin"
        task = DownloadTask(id="dl", url="http://example.com/m.bin", dest=dest)

        fake_torrent = AsyncMock()
        fake_torrent.download = AsyncMock(side_effect=Exception("no peers"))
        mock_client = self._make_http_mock(data)

        with patch.object(dm, "_get_torrent_downloader", return_value=fake_torrent):
            with patch("tinyagentos.download_manager.httpx.AsyncClient", return_value=mock_client):
                await dm._download_with_fallback(
                    task,
                    expected_sha256="93cdef50225636d780608d21182f72523754766637ee85e9d4682af045e61678",
                    magnet="magnet:?xt=urn:btih:abc",
                    license_allows_redistribution=True,
                )

        assert task.status == "complete"
        assert dest.read_bytes() == data

    @pytest.mark.asyncio
    async def test_torrent_unavailable_falls_back_to_http(self, dm, tmp_path):
        data = b"http because no torrent"
        dest = tmp_path / "model.bin"
        task = DownloadTask(id="dl", url="http://example.com/m.bin", dest=dest)
        mock_client = self._make_http_mock(data)

        with patch.object(dm, "_get_torrent_downloader", return_value=None):
            with patch("tinyagentos.download_manager.httpx.AsyncClient", return_value=mock_client):
                await dm._download_with_fallback(
                    task,
                    expected_sha256=None,
                    magnet="magnet:?xt=urn:btih:abc",
                    license_allows_redistribution=True,
                )

        assert task.status == "complete"

    @pytest.mark.asyncio
    async def test_torrent_resets_state_before_http_fallback(self, dm, tmp_path):
        data = b"clean slate"
        dest = tmp_path / "model.bin"
        task = DownloadTask(id="dl", url="http://example.com/m.bin", dest=dest)

        fake_torrent = AsyncMock()
        fake_torrent.download = AsyncMock(side_effect=Exception("torrent broke"))
        mock_client = self._make_http_mock(data)

        with patch.object(dm, "_get_torrent_downloader", return_value=fake_torrent):
            with patch("tinyagentos.download_manager.httpx.AsyncClient", return_value=mock_client):
                await dm._download_with_fallback(
                    task,
                    expected_sha256="97da6995e0f94ba51e5f3634a84303861e8b3997959f4041e263af26c58a247b",
                    magnet="magnet:?xt=urn:btih:abc",
                    license_allows_redistribution=True,
                )

        assert task.status == "complete"
        assert task.error == ""


# ---------------------------------------------------------------------------
# start_installer_task
# ---------------------------------------------------------------------------

class TestStartInstallerTask:
    """A backend-specific installer (e.g. RkllamaInstaller.install(), which
    pulls a weight via the backend's own /api/pull instead of a raw HTTP
    transfer) is tracked through the same DownloadTask polling API as a
    regular download."""

    @pytest.mark.asyncio
    async def test_success_marks_task_complete(self):
        dm = DownloadManager()

        async def _install():
            return {"success": True}

        task = dm.start_installer_task("dl-installer", _install())
        assert task.id == "dl-installer"
        assert task.status == "pending"
        await dm._running["dl-installer"]
        assert task.status == "complete"
        assert task.completed_at > 0

    @pytest.mark.asyncio
    async def test_failure_result_marks_task_error(self):
        dm = DownloadManager()

        async def _install():
            return {"success": False, "error": "rkllama pull failed"}

        task = dm.start_installer_task("dl-installer", _install())
        await dm._running["dl-installer"]
        assert task.status == "error"
        assert task.error == "rkllama pull failed"

    @pytest.mark.asyncio
    async def test_raised_exception_marks_task_error(self):
        dm = DownloadManager()

        async def _install():
            raise RuntimeError("connection refused")

        task = dm.start_installer_task("dl-installer", _install())
        await dm._running["dl-installer"]
        assert task.status == "error"
        assert task.error == "connection refused"

    @pytest.mark.asyncio
    async def test_progress_callback_updates_task_bytes(self):
        # A caller may pass a factory (callable taking on_progress) instead
        # of a bare coroutine, so an installer that streams incremental
        # progress (e.g. RkllamaInstaller.install()) can update this task's
        # downloaded_bytes/total_bytes as it goes, instead of leaving
        # percent stuck at 0 until the task finishes.
        dm = DownloadManager()

        def _install_factory(on_progress):
            async def _run():
                on_progress(50, 200)
                on_progress(200, 200)
                return {"success": True}
            return _run()

        task = dm.start_installer_task("dl-installer", _install_factory)
        assert task.downloaded_bytes == 0
        assert task.total_bytes == 0
        await dm._running["dl-installer"]
        assert task.status == "complete"
        assert task.downloaded_bytes == 200
        assert task.total_bytes == 200

    @pytest.mark.asyncio
    async def test_bare_coroutine_without_progress_still_works(self):
        # Backward compatibility: a plain coroutine (no progress reporting)
        # must keep working exactly as before.
        dm = DownloadManager()

        async def _install():
            return {"success": True}

        task = dm.start_installer_task("dl-installer", _install())
        await dm._running["dl-installer"]
        assert task.status == "complete"
        assert task.downloaded_bytes == 0
        assert task.total_bytes == 0


# ---------------------------------------------------------------------------
# _download: timeouts, resume, retry and cleanup
# ---------------------------------------------------------------------------

class _FakeHttpServer:
    """A stand-in httpx.AsyncClient that honours the transport contract the
    real client documents, so a test can drive the failure modes a home
    internet connection actually produces without opening a socket.

    It reproduces the two behaviours that matter here:

    * ``timeout=None`` disables the read timeout, so a server that stops
      sending mid-body leaves the stream awaiting forever. A finite
      ``read`` raises ``httpx.ReadTimeout`` instead.
    * A request carrying ``Range: bytes=N-`` is answered with 206 and the
      remaining bytes, exactly as a CDN that supports resume does.
    """

    def __init__(self, body: bytes, *, stall_after: int | None = None,
                 fail_attempts: int = 0, failure=None, supports_range: bool = True):
        self.body = body
        self.stall_after = stall_after
        self.fail_attempts = fail_attempts
        self.failure = failure or httpx.ReadError("connection reset")
        self.supports_range = supports_range
        self.requests: list[dict] = []
        self.timeouts: list[object] = []

    def __call__(self, *args, **kwargs):
        self.timeouts.append(kwargs.get("timeout"))
        return _FakeClient(self, kwargs.get("timeout"))


class _FakeClient:
    def __init__(self, server: "_FakeHttpServer", timeout):
        self._server = server
        self._timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, headers=None, **kwargs):
        headers = dict(headers or {})
        self._server.requests.append(headers)
        return _FakeStream(self._server, self._timeout, headers)


class _FakeStream:
    def __init__(self, server: "_FakeHttpServer", timeout, headers: dict):
        self._server = server
        self._timeout = timeout
        self._attempt = len(server.requests)
        rng = headers.get("Range")
        self._offset = 0
        self.status_code = 200
        self.headers: dict[str, str] = {}
        if rng and server.supports_range:
            self._offset = int(rng.removeprefix("bytes=").rstrip("-"))
            if self._offset >= len(server.body):
                self.status_code = 416
            else:
                self.status_code = 206
                self.headers["content-range"] = (
                    f"bytes {self._offset}-{len(server.body) - 1}/{len(server.body)}"
                )
        self._payload = server.body[self._offset:]
        if self.status_code != 416:
            self.headers["content-length"] = str(len(self._payload))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "http://example.com/f.bin"),
                response=httpx.Response(self.status_code),
            )

    async def aiter_bytes(self, chunk_size=65536):
        failing = self._attempt <= self._server.fail_attempts
        # A dropped connection cuts the body off part-way through; a failure
        # that arrives only after every byte has been delivered would leave a
        # complete stage file and never exercise resume.
        limit = len(self._payload) // 2 if failing else None
        sent = 0
        for i in range(0, len(self._payload), chunk_size):
            if self._server.stall_after is not None and sent >= self._server.stall_after:
                break
            chunk = self._payload[i:i + chunk_size]
            if limit is not None and sent + len(chunk) > limit:
                chunk = chunk[:limit - sent]
                if chunk:
                    yield chunk
                break
            yield chunk
            sent += len(chunk)
        if failing:
            raise self._server.failure
        if self._server.stall_after is not None:
            read = getattr(self._timeout, "read", self._timeout)
            if read is None:
                # Half-open connection: the peer stops sending and never
                # closes. With no read timeout the stream waits forever.
                await asyncio.Event().wait()
            # Stand-in for the real `read`-second wait; what is under test is
            # that a finite read timeout turns the stall into an error at all,
            # not how long that error takes to arrive.
            await asyncio.sleep(0.01)
            raise httpx.ReadTimeout("timed out reading response body")


@pytest.fixture
def fast_retries():
    """Collapse the production backoff so a retry test costs milliseconds.

    ``create=True`` so the fixture still applies against a build with no
    retry at all — the red run then shows the download defect under test
    rather than an AttributeError from the fixture.
    """
    with patch.object(download_manager, "DOWNLOAD_RETRY_BASE_DELAY", 0.001, create=True), \
         patch.object(download_manager, "DOWNLOAD_RETRY_MAX_DELAY", 0.001, create=True):
        yield


class TestDownloadTimeoutResumeAndCleanup:
    @pytest_asyncio.fixture
    def dm(self):
        return DownloadManager()

    @pytest.mark.asyncio
    async def test_stalled_connection_errors_instead_of_hanging(self, dm, tmp_path, fast_retries):
        """A server that stops sending mid-body must surface as an error.
        With timeout=None the task sits at status="downloading" forever and
        the user watches a progress bar frozen at 63%."""
        dest = tmp_path / "out.bin"
        task = DownloadTask(id="dl", url="http://example.com/f.bin", dest=dest)
        server = _FakeHttpServer(b"x" * 200_000, stall_after=65536)

        with patch("tinyagentos.download_manager.httpx.AsyncClient", server):
            await asyncio.wait_for(dm._download(task, expected_sha256=None), timeout=10)

        assert task.status == "error"
        assert task.error

    @pytest.mark.asyncio
    async def test_client_is_built_with_finite_timeouts(self, dm, tmp_path):
        dest = tmp_path / "out.bin"
        task = DownloadTask(id="dl", url="http://example.com/f.bin", dest=dest)
        server = _FakeHttpServer(b"payload")

        with patch("tinyagentos.download_manager.httpx.AsyncClient", server):
            await dm._download(task, expected_sha256=None)

        assert server.timeouts, "no AsyncClient was constructed"
        timeout = server.timeouts[0]
        assert isinstance(timeout, httpx.Timeout)
        # The read timeout is the one that turns a half-open connection into
        # an error; the others keep a dead peer from wedging connect/pool.
        assert timeout.read is not None and timeout.read > 0
        assert timeout.connect is not None and timeout.connect > 0
        assert timeout.write is not None and timeout.write > 0
        assert timeout.pool is not None and timeout.pool > 0

    @pytest.mark.asyncio
    async def test_interrupted_download_resumes_from_byte_offset(self, dm, tmp_path):
        """Bytes already on disk from an interrupted transfer are asked for
        with a Range header instead of being thrown away — a 40 GB model that
        died at 39 GB must not restart from zero."""
        body = bytes(i % 256 for i in range(4096))
        dest = tmp_path / "out.bin"
        part = tmp_path / "out.bin.part"
        part.write_bytes(body[:3000])
        task = DownloadTask(id="dl", url="http://example.com/f.bin", dest=dest)
        server = _FakeHttpServer(body)

        with patch("tinyagentos.download_manager.httpx.AsyncClient", server):
            await dm._download(task, expected_sha256=hashlib.sha256(body).hexdigest())

        assert server.requests[0].get("Range") == "bytes=3000-"
        assert task.status == "complete", task.error
        assert dest.read_bytes() == body
        assert not part.exists()

    @pytest.mark.asyncio
    async def test_server_ignoring_range_restarts_cleanly(self, dm, tmp_path):
        """A mirror that answers 200 to a Range request is sending the whole
        file again; appending it to the stub would double the bytes."""
        body = b"abcdefghij" * 100
        dest = tmp_path / "out.bin"
        part = tmp_path / "out.bin.part"
        part.write_bytes(b"stale bytes")
        task = DownloadTask(id="dl", url="http://example.com/f.bin", dest=dest)
        server = _FakeHttpServer(body, supports_range=False)

        with patch("tinyagentos.download_manager.httpx.AsyncClient", server):
            await dm._download(task, expected_sha256=hashlib.sha256(body).hexdigest())

        assert task.status == "complete", task.error
        assert dest.read_bytes() == body

    @pytest.mark.asyncio
    async def test_completed_stub_answered_with_416_restarts_from_zero(self, dm, tmp_path, fast_retries):
        """A .part left behind holding every byte of the file gets a 416 to
        its Range request. Treating that as a hard 4xx would wedge the model
        permanently: it must fall back to a clean full fetch."""
        body = b"complete already"
        dest = tmp_path / "out.bin"
        part = tmp_path / "out.bin.part"
        part.write_bytes(body)
        task = DownloadTask(id="dl", url="http://example.com/f.bin", dest=dest)
        server = _FakeHttpServer(body)

        with patch("tinyagentos.download_manager.httpx.AsyncClient", server):
            await dm._download(task, expected_sha256=hashlib.sha256(body).hexdigest())

        assert task.status == "complete", task.error
        assert dest.read_bytes() == body

    @pytest.mark.asyncio
    async def test_failed_download_leaves_no_file_at_destination(self, dm, tmp_path, fast_retries):
        """A partial file at the canonical path is read as a present, valid
        model by every later "is this installed?" existence check."""
        dest = tmp_path / "out.bin"
        task = DownloadTask(id="dl", url="http://example.com/f.bin", dest=dest)
        server = _FakeHttpServer(b"y" * 200_000, fail_attempts=99)

        with patch("tinyagentos.download_manager.httpx.AsyncClient", server):
            await dm._download(task, expected_sha256=None)

        assert task.status == "error"
        assert not dest.exists()

    @pytest.mark.asyncio
    async def test_transient_failure_is_retried(self, dm, tmp_path, fast_retries):
        """A single dropped connection from a mirror must not kill a
        multi-gigabyte pull."""
        body = b"retry me" * 500
        dest = tmp_path / "out.bin"
        task = DownloadTask(id="dl", url="http://example.com/f.bin", dest=dest)
        server = _FakeHttpServer(body, fail_attempts=1)

        with patch("tinyagentos.download_manager.httpx.AsyncClient", server):
            await dm._download(task, expected_sha256=hashlib.sha256(body).hexdigest())

        assert task.status == "complete", task.error
        assert len(server.requests) == 2
        assert dest.read_bytes() == body

    @pytest.mark.asyncio
    async def test_retry_resumes_rather_than_restarting(self, dm, tmp_path, fast_retries):
        body = b"z" * 200_000
        dest = tmp_path / "out.bin"
        task = DownloadTask(id="dl", url="http://example.com/f.bin", dest=dest)
        server = _FakeHttpServer(body, fail_attempts=1)

        with patch("tinyagentos.download_manager.httpx.AsyncClient", server):
            await dm._download(task, expected_sha256=hashlib.sha256(body).hexdigest())

        assert task.status == "complete", task.error
        assert server.requests[1].get("Range") == "bytes=100000-"
        assert dest.read_bytes() == body

    @pytest.mark.asyncio
    async def test_server_5xx_is_retried_but_404_is_not(self, dm, tmp_path, fast_retries):
        """4xx is the server saying the URL is wrong; retrying it just delays
        the error the user needs to see."""
        dest = tmp_path / "out.bin"
        task = DownloadTask(id="dl", url="http://example.com/f.bin", dest=dest)
        server = _FakeHttpServer(
            b"never sent",
            fail_attempts=99,
            failure=httpx.HTTPStatusError(
                "HTTP 404",
                request=httpx.Request("GET", "http://example.com/f.bin"),
                response=httpx.Response(404),
            ),
        )

        with patch("tinyagentos.download_manager.httpx.AsyncClient", server):
            await dm._download(task, expected_sha256=None)

        assert task.status == "error"
        assert len(server.requests) == 1


class TestTaskPruning:
    """self._tasks grew for the lifetime of the process: every model the user
    ever downloaded stayed resident, and /api/models/downloads listed them
    all."""

    @pytest.mark.asyncio
    async def test_old_finished_tasks_are_pruned(self, tmp_path):
        dm = DownloadManager()
        stale = DownloadTask(id="old", url="u", dest=tmp_path / "old.bin")
        stale.status = "complete"
        stale.completed_at = time.time() - (download_manager.TASK_RETENTION_SECONDS + 60)
        dm._tasks["old"] = stale
        dm._running["old"] = MagicMock()

        async def _install():
            return {"success": True}

        dm.start_installer_task("fresh", _install())
        await dm._running["fresh"]

        assert "old" not in dm._tasks
        assert "old" not in dm._running
        assert "fresh" in dm._tasks

    @pytest.mark.asyncio
    async def test_recent_and_active_tasks_are_kept(self, tmp_path):
        dm = DownloadManager()
        recent = DownloadTask(id="recent", url="u", dest=tmp_path / "r.bin")
        recent.status = "complete"
        recent.completed_at = time.time()
        running = DownloadTask(id="running", url="u", dest=tmp_path / "s.bin")
        running.status = "downloading"
        running.started_at = time.time() - (download_manager.TASK_RETENTION_SECONDS + 60)
        dm._tasks["recent"] = recent
        dm._tasks["running"] = running

        async def _install():
            return {"success": True}

        dm.start_installer_task("fresh", _install())
        await dm._running["fresh"]

        assert dm.get_progress("recent") is recent
        assert dm.get_progress("running") is running
