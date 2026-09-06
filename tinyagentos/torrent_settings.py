"""User-facing torrent / seeding settings.

Three knobs, durable in ``data/torrent_settings.json``:

- ``seed_enabled`` — whether TinyAgentOS seeds models it has downloaded.
  Default True because model sharing is a core OS feature and the
  whole point of the mesh is that every install is a peer. Users who
  don't want to share can flip this off.

- ``upload_rate_limit_kbps`` — hard cap on upload bandwidth across all
  active torrents, in KB/s. Default 5000 (≈40 Mbit/s), which is
  roughly half of a typical UK fibre upstream and well below even
  a 1 Gbit/s symmetric line. libtorrent enforces this at the
  session level.

- ``max_active_seeds`` — how many torrents the session will actively
  seed simultaneously. Default 20. Caps RAM usage (libtorrent holds
  ~16 MB per active torrent) and prevents a box with thousands of
  downloaded models from pinning itself as a universal peer.

Settings live in a plain JSON file, not a SQLite store, because they
change rarely and the read path wants to be dependency-free (the
TorrentDownloader constructor reads them at session-start before any
async context exists).
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TorrentSettings:
    seed_enabled: bool = True
    upload_rate_limit_kbps: int = 5000
    max_active_seeds: int = 20

    def to_dict(self) -> dict:
        return asdict(self)


class TorrentSettingsStore:
    """JSON-backed persistent store for the three torrent knobs.

    Thread-safe isn't a concern — the store is read once on
    TorrentDownloader session-start and written via a single
    /api/torrent/settings PUT handler. Concurrent writes from multiple
    browser tabs would race, but the worst-case outcome is the last
    write wins, which is fine for a 3-field config file.
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> TorrentSettings:
        if not self.path.exists():
            return TorrentSettings()
        try:
            raw = json.loads(self.path.read_text())
            return TorrentSettings(
                seed_enabled=bool(raw.get("seed_enabled", True)),
                upload_rate_limit_kbps=int(raw.get("upload_rate_limit_kbps", 5000)),
                max_active_seeds=int(raw.get("max_active_seeds", 20)),
            )
        except Exception:
            logger.exception("torrent_settings.json corrupt — using defaults")
            return TorrentSettings()

    def save(self, settings: TorrentSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(settings.to_dict(), indent=2))
