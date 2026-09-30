"""Tests for GET /api/cluster/map — the read-only capability + placement view.

The "see it" half of #897: one aggregate over the cluster manager's own
state (no second source of truth) that answers "what can the whole cluster
do" and "what is installed / running where, on what, and is it healthy".

Coverage:
- a node reporting capabilities plus a loaded and an installed-but-stopped
  model shows up in the aggregate with its VRAM numbers and health
- a node with no GPU (CPU-only tier) is still listed, with its CPU-only
  capabilities present in the aggregate
- an offline node stays in the map with health "offline"
- an active GPU lease is attributed to its node
- the endpoint is read-only: it must not mutate worker state
- a cookie-less caller and a signed-in non-admin are both refused
"""
from __future__ import annotations

import time

import pytest

from tinyagentos.cluster.worker_protocol import GpuLease, WorkerInfo


def _put_worker(app, worker: WorkerInfo) -> WorkerInfo:
    app.state.cluster_manager._workers[worker.name] = worker  # noqa: SLF001
    return worker


@pytest.mark.asyncio
async def test_map_aggregates_capabilities_and_placement(client, app):
    """A node's capabilities, loaded vs installed models and VRAM land in the map."""
    now = time.time()
    _put_worker(
        app,
        WorkerInfo(
            name="gpu-box",
            url="http://10.0.0.7:9000",
            platform="linux",
            status="online",
            last_heartbeat=now,
            load=0.25,
            hardware={
                "ram_mb": 32768,
                "cpu": {"arch": "x86_64", "cores": 8},
                "gpu": {"type": "nvidia", "model": "RTX 3060", "vram_mb": 12288, "cuda": True},
                "npu": {},
            },
            # One running backend with a loaded model and one installed-but-not
            # running model declared in the worker manifest.
            backends=[
                {
                    "name": "vllm:8000",
                    "type": "vllm",
                    "status": "ok",
                    "capabilities": ["llm-chat"],
                    "models": [{"name": "qwen2.5-7b"}],
                    "loaded_models": [{"name": "qwen2.5-7b"}],
                    "available_models": [
                        {
                            "model_id": "qwen2.5-7b",
                            "capability": "chat",
                            "software": "vllm",
                            "vram_required_gb": 6.0,
                            "status": "loaded",
                        },
                        {
                            "model_id": "llama3-8b",
                            "capability": "chat",
                            "software": "vllm",
                            "vram_required_gb": 8.0,
                            "status": "available",
                        },
                    ],
                },
                # Synthetic entry for a declared but stopped backend — the
                # worker agent emits these so total capacity is visible.
                {
                    "name": "sd-cpp",
                    "type": "sd-cpp",
                    "url": None,
                    "status": "stopped",
                    "capabilities": ["image-generation"],
                    "models": [],
                    "available_models": [
                        {
                            "model_id": "dreamshaper-8-lcm",
                            "capability": "image-generation",
                            "software": "sd-cpp",
                            "status": "available",
                        }
                    ],
                },
            ],
            models=["qwen2.5-7b"],
            capabilities=["chat"],
            free_vram_mb=4096,
            used_vram_mb=8192,
        ),
    )

    resp = await client.get("/api/cluster/map")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    node = next((n for n in data["nodes"] if n["name"] == "gpu-box"), None)
    assert node is not None, data
    assert node["health"] == "online"
    assert node["status"] == "online"
    assert node["platform"] == "linux"
    assert node["tier_id"] == "x86-cuda-12gb"

    # Live hardware / VRAM accounting.
    assert node["hardware"]["gpu"]["vram_mb"] == 12288
    assert node["vram"]["free_mb"] == 4096
    assert node["vram"]["used_mb"] == 8192
    assert node["vram"]["total_mb"] == 12288

    # Placement: one loaded model, two installed-but-stopped.
    placement = {(p["model_id"], p["state"]) for p in node["placement"]}
    assert ("qwen2.5-7b", "loaded") in placement
    assert ("llama3-8b", "installed") in placement
    assert ("dreamshaper-8-lcm", "installed") in placement

    loaded = next(p for p in node["placement"] if p["model_id"] == "qwen2.5-7b")
    assert loaded["backend"] == "vllm:8000"
    assert loaded["backend_type"] == "vllm"
    assert loaded["backend_status"] == "ok"
    assert loaded["capability"] == "chat"

    stopped = next(p for p in node["placement"] if p["model_id"] == "dreamshaper-8-lcm")
    assert stopped["backend_status"] == "stopped"
    assert stopped["state"] == "installed"

    # Capability aggregate: chat runs here, image-gen is only installed here.
    # Buckets are mutually exclusive — a node serving a capability is reported
    # as active, not also as installed.
    caps = {c["capability"]: c for c in data["capabilities"]}
    assert "gpu-box" in caps["chat"]["active_nodes"]
    assert "gpu-box" not in caps["chat"]["installed_nodes"]
    assert "gpu-box" in caps["image-generation"]["installed_nodes"]
    assert "gpu-box" not in caps["image-generation"]["active_nodes"]
    # The finer detail survives at node level: llama3-8b is installed for the
    # same capability the node is already serving with qwen2.5-7b.
    assert {"qwen2.5-7b", "llama3-8b"} <= {
        p["model_id"] for p in node["placement"] if p["capability"] == "chat"
    }


