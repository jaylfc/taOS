"""Hourly auto-update checker.

Polls the configured git remote once an hour and notifies the user when new
commits land. De-dupes notifications via the "last notified commit" marker so
the user gets one notification per new release, not one per poll cycle.

Also fires a single anonymous install-count ping per cycle to
``TAOS_UPDATE_CHECK_URL`` (default ``https://taos.my/api/v1/version-check``).
The ping carries a random per-install id (no PII, stored in the data dir) so
the server keeps an exact historical install count. Disable with
``TAOS_NO_UPDATE_PING=1`` or the ``update_ping_enabled`` preference in
Settings. All failures degrade silently to debug logging.

Uses ``asyncio.create_subprocess_exec`` (list-of-args, never shell) so
untrusted paths cannot cause command injection.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import sys
from pathlib import Path
from typing import Optional

import tinyagentos.github_releases as github_releases
import tinyagentos

logger = logging.getLogger(__name__)

# Conservative git ref-name validation. The tracked branch flows into git
# argv (fetch/pull/rev-parse), so a value like "--upload-pack=evil" would be a
# flag-injection (argument injection) if it reached those calls. Reject any
# value that isn't a plain ref name: no leading '-', no whitespace/control
# chars, none of git's forbidden ref characters (~ ^ : ? * [ \), no "..",
# no "@{", no leading/trailing '/', no "//", no trailing ".lock"/".".
_FORBIDDEN_REF_CHARS = set(" \t\n\r~^:?*[\\\x7f")


def is_valid_branch_name(name: str) -> bool:
    """True if *name* is a safe git branch/ref name to pass in argv.

    Mirrors the relevant subset of ``git check-ref-format --branch`` rules.
    Used to keep user-influenced branch values out of git flag positions.
    """
    if not isinstance(name, str) or not name or len(name) > 255:
        return False
    if name[0] == "-" or name[0] == "/" or name[-1] == "/" or name[-1] == ".":
        return False
    if name.endswith(".lock") or "//" in name or ".." in name or "@{" in name:
        return False
    if any(c in _FORBIDDEN_REF_CHARS or ord(c) < 0x20 for c in name):
        return False
    return True

# How often to check for updates (seconds). One hour by default.
CHECK_INTERVAL = 60 * 60

# Namespace used in /api/preferences/auto-update for user settings.
PREF_NAMESPACE = "auto-update"

# Default URL for the version-check/install-ping endpoint.
_DEFAULT_UPDATE_CHECK_URL = "https://taos.my/api/v1/version-check"

# Defaults the user gets on a fresh install.
DEFAULT_PREFS = {
    "check_enabled": True,
    "update_ping_enabled": True,
    "last_notified_commit": None,
    "last_reminder_at": None,
}


def _ping_enabled_by_env() -> bool:
    """False when the operator sets TAOS_NO_UPDATE_PING=1 in the environment."""
    return os.environ.get("TAOS_NO_UPDATE_PING", "").strip() not in ("1", "true", "yes")


def _install_id(data_dir: Optional[Path]) -> str:
    """Return this install's stable random id, creating it once if needed.

    A random UUID with no PII and no hardware fingerprint, stored at
    ``<data_dir>/.install_id``. The data dir is preserved across upgrades and
    in-place reinstalls, so the id (and the install's place in the historical
    count) is stable. A full wipe yields a new id, which is correct: that is a
    genuinely new install.
    """
    if data_dir is None:
        return ""
    path = Path(data_dir) / ".install_id"
    try:
        if path.exists():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        import uuid
        new_id = uuid.uuid4().hex
        path.write_text(new_id, encoding="utf-8")
        return new_id
    except Exception:
        return ""


async def send_version_ping(http_client, data_dir: Optional[Path] = None) -> None:
    """Fire the anonymous version-check/install-count ping.

    Sends ``GET <url>?v=<version>&platform=<sys.platform>-<machine>&id=<uuid>``.
    The id is a random per-install UUID (no PII) so the server keeps an exact
    historical count. Any error (network, DNS, timeout, bad status) is logged
    at DEBUG and silently dropped.
    """
    url = os.environ.get("TAOS_UPDATE_CHECK_URL", "").strip() or _DEFAULT_UPDATE_CHECK_URL
    version = getattr(tinyagentos, "__version__", "unknown")
    plat = f"{sys.platform}-{platform.machine()}"
    params = {"v": version, "platform": plat}
    iid = _install_id(data_dir)
    if iid:
        params["id"] = iid
    try:
        resp = await http_client.get(
            url,
            params=params,
            timeout=5.0,
            follow_redirects=True,
        )
        logger.debug(
            "version-check ping: status=%s url=%s", resp.status_code, url
        )
        if resp.status_code == 200:
            try:
                data = resp.json()
                latest = data.get("latest_version")
                if latest:
                    logger.debug("latest release from taos.my: %s", latest)
            except Exception:
                pass
    except Exception as exc:
        logger.debug("version-check ping failed (ignored): %s", exc)


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
            candidate = chosen.strip()
            # Never let a malformed stored value reach git argv (flag injection).
            if is_valid_branch_name(candidate):
                return candidate
            logger.warning(
                "resolve_tracked_branch: stored tracked_branch %r is not a valid "
                "ref name; ignoring and using the checked-out branch", candidate,
            )
    except Exception:
        logger.warning("resolve_tracked_branch: pref read failed; using checked-out branch")
    return await update_tracking_branch(project_dir)


# .txt is deliberately excluded: a root-level .txt is ambiguous (e.g.
# requirements.txt / constraints.txt), and misclassifying it as docs would
# silently suppress a real dependency update. A .txt under docs/ is still
# treated as docs via the docs/ branch below.
_DOC_EXTENSIONS = (".md", ".markdown", ".rst")
_CODE_EXTENSIONS = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json",
    ".yaml", ".yml", ".sh", ".toml", ".cfg", ".ini",
)


def is_documentation_path(path: str) -> bool:
    """True when *path* is documentation-only.

    A path counts as documentation if it ends in a doc extension anywhere, or
    lives under ``docs/`` without a code/config extension. When unsure, False.
    """
    p = path.strip().replace("\\", "/")
    if not p:
        return False
    lower = p.lower()
    if any(lower.endswith(ext) for ext in _DOC_EXTENSIONS):
        return True
    if p.startswith("docs/"):
        if any(lower.endswith(ext) for ext in _CODE_EXTENSIONS):
            return False
        return True
    return False


async def changes_are_docs_only(
    project_dir: Path, current: str, remote: str
) -> bool:
    """True when every file changed between *current* and *remote* is documentation."""
    if not current or not remote or current == remote:
        return False
    if project_dir is None or not Path(project_dir).exists():
        return False
    rc, out = await _run(
        ["git", "diff", "--name-only", f"{current}..{remote}"],
        project_dir,
    )
    if rc != 0:
        return False
    paths = [line.strip() for line in out.splitlines() if line.strip()]
    if not paths:
        return False
    return all(is_documentation_path(p) for p in paths)


async def remote_is_strictly_ahead(project_dir: Path, current: str, remote: str) -> bool:
    """True only if ``current`` is a strict ancestor of ``remote`` -- i.e. the
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
          dedupe marker, ping opt-out)

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
        # server boots -- space out with the rest of startup.
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

        # Anonymous install-count ping. Two opt-out layers:
        # 1. TAOS_NO_UPDATE_PING=1 env var (operator/system level)
        # 2. update_ping_enabled pref (Settings UI toggle, per-user)
        if _ping_enabled_by_env() and prefs.get("update_ping_enabled", True):
            _app = self._app_state
            _http = getattr(_app, "http_client", None)
            _data_dir = getattr(_app, "data_dir", None)
            if _http is not None:
                await send_version_ping(_http, _data_dir)
            else:
                # No shared client available yet -- create a one-shot client.
                try:
                    import httpx
                    async with httpx.AsyncClient() as tmp_client:
                        await send_version_ping(tmp_client, _data_dir)
                except Exception as exc:
                    logger.debug("version-check ping (standalone client) failed: %s", exc)

        # Fetch latest from origin/<tracked branch>
        new_commit = await self._probe_remote()
        if new_commit is not None:
            current = await self._current_commit()
            # Only an update if the remote is strictly newer than us -- never
            # flag an older/divergent commit (e.g. master's tip while we run
            # ahead on dev) as available.
            if await remote_is_strictly_ahead(self._project_dir, current, new_commit):
                if await changes_are_docs_only(self._project_dir, current, new_commit):
                    logger.debug(
                        "auto-update: skipping docs-only diff %s..%s",
                        (current or "")[:7],
                        new_commit[:7],
                    )
                    return
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
        # `--` keeps `branch` in refspec position; resolve_tracked_branch also
        # validates it (flag-injection defence in depth).
        rc, _ = await _run(
            ["git", "fetch", "--quiet", "origin", "--", branch], self._project_dir
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
