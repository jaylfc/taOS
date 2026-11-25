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


def _hw(gpu_type: str | None) -> dict | None:
    """Build a minimal hardware dict for _collect_resources tests."""
    if gpu_type is None:
        return None
    flags = {}
    if gpu_type in ("nvidia", "cuda"):
        flags = {"cuda": True}
    elif gpu_type in ("amd", "rocm"):
        flags = {"rocm": True}
    return {"gpu": {"type": gpu_type, **flags}}


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
    resources = _collect_resources([], _hw("nvidia"))
    assert "gpu-cuda-0" in resources
    assert "gpu-rocm-0" not in resources
    assert "gpu-metal" not in resources
    assert resources[0] == "cpu-inference"


def test_rocm_worker_reports_gpu_rocm(monkeypatch: pytest.MonkeyPatch) -> None:
    """An AMD/ROCm worker must advertise gpu-rocm-0, not gpu-cuda-0."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    resources = _collect_resources([], _hw("amd"))
    assert "gpu-rocm-0" in resources
    assert "gpu-cuda-0" not in resources
    assert "gpu-metal" not in resources
    assert resources[0] == "cpu-inference"


def test_nvidia_via_cuda_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """``gpu_type`` may be ``cuda`` (alias) instead of ``nvidia``."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    assert _collect_resources([], _hw("cuda")) == ["cpu-inference", "gpu-cuda-0"]


def test_amd_via_rocm_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """``gpu_type`` may be ``rocm`` (alias) instead of ``amd``."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    assert _collect_resources([], _hw("rocm")) == ["cpu-inference", "gpu-rocm-0"]


def test_no_gpu_hardware_no_gpu_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GPU-less worker must not advertise any gpu-* resource, even when
    a GPU-capable backend is not running (#3228 regression guard)."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    assert _collect_resources([{"type": "ollama"}], _hw("none")) == ["cpu-inference"]
    assert _collect_resources([{"type": "vllm"}], None) == ["cpu-inference"]


def test_ollama_on_nvidia_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing ollama/vllm workers on NVIDIA must still report gpu-cuda-0."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    assert _collect_resources([{"type": "ollama"}], _hw("nvidia")) == [
        "cpu-inference", "gpu-cuda-0",
    ]
    assert _collect_resources([{"type": "vllm"}], _hw("nvidia")) == [
        "cpu-inference", "gpu-cuda-0",
    ]


def test_apple_silicon_still_gpu_metal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apple Silicon workers still report gpu-metal."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    assert _collect_resources([{"type": "mlx"}], _hw("apple")) == [
        "cpu-inference", "gpu-metal",
    ]


def test_intel_mac_no_gpu_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    """An Intel Mac with no Apple GPU must not advertise any gpu-* class."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    assert _collect_resources([{"type": "ollama"}], _hw(""), "darwin") == [
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

# ---------------------------------------------------------------------------
# Multi-GPU enumeration (taOS #3228 follow-up)
#
# detect_hardware() enumerates every GPU into ``hardware["gpus"]`` (PCI
# order), but _collect_resources() only ever read the singular
# ``hardware["gpu"]`` -- a two-GPU worker still advertised a single
# leasable resource. The design doc's resource table
# (docs/design/resource-scheduler.md) specifies ``gpu-cuda-N`` /
# ``gpu-rocm-N`` at 1 per GPU; these tests pin the enumeration.
# ---------------------------------------------------------------------------


def _hw_multi(gpus: list[dict]) -> dict:
    """Hardware dict carrying an enumerated ``gpus`` list (PCI order).

    ``gpu`` mirrors ``gpus[0]``, exactly as detect_hardware() builds it.
    """
    return {"gpu": dict(gpus[0]) if gpus else {"type": "none"}, "gpus": gpus}


def _nvidia() -> dict:
    return {"type": "nvidia", "model": "RTX 4090", "vram_mb": 24576, "cuda": True}


def _amd() -> dict:
    return {"type": "amd", "model": "RX 7900 XTX", "vram_mb": 24576, "rocm": True}


def test_two_nvidia_gpus_report_two_cuda_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two enumerated NVIDIA GPUs -> gpu-cuda-0 AND gpu-cuda-1."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    resources = _collect_resources([], _hw_multi([_nvidia(), _nvidia()]))
    assert resources == ["cpu-inference", "gpu-cuda-0", "gpu-cuda-1"]


def test_three_amd_gpus_report_three_rocm_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three enumerated AMD GPUs -> gpu-rocm-0, gpu-rocm-1, gpu-rocm-2."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    resources = _collect_resources([], _hw_multi([_amd(), _amd(), _amd()]))
    assert resources == ["cpu-inference", "gpu-rocm-0", "gpu-rocm-1", "gpu-rocm-2"]


