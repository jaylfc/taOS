"""Unit tests for the torrent downloader + hybrid download path.

These cover the policy gate (should_use_torrent), the
TORRENT_AVAILABLE flag, and the DownloadManager hybrid behaviour
under torrent failure (fallback to HTTP).

Full libtorrent swarm tests would need a real tracker / peers and
aren't worth running in CI — integration coverage comes from
production use once the mirror seedbox is live (Phase 2).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.torrent_downloader import (
    TORRENT_AVAILABLE,
    TorrentError,
    TorrentNotAvailable,
    TorrentTimeout,
    should_use_torrent,
)


def test_torrent_available_matches_import():
    """Sanity check that the import flag reflects the real state."""
    try:
        import libtorrent  # noqa: F401
        expected = True
    except ImportError:
        expected = False
    assert TORRENT_AVAILABLE is expected


def test_should_use_torrent_requires_magnet_or_url():
    """A variant without a magnet URI is never torrent-eligible."""
    if not TORRENT_AVAILABLE:
        pytest.skip("libtorrent not installed on this host")
    assert should_use_torrent({}, seed_enabled=True) is False
    assert should_use_torrent({"license_allows_redistribution": True}, seed_enabled=True) is False


def test_should_use_torrent_requires_license_allowance():
    """Without explicit licence allowance, we never attempt torrent.

    This is the copyright safety gate: even if a magnet URI exists,
    don't try the swarm unless the catalog publisher has marked the
    variant as redistributable under its licence.
    """
    if not TORRENT_AVAILABLE:
        pytest.skip("libtorrent not installed on this host")
    variant = {"magnet": "magnet:?xt=urn:btih:abc", "license_allows_redistribution": False}
    assert should_use_torrent(variant, seed_enabled=True) is False


def test_should_use_torrent_happy_path():
    if not TORRENT_AVAILABLE:
        pytest.skip("libtorrent not installed on this host")
    variant = {
        "magnet": "magnet:?xt=urn:btih:abc",
        "license_allows_redistribution": True,
    }
    assert should_use_torrent(variant, seed_enabled=True) is True


def test_should_use_torrent_accepts_torrent_url_field():
    if not TORRENT_AVAILABLE:
        pytest.skip("libtorrent not installed on this host")
    variant = {
        "torrent_url": "https://example.com/model.torrent",
        "license_allows_redistribution": True,
    }
    assert should_use_torrent(variant, seed_enabled=True) is True


def test_torrent_not_available_without_libtorrent():
    """When libtorrent is missing, should_use_torrent returns False
    regardless of the variant's state. The DownloadManager uses this
    signal to skip straight to HTTP."""
    variant = {
        "magnet": "magnet:?xt=urn:btih:abc",
        "license_allows_redistribution": True,
    }
    with patch("tinyagentos.torrent_downloader.TORRENT_AVAILABLE", False):
        assert should_use_torrent(variant, seed_enabled=True) is False


@pytest.mark.asyncio
async def test_download_manager_hybrid_falls_back_to_http_on_torrent_failure(tmp_path: Path):
    """The hybrid download path: if the torrent attempt raises, the
    HTTP path takes over transparently and the caller sees one
    successful DownloadTask."""
    from tinyagentos.download_manager import DownloadManager

    dm = DownloadManager()
    dest = tmp_path / "model.bin"

    # Stub the torrent downloader to always raise — simulates
    # libtorrent installed but the swarm has no peers, or the
    # torrent_url is broken, etc.
    fake_torrent = MagicMock()
    fake_torrent.download = AsyncMock(side_effect=TorrentTimeout("no peers"))
    with patch.object(dm, "_get_torrent_downloader", return_value=fake_torrent):
        # Also stub the HTTP path so we can verify it was called
        async def fake_http(task, sha=None):
            task.dest.write_bytes(b"fake content from http fallback")
            task.status = "complete"

        with patch.object(dm, "_download", side_effect=fake_http):
            task = dm.start_download(
                download_id="test",
                url="http://example.com/model.bin",
                dest=dest,
                expected_sha256="abc",
                magnet="magnet:?xt=urn:btih:abc",
                license_allows_redistribution=True,
            )
            # Wait for the background task to complete
            await asyncio.wait_for(dm._running["test"], timeout=5)

    # HTTP fallback ran because torrent raised
    assert fake_torrent.download.called
    assert task.status == "complete"


@pytest.mark.asyncio
async def test_download_manager_skips_torrent_without_license(tmp_path: Path):
    """If the variant has a magnet but no redistribution licence, the
    torrent path is skipped and HTTP runs immediately — no torrent
    session is created."""
    from tinyagentos.download_manager import DownloadManager

    dm = DownloadManager()
    dest = tmp_path / "model.bin"

    # Track whether the torrent factory was invoked
    torrent_factory_calls = {"n": 0}
    def _factory_spy():
        torrent_factory_calls["n"] += 1
        return None  # as if libtorrent unavailable

    with patch.object(dm, "_get_torrent_downloader", side_effect=_factory_spy):
        async def fake_http(task, sha=None):
            task.dest.write_bytes(b"http")
            task.status = "complete"

        with patch.object(dm, "_download", side_effect=fake_http):
            dm.start_download(
                download_id="t",
                url="http://example.com/m.bin",
                dest=dest,
                magnet="magnet:?xt=urn:btih:abc",
                license_allows_redistribution=False,  # gate closed
            )
            await asyncio.wait_for(dm._running["t"], timeout=5)

    # Policy gate closed — torrent factory should never have been called
    assert torrent_factory_calls["n"] == 0


@pytest.mark.asyncio
async def test_download_manager_pure_http_when_no_magnet(tmp_path: Path):
    """A variant with no magnet URI uses HTTP directly. Unchanged from
    the pre-Phase-1 behaviour — backwards-compat check."""
    from tinyagentos.download_manager import DownloadManager

    dm = DownloadManager()
    dest = tmp_path / "model.bin"

    async def fake_http(task, sha=None):
        task.dest.write_bytes(b"http")
        task.status = "complete"

    with patch.object(dm, "_download", side_effect=fake_http):
        dm.start_download(
            download_id="t",
            url="http://example.com/m.bin",
            dest=dest,
        )
        await asyncio.wait_for(dm._running["t"], timeout=5)

    assert dest.read_bytes() == b"http"


@pytest.mark.asyncio
async def test_download_manager_skips_torrent_without_sha256(tmp_path: Path):
    """When no SHA256 hash is available, the torrent path is skipped
    entirely — untrusted peers cannot be verified, so we fall straight
    through to HTTP."""
    from tinyagentos.download_manager import DownloadManager

    dm = DownloadManager()
    dest = tmp_path / "model.bin"

    # Track whether the torrent factory was even invoked
    torrent_factory_calls = {"n": 0}
    def _factory_spy():
        torrent_factory_calls["n"] += 1
        return MagicMock()

    with patch.object(dm, "_get_torrent_downloader", side_effect=_factory_spy):
        async def fake_http(task, sha=None):
            task.dest.write_bytes(b"http")
            task.status = "complete"

        with patch.object(dm, "_download", side_effect=fake_http):
            dm.start_download(
                download_id="t",
                url="http://example.com/m.bin",
                dest=dest,
                magnet="magnet:?xt=urn:btih:abc",
                license_allows_redistribution=True,
            )
            await asyncio.wait_for(dm._running["t"], timeout=5)

    # Torrent path was never touched — no hash, no torrent
    assert torrent_factory_calls["n"] == 0
    assert dest.read_bytes() == b"http"


@pytest.mark.asyncio
async def test_download_manager_torrent_sha256_mismatch_falls_back_to_http(tmp_path: Path):
    """If the torrent delivers a file whose SHA256 doesn't match the
    expected hash, the download fails and falls back to HTTP."""
    import hashlib
    from tinyagentos.download_manager import DownloadManager

    dm = DownloadManager()
    dest = tmp_path / "model.bin"

    # The mock torrent writes tainted data then raises TorrentError to
    # simulate the mandatory SHA256 check catching a mismatch
    fake_torrent = MagicMock()
    async def _mock_download(task_id, magnet_or_torrent, dest, expected_sha256, progress_cb, passkey=None, web_seeds=None):
        dest.write_bytes(b"tainted data from malicious peer")
        raise TorrentError("sha256 mismatch after torrent download")

    fake_torrent.download = AsyncMock(side_effect=_mock_download)

    # HTTP fallback data
    http_data = b"verified model from http"
    expected_hash = hashlib.sha256(http_data).hexdigest()

    with patch.object(dm, "_get_torrent_downloader", return_value=fake_torrent):
        async def fake_http(task, sha=None):
            task.dest.write_bytes(http_data)
            task.status = "complete"

        with patch.object(dm, "_download", side_effect=fake_http):
            dm.start_download(
                download_id="t",
                url="http://example.com/m.bin",
                dest=dest,
                expected_sha256=expected_hash,
                magnet="magnet:?xt=urn:btih:abc",
                license_allows_redistribution=True,
            )
            await asyncio.wait_for(dm._running["t"], timeout=5)

    # HTTP fallback succeeded, torrent was attempted
    assert fake_torrent.download.called
    assert dest.read_bytes() == http_data
