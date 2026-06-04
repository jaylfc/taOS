"""Enroll the local taOS controller as a cluster worker.

Called during app lifespan. Idempotent: calling twice on the same manager keeps
the same signing key (the worker registration is left untouched on subsequent
calls). The signing key is in-memory only (regenerated on restart).
"""
from __future__ import annotations

import asyncio
import logging
import os

from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.worker_protocol import WorkerInfo

logger = logging.getLogger(__name__)

# Module-level. Survives across calls within the same process. Reset on restart.
_LOCAL_SIGNING_KEY: bytes | None = None


def _cpu_load() -> float:
    """Best-effort 0-1 CPU utilisation for the local worker's load field."""
    try:
        import psutil
        return min(1.0, max(0.0, psutil.cpu_percent(interval=None) / 100.0))
    except Exception:
        return 0.0


async def local_heartbeat_loop(
    manager: ClusterManager,
    config,
    interval: float = 15.0,
    backends_provider=None,
) -> None:
    """Self-heartbeat the 'local' worker (the controller itself).

    The controller never receives heartbeats from elsewhere — it IS the
    server — so without this it would be marked offline after the heartbeat
    timeout and its backends/loaded-models would never refresh. Heartbeating
    here keeps it online and, by passing the live backends, keeps its backend
    + derived model list current the same way a remote worker's agent does.

    ``backends_provider`` is an optional zero-arg callable returning the live
    backend dicts (``{name, type, url, models, ...}``). Prefer it over
    ``config.backends``: the configured list carries no live model data, while
    the live catalog actually knows which models are loaded right now. Falls
    back to ``config.backends`` when no provider is given or it raises.
    """
    # psutil.cpu_percent(interval=None) returns 0.0 on its first call (no prior
    # sample to diff against). Prime it once so the first heartbeat carries a
    # real reading rather than a meaningless zero.
    _cpu_load()
    while True:
        backends = list(getattr(config, "backends", None) or [])
        if backends_provider is not None:
            try:
                backends = list(backends_provider() or [])
            except Exception:
                logger.exception("local heartbeat: backends_provider failed; using config")
        try:
            manager.heartbeat(
                "local",
                load=_cpu_load(),
                backends=backends,
            )
        except Exception:
            logger.exception("local heartbeat failed")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return


async def enroll_local_worker(
    manager: ClusterManager,
    bind_port: int = 6969,
    hardware: dict | None = None,
    backends: list[dict] | None = None,
) -> None:
    """Register the 'local' worker (the controller itself) in *manager*. Idempotent.

    On first call, generates a 32-byte random signing key and registers the
    worker with the controller's own hardware + backends so the Cluster view
    shows the host's real CPU/RAM/NPU and loaded backends rather than
    "Unknown CPU / CPU only / No backends loaded". On subsequent calls (same
    process, same manager) the existing worker is left untouched.
    """
    global _LOCAL_SIGNING_KEY

    if manager.get_worker("local") is not None:
        return  # already enrolled, leave it alone

    if _LOCAL_SIGNING_KEY is None:
        _LOCAL_SIGNING_KEY = os.urandom(32)

    worker = WorkerInfo(
        name="local",
        url=f"http://127.0.0.1:{bind_port}",
        worker_url=f"http://127.0.0.1:{bind_port}",
        signing_key=_LOCAL_SIGNING_KEY,
        platform="local",
        hardware=hardware or {},
        backends=list(backends or []),
    )
    await manager.register_worker(worker)
