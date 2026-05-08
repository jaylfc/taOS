# tinyagentos/hardware.py
from __future__ import annotations

import json
import platform
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class CpuInfo:
    arch: str = ""
    model: str = ""
    cores: int = 0
    soc: str = ""


@dataclass
class NpuInfo:
    type: str = "none"      # rknpu | hailo | coral | qualcomm | none
    device: str = ""
    tops: int = 0
    cores: int = 0


@dataclass
class GpuInfo:
    type: str = "none"      # nvidia | amd | mali | intel | none
    model: str = ""
    vram_mb: int = 0
    vulkan: bool = False
    cuda: bool = False
    rocm: bool = False


@dataclass
class DiskInfo:
    total_gb: int = 0
    free_gb: int = 0
    type: str = ""           # emmc | sd | nvme | ssd | hdd


@dataclass
class OsInfo:
    distro: str = ""
    version: str = ""
    kernel: str = ""


# Canonical RAM buckets covering the SBC / desktop / workstation range.
# Boards reserve a slice for kernel/GPU so kernel-reported ram_mb is always
# slightly below the marketed capacity (a "16 GB" Pi reports ~15.6 GB);
# floor-dividing produced an `arm-npu-15gb` tier that didn't match any
# catalog manifest using `arm-npu-16gb`. Snap to the closest bucket.
_CANONICAL_RAM_GB: tuple[int, ...] = (
    1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512,
)


def _snap_ram_to_canonical_gb(ram_mb: int) -> int:
    """Map kernel-reported MB to the closest canonical bucket in GB.

    ``ram_mb <= 0`` returns 1 to keep `profile_id` parseable while signalling
    "unknown" via everything else (`cpu`, `npu.type=="none"`, etc.)."""
    if ram_mb <= 0:
        return 1
    actual_gb = ram_mb / 1024
    return min(_CANONICAL_RAM_GB, key=lambda b: abs(b - actual_gb))


@dataclass
class HardwareProfile:
    cpu: CpuInfo = field(default_factory=CpuInfo)
    ram_mb: int = 0
    npu: NpuInfo = field(default_factory=NpuInfo)
    gpu: GpuInfo = field(default_factory=GpuInfo)
    disk: DiskInfo = field(default_factory=DiskInfo)
    os: OsInfo = field(default_factory=OsInfo)

    @property
    def profile_id(self) -> str:
        arch = "arm" if self.cpu.arch in ("aarch64", "armv7l") else "x86"
        if self.npu.type != "none":
            accel = "npu"
        elif self.gpu.cuda:
            accel = "cuda"
        elif self.gpu.rocm:
            accel = "rocm"
        elif self.gpu.vulkan:
            accel = "vulkan"
        else:
            accel = "cpu"
        ram_gb = _snap_ram_to_canonical_gb(self.ram_mb)
        return f"{arch}-{accel}-{ram_gb}gb"

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data["profile_id"] = self.profile_id
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> HardwareProfile:
        data = json.loads(path.read_text())
        data.pop("profile_id", None)
        return cls(
            cpu=CpuInfo(**data.get("cpu", {})),
            ram_mb=data.get("ram_mb", 0),
            npu=NpuInfo(**data.get("npu", {})),
            gpu=GpuInfo(**data.get("gpu", {})),
            disk=DiskInfo(**data.get("disk", {})),
            os=OsInfo(**data.get("os", {})),
        )


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def _detect_cpu() -> CpuInfo:
    arch = platform.machine()
    cores = 0
    model = ""
    soc = ""
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text()
        for line in cpuinfo.split("\n"):
            if line.startswith("processor"):
                cores += 1
            if "model name" in line.lower() or "hardware" in line.lower():
                model = line.split(":")[-1].strip()
        # Detect SoC for ARM
        dt_model = Path("/proc/device-tree/model")
        if dt_model.exists():
            soc_str = dt_model.read_text().strip("\x00").lower()
            if "rk3588" in soc_str:
                soc = "rk3588"
            elif "rk3576" in soc_str:
                soc = "rk3576"
            elif "bcm2712" in soc_str:
                soc = "bcm2712"
            elif "bcm2711" in soc_str or "raspberry pi 4" in soc_str:
                soc = "bcm2711"
    except OSError:
        pass
    # macOS / Apple Silicon detection
    if platform.system() == "Darwin":
        try:
            import subprocess
            chip_info = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                        capture_output=True, text=True, timeout=5).stdout.strip()
            if chip_info:
                model = chip_info
            if "apple" in chip_info.lower() or arch == "arm64":
                # Detect M-series chip
                for m in ["m5", "m4", "m3", "m2", "m1"]:
                    if m in chip_info.lower():
                        soc = m
                        break
                if not soc and arch == "arm64":
                    soc = "apple-silicon"
        except Exception:
            pass
    if not cores:
        import os
        cores = os.cpu_count() or 1
    return CpuInfo(arch=arch, model=model, cores=cores, soc=soc)


