"""Tests for issue #3228: GPU resource collection and lease claiming.

Before the fix, ``_collect_resources()`` only emitted a GPU resource when a
live backend of type vllm/ollama/exo/mlx was detected — and then hardcoded
``"gpu-cuda-0"``.  A llama.cpp-only NVIDIA worker and any AMD/ROCm worker
therefore advertised only ``cpu-inference``, making their GPU un-leasable via
``POST /api/cluster/leases/claim``.

After the fix, the GPU resource class is derived from the hardware
``gpu_type`` directly (``nvidia``/``cuda`` -> ``gpu-cuda-0``, ``amd``/``rocm``
-> ``gpu-rocm-0``, ``apple`` -> ``gpu-metal``), independent of whether a
GPU-capable backend happens to be live at registration time.
"""
from __future__ import annotations

import json

import pytest

from tinyagentos.worker.agent import _collect_resources


# ---------------------------------------------------------------------------
# _collect_resources unit tests (taOS #3228)
# ---------------------------------------------------------------------------

def test_llama_cpp_only_nvidia_reports_gpu_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    """A llama.cpp-only NVIDIA worker must advertise gpu-cuda-0.

    Before the fix the GPU resource was gated on a live backend of type
    vllm/ollama/exo/mlx; llama.cpp was never in that set, so a
    llama.cpp-only NVIDIA box reported only ``cpu-inference`` and its GPU
    could never be leased (#3228).
    """
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    resources = _collect_resources([], "nvidia")
    assert "gpu-cuda-0" in resources
    assert "gpu-rocm-0" not in resources
    assert "gpu-metal" not in resources
    assert resources[0] == "cpu-inference"


def test_rocm_worker_reports_gpu_rocm(monkeypatch: pytest.MonkeyPatch) -> None:
    """An AMD/ROCm worker must advertise gpu-rocm-0, not gpu-cuda-0."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    resources = _collect_resources([], "amd")
    assert "gpu-rocm-0" in resources
    assert "gpu-cuda-0" not in resources
    assert "gpu-metal" not in resources
    assert resources[0] == "cpu-inference"


def test_nvidia_via_cuda_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """``gpu_type`` may be ``cuda`` (alias) instead of ``nvidia``."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    assert _collect_resources([], "cuda") == ["cpu-inference", "gpu-cuda-0"]


def test_amd_via_rocm_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """``gpu_type`` may be ``rocm`` (alias) instead of ``amd``."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    assert _collect_resources([], "rocm") == ["cpu-inference", "gpu-rocm-0"]


def test_no_gpu_hardware_no_gpu_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GPU-less worker must not advertise any gpu-* resource, even when
    a GPU-capable backend is not running (#3228 regression guard)."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    assert _collect_resources([{"type": "ollama"}], "none") == ["cpu-inference"]
    assert _collect_resources([{"type": "vllm"}], None) == ["cpu-inference"]


def test_ollama_on_nvidia_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing ollama/vllm workers on NVIDIA must still report gpu-cuda-0."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    assert _collect_resources([{"type": "ollama"}], "nvidia") == [
        "cpu-inference", "gpu-cuda-0",
    ]
    assert _collect_resources([{"type": "vllm"}], "nvidia") == [
        "cpu-inference", "gpu-cuda-0",
    ]


def test_apple_silicon_still_gpu_metal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apple Silicon workers still report gpu-metal."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    assert _collect_resources([{"type": "mlx"}], "apple") == [
        "cpu-inference", "gpu-metal",
    ]


def test_intel_mac_no_gpu_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    """An Intel Mac with no Apple GPU must not advertise any gpu-* class."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    assert _collect_resources([{"type": "ollama"}], "", "darwin") == [
        "cpu-inference",
    ]


# ---------------------------------------------------------------------------
# Lease claim integration tests (taOS #3228)
# ---------------------------------------------------------------------------

from test_routes_cluster_pairing import pair_worker, sign_worker_request  # noqa: E402


