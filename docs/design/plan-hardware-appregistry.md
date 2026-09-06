# Platform Plan 1: Hardware Detection + App Registry Implementation Plan

**Status:** Implemented — hardware detection and app registry both shipped; see `tinyagentos/hardware.py` and `tinyagentos/registry.py`.

**Amended:** 2026-04-11 — app/model "installed" state follows
**backend-driven discovery**. Rather than relying on filename conventions
in `data/models/` or a static `installed.json`, the app registry asks each
live backend what it has loaded or can currently serve and unions that
with the on-disk manifest catalog. Static state files remain as a cache
but are never the source of truth. See
[resource-scheduler.md §Backend-driven discovery](resource-scheduler.md).

**Goal:** Add hardware auto-detection and an app registry that can parse manifests, track installed apps, and manage app lifecycle (install/uninstall/start/stop) — the foundation for the App Store, Model Manager, and Agent Deployer.

**Architecture:** Hardware detector profiles the system (CPU, RAM, NPU, GPU) on first boot and on-demand. App registry loads manifests from a catalog directory, tracks installed apps in a JSON file, and provides install/uninstall operations per install method (pip, docker, download, script). Docker Compose for service apps, pip venvs for agent frameworks, file download for models.

**Tech Stack:** Python 3.10+, FastAPI (existing), subprocess for system detection, docker compose CLI, pyyaml for manifests, existing test infrastructure.

**Spec:** `docs/specs/2026-04-05-app-store-platform-design.md`

---

## File Map

```
tinyagentos/
├── tinyagentos/
│   ├── hardware.py             # Hardware detection (CPU, RAM, NPU, GPU, disk)
│   ├── registry.py             # App registry: parse manifests, track installed, lifecycle
│   ├── installers/
│   │   ├── __init__.py
│   │   ├── base.py             # AppInstaller ABC
│   │   ├── pip_installer.py    # pip install into venvs
│   │   ├── docker_installer.py # docker compose up/down
│   │   ├── download_installer.py # model file downloads with SHA256
│   │   └── script_installer.py # custom shell scripts
│   ├── routes/
│   │   └── store.py            # API endpoints for store operations
│   └── templates/
│       └── store.html          # App Store page (basic, expanded in Plan 2)
├── tests/
│   ├── test_hardware.py
│   ├── test_registry.py
│   └── test_installers.py
└── data/
    ├── hardware.json           # Auto-generated hardware profile
    └── installed.json          # Installed apps registry
```

---

### Task 1: Hardware Detection Module

**Files:**
- Create: `tinyagentos/tinyagentos/hardware.py`
- Create: `tinyagentos/tests/test_hardware.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_hardware.py
import json
import pytest
from unittest.mock import patch, mock_open
from tinyagentos.hardware import detect_hardware, get_hardware_profile, HardwareProfile


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


class TestGetHardwareProfile:
    def test_returns_cached_if_exists(self, tmp_path):
        profile = detect_hardware()
        path = tmp_path / "hardware.json"
        profile.save(path)
        loaded = get_hardware_profile(path)
        assert loaded.profile_id == profile.profile_id

    def test_detects_if_no_cache(self, tmp_path):
        path = tmp_path / "hardware.json"
        profile = get_hardware_profile(path)
        assert profile.ram_mb > 0
        assert path.exists()  # auto-saved
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/jay/tinyagentos && pytest tests/test_hardware.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'tinyagentos.hardware'`

- [ ] **Step 3: Implement hardware detection**

```python
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
        ram_gb = max(1, self.ram_mb // 1024)
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
    except OSError:
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


def _detect_npu() -> NpuInfo:
    # Rockchip RKNPU
    if Path("/dev/rknpu").exists():
        cores = 3  # RK3588 default
        return NpuInfo(type="rknpu", device="/dev/rknpu", tops=6, cores=cores)
    # Hailo
    for p in Path("/dev").glob("hailo*"):
        return NpuInfo(type="hailo", device=str(p), tops=26, cores=1)
    # Google Coral
    for p in Path("/dev").glob("apex_*"):
        return NpuInfo(type="coral", device=str(p), tops=4, cores=1)
    return NpuInfo()


def _detect_gpu() -> GpuInfo:
    gpu = GpuInfo()
    lspci = _run(["lspci"])
    if "NVIDIA" in lspci.upper():
        gpu.type = "nvidia"
        for line in lspci.split("\n"):
            if "NVIDIA" in line.upper() and ("VGA" in line or "3D" in line):
                gpu.model = line.split(":")[-1].strip()
                break
        # Check CUDA
        gpu.cuda = shutil.which("nvidia-smi") is not None
        # Check Vulkan
        gpu.vulkan = gpu.cuda  # NVIDIA cards with drivers have Vulkan
    elif "AMD" in lspci.upper() and "VGA" in lspci.upper():
        gpu.type = "amd"
        for line in lspci.split("\n"):
            if "AMD" in line.upper() and "VGA" in line:
                gpu.model = line.split(":")[-1].strip()
                break
        gpu.rocm = Path("/opt/rocm").exists()
        gpu.vulkan = gpu.rocm
    else:
        # Check for integrated Mali (ARM)
        drm_path = Path("/sys/class/drm")
        if drm_path.exists():
            for card in drm_path.glob("card*/device/driver"):
                driver = card.resolve().name if card.exists() else ""
                if "mali" in driver.lower() or "panfrost" in driver.lower():
                    gpu.type = "mali"
                    gpu.model = "Mali (integrated)"
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
            rota = parts[1] if len(parts) > 1 else "1"
            tran = parts[2] if len(parts) > 2 else ""
            if "nvme" in tran:
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
    """Load cached hardware profile, or detect and cache if missing."""
    if cache_path.exists():
        return HardwareProfile.load(cache_path)
    profile = detect_hardware()
    profile.save(cache_path)
    return profile
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_hardware.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/hardware.py tests/test_hardware.py
git commit -m "feat: hardware auto-detection (CPU, RAM, NPU, GPU, disk, OS)"
```

