import types

import pytest

from tinyagentos.tools.cluster_tools import execute_describe_image_capabilities


class _Backend:
    def __init__(self, name, type_, models, lifecycle="running"):
        self.name = name
        self.type = type_
        self.models = models
        self.lifecycle_state = lifecycle


class _Catalog:
    def __init__(self, backends):
        self._b = backends

    def backends_with_capability(self, cap):
        return self._b if cap == "image-generation" else []


class _Worker:
    def __init__(self, name, hardware, backends, status="online"):
        self.name = name
        self.hardware = hardware
        self.backends = backends
        self.status = status


class _Cluster:
    def __init__(self, workers):
        self._w = workers

    def get_workers(self):
        return self._w


def _req(catalog=None, cluster=None, hardware=None):
    state = types.SimpleNamespace(
        backend_catalog=catalog, cluster_manager=cluster, hardware_profile=hardware
    )
    return types.SimpleNamespace(app=types.SimpleNamespace(state=state))


@pytest.mark.asyncio
async def test_local_image_backends_listed_with_tier_and_loaded():
    catalog = _Catalog([_Backend("sd", "sd-cpp", [{"id": "sdxl"}], "running")])
    res = await execute_describe_image_capabilities({}, _req(catalog=catalog, hardware={"gpu": "RTX 3060", "vram": "12GB"}))
    local = res["tiers"][0]
    assert local["node"] == "local"
    assert local["hardware"]["gpu"] == "RTX 3060"
    be = local["image_backends"][0]
    assert be["type"] == "sd-cpp" and be["tier"] == "cpu/gpu" and be["loaded"] is True
    assert be["models"] == ["sdxl"]


@pytest.mark.asyncio
async def test_cluster_workers_included():
    worker = _Worker("nvidia-box", {"gpu": "3060", "vram": "12GB"},
                     [{"name": "sd", "type": "sd-cpp", "capabilities": ["image-generation"], "models": ["sdxl"]}])
    res = await execute_describe_image_capabilities({}, _req(cluster=_Cluster([worker])))
    nodes = [t["node"] for t in res["tiers"]]
    assert "nvidia-box" in nodes
    w = next(t for t in res["tiers"] if t["node"] == "nvidia-box")
    assert w["image_backends"][0]["type"] == "sd-cpp"


@pytest.mark.asyncio
async def test_offline_worker_skipped():
    worker = _Worker("down", {}, [], status="offline")
    res = await execute_describe_image_capabilities({}, _req(cluster=_Cluster([worker])))
    assert all(t["node"] != "down" for t in res["tiers"])


@pytest.mark.asyncio
async def test_empty_state_is_safe():
    res = await execute_describe_image_capabilities({}, _req())
    assert res["tiers"][0]["node"] == "local"
    assert res["tiers"][0]["image_backends"] == []
