"""Client-side taOSnet wiring for the libtorrent download path.

taOSnet is a closed, authenticated swarm. The shared magnet/.torrent carries
the info_hash and the HuggingFace web seeds but NOT the tracker passkey; each
node injects its own account-bound passkey into the tracker announce at
runtime. The tracker answers 401 on a bad or rotated passkey, so the client
re-fetches the passkey and re-announces.

This module holds the pure URL construction. The libtorrent-touching wiring
(setting trackers / url_seeds on add_torrent_params, torrent_url metadata
fetch) lives in torrent_downloader.py, which imports these helpers.
"""
from __future__ import annotations

from urllib.parse import quote

DEFAULT_TRACKER_BASE = "https://tracker.taos.my"
DEFAULT_TORRENT_BASE = "https://taos.my"


def announce_url(passkey: str, tracker_base: str = DEFAULT_TRACKER_BASE) -> str:
    """Private tracker announce URL with the node's passkey as a path segment:
    ``https://tracker.taos.my/<passkey>/announce``.

    The passkey is opaque and is percent-encoded as a single path segment.
    """
    return f"{tracker_base.rstrip('/')}/{_segment(passkey, 'passkey')}/announce"


def scrape_url(passkey: str, tracker_base: str = DEFAULT_TRACKER_BASE) -> str:
    """Private tracker scrape URL, passkey as a path segment."""
    return f"{tracker_base.rstrip('/')}/{_segment(passkey, 'passkey')}/scrape"


def torrent_metadata_url(info_hash: str, torrent_base: str = DEFAULT_TORRENT_BASE) -> str:
    """Public .torrent metadata URL, keyed on info_hash. This is the
    web-seed-only fetch path libtorrent uses to get the piece hashes."""
    return f"{torrent_base.rstrip('/')}/taosnet/{_segment(info_hash, 'info_hash')}.torrent"


def _segment(value: str, name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{name} is required")
    return quote(value.strip(), safe="")
