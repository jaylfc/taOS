from __future__ import annotations

"""HMAC authentication dependency for cluster worker endpoints.

Workers sign every request with a key they obtained from the pairing flow.
The signing string is:

    f"{timestamp}.{METHOD}.{path}.{sha256(raw_body).hexdigest()}"

keyed with the worker's 32-byte signing_key (HMAC-SHA256).

Required headers:
    X-TAOS-Worker-Name   — the worker's registered name
    X-TAOS-Timestamp     — unix seconds (integer string)
    X-TAOS-Signature     — hex-encoded HMAC-SHA256

The body name field must equal the header worker name; a paired worker
cannot register or heartbeat on behalf of a different name.
"""

import hashlib
import hmac
import time

from fastapi import Request
from fastapi.responses import JSONResponse


def _err(code: str, msg: str, status: int) -> JSONResponse:
    return JSONResponse({"error": msg, "code": code}, status_code=status)


async def require_worker_hmac(request: Request) -> None:
    """FastAPI dependency — 401/403 on any auth failure, else None.

    Attach with: ``Depends(require_worker_hmac)``.
    """
    worker_name = request.headers.get("x-taos-worker-name", "").strip()
    timestamp_str = request.headers.get("x-taos-timestamp", "").strip()
    signature = request.headers.get("x-taos-signature", "").strip()

    if not worker_name or not timestamp_str or not signature:
        raise _HMACError(
            _err(
                "worker_not_paired",
                "Worker not paired. Run the worker installer to pair this device with the controller.",
                401,
            )
        )

    # Timestamp skew check
    try:
        ts = int(timestamp_str)
    except ValueError:
        raise _HMACError(
            _err("stale_timestamp", "Invalid timestamp", 401)
        )
    if abs(time.time() - ts) > 300:
        raise _HMACError(
            _err("stale_timestamp", "Timestamp too far from server time", 401)
        )

    # Look up signing key
    pairing_store = getattr(request.app.state, "cluster_pairing", None)
    if pairing_store is None:
        raise _HMACError(
            _err(
                "worker_not_paired",
                "Worker not paired. Run the worker installer to pair this device with the controller.",
                401,
            )
        )
    signing_key = await pairing_store.get_signing_key(worker_name)
    if signing_key is None:
        raise _HMACError(
            _err(
                "worker_not_paired",
                "Worker not paired. Run the worker installer to pair this device with the controller.",
                401,
            )
        )

    # Verify signature
    raw_body = await request.body()
    body_hash = hashlib.sha256(raw_body).hexdigest()
    method = request.method.upper()
    path = request.url.path
    message = f"{timestamp_str}.{method}.{path}.{body_hash}".encode()
    expected = hmac.new(signing_key, message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise _HMACError(
            _err("bad_signature", "Signature verification failed", 401)
        )

    # Store for route-level body/header name cross-check
    request.state.hmac_worker_name = worker_name


class _HMACError(Exception):
    """Wraps a JSONResponse so the route can return it directly."""

    def __init__(self, response: JSONResponse):
        self.response = response
        super().__init__()
