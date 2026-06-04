"""Hourly auto-update checker.

Polls the configured git remote once an hour and notifies the user when new
commits land. De-dupes notifications via the "last notified commit" marker so
the user gets one notification per new release, not one per poll cycle.

Uses ``asyncio.create_subprocess_exec`` (list-of-args, never shell) so
untrusted paths cannot cause command injection.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

import tinyagentos.github_releases as github_releases

logger = logging.getLogger(__name__)

# How often to check for updates (seconds). One hour by default.
CHECK_INTERVAL = 60 * 60

# Namespace used in /api/preferences/auto-update for user settings.
PREF_NAMESPACE = "auto-update"

# Defaults the user gets on a fresh install.
DEFAULT_PREFS = {
    "check_enabled": True,
    "last_notified_commit": None,
    "last_reminder_at": None,
}


async def poll_frameworks(manifests, *, http_client, arch, cache):
    """Refresh the latest-release cache for every framework that declares
    a release_source. Transient errors preserve the last-good cache entry.
    """
    for fw_id, manifest in manifests.items():
        if not manifest.get("release_source"):
            continue
        try:
            cache[fw_id] = await github_releases.fetch_latest_release(manifest, http_client, arch=arch)
        except Exception:
            logger.warning(
                "poll_frameworks: refresh for %s failed; keeping last good", fw_id
            )


async def _run(args: list[str], cwd: Path) -> tuple[int, str]:
    """Run a subprocess safely (no shell) and return (returncode, output)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(cwd),
    )
    stdout, _ = await proc.communicate()
    return proc.returncode or 0, (stdout.decode() if stdout else "")


async def update_tracking_branch(project_dir: Path) -> str:
    """The branch this install tracks for updates.

    Returns the currently checked-out branch (e.g. ``master`` on a stable
    install, ``dev`` on a dev/test box). Falls back to ``master`` when the
    repo is in detached HEAD (tag/SHA-pinned deploys) or the branch can't be
    determined, preserving the historical stable-channel behaviour.
    """
    rc, out = await _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], project_dir)
    branch = out.strip() if rc == 0 else ""
    return branch if branch and branch != "HEAD" else "master"


async def resolve_tracked_branch(settings_store, project_dir: Path) -> str:
    """The branch to track for updates: the user's saved ``tracked_branch``
    preference (set via the branch selector) when present, else the
    checked-out branch (``update_tracking_branch``)."""
    try:
        prefs = await settings_store.get_preference("user", PREF_NAMESPACE)
        chosen = (prefs or {}).get("tracked_branch")
        if chosen and isinstance(chosen, str) and chosen.strip():
            return chosen.strip()
    except Exception:
        logger.warning("resolve_tracked_branch: pref read failed; using checked-out branch")
    return await update_tracking_branch(project_dir)


async def remote_is_strictly_ahead(project_dir: Path, current: str, remote: str) -> bool:
    """True only if ``current`` is a strict ancestor of ``remote`` — i.e. the
    remote is genuinely newer. Prevents offering an older or divergent commit
    (e.g. master's tip when running ahead on dev) as an "update"."""
    if not current or not remote or current == remote:
        return False
    rc, _ = await _run(
        ["git", "merge-base", "--is-ancestor", current, remote], project_dir
    )
    return rc == 0


class AutoUpdateService:
    """Background service that periodically checks GitHub for updates.

    Depends on:
        - ``notif_store`` for firing user notifications
        - ``settings_store`` for reading user prefs (check-enabled toggle,
          dedupe marker)

    Start with ``start()`` during app lifespan, stop with ``stop()``.
    """

    def __init__(self, project_dir: Path, notif_store, settings_store, app_state=None):
        self._project_dir = project_dir
        self._notif = notif_store
        self._settings = settings_store
        self._app_state = app_state
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="auto-update-checker")
        logger.info("AutoUpdateService started (interval=%ds)", CHECK_INTERVAL)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self) -> None:
        # Small initial delay so we don't slam GitHub the instant the
        # server boots — space out with the rest of startup.
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=90)
            return
        except asyncio.TimeoutError:
            pass

        while True:
            try:
                await self._run_once()
            except Exception:
                logger.exception("auto-update check failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=CHECK_INTERVAL)
                return
            except asyncio.TimeoutError:
                pass  # tick again

    async def _run_once(self) -> None:
        prefs = await self._get_prefs()
        if not prefs.get("check_enabled", True):
            return

        # Fetch latest from origin/master
        new_commit = await self._probe_remote()
        if new_commit is None:
            pass
        else:
            current = await self._current_commit()
            # Only an update if the remote is strictly newer than us — never
            # flag an older/divergent commit (e.g. master's tip while we run
            # ahead on dev) as available.
            if await remote_is_strictly_ahead(self._project_dir, current, new_commit):
                # Skip re-notifying for a commit we've already flagged.
                if prefs.get("last_notified_commit") != new_commit:
                    await self._notify_available(current, new_commit)
                    # Remember so we don't re-notify next hour.
                    prefs["last_notified_commit"] = new_commit
                    await self._save_prefs(prefs)

        # Poll latest framework release metadata for frameworks that publish
        # GitHub releases. Guarded with getattr so it's a no-op before Task 8.1
        # initialises these state attributes.
        from tinyagentos.frameworks import FRAMEWORKS
        _app = self._app_state
        _http = getattr(_app, "http_client", None)
        _arch = getattr(_app, "host_arch", None)
        _fw_cache = getattr(_app, "latest_framework_versions", None)
        if _http is not None and _arch is not None and _fw_cache is not None:
            await poll_frameworks(
                FRAMEWORKS,
                http_client=_http,
                arch=_arch,
                cache=_fw_cache,
            )

    async def _probe_remote(self) -> Optional[str]:
        """Return the tip of the tracked remote branch (the branch this install
        is on), or None on failure."""
        branch = await resolve_tracked_branch(self._settings, self._project_dir)
        rc, _ = await _run(
            ["git", "fetch", "--quiet", "origin", branch], self._project_dir
        )
        if rc != 0:
            return None
        rc2, out = await _run(
            ["git", "rev-parse", f"origin/{branch}"], self._project_dir
        )
        if rc2 != 0:
            return None
        return out.strip()

    async def _current_commit(self) -> str:
        rc, out = await _run(["git", "rev-parse", "HEAD"], self._project_dir)
        return out.strip() if rc == 0 else ""

    async def _notify_available(self, current: str, new_commit: str) -> None:
        short_old = (current or "")[:7]
        short_new = new_commit[:7]
        await self._notif.emit_event(
            event_type="system.update",
            title="taOS update available",
            message=f"A new version is ready ({short_old}->{short_new}). Open Settings to install.",
            level="info",
        )
        logger.info("Notified user of update: %s -> %s", short_old, short_new)

    async def _get_prefs(self) -> dict:
        try:
            saved = await self._settings.get_preference("user", PREF_NAMESPACE)
            return {**DEFAULT_PREFS, **(saved or {})}
        except Exception:
            return dict(DEFAULT_PREFS)

    async def _save_prefs(self, prefs: dict) -> None:
        try:
            await self._settings.save_preference("user", PREF_NAMESPACE, prefs)
        except Exception:
            logger.exception("failed to save auto-update prefs")


