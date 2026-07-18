"""Worker-side version-detection service.

Polls the configured update-check endpoint on an interval and notifies the
controller (via a state flag on the WorkerAgent) when a new version is
available. Mirrors the controller-side ``AutoUpdateService``
(``tinyagentos/auto_update.py``) but runs on the worker node.

Sends an anonymous install-count ping (worker id, platform, version) on
each poll cycle — same pattern as ``send_version_ping()``. All failures
degrade silently to debug logging.

Config is stored in a JSON file under the worker's state directory so it
persists across restarts. The ``WorkerAgent`` reads the service's state
during heartbeat and forwards it to the controller, which surfaces it in
the Resource Manager / cluster view.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import sys
from pathlib import Path
from typing import Optional

import tinyagentos

logger = logging.getLogger(__name__)

# Default URL for the version-check/install-ping endpoint.
_DEFAULT_UPDATE_CHECK_URL = "https://taos.my/api/v1/version-check"

# How often to check for updates (seconds). One hour by default.
_DEFAULT_CHECK_INTERVAL = 60 * 60

# Config file stored in the worker's state directory.
_CONFIG_FILENAME = "update_check_config.json"


def _default_state_dir() -> Path:
    """Return the default worker state directory."""
    return Path("/var/lib/tinyagentos-worker")


def _install_id(state_dir: Path) -> str:
    """Return this install's stable random id, creating it once if needed.

    A random UUID with no PII and no hardware fingerprint, stored at
    ``<state_dir>/.install_id``. Same pattern as auto_update._install_id().
    """
    path = Path(state_dir) / ".install_id"
    try:
        if path.exists():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        state_dir.mkdir(parents=True, exist_ok=True)
        import uuid
        new_id = uuid.uuid4().hex
        path.write_text(new_id, encoding="utf-8")
        return new_id
    except Exception:
        return ""


class WorkerUpdateConfig:
    """Worker-side update-check preferences.

    Stored as JSON in the worker's state directory. Every field has a
    sensible default so a missing config file behaves the same as a
    fresh install.
    """

    enabled: bool = True
    channel: str = "stable"
    pinned_version: str | None = None
    check_interval: int = _DEFAULT_CHECK_INTERVAL
    last_notified_version: str | None = None

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "channel": self.channel,
            "pinned_version": self.pinned_version,
            "check_interval": self.check_interval,
            "last_notified_version": self.last_notified_version,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> WorkerUpdateConfig:
        cfg = cls()
        if not d:
            return cfg
        cfg.enabled = bool(d.get("enabled", True))
        cfg.channel = str(d.get("channel", "stable"))
        pinned = d.get("pinned_version")
        cfg.pinned_version = str(pinned) if pinned else None
        cfg.check_interval = int(d.get("check_interval", _DEFAULT_CHECK_INTERVAL))
        notified = d.get("last_notified_version")
        cfg.last_notified_version = str(notified) if notified else None
        return cfg


def load_config(state_dir: Path) -> WorkerUpdateConfig:
    """Load update-check config from disk, returning defaults on any error."""
    path = state_dir / _CONFIG_FILENAME
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            return WorkerUpdateConfig.from_dict(raw)
    except Exception:
        logger.debug("Failed to load update-check config; using defaults", exc_info=True)
    return WorkerUpdateConfig()


def save_config(state_dir: Path, config: WorkerUpdateConfig) -> None:
    """Persist update-check config. Errors are logged and swallowed."""
    path = state_dir / _CONFIG_FILENAME
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")
    except Exception:
        logger.debug("Failed to save update-check config", exc_info=True)


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a version string like '1.0.0-beta.40' into a numeric tuple.

    Pre-release markers are stripped for comparison; only the numeric
    components are kept. Returns an empty tuple on parse failure.
    """
    try:
        # Strip 'v' prefix (single leading 'v' or 'V' only)
        v = version_str.strip()
        if v.startswith("v") or v.startswith("V"):
            v = v[1:]
        # Take only the portion before any '-' or '+'
        v = v.split("-")[0].split("+")[0]
        parts = [int(p) for p in v.split(".") if p]
        return tuple(parts) if parts else ()
    except (ValueError, TypeError):
        return ()


def is_newer_version(latest: str, current: str) -> bool:
    """True if *latest* is strictly newer than *current*.

    Compares numeric components only; pre-release markers are ignored.
    A version with fewer components is padded with zeros for comparison.
    Returns False when either string is unparseable.
    """
    latest_parts = _parse_version(latest)
    current_parts = _parse_version(current)
    if not latest_parts or not current_parts:
        return False
    # Pad shorter tuple with zeros
    max_len = max(len(latest_parts), len(current_parts))
    l = list(latest_parts) + [0] * (max_len - len(latest_parts))
    c = list(current_parts) + [0] * (max_len - len(current_parts))
    for lp, cp in zip(l, c):
        if lp > cp:
            return True
        if lp < cp:
            return False
    return False  # equal


def _channel_from_version(version: str) -> str:
    """Infer the channel from a version string.

    Returns 'dev', 'beta', or 'stable' based on pre-release markers.
    """
    v = version.strip().lower()
    if "dev" in v:
        return "dev"
    if "beta" in v or "alpha" in v or "rc" in v:
        return "beta"
    return "stable"