@pytest.mark.asyncio
async def test_map_catalog_fallback_keeps_resident_model_loaded(client, app):
    """A backend with a `models` catalog and a `loaded_models` residency signal
    but NO `available_models`: the resident catalog model must read `loaded`,
    the idle ones `installed`. Regression for the blanket-"installed" fallback,
    which emitted the resident model as installed and then had the later
    resident pass deduped away (same model_id + backend key)."""
    _put_worker(
        app,
        WorkerInfo(
            name="no-manifest-box",
            url="http://10.0.0.12:9000",
            platform="linux",
            status="online",
            last_heartbeat=time.time(),
            hardware={
                "ram_mb": 16384,
                "cpu": {"arch": "x86_64"},
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 8192},
            },
            backends=[
                {
                    "name": "ollama:11434",
                    "type": "ollama",
                    "status": "ok",
                    "capabilities": ["llm-chat"],
                    # No worker manifest on this node => no `available_models`,
                    # but the /api/ps residency probe still ran.
                    "models": [{"name": "qwen2.5-7b"}, {"name": "phi-3-mini"}],
                    "loaded_models": [{"name": "qwen2.5-7b"}],
                }
            ],
            models=["qwen2.5-7b", "phi-3-mini"],
            capabilities=["chat"],
        ),
    )

    resp = await client.get("/api/cluster/map")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    node = next((n for n in data["nodes"] if n["name"] == "no-manifest-box"), None)
    assert node is not None, data
    placement = {(p["model_id"], p["state"]) for p in node["placement"]}
    assert ("qwen2.5-7b", "loaded") in placement
    assert ("phi-3-mini", "installed") in placement
    # Exactly one row per model — the resident pass must not double-add.
    assert len(node["placement"]) == 2


@pytest.mark.asyncio
async def test_map_legacy_backend_without_loaded_models_is_installed(client, app):
    """A backend that never reports `loaded_models` must not have its whole
    catalog counted as loaded — the absence of a residency signal means every
    model is installed, not resident (regression for the fallback that treated
    `models` as the residency set)."""
    _put_worker(
        app,
        WorkerInfo(
            name="legacy-box",
            url="http://10.0.0.11:9000",
            platform="linux",
            status="online",
            last_heartbeat=time.time(),
            hardware={
                "ram_mb": 16384,
                "cpu": {"arch": "x86_64"},
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 8192},
            },
            backends=[
                {
                    "name": "ollama:11434",
                    "type": "ollama",
                    "status": "ok",
                    "capabilities": ["llm-chat"],
                    # Legacy worker: `models` (the whole catalog) but NO
                    # `loaded_models` residency signal.
                    "models": [{"name": "qwen2.5-7b"}, {"name": "phi-3-mini"}],
                }
            ],
            models=["qwen2.5-7b", "phi-3-mini"],
            capabilities=["chat"],
        ),
    )

    resp = await client.get("/api/cluster/map")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    node = next((n for n in data["nodes"] if n["name"] == "legacy-box"), None)
    assert node is not None, data
    placement = {(p["model_id"], p["state"]) for p in node["placement"]}
    # With no residency signal, nothing may be claimed "loaded".
    assert ("qwen2.5-7b", "installed") in placement
    assert ("phi-3-mini", "installed") in placement
    assert all(state == "installed" for _, state in placement)


