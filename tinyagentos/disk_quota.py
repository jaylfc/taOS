"""Host-side disk quota monitor for taOS agent containers.

Polls each running taos-agent-* container for rootfs usage, raises
notifications at threshold boundaries, and persists usage snapshots so
the UI can read them without a live query.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tinyagentos.config import AppConfig
    from tinyagentos.notifications import NotificationStore

logger = logging.getLogger(__name__)

_DISK_USAGE_RE = re.compile(r"(?i)disk\s+usage.*?:\s*([\d.]+)\s*(GiB?|MiB?|KiB?|B)", re.MULTILINE)


def _to_gib(value: float, unit: str) -> float:
    unit = unit.upper()
    if unit.startswith("G"):
        return value
    if unit.startswith("M"):
        return value / 1024.0
    if unit.startswith("K"):
        return value / (1024.0 * 1024.0)
    return value / (1024.0 ** 3)


class DiskQuotaMonitor:
    DEFAULT_QUOTA_GIB = 40
    WARN_THRESHOLD = 0.875
    HARD_THRESHOLD = 1.0

    def __init__(self, config: "AppConfig", container_backend, notifications: "NotificationStore"):
        self._config = config
        self._backend = container_backend
        self._notifications = notifications
        self._last_state: dict[str, str] = {}

    async def scan_all(self) -> list[dict]:
        """Iterate live taos-agent-* containers, sample rootfs usage,
        emit notifications at threshold boundaries, persist usage snapshot.

        Returns list of {name, used_gib, quota_gib, percent, state, last_checked_at}.
        """
        try:
            containers = await self._backend.list_containers(prefix="taos-agent-")
        except Exception:
            logger.warning("disk_quota: could not list containers", exc_info=True)
            return []

        results: list[dict] = []
        for cinfo in containers:
            agent_name = cinfo.name[len("taos-agent-"):]
            try:
                record = await self._scan_one(agent_name, cinfo.name)
                if record is not None:
                    results.append(record)
            except Exception:
                logger.warning("disk_quota: scan failed for %s", agent_name, exc_info=True)

        return results

    async def resize_quota(self, agent_name: str, new_size_gib: int) -> dict:
        """Apply a new quota size to an agent.

        Raises ValueError("dir-backend:...") if the pool is dir-type.
        Raises ValueError("agent not found: ...") for unknown agents.
        Raises RuntimeError on incus command failure.
        """
        agent = self._find_agent(agent_name)
        if agent is None:
            raise ValueError(f"agent not found: {agent_name}")

        pool_type = await self._detect_pool_type(f"taos-agent-{agent_name}")
        if pool_type == "dir":
            raise ValueError("dir-backend:cannot live-resize quotas on a dir storage pool")

        container = f"taos-agent-{agent_name}"
        cmd = ["incus", "config", "device", "set", container, "root", f"size={new_size_gib}GiB"]
        rc, out = await _run(cmd)
        if rc != 0:
            raise RuntimeError(f"incus resize failed: {out.strip()}")

        agent["disk_quota_gib"] = new_size_gib
        await self._notifications.emit_event(
            "disk_quota",
            f"Agent {agent_name} quota updated",
            f"Disk quota set to {new_size_gib} GiB",
            level="info",
        )
        return {"ok": True, "agent_name": agent_name, "new_quota_gib": new_size_gib,
                "message": f"quota set to {new_size_gib} GiB"}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _scan_one(self, agent_name: str, container_name: str) -> dict | None:
        used_gib = await self._sample_usage(container_name)
        if used_gib is None:
            return None

        agent = self._find_agent(agent_name)
        quota_gib = float(
            (agent.get("disk_quota_gib") if agent else None) or self.DEFAULT_QUOTA_GIB
        )
        percent = used_gib / quota_gib if quota_gib > 0 else 0.0

        if percent >= self.HARD_THRESHOLD:
            new_state = "hard"
        elif percent >= self.WARN_THRESHOLD:
            new_state = "warn"
        else:
            new_state = "ok"

        prev_state = self._last_state.get(agent_name, "ok")
        await self._maybe_notify(agent_name, prev_state, new_state, used_gib, quota_gib, percent)
        self._last_state[agent_name] = new_state

        if new_state == "hard" and agent is not None and not agent.get("paused"):
            agent["paused"] = True
            logger.warning("disk_quota: pausing agent %s — disk full", agent_name)

        ts = time.time()
        if agent is not None:
            agent["disk_usage_gib"] = round(used_gib, 3)
            agent["disk_last_checked_at"] = ts

        return {
            "name": agent_name,
            "used_gib": round(used_gib, 3),
            "quota_gib": quota_gib,
            "percent": round(percent, 4),
            "state": new_state,
            "last_checked_at": ts,
        }

    async def _sample_usage(self, container_name: str) -> float | None:
        """Priority: btrfs qgroup > incus info > exec df."""
        for strategy in (
            self._sample_btrfs_qgroup,
            self._sample_incus_info,
            self._sample_df,
        ):
            try:
                result = await strategy(container_name)
                if result is not None:
                    return result
            except Exception:
                pass
        logger.warning("disk_quota: all sampling strategies failed for %s", container_name)
        return None

    async def _sample_btrfs_qgroup(self, container_name: str) -> float | None:
        """Attempt to read disk usage from incus info (which for btrfs pools
        includes qgroup-derived figures in the storage section)."""
        rc, out = await _run(["incus", "info", container_name])
        if rc != 0:
            return None
        m = _DISK_USAGE_RE.search(out)
        if m:
            return _to_gib(float(m.group(1)), m.group(2))
        return None

    async def _sample_incus_info(self, container_name: str) -> float | None:
        """Extract disk usage from incus info output line-by-line."""
        rc, out = await _run(["incus", "info", container_name])
        if rc != 0:
            return None
        for line in out.splitlines():
            low = line.lower()
            if "disk" not in low:
                continue
            parts = line.split()
            for i, p in enumerate(parts):
                for suffix, mult in [("GiB", 1.0), ("MiB", 1.0/1024), ("KiB", 1.0/(1024**2))]:
                    if p == suffix and i > 0:
                        try:
                            return float(parts[i - 1]) * mult
                        except ValueError:
                            pass
                    if p.endswith(suffix):
                        try:
                            return float(p[:-len(suffix)]) * mult
                        except ValueError:
                            pass
        return None

    async def _sample_df(self, container_name: str) -> float | None:
        """Fallback: exec df -BG / inside the container."""
        rc, out = await _run(
            ["incus", "exec", container_name, "--", "df", "-BG", "/"],
            timeout=15,
        )
        if rc != 0:
            return None
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3:
                used_str = parts[2]
                if used_str.endswith("G"):
                    try:
                        return float(used_str[:-1])
                    except ValueError:
                        pass
        return None

    async def _detect_pool_type(self, container_name: str) -> str:
        """Return the incus storage pool driver type for a container."""
        rc, out = await _run(["incus", "config", "show", container_name])
        pool_name = "default"
        if rc == 0:
            for line in out.splitlines():
                if "pool:" in line.lower():
                    pool_name = line.split(":")[-1].strip()
                    break
        rc2, out2 = await _run(["incus", "storage", "show", pool_name])
        if rc2 != 0:
            return "unknown"
        for line in out2.splitlines():
            if "driver:" in line.lower():
                return line.split(":")[-1].strip().lower()
        return "unknown"

    async def _maybe_notify(
        self,
        agent_name: str,
        prev_state: str,
        new_state: str,
        used_gib: float,
        quota_gib: float,
        percent: float,
    ) -> None:
        if prev_state == new_state:
            return

        pct_str = f"{percent * 100:.1f}%"
        if prev_state == "ok" and new_state == "warn":
            title = f"Agent {agent_name} disk at {pct_str}"
            msg = f"{used_gib:.1f}/{quota_gib:.0f} GiB — approaching limit"
            level = "warning"
        elif new_state == "hard":
            title = f"Agent {agent_name} disk FULL"
            msg = f"Disk full ({pct_str}) — agent paused until user action"
            level = "error"
        else:
            title = f"Agent {agent_name} disk cleared"
            msg = f"Disk usage back to {pct_str} ({used_gib:.1f}/{quota_gib:.0f} GiB)"
            level = "info"

        try:
            await self._notifications.emit_event("disk_quota", title, msg, level=level)
        except Exception:
            logger.warning("disk_quota: notification emit failed for %s", agent_name, exc_info=True)

    def _find_agent(self, agent_name: str) -> dict | None:
        for a in self._config.agents:
            if a.get("name") == agent_name:
                return a
        return None


# ---------------------------------------------------------------------------
# CLI entry point — python -m tinyagentos.disk_quota scan
# ---------------------------------------------------------------------------

def _default_data_dir():
    """Resolve the data dir for standalone CLI use (no app.state available).

    tinyagentos/disk_quota.py -> parent.parent is the install root, same
    PROJECT_DIR derivation as app.py — works regardless of install location
    (/opt/tinyagentos, /opt/taos, etc) and doesn't depend on $HOME.
    """
    from pathlib import Path

    return Path(__file__).parent.parent / "data"


async def _cli_scan() -> None:
    from tinyagentos.config import load_config, save_config
    from tinyagentos.notifications import NotificationStore

    data_dir = _default_data_dir()

    config = load_config(data_dir / "config.yaml")
    notif_store = NotificationStore(data_dir / "notifications.db")
    await notif_store.init()

    try:
        from tinyagentos.containers.backend import get_backend
        backend = get_backend()
    except Exception:
        from tinyagentos.containers.lxc import LXCBackend
        backend = LXCBackend()

    monitor = DiskQuotaMonitor(config, backend, notif_store)
    results = await monitor.scan_all()

    save_config(config, data_dir / "config.yaml")

    for r in results:
        logger.info(
            "disk_quota: %s  %.2f/%.0f GiB  %s",
            r["name"], r["used_gib"], r["quota_gib"], r["state"],
        )

    await notif_store.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "scan":
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        asyncio.run(_cli_scan())
    else:
        print("Usage: python -m tinyagentos.disk_quota scan")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------

async def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return 1, f"timeout after {timeout}s"
    return proc.returncode, stdout.decode() if stdout else ""
