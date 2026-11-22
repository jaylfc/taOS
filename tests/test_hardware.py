# tests/test_hardware.py
import json
from pathlib import Path
import pytest
from unittest.mock import patch
from tinyagentos.hardware import detect_hardware, get_hardware_profile, HardwareProfile, CpuInfo
from tinyagentos import hardware as hardware_mod
from tinyagentos.hardware import _nvidia_vram_for_model, _amd_vram_for_model
from tinyagentos.hardware import _soc_from_devicetree

import pytest_asyncio


class TestSocFromDeviceTree:
    def test_rk3588_from_compatible_when_model_omits_it(self):
        # The board name ("Orange Pi 5 Plus") does not contain the SoC; the
        # compatible string does. Both files are concatenated before matching.
        text = " orange pi 5 plus rockchip,rk3588-orangepi-5-plus rockchip,rk3588"
        assert _soc_from_devicetree(text) == "rk3588"

    def test_board_name_alone_does_not_match(self):
        assert _soc_from_devicetree(" orange pi 5 plus") == ""

    def test_raspberry_pi(self):
        assert _soc_from_devicetree(" raspberry pi 5 brcm,bcm2712") == "bcm2712"
        assert _soc_from_devicetree(" raspberry pi 4 model b") == "bcm2711"

    def test_unknown_returns_empty(self):
        assert _soc_from_devicetree(" generic x86 box") == ""


class TestDetectHardware:
    def test_returns_hardware_profile(self):
        profile = detect_hardware()
        assert isinstance(profile, HardwareProfile)
        assert profile.cpu.arch in ("aarch64", "arm64", "x86_64", "armv7l")
        assert profile.ram_mb > 0
        assert profile.disk.total_gb > 0

    def test_profile_id_format(self):
        profile = detect_hardware()
        pid = profile.profile_id
        # Format: {arch}-{accelerator}-{ram}gb
        parts = pid.split("-")
        assert len(parts) >= 3
        assert parts[-1].endswith("gb")

    def test_profile_id_treats_arm64_as_arm(self):
        # macOS reports platform.machine() == "arm64". It must land in the arm-*
        # tier; an M1 Mac previously produced "x86-cpu-8gb" and missed the
        # arm-cpu-* catalog entries.
        profile = HardwareProfile(cpu=CpuInfo(arch="arm64"), ram_mb=8192)
        assert profile.profile_id.startswith("arm-")

    def test_detect_ram_reads_hw_memsize_on_darwin(self, monkeypatch):
        # macOS has no /proc/meminfo; total RAM comes from hw.memsize in bytes.
        monkeypatch.setattr(hardware_mod.platform, "system", lambda: "Darwin")

        class _Completed:
            stdout = "17179869184\n"  # 16 GiB, in bytes

        monkeypatch.setattr("subprocess.run", lambda *a, **k: _Completed())
        assert hardware_mod._detect_ram() == 16384

    def test_detect_ram_blank_memsize_does_not_raise(self, monkeypatch):
        # A blank hw.memsize must not raise or return a bogus value: the probe
        # falls through to the /proc path (absent on macOS -> 0; present on
        # Linux -> the real figure). Either way the result must be a sane int.
        monkeypatch.setattr(hardware_mod.platform, "system", lambda: "Darwin")

        class _Completed:
            stdout = "\n"

        monkeypatch.setattr("subprocess.run", lambda *a, **k: _Completed())
        result = hardware_mod._detect_ram()
        assert isinstance(result, int)
        assert result >= 0

    def test_npu_detection_returns_type(self):
        profile = detect_hardware()
        assert profile.npu.type in ("rknpu", "hailo", "coral", "qualcomm", "none")

    def test_gpu_detection_returns_type(self):
        profile = detect_hardware()
        assert profile.gpu.type in ("nvidia", "amd", "mali", "intel", "apple", "none")

    def test_gpu_panthor_driver_no_mali0(self, monkeypatch):
        # Test that panthor driver is detected in path 2 even without /sys/class/misc/mali0
        # Simulates the mainline panthor GPU driver scenario
        # Mirror the _stub_environment mocking style from TestDetectNpuRknpuModernPaths

        # panthor is a Linux DRM driver, so pin the platform to Linux. On a
        # Darwin host _detect_gpu returns the Apple Silicon GPU before it ever
        # reaches the DRM scan, and this simulated /sys tree would be ignored.
        monkeypatch.setattr(hardware_mod.platform, "system", lambda: "Linux")

        # Stub Path.exists: mali0 absent, /sys/class/drm present, card1/device/driver
        # present; all else falls through to real Path.exists
        real_path_exists = Path.exists
        def fake_path_exists(self):
            s = str(self)
            if s == "/sys/class/misc/mali0":
                return False  # mali0 absent (panthor scenario)
            if s == "/sys/class/drm":
                return True  # drm directory exists (panthor scenario)
            if "card1/device/driver" in s:
                return True  # test card driver exists (panthor scenario)
            return real_path_exists(self)
        monkeypatch.setattr(Path, "exists", fake_path_exists)

        # Single _path_exists_safe that blocks NVIDIA probe
        real_safe = hardware_mod._path_exists_safe
        def fake_path_exists_safe(p):
            s = str(p)
            if "/proc/driver/nvidia/gpus" in s or s == "/proc/driver/nvidia":
                return False  # block NVIDIA probe
            return real_safe(p)
        monkeypatch.setattr(hardware_mod, "_path_exists_safe", fake_path_exists_safe)

        real_glob = Path.glob
        def fake_glob(self, pattern):
            if str(self) == "/sys/class/drm" and pattern == "card*/device/driver":
                return [Path("/sys/class/drm/card1/device/driver")]
            return real_glob(self, pattern)
        monkeypatch.setattr(Path, "glob", fake_glob)

        real_resolve = Path.resolve
        driver_targets = {
            "/sys/class/drm/card1/device/driver": "panthor",
        }
        def fake_resolve(self, *args, **kwargs):
            key = str(self)
            if key in driver_targets:
                # Return path whose .name is the driver name
                return Path(f"/sys/bus/platform/drivers/{driver_targets[key]}")
            return real_resolve(self, *args, **kwargs)
        monkeypatch.setattr(Path, "resolve", fake_resolve)

        # Mock _run to prevent lspci NVIDIA/AMD detection
        real_run = hardware_mod._run
        def fake_run(*a, **k):
            return ""
        monkeypatch.setattr(hardware_mod, "_run", fake_run)

        profile = detect_hardware()
        assert profile.gpu.type == "mali"

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


