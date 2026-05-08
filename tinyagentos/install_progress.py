"""In-memory install progress tracking.

The Store's install path is fire-and-forget today: ``POST
/api/store/install-v2`` returns 200 the moment the resolver decides
which backend to use, but the actual download + setup happens after
the response is sent. The frontend has no way to know whether work is
still in flight, how far along the download is, or whether the
install succeeded.

This module exposes a tiny in-memory progress store keyed by
``install_id`` (uuid generated per install attempt). The Store route
allocates an id, the installer updates ``state`` / ``bytes_downloaded``
/ ``bytes_total`` as it goes, and the frontend polls a GET endpoint.

State is in-memory only — losing it on controller restart is fine
because the install path is also lost (subprocess dies). Stale entries
older than INSTALL_PROGRESS_TTL_S are pruned lazily on every call.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from threading import Lock
from typing import Literal

InstallState = Literal[
    "queued",        # registered but no work has started yet
    "downloading",   # bytes streaming, watch bytes_downloaded / bytes_total
    "verifying",     # SHA256 check, post-download
    "unpacking",     # extracting / moving into place
    "starting",      # backend service starting up
    "installed",     # terminal — success
    "failed",        # terminal — error populated
    "cancelled",     # terminal — caller asked to stop. Reserved for the
                     # follow-up "Cancel install" UI; the frontend already
                     # treats this as a terminal state and hides the bar
                     # the same way as installed/failed.
]

# Drop entries older than this many seconds the next time anything
# touches the store. Long enough that a slow hardware-side install
# (multi-GB models on a Pi) doesn't get pruned mid-flight; short
# enough that a forgotten "installed" entry doesn't linger forever.
INSTALL_PROGRESS_TTL_S = 60 * 60  # 1 hour


@dataclass
class InstallProgress:
    install_id: str
    app_id: str
    target_remote: str | None
    state: InstallState = "queued"
    bytes_downloaded: int = 0
    bytes_total: int = 0
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error: str | None = None
    detail: str = ""  # one-line human-readable progress hint

    @property
    def percent(self) -> float | None:
        if self.bytes_total <= 0:
            return None
        return min(100.0, 100.0 * self.bytes_downloaded / self.bytes_total)

    def to_dict(self) -> dict:
        return {
            "install_id": self.install_id,
            "app_id": self.app_id,
            "target_remote": self.target_remote,
            "state": self.state,
            "bytes_downloaded": self.bytes_downloaded,
            "bytes_total": self.bytes_total,
            "percent": self.percent,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "detail": self.detail,
        }


class InstallProgressStore:
    """Thread-safe in-memory progress tracker.

    The Lock is fine for the modest concurrency the Store sees — a
    handful of in-flight installs at most. Async callers don't await
    inside the critical section, just snapshot.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._entries: dict[str, InstallProgress] = {}

    def start(self, app_id: str, target_remote: str | None = None) -> InstallProgress:
        install_id = uuid.uuid4().hex
        entry = InstallProgress(
            install_id=install_id,
            app_id=app_id,
            target_remote=target_remote,
        )
        with self._lock:
            self._prune_locked()
            self._entries[install_id] = entry
        return entry

    def update(
        self,
        install_id: str,
        *,
        state: InstallState | None = None,
        bytes_downloaded: int | None = None,
        bytes_total: int | None = None,
        detail: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            entry = self._entries.get(install_id)
            if entry is None:
                return
            if state is not None:
                entry.state = state
            if bytes_downloaded is not None:
                entry.bytes_downloaded = bytes_downloaded
            if bytes_total is not None:
                entry.bytes_total = bytes_total
            if detail is not None:
                entry.detail = detail
            if error is not None:
                entry.error = error
            entry.updated_at = time.time()

    def finish(
        self,
        install_id: str,
        *,
        success: bool,
        error: str | None = None,
        detail: str | None = None,
    ) -> None:
        with self._lock:
            entry = self._entries.get(install_id)
            if entry is None:
                return
            entry.state = "installed" if success else "failed"
            if error is not None:
                entry.error = error
            if detail is not None:
                entry.detail = detail
            entry.finished_at = time.time()
            entry.updated_at = entry.finished_at

    def get(self, install_id: str) -> InstallProgress | None:
        with self._lock:
            self._prune_locked()
            return self._entries.get(install_id)

    def list_by_app(self, app_id: str) -> list[InstallProgress]:
        """Return all entries for an app, newest first. Useful when the
        frontend doesn't yet know the install_id but wants to find any
        in-flight install for a card."""
        with self._lock:
            self._prune_locked()
            matches = [e for e in self._entries.values() if e.app_id == app_id]
        matches.sort(key=lambda e: e.started_at, reverse=True)
        return matches

    def list_all(self) -> list[InstallProgress]:
        with self._lock:
            self._prune_locked()
            entries = list(self._entries.values())
        entries.sort(key=lambda e: e.started_at, reverse=True)
        return entries

    def _prune_locked(self) -> None:
        cutoff = time.time() - INSTALL_PROGRESS_TTL_S
        stale = [
            iid for iid, e in self._entries.items()
            if e.finished_at is not None and e.finished_at < cutoff
        ]
        for iid in stale:
            del self._entries[iid]


# Single shared instance — bound to app.state in app.py and consumed
# by the Store install routes. Exposed as a module global so install
# helpers (download_file etc.) can update progress without each one
# threading the store through their kwargs.
_GLOBAL: InstallProgressStore | None = None


def get_global_store() -> InstallProgressStore:
    global _GLOBAL
    if _GLOBAL is None:
        _GLOBAL = InstallProgressStore()
    return _GLOBAL
