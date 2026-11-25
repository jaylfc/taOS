"""Registry wiring for the lazy lifecycle wrappers (issue #63).

``LazyBackendProxy`` (tinyagentos/lazy_backend_proxy.py, PR #474) is the
generic wrapper: it listens on a user-facing port, starts the real backend
subprocess on the first inbound connection, forwards traffic, and stops the
subprocess after an idle TTL — for backends that load their model at process
start and have no idle eviction of their own (sd-server, llama-server,
whisper.cpp server). PR #474 deliberately shipped the proxy unwired; this
module is the integration. It builds one proxy per backend config entry that
opts in with a ``lazy`` mapping, so a backend is wrapped by configuration
instead of by per-backend code.

Config shape (produced from a manifest ``lifecycle.lazy`` block by
``auto_register_from_manifest``, or written directly into config.backends):

    {
        "name": "local-stable-diffusion-cpp",
        "type": "sd-cpp",
        "url": "http://127.0.0.1:30450",        # user-facing (the proxy)
        "keep_alive_minutes": 5,                # the registry TTL
        "lazy": {
            "start_cmd": "env TAOS_SD_CPP_PORT=30451 {project_dir}/services/stable-diffusion-cpp/run.sh",
            "backend_url": "http://127.0.0.1:30451",   # the real server
            "health_url": "http://127.0.0.1:30451/sdapi/v1/options",  # optional
            "stop_cmd": "",                             # optional
        },
    }

TTL semantics follow the rest of the registry: the idle timeout is the
entry's ``keep_alive_minutes`` x 60; 0 means never evict (always-on), the
same meaning LifecycleManager gives it.

``{project_dir}`` in start/stop commands expands to the taOS project root
(the parent of the ``tinyagentos`` package — the same PROJECT_DIR app.py
uses), so manifests can name installer-produced run scripts portably.

The proxy binds 127.0.0.1, so a wrapped backend becomes loopback-only even
if the raw server would bind 0.0.0.0. Every in-tree consumer (routes,
gateway) is loopback, and not re-exposing an unauthenticated inference
server on the LAN is the safer default.

Cold-start latency is logged by the proxy itself ("healthy in %.2fs"),
which is the log line the Activity widget surfaces.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from tinyagentos.lazy_backend_proxy import LazyBackendProxy

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent


def _expand_project_dir(cmd: str) -> str:
    return cmd.replace("{project_dir}", str(PROJECT_DIR))


def _port_of(url: str) -> int | None:
    try:
        return urlparse(url).port
    except ValueError:
        return None


def _host_of(url: str) -> str:
    return urlparse(url).hostname or "127.0.0.1"


def build_lazy_proxies(backends: list[dict]) -> dict[str, LazyBackendProxy]:
    """Construct (but do not start) a proxy per opted-in backend entry.

    Malformed entries are logged and skipped — one bad lazy block must
    never take down app startup (same posture as auto-register).
    """
    proxies: dict[str, LazyBackendProxy] = {}
    for backend in backends:
        if not backend.get("enabled", True):
            continue
        lazy = backend.get("lazy")
        if not isinstance(lazy, dict):
            continue
        name = str(backend.get("name") or "").strip()
        if not name:
            logger.warning("lazy wiring: skipping lazy block on a nameless backend entry")
            continue
        proxy = _build_one(name, backend, lazy)
        if proxy is not None:
            proxies[name] = proxy
    return proxies


def _build_one(name: str, backend: dict, lazy: dict) -> LazyBackendProxy | None:
    start_cmd = str(lazy.get("start_cmd") or "").strip()
    backend_url = str(lazy.get("backend_url") or "").strip()
    public_url = str(backend.get("url") or "").strip()
    if not start_cmd or not backend_url or not public_url:
        logger.warning(
            "lazy wiring: %s skipped — a lazy block needs start_cmd, "
            "backend_url and a public url on the entry",
            name,
        )
        return None
    proxy_port = _port_of(public_url)
    backend_port = _port_of(backend_url)
    if proxy_port is None or backend_port is None:
        logger.warning(
            "lazy wiring: %s skipped — could not parse ports from %r / %r",
            name, public_url, backend_url,
        )
        return None
    if proxy_port == backend_port:
        logger.warning(
            "lazy wiring: %s skipped — public and backend ports are both "
            "%d; the proxy would forward to itself",
            name, proxy_port,
        )
        return None
    keep_alive_minutes = backend.get("keep_alive_minutes", 10)
    try:
        idle_timeout = float(keep_alive_minutes) * 60.0
    except (TypeError, ValueError):
        logger.warning(
            "lazy wiring: %s skipped — keep_alive_minutes %r is not a number",
            name, keep_alive_minutes,
        )
        return None
    if not idle_timeout >= 0:  # negative or NaN
        logger.warning(
            "lazy wiring: %s skipped — keep_alive_minutes %r does not "
            "give a usable idle timeout",
            name, keep_alive_minutes,
        )
        return None
    return LazyBackendProxy(
        proxy_port=proxy_port,
        backend_host=_host_of(backend_url),
        backend_port=backend_port,
        start_cmd=_expand_project_dir(start_cmd),
        stop_cmd=_expand_project_dir(str(lazy.get("stop_cmd") or "")),
        idle_timeout_seconds=idle_timeout,
        health_url=str(lazy.get("health_url") or "") or None,
    )


def probe_target(backend: dict) -> dict:
    """Return the backend dict the catalog should health-probe.

    For a lazy entry the public URL is the proxy: probing it would
    cold-start the backend on the first poll and keep it warm forever,
    defeating the wrapper. Probe the real server instead — a stopped
    lazy backend then reads in the catalog exactly like a stopped
    auto-managed one (status error, no models), and a warm one reads ok.
    """
    lazy = backend.get("lazy")
    if isinstance(lazy, dict) and lazy.get("backend_url"):
        return {**backend, "url": str(lazy["backend_url"])}
    return backend


async def start_lazy_proxies(proxies: dict[str, LazyBackendProxy]) -> None:
    """Start listening on every proxy. Never raises: a proxy that cannot
    bind (e.g. port already taken by the raw service) is logged, and the
    app — and the other proxies — carry on."""
    for name, proxy in proxies.items():
        try:
            await proxy.start()
        except Exception:
            logger.exception("lazy wiring: proxy for %s failed to start", name)


async def stop_lazy_proxies(proxies: dict[str, LazyBackendProxy]) -> None:
    """Stop every proxy and its backend subprocess. Never raises."""
    for name, proxy in proxies.items():
        try:
            await proxy.stop()
        except Exception:
            logger.exception("lazy wiring: proxy for %s failed to stop", name)