class TestNvidiaVramTable:
    def test_gtx_1050_ti_resolves_to_4096(self):
        assert _nvidia_vram_for_model("NVIDIA GeForce GTX 1050 Ti") == 4096

    def test_gtx_1050_ti_case_insensitive(self):
        assert _nvidia_vram_for_model("gtx 1050 ti") == 4096

    def test_rtx_3090_resolves_correctly(self):
        assert _nvidia_vram_for_model("NVIDIA GeForce RTX 3090") == 24576

    def test_unknown_model_returns_zero(self):
        assert _nvidia_vram_for_model("NVIDIA GeForce GTX 580") == 0

    def test_empty_model_returns_zero(self):
        assert _nvidia_vram_for_model("") == 0


class TestAmdVramTable:
    def test_rx_7900_xtx_resolves_to_24576(self):
        assert _amd_vram_for_model("AMD Radeon RX 7900 XTX") == 24576

    def test_rx_7900_xtx_case_insensitive(self):
        assert _amd_vram_for_model("rx 7900 xtx") == 24576

    def test_rx_6600_resolves_to_8192(self):
        assert _amd_vram_for_model("AMD Radeon RX 6600") == 8192

    def test_rx_7900_xt_does_not_match_xtx(self):
        # RX 7900 XT is 20480, not 24576 — ensure longer key matches first
        assert _amd_vram_for_model("AMD Radeon RX 7900 XT") == 20480

    def test_unknown_amd_card_returns_zero(self):
        assert _amd_vram_for_model("AMD Radeon HD 7970") == 0

    def test_empty_model_returns_zero(self):
        assert _amd_vram_for_model("") == 0


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


class TestWslDetection:
    def test_detect_wsl_via_env(self, monkeypatch):
        monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
        assert hardware_mod._detect_wsl() is True

    def test_no_wsl_on_clean_host(self, monkeypatch):
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
        monkeypatch.delenv("WSL_INTEROP", raising=False)
        # A normal Linux/mac host has no microsoft marker (or no /proc/version).
        assert hardware_mod._detect_wsl() is False

    def test_detect_hardware_explains_wsl_cap(self, monkeypatch):
        monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
        monkeypatch.setattr(hardware_mod, "_detect_ram", lambda: 8192)
        prof = detect_hardware()
        assert prof.wsl is True
        assert ".wslconfig" in prof.mem_note
        assert "8GB" in prof.mem_note
        # ram_mb itself is left untouched (8GB really is what the VM has)
        assert prof.ram_mb == 8192


