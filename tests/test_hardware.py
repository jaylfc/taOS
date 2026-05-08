# tests/test_hardware.py
import json
from pathlib import Path
import pytest
from unittest.mock import patch
from tinyagentos.hardware import detect_hardware, get_hardware_profile, HardwareProfile
from tinyagentos import hardware as hardware_mod

import pytest_asyncio


class TestDetectHardware:
    def test_returns_hardware_profile(self):
        profile = detect_hardware()
        assert isinstance(profile, HardwareProfile)
        assert profile.cpu.arch in ("aarch64", "x86_64", "armv7l")
        assert profile.ram_mb > 0
        assert profile.disk.total_gb > 0

    def test_profile_id_format(self):
        profile = detect_hardware()
        pid = profile.profile_id
        # Format: {arch}-{accelerator}-{ram}gb
        parts = pid.split("-")
        assert len(parts) >= 3
        assert parts[-1].endswith("gb")

    def test_npu_detection_returns_type(self):
        profile = detect_hardware()
        assert profile.npu.type in ("rknpu", "hailo", "coral", "qualcomm", "none")

    def test_gpu_detection_returns_type(self):
        profile = detect_hardware()
        assert profile.gpu.type in ("nvidia", "amd", "mali", "intel", "none")

    def test_save_and_load(self, tmp_path):
        profile = detect_hardware()
        path = tmp_path / "hardware.json"
        profile.save(path)
        assert path.exists()
        loaded = HardwareProfile.load(path)
        assert loaded.profile_id == profile.profile_id
        assert loaded.ram_mb == profile.ram_mb


class TestDetectNpuRknpuModernPaths:
    """Modern RK3588 BSP kernels don't always expose /dev/rknpu, debugfs, or
    devfreq. Detection should still succeed via the platform-drivers bind
    dir or the DRM render-node driver symlink."""

    def _stub_environment(self, monkeypatch, exists_set, drm_nodes):
        """Stub filesystem probes so _detect_npu sees only the configured
        signals. exists_set: set of str paths that _path_exists_safe should
        report present. drm_nodes: list of (renderD_path, driver_basename)
        tuples to expose under /sys/class/drm."""
        def fake_exists_safe(p):
            return str(p) in exists_set
        monkeypatch.setattr(hardware_mod, "_path_exists_safe", fake_exists_safe)

        real_glob = Path.glob
        def fake_glob(self, pattern):
            if str(self) == "/sys/class/drm" and pattern == "renderD*":
                return iter([Path(node) for node, _ in drm_nodes])
            if str(self) == "/dev":
                return iter([])
            return real_glob(self, pattern)
        monkeypatch.setattr(Path, "glob", fake_glob)

        driver_targets = {
            f"{node}/device/driver": basename for node, basename in drm_nodes
        }
        real_resolve = Path.resolve
        def fake_resolve(self, *args, **kwargs):
            key = str(self)
            if key in driver_targets:
                return Path(f"/sys/bus/platform/drivers/{driver_targets[key]}")
            return real_resolve(self, *args, **kwargs)
        monkeypatch.setattr(Path, "resolve", fake_resolve)

        # Block /proc/device-tree/model and rknpu/load reads — force the
        # core-count fallback path so tests don't depend on host files.
        real_read_text = Path.read_text
        def fake_read_text(self, *args, **kwargs):
            if str(self) in (
                "/proc/device-tree/model",
                "/sys/kernel/debug/rknpu/load",
            ):
                raise OSError("stubbed")
            return real_read_text(self, *args, **kwargs)
        monkeypatch.setattr(Path, "read_text", fake_read_text)

        # Neutralise lspci / non-rknpu accelerator probes.
        monkeypatch.setattr(hardware_mod, "_run", lambda *a, **k: "")

    def test_platform_drivers_dir_alone_detects_rknpu(self, monkeypatch):
        self._stub_environment(
            monkeypatch,
            exists_set={"/sys/bus/platform/drivers/RKNPU"},
            drm_nodes=[],
        )
        npu = hardware_mod._detect_npu()
        assert npu.type == "rknpu"

    def test_drm_render_node_driver_symlink_detects_rknpu(self, monkeypatch):
        self._stub_environment(
            monkeypatch,
            exists_set=set(),
            drm_nodes=[("/sys/class/drm/renderD129", "RKNPU")],
        )
        npu = hardware_mod._detect_npu()
        assert npu.type == "rknpu"


