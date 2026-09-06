"""libtorrent-backed tests for taOSnet wiring in TorrentDownloader.

These require libtorrent and are skipped where it is not installed (the repo
treats it as an optional runtime dep, so CI skips these; they run on a host
with python-libtorrent installed, e.g. the Pi).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tinyagentos.torrent_downloader import TORRENT_AVAILABLE, TorrentDownloader, TorrentError

pytestmark = pytest.mark.skipif(
    not TORRENT_AVAILABLE, reason="libtorrent not installed"
)

# A well-formed magnet with a 40-hex info_hash and no tracker (the taOSnet shape:
# info_hash carried, passkey injected by the client at runtime).
MAGNET = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=test"


@pytest.fixture
def downloader():
    return TorrentDownloader()


def test_session_has_dht_disabled(downloader):
    import libtorrent as lt

    settings = downloader._session.get_settings()
    assert settings.get("enable_dht") is False


def test_passkey_injected_as_private_tracker(downloader, tmp_path):
    params = downloader._build_params(MAGNET, tmp_path, passkey="secretpasskey")
    trackers = [t if isinstance(t, str) else t.url for t in params.trackers]
    assert trackers == ["https://tracker.taos.my/secretpasskey/announce"]


def test_web_seeds_added(downloader, tmp_path):
    seeds = ["https://huggingface.co/x/resolve/main/model.gguf"]
    params = downloader._build_params(MAGNET, tmp_path, web_seeds=seeds)
    assert list(params.url_seeds) == seeds


def test_no_passkey_leaves_trackers_empty(downloader, tmp_path):
    params = downloader._build_params(MAGNET, tmp_path)
    assert list(params.trackers) == []


def test_unsupported_source_rejected(downloader, tmp_path):
    with pytest.raises(TorrentError):
        downloader._build_params("ftp://nope/file.torrent", tmp_path)


def test_torrent_url_fetch_parses_metadata(downloader, tmp_path, monkeypatch):
    import libtorrent as lt

    # Build a real .torrent for a small file, then serve its bytes via a
    # monkeypatched httpx.get so _params_from_torrent_url can parse it.
    data_file = tmp_path / "weights.bin"
    data_file.write_bytes(b"taosnet-test-weights" * 1024)

    fs = lt.file_storage()
    lt.add_files(fs, str(data_file))
    ct = lt.create_torrent(fs)
    lt.set_piece_hashes(ct, str(tmp_path))
    torrent_bytes = lt.bencode(ct.generate())
    expected_ih = str(lt.torrent_info(lt.bdecode(torrent_bytes)).info_hash())

    class _Resp:
        content = torrent_bytes

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        "httpx.get", lambda *a, **k: _Resp(), raising=True
    )
    params = downloader._params_from_torrent_url("https://taos.my/taosnet/x.torrent")
    assert str(params.ti.info_hash()) == expected_ih