@pytest.mark.asyncio
async def test_map_lists_gpuless_node_with_cpu_only_capabilities(client, app):
    """A node with no GPU workers is still listed with its CPU-only view."""
    _put_worker(
        app,
        WorkerInfo(
            name="pi-cpu",
            url="http://10.0.0.9:9000",
            platform="linux",
            status="online",
            last_heartbeat=time.time(),
            hardware={"ram_mb": 8192, "cpu": {"arch": "x86_64"}, "gpu": {}, "npu": {}},
            backends=[
                {
                    "name": "llama-cpp",
                    "type": "llama-cpp",
                    "url": None,
                    "status": "stopped",
                    "capabilities": ["llm-chat", "embedding"],
                    "models": [],
                    "available_models": [
                        {
                            "model_id": "phi-3-mini",
                            "capability": "chat",
                            "software": "llama-cpp",
                            "status": "available",
                        }
                    ],
                }
            ],
            models=[],
            capabilities=[],
            potential_capabilities=["upscaling"],
        ),
    )

    resp = await client.get("/api/cluster/map")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    node = next((n for n in data["nodes"] if n["name"] == "pi-cpu"), None)
    assert node is not None, data
    # Still listed, and its CPU-only tier is derived rather than dropped.
    assert node["tier_id"] == "x86-cpu-8gb"
    assert node["health"] == "online"
    # No GPU probe -> no VRAM claim (None must not collapse to 0).
    assert node["vram"]["total_mb"] is None
    assert node["vram"]["free_mb"] is None
    # The catalog potential derived from hardware is surfaced alongside
    # whatever the worker already reported.
    assert "upscaling" in node["potential_capabilities"]

    caps = {c["capability"]: c for c in data["capabilities"]}
    # Capabilities reach the aggregate even for a backend that is stopped, and
    # the node is reported as installed-only (nothing here is serving).
    assert caps["chat"]["installed_nodes"] == ["pi-cpu"]
    assert "pi-cpu" in caps["llm-chat"]["installed_nodes"]
    assert "pi-cpu" in caps["embedding"]["installed_nodes"]
    assert caps["chat"]["active_nodes"] == []
    assert caps["llm-chat"]["active_nodes"] == []