class TestDiskTypeDetection:
    """Test _detect_disk correctly distinguishes eMMC vs microSD on mmcblk."""

    def test_mmcblk_with_boot_partitions_is_emmc(self, monkeypatch, tmp_path):
        """mmcblk with boot0/rpmb partitions -> eMMC."""
        monkeypatch.setattr(
            "tinyagentos.hardware._run",
            lambda *a, **k: "mmcblk0 1 mmc\n" if "lsblk" in a[0] else "",
        )
        real_exists = Path.exists
        seen_paths = []
        def fake_exists(self):
            s = str(self)
            seen_paths.append(s)
            if s.endswith("/sys/block/mmcblk0boot0") or s.endswith("/sys/block/mmcblk0boot1") or s.endswith("/sys/block/mmcblk0rpmb"):
                return True
            if s.endswith("/sys/block/mmcblk0"):
                return True
            return real_exists(self)
        monkeypatch.setattr(Path, "exists", fake_exists)
        import shutil as sh
        real_disk_usage = sh.disk_usage
        def fake_disk_usage(path):
            class DU:
                total = 32 * (1024**3)
                free = 16 * (1024**3)
            return DU()
        monkeypatch.setattr(sh, "disk_usage", fake_disk_usage)

        disk = hardware_mod._detect_disk()
        assert disk.type == "emmc"
        for p in seen_paths:
            assert not p.endswith("mmcblkboot0"), f"Wrong path queried: {p}"

    def test_mmcblk_without_boot_partitions_is_sd(self, monkeypatch, tmp_path):
        """mmcblk without boot partitions -> sd (microSD)."""
        monkeypatch.setattr(
            "tinyagentos.hardware._run",
            lambda *a, **k: "mmcblk0 1 mmc\n" if "lsblk" in a[0] else "",
        )
        real_exists = Path.exists
        seen_paths = []
        def fake_exists(self):
            s = str(self)
            seen_paths.append(s)
            if s.endswith("/sys/block/mmcblk0boot0") or s.endswith("/sys/block/mmcblk0boot1") or s.endswith("/sys/block/mmcblk0rpmb"):
                return False
            if s.endswith("/sys/block/mmcblk0"):
                return True
            if s.endswith("/sys/block/mmcblk0/device/type"):
                return True
            return real_exists(self)
        monkeypatch.setattr(Path, "exists", fake_exists)
        real_read_text = Path.read_text
        def fake_read_text(self, *args, **kwargs):
            if str(self) == "/sys/block/mmcblk0/device/type":
                return "SD\n"
            return real_read_text(self, *args, **kwargs)
        monkeypatch.setattr(Path, "read_text", fake_read_text)

        import shutil as sh
        real_disk_usage = sh.disk_usage
        def fake_disk_usage(path):
            class DU:
                total = 32 * (1024**3)
                free = 16 * (1024**3)
            return DU()
        monkeypatch.setattr(sh, "disk_usage", fake_disk_usage)

        disk = hardware_mod._detect_disk()
        assert disk.type == "sd"
        for p in seen_paths:
            assert not p.endswith("mmcblkboot0"), f"Wrong path queried: {p}"

    def test_mmcblk_sysfs_type_mmc_is_emmc(self, monkeypatch):
        """mmcblk with sysfs type=MMC -> emmc (fallback when no boot partitions)."""
        monkeypatch.setattr(
            "tinyagentos.hardware._run",
            lambda *a, **k: "mmcblk0 1 mmc\n" if "lsblk" in a[0] else "",
        )
        real_exists = Path.exists
        def fake_exists(self):
            s = str(self)
            if "boot0" in s or "rpmb" in s:
                return False
            if s.endswith("/sys/block/mmcblk0"):
                return True
            if s.endswith("/sys/block/mmcblk0/device/type"):
                return True
            return real_exists(self)
        monkeypatch.setattr(Path, "exists", fake_exists)
        real_read_text = Path.read_text
        def fake_read_text(self, *args, **kwargs):
            if str(self) == "/sys/block/mmcblk0/device/type":
                return "MMC\n"
            return real_read_text(self, *args, **kwargs)
        monkeypatch.setattr(Path, "read_text", fake_read_text)

        import shutil as sh
        def fake_disk_usage(path):
            class DU:
                total = 32 * (1024**3)
                free = 16 * (1024**3)
            return DU()
        monkeypatch.setattr(sh, "disk_usage", fake_disk_usage)

        disk = hardware_mod._detect_disk()
        assert disk.type == "emmc"

    def test_mmcblk_probe_permission_denied_falls_back_to_sd(self, monkeypatch):
        """PermissionError on /sys/block/mmcblk0* probes falls back to sd."""
        monkeypatch.setattr(
            "tinyagentos.hardware._run",
            lambda *a, **k: "mmcblk0 1 mmc\n" if "lsblk" in a[0] else "",
        )
        real_exists = Path.exists
        def fake_exists(self):
            s = str(self)
            if s.startswith("/sys/block/mmcblk0"):
                raise PermissionError(f"[Errno 13] Permission denied: '{s}'")
            return real_exists(self)
        monkeypatch.setattr(Path, "exists", fake_exists)

        import shutil as sh
        def fake_disk_usage(path):
            class DU:
                total = 32 * (1024**3)
                free = 16 * (1024**3)
            return DU()
        monkeypatch.setattr(sh, "disk_usage", fake_disk_usage)

        disk = hardware_mod._detect_disk()
        assert disk.type == "sd"


