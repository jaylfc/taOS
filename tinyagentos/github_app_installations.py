"""GitHub App installation registry.

Stores active installation IDs + metadata in a JSON file under the data
directory. This is intentionally NOT a SQLite store — installation data is
small, infrequently written, and referenced at startup time.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from tinyagentos.atomic_io import atomic_write_text

logger = logging.getLogger(__name__)

_INSTALLATIONS_FILE = "github_app_installations.json"


class GitHubAppInstallations:
    """In-memory + JSON-backed registry of active GitHub App installations."""

    def __init__(self, data_dir: Path) -> None:
        self._path: Path = data_dir / _INSTALLATIONS_FILE
        self._installations: dict[int, dict] = {}
        self._loaded: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()

    async def init(self) -> None:
        """Load installations from disk. Idempotent."""
        if self._loaded:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._load()
        self._loaded = True

    async def close(self) -> None:
        """No-op (no open handles). Present for symmetry with BaseStore."""
        pass

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            self._installations = {}
            return
        try:
            data = json.loads(self._path.read_text())
            # Convert string keys back to int
            self._installations = {
                int(k): v for k, v in data.get("installations", {}).items()
            }
        except (json.JSONDecodeError, ValueError, KeyError, AttributeError, TypeError) as exc:
            logger.warning(
                "Corrupt %s, starting fresh: %s", self._path.name, exc
            )
            self._installations = {}

    def _save_sync(self, data: dict) -> None:
        """Write pre-serialised data to disk (called in a thread)."""
        atomic_write_text(self._path, json.dumps(data, indent=2))

    async def _save(self) -> None:
        """Persist installations to disk without blocking the event loop.

        Snapshot the installations dict on the event-loop thread before
        dispatching to ``asyncio.to_thread`` to avoid a data race: the
        thread iterates over a local copy, so concurrent ``add()`` /
        ``remove()`` calls cannot trigger ``RuntimeError`` from dict
        mutation during iteration.
        """
        data = {
            "installations": {str(k): v for k, v in self._installations.items()},
            "updated_at": int(time.time()),
        }
        await asyncio.to_thread(self._save_sync, data)

    # -- public API ---------------------------------------------------------

    def get(self, installation_id: int) -> dict | None:
        """Return installation metadata or None."""
        return self._installations.get(installation_id)

    def list_all(self) -> list[dict]:
        """Return all active installations with their metadata."""
        return [
            {"installation_id": iid, **meta}
            for iid, meta in self._installations.items()
        ]

    async def add(
        self,
        installation_id: int,
        account_login: str = "",
        account_type: str = "",
        account_avatar_url: str = "",
        repository_selection: str = "selected",
    ) -> None:
        """Record an active installation."""
        async with self._lock:
            self._installations[installation_id] = {
                "account_login": account_login,
                "account_type": account_type,
                "account_avatar_url": account_avatar_url,
                "repository_selection": repository_selection,
                "installed_at": int(time.time()),
            }
            await self._save()
        logger.info(
            "GitHub App installation %s added (%s/%s)",
            installation_id,
            account_login,
            account_type,
        )

    async def remove(self, installation_id: int) -> bool:
        """Remove an installation. Returns False if not found."""
        async with self._lock:
            if installation_id not in self._installations:
                return False
            del self._installations[installation_id]
            await self._save()
        logger.info("GitHub App installation %s removed", installation_id)
        return True