@pytest.mark.asyncio
async def test_map_keeps_offline_node_and_attributes_leases(client, app):
    """Offline rows are preserved (health offline) and leases name their node."""
    _put_worker(
        app,
        WorkerInfo(
            name="stale-box",
            url="http://10.0.0.11:9000",
            status="offline",
            last_heartbeat=time.time() - 3600,
            hardware={"ram_mb": 16384, "cpu": {"arch": "x86_64"}, "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 8192}},
            capabilities=["chat"],
        ),
    )
    _put_worker(
        app,
        WorkerInfo(
            name="gpu-box",
            url="http://10.0.0.7:9000",
            status="online",
            last_heartbeat=time.time(),
            hardware={"ram_mb": 32768, "cpu": {"arch": "x86_64"}, "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 12288}},
            capabilities=["chat"],
        ),
    )
    app.state.cluster_manager._leases["l_test"] = GpuLease(  # noqa: SLF001
        lease_id="l_test",
        resource_id="gpu-box:gpu-cuda-0",
        caller="skald-dispatcher",
        expires_at=time.time() + 60,
        required_vram_mb=6144,
    )

    resp = await client.get("/api/cluster/map")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    stale = next((n for n in data["nodes"] if n["name"] == "stale-box"), None)
    assert stale is not None, data
    assert stale["status"] == "offline"
    assert stale["health"] == "offline"
    assert stale["heartbeat_age_s"] >= 3600

    gpu = next(n for n in data["nodes"] if n["name"] == "gpu-box")
    assert [lease["caller"] for lease in gpu["leases"]] == ["skald-dispatcher"]
    assert gpu["leases"][0]["resource_id"] == "gpu-box:gpu-cuda-0"
    # Leases are attributed, not duplicated onto unrelated nodes.
    assert stale["leases"] == []

    # An offline node keeps its row and its last-reported capability, but that
    # capability is NOT advertised as serving: the manager retains the worker,
    # the mesh still cannot reach it.
    caps = {c["capability"]: c for c in data["capabilities"]}
    assert caps["chat"]["active_nodes"] == ["gpu-box"]
    assert caps["chat"]["installed_nodes"] == ["stale-box"]


@pytest.mark.asyncio
async def test_map_capability_buckets_never_overlap(client, app):
    """Serving, installed and potential are mutually exclusive per capability."""
    _put_worker(
        app,
        WorkerInfo(
            name="gpu-box",
            url="http://10.0.0.7:9000",
            status="online",
            last_heartbeat=time.time(),
            hardware={"ram_mb": 32768, "cpu": {"arch": "x86_64"}, "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 12288}},
            capabilities=["chat"],
            backends=[
                {
                    "name": "vllm:8000",
                    "type": "vllm",
                    "status": "ok",
                    "capabilities": ["llm-chat"],
                    "models": [{"name": "qwen2.5-7b"}],
                    "available_models": [
                        {"model_id": "qwen2.5-7b", "capability": "chat", "status": "loaded"},
                        {"model_id": "llama3-8b", "capability": "chat", "status": "available"},
                    ],
                }
            ],
        ),
    )
    _put_worker(
        app,
        WorkerInfo(
            name="pi-cpu",
            url="http://10.0.0.9:9000",
            status="online",
            last_heartbeat=time.time(),
            hardware={"ram_mb": 8192, "cpu": {"arch": "x86_64"}, "gpu": {}, "npu": {}},
            potential_capabilities=["chat"],
        ),
    )

    resp = await client.get("/api/cluster/map")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    caps = {c["capability"]: c for c in data["capabilities"]}
    # gpu-box serves chat (so not installed/capable for it), pi-cpu only could.
    assert caps["chat"]["active_nodes"] == ["gpu-box"]
    assert caps["chat"]["installed_nodes"] == []
    assert caps["chat"]["potential_nodes"] == ["pi-cpu"]
    for cap in data["capabilities"]:
        active = set(cap["active_nodes"])
        installed = set(cap["installed_nodes"])
        potential = set(cap["potential_nodes"])
        assert not (active & installed), cap
        assert not (active & potential), cap
        assert not (installed & potential), cap


@pytest.mark.asyncio
async def test_map_requires_an_admin_session(app, client):
    """A new endpoint is unauthenticated until proven otherwise — prove it is not.

    The `client` fixture carries a test admin session, so this builds a second,
    cookie-less client against the same app: a whole-mesh hardware/placement
    inventory must not be readable without one. The auth middleware answers 401
    before the route's own `_require_admin` 403 can run, so either status is a
    correct rejection here.
    """
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anon:
        resp = await anon.get("/api/cluster/map")
    assert resp.status_code in (401, 403)
    assert "nodes" not in resp.text


@pytest.mark.asyncio
async def test_map_rejects_a_signed_in_non_admin(client, app, monkeypatch):
    """A signed-in non-admin gets the route's own 403, not just the middleware's.

    The cookie-less client above is rejected by the auth middleware before the
    handler runs, so on its own it would still pass if `_require_admin` were
    dropped. This pins the admin-only contract down: a valid session that is not
    an admin must not read a whole-mesh hardware/placement inventory.
    """
    monkeypatch.setattr(app.state.auth, "session_user", lambda token, user_agent=None: {"is_admin": False})

    resp = await client.get("/api/cluster/map")
    assert resp.status_code == 403, resp.text
    assert "nodes" not in resp.text


@pytest.mark.asyncio
async def test_map_is_read_only_for_cluster_state(client, app):
    """Reading the map must not write back into the cluster manager's workers."""
    worker = _put_worker(
        app,
        WorkerInfo(
            name="gpu-box",
            url="http://10.0.0.7:9000",
            status="online",
            last_heartbeat=time.time(),
            hardware={"ram_mb": 32768, "cpu": {"arch": "x86_64"}, "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 12288}},
            capabilities=["chat"],
        ),
    )
    before = (worker.tier_id, list(worker.potential_capabilities), worker.status)

    resp = await client.get("/api/cluster/map")
    assert resp.status_code == 200, resp.text

    assert (worker.tier_id, list(worker.potential_capabilities), worker.status) == before