class TestRecommendedFrameworkProperty:
    def test_low_ram_returns_picoclaw(self):
        profile = HardwareProfile(ram_mb=4096)
        assert profile.recommended_framework == "picoclaw"

    def test_snapped_8gb_returns_picoclaw(self):
        profile = HardwareProfile(ram_mb=7800)
        assert profile.recommended_framework == "picoclaw"

    def test_high_ram_returns_default(self):
        profile = HardwareProfile(ram_mb=16384)
        assert profile.recommended_framework == "hermes"

    def test_mobile_device_class_returns_picoclaw_even_with_high_ram(self):
        profile = HardwareProfile(ram_mb=16384, device_class="mobile")
        assert profile.recommended_framework == "picoclaw"

    def test_none_device_class_with_high_ram_returns_default(self):
        profile = HardwareProfile(ram_mb=16384, device_class=None)
        assert profile.recommended_framework == "hermes"

    def test_unknown_device_class_with_high_ram_returns_default(self):
        profile = HardwareProfile(ram_mb=16384, device_class="desktop")
        assert profile.recommended_framework == "hermes"

    def test_unknown_device_class_with_low_ram_returns_picoclaw(self):
        profile = HardwareProfile(ram_mb=4096, device_class="desktop")
        assert profile.recommended_framework == "picoclaw"


class TestWsl2AmdDetection:
    """WSL2 exposes AMD GPUs via /dev/dxg + rocminfo (taOS #3228)."""

    def test_detects_amd_via_rocminfo(self, monkeypatch):
        import tinyagentos.hardware as hw
        monkeypatch.setattr(hw, "_detect_wsl", lambda: True)
        monkeypatch.setattr(hw.Path, "exists", lambda self: str(self) == "/dev/dxg")
        rocminfo_out = (
            "ROCkets\n"
            "  Name:                    gfx1100\n"
            "  Marketing Name:          AMD Radeon RX 7900 XTX\n"
            "  Vendor Name:             AMD\n"
        )
        monkeypatch.setattr(hw, "_run", lambda cmd: rocminfo_out if cmd == ["rocminfo"] else "")
        model, vram = hw._detect_amd_via_wsl()
        assert model == "AMD Radeon RX 7900 XTX"
        assert vram > 0  # RX 7900 XTX is in the VRAM table

    def test_selects_gpu_agent_when_cpu_agent_comes_first(self, monkeypatch):
        import tinyagentos.hardware as hw
        monkeypatch.setattr(hw, "_detect_wsl", lambda: True)
        monkeypatch.setattr(hw.Path, "exists", lambda self: str(self) == "/dev/dxg")
        rocminfo_out = (
            "=====================\n"
            "HSA Agents\n"
            "=====================\n"
            "Agent 1\n"
            "---------------------\n"
            "  Name:                    AMD Ryzen 9 7950X 16-Core Processor\n"
            "  Marketing Name:          AMD Ryzen 9 7950X 16-Core Processor\n"
            "  Vendor Name:             CPU\n"
            "  Device Type:             CPU\n"
            "\n"
            "Agent 2\n"
            "---------------------\n"
            "  Name:                    gfx1100\n"
            "  Marketing Name:          AMD Radeon RX 7900 XTX\n"
            "  Vendor Name:             AMD\n"
            "  Device Type:             GPU\n"
        )
        monkeypatch.setattr(hw, "_run", lambda cmd: rocminfo_out if cmd == ["rocminfo"] else "")
        model, vram = hw._detect_amd_via_wsl()
        assert model == "AMD Radeon RX 7900 XTX"
        assert vram == 24576

    def test_wsl_rocm_does_not_imply_vulkan(self, monkeypatch):
        import tinyagentos.hardware as hw
        monkeypatch.setattr(hw.platform, "system", lambda: "Linux")
        monkeypatch.setattr(hw, "_detect_nvidia_via_proc", lambda: ("", False))
        monkeypatch.setattr(hw, "_detect_amd_via_wsl",
                            lambda: ("AMD Radeon RX 7900 XTX", 24576))
        monkeypatch.setattr(hw, "_run", lambda cmd: "")
        monkeypatch.setattr(hw.Path, "exists", lambda self: False)
        monkeypatch.setattr(hw.shutil, "which", lambda name: None)
        gpu = hw._detect_gpu()
        assert gpu.type == "amd"
        assert gpu.rocm is True
        assert gpu.vulkan is False

    def test_returns_empty_when_not_wsl(self, monkeypatch):
        import tinyagentos.hardware as hw
        monkeypatch.setattr(hw, "_detect_wsl", lambda: False)
        model, vram = hw._detect_amd_via_wsl()
        assert model == ""
        assert vram == 0

    def test_returns_empty_when_no_dxg(self, monkeypatch):
        import tinyagentos.hardware as hw
        monkeypatch.setattr(hw, "_detect_wsl", lambda: True)
        monkeypatch.setattr(hw.Path, "exists", lambda self: False)
        model, vram = hw._detect_amd_via_wsl()
        assert model == ""
        assert vram == 0