def _detect_ram() -> int:
    try:
        meminfo = Path("/proc/meminfo").read_text()
        for line in meminfo.split("\n"):
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                return kb // 1024
    except OSError:
        pass
    return 0


def _path_exists_safe(p: Path) -> bool:
    """Path.exists() raises PermissionError on paths the caller can't
    stat — e.g. /sys/kernel/debug/rknpu/load when running unprivileged.
    Treat denied as 'not present' so detection on non-Rockchip hosts
    doesn't crash the worker. Same fix applies to any other privileged
    path probe."""
    try:
        return p.exists()
    except (PermissionError, OSError):
        return False


def _drm_has_rknpu() -> bool:
    """Return True if any /sys/class/drm renderD* node binds to the RKNPU driver."""
    try:
        for node in Path("/sys/class/drm").glob("renderD*"):
            driver_link = node / "device" / "driver"
            try:
                if driver_link.resolve().name == "RKNPU":
                    return True
            except (OSError, RuntimeError):
                continue
    except OSError:
        pass
    return False


def _detect_npu() -> NpuInfo:
    # Rockchip RKNPU — detect via multiple paths (driver exposes different interfaces per board)
    rknpu_paths = [
        Path("/dev/rknpu"),
        Path("/sys/kernel/debug/rknpu/load"),  # debugfs — most reliable detection
        Path("/sys/class/devfreq/fdab0000.npu"),  # RK3588 NPU devfreq node
        Path("/sys/bus/platform/drivers/RKNPU"),  # platform-driver bind dir on modern BSP kernels
    ]
    # DRM render-node fallback: modern RK3588 BSP exposes the NPU as a render
    # node whose device/driver symlink resolves to RKNPU. Catches hosts where
    # devfreq isn't exposed and the platform-drivers dir isn't readable.
    if any(_path_exists_safe(p) for p in rknpu_paths) or _drm_has_rknpu():
        # Detect SoC variant — RK3588 has 3 cores at 6 TOPS, RK3576 has 1 core at 6 TOPS,
        # RK3568 has 1 core at 1 TOPS
        soc = ""
        try:
            model = Path("/proc/device-tree/model").read_text(errors="replace").lower()
            if "rk3588" in model:
                return NpuInfo(type="rknpu", device="rk3588", tops=6, cores=3)
            if "rk3576" in model:
                return NpuInfo(type="rknpu", device="rk3576", tops=6, cores=1)
            if "rk3568" in model:
                return NpuInfo(type="rknpu", device="rk3568", tops=1, cores=1)
        except (OSError, ValueError):
            pass
        # Parse NPU load file to infer core count if available
        cores = 1
        try:
            load_text = Path("/sys/kernel/debug/rknpu/load").read_text()
            import re as _re
            core_matches = _re.findall(r"Core(\d+):", load_text)
            if core_matches:
                cores = len(core_matches)
        except (OSError, PermissionError):
            pass
        tops = 6 if cores == 3 else (6 if cores == 1 else 1)
        return NpuInfo(type="rknpu", device=soc or "rknpu", tops=tops, cores=cores)
    # Hailo — distinguish 8L (13 TOPS, vision only) from 10H (40 TOPS, LLM capable)
    for p in Path("/dev").glob("hailo*"):
        hailo_info = _run(["lspci", "-d", "1e60:"])
        if "10h" in hailo_info.lower() or "hailo-10" in hailo_info.lower():
            return NpuInfo(type="hailo10h", device=str(p), tops=40, cores=1)
        return NpuInfo(type="hailo", device=str(p), tops=13, cores=1)
    # M5Stack LLM-8850 / Axera AX8850 (24 TOPS, LLM capable, M.2 add-on)
    axera_info = _run(["lspci"])
    if "axera" in axera_info.lower() or "ax8850" in axera_info.lower():
        return NpuInfo(type="axera", device="pcie", tops=24, cores=1)
    # Google Coral
    for p in Path("/dev").glob("apex_*"):
        return NpuInfo(type="coral", device=str(p), tops=4, cores=1)
    return NpuInfo()


