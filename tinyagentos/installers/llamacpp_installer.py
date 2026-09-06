"""llama.cpp server (router mode) — the taOS default local LLM backend.

Generalizes the Rockchip-only NPU one-tap install (#1535/#1597/#1598) to
every other platform: llama.cpp (MIT) built-in "router mode" gives chat,
``/v1/embeddings`` and ``/v1/rerank`` from one process, so it's the single
default local backend on NVIDIA CUDA, AMD ROCm, Apple Silicon (Metal) and
x86 CPU-only. Rockchip NPU boards are unaffected — they keep rkllama /
rk-llama.cpp exactly as-is (see rkllama_installer.py / rkllamacpp_installer.py).

The actual install (binary download + systemd unit / launchd plist +
health-gate) lives in ``scripts/install-llama-cpp.sh``, run via the same
Store ScriptInstaller path every other script-backed service manifest
uses. This module only holds the runtime constant (default port) and the
cheap local-only health probe used by ``routes/setup.py`` and by
``config.py``'s auto-register gate — mirrors ``rkllamacpp_is_running()``.
"""
from __future__ import annotations

import os
import socket
import urllib.request

# taOS default port for the generic (non-Rockchip) llama.cpp router-mode
# server — see tinyagentos/installers/port_allocator.py RESERVED_PORTS and
# app-catalog/services/llama-cpp/manifest.yaml requires.ports. Distinct from
# 7834 (LiteLLM proxy) and 7833 (rkllama).
DEFAULT_PORT = 7835


def _default_port() -> int:
    """Resolve port from TAOS_LLAMACPP_PORT or fallback to DEFAULT_PORT."""
    raw = os.environ.get("TAOS_LLAMACPP_PORT", str(DEFAULT_PORT))
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_PORT


def llamacpp_is_running(timeout: float = 1.0) -> bool:
    """True if a live llama.cpp router-mode server answers /health locally.

    Verified against a real llama-server (b9867) build: router mode
    (``--models-dir``) answers ``GET /health`` with ``{"status":"ok"}``
    even when the models directory is empty — no model needs to be
    installed for this probe (or the setup checklist's completion state)
    to go green once the server itself is up.

    Callers should run this off the event loop (``asyncio.to_thread``)
    since it blocks on socket I/O.
    """
    port = _default_port()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            pass
    except OSError:
        return False
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False
