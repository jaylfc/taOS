"""Pure-URL tests for taOSnet client wiring (no libtorrent required)."""
from __future__ import annotations

import pytest

from tinyagentos.taosnet.torrent_client import (
    announce_url,
    scrape_url,
    torrent_metadata_url,
)


def test_announce_url_embeds_passkey_as_path_segment():
    assert (
        announce_url("abc123")
        == "https://tracker.taos.my/abc123/announce"
    )


def test_scrape_url():
    assert scrape_url("abc123") == "https://tracker.taos.my/abc123/scrape"


def test_torrent_metadata_url_keyed_on_info_hash():
    assert (
        torrent_metadata_url("deadbeef")
        == "https://taos.my/taosnet/deadbeef.torrent"
    )


def test_passkey_is_percent_encoded_as_single_segment():
    # An opaque token must not break path structure even if it contains
    # slashes or other reserved characters.
    url = announce_url("a/b?c=d")
    assert url == "https://tracker.taos.my/a%2Fb%3Fc%3Dd/announce"
    assert url.count("/announce") == 1


def test_custom_bases_and_trailing_slash_handling():
    assert (
        announce_url("k", tracker_base="https://tracker.example.com/")
        == "https://tracker.example.com/k/announce"
    )
    assert (
        torrent_metadata_url("h", torrent_base="https://mirror.example.com/")
        == "https://mirror.example.com/taosnet/h.torrent"
    )


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_empty_passkey_or_hash_rejected(bad):
    with pytest.raises(ValueError):
        announce_url(bad)
    with pytest.raises(ValueError):
        torrent_metadata_url(bad)