async def _register_worker(
    client,
    app,
    name,
    url,
    capabilities=None,
    hardware=None,
    resources=None,
):
    """Pair and register a worker, optionally carrying a resource inventory."""
    await app.state.cluster_pairing.init()
    key = await pair_worker(client, app, name, url)
    body: dict = {
        "name": name,
        "url": url,
        "capabilities": capabilities or [],
    }
    if hardware is not None:
        body["hardware"] = hardware
    if resources is not None:
        body["resources"] = resources
    body_bytes = json.dumps(body).encode()
    headers = sign_worker_request(key, name, "POST", "/api/cluster/workers", body_bytes)
    headers["content-type"] = "application/json"
    resp = await client.post("/api/cluster/workers", content=body_bytes, headers=headers)
    assert resp.status_code == 200, resp.text
    return key


async def _heartbeat(client, key, name, **fields):
    """Send a signed heartbeat carrying the given extra fields."""
    body = {"name": name, **fields}
    body_bytes = json.dumps(body).encode()
    headers = sign_worker_request(key, name, "POST", "/api/cluster/heartbeat", body_bytes)
    headers["content-type"] = "application/json"
    resp = await client.post("/api/cluster/heartbeat", content=body_bytes, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp


def _auth(app):
    return {"Authorization": f"Bearer {app.state.auth.get_local_token()}"}


@pytest.mark.asyncio
async def test_lease_claim_cuda_nvidia_worker(client, app):
    """A llama.cpp-only NVIDIA worker that advertises gpu-cuda-0 via its
    resource inventory can have that GPU leased with required_vram_mb."""
    key = await _register_worker(
        client, app, "llama-nvidia", "http://10.0.0.1:9000",
        capabilities=["llm-chat"],
        hardware={"gpu": {"type": "nvidia", "model": "RTX 4090", "vram_mb": 24576, "cuda": True}},
        resources=["cpu-inference", "gpu-cuda-0"],
    )
    await _heartbeat(client, key, "llama-nvidia",
                     free_vram_mb=20000, used_vram_mb=4000)

    resp = await client.post(
        "/api/cluster/leases/claim",
        headers=_auth(app),
        json={
            "resource_id": "llama-nvidia:gpu-cuda-0",
            "caller": "skald-dispatcher",
            "ttl_seconds": 30,
            "required_vram_mb": 4000,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "claimed"
    assert data["resource_id"] == "llama-nvidia:gpu-cuda-0"
    assert data["required_vram_mb"] == 4000


@pytest.mark.asyncio
async def test_lease_claim_rocm_worker(client, app):
    """A ROCm worker that advertises gpu-rocm-0 can have that GPU leased."""
    key = await _register_worker(
        client, app, "rocm-node", "http://10.0.0.2:9000",
        capabilities=["llm-chat"],
        hardware={"gpu": {"type": "amd", "model": "AMD Radeon RX 7900 XTX", "vram_mb": 24576, "rocm": True}},
        resources=["cpu-inference", "gpu-rocm-0"],
    )
    await _heartbeat(client, key, "rocm-node",
                     free_vram_mb=20000, used_vram_mb=4000)

    resp = await client.post(
        "/api/cluster/leases/claim",
        headers=_auth(app),
        json={
            "resource_id": "rocm-node:gpu-rocm-0",
            "caller": "skald-dispatcher",
            "ttl_seconds": 30,
            "required_vram_mb": 4000,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "claimed"
    assert data["resource_id"] == "rocm-node:gpu-rocm-0"
    assert data["required_vram_mb"] == 4000


@pytest.mark.asyncio
async def test_lease_claim_cuda_insufficient_vram(client, app):
    """Lease claim against a CUDA worker with insufficient VRAM is refused."""
    key = await _register_worker(
        client, app, "cuda-tight", "http://10.0.0.3:9000",
        capabilities=["llm-chat"],
        hardware={"gpu": {"type": "nvidia", "model": "RTX 4060", "vram_mb": 8192, "cuda": True}},
        resources=["cpu-inference", "gpu-cuda-0"],
    )
    await _heartbeat(client, key, "cuda-tight",
                     free_vram_mb=1000, used_vram_mb=7000)

    resp = await client.post(
        "/api/cluster/leases/claim",
        headers=_auth(app),
        json={
            "resource_id": "cuda-tight:gpu-cuda-0",
            "required_vram_mb": 8000,
        },
    )
    assert resp.status_code == 409
    assert "unavailable" in resp.json()["error"]
