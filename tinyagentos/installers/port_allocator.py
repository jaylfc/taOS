"""Host-port allocation for installed apps.

Apps installed via Docker or LXC need a host port to be reachable.
Ports in the well-known / core / taOS-reserved range must never be
assigned to user-installed apps — they collide with system services.

Allocation is deterministic per app_id (stable across restarts) by
hashing the id into the high pool; if the deterministic slot is already
taken we walk forward until we find a free, non-reserved port.
"""
from __future__ import annotations

import hashlib
import socket
from contextlib import closing

# Ports that must never be assigned to an app host-port.
# Includes IANA well-known ports that commonly run on dev machines,
# core taOS services, and taosmd bus.
RESERVED_PORTS: frozenset[int] = frozenset({
    # Well-known / OS
    22,    # SSH
    25,    # SMTP
    53,    # DNS
    80,    # HTTP
    110,   # POP3
    143,   # IMAP
    443,   # HTTPS
    465,   # SMTPS
    587,   # SMTP submission
    993,   # IMAPS
    995,   # POP3S
    # Common dev / framework defaults that clash
    1080,  # SOCKS
    3000,  # Node / React dev / Grafana
    3306,  # MySQL
    4000,  # LiteLLM (taOS internal)
    5000,  # Flask / generic dev
    5173,  # Vite dev
    5432,  # PostgreSQL
    6379,  # Redis
    7900,  # taosmd A2A bus
    8000,  # Django / generic dev
    8080,  # Tomcat / common web app default
    8443,  # HTTPS alt
    8888,  # Jupyter
    9000,  # MinIO / SonarQube
    9090,  # Prometheus
    27017, # MongoDB
    # taOS core
    6969,  # taOS controller
})

# High pool used for all app host-port assignments.
_POOL_START = 30_000
_POOL_END   = 40_000


def _is_port_free(port: int) -> bool:
    """Return True if no process is currently bound to *port* on 0.0.0.0."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def allocate_host_port(app_id: str, *, exclude: set[int] | frozenset[int] = frozenset()) -> int:
    """Return a stable, non-reserved, free host port for *app_id*.

    The starting point is derived from a hash of *app_id* so the same
    app always gets the same port on a fresh install (assuming the slot
    is available).  If the preferred slot is occupied or reserved we walk
    forward (wrapping within the pool) until we find a usable port.

    ``exclude`` holds ports already claimed by the caller in this
    allocation round but not yet bound (e.g. earlier ports of a
    multi-port app), so consecutive calls cannot hand out the same port.

    Raises ``RuntimeError`` if the entire pool is exhausted.
    """
    pool_size = _POOL_END - _POOL_START
    # Deterministic starting offset from the app_id.
    digest = int(hashlib.sha256(app_id.encode()).hexdigest(), 16)
    start_offset = digest % pool_size

    for i in range(pool_size):
        candidate = _POOL_START + (start_offset + i) % pool_size
        if candidate in RESERVED_PORTS or candidate in exclude:
            continue
        if _is_port_free(candidate):
            return candidate

    raise RuntimeError(
        f"Port allocator exhausted the full pool {_POOL_START}-{_POOL_END} "
        f"for app '{app_id}' — no free non-reserved port found."
    )