def version_matches_channel(version: str, channel: str) -> bool:
    """True if *version* belongs to the given *channel*.

    - 'stable': only full releases (no pre-release markers)
    - 'beta': beta, alpha, rc pre-releases
    - 'dev': any version, including dev builds
    """
    if channel == "dev":
        return True
    v_channel = _channel_from_version(version)
    if channel == "stable":
        return v_channel == "stable"
    if channel == "beta":
        return v_channel in ("stable", "beta")
    return True


def version_matches_pin(version: str, pinned: str | None) -> bool:
    """True if *version* is not newer than *pinned*.

    When a user pins to a version, only that exact version or older is
    considered (pin acts as a ceiling). None means no pin.
    """
    if pinned is None:
        return True
    return not is_newer_version(version, pinned)


class WorkerUpdateService:
    """Background service that periodically checks for worker updates.

    Started during worker agent startup, stopped on shutdown. Stores its
    config and state in the worker's state directory. The agent reads
    the service's state via ``get_state()`` during heartbeat to forward
    update notifications to the controller.

    Parameters:
        state_dir: Worker state directory (stores config + install id)
        worker_name: Worker name (sent in ping)
        http_client: Optional shared httpx client; creates one-shot if None
    """

    def __init__(
        self,
        state_dir: Path | None = None,
        worker_name: str = "",
        http_client=None,
    ):
        self._state_dir = state_dir or _default_state_dir()
        self._worker_name = worker_name
        self._http_client = http_client
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

        # In-memory cache of the last check result, read by the agent
        # during heartbeat and forwarded to the controller.
        self._latest_version: str | None = None
        self._update_available: bool = False
        self._update_message: str = ""

    async def start(self) -> None:
        """Start the background update-check loop."""
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="worker-update-checker")
        logger.info("WorkerUpdateService started")

    async def stop(self) -> None:
        """Stop the background loop and await clean shutdown."""
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def get_state(self) -> dict:
        """Return current update-check state for the heartbeat payload.

        The agent calls this during heartbeat and includes the result so
        the controller can surface update availability in the cluster view.
        """
        return {
            "update_available": self._update_available,
            "latest_version": self._latest_version,
            "current_version": getattr(tinyagentos, "__version__", "unknown"),
            "message": self._update_message,
        }

    async def _loop(self) -> None:
        """Main loop: small initial delay, then poll on configured interval."""
        # Initial delay: use the configured interval, floored at 10 s
        # and capped at 90 s so we don't slam the endpoint on startup.
        config = load_config(self._state_dir)
        initial_delay = min(max(config.check_interval, 10), 90)
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=initial_delay)
            return
        except asyncio.TimeoutError:
            pass

        while True:
            try:
                await self._run_once()
            except Exception:
                logger.exception("Worker update check failed")
            config = load_config(self._state_dir)
            interval = max(config.check_interval, 300)  # floor: 5 min
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass  # tick again

    async def _run_once(self) -> None:
        """Perform one check cycle: ping + version comparison."""
        config = load_config(self._state_dir)
        if not config.enabled:
            return

        # --- Anonymous install-count ping ---
        url = os.environ.get("TAOS_UPDATE_CHECK_URL", "").strip() or _DEFAULT_UPDATE_CHECK_URL
        version = getattr(tinyagentos, "__version__", "unknown")
        plat = f"{sys.platform}-{platform.machine()}"
        params = {
            "v": version,
            "platform": plat,
            "worker": self._worker_name,
            "channel": config.channel,
        }
        iid = _install_id(self._state_dir)
        if iid:
            params["id"] = iid

        latest_version: str | None = None
        try:
            client = self._http_client
            if client is None:
                import httpx
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as tmp:
                    resp = await tmp.get(url, params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        latest_version = data.get("latest_version")
            else:
                resp = await client.get(url, params=params, timeout=10.0)
                if resp.status_code == 200:
                    data = resp.json()
                    latest_version = data.get("latest_version")
        except Exception as exc:
            logger.debug("version-check ping failed (ignored): %s", exc)
            return

        if not latest_version:
            return

        # --- Version comparison ---
        # Pin check: if pinned, only consider versions <= pinned
        if not version_matches_pin(latest_version, config.pinned_version):
            logger.debug(
                "update check: latest %s exceeds pinned %s; not notifying",
                latest_version, config.pinned_version,
            )
            self._update_available = False
            self._update_message = ""
            return

        # Channel filter: only notify for versions in the user's channel
        if not version_matches_channel(latest_version, config.channel):
            logger.debug(
                "update check: latest %s not in channel %s; not notifying",
                latest_version, config.channel,
            )
            self._update_available = False
            self._update_message = ""
            return

        if not is_newer_version(latest_version, version):
            self._update_available = False
            self._update_message = ""
            return

        # All checks passed — record the latest available version.
        self._latest_version = latest_version

        # Avoid re-notifying for a version we've already flagged.
        if config.last_notified_version == latest_version:
            return

        # New version! Set the update flag.
        self._update_available = True
        self._update_message = (
            f"Worker update available: {version} → {latest_version}"
        )
        config.last_notified_version = latest_version
        save_config(self._state_dir, config)
        logger.info(
            "Worker update available: %s → %s (channel=%s)",
            version, latest_version, config.channel,
        )
