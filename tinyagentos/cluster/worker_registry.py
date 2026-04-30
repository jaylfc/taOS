# tinyagentos/cluster/worker_registry.py
"""Worker registry — full implementation lands in Tasks 22-23.

For now, exposes get_local_worker() returning a stub for the local single-Pi case.
Tasks 22-23 will extend this to handle remote workers, signing keys, and tls_cert_provider.
"""
from __future__ import annotations

import hashlib
from typing import Any

# Local worker config; signing key is a deterministic fixed value for now.
# Tasks 22-23 replace this with proper key generation persisted to disk.
_LOCAL_SIGNING_KEY: bytes = hashlib.sha256(b"taos-local-worker-default-signing-key-v1").digest()
_LOCAL_WORKER_URL: str = "http://127.0.0.1:6969"


def get_local_worker() -> dict[str, Any]:
    """Return the local worker's config: worker_url and signing_key."""
    return {
        "worker_url": _LOCAL_WORKER_URL,
        "signing_key": _LOCAL_SIGNING_KEY,
        "name": "local",
    }
