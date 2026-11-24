"""Tests for lazy_backend_wiring — registry-driven LazyBackendProxy setup (#63).

The proxy itself is covered by tests/test_lazy_backend_proxy.py. These tests
cover the integration PR #474 left out: building proxies from backend config
entries that opt in with a ``lazy`` block, TTL sourced from the entry's
registry ``keep_alive_minutes``, manifest -> config plumbing, and an
end-to-end cold start through the public port.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import time

import httpx
import pytest

from tinyagentos.lazy_backend_wiring import (
    PROJECT_DIR,
    build_lazy_proxies,
    probe_target,
    start_lazy_proxies,
    stop_lazy_proxies,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _lazy_entry(**overrides) -> dict:
    public = _free_port()
    private = _free_port()
    entry = {
        "name": "local-stable-diffusion-cpp",
        "type": "sd-cpp",
        "url": f"http://127.0.0.1:{public}",
        "priority": 99,
        "enabled": True,
        "auto_manage": False,
        "keep_alive_minutes": 5,
        "lazy": {
            "start_cmd": f"{sys.executable} -m http.server {private} --bind 127.0.0.1",
            "backend_url": f"http://127.0.0.1:{private}",
            "health_url": f"http://127.0.0.1:{private}/",
        },
    }
    entry.update(overrides)
    return entry


# -- construction -------------------------------------------------------------


def test_build_creates_proxy_with_registry_ttl():
    """keep_alive_minutes is the registry TTL: 5 minutes -> 300s idle timeout."""
    proxies = build_lazy_proxies([_lazy_entry()])
    assert list(proxies) == ["local-stable-diffusion-cpp"]
    proxy = proxies["local-stable-diffusion-cpp"]
    assert proxy._idle_timeout == 300.0
    assert proxy.url.startswith("http://127.0.0.1:")


def test_build_keep_alive_zero_means_never_evict():
    """0 keeps the codebase-wide 'always-on' meaning: idle timeout disabled."""
    proxies = build_lazy_proxies([_lazy_entry(keep_alive_minutes=0)])
    assert proxies["local-stable-diffusion-cpp"]._idle_timeout == 0.0


def test_build_supports_llama_cpp_entry():
    """The mechanism is type-agnostic: a llama-cpp entry with a lazy block
    (the large-model fallback shape from the issue) gets a proxy too."""
    entry = _lazy_entry(name="local-llama-fallback", type="llama-cpp")
    proxies = build_lazy_proxies([entry])
    assert "local-llama-fallback" in proxies


def test_build_skips_entries_without_lazy_block():
    entry = _lazy_entry()
    del entry["lazy"]
    assert build_lazy_proxies([entry]) == {}


def test_build_skips_disabled_entries():
    assert build_lazy_proxies([_lazy_entry(enabled=False)]) == {}


@pytest.mark.parametrize(
    "lazy",
    [
        {},  # empty block
        {"backend_url": "http://127.0.0.1:9999"},  # no start_cmd
        {"start_cmd": "true"},  # no backend_url
    ],
)
def test_build_skips_malformed_lazy_blocks(lazy):
    entry = _lazy_entry(lazy=lazy)
    assert build_lazy_proxies([entry]) == {}


def test_build_skips_when_public_and_backend_ports_collide():
    entry = _lazy_entry()
    entry["lazy"]["backend_url"] = entry["url"]  # same port -> proxy would loop
    assert build_lazy_proxies([entry]) == {}


def test_project_dir_placeholder_expands_in_start_cmd():
    entry = _lazy_entry()
    entry["lazy"]["start_cmd"] = "{project_dir}/services/stable-diffusion-cpp/run.sh"
    proxy = build_lazy_proxies([entry])["local-stable-diffusion-cpp"]
    assert "{project_dir}" not in proxy._start_cmd
    assert proxy._start_cmd == f"{PROJECT_DIR}/services/stable-diffusion-cpp/run.sh"


# -- probe targeting ------------------------------------------------------------


def test_probe_target_points_at_real_server_for_lazy_entry():
    """Catalog probes must not go through the proxy (they would cold-start
    the backend and keep it warm forever); they probe the real server."""
    entry = _lazy_entry()
    target = probe_target(entry)
    assert target["url"] == entry["lazy"]["backend_url"]
    assert target["name"] == entry["name"]


def test_probe_target_unchanged_without_lazy_block():
    entry = _lazy_entry()
    del entry["lazy"]
    assert probe_target(entry)["url"] == entry["url"]


# -- end to end -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_through_public_port_cold_starts_backend():
    """First request to the entry's public URL starts the real server via
    the lazy start_cmd; the proxy forwards to it."""
    entry = _lazy_entry()
    proxies = build_lazy_proxies([entry])
    await start_lazy_proxies(proxies)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.get(entry["url"] + "/")
        assert resp.status_code == 200
    finally:
        await stop_lazy_proxies(proxies)


@pytest.mark.asyncio
async def test_idle_backend_is_stopped_after_registry_ttl():
    """With a tiny registry TTL the subprocess is evicted once idle, and the
    private port stops answering."""
    entry = _lazy_entry(keep_alive_minutes=0.02)  # 1.2s, test-scale TTL
    backend_url = entry["lazy"]["backend_url"]
    proxies = build_lazy_proxies([entry])
    proxy = proxies["local-stable-diffusion-cpp"]
    await start_lazy_proxies(proxies)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.get(entry["url"] + "/")
            assert resp.status_code == 200
            assert proxy._proc is not None and proxy._proc.poll() is None
            # Let the idle TTL lapse with no further requests.
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if proxy._proc is None:
                    break
                await asyncio.sleep(0.2)
            assert proxy._proc is None, "backend not evicted after idle TTL"
            with pytest.raises(httpx.TransportError):
                await client.get(backend_url + "/", timeout=2.0)
    finally:
        await stop_lazy_proxies(proxies)