def _detect_nvidia_via_proc() -> tuple[str, bool]:
    """Read NVIDIA GPU info from /proc/driver/nvidia.

    Used as a fallback when nvidia-smi isn't installed (containers,
    minimal Debian, headless servers without the userspace utilities).
    Returns (model_string, driver_present).
    """
    info_root = Path("/proc/driver/nvidia/gpus")
    if not _path_exists_safe(info_root):
        return "", False
    try:
        for gpu_dir in info_root.iterdir():
            info_file = gpu_dir / "information"
            if not _path_exists_safe(info_file):
                continue
            for line in info_file.read_text(errors="replace").splitlines():
                if line.lower().startswith("model:"):
                    return line.split(":", 1)[1].strip(), True
        return "", True  # driver present but no model line found
    except (PermissionError, OSError):
        return "", False


# VRAM lookup table for common NVIDIA GPUs. Used when neither nvidia-smi
# nor libnvidia-ml is available (containers, minimal images, restricted
# userspace). PCI BAR sizes can't be used because they're rounded up to
# the nearest power of 2 — a 12GB 3060 reports a 16GB BAR1, which is
# the addressable aperture, not the actual VRAM.
#
# Keys are normalised model substrings (matched as case-insensitive
# substrings against the canonical name from /proc/driver/nvidia or
# nvidia-smi). Order matters: longer / more-specific keys first so
# "3080 Ti" matches before "3080".
_NVIDIA_VRAM_MB = [
    # RTX 50 series (Blackwell, 2025+)
    ("rtx 5090",       32768),
    ("rtx 5080",       16384),
    ("rtx 5070 ti",    16384),
    ("rtx 5070",       12288),
    ("rtx 5060 ti",     8192),
    ("rtx 5060",        8192),
    # RTX 40 series (Ada Lovelace)
    ("rtx 4090",       24576),
    ("rtx 4080 super", 16384),
    ("rtx 4080",       16384),
    ("rtx 4070 ti super", 16384),
    ("rtx 4070 super", 12288),
    ("rtx 4070 ti",    12288),
    ("rtx 4070",       12288),
    ("rtx 4060 ti",     8192),
    ("rtx 4060",        8192),
    # RTX 30 series (Ampere)
    ("rtx 3090 ti",    24576),
    ("rtx 3090",       24576),
    ("rtx 3080 ti",    12288),
    ("rtx 3080",       10240),  # 10 GB common variant; 12 GB exists too
    ("rtx 3070 ti",     8192),
    ("rtx 3070",        8192),
    ("rtx 3060 ti",     8192),
    ("rtx 3060",       12288),  # 12 GB common variant; 8 GB exists too
    ("rtx 3050",        8192),
    # RTX 20 series (Turing)
    ("rtx 2080 ti",    11264),
    ("rtx 2080 super",  8192),
    ("rtx 2080",        8192),
    ("rtx 2070 super",  8192),
    ("rtx 2070",        8192),
    ("rtx 2060 super",  8192),
    ("rtx 2060",        6144),
    # GTX 16 / 10 series
    ("gtx 1660 ti",     6144),
    ("gtx 1660 super",  6144),
    ("gtx 1660",        6144),
    ("gtx 1650",        4096),
    ("gtx 1080 ti",    11264),
    ("gtx 1080",        8192),
    ("gtx 1070 ti",     8192),
    ("gtx 1070",        8192),
    ("gtx 1060",        6144),
    # Datacenter / workstation
    ("h100",           81920),
    ("a100 80",        81920),
    ("a100",           40960),
    ("l40s",           49152),
    ("l40",            49152),
    ("l4",             24576),
    ("a40",            49152),
    ("a30",            24576),
    ("a10",            24576),
    ("a6000",          49152),
    ("a5000",          24576),
    ("a4500",          20480),
    ("a4000",          16384),
    ("a2000",           6144),
    # Older Tesla
    ("tesla v100 32",  32768),
    ("tesla v100",     16384),
    ("tesla t4",       16384),
    ("tesla p100 16",  16384),
    ("tesla p100",     12288),
    ("tesla p40",      24576),
    ("tesla p4",        8192),
]