class TestIntelGpuName:
    """Intel GPU model names come from pci.ids, never raw PCI ids."""

    def _stub_pci_ids(self, monkeypatch, tmp_path, contents):
        pci = tmp_path / "pci.ids"
        pci.write_text(contents)
        monkeypatch.setattr(hardware_mod, "_PCI_IDS_PATHS", (pci,))

    def test_pci_ids_lookup_shows_marketing_name(self, monkeypatch, tmp_path):
        self._stub_pci_ids(
            monkeypatch,
            tmp_path,
            "8086  Intel Corporation\n\t4c8a  RocketLake-S GT1 [UHD Graphics 750]\n",
        )
        assert hardware_mod._pci_model_name("0x8086", "0x4c8a") == "Intel UHD Graphics 750"

    def test_unknown_device_returns_empty(self, monkeypatch, tmp_path):
        self._stub_pci_ids(
            monkeypatch,
            tmp_path,
            "8086  Intel Corporation\n\t0002  Some other card\n",
        )
        assert hardware_mod._pci_model_name("0x8086", "0x4c8a") == ""

    def test_detect_intel_gpus_uses_resolved_name(self, monkeypatch, tmp_path):
        import tinyagentos.hardware as hw

        monkeypatch.setattr(hw.platform, "system", lambda: "Linux")
        self._stub_pci_ids(
            monkeypatch,
            tmp_path,
            "8086  Intel Corporation\n\t4c8a  RocketLake-S GT1 [UHD Graphics 750]\n",
        )

        real_safe = hw._path_exists_safe
        def fake_path_exists_safe(p):
            s = str(p)
            if "/proc/driver/nvidia" in s:
                return False
            if "/sys/class/misc/mali0" in s:
                return False
            return real_safe(p)

        monkeypatch.setattr(hw, "_path_exists_safe", fake_path_exists_safe)

        original_exists = hw.Path.exists
        def fake_exists(self):
            s = str(self)
            if s in (
                "/sys/class/drm",
                "/sys/class/drm/card0",
                "/sys/class/drm/card0/device",
                "/sys/class/drm/card0/device/vendor",
                "/sys/class/drm/card0/device/device",
            ):
                return True
            return original_exists(self)
        monkeypatch.setattr(hw.Path, "exists", fake_exists)

        device_path = hw.Path("/sys/class/drm/card0/device")
        original_truediv = hw.Path.__truediv__
        def mock_truediv(self, other):
            if str(self) == "/sys/class/drm/card0" and other == "device":
                return device_path
            return original_truediv(self, other)
        monkeypatch.setattr(hw.Path, "__truediv__", mock_truediv)

        original_resolve = hw.Path.resolve
        def fake_resolve(self, *args, **kwargs):
            if str(self) == "/sys/class/drm/card0/device":
                return hw.Path("/sys/devices/pci0000:00/0000:00:02.0/drm/card0/device")
            return original_resolve(self, *args, **kwargs)
        monkeypatch.setattr(hw.Path, "resolve", fake_resolve)

        original_read_text = hw.Path.read_text
        def fake_read_text(self, *args, **kwargs):
            s = str(self)
            if s.endswith("/device/vendor"):
                return "0x8086\n"
            if s.endswith("/device/device"):
                return "0x4c8a\n"
            return original_read_text(self, *args, **kwargs)
        monkeypatch.setattr(hw.Path, "read_text", fake_read_text)

        original_glob = hw.Path.glob
        def fake_glob(self, pattern):
            if str(self) == "/sys/class/drm" and pattern == "card*":
                return iter([hw.Path("/sys/class/drm/card0")])
            return original_glob(self, pattern)
        monkeypatch.setattr(hw.Path, "glob", fake_glob)

        gpus = hw._detect_intel_gpus()
        assert len(gpus) == 1
        assert gpus[0][0].model == "Intel UHD Graphics 750"
        assert gpus[0][0].vram_mb == 0
        assert gpus[0][1] == "0000:00:02.0"

    def test_detect_intel_gpus_missing_pci_ids_fallback(self, monkeypatch, tmp_path):
        import tinyagentos.hardware as hw

        monkeypatch.setattr(hw.platform, "system", lambda: "Linux")
        # No pci.ids file at all.
        monkeypatch.setattr(hw, "_PCI_IDS_PATHS", (tmp_path / "does-not-exist.ids",))

        real_safe = hw._path_exists_safe
        def fake_path_exists_safe(p):
            s = str(p)
            if "/proc/driver/nvidia" in s:
                return False
            if "/sys/class/misc/mali0" in s:
                return False
            return real_safe(p)

        monkeypatch.setattr(hw, "_path_exists_safe", fake_path_exists_safe)

        original_exists = hw.Path.exists
        def fake_exists(self):
            s = str(self)
            if s in (
                "/sys/class/drm",
                "/sys/class/drm/card0",
                "/sys/class/drm/card0/device",
                "/sys/class/drm/card0/device/vendor",
                "/sys/class/drm/card0/device/device",
            ):
                return True
            return original_exists(self)
        monkeypatch.setattr(hw.Path, "exists", fake_exists)

        device_path = hw.Path("/sys/class/drm/card0/device")
        original_truediv = hw.Path.__truediv__
        def mock_truediv(self, other):
            if str(self) == "/sys/class/drm/card0" and other == "device":
                return device_path
            return original_truediv(self, other)
        monkeypatch.setattr(hw.Path, "__truediv__", mock_truediv)

        original_resolve = hw.Path.resolve
        def fake_resolve(self, *args, **kwargs):
            if str(self) == "/sys/class/drm/card0/device":
                return hw.Path("/sys/devices/pci0000:00/0000:00:02.0/drm/card0/device")
            return original_resolve(self, *args, **kwargs)
        monkeypatch.setattr(hw.Path, "resolve", fake_resolve)

        original_read_text = hw.Path.read_text
        def fake_read_text(self, *args, **kwargs):
            s = str(self)
            if s.endswith("/device/vendor"):
                return "0x8086\n"
            if s.endswith("/device/device"):
                return "0x4c8a\n"
            return original_read_text(self, *args, **kwargs)
        monkeypatch.setattr(hw.Path, "read_text", fake_read_text)

        original_glob = hw.Path.glob
        def fake_glob(self, pattern):
            if str(self) == "/sys/class/drm" and pattern == "card*":
                return iter([hw.Path("/sys/class/drm/card0")])
            return original_glob(self, pattern)
        monkeypatch.setattr(hw.Path, "glob", fake_glob)

        gpus = hw._detect_intel_gpus()
        assert len(gpus) == 1
        assert gpus[0][0].model == "Intel integrated graphics"