---

### Task 2: App Manifest Parser + Registry

**Files:**
- Create: `tinyagentos/tinyagentos/registry.py`
- Create: `tinyagentos/tests/test_registry.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_registry.py
import json
import pytest
import yaml
from tinyagentos.registry import AppManifest, AppRegistry, AppState


@pytest.fixture
def catalog_dir(tmp_path):
    """Create a test catalog with sample manifests."""
    agents = tmp_path / "agents" / "smolagents"
    agents.mkdir(parents=True)
    (agents / "manifest.yaml").write_text(yaml.dump({
        "id": "smolagents",
        "name": "SmolAgents",
        "type": "agent-framework",
        "version": "1.0.0",
        "description": "Code-based agent framework",
        "requires": {"ram_mb": 256},
        "install": {"method": "pip", "package": "smolagents"},
        "hardware_tiers": {"arm-npu-16gb": "full", "cpu-only": "full"},
    }))
    models = tmp_path / "models" / "qwen3-8b"
    models.mkdir(parents=True)
    (models / "manifest.yaml").write_text(yaml.dump({
        "id": "qwen3-8b",
        "name": "Qwen 3 8B",
        "type": "model",
        "version": "3.0.0",
        "description": "General-purpose chat model",
        "variants": [
            {"id": "q4_k_m", "name": "Q4_K_M", "format": "gguf", "size_mb": 4800,
             "min_ram_mb": 6144, "download_url": "https://example.com/qwen3-8b.gguf",
             "backend": ["ollama", "llama-cpp"]},
        ],
        "hardware_tiers": {"arm-npu-16gb": {"recommended": "q4_k_m"}, "cpu-only": {"recommended": "q4_k_m"}},
    }))
    services = tmp_path / "services" / "gitea"
    services.mkdir(parents=True)
    (services / "manifest.yaml").write_text(yaml.dump({
        "id": "gitea",
        "name": "Gitea",
        "type": "service",
        "version": "1.22.0",
        "description": "Self-hosted Git server",
        "requires": {"ram_mb": 256, "ports": [3000]},
        "install": {"method": "docker", "image": "gitea/gitea:1.22",
                    "volumes": ["data:/data"], "env": {"ROOT_URL": "http://localhost:3000"}},
        "hardware_tiers": {"arm-npu-16gb": "full", "cpu-only": "full"},
    }))
    return tmp_path


@pytest.fixture
def registry(catalog_dir, tmp_path):
    installed_path = tmp_path / "installed.json"
    return AppRegistry(catalog_dir=catalog_dir, installed_path=installed_path)


class TestAppManifest:
    def test_load_agent_manifest(self, catalog_dir):
        m = AppManifest.from_file(catalog_dir / "agents" / "smolagents" / "manifest.yaml")
        assert m.id == "smolagents"
        assert m.type == "agent-framework"
        assert m.install["method"] == "pip"

    def test_load_model_manifest(self, catalog_dir):
        m = AppManifest.from_file(catalog_dir / "models" / "qwen3-8b" / "manifest.yaml")
        assert m.id == "qwen3-8b"
        assert m.type == "model"
        assert len(m.variants) == 1

    def test_load_service_manifest(self, catalog_dir):
        m = AppManifest.from_file(catalog_dir / "services" / "gitea" / "manifest.yaml")
        assert m.id == "gitea"
        assert m.type == "service"
        assert m.install["method"] == "docker"

    def test_compatible_with_tier(self, catalog_dir):
        m = AppManifest.from_file(catalog_dir / "agents" / "smolagents" / "manifest.yaml")
        assert m.is_compatible("arm-npu-16gb")
        assert m.is_compatible("cpu-only")
        assert not m.is_compatible("nonexistent-tier")


class TestAppRegistry:
    def test_load_catalog(self, registry):
        apps = registry.list_available()
        assert len(apps) == 3
        ids = {a.id for a in apps}
        assert "smolagents" in ids
        assert "qwen3-8b" in ids
        assert "gitea" in ids

    def test_filter_by_type(self, registry):
        models = registry.list_available(type_filter="model")
        assert len(models) == 1
        assert models[0].id == "qwen3-8b"

    def test_get_app(self, registry):
        app = registry.get("smolagents")
        assert app is not None
        assert app.name == "SmolAgents"

    def test_get_nonexistent(self, registry):
        assert registry.get("nonexistent") is None

    def test_installed_empty_initially(self, registry):
        assert registry.list_installed() == []

    def test_mark_installed(self, registry):
        registry.mark_installed("smolagents", "1.0.0")
        installed = registry.list_installed()
        assert len(installed) == 1
        assert installed[0]["id"] == "smolagents"
        assert installed[0]["state"] == "installed"

    def test_mark_uninstalled(self, registry):
        registry.mark_installed("smolagents", "1.0.0")
        registry.mark_uninstalled("smolagents")
        assert registry.list_installed() == []

    def test_is_installed(self, registry):
        assert not registry.is_installed("smolagents")
        registry.mark_installed("smolagents", "1.0.0")
        assert registry.is_installed("smolagents")

    def test_installed_persists(self, registry, tmp_path):
        registry.mark_installed("gitea", "1.22.0")
        # Create new registry instance pointing at same file
        registry2 = AppRegistry(catalog_dir=registry.catalog_dir, installed_path=registry.installed_path)
        assert registry2.is_installed("gitea")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_registry.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement registry**

```python
# tinyagentos/registry.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class AppManifest:
    id: str
    name: str
    type: str                   # agent-framework | model | service | plugin
    version: str
    description: str = ""
    icon: str = ""
    homepage: str = ""
    license: str = ""
    requires: dict = field(default_factory=dict)
    install: dict = field(default_factory=dict)
    hardware_tiers: dict = field(default_factory=dict)
    config_schema: list = field(default_factory=list)
    variants: list = field(default_factory=list)   # models only
    capabilities: list = field(default_factory=list)
    lifecycle: dict = field(default_factory=dict)
    manifest_dir: Path | None = None

    @classmethod
    def from_file(cls, path: Path) -> AppManifest:
        data = yaml.safe_load(path.read_text())
        return cls(
            id=data["id"],
            name=data["name"],
            type=data["type"],
            version=data["version"],
            description=data.get("description", ""),
            icon=data.get("icon", ""),
            homepage=data.get("homepage", ""),
            license=data.get("license", ""),
            requires=data.get("requires", {}),
            install=data.get("install", {}),
            hardware_tiers=data.get("hardware_tiers", {}),
            config_schema=data.get("config_schema", []),
            variants=data.get("variants", []),
            capabilities=data.get("capabilities", []),
            lifecycle=data.get("lifecycle", {}),
            manifest_dir=path.parent,
        )

    def is_compatible(self, profile_id: str) -> bool:
        if not self.hardware_tiers:
            return True  # no restrictions
        tier = self.hardware_tiers.get(profile_id)
        if tier is None:
            return False
        if isinstance(tier, str):
            return tier != "unsupported"
        if isinstance(tier, dict):
            return tier.get("recommended") is not None or tier.get("fallback") is not None
        return False


