"""Surface tests for the resolver public type API.

Real behavior tests for resolve()/classify() live in test_resolver.py;
this file just pins the wire shape so callers (frontend, dispatcher) can
rely on field names not silently changing.
"""
from tinyagentos.catalog.resolver import (
    BackendDep,
    DeviceCapability,
    ResolveErr,
    ResolveOk,
)


class TestDeviceCapability:
    def test_construction_with_required_fields(self):
        d = DeviceCapability(
            device_id="local",
            targets=("rockchip", "cpu"),
            total_ram_mb=16384,
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=("rk-llama-cpp",),
        )
        assert d.device_id == "local"
        assert d.targets == ("rockchip", "cpu")


class TestBackendDep:
    def test_default_min_vram_zero(self):
        b = BackendDep(
            id="rk-llama-cpp",
            targets=("rockchip",),
            min_ram_mb=4096,
        )
        assert b.min_vram_mb == 0


class TestResolveOk:
    def test_action_use(self):
        r = ResolveOk(backend_id="rk-llama-cpp", variant_id="q4_k_m", action="use")
        assert r.action == "use"

    def test_action_install_chain(self):
        r = ResolveOk(backend_id="ollama", variant_id="q8_0", action="install_chain")
        assert r.action == "install_chain"


class TestResolveErr:
    def test_carries_structured_advice(self):
        e = ResolveErr(
            reason="Q8_0 needs 8 GB RAM, this Pi has 16 GB but only 1.2 GB free disk",
            near_miss={"variant": "q8_0", "blocked_by": "disk", "short_by_mb": 5800},
            suggestions=[
                "Pick a smaller variant — Q4_K_M needs 1.9 GB disk",
                "Install on workerB (12.4 GB free)",
            ],
        )
        assert e.near_miss["blocked_by"] == "disk"
        assert len(e.suggestions) == 2
