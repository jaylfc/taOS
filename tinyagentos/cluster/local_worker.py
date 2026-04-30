"""Enroll the local taOS controller as a cluster worker.

Called during app lifespan. Idempotent: calling twice on the same manager keeps
the same signing key (the worker registration is left untouched on subsequent
calls). The signing key is in-memory only (regenerated on restart).
"""
from __future__ import annotations

import os

from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.worker_protocol import WorkerInfo

# Module-level. Survives across calls within the same process. Reset on restart.
_LOCAL_SIGNING_KEY: bytes | None = None


async def enroll_local_worker(manager: ClusterManager, bind_port: int = 6969) -> None:
    """Register the 'local' worker in *manager*. Idempotent.

    On first call, generates a 32-byte random signing key and registers the
    worker. On subsequent calls (same process, same manager) the existing
    worker is left untouched.
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
    )
    await manager.register_worker(worker)