def _nvidia_vram_for_model(model: str) -> int:
    """Return known VRAM in MB for a NVIDIA GPU model name, or 0 if unknown.

    Used as a final fallback when neither nvidia-smi nor libnvidia-ml
    can give us the actual figure. The lookup is approximate — if the
    user has a non-standard variant (e.g. 8 GB 3060 vs 12 GB 3060),
    install nvidia-smi for exact reporting.
    """
    if not model:
        return 0
    needle = model.lower()
    for key, mb in _NVIDIA_VRAM_MB:
        if key in needle:
            return mb
    return 0


def _detect_gpu() -> GpuInfo:
    gpu = GpuInfo()
    # Apple Silicon — unified memory acts as VRAM, MLX-accelerated
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        gpu.type = "apple"
        gpu.model = "Apple Silicon (unified memory)"
        # Unified memory = total RAM is available as VRAM for MLX
        try:
            import subprocess
            mem_bytes = int(subprocess.run(["sysctl", "-n", "hw.memsize"],
                                           capture_output=True, text=True, timeout=5).stdout.strip())
            gpu.vram_mb = mem_bytes // (1024 * 1024)
        except Exception:
            gpu.vram_mb = 0
        gpu.vulkan = False
        gpu.cuda = False
        gpu.rocm = False
        return gpu

    # Linux NVIDIA detection — preferred order:
    # 1. /proc/driver/nvidia (kernel module + driver present, works in
    #    containers without nvidia-smi userspace utilities)
    # 2. lspci as fallback for hosts where /proc/driver/nvidia isn't
    #    available (no driver loaded yet, or nouveau-only)
    proc_model, proc_driver_present = _detect_nvidia_via_proc()
    if proc_driver_present:
        gpu.type = "nvidia"
        gpu.model = proc_model or "NVIDIA GPU (unknown model)"
        # Driver present implies CUDA + Vulkan are usable. We can't
        # query exact VRAM without nvidia-smi or pynvml, so fall back
        # to a known-cards lookup table keyed by the model name. If
        # the card isn't in the table, vram_mb stays 0 and the
        # controller treats that as 'unknown' rather than 'no GPU'.
        gpu.cuda = True
        gpu.vulkan = True
        gpu.vram_mb = _nvidia_vram_for_model(gpu.model)
        return gpu

    lspci = _run(["lspci"])
    if "NVIDIA" in lspci.upper():
        gpu.type = "nvidia"
        for line in lspci.split("\n"):
            if "NVIDIA" in line.upper() and ("VGA" in line or "3D" in line):
                gpu.model = line.split(":")[-1].strip()
                break
        # No /proc/driver/nvidia means the kernel module isn't loaded,
        # so cuda/vulkan are not actually available even though the
        # device exists. Fall back to nvidia-smi for confirmation.
        gpu.cuda = shutil.which("nvidia-smi") is not None
        gpu.vulkan = gpu.cuda
    elif "AMD" in lspci.upper() and "VGA" in lspci.upper():
        gpu.type = "amd"
        for line in lspci.split("\n"):
            if "AMD" in line.upper() and "VGA" in line:
                gpu.model = line.split(":")[-1].strip()
                break
        gpu.rocm = Path("/opt/rocm").exists()
        gpu.vulkan = gpu.rocm
    else:
        # Check for integrated Mali (ARM) — multiple detection paths
        mali_found = False
        # Path 1: /sys/class/misc/mali0 (proprietary Mali driver)
        if Path("/sys/class/misc/mali0").exists():
            mali_found = True
            # Try to get the specific Mali variant from device tree
            compat_path = Path("/proc/device-tree/gpu@fb000000/compatible")
            if compat_path.exists():
                compat = compat_path.read_text(errors="replace").strip().strip("\x00")
                # e.g. "arm,mali-bifrost" → "Mali-Bifrost"
                if "mali" in compat.lower():
                    variant = compat.split(",")[-1].replace("mali-", "Mali-").replace("mali", "Mali")
                    gpu.type = "mali"
                    gpu.model = f"{variant} (integrated)"
            if not gpu.model:
                gpu.type = "mali"
                gpu.model = "Mali (integrated)"
        # Path 2: /sys/class/drm/card*/device/driver contains mali or panfrost
        if not mali_found:
            drm_path = Path("/sys/class/drm")
            if drm_path.exists():
                for card in drm_path.glob("card*/device/driver"):
                    driver = card.resolve().name if card.exists() else ""
                    if "mali" in driver.lower() or "panfrost" in driver.lower():
                        gpu.type = "mali"
                        gpu.model = "Mali (integrated)"
                        mali_found = True
                        break
    # Check Vulkan availability
    if not gpu.vulkan and shutil.which("vulkaninfo"):
        result = _run(["vulkaninfo", "--summary"])
        if "GPU" in result and "ERROR" not in result:
            gpu.vulkan = True
    return gpu