def test_mixed_gpus_indexed_per_class_in_detection_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Indices count within each class, in detection order; entries that
    are neither CUDA- nor ROCm-class (e.g. an Intel iGPU) are skipped."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    gpus = [_amd(), {"type": "intel", "model": "UHD 770"}, _nvidia(), _nvidia()]
    resources = _collect_resources([], _hw_multi(gpus))
    assert resources == ["cpu-inference", "gpu-rocm-0", "gpu-cuda-0", "gpu-cuda-1"]


def test_gpu_without_usable_driver_not_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PCI-visible card whose entry lacks the cuda usability flag is not
    leasable (#3242 rule, applied per enumerated GPU)."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    no_driver = {"type": "nvidia", "model": "RTX 4090", "vram_mb": 24576, "cuda": False}
    resources = _collect_resources([], _hw_multi([_nvidia(), no_driver]))
    assert resources == ["cpu-inference", "gpu-cuda-0"]


def test_unusable_first_card_keeps_positional_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """Indices are positional within the class: when the first card has
    no usable driver, the second (usable) card is gpu-cuda-1, not
    gpu-cuda-0 -- a resource name stays bound to the same physical card
    across driver-state changes."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    no_driver = {"type": "nvidia", "model": "RTX 4090", "vram_mb": 24576, "cuda": False}
    resources = _collect_resources([], _hw_multi([no_driver, _nvidia()]))
    assert resources == ["cpu-inference", "gpu-cuda-1"]
    no_rocm = {"type": "amd", "model": "RX 7900 XTX", "vram_mb": 24576, "rocm": False}
    resources = _collect_resources([], _hw_multi([no_rocm, _amd()]))
    assert resources == ["cpu-inference", "gpu-rocm-1"]


def test_enumerated_none_gpu_reports_no_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    """_detect_gpus() returns a single type="none" entry on GPU-less
    hosts; that entry must not produce a GPU resource."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    assert _collect_resources([], _hw_multi([{"type": "none"}])) == ["cpu-inference"]


def test_single_amd_in_list_still_gpu_rocm_0(monkeypatch: pytest.MonkeyPatch) -> None:
    """Single-GPU enumerated profiles (incl. the WSL2 /dev/dxg shape, one
    amd entry with rocm=True) are unchanged: exactly one gpu-rocm-0."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    assert _collect_resources([], _hw_multi([_amd()])) == ["cpu-inference", "gpu-rocm-0"]


def test_flagless_list_falls_back_to_singular_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``gpus`` list whose entries carry no usability flags must not lose
    the resource the singular ``gpu`` entry earned before enumeration --
    the singular derivation remains as the fallback."""
    monkeypatch.delenv("TAOS_WORKER_RESOURCES", raising=False)
    hw = {"gpu": {"type": "nvidia", "cuda": True}, "gpus": [{"type": "nvidia"}]}
    assert _collect_resources([], hw) == ["cpu-inference", "gpu-cuda-0"]


@pytest.mark.asyncio
async def test_lease_claim_second_gpu_independently(client, app):
    """A dual-GPU worker's gpu-cuda-1 is leasable on its own, leasing it
    does not block gpu-cuda-0, and a second claim on gpu-cuda-1 is refused
    while the first lease lives (one active lease per resource)."""
    hw = _hw_multi([_nvidia(), _nvidia()])
    resources = _collect_resources([], hw)
    assert "gpu-cuda-1" in resources  # enumeration precondition
    key = await _register_worker(
        client, app, "dual-nvidia", "http://10.0.0.4:9000",
        capabilities=["llm-chat"],
        hardware={"gpu": hw["gpu"]},
        resources=resources,
    )
    await _heartbeat(client, key, "dual-nvidia",
                     free_vram_mb=20000, used_vram_mb=4000)

    claim = {"caller": "skald-dispatcher", "ttl_seconds": 30, "required_vram_mb": 4000}
    resp = await client.post(
        "/api/cluster/leases/claim", headers=_auth(app),
        json={**claim, "resource_id": "dual-nvidia:gpu-cuda-1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "claimed"

    resp = await client.post(
        "/api/cluster/leases/claim", headers=_auth(app),
        json={**claim, "resource_id": "dual-nvidia:gpu-cuda-0"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "claimed"

    resp = await client.post(
        "/api/cluster/leases/claim", headers=_auth(app),
        json={**claim, "resource_id": "dual-nvidia:gpu-cuda-1"},
    )
    assert resp.status_code == 409