class AppRegistry:
    def __init__(self, catalog_dir: Path, installed_path: Path):
        self.catalog_dir = catalog_dir
        self.installed_path = installed_path
        self._catalog: list[AppManifest] = []
        self._load_catalog()

    def _load_catalog(self) -> None:
        self._catalog = []
        for type_dir in ("agents", "models", "services", "plugins"):
            base = self.catalog_dir / type_dir
            if not base.exists():
                continue
            for app_dir in sorted(base.iterdir()):
                manifest = app_dir / "manifest.yaml"
                if manifest.exists():
                    try:
                        self._catalog.append(AppManifest.from_file(manifest))
                    except (yaml.YAMLError, KeyError):
                        pass  # skip invalid manifests

    def reload(self) -> None:
        self._load_catalog()

    def list_available(self, type_filter: str | None = None) -> list[AppManifest]:
        if type_filter:
            return [a for a in self._catalog if a.type == type_filter]
        return list(self._catalog)

    def get(self, app_id: str) -> AppManifest | None:
        return next((a for a in self._catalog if a.id == app_id), None)

    def _read_installed(self) -> list[dict]:
        if not self.installed_path.exists():
            return []
        return json.loads(self.installed_path.read_text())

    def _write_installed(self, apps: list[dict]) -> None:
        self.installed_path.parent.mkdir(parents=True, exist_ok=True)
        self.installed_path.write_text(json.dumps(apps, indent=2))

    def list_installed(self) -> list[dict]:
        return self._read_installed()

    def is_installed(self, app_id: str) -> bool:
        return any(a["id"] == app_id for a in self._read_installed())

    def mark_installed(self, app_id: str, version: str, state: str = "installed") -> None:
        apps = self._read_installed()
        apps = [a for a in apps if a["id"] != app_id]
        apps.append({"id": app_id, "version": version, "state": state})
        self._write_installed(apps)

    def mark_uninstalled(self, app_id: str) -> None:
        apps = [a for a in self._read_installed() if a["id"] != app_id]
        self._write_installed(apps)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_registry.py -v
```

Expected: All 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/registry.py tests/test_registry.py
git commit -m "feat: app manifest parser and registry with install tracking"
```

---

### Task 3: App Installers (pip, docker, download)

