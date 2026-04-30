# tinyagentos/cluster/worker_registry.py
"""Bridge from the route-side `get_local_worker()` API to the ClusterManager.

Tasks 22-23 register the local worker via enroll_local_worker(); this module
just exposes that registration to non-async callers (route handlers).
"""
from __future__ import annotations

from typing import Any, Optional

from tinyagentos.cluster.manager import ClusterManager

_active_manager: Optional[ClusterManager] = None


def set_active_manager(manager: ClusterManager) -> None:
    """Called by app startup to register the active ClusterManager."""
    global _active_manager
    _active_manager = manager


def get_local_worker() -> dict[str, Any]:
    """Return the local worker config as a dict.

    Raises RuntimeError if no manager has been registered or if the local
    worker has not been enrolled — fail closed to prevent forging tickets
    with a fallback key.
    """
    if _active_manager is None:
        raise RuntimeError(
            "No active ClusterManager — local worker not enrolled. "
            "Tests must call set_active_manager(test_manager); production "
            "calls it from app startup after enroll_local_worker()."
        )
    worker = _active_manager.get_worker("local")
    if worker is None:
        raise RuntimeError("Local worker not registered with ClusterManager")
    return {
        "worker_url": worker.worker_url,
        "signing_key": worker.signing_key,
        "name": worker.name,
    }