class TestGetHardwareProfile:
    def test_always_reprobes_on_startup(self, tmp_path, monkeypatch):
        """Cache file with stale data is ignored; probe always runs first."""
        # Write a stale cache with a clearly wrong ram_mb
        stale = detect_hardware()
        stale_path = tmp_path / "hardware.json"
        stale.save(stale_path)
        stale_data = json.loads(stale_path.read_text())
        stale_data["ram_mb"] = 1  # sentinel value to detect stale read
        stale_path.write_text(json.dumps(stale_data))

        fresh = detect_hardware()
        monkeypatch.setattr(
            "tinyagentos.hardware.detect_hardware", lambda: fresh
        )

        result = get_hardware_profile(stale_path)
        assert result.ram_mb == fresh.ram_mb
        assert result.ram_mb != 1

        # Cache file should now reflect the fresh probe
        saved = json.loads(stale_path.read_text())
        assert saved["ram_mb"] == fresh.ram_mb

    def test_falls_back_to_cache_when_probe_raises(self, tmp_path, monkeypatch):
        """When detect_hardware raises, the cached profile is returned."""
        cached = detect_hardware()
        cache_path = tmp_path / "hardware.json"
        cached.save(cache_path)

        monkeypatch.setattr(
            "tinyagentos.hardware.detect_hardware",
            lambda: (_ for _ in ()).throw(RuntimeError("test")),
        )

        result = get_hardware_profile(cache_path)
        assert result.profile_id == cached.profile_id

    def test_reraises_when_probe_raises_and_no_cache(self, tmp_path, monkeypatch):
        """When detect_hardware raises and no cache exists, the exception propagates."""
        cache_path = tmp_path / "hardware.json"

        monkeypatch.setattr(
            "tinyagentos.hardware.detect_hardware",
            lambda: (_ for _ in ()).throw(RuntimeError("test")),
        )

        with pytest.raises(RuntimeError, match="test"):
            get_hardware_profile(cache_path)

    def test_detects_if_no_cache(self, tmp_path):
        path = tmp_path / "hardware.json"
        profile = get_hardware_profile(path)
        assert profile.ram_mb > 0
        assert path.exists()  # auto-saved


@pytest.mark.asyncio
class TestHardwareRefreshEndpoint:
    async def test_refresh_returns_fresh_profile(self, client):
        """POST /api/system/hardware/refresh returns a valid hardware profile."""
        resp = await client.post("/api/system/hardware/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert "ram_mb" in data
        assert data["ram_mb"] > 0
        assert "profile_id" in data

    async def test_refresh_updates_app_state(self, client):
        """After refresh, GET /api/system reflects the new hardware values."""
        post_resp = await client.post("/api/system/hardware/refresh")
        assert post_resp.status_code == 200
        fresh = post_resp.json()

        get_resp = await client.get("/api/system")
        assert get_resp.status_code == 200
        hw = get_resp.json()["hardware"]
        assert hw["ram_mb"] == fresh["ram_mb"]
        assert hw["profile_id"] == fresh["profile_id"]

    async def test_refresh_overwrites_cache_file(self, client):
        """After refresh, the cache file reflects the fresh probe."""
        data_dir = client._transport.app.state.data_dir
        cache_path = data_dir / "hardware.json"

        resp = await client.post("/api/system/hardware/refresh")
        assert resp.status_code == 200
        fresh = resp.json()

        assert cache_path.exists()
        saved = json.loads(cache_path.read_text())
        assert saved["ram_mb"] == fresh["ram_mb"]