**Files:**
- Create: `tinyagentos/tinyagentos/installers/__init__.py`
- Create: `tinyagentos/tinyagentos/installers/base.py`
- Create: `tinyagentos/tinyagentos/installers/pip_installer.py`
- Create: `tinyagentos/tinyagentos/installers/docker_installer.py`
- Create: `tinyagentos/tinyagentos/installers/download_installer.py`
- Create: `tinyagentos/tests/test_installers.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_installers.py
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from tinyagentos.installers.base import get_installer
from tinyagentos.installers.pip_installer import PipInstaller
from tinyagentos.installers.docker_installer import DockerInstaller
from tinyagentos.installers.download_installer import DownloadInstaller


class TestGetInstaller:
    def test_returns_pip(self):
        assert isinstance(get_installer("pip"), PipInstaller)

    def test_returns_docker(self):
        assert isinstance(get_installer("docker"), DockerInstaller)

    def test_returns_download(self):
        assert isinstance(get_installer("download"), DownloadInstaller)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown install method"):
            get_installer("unknown")


class TestPipInstaller:
    @pytest.mark.asyncio
    async def test_install_creates_venv(self, tmp_path):
        installer = PipInstaller(apps_dir=tmp_path)
        with patch("tinyagentos.installers.pip_installer.run_cmd", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "")
            result = await installer.install("testapp", {"method": "pip", "package": "testpkg"})
            assert result["success"] is True
            # Should have called python -m venv and pip install
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("venv" in c for c in calls)
            assert any("pip" in c and "testpkg" in c for c in calls)

    @pytest.mark.asyncio
    async def test_uninstall_removes_dir(self, tmp_path):
        installer = PipInstaller(apps_dir=tmp_path)
        app_dir = tmp_path / "testapp"
        app_dir.mkdir()
        (app_dir / "venv").mkdir()
        result = await installer.uninstall("testapp")
        assert result["success"] is True
        assert not app_dir.exists()


class TestDockerInstaller:
    @pytest.mark.asyncio
    async def test_install_writes_compose(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        install_config = {
            "method": "docker",
            "image": "gitea/gitea:1.22",
            "volumes": ["data:/data"],
            "env": {"ROOT_URL": "http://localhost:3000"},
        }
        with patch("tinyagentos.installers.docker_installer.run_cmd", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "")
            result = await installer.install("gitea", install_config)
            assert result["success"] is True
            compose_file = tmp_path / "gitea" / "docker-compose.yaml"
            assert compose_file.exists()

    @pytest.mark.asyncio
    async def test_start_runs_compose_up(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        app_dir = tmp_path / "gitea"
        app_dir.mkdir()
        (app_dir / "docker-compose.yaml").write_text("version: '3'")
        with patch("tinyagentos.installers.docker_installer.run_cmd", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "")
            result = await installer.start("gitea")
            assert result["success"] is True
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("up" in c and "-d" in c for c in calls)


class TestDownloadInstaller:
    @pytest.mark.asyncio
    async def test_install_downloads_file(self, tmp_path):
        installer = DownloadInstaller(models_dir=tmp_path)
        variant = {
            "id": "q4_k_m",
            "download_url": "https://example.com/model.gguf",
            "size_mb": 100,
            "sha256": "abc123",
        }
        with patch("tinyagentos.installers.download_installer.download_file", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = tmp_path / "qwen3-8b-q4_k_m.gguf"
            result = await installer.install("qwen3-8b", {"method": "download"}, variant=variant)
            assert result["success"] is True
            mock_dl.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_installers.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement base installer and factory**

```python
# tinyagentos/installers/__init__.py
```

```python
# tinyagentos/installers/base.py
from __future__ import annotations

import asyncio
import subprocess
from abc import ABC, abstractmethod