class TestDetectGpusMulti:
    """Tests for the new _detect_gpus function that returns all GPUs."""

    def test_detect_gpus_returns_multiple_nvidia_cards(self, monkeypatch):
        """_detect_gpus should return a list with all NVIDIA GPUs, ordered by PCI address."""
        import tinyagentos.hardware as hw
        monkeypatch.setattr(hw.platform, "system", lambda: "Linux")

        def fake_path_exists_safe(p):
            s = str(p)
            if "/proc/driver/nvidia/gpus" in s:
                return False
            if s == "/proc/driver/nvidia":
                return False
            if "/sys/class/misc/mali0" in s:
                return False
            if "/sys/class/drm" in s:
                return False
            return hw._path_exists_safe(p)
        monkeypatch.setattr(hw, "_path_exists_safe", fake_path_exists_safe)

        def fake_run(cmd):
            if cmd == ["lspci"]:
                return ""
            if cmd[:2] == ["nvidia-smi", "--query-gpu=index,name,utilization.gpu,clocks.gr,memory.used,memory.total,pci.bus_id"]:
                return """0, NVIDIA GeForce RTX 3090, 45, 1800, 12288, 24576, 00000000:01:00.0
1, NVIDIA GeForce RTX 3060, 12, 1500, 4096, 12288, 00000000:0a:00.0"""
            return ""
        monkeypatch.setattr(hw, "_run", fake_run)
        monkeypatch.setattr(hw.shutil, "which", lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None)

        gpus = hw._detect_gpus()

        assert isinstance(gpus, list)
        assert len(gpus) == 2
        assert gpus[0].type == "nvidia"
        assert "3090" in gpus[0].model
        assert gpus[0].vram_mb == 24576
        assert gpus[1].type == "nvidia"
        assert "3060" in gpus[1].model
        assert gpus[1].vram_mb == 12288

    def test_detect_gpus_nvidia_smi_missing_falls_back_to_proc(self, monkeypatch):
        """When nvidia-smi is missing, _detect_gpus should fall back to /proc/driver/nvidia."""
        import tinyagentos.hardware as hw
        monkeypatch.setattr(hw.platform, "system", lambda: "Linux")
        monkeypatch.setattr(hw.shutil, "which", lambda name: None)

        def fake_path_exists_safe(p):
            s = str(p)
            if "/proc/driver/nvidia/gpus" in s:
                return True
            if s == "/proc/driver/nvidia":
                return True
            # Block WSL detection and DRM
            if "/proc/version" in s:
                return False
            if "/sys/class/drm" in s:
                return False
            return hw._path_exists_safe(p)
        monkeypatch.setattr(hw, "_path_exists_safe", fake_path_exists_safe)

        # Mock iterdir for /proc/driver/nvidia/gpus
        original_iterdir = hw.Path.iterdir
        def fake_iterdir(self):
            if str(self) == "/proc/driver/nvidia/gpus":
                class FakeGPUDir:
                    def __truediv__(self, other):
                        return Path("/proc/driver/nvidia/gpus/0/information")
                return iter([FakeGPUDir()])
            return original_iterdir(self)
        monkeypatch.setattr(hw.Path, "iterdir", fake_iterdir)

        # Mock read_text for the information file
        original_read_text = hw.Path.read_text
        def fake_read_text(self, *args, **kwargs):
            if str(self) == "/proc/driver/nvidia/gpus/0/information":
                return "Model:           NVIDIA GeForce RTX 3080\n"
            return original_read_text(self, *args, **kwargs)
        monkeypatch.setattr(hw.Path, "read_text", fake_read_text)

        def fake_run(cmd):
            return ""
        monkeypatch.setattr(hw, "_run", fake_run)

        gpus = hw._detect_gpus()
        assert len(gpus) == 1
        assert gpus[0].type == "nvidia"
        assert "3080" in gpus[0].model

    def test_detect_gpus_no_nvidia_no_crash(self, monkeypatch):
        """_detect_gpus should not crash when no NVIDIA GPU present."""
        import tinyagentos.hardware as hw
        monkeypatch.setattr(hw.platform, "system", lambda: "Linux")
        monkeypatch.setattr(hw.shutil, "which", lambda name: None)

        def fake_path_exists_safe(p):
            s = str(p)
            if "/proc/driver/nvidia/gpus" in s:
                return False
            if "/sys/class/drm" in s:
                return False
            if "/proc/version" in s:
                return False
            return hw._path_exists_safe(p)
        monkeypatch.setattr(hw, "_path_exists_safe", fake_path_exists_safe)

        def fake_run(cmd):
            return ""
        monkeypatch.setattr(hw, "_run", fake_run)

        gpus = hw._detect_gpus()
        # Should return at least a "none" GPU
        assert isinstance(gpus, list)
        assert len(gpus) >= 1

    def test_hardware_profile_has_gpus_list(self, monkeypatch):
        """HardwareProfile should have a gpus list populated by detect_hardware."""
        import tinyagentos.hardware as hw
        monkeypatch.setattr(hw.platform, "system", lambda: "Linux")
        monkeypatch.setattr(hw.shutil, "which", lambda name: None)

        def fake_path_exists_safe(p):
            s = str(p)
            if "/proc/driver/nvidia/gpus" in s:
                return False
            if "/sys/class/drm" in s:
                return False
            if "/proc/version" in s:
                return False
            # Allow NPU detection paths
            if "/dev/rknpu" in s:
                return False
            if "/sys/kernel/debug/rknpu" in s:
                return False
            if "/sys/class/devfreq/fdab0000.npu" in s:
                return False
            if "/sys/bus/platform/drivers/RKNPU" in s:
                return False
            if "/dev/hailo" in s:
                return False
            if "/dev/apex_" in s:
                return False
            return hw._path_exists_safe(p)
        monkeypatch.setattr(hw, "_path_exists_safe", fake_path_exists_safe)

        def fake_run(cmd):
            return ""
        monkeypatch.setattr(hw, "_run", fake_run)

        # Mock DRM glob to return empty
        original_glob = hw.Path.glob
        def fake_glob(self, pattern):
            if str(self) == "/sys/class/drm" and pattern == "renderD*":
                return iter([])
            return original_glob(self, pattern)
        monkeypatch.setattr(hw.Path, "glob", fake_glob)

        # Mock /dev glob for hailo/coral
        def fake_dev_glob(self, pattern):
            if str(self) == "/dev" and pattern in ("hailo*", "apex_*"):
                return iter([])
            return original_glob(self, pattern)
        monkeypatch.setattr(hw.Path, "glob", fake_dev_glob)

        profile = hw.detect_hardware()
        assert hasattr(profile, "gpus")
        assert isinstance(profile.gpus, list)
        assert len(profile.gpus) >= 1
        # First GPU should match the single gpu field
        assert profile.gpu.type == profile.gpus[0].type
        assert profile.gpu.model == profile.gpus[0].model

    def test_detect_gpus_intel_and_nvidia_pci_order(self, monkeypatch):
        """_detect_gpus should return all GPUs (NVIDIA + Intel) in PCI address order.

        Two NVIDIA cards from fake nvidia-smi plus a sysfs Intel device
        should give three entries sorted by PCI address.
        """
        import tinyagentos.hardware as hw
        monkeypatch.setattr(hw.platform, "system", lambda: "Linux")

        def fake_path_exists_safe(p):
            s = str(p)
            # Block /proc/driver/nvidia so we use nvidia-smi path
            if "/proc/driver/nvidia/gpus" in s:
                return False
            if s == "/proc/driver/nvidia":
                return False
            # Allow Intel DRM sysfs paths under card0/device
            if "/sys/class/drm/card0/device" in s:
                return True
            # Block other GPU types
            if "/sys/class/misc/mali0" in s:
                return False
            if "/proc/version" in s:
                return False
            if "/sys/class/drm" in s and "card" not in s:
                return True
            return hw._path_exists_safe(p)
        monkeypatch.setattr(hw, "_path_exists_safe", fake_path_exists_safe)

        # Mock Path.exists for DRM and device paths
        original_exists = hw.Path.exists
        def fake_exists(self):
            s = str(self)
            if s == "/sys/class/drm":
                return True
            if s == "/sys/class/drm/card0":
                return True
            if s == "/sys/class/drm/card0/device":
                return True
            return original_exists(self)
        monkeypatch.setattr(hw.Path, "exists", fake_exists)

        def fake_run(cmd):
            if cmd == ["lspci"]:
                return ""
            if cmd[:2] == ["nvidia-smi", "--query-gpu=index,name,utilization.gpu,clocks.gr,memory.used,memory.total,pci.bus_id"]:
                # Two NVIDIA cards: 3090 at 01:00.0, 3060 at 0a:00.0
                return (
                    "0, NVIDIA GeForce RTX 3090, 45, 1800, 12288, 24576, 00000000:01:00.0\n"
                    "1, NVIDIA GeForce RTX 3060, 12, 1500, 4096, 12288, 00000000:0a:00.0"
                )
            return ""
        monkeypatch.setattr(hw, "_run", fake_run)
        monkeypatch.setattr(hw.shutil, "which", lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None)

        # Mock Intel GPU sysfs
        original_resolve = hw.Path.resolve
        def fake_resolve(self, *args, **kwargs):
            s = str(self)
            if "card0/device" in s:
                return hw.Path("/sys/devices/pci0000:00/0000:00:02.0/drm/card0/device")
            return original_resolve(self, *args, **kwargs)
        monkeypatch.setattr(hw.Path, "resolve", fake_resolve)

        original_read_text = hw.Path.read_text
        def fake_read_text(self, *args, **kwargs):
            s = str(self)
            if "vendor" in s:
                return "0x8086\n"
            if "device" in s and "subsystem" not in s:
                return "0x4680\n"
            if "subsystem_device" in s:
                return "0x1234\n"
            return original_read_text(self, *args, **kwargs)
        monkeypatch.setattr(hw.Path, "read_text", fake_read_text)

        # Mock DRM glob to return card0 (Intel)
        # Patch __truediv__ globally so card / "device" returns the right path
        device_path = hw.Path("/sys/class/drm/card0/device")
        original_truediv = hw.Path.__truediv__
        def mock_truediv(self, other):
            if str(self) == "/sys/class/drm/card0" and other == "device":
                return device_path
            return original_truediv(self, other)
        monkeypatch.setattr(hw.Path, "__truediv__", mock_truediv)

        original_glob = hw.Path.glob
        def fake_glob(self, pattern):
            if str(self) == "/sys/class/drm" and pattern == "card*":
                return iter([hw.Path("/sys/class/drm/card0")])
            return original_glob(self, pattern)
        monkeypatch.setattr(hw.Path, "glob", fake_glob)

        gpus = hw._detect_gpus()

        assert isinstance(gpus, list)
        assert len(gpus) == 3
        # PCI order: 00:02.0 (Intel) < 01:00.0 (3090) < 0a:00.0 (3060)
        assert gpus[0].type == "intel"
        assert gpus[1].type == "nvidia"
        assert "3090" in gpus[1].model
        assert gpus[2].type == "nvidia"
        assert "3060" in gpus[2].model

    def test_gpu_equals_first_gpus(self, monkeypatch):
        """The single gpu field should equal the first entry in gpus list."""
        import tinyagentos.hardware as hw
        monkeypatch.setattr(hw.platform, "system", lambda: "Linux")
        monkeypatch.setattr(hw.shutil, "which", lambda name: None)

        def fake_path_exists_safe(p):
            s = str(p)
            if "/proc/driver/nvidia/gpus" in s:
                return False
            if "/sys/class/drm" in s:
                return False
            if "/proc/version" in s:
                return False
            # Allow NPU detection paths
            if "/dev/rknpu" in s:
                return False
            if "/sys/kernel/debug/rknpu" in s:
                return False
            if "/sys/class/devfreq/fdab0000.npu" in s:
                return False
            if "/sys/bus/platform/drivers/RKNPU" in s:
                return False
            if "/dev/hailo" in s:
                return False
            if "/dev/apex_" in s:
                return False
            return hw._path_exists_safe(p)
        monkeypatch.setattr(hw, "_path_exists_safe", fake_path_exists_safe)

        def fake_run(cmd):
            return ""
        monkeypatch.setattr(hw, "_run", fake_run)

        # Mock DRM glob to return empty
        original_glob = hw.Path.glob
        def fake_glob(self, pattern):
            if str(self) == "/sys/class/drm" and pattern == "renderD*":
                return iter([])
            return original_glob(self, pattern)
        monkeypatch.setattr(hw.Path, "glob", fake_glob)

        # Mock /dev glob for hailo/coral
        def fake_dev_glob(self, pattern):
            if str(self) == "/dev" and pattern in ("hailo*", "apex_*"):
                return iter([])
            return original_glob(self, pattern)
        monkeypatch.setattr(hw.Path, "glob", fake_dev_glob)

        profile = hw.detect_hardware()
        assert profile.gpu.type == profile.gpus[0].type
        assert profile.gpu.model == profile.gpus[0].model
        assert profile.gpu.vram_mb == profile.gpus[0].vram_mb
        assert profile.gpu.cuda == profile.gpus[0].cuda
        assert profile.gpu.vulkan == profile.gpus[0].vulkan
        assert profile.gpu.rocm == profile.gpus[0].rocm
