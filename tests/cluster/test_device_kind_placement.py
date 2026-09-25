"""A kind="device" node (a taOSusb board paired over BLE) must never be
selected for any job type -- the model mesh included.

Each candidate-selection site is tested with the device ADVERTISING the
exact capability being requested, so a bare "the device has no matching
capability" reading cannot pass these by accident -- the guard under test
is the kind check itself, not a side effect of an empty capabilities list.
"""
from __future__ import annotations

import pytest

from tinyagentos.browser_sessions import _capable_workers
from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.worker_protocol import WorkerInfo


def _device(name="taOSusb-DEV1", **overrides) -> WorkerInfo:
    kwargs = dict(
        name=name,
        url="",
        kind="device",
        status="online",
        # Deliberately over-advertise every capability a job type below
        # would look for, so the test proves the KIND guard fires -- not
        # that the device merely lacks the capability.
        capabilities=["chat", "embed", "image-generation", "browser"],
        hardware={
            "ram_mb": 16384,
            "cpu": {"cores": 16},
            "gpu": {"cuda": True, "vram_mb": 24576},
        },
        free_vram_mb=24576,
        used_vram_mb=0,
    )
    kwargs.update(overrides)
    return WorkerInfo(**kwargs)


def _worker(name="real-worker", **overrides) -> WorkerInfo:
    kwargs = dict(
        name=name,
        url="http://10.0.0.1:6970",
        kind="worker",
        status="online",
        capabilities=["chat", "embed", "image-generation", "browser"],
        hardware={
            "ram_mb": 16384,
            "cpu": {"cores": 16},
            "gpu": {"cuda": True, "vram_mb": 24576},
        },
        free_vram_mb=24576,
        used_vram_mb=0,
    )
    kwargs.update(overrides)
    return WorkerInfo(**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", ["chat", "embed", "image-generation"])
async def test_device_never_selected_for_model_mesh_capability(capability):
    """cluster.get_workers_for_capability backs TaskRouter.route_request --
    chat, embed, image-generation -- the model mesh's candidate pool."""
    mgr = ClusterManager()
    await mgr.register_worker(_device())
    assert mgr.get_workers_for_capability(capability) == []

    # Control: a real worker with the same capability IS selected -- proves
    # the empty result above is the kind guard, not a broken test setup.
    await mgr.register_worker(_worker())
    eligible = mgr.get_workers_for_capability(capability)
    assert [w.name for w in eligible] == ["real-worker"]


@pytest.mark.asyncio
async def test_device_never_selected_as_best_worker():
    mgr = ClusterManager()
    await mgr.register_worker(_device())
    assert mgr.get_best_worker("chat") is None
    await mgr.register_worker(_worker())
    assert mgr.get_best_worker("chat").name == "real-worker"


@pytest.mark.asyncio
async def test_device_never_selected_for_browser_sessions():
    """browser_sessions._capable_workers is a second, independent
    candidate-selection site -- exactly the kind of second job type the
    plan warns a capability-only guard would miss."""
    mgr = ClusterManager()
    await mgr.register_worker(_device())
    candidates = _capable_workers(mgr, min_ram_mb=1024, min_cores=1)
    assert candidates == []

    await mgr.register_worker(_worker())
    candidates = _capable_workers(mgr, min_ram_mb=1024, min_cores=1)
    assert [w.name for w in candidates] == ["real-worker"]


@pytest.mark.asyncio
async def test_device_excluded_even_when_only_worker_in_cluster():
    """No control worker at all -- the device alone must still yield zero
    candidates for every job-selection path, not just when outranked."""
    mgr = ClusterManager()
    await mgr.register_worker(_device(name="lonely-device"))
    assert mgr.get_workers_for_capability("chat") == []
    assert mgr.get_best_worker("embed") is None
    assert _capable_workers(mgr, min_ram_mb=0, min_cores=0) == []


@pytest.mark.asyncio
async def test_device_never_admits_a_gpu_task():
    """The GPU arbiter's two cluster admission checks (VRAM and GPU arch).
    The device over-advertises 24 GB and an sm_86 GPU; alone in the
    cluster it must still admit nothing."""
    from tinyagentos.scheduler.gpu_arbiter import GpuArbiter

    mgr = ClusterManager()
    dev = _device(name="gpu-device")
    dev.hardware["gpu"]["compute_cap"] = "sm_86"
    await mgr.register_worker(dev)
    arbiter = GpuArbiter(cluster_manager=mgr)
    assert arbiter._check_cluster_admission(1024).admitted is False
    assert arbiter._check_gpu_arch_compatibility("sm_86")[0] is False

    # Control: the same hardware as a worker is admitted by both.
    mgr2 = ClusterManager()
    w = _worker(name="gpu-worker")
    w.hardware["gpu"]["compute_cap"] = "sm_86"
    await mgr2.register_worker(w)
    arbiter2 = GpuArbiter(cluster_manager=mgr2)
    assert arbiter2._check_cluster_admission(1024).admitted is True
    assert arbiter2._check_gpu_arch_compatibility("sm_86")[0] is True


def test_worker_info_kind_defaults_to_worker():
    """Every pre-existing worker (registered before this change existed)
    must default to kind='worker', not silently become a device."""
    w = WorkerInfo(name="legacy", url="http://x:1")
    assert w.kind == "worker"