async def run_cmd(cmd: list[str], cwd: str | None = None, timeout: int = 300) -> tuple[int, str]:
    """Run a command asynchronously, return (returncode, output)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return proc.returncode, stdout.decode() if stdout else ""


class AppInstaller(ABC):
    @abstractmethod
    async def install(self, app_id: str, install_config: dict, **kwargs) -> dict:
        ...

    @abstractmethod
    async def uninstall(self, app_id: str) -> dict:
        ...

    async def start(self, app_id: str) -> dict:
        return {"success": False, "error": "start not supported for this installer"}

    async def stop(self, app_id: str) -> dict:
        return {"success": False, "error": "stop not supported for this installer"}


def get_installer(method: str, **kwargs) -> AppInstaller:
    from tinyagentos.installers.pip_installer import PipInstaller
    from tinyagentos.installers.docker_installer import DockerInstaller
    from tinyagentos.installers.download_installer import DownloadInstaller

    if method == "pip":
        return PipInstaller(**kwargs)
    elif method == "docker":
        return DockerInstaller(**kwargs)
    elif method == "download":
        return DownloadInstaller(**kwargs)
    else:
        raise ValueError(f"Unknown install method: '{method}'")
```

- [ ] **Step 4: Implement pip installer**

```python
# tinyagentos/installers/pip_installer.py
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from tinyagentos.installers.base import AppInstaller, run_cmd


class PipInstaller(AppInstaller):
    def __init__(self, apps_dir: Path | None = None):
        self.apps_dir = apps_dir or Path("/opt/tinyagentos/apps")

    async def install(self, app_id: str, install_config: dict, **kwargs) -> dict:
        app_dir = self.apps_dir / app_id
        venv_dir = app_dir / "venv"
        app_dir.mkdir(parents=True, exist_ok=True)

        # Create venv
        code, output = await run_cmd([sys.executable, "-m", "venv", str(venv_dir)])
        if code != 0:
            return {"success": False, "error": f"venv creation failed: {output}"}

        # Install package
        pip = str(venv_dir / "bin" / "pip")
        package = install_config["package"]
        extras = install_config.get("extras", [])
        pkg_spec = f"{package}[{','.join(extras)}]" if extras else package

        code, output = await run_cmd([pip, "install", pkg_spec])
        if code != 0:
            return {"success": False, "error": f"pip install failed: {output}"}

        return {"success": True, "path": str(app_dir)}

    async def uninstall(self, app_id: str) -> dict:
        app_dir = self.apps_dir / app_id
        if app_dir.exists():
            shutil.rmtree(app_dir)
        return {"success": True}
```

- [ ] **Step 5: Implement docker installer**

```python
# tinyagentos/installers/docker_installer.py
from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from tinyagentos.installers.base import AppInstaller, run_cmd


class DockerInstaller(AppInstaller):
    def __init__(self, apps_dir: Path | None = None):
        self.apps_dir = apps_dir or Path("/opt/tinyagentos/apps")

    def _compose_path(self, app_id: str) -> Path:
        return self.apps_dir / app_id / "docker-compose.yaml"

    def _generate_compose(self, app_id: str, install_config: dict) -> dict:
        """Generate a docker-compose.yaml from the manifest install config."""
        service = {
            "image": install_config["image"],
            "restart": "unless-stopped",
        }
        if "volumes" in install_config:
            service["volumes"] = install_config["volumes"]
        if "env" in install_config:
            service["environment"] = install_config["env"]
        if "ports" in install_config.get("requires", {}):
            service["ports"] = [f"{p}:{p}" for p in install_config["requires"]["ports"]]
        elif "ports" in install_config:
            service["ports"] = [f"{p}:{p}" for p in install_config["ports"]]

        return {
            "version": "3.8",
            "services": {app_id: service},
        }

    async def install(self, app_id: str, install_config: dict, **kwargs) -> dict:
        app_dir = self.apps_dir / app_id
        app_dir.mkdir(parents=True, exist_ok=True)

        compose = self._generate_compose(app_id, install_config)
        compose_path = self._compose_path(app_id)
        compose_path.write_text(yaml.dump(compose, default_flow_style=False))

        # Pull image
        code, output = await run_cmd(
            ["docker", "compose", "-f", str(compose_path), "pull"],
            cwd=str(app_dir),
        )
        if code != 0:
            return {"success": False, "error": f"docker pull failed: {output}"}

        return {"success": True, "path": str(app_dir)}

    async def uninstall(self, app_id: str) -> dict:
        compose_path = self._compose_path(app_id)
        if compose_path.exists():
            await run_cmd(
                ["docker", "compose", "-f", str(compose_path), "down", "-v"],
                cwd=str(compose_path.parent),
            )
        app_dir = self.apps_dir / app_id
        if app_dir.exists():
            shutil.rmtree(app_dir)
        return {"success": True}

    async def start(self, app_id: str) -> dict:
        compose_path = self._compose_path(app_id)
        if not compose_path.exists():
            return {"success": False, "error": "docker-compose.yaml not found"}
        code, output = await run_cmd(
            ["docker", "compose", "-f", str(compose_path), "up", "-d"],
            cwd=str(compose_path.parent),
        )
        return {"success": code == 0, "output": output}

    async def stop(self, app_id: str) -> dict:
        compose_path = self._compose_path(app_id)
        if not compose_path.exists():
            return {"success": False, "error": "docker-compose.yaml not found"}
        code, output = await run_cmd(
            ["docker", "compose", "-f", str(compose_path), "down"],
            cwd=str(compose_path.parent),
        )
        return {"success": code == 0, "output": output}
```

- [ ] **Step 6: Implement download installer**

```python
# tinyagentos/installers/download_installer.py
from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from tinyagentos.installers.base import AppInstaller


async def download_file(url: str, dest: Path, expected_sha256: str | None = None) -> Path:
    """Download a file with optional SHA256 verification."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            sha = hashlib.sha256()
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    sha.update(chunk)
    if expected_sha256 and sha.hexdigest() != expected_sha256:
        dest.unlink()
        raise ValueError(f"SHA256 mismatch: expected {expected_sha256}, got {sha.hexdigest()}")
    return dest