def _detect_disk() -> DiskInfo:
    try:
        import shutil as sh
        usage = sh.disk_usage("/")
        total_gb = usage.total // (1024 ** 3)
        free_gb = usage.free // (1024 ** 3)
    except OSError:
        total_gb = 0
        free_gb = 0
    # Detect storage type
    dtype = ""
    lsblk = _run(["lsblk", "-dno", "NAME,ROTA,TRAN"])
    for line in lsblk.split("\n"):
        parts = line.split()
        if len(parts) >= 2:
            name = parts[0]
            # Skip virtual/non-disk devices
            if any(name.startswith(skip) for skip in ("zram", "loop", "mtdblock", "ram")):
                continue
            rota = parts[1] if len(parts) > 1 else "1"
            tran = parts[2] if len(parts) > 2 else ""
            if "nvme" in tran or "nvme" in name:
                dtype = "nvme"
                break
            elif "mmc" in parts[0]:
                dtype = "emmc" if "mmcblk" in parts[0] else "sd"
                break
            elif rota == "0":
                dtype = "ssd"
                break
            elif rota == "1":
                dtype = "hdd"
    return DiskInfo(total_gb=total_gb, free_gb=free_gb, type=dtype)


def _detect_os() -> OsInfo:
    import os as _os
    distro = ""
    version = ""
    try:
        for line in Path("/etc/os-release").read_text().split("\n"):
            if line.startswith("ID="):
                distro = line.split("=", 1)[1].strip('"')
            elif line.startswith("VERSION_ID="):
                version = line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    kernel = platform.release()
    # Detect Android/Termux
    if "TERMUX_VERSION" in _os.environ or "com.termux" in str(Path.home()):
        distro = "android-termux"
    return OsInfo(distro=distro, version=version, kernel=kernel)


def detect_hardware() -> HardwareProfile:
    """Detect all hardware and return a profile."""
    return HardwareProfile(
        cpu=_detect_cpu(),
        ram_mb=_detect_ram(),
        npu=_detect_npu(),
        gpu=_detect_gpu(),
        disk=_detect_disk(),
        os=_detect_os(),
    )


def get_hardware_profile(cache_path: Path) -> HardwareProfile:
    """Detect hardware on every startup. Cache is a fallback for cases where
    detection raises (broken /sys access, unsupported platform), not the
    primary read path. Re-probing on startup means newly-installed
    drivers/tooling are picked up without a manual cache delete."""
    try:
        profile = detect_hardware()
    except Exception:
        if cache_path.exists():
            return HardwareProfile.load(cache_path)
        raise
    profile.save(cache_path)
    return profile
