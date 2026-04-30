# tinyagentos/cluster/worker_registry.py
"""Bridge between the route-side `get_local_worker()` API and the cluster manager.

After Task 23 lands enroll_local_worker, this module looks up "local" in
ClusterManager and returns the WorkerInfo as a dict. If no manager exists
(e.g., during unit tests that don't start the app), falls back to the
stub key for compatibility.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

from tinyagentos.cluster.manager import ClusterManager

# Stub key used as fallback when no ClusterManager has enrolled a local worker
# (mostly for unit tests that don't invoke the app lifespan).
_FALLBACK_SIGNING_KEY: bytes = hashlib.sha256(
    b"taos-local-worker-default-signing-key-v1"
).digest()
_FALLBACK_WORKER_URL: str = "http://127.0.0.1:6969"

# Module-level reference to the active ClusterManager. Set by app startup.
_active_manager: Optional[ClusterManager] = None


def set_active_manager(manager: ClusterManager) -> None:
    """Called by app startup to register the active ClusterManager."""
    global _active_manager
    _active_manager = manager


def get_local_worker() -> dict[str, Any]:
    """Return the local worker config as a dict.

    Reads from the active ClusterManager if available; falls back to the stub.
    """
    if _active_manager is not None:
        worker = _active_manager.get_worker("local")
        if worker is not None:
            return {
                "worker_url": worker.worker_url or _FALLBACK_WORKER_URL,
                "signing_key": worker.signing_key or _FALLBACK_SIGNING_KEY,
                "name": worker.name,
            }
    return {
        "worker_url": _FALLBACK_WORKER_URL,
        "signing_key": _FALLBACK_SIGNING_KEY,
        "name": "local",
    }
