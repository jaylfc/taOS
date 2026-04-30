"""Enroll the local taOS controller as a cluster worker.

Called during app lifespan to register a 'local' WorkerInfo in the
ClusterManager with a fresh random signing key.  The signing key is
in-memory only (regenerated on restart); persisting it to disk is
deferred to a later task.
"""
from __future__ import annotations

import os

from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.worker_protocol import WorkerInfo


async def enroll_local_worker(manager: ClusterManager, bind_port: int = 6969) -> None:
    """Register the 'local' worker in *manager* with a random 32-byte signing key."""
    signing_key = os.urandom(32)
    worker = WorkerInfo(
        name="local",
        url=f"http://127.0.0.1:{bind_port}",
        worker_url=f"http://127.0.0.1:{bind_port}",
        signing_key=signing_key,
        platform="local",
    )
    await manager.register_worker(worker)