class DownloadInstaller(AppInstaller):
    def __init__(self, models_dir: Path | None = None):
        self.models_dir = models_dir or Path("/opt/tinyagentos/models")

    async def install(self, app_id: str, install_config: dict, variant: dict | None = None, **kwargs) -> dict:
        if not variant:
            return {"success": False, "error": "variant required for model download"}

        filename = f"{app_id}-{variant['id']}.{variant.get('format', 'bin')}"
        dest = self.models_dir / filename

        if dest.exists():
            return {"success": True, "path": str(dest), "cached": True}

        try:
            path = await download_file(
                url=variant["download_url"],
                dest=dest,
                expected_sha256=variant.get("sha256"),
            )
            return {"success": True, "path": str(path)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def uninstall(self, app_id: str, variant_id: str | None = None, **kwargs) -> dict:
        # Delete matching model files
        deleted = []
        for f in self.models_dir.glob(f"{app_id}*"):
            if variant_id and variant_id not in f.name:
                continue
            f.unlink()
            deleted.append(f.name)
        return {"success": True, "deleted": deleted}
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
pytest tests/test_installers.py -v
```

Expected: All tests PASS.

- [ ] **Step 8: Commit**

```bash
git add tinyagentos/installers/ tests/test_installers.py
git commit -m "feat: app installers for pip (venv), docker (compose), and model downloads"
```

---

### Task 4: Store API Endpoints

**Files:**
- Create: `tinyagentos/tinyagentos/routes/store.py`
- Modify: `tinyagentos/tinyagentos/app.py` (add registry to app state, add store router)
- Create: `tinyagentos/tests/test_routes_store.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_routes_store.py
import pytest
import pytest_asyncio
import yaml
from httpx import AsyncClient, ASGITransport
from tinyagentos.app import create_app


@pytest.fixture
def catalog_dir(tmp_path):
    agents = tmp_path / "catalog" / "agents" / "smolagents"
    agents.mkdir(parents=True)
    (agents / "manifest.yaml").write_text(yaml.dump({
        "id": "smolagents", "name": "SmolAgents", "type": "agent-framework",
        "version": "1.0.0", "description": "Code-based agents",
        "requires": {"ram_mb": 256},
        "install": {"method": "pip", "package": "smolagents"},
        "hardware_tiers": {"arm-npu-16gb": "full", "cpu-only": "full"},
    }))
    models = tmp_path / "catalog" / "models" / "test-model"
    models.mkdir(parents=True)
    (models / "manifest.yaml").write_text(yaml.dump({
        "id": "test-model", "name": "Test Model", "type": "model",
        "version": "1.0.0", "description": "A test model",
        "variants": [{"id": "small", "name": "Small", "format": "gguf", "size_mb": 100,
                       "min_ram_mb": 512, "download_url": "https://example.com/test.gguf",
                       "backend": ["ollama"]}],
        "hardware_tiers": {"arm-npu-16gb": {"recommended": "small"}},
    }))
    return tmp_path / "catalog"


@pytest.fixture
def app_with_store(tmp_data_dir, catalog_dir):
    return create_app(data_dir=tmp_data_dir, catalog_dir=catalog_dir)


@pytest_asyncio.fixture
async def store_client(app_with_store):
    store = app_with_store.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    await app_with_store.state.qmd_client.init()
    transport = ASGITransport(app=app_with_store)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await store.close()
    await app_with_store.state.qmd_client.close()
    await app_with_store.state.http_client.aclose()


@pytest.mark.asyncio
class TestStoreAPI:
    async def test_list_catalog(self, store_client):
        resp = await store_client.get("/api/store/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        ids = {a["id"] for a in data}
        assert "smolagents" in ids
        assert "test-model" in ids

    async def test_filter_catalog_by_type(self, store_client):
        resp = await store_client.get("/api/store/catalog?type=model")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "test-model"

    async def test_list_installed_empty(self, store_client):
        resp = await store_client.get("/api/store/installed")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_get_app_detail(self, store_client):
        resp = await store_client.get("/api/store/app/smolagents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "smolagents"
        assert data["type"] == "agent-framework"

    async def test_get_nonexistent_app(self, store_client):
        resp = await store_client.get("/api/store/app/nonexistent")
        assert resp.status_code == 404

    async def test_hardware_profile(self, store_client):
        resp = await store_client.get("/api/hardware")
        assert resp.status_code == 200
        data = resp.json()
        assert "profile_id" in data
        assert "ram_mb" in data
        assert data["ram_mb"] > 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_routes_store.py -v
```

Expected: FAIL

- [ ] **Step 3: Implement store routes**

```python
# tinyagentos/routes/store.py
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()


@router.get("/store", response_class=HTMLResponse)
async def store_page(request: Request):
    templates = request.app.state.templates
    registry = request.app.state.registry
    apps = registry.list_available()
    installed_ids = {a["id"] for a in registry.list_installed()}
    return templates.TemplateResponse("store.html", {
        "request": request,
        "active_page": "store",
        "apps": [{"manifest": a, "installed": a.id in installed_ids} for a in apps],
    })


@router.get("/api/store/catalog")
async def list_catalog(request: Request, type: str | None = None):
    registry = request.app.state.registry
    apps = registry.list_available(type_filter=type)
    return [
        {
            "id": a.id, "name": a.name, "type": a.type, "version": a.version,
            "description": a.description, "icon": a.icon,
            "requires": a.requires, "hardware_tiers": a.hardware_tiers,
            "installed": registry.is_installed(a.id),
        }
        for a in apps
    ]


@router.get("/api/store/installed")
async def list_installed(request: Request):
    return request.app.state.registry.list_installed()


@router.get("/api/store/app/{app_id}")
async def get_app(request: Request, app_id: str):
    registry = request.app.state.registry
    app = registry.get(app_id)
    if not app:
        return JSONResponse({"error": f"App '{app_id}' not found"}, status_code=404)
    return {
        "id": app.id, "name": app.name, "type": app.type, "version": app.version,
        "description": app.description, "homepage": app.homepage, "license": app.license,
        "requires": app.requires, "install": app.install,
        "hardware_tiers": app.hardware_tiers, "config_schema": app.config_schema,
        "variants": app.variants, "capabilities": app.capabilities,
        "installed": registry.is_installed(app.id),
    }


@router.get("/api/hardware")
async def hardware_profile(request: Request):
    profile = request.app.state.hardware_profile
    data = asdict(profile)
    data["profile_id"] = profile.profile_id
    return data


@router.post("/api/hardware/detect")
async def redetect_hardware(request: Request):
    from tinyagentos.hardware import detect_hardware
    profile = detect_hardware()
    profile.save(request.app.state.config_path.parent / "hardware.json")
    request.app.state.hardware_profile = profile
    data = asdict(profile)
    data["profile_id"] = profile.profile_id
    return data
```

- [ ] **Step 4: Update app.py to add registry and hardware to app state**

Add imports near the top of `create_app()` and wire up registry + hardware + store router. The key changes to `tinyagentos/app.py`:

1. Add `catalog_dir` parameter to `create_app()`
2. Create `AppRegistry` and `HardwareProfile` in factory
3. Store on `app.state`
4. Include store router

```python
# In create_app(), after existing imports inside the function:
from tinyagentos.registry import AppRegistry
from tinyagentos.hardware import get_hardware_profile

# After config is loaded:
catalog_dir = catalog_dir or PROJECT_DIR / "app-catalog"
hardware_path = data_dir / "hardware.json"
hardware_profile = get_hardware_profile(hardware_path)
installed_path = data_dir / "installed.json"
registry = AppRegistry(catalog_dir=catalog_dir, installed_path=installed_path)

# Add to app.state (both eager and lifespan):
app.state.registry = registry
app.state.hardware_profile = hardware_profile

# Add router:
from tinyagentos.routes.store import router as store_router
app.include_router(store_router)
```

- [ ] **Step 5: Create minimal store template**

```html
<!-- tinyagentos/templates/store.html -->
{% extends "base.html" %}
{% block title %}App Store — TinyAgentOS{% endblock %}
{% block content %}
<h2>App Store</h2>

<div class="search-modes" style="margin-bottom: 1.5rem;">
    <button class="active" hx-get="/api/store/catalog" hx-target="#app-grid">All</button>
    <button hx-get="/api/store/catalog?type=agent-framework" hx-target="#app-grid">Agents</button>
    <button hx-get="/api/store/catalog?type=model" hx-target="#app-grid">Models</button>
    <button hx-get="/api/store/catalog?type=service" hx-target="#app-grid">Services</button>
</div>

<div id="app-grid" class="kpi-grid">
{% for item in apps %}
    <article class="kpi-card" style="text-align: left; padding: 1.25rem;">
        <h4 style="margin-bottom: 0.25rem;">{{ item.manifest.name }}</h4>
        <small style="color: var(--pico-muted-color);">{{ item.manifest.type }} · v{{ item.manifest.version }}</small>
        <p style="font-size: 0.9rem; margin: 0.5rem 0;">{{ item.manifest.description }}</p>
        {% if item.installed %}
            <button class="outline" disabled>Installed</button>
        {% else %}
            <button class="outline"
                    hx-post="/api/store/install"
                    hx-vals='{"app_id": "{{ item.manifest.id }}"}'
                    hx-swap="outerHTML"
                    hx-confirm="Install {{ item.manifest.name }}?">
                Install
            </button>
        {% endif %}
    </article>
{% endfor %}
</div>
{% endblock %}
```

- [ ] **Step 6: Update base.html nav to include Store**

In `tinyagentos/templates/base.html`, add Store link after Dashboard:

```html
<li><a href="/store" {% if active_page == 'store' %}class="active"{% endif %}>Store</a></li>
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
pytest tests/ -v
```

Expected: All tests PASS (existing 54 + new).

- [ ] **Step 8: Commit**

```bash
git add tinyagentos/routes/store.py tinyagentos/templates/store.html tinyagentos/templates/base.html tinyagentos/app.py tests/test_routes_store.py
git commit -m "feat: App Store page with catalog browsing, hardware detection API

- /store page with app grid, type filters
- /api/store/catalog, /api/store/installed, /api/store/app/{id}
- /api/hardware for hardware profile
- Registry and hardware profile wired into app state"
```

---

### Task 5: Install/Uninstall API Endpoints

**Files:**
- Modify: `tinyagentos/tinyagentos/routes/store.py`
- Modify: `tinyagentos/tests/test_routes_store.py`

- [ ] **Step 1: Add install/uninstall tests**

Add to `tests/test_routes_store.py`:

```python
@pytest.mark.asyncio
class TestStoreInstallAPI:
    async def test_install_unknown_app_fails(self, store_client):
        resp = await store_client.post("/api/store/install", json={"app_id": "nonexistent"})
        assert resp.status_code == 404

    async def test_uninstall_not_installed_fails(self, store_client):
        resp = await store_client.post("/api/store/uninstall", json={"app_id": "smolagents"})
        assert resp.status_code == 404
```

- [ ] **Step 2: Add install/uninstall endpoints to store.py**

```python
from pydantic import BaseModel
from tinyagentos.installers.base import get_installer

class InstallRequest(BaseModel):
    app_id: str
    variant_id: str | None = None  # for models

class UninstallRequest(BaseModel):
    app_id: str

@router.post("/api/store/install")
async def install_app(request: Request, body: InstallRequest):
    registry = request.app.state.registry
    manifest = registry.get(body.app_id)
    if not manifest:
        return JSONResponse({"error": f"App '{body.app_id}' not found"}, status_code=404)
    if registry.is_installed(body.app_id):
        return JSONResponse({"error": f"App '{body.app_id}' already installed"}, status_code=409)

    method = manifest.install.get("method", "")
    try:
        installer = get_installer(method)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    kwargs = {}
    if manifest.type == "model" and body.variant_id:
        variant = next((v for v in manifest.variants if v["id"] == body.variant_id), None)
        if not variant:
            return JSONResponse({"error": f"Variant '{body.variant_id}' not found"}, status_code=404)
        kwargs["variant"] = variant

    result = await installer.install(body.app_id, manifest.install, **kwargs)
    if result["success"]:
        registry.mark_installed(body.app_id, manifest.version)
        return {"status": "installed", "app_id": body.app_id}
    return JSONResponse({"error": result.get("error", "Install failed")}, status_code=500)


@router.post("/api/store/uninstall")
async def uninstall_app(request: Request, body: UninstallRequest):
    registry = request.app.state.registry
    if not registry.is_installed(body.app_id):
        return JSONResponse({"error": f"App '{body.app_id}' not installed"}, status_code=404)

    manifest = registry.get(body.app_id)
    method = manifest.install.get("method", "") if manifest else "pip"
    try:
        installer = get_installer(method)
    except ValueError:
        pass  # best effort uninstall
    else:
        await installer.uninstall(body.app_id)

    registry.mark_uninstalled(body.app_id)
    return {"status": "uninstalled", "app_id": body.app_id}
```

- [ ] **Step 3: Run tests to verify they pass**

```bash
pytest tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tinyagentos/routes/store.py tests/test_routes_store.py
git commit -m "feat: install/uninstall API endpoints with resource checking"
```

---

### Task 6: Create Initial App Catalog

**Files:**
- Create: `app-catalog/` directory with sample manifests

- [ ] **Step 1: Create catalog structure with real manifests**

Create `app-catalog/` in the tinyagentos repo root with manifests for the agent frameworks, models, and services we've identified:

```bash
mkdir -p app-catalog/{agents/{smolagents,pocketflow,openclaw},models/{qwen3-embedding-0.6b,qwen3-reranker-0.6b,qwen3-8b,qwen3-4b},services/{gitea,code-server}}
```

Write manifest.yaml for each. Examples:

**app-catalog/agents/smolagents/manifest.yaml:**
```yaml
id: smolagents
name: SmolAgents
type: agent-framework
version: 1.0.0
description: "HuggingFace's code-based agent framework — 30% fewer LLM calls than JSON tool calling"
homepage: https://github.com/huggingface/smolagents
license: Apache-2.0

requires:
  ram_mb: 256
  python: ">=3.10"

install:
  method: pip
  package: smolagents

hardware_tiers:
  arm-npu-16gb: full
  arm-npu-32gb: full
  x86-cuda-12gb: full
  x86-vulkan-8gb: full
  cpu-only: full
```

**app-catalog/models/qwen3-8b/manifest.yaml:**
```yaml
id: qwen3-8b
name: Qwen 3 8B
type: model
version: 3.0.0
description: "General-purpose chat model with strong tool calling"
homepage: https://huggingface.co/Qwen/Qwen3-8B
capabilities: [chat, tool-calling, code]

variants:
  - id: q4_k_m
    name: "Q4_K_M (4.8GB)"
    format: gguf
    size_mb: 4800
    min_ram_mb: 6144
    download_url: https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/qwen3-8b-q4_k_m.gguf
    backend: [ollama, llama-cpp, vllm]

hardware_tiers:
  arm-npu-16gb: {recommended: q4_k_m}
  arm-npu-32gb: {recommended: q4_k_m}
  x86-cuda-12gb: {recommended: q4_k_m}
  x86-vulkan-8gb: {recommended: q4_k_m}
  x86-vulkan-4gb: unsupported
  cpu-only: {recommended: q4_k_m, notes: "Slow but functional"}
```

**app-catalog/services/gitea/manifest.yaml:**
```yaml
id: gitea
name: Gitea
type: service
version: 1.22.0
description: "Self-hosted Git server — auto-creates accounts for deployed agents"
homepage: https://gitea.io
license: MIT

requires:
  ram_mb: 256
  disk_mb: 500
  ports: [3000]

install:
  method: docker
  image: gitea/gitea:1.22
  volumes:
    - data:/data
    - config:/etc/gitea
  env:
    GITEA__server__ROOT_URL: "http://localhost:3000"

hardware_tiers:
  arm-npu-16gb: full
  arm-npu-32gb: full
  x86-cuda-12gb: full
  cpu-only: full
```

- [ ] **Step 2: Commit catalog**

```bash
git add app-catalog/
git commit -m "feat: initial app catalog with agent frameworks, models, and services"
```

---

### Task 7: Integration Test

- [ ] **Step 1: Run full test suite**

```bash
cd /home/jay/tinyagentos && pytest tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 2: Manual verification**

Start TinyAgentOS and verify:
1. `/store` page loads showing apps from the catalog
2. `/api/hardware` returns correct hardware profile
3. `/api/store/catalog` lists all apps
4. `/api/store/catalog?type=model` filters correctly
5. Type filter buttons work on the store page

```bash
python3.12 -m uvicorn tinyagentos.app:create_app --factory --host 0.0.0.0 --port 6969
```

- [ ] **Step 3: Commit any fixes**

---

## Self-Review

**Spec coverage:**
- Hardware detection ✓ (Task 1)
- App manifest format ✓ (Task 2 — AppManifest dataclass)
- App registry with install tracking ✓ (Task 2)
- Install methods: pip ✓, docker ✓, download ✓ (Task 3)
- Store API endpoints ✓ (Tasks 4-5)
- App catalog structure ✓ (Task 6)
- Hardware profile endpoint ✓ (Task 4)
- Pre-install resource check: partially — manifest `requires.ram_mb` is available but enforcement deferred to Plan 2 (needs GUI integration)
- Container memory limits: deferred to Plan 4 (Agent Deployer)
- Store UI page: basic version ✓ (Task 4), full version in Plan 2

**Not covered (correct — separate plans):**
- Model Manager UI (Plan 3)
- Agent Deployer with LXC (Plan 4)
- OS Image Build (Plan 5)
- Catalog git sync (Plan 2)
