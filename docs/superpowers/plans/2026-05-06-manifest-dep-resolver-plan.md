# Manifest Dependency Resolver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace implicit `install.method` coupling in catalog manifests with a per-variant `requires.backends` schema, a pure-function resolver that picks a backend for a (manifest, variant, device) tuple, and a recursive install dispatcher that chains backend + model installs in one user click.

**Architecture:** New `tinyagentos/catalog/resolver.py` module holds pure-function `resolve()` + `classify()`. The existing dispatcher in `tinyagentos/routes/store_install.py` is rewritten to call the resolver, recursively install missing backends through the same dispatcher (one level deep), then install the model through the existing `Installer` class registry. Frontend reads `/api/store/resolve` for compatibility classification.

**Tech Stack:** Python 3.10–3.13, FastAPI, pytest + pytest-asyncio (server). React + TypeScript + Vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-05-06-manifest-dep-resolver-design.md`

**Branch:** `feat/manifest-dep-resolver`

---

## Reference: catalog targets enum

```
rockchip-rk3588   # Orange Pi 5 Plus, friends — RK3588 NPU
apple-silicon     # M1/M2/M3+ — MLX / Metal
x86-cuda          # x86_64 with NVIDIA CUDA-capable GPU
x86-vulkan        # x86_64 with Vulkan-capable GPU (AMD, Intel Arc)
cpu               # generic CPU fallback (any arch)
```

## Reference: schema example

The shape every model manifest will end up in after migration:

```yaml
id: qwen2.5-3b
name: Qwen 2.5 3B Instruct
type: model
version: 2.5.0
context_window: 32768
capabilities: [chat, tool-calling, code]

variants:
  - id: q4_k_m
    name: "Q4_K_M (1.9GB)"
    format: gguf
    size_mb: 1900
    download_url: https://huggingface.co/bartowski/Qwen2.5-3B-Instruct-GGUF/resolve/main/Qwen2.5-3B-Instruct-Q4_K_M.gguf
    requires:
      backends:
        - id: rk-llama-cpp
          targets: [rockchip-rk3588]
          min_ram_mb: 4096
        - id: ollama
          targets: [apple-silicon, x86-cuda, cpu]
          min_ram_mb: 4096
        - id: llama-cpp
          targets: [cpu]
          min_ram_mb: 4096

hardware_tiers:                # opaque — kept for future Help app
  arm-npu-16gb: {recommended: q4_k_m}
  cpu-only: {recommended: q4_k_m}
```

The deprecated fields `install: {method: ...}` (top level) and `variants[].backend: [...]` are removed by the migration.

---

## Task 1: Hardware → catalog-targets translation

Adds a pure helper that derives the catalog-wide `targets[]` enum (`rockchip-rk3588`, `apple-silicon`, `x86-cuda`, `x86-vulkan`, `cpu`) from the existing `WorkerInfo.hardware` dict. The resolver consumes this list as input.

**Files:**
- Modify: `tinyagentos/cluster/capabilities.py` (add `hardware_to_targets`)
- Test: `tests/cluster/test_hardware_to_targets.py` (new)

- [ ] **Step 1.1: Write the failing test**

Create `tests/cluster/test_hardware_to_targets.py`:

```python
"""Hardware → catalog targets enum derivation.

Tests cover the realistic device classes the catalog supports today:
Pi NPU, Mac M-series, Linux+CUDA, Linux+Vulkan, CPU-only fallback.
"""
import pytest

from tinyagentos.cluster.capabilities import hardware_to_targets


class TestHardwareToTargets:
    def test_rk3588_npu_returns_rockchip_and_cpu(self):
        hw = {
            "cpu": {"arch": "aarch64"},
            "npu": {"type": "rk3588"},
            "ram_mb": 16384,
        }
        assert hardware_to_targets(hw) == ["rockchip-rk3588", "cpu"]

    def test_apple_silicon_returns_apple_and_cpu(self):
        hw = {
            "cpu": {"arch": "arm64"},
            "gpu": {"type": "apple"},
            "ram_mb": 16384,
        }
        assert hardware_to_targets(hw) == ["apple-silicon", "cpu"]

    def test_nvidia_cuda_returns_x86_cuda_and_cpu(self):
        hw = {
            "cpu": {"arch": "x86_64"},
            "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 12288},
            "ram_mb": 32768,
        }
        assert hardware_to_targets(hw) == ["x86-cuda", "cpu"]

    def test_amd_vulkan_returns_x86_vulkan_and_cpu(self):
        hw = {
            "cpu": {"arch": "x86_64"},
            "gpu": {"type": "amd", "vulkan": True, "vram_mb": 8192},
            "ram_mb": 32768,
        }
        assert hardware_to_targets(hw) == ["x86-vulkan", "cpu"]

    def test_intel_vulkan_returns_x86_vulkan_and_cpu(self):
        hw = {
            "cpu": {"arch": "x86_64"},
            "gpu": {"type": "intel", "vulkan": True, "vram_mb": 4096},
            "ram_mb": 16384,
        }
        assert hardware_to_targets(hw) == ["x86-vulkan", "cpu"]

    def test_cpu_only_returns_cpu(self):
        hw = {
            "cpu": {"arch": "x86_64"},
            "ram_mb": 8192,
        }
        assert hardware_to_targets(hw) == ["cpu"]

    def test_arm_cpu_only_returns_cpu(self):
        hw = {
            "cpu": {"arch": "aarch64"},
            "ram_mb": 4096,
        }
        assert hardware_to_targets(hw) == ["cpu"]

    def test_empty_hardware_returns_cpu_only(self):
        assert hardware_to_targets({}) == ["cpu"]

    def test_npu_takes_priority_over_gpu(self):
        # If both NPU and GPU are present, NPU wins (we run accelerated
        # there first), but cpu still in the list as the fallback.
        hw = {
            "cpu": {"arch": "aarch64"},
            "npu": {"type": "rk3588"},
            "gpu": {"type": "mali"},
            "ram_mb": 16384,
        }
        assert hardware_to_targets(hw) == ["rockchip-rk3588", "cpu"]

    def test_string_cpu_field_does_not_crash(self):
        # Older worker agents may send cpu as a plain string, not a dict.
        hw = {"cpu": "x86_64", "ram_mb": 8192}
        assert hardware_to_targets(hw) == ["cpu"]
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/cluster/test_hardware_to_targets.py -v`
Expected: ImportError — `cannot import name 'hardware_to_targets' from 'tinyagentos.cluster.capabilities'`

- [ ] **Step 1.3: Implement `hardware_to_targets`**

Append to `tinyagentos/cluster/capabilities.py` (after `worker_tier_id`):

```python
def hardware_to_targets(hardware: dict) -> list[str]:
    """Derive the resolver's catalog-targets list from a worker hardware dict.

    Catalog targets are an enumeration the manifest schema uses to declare
    which hardware classes a backend can run on. Distinct from the
    fuzzy ``tier_id`` used by the legacy ``hardware_tiers`` filter — this
    list is what the new resolver consumes.

    Returns
    -------
    list[str]
        Targets in priority order. Always includes ``"cpu"`` as the fallback.
    """
    targets: list[str] = []
    if not hardware:
        return ["cpu"]

    cpu_raw = hardware.get("cpu") or {}
    cpu = cpu_raw if isinstance(cpu_raw, dict) else {}
    arch_raw = cpu.get("arch", "")
    arch = "arm" if arch_raw in ("aarch64", "armv7l", "arm64") else "x86"

    npu = hardware.get("npu") or {}
    gpu = hardware.get("gpu") or {}

    npu_type = npu.get("type", "none") or "none"
    gpu_type = gpu.get("type", "none") or "none"

    # NPU takes priority over GPU when both are present.
    if npu_type == "rk3588":
        targets.append("rockchip-rk3588")
    elif gpu_type == "apple":
        targets.append("apple-silicon")
    elif gpu_type == "nvidia" and gpu.get("cuda"):
        targets.append("x86-cuda")
    elif (gpu_type in ("amd", "intel") and gpu.get("vulkan")) or (
        gpu_type != "none" and gpu.get("vulkan")
    ):
        targets.append(f"{arch}-vulkan" if arch == "x86" else "x86-vulkan")

    targets.append("cpu")
    return targets
```

- [ ] **Step 1.4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/cluster/test_hardware_to_targets.py -v`
Expected: 10 passed

- [ ] **Step 1.5: Commit**

```bash
git add tinyagentos/cluster/capabilities.py tests/cluster/test_hardware_to_targets.py
git commit -m "feat(cluster): hardware_to_targets() — derive catalog targets enum from worker hardware"
```

---

## Task 2: Catalog package + resolver type definitions

Sets up the new `tinyagentos/catalog/` package and declares the dataclasses the resolver returns. Type-only commit — no logic yet, no tests beyond an import smoke test (testing dataclasses is anti-value).

**Files:**
- Create: `tinyagentos/catalog/__init__.py`
- Create: `tinyagentos/catalog/resolver.py`
- Create: `tests/catalog/__init__.py`
- Test: `tests/catalog/test_resolver_types.py`

- [ ] **Step 2.1: Create the package files**

Create `tinyagentos/catalog/__init__.py` (empty):

```python
"""Catalog manifest helpers — resolver, classification, schema types."""
```

Create `tests/catalog/__init__.py` (empty):

```python
```

- [ ] **Step 2.2: Write the failing import test**

Create `tests/catalog/test_resolver_types.py`:

```python
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
            targets=("rockchip-rk3588", "cpu"),
            total_ram_mb=16384,
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=("rk-llama-cpp",),
        )
        assert d.device_id == "local"
        assert d.targets == ("rockchip-rk3588", "cpu")


class TestBackendDep:
    def test_default_min_vram_zero(self):
        b = BackendDep(
            id="rk-llama-cpp",
            targets=("rockchip-rk3588",),
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
```

- [ ] **Step 2.3: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/catalog/test_resolver_types.py -v`
Expected: ImportError — `cannot import name 'BackendDep' from 'tinyagentos.catalog.resolver'`

- [ ] **Step 2.4: Create `resolver.py` with type definitions**

Create `tinyagentos/catalog/resolver.py`:

```python
"""Pure-function resolver for catalog model manifests.

Given a (manifest, variant, device, force) tuple, decides which backend
should serve the model and whether the chain needs an extra install step.
No I/O, no httpx, no cluster lookups — inputs are passed in by the caller.
This module is the single source of truth shared by the install dispatcher
and the frontend's compatibility classification (via /api/store/resolve).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union


@dataclass(frozen=True)
class DeviceCapability:
    """Snapshot of a single device's resources, supplied by the caller.

    ``total_ram_mb`` / ``total_vram_mb`` are *capacity* (not current free) —
    dynamic unload makes free-now an unreliable signal. Disk stays "free"
    because nothing auto-evicts on disk.
    """
    device_id: str
    targets: tuple[str, ...]
    total_ram_mb: int
    total_vram_mb: int
    free_disk_mb: int
    installed_backends: tuple[str, ...]


@dataclass(frozen=True)
class BackendDep:
    """A single backend candidate listed under variant.requires.backends."""
    id: str
    targets: tuple[str, ...]
    min_ram_mb: int
    min_vram_mb: int = 0


@dataclass(frozen=True)
class ResolveOk:
    """Successful resolve. ``action`` tells the dispatcher whether the
    backend needs installing first."""
    backend_id: str
    variant_id: str
    action: Literal["use", "install_chain"]


@dataclass(frozen=True)
class ResolveErr:
    """Could not resolve. ``near_miss`` and ``suggestions`` feed the UI."""
    reason: str
    near_miss: dict[str, Any] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)


ResolveResult = Union[ResolveOk, ResolveErr]
```

- [ ] **Step 2.5: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/catalog/test_resolver_types.py -v`
Expected: 5 passed

- [ ] **Step 2.6: Commit**

```bash
git add tinyagentos/catalog/__init__.py tinyagentos/catalog/resolver.py tests/catalog/__init__.py tests/catalog/test_resolver_types.py
git commit -m "feat(catalog): resolver package skeleton with DeviceCapability/BackendDep/ResolveOk/ResolveErr"
```

---

## Task 3: `resolve()` — explicit variant + four gates

Implements `resolve(manifest, variant_id, device, force)` for the explicit-variant case (no auto). Walks `variant.requires.backends` in declaration order, applies the four gates (target / RAM / VRAM / disk), returns the first match as `ResolveOk` or a structured `ResolveErr` if nothing fits.

**Files:**
- Modify: `tinyagentos/catalog/resolver.py`
- Test: `tests/catalog/test_resolver.py` (new)

- [ ] **Step 3.1: Write the failing test**

Create `tests/catalog/test_resolver.py`:

```python
"""Behavior tests for resolve() and classify().

Manifest fixtures are dicts (matching what AppManifest.from_file produces
for the .install/.requires/.variants slots), not full AppManifest objects —
the resolver only needs the variants list and never touches the rest.
"""
import pytest

from tinyagentos.catalog.resolver import (
    DeviceCapability,
    ResolveErr,
    ResolveOk,
    resolve,
)


def make_qwen_manifest() -> dict:
    """Realistic post-migration model manifest fixture."""
    return {
        "id": "qwen2.5-3b",
        "type": "model",
        "context_window": 32768,
        "variants": [
            {
                "id": "q4_k_m",
                "size_mb": 1900,
                "requires": {
                    "backends": [
                        {
                            "id": "rk-llama-cpp",
                            "targets": ["rockchip-rk3588"],
                            "min_ram_mb": 4096,
                        },
                        {
                            "id": "ollama",
                            "targets": ["apple-silicon", "x86-cuda", "cpu"],
                            "min_ram_mb": 4096,
                        },
                        {
                            "id": "llama-cpp",
                            "targets": ["cpu"],
                            "min_ram_mb": 4096,
                        },
                    ],
                },
            },
            {
                "id": "q8_0",
                "size_mb": 3400,
                "requires": {
                    "backends": [
                        {
                            "id": "rk-llama-cpp",
                            "targets": ["rockchip-rk3588"],
                            "min_ram_mb": 6144,
                        },
                        {
                            "id": "ollama",
                            "targets": ["apple-silicon", "x86-cuda", "cpu"],
                            "min_ram_mb": 6144,
                        },
                    ],
                },
            },
        ],
    }


def pi_device(installed: tuple[str, ...] = ()) -> DeviceCapability:
    return DeviceCapability(
        device_id="pi",
        targets=("rockchip-rk3588", "cpu"),
        total_ram_mb=16384,
        total_vram_mb=0,
        free_disk_mb=50_000,
        installed_backends=installed,
    )


class TestResolveExplicitVariant:
    def test_use_when_backend_already_installed(self):
        m = make_qwen_manifest()
        r = resolve(m, "q4_k_m", pi_device(installed=("rk-llama-cpp",)))
        assert isinstance(r, ResolveOk)
        assert r.backend_id == "rk-llama-cpp"
        assert r.variant_id == "q4_k_m"
        assert r.action == "use"

    def test_install_chain_when_backend_missing(self):
        m = make_qwen_manifest()
        r = resolve(m, "q4_k_m", pi_device(installed=()))
        assert isinstance(r, ResolveOk)
        assert r.backend_id == "rk-llama-cpp"
        assert r.action == "install_chain"

    def test_declaration_order_tiebreaker(self):
        # Pi has both rockchip and cpu in targets — rk-llama-cpp is declared
        # first; even if llama-cpp would also match cpu, the first match wins.
        m = make_qwen_manifest()
        r = resolve(m, "q4_k_m", pi_device(installed=("rk-llama-cpp", "llama-cpp")))
        assert isinstance(r, ResolveOk)
        assert r.backend_id == "rk-llama-cpp"


class TestResolveGates:
    def test_no_target_intersection_returns_err(self):
        m = make_qwen_manifest()
        # Apple-silicon device tries to install qwen but the qwen variant
        # only lists rockchip / x86-cuda / cpu — Mac matches "cpu" via ollama.
        # Force a stricter manifest to test the no-intersection case.
        no_match = {
            "id": "rkllm-only-model",
            "type": "model",
            "variants": [
                {
                    "id": "default",
                    "size_mb": 1000,
                    "requires": {
                        "backends": [
                            {
                                "id": "rkllama",
                                "targets": ["rockchip-rk3588"],
                                "min_ram_mb": 4096,
                            },
                        ],
                    },
                },
            ],
        }
        mac = DeviceCapability(
            device_id="mac",
            targets=("apple-silicon", "cpu"),
            total_ram_mb=16384,
            total_vram_mb=0,
            free_disk_mb=200_000,
            installed_backends=(),
        )
        r = resolve(no_match, "default", mac)
        assert isinstance(r, ResolveErr)
        assert r.near_miss["blocked_by"] == "target"

    def test_ram_short_returns_err(self):
        m = make_qwen_manifest()
        small_pi = DeviceCapability(
            device_id="pi",
            targets=("rockchip-rk3588", "cpu"),
            total_ram_mb=2048,  # below q8_0's 6144 floor
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=(),
        )
        r = resolve(m, "q8_0", small_pi)
        assert isinstance(r, ResolveErr)
        assert r.near_miss["blocked_by"] == "ram"
        assert r.near_miss["short_by_mb"] > 0

    def test_vram_short_returns_err(self):
        # Construct a manifest where the only matching backend needs VRAM.
        vram_required = {
            "id": "vram-hungry",
            "type": "model",
            "variants": [
                {
                    "id": "fp16",
                    "size_mb": 1000,
                    "requires": {
                        "backends": [
                            {
                                "id": "vllm",
                                "targets": ["x86-cuda"],
                                "min_ram_mb": 4096,
                                "min_vram_mb": 24576,
                            },
                        ],
                    },
                },
            ],
        }
        small_gpu = DeviceCapability(
            device_id="gpu",
            targets=("x86-cuda", "cpu"),
            total_ram_mb=32768,
            total_vram_mb=8192,
            free_disk_mb=200_000,
            installed_backends=(),
        )
        r = resolve(vram_required, "fp16", small_gpu)
        assert isinstance(r, ResolveErr)
        assert r.near_miss["blocked_by"] == "vram"

    def test_disk_short_returns_err_even_when_other_gates_pass(self):
        m = make_qwen_manifest()
        full_pi = DeviceCapability(
            device_id="pi",
            targets=("rockchip-rk3588", "cpu"),
            total_ram_mb=16384,
            total_vram_mb=0,
            free_disk_mb=500,  # well below the 1900 MB the variant needs
            installed_backends=("rk-llama-cpp",),
        )
        r = resolve(m, "q4_k_m", full_pi)
        assert isinstance(r, ResolveErr)
        assert r.near_miss["blocked_by"] == "disk"
        assert r.near_miss["short_by_mb"] >= 1400


class TestResolveUnknownVariant:
    def test_returns_err_when_variant_id_not_found(self):
        m = make_qwen_manifest()
        r = resolve(m, "does-not-exist", pi_device())
        assert isinstance(r, ResolveErr)
        assert "does-not-exist" in r.reason
```

- [ ] **Step 3.2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/catalog/test_resolver.py -v`
Expected: ImportError — `cannot import name 'resolve' from 'tinyagentos.catalog.resolver'`

- [ ] **Step 3.3: Implement `resolve` (explicit variant only — no auto yet)**

Append to `tinyagentos/catalog/resolver.py`:

```python
def _coerce_backends(raw: list[dict]) -> list[BackendDep]:
    """Normalize a YAML-loaded backends list into BackendDep objects."""
    out: list[BackendDep] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(
                BackendDep(
                    id=str(entry["id"]),
                    targets=tuple(entry.get("targets", []) or []),
                    min_ram_mb=int(entry.get("min_ram_mb", 0) or 0),
                    min_vram_mb=int(entry.get("min_vram_mb", 0) or 0),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _check_variant(
    variant: dict,
    device: DeviceCapability,
    *,
    force: bool,
) -> ResolveResult:
    """Try to resolve a single variant. Pure function."""
    deps = _coerce_backends(
        ((variant.get("requires") or {}).get("backends")) or []
    )
    if not deps:
        return ResolveErr(
            reason=f"variant {variant.get('id')!r} has no requires.backends",
            near_miss={"variant": variant.get("id"), "blocked_by": "schema"},
            suggestions=["Update the manifest to declare requires.backends"],
        )

    size_mb = int(variant.get("size_mb", 0) or 0)
    if size_mb > 0 and device.free_disk_mb < size_mb:
        # Disk gate runs even with force=True — you can't write to a full disk.
        return ResolveErr(
            reason=(
                f"variant {variant.get('id')!r} needs {size_mb} MB disk, "
                f"device has {device.free_disk_mb} MB free"
            ),
            near_miss={
                "variant": variant.get("id"),
                "blocked_by": "disk",
                "short_by_mb": size_mb - device.free_disk_mb,
            },
            suggestions=[
                "Pick a smaller variant",
                "Free up disk on this device",
                "Install on a worker with more disk",
            ],
        )

    closest_short_mb = -1
    closest_blocked_by = "target"
    closest_variant = variant.get("id")

    device_targets = set(device.targets)
    for dep in deps:
        # Gate 1: target intersection (bypassed by force).
        if not force and not (set(dep.targets) & device_targets):
            if closest_short_mb < 0:
                closest_blocked_by = "target"
            continue

        # Gate 2: total RAM (bypassed by force).
        if not force and device.total_ram_mb < dep.min_ram_mb:
            short = dep.min_ram_mb - device.total_ram_mb
            if closest_short_mb < 0 or short < closest_short_mb:
                closest_short_mb = short
                closest_blocked_by = "ram"
            continue

        # Gate 3: total VRAM if the dep declares a floor (bypassed by force).
        if not force and dep.min_vram_mb > 0 and device.total_vram_mb < dep.min_vram_mb:
            short = dep.min_vram_mb - device.total_vram_mb
            if closest_short_mb < 0 or short < closest_short_mb:
                closest_short_mb = short
                closest_blocked_by = "vram"
            continue

        # All gates passed — pick this dep.
        action: Literal["use", "install_chain"] = (
            "use" if dep.id in device.installed_backends else "install_chain"
        )
        return ResolveOk(
            backend_id=dep.id,
            variant_id=str(variant.get("id", "")),
            action=action,
        )

    return ResolveErr(
        reason=(
            f"no compatible backend for {variant.get('id')!r} on device "
            f"{device.device_id!r}"
        ),
        near_miss={
            "variant": closest_variant,
            "blocked_by": closest_blocked_by,
            "short_by_mb": max(0, closest_short_mb),
        },
        suggestions=_suggestions_for(closest_blocked_by),
    )


def _suggestions_for(blocked_by: str) -> list[str]:
    if blocked_by == "ram":
        return [
            "Pick a smaller variant",
            "Install on a device with more RAM",
        ]
    if blocked_by == "vram":
        return [
            "Pick a smaller variant",
            "Install on a device with a larger GPU",
        ]
    if blocked_by == "disk":
        return [
            "Pick a smaller variant",
            "Free up disk on this device",
            "Install on a worker with more disk",
        ]
    if blocked_by == "target":
        return [
            "Install on a device whose hardware can run this model",
            "Use 'Archive anyway' to download for later",
        ]
    return []


def resolve(
    manifest: dict,
    variant_id: str,
    device: DeviceCapability,
    *,
    force: bool = False,
) -> ResolveResult:
    """Pick a backend for (manifest, variant, device).

    Parameters
    ----------
    manifest:
        Loaded model manifest dict (typically from ``AppManifest.from_file``
        round-tripped via ``yaml.safe_load``).
    variant_id:
        The chosen variant's ``id``, or the literal ``"auto"`` to ask the
        resolver to pick the largest-fitting variant.
    device:
        Capacity snapshot of the target device.
    force:
        When ``True``, the target / RAM / VRAM gates are bypassed (used for
        the "Archive anyway" download flow). The disk gate always applies.
    """
    variants = manifest.get("variants") or []
    if not variants:
        return ResolveErr(
            reason="manifest has no variants",
            near_miss={"blocked_by": "schema"},
            suggestions=["Fix the manifest"],
        )

    if variant_id != "auto":
        for v in variants:
            if isinstance(v, dict) and v.get("id") == variant_id:
                return _check_variant(v, device, force=force)
        return ResolveErr(
            reason=f"variant {variant_id!r} not found in manifest",
            near_miss={"variant": variant_id, "blocked_by": "schema"},
            suggestions=[f"Available variants: {[v.get('id') for v in variants]}"],
        )

    # auto branch is wired up in Task 4 — this path is unreachable until then.
    raise NotImplementedError("variant_id='auto' is added in Task 4")
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/catalog/test_resolver.py -v`
Expected: 7 passed

- [ ] **Step 3.5: Commit**

```bash
git add tinyagentos/catalog/resolver.py tests/catalog/test_resolver.py
git commit -m "feat(catalog): resolve() — explicit-variant gates (target/ram/vram/disk)"
```

---

## Task 4: `resolve()` — auto-variant (quality-first)

Adds the `variant_id="auto"` branch. Sorts variants by `size_mb` descending and returns the first that resolves `Ok`. This makes the user click Install once and get the highest-quality quant their hardware can serve.

**Files:**
- Modify: `tinyagentos/catalog/resolver.py:resolve` (auto branch)
- Test: `tests/catalog/test_resolver.py` (extend)

- [ ] **Step 4.1: Write the failing test**

Append to `tests/catalog/test_resolver.py`:

```python
class TestResolveAutoVariant:
    def test_auto_picks_largest_fitting_variant(self):
        # Pi has 16 GB total RAM — q8_0 (needs 6144) fits, so quality-first
        # should return q8_0 over q4_k_m.
        m = make_qwen_manifest()
        r = resolve(m, "auto", pi_device(installed=("rk-llama-cpp",)))
        assert isinstance(r, ResolveOk)
        assert r.variant_id == "q8_0"
        assert r.backend_id == "rk-llama-cpp"

    def test_auto_falls_through_to_smaller_when_larger_blocked(self):
        m = make_qwen_manifest()
        small_pi = DeviceCapability(
            device_id="pi",
            targets=("rockchip-rk3588", "cpu"),
            total_ram_mb=4096,  # q8_0's 6144 doesn't fit, q4_k_m's 4096 does
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=("rk-llama-cpp",),
        )
        r = resolve(m, "auto", small_pi)
        assert isinstance(r, ResolveOk)
        assert r.variant_id == "q4_k_m"

    def test_auto_returns_err_when_nothing_fits(self):
        m = make_qwen_manifest()
        tiny = DeviceCapability(
            device_id="tiny",
            targets=("rockchip-rk3588", "cpu"),
            total_ram_mb=1024,  # below every variant's floor
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=(),
        )
        r = resolve(m, "auto", tiny)
        assert isinstance(r, ResolveErr)
        assert r.near_miss["blocked_by"] in ("ram", "target")
```

- [ ] **Step 4.2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/catalog/test_resolver.py::TestResolveAutoVariant -v`
Expected: 3 fail with `NotImplementedError: variant_id='auto' is added in Task 4`

- [ ] **Step 4.3: Implement the auto branch**

In `tinyagentos/catalog/resolver.py`, replace the `raise NotImplementedError(...)` line at the bottom of `resolve()` with:

```python
    # auto: walk variants by size_mb descending, return first Ok.
    sorted_variants = sorted(
        (v for v in variants if isinstance(v, dict)),
        key=lambda v: int(v.get("size_mb", 0) or 0),
        reverse=True,
    )
    last_err: ResolveErr | None = None
    for v in sorted_variants:
        result = _check_variant(v, device, force=force)
        if isinstance(result, ResolveOk):
            return result
        last_err = result

    if last_err is not None:
        return last_err
    return ResolveErr(
        reason="no variants in manifest could be evaluated",
        near_miss={"blocked_by": "schema"},
        suggestions=["Fix the manifest"],
    )
```

- [ ] **Step 4.4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/catalog/test_resolver.py -v`
Expected: 10 passed (7 from Task 3 + 3 new)

- [ ] **Step 4.5: Commit**

```bash
git add tinyagentos/catalog/resolver.py tests/catalog/test_resolver.py
git commit -m "feat(catalog): resolve() auto-variant (quality-first, size_mb desc)"
```

---

## Task 5: `force=True` archive-anyway flag

Existing tests already exercise gates. This task pins the `force=True` behaviour: target / RAM / VRAM gates are bypassed, but the disk gate is not.

**Files:**
- Modify: nothing (already implemented in Task 3 — this task adds tests that pin the behavior)
- Test: `tests/catalog/test_resolver.py` (extend)

- [ ] **Step 5.1: Write the failing test**

Append to `tests/catalog/test_resolver.py`:

```python
class TestResolveForceFlag:
    def test_force_bypasses_target_mismatch(self):
        no_match = {
            "id": "mlx-only",
            "type": "model",
            "variants": [
                {
                    "id": "default",
                    "size_mb": 1000,
                    "requires": {
                        "backends": [
                            {
                                "id": "mlx",
                                "targets": ["apple-silicon"],
                                "min_ram_mb": 4096,
                            },
                        ],
                    },
                },
            ],
        }
        pi = pi_device(installed=())
        without_force = resolve(no_match, "default", pi, force=False)
        with_force = resolve(no_match, "default", pi, force=True)
        assert isinstance(without_force, ResolveErr)
        assert isinstance(with_force, ResolveOk)
        assert with_force.backend_id == "mlx"
        assert with_force.action == "install_chain"

    def test_force_bypasses_ram_short(self):
        m = make_qwen_manifest()
        small_pi = DeviceCapability(
            device_id="pi",
            targets=("rockchip-rk3588", "cpu"),
            total_ram_mb=1024,
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=("rk-llama-cpp",),
        )
        without_force = resolve(m, "q8_0", small_pi, force=False)
        with_force = resolve(m, "q8_0", small_pi, force=True)
        assert isinstance(without_force, ResolveErr)
        assert isinstance(with_force, ResolveOk)

    def test_force_does_not_bypass_disk_gate(self):
        m = make_qwen_manifest()
        full_pi = DeviceCapability(
            device_id="pi",
            targets=("rockchip-rk3588", "cpu"),
            total_ram_mb=16384,
            total_vram_mb=0,
            free_disk_mb=100,  # nowhere near 1900 MB
            installed_backends=("rk-llama-cpp",),
        )
        r = resolve(m, "q4_k_m", full_pi, force=True)
        assert isinstance(r, ResolveErr)
        assert r.near_miss["blocked_by"] == "disk"
```

- [ ] **Step 5.2: Run tests**

Run: `PYTHONPATH=. pytest tests/catalog/test_resolver.py::TestResolveForceFlag -v`
Expected: 3 passed (the implementation already handles force; this just pins it).

- [ ] **Step 5.3: Commit**

```bash
git add tests/catalog/test_resolver.py
git commit -m "test(catalog): pin force=True bypass semantics (target/ram/vram skipped, disk enforced)"
```

---

## Task 6: `classify()` — green/amber/red

Classification helper used by the Store frontend to colour-code model cards. Walks all variants and inspects whether any resolved Ok against a non-cpu target.

**Files:**
- Modify: `tinyagentos/catalog/resolver.py` (add `classify`)
- Test: `tests/catalog/test_resolver.py` (extend)

- [ ] **Step 6.1: Write the failing test**

Append to `tests/catalog/test_resolver.py`:

```python
from tinyagentos.catalog.resolver import classify  # noqa: E402


class TestClassify:
    def test_green_when_accelerated_target_matches(self):
        m = make_qwen_manifest()
        # Pi-NPU has rockchip-rk3588 → rk-llama-cpp matches → green.
        assert classify(m, pi_device()) == "green"

    def test_amber_when_only_cpu_target_matches(self):
        m = make_qwen_manifest()
        cpu_box = DeviceCapability(
            device_id="cpu-box",
            targets=("cpu",),
            total_ram_mb=16384,
            total_vram_mb=0,
            free_disk_mb=200_000,
            installed_backends=(),
        )
        assert classify(m, cpu_box) == "amber"

    def test_red_when_no_variant_resolves(self):
        m = make_qwen_manifest()
        tiny = DeviceCapability(
            device_id="tiny",
            targets=("cpu",),
            total_ram_mb=1024,
            total_vram_mb=0,
            free_disk_mb=200_000,
            installed_backends=(),
        )
        assert classify(m, tiny) == "red"
```

- [ ] **Step 6.2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/catalog/test_resolver.py::TestClassify -v`
Expected: ImportError for `classify`

- [ ] **Step 6.3: Implement `classify`**

Append to `tinyagentos/catalog/resolver.py`:

```python
def classify(manifest: dict, device: DeviceCapability) -> Literal["green", "amber", "red"]:
    """Classify a model's compatibility with a device.

    Returns one of:

    - ``"green"`` — at least one variant resolves on a non-``cpu`` target
      (accelerated path available).
    - ``"amber"`` — at least one variant resolves but only on ``cpu``.
    - ``"red"`` — no variant resolves under non-force gates.
    """
    variants = manifest.get("variants") or []
    accelerated = False
    cpu_only = False
    for v in variants:
        if not isinstance(v, dict):
            continue
        result = _check_variant(v, device, force=False)
        if isinstance(result, ResolveOk):
            # Find the dep that won so we know its targets.
            deps = _coerce_backends(
                ((v.get("requires") or {}).get("backends")) or []
            )
            for dep in deps:
                if dep.id != result.backend_id:
                    continue
                if any(t != "cpu" for t in dep.targets if t in device.targets):
                    accelerated = True
                else:
                    cpu_only = True
                break

    if accelerated:
        return "green"
    if cpu_only:
        return "amber"
    return "red"
```

- [ ] **Step 6.4: Run tests**

Run: `PYTHONPATH=. pytest tests/catalog/test_resolver.py -v`
Expected: 16 passed (10 from earlier + 3 force + 3 classify)

- [ ] **Step 6.5: Commit**

```bash
git add tinyagentos/catalog/resolver.py tests/catalog/test_resolver.py
git commit -m "feat(catalog): classify() returns green/amber/red for (manifest, device)"
```

---

## Task 7: ScriptInstaller — for backend service manifests using `method: script`

Backend service manifests like `rk-llama-cpp` declare `install: {method: script, script: scripts/install-X.sh}`. The current `get_installer()` registry doesn't handle `script`. Add a small `ScriptInstaller` that shells out to the script. Required for the recursive backend-install chain in Task 9.

**Files:**
- Create: `tinyagentos/installers/script_installer.py`
- Modify: `tinyagentos/installers/base.py` (dispatch `script` → ScriptInstaller)
- Test: `tests/installers/test_script_installer.py` (new)

- [ ] **Step 7.1: Write the failing test**

Create `tests/installers/__init__.py` (empty if not present) and `tests/installers/test_script_installer.py`:

```python
import os
import stat
from pathlib import Path

import pytest

from tinyagentos.installers.script_installer import ScriptInstaller


@pytest.fixture
def fake_script(tmp_path: Path) -> Path:
    """A trivial bash script that writes a marker file and exits 0."""
    script_path = tmp_path / "scripts" / "install-fake.sh"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("#!/bin/bash\nset -e\necho installed > \"$1/marker\"\n")
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
    return script_path


@pytest.fixture
def failing_script(tmp_path: Path) -> Path:
    p = tmp_path / "scripts" / "install-bust.sh"
    p.parent.mkdir(parents=True)
    p.write_text("#!/bin/bash\necho boom 1>&2\nexit 7\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


class TestScriptInstallerInstall:
    @pytest.mark.asyncio
    async def test_runs_script_with_app_id(self, tmp_path, fake_script):
        installer = ScriptInstaller(project_dir=tmp_path)
        result = await installer.install(
            "fake-svc",
            install_config={"method": "script", "script": str(fake_script.relative_to(tmp_path))},
        )
        assert result["success"] is True
        # The marker the script wrote uses arg $1 as a writable dir.
        # ScriptInstaller passes app_id and project_dir as args; verify the
        # script ran by checking it produced a marker we can find.
        # (The fake script writes to $1/marker.)
        # Implementation detail: ScriptInstaller passes (app_id, project_dir)
        # so marker should be at <project_dir>/marker.
        assert (tmp_path / "marker").read_text().strip() == "installed"

    @pytest.mark.asyncio
    async def test_returns_failure_with_stderr_when_script_fails(self, tmp_path, failing_script):
        installer = ScriptInstaller(project_dir=tmp_path)
        result = await installer.install(
            "bust-svc",
            install_config={"method": "script", "script": str(failing_script.relative_to(tmp_path))},
        )
        assert result["success"] is False
        assert "boom" in result.get("error", "") or "rc=7" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_missing_script_path_returns_error(self, tmp_path):
        installer = ScriptInstaller(project_dir=tmp_path)
        result = await installer.install(
            "x",
            install_config={"method": "script"},  # no `script` key
        )
        assert result["success"] is False
        assert "script" in result.get("error", "").lower()


class TestScriptInstallerUninstall:
    @pytest.mark.asyncio
    async def test_uninstall_runs_uninstall_script_when_provided(self, tmp_path):
        un = tmp_path / "scripts" / "uninstall-fake.sh"
        un.parent.mkdir(parents=True)
        un.write_text("#!/bin/bash\necho removed > \"$1/uninstalled\"\n")
        un.chmod(un.stat().st_mode | stat.S_IXUSR)
        installer = ScriptInstaller(project_dir=tmp_path)
        # Install with uninstall_script declared so the installer remembers it.
        await installer.install(
            "x",
            install_config={
                "method": "script",
                "script": "scripts/install-bust.sh",  # we'll stub install separately
                "uninstall_script": str(un.relative_to(tmp_path)),
            },
        )
        # Direct uninstall call.
        result = await installer.uninstall_with_script(
            "x", str(un.relative_to(tmp_path))
        )
        assert result["success"] is True
        assert (tmp_path / "uninstalled").read_text().strip() == "removed"
```

- [ ] **Step 7.2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/installers/test_script_installer.py -v`
Expected: ImportError — module not found.

- [ ] **Step 7.3: Implement `ScriptInstaller`**

Create `tinyagentos/installers/script_installer.py`:

```python
"""Installer for backend service manifests using ``install: {method: script}``.

The script receives one argument: the project root path. The script is
expected to be idempotent — invoked again on a host where the backend
already exists, it should detect that and exit 0.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from tinyagentos.installers.base import AppInstaller, run_cmd

logger = logging.getLogger(__name__)


class ScriptInstaller(AppInstaller):
    """Run a shell script declared in ``install.script`` (path relative to project_dir)."""

    def __init__(self, project_dir: Path | str | None = None, timeout: int = 1800):
        self.project_dir = Path(project_dir) if project_dir else Path.cwd()
        self.timeout = timeout

    async def install(
        self,
        app_id: str,
        install_config: dict,
        **_: Any,
    ) -> dict:
        rel_script = install_config.get("script")
        if not rel_script:
            return {"success": False, "error": "install.script not declared in manifest"}
        script_path = (self.project_dir / rel_script).resolve()
        if not script_path.is_file():
            return {"success": False, "error": f"script not found: {script_path}"}

        rc, out = await run_cmd(
            ["bash", str(script_path), str(self.project_dir)],
            cwd=str(self.project_dir),
            timeout=self.timeout,
        )
        if rc != 0:
            return {
                "success": False,
                "error": f"install script failed (rc={rc}): {out.strip()[-1000:]}",
            }
        return {"success": True, "app_id": app_id, "method": "script"}

    async def uninstall(self, app_id: str) -> dict:
        # The uninstall script (if any) is recorded in install_config and
        # run via uninstall_with_script() — direct uninstall(app_id) on a
        # ScriptInstaller without that context is a no-op.
        return {"success": True, "status": "uninstalled", "note": "no uninstall script declared"}

    async def uninstall_with_script(self, app_id: str, rel_script: str) -> dict:
        """Run an explicit uninstall script."""
        script_path = (self.project_dir / rel_script).resolve()
        if not script_path.is_file():
            return {"success": False, "error": f"uninstall script not found: {script_path}"}
        rc, out = await run_cmd(
            ["bash", str(script_path), str(self.project_dir)],
            cwd=str(self.project_dir),
            timeout=self.timeout,
        )
        if rc != 0:
            return {
                "success": False,
                "error": f"uninstall script failed (rc={rc}): {out.strip()[-1000:]}",
            }
        return {"success": True, "app_id": app_id}
```

- [ ] **Step 7.4: Wire dispatch in `tinyagentos/installers/base.py`**

Append to the `get_installer` chain in `tinyagentos/installers/base.py`:

```python
    elif method == "script":
        from tinyagentos.installers.script_installer import ScriptInstaller
        return ScriptInstaller(**kwargs)
```

(Place this `elif` immediately before the final `else` that raises `ValueError`.)

- [ ] **Step 7.5: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/installers/test_script_installer.py -v`
Expected: 4 passed

Run the full installer suite to make sure nothing else broke:
Run: `PYTHONPATH=. pytest tests/installers/ -v`
Expected: all green

- [ ] **Step 7.6: Commit**

```bash
git add tinyagentos/installers/script_installer.py tinyagentos/installers/base.py tests/installers/test_script_installer.py tests/installers/__init__.py
git commit -m "feat(installers): ScriptInstaller for service manifests using method: script"
```

---

## Task 8: Migration script — schema rewrite + context_window seeding

Writes (and unit-tests) `scripts/migrate-manifests-to-requires-backends.py`. The script reads each model manifest, infers `requires.backends` from the legacy `install.method` and `variants[].backend` fields, and seeds `context_window` from a hand-curated lookup table for known models (manual audit covers the rest).

The script does NOT run on the real catalog yet — that happens in Task 9. This task ships only the script + its unit tests so the migration is reviewable in isolation.

**Files:**
- Create: `scripts/migrate-manifests-to-requires-backends.py`
- Test: `tests/scripts/test_migrate_manifests.py` (new)

- [ ] **Step 8.1: Write the failing test**

Create `tests/scripts/__init__.py` (empty if not present) and `tests/scripts/test_migrate_manifests.py`:

```python
"""Tests for the manifest migration script.

The script is one-shot but worth pinning while it runs against the real
catalog in Task 9 — bugs in inference would silently corrupt 30 manifests.
"""
import importlib.util
import sys
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

# Load the migration script as a module without making it part of the
# tinyagentos package — keeps it self-contained.
SCRIPT = Path("scripts/migrate-manifests-to-requires-backends.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("migrate_manifests", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def migrate_mod():
    return _load_module()


class TestInferBackends:
    def test_rkllama_method_maps_to_rkllama_backend(self, migrate_mod):
        manifest = {
            "id": "qwen2.5-3b-rkllm",
            "type": "model",
            "install": {"method": "rkllama"},
            "variants": [{"id": "default", "min_ram_mb": 4096}],
        }
        out = migrate_mod.infer_backends(manifest, manifest["variants"][0])
        assert out == [
            {"id": "rkllama", "targets": ["rockchip-rk3588"], "min_ram_mb": 4096}
        ]

    def test_rkllamacpp_method_maps_to_rk_llama_cpp_backend(self, migrate_mod):
        manifest = {
            "type": "model",
            "install": {"method": "rkllamacpp"},
            "variants": [{"id": "q4_k_m", "min_ram_mb": 4096}],
        }
        out = migrate_mod.infer_backends(manifest, manifest["variants"][0])
        assert out == [
            {"id": "rk-llama-cpp", "targets": ["rockchip-rk3588"], "min_ram_mb": 4096}
        ]

    def test_variant_backend_ollama_llama_cpp_maps_to_pair(self, migrate_mod):
        manifest = {
            "type": "model",
            "variants": [
                {
                    "id": "q4_k_m",
                    "min_ram_mb": 4096,
                    "backend": ["ollama", "llama-cpp"],
                },
            ],
        }
        out = migrate_mod.infer_backends(manifest, manifest["variants"][0])
        ids = [b["id"] for b in out]
        assert "ollama" in ids
        assert "llama-cpp" in ids
        for b in out:
            if b["id"] == "ollama":
                assert "apple-silicon" in b["targets"]
                assert "x86-cuda" in b["targets"]
                assert "cpu" in b["targets"]
            if b["id"] == "llama-cpp":
                assert b["targets"] == ["cpu"]

    def test_mlx_only_maps_to_apple_silicon(self, migrate_mod):
        manifest = {
            "type": "model",
            "variants": [{"id": "fp16", "min_ram_mb": 8192, "backend": ["mlx"]}],
        }
        out = migrate_mod.infer_backends(manifest, manifest["variants"][0])
        assert out == [
            {"id": "mlx", "targets": ["apple-silicon"], "min_ram_mb": 8192}
        ]


class TestRewriteManifest:
    def test_removes_deprecated_fields_and_adds_requires_backends(self, migrate_mod, tmp_path: Path):
        src = dedent(
            """
            id: qwen2.5-3b
            name: Qwen 2.5 3B Instruct
            type: model
            version: 2.5.0
            capabilities: [chat, tool-calling]
            variants:
              - id: q4_k_m
                size_mb: 1900
                min_ram_mb: 3072
                download_url: https://example/q4.gguf
                backend: [ollama, llama-cpp]
            hardware_tiers:
              cpu-only: {recommended: q4_k_m}
            """
        ).strip() + "\n"
        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text(src)

        migrate_mod.migrate_manifest(manifest_path, context_lookup={"qwen2.5-3b": 32768})

        out = yaml.safe_load(manifest_path.read_text())
        assert "install" not in out
        assert "backend" not in out["variants"][0]
        assert out["context_window"] == 32768
        deps = out["variants"][0]["requires"]["backends"]
        assert any(b["id"] == "ollama" for b in deps)
        assert any(b["id"] == "llama-cpp" for b in deps)
        # hardware_tiers is preserved as opaque metadata.
        assert "hardware_tiers" in out

    def test_skips_non_model_manifests(self, migrate_mod, tmp_path: Path):
        src = dedent(
            """
            id: some-service
            name: A Service
            type: service
            version: 1.0.0
            install: {method: docker, image: foo/bar}
            """
        ).strip() + "\n"
        p = tmp_path / "manifest.yaml"
        p.write_text(src)
        before = p.read_text()
        migrate_mod.migrate_manifest(p, context_lookup={})
        assert p.read_text() == before  # unchanged
```

- [ ] **Step 8.2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/scripts/test_migrate_manifests.py -v`
Expected: file-not-found because the migration script doesn't exist yet.

- [ ] **Step 8.3: Implement the migration script**

Create `scripts/migrate-manifests-to-requires-backends.py`:

```python
#!/usr/bin/env python3
"""One-shot migration of model manifests to the new requires.backends shape.

Reads each ``app-catalog/models/*/manifest.yaml`` and rewrites it:

- Adds ``context_window`` (top-level) from a curated lookup table.
- Adds ``variants[].requires.backends`` inferred from legacy
  ``install.method`` and ``variants[].backend``.
- Removes the legacy ``install`` block and ``variants[].backend`` field.
- Preserves ``hardware_tiers`` (now opaque metadata).

Run from repo root:

    python scripts/migrate-manifests-to-requires-backends.py [--dry-run]

Manual audit pass (separate, after running this) covers context_window
values not in the lookup table and any backend-specific quant edge cases.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

# Curated lookup — populated for the highest-traffic models. Anything not
# in here is left at 0 and flagged in the migration report for the manual
# audit pass to fix.
DEFAULT_CONTEXT_WINDOW: dict[str, int] = {
    "qwen2.5-3b": 32768,
    "qwen2.5-7b": 32768,
    "qwen2.5-14b": 32768,
    "qwen3-4b": 32768,
    "qwen3-8b": 32768,
    "gemma-3-1b": 32768,
    "gemma-3-4b": 128000,
    "gemma-3-12b": 128000,
    "deepseek-r1-14b": 32768,
    "deepseek-coder-v2-lite": 32768,
    "command-r-35b": 128000,
    "granite-3.1-2b": 32768,
    "granite-3.1-8b": 32768,
    "gemma-2-2b": 8192,
    "gemma-2-9b": 8192,
    "bge-large-en-v1.5": 512,
    "bge-m3": 8192,
    "bge-small-en-v1.5": 512,
    "bge-reranker-v2-m3": 8192,
    "qwen3-embedding-0.6b": 32768,
    "qwen3-reranker-0.6b": 32768,
}

# Backend ID → (targets, default-min-ram-fallback).  RAM fallback is only used
# when the legacy variant doesn't declare ``min_ram_mb``.
BACKEND_TARGETS: dict[str, tuple[list[str], int]] = {
    "rkllama": (["rockchip-rk3588"], 2048),
    "rk-llama-cpp": (["rockchip-rk3588"], 2048),
    "ollama": (["apple-silicon", "x86-cuda", "x86-vulkan", "cpu"], 4096),
    "llama-cpp": (["cpu"], 4096),
    "mlx": (["apple-silicon"], 4096),
    "vllm": (["x86-cuda"], 8192),
    "comfyui": (["x86-cuda", "x86-vulkan", "apple-silicon"], 4096),
    "transformers": (["x86-cuda", "x86-vulkan", "cpu"], 8192),
}


def _backend_dep(bid: str, min_ram: int) -> dict:
    targets, fallback = BACKEND_TARGETS.get(bid, (["cpu"], 4096))
    return {
        "id": bid,
        "targets": list(targets),
        "min_ram_mb": int(min_ram or fallback),
    }


def infer_backends(manifest: dict, variant: dict) -> list[dict]:
    """Build a requires.backends list from legacy install.method + variant.backend."""
    method = (manifest.get("install") or {}).get("method")
    legacy_backends = list(variant.get("backend") or [])
    min_ram = int(variant.get("min_ram_mb") or 0)

    out: list[dict] = []

    # Method-driven mapping — special-cases for rkllama/rkllamacpp.
    if method == "rkllama":
        out.append(_backend_dep("rkllama", min_ram))
    elif method == "rkllamacpp":
        out.append(_backend_dep("rk-llama-cpp", min_ram))

    # Variant-declared backends — cumulative with the method-derived dep.
    for bid in legacy_backends:
        # Some manifests use "llama.cpp" with a dot — normalize.
        normalized = "llama-cpp" if bid in ("llama.cpp", "llama-cpp") else bid
        if any(d["id"] == normalized for d in out):
            continue
        out.append(_backend_dep(normalized, min_ram))

    return out


def migrate_manifest(path: Path, *, context_lookup: dict[str, int]) -> bool:
    """Rewrite a single manifest file in place. Returns True if changed."""
    raw = path.read_text()
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        return False
    if data.get("type") != "model":
        return False

    changed = False
    mid = data.get("id", "")

    # Set context_window if missing.
    if "context_window" not in data:
        data["context_window"] = int(context_lookup.get(mid, 0))
        changed = True

    # Rewrite each variant.
    for v in data.get("variants") or []:
        if not isinstance(v, dict):
            continue
        backends = infer_backends(data, v)
        if backends:
            v.setdefault("requires", {})
            v["requires"]["backends"] = backends
            changed = True
        if "backend" in v:
            del v["backend"]
            changed = True

    # Drop top-level install block (legacy).
    if "install" in data:
        del data["install"]
        changed = True

    if changed:
        path.write_text(
            yaml.safe_dump(
                data, sort_keys=False, allow_unicode=True, default_flow_style=False
            )
        )
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path("app-catalog/models"),
        help="Catalog directory to migrate (default: app-catalog/models)"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"error: {args.root} is not a directory", file=sys.stderr)
        return 2

    changed: list[str] = []
    skipped: list[str] = []
    for manifest_path in sorted(args.root.rglob("manifest.yaml")):
        if args.dry_run:
            data = yaml.safe_load(manifest_path.read_text())
            if isinstance(data, dict) and data.get("type") == "model":
                changed.append(str(manifest_path))
            else:
                skipped.append(str(manifest_path))
            continue
        if migrate_manifest(manifest_path, context_lookup=DEFAULT_CONTEXT_WINDOW):
            changed.append(str(manifest_path))
        else:
            skipped.append(str(manifest_path))

    print(f"\nMigrated: {len(changed)} manifests")
    print(f"Skipped : {len(skipped)} manifests (non-model)")
    if not args.dry_run:
        # Flag manifests where context_window stayed at 0 — these need the
        # manual audit pass.
        zero_ctx: list[str] = []
        for p in changed:
            data = yaml.safe_load(Path(p).read_text())
            if isinstance(data, dict) and data.get("context_window", 0) == 0:
                zero_ctx.append(data.get("id", p))
        if zero_ctx:
            print(
                f"\n⚠ {len(zero_ctx)} manifests have context_window=0 — fix in audit pass:"
            )
            for mid in zero_ctx:
                print(f"  - {mid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 8.4: Run the migration script tests**

Run: `PYTHONPATH=. pytest tests/scripts/test_migrate_manifests.py -v`
Expected: 5 passed

- [ ] **Step 8.5: Commit**

```bash
git add scripts/migrate-manifests-to-requires-backends.py tests/scripts/test_migrate_manifests.py tests/scripts/__init__.py
git commit -m "feat(scripts): manifest migration script with unit tests (does not run on catalog yet)"
```

---

## Task 9: Run migration on catalog + rewrite dispatcher + extend audit (atomic)

This is the load-bearing change. Runs the migration script, rewrites the install dispatcher to use the resolver, extends the audit script to forbid deprecated fields, and verifies the test suite still passes. All in one commit because the migration breaks the old dispatcher and the new dispatcher needs the migrated manifests — they're truly atomic.

**Files:**
- Modify: `app-catalog/models/*/manifest.yaml` (~30 model manifests)
- Modify: `tinyagentos/routes/store_install.py` (rewrite dispatcher)
- Modify: `scripts/audit-manifests.py` (extend rules)
- Test: `tests/routes/test_store_install_v2.py` (new tests)

- [ ] **Step 9.1: Run the migration script**

Run: `python scripts/migrate-manifests-to-requires-backends.py`
Expected output ends with a `Migrated: N manifests` line and possibly a list of manifests with `context_window=0`.

- [ ] **Step 9.2: Manual audit pass — fix `context_window=0` cases**

For every manifest reported with `context_window: 0` in step 9.1, look up the
real value:

1. Visit the model's HuggingFace page (the `homepage:` URL in the manifest).
2. Open `config.json` (Files tab → `config.json` → Raw).
3. Read `max_position_embeddings` (or `n_ctx_train` for some llama.cpp variants).
4. Set `context_window: <int>` in the manifest.

For each manifest where the migration script added incorrect backends (rare —
only for multimodal / specialty quants like AWQ where the inferred backends
don't match reality), edit the manifest manually.

Common fixes:
- AWQ quants → `requires.backends` should be `[{id: vllm, ...}]` only.
- Vision-language → add `transformers` as a fallback backend on `cpu`.
- Embedding models → already covered if they had `backend: [transformers]`; otherwise add transformers manually.

- [ ] **Step 9.3: Write the dispatcher integration test FIRST**

Create `tests/routes/test_store_install_v2.py`:

```python
"""Integration tests for the resolver-driven /api/store/install-v2 dispatcher.

Mocks AppRegistry + ClusterManager so we can drive the dispatcher through
its three branches (use, install_chain, force-archive) without needing a
real worker. Real installer side effects are mocked at the
`get_installer` boundary.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.catalog.resolver import DeviceCapability


def make_qwen_manifest():
    """In-memory manifest matching the post-migration shape."""
    m = MagicMock()
    m.id = "qwen2.5-3b"
    m.type = "model"
    m.variants = [
        {
            "id": "q4_k_m",
            "size_mb": 1900,
            "download_url": "https://example/q4.gguf",
            "requires": {
                "backends": [
                    {"id": "rk-llama-cpp", "targets": ["rockchip-rk3588"], "min_ram_mb": 4096},
                ],
            },
        },
    ]
    m.context_window = 32768
    m.hardware_tiers = {}
    return m


def make_backend_service():
    m = MagicMock()
    m.id = "rk-llama-cpp"
    m.type = "service"
    m.install = {"method": "script", "script": "scripts/install-rkllamacpp.sh"}
    m.requires = {}
    m.hardware_tiers = {}
    return m


@pytest.fixture
def fake_registry():
    reg = MagicMock()
    qwen = make_qwen_manifest()
    backend = make_backend_service()
    reg.get_app = MagicMock(side_effect=lambda app_id: {
        "qwen2.5-3b": qwen,
        "rk-llama-cpp": backend,
    }.get(app_id))
    reg.mark_installed = MagicMock()
    return reg


@pytest.fixture
def pi_capability():
    return DeviceCapability(
        device_id="local",
        targets=("rockchip-rk3588", "cpu"),
        total_ram_mb=16384,
        total_vram_mb=0,
        free_disk_mb=50_000,
        installed_backends=(),
    )


class TestInstallChainHappyPath:
    @pytest.mark.asyncio
    async def test_chains_backend_then_model(self, client, fake_registry, pi_capability):
        client._transport.app.state.registry = fake_registry
        # Patch the capability snapshot the dispatcher pulls.
        with patch(
            "tinyagentos.routes.store_install.get_device_capability",
            new=AsyncMock(return_value=pi_capability),
        ), patch(
            "tinyagentos.routes.store_install.get_installer"
        ) as mock_get:
            backend_inst = MagicMock()
            backend_inst.install = AsyncMock(return_value={"success": True, "method": "script"})
            model_inst = MagicMock()
            model_inst.install = AsyncMock(return_value={"success": True, "runtime_location": {"host": "localhost", "port": 8090}})
            mock_get.side_effect = [backend_inst, model_inst]
            r = await client.post("/api/store/install-v2", json={
                "manifest_id": "qwen2.5-3b",
                "variant_id": "q4_k_m",
            })
        assert r.status_code == 200
        body = r.json()
        assert body["chain"][0]["step"] == "backend"
        assert body["chain"][0]["status"] == "installed"
        assert body["chain"][1]["step"] == "model"
        assert body["chain"][1]["status"] == "installed"


class TestInstallChainBackendFailure:
    @pytest.mark.asyncio
    async def test_returns_500_when_backend_install_fails(self, client, fake_registry, pi_capability):
        client._transport.app.state.registry = fake_registry
        with patch(
            "tinyagentos.routes.store_install.get_device_capability",
            new=AsyncMock(return_value=pi_capability),
        ), patch(
            "tinyagentos.routes.store_install.get_installer"
        ) as mock_get:
            backend_inst = MagicMock()
            backend_inst.install = AsyncMock(return_value={"success": False, "error": "build failed"})
            model_inst = MagicMock()
            model_inst.install = AsyncMock(return_value={"success": True})
            mock_get.side_effect = [backend_inst, model_inst]
            r = await client.post("/api/store/install-v2", json={
                "manifest_id": "qwen2.5-3b",
                "variant_id": "q4_k_m",
            })
        assert r.status_code == 500
        assert "backend" in r.json()["error"].lower()


class TestResolveErrorReturns422:
    @pytest.mark.asyncio
    async def test_returns_structured_error_with_suggestions(self, client, fake_registry):
        # Tiny device that cannot run any variant.
        tiny = DeviceCapability(
            device_id="tiny",
            targets=("cpu",),
            total_ram_mb=1024,
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=(),
        )
        client._transport.app.state.registry = fake_registry
        with patch(
            "tinyagentos.routes.store_install.get_device_capability",
            new=AsyncMock(return_value=tiny),
        ):
            r = await client.post("/api/store/install-v2", json={
                "manifest_id": "qwen2.5-3b",
                "variant_id": "q4_k_m",
            })
        assert r.status_code == 422
        body = r.json()
        assert "near_miss" in body
        assert "suggestions" in body
        assert isinstance(body["suggestions"], list)
```

- [ ] **Step 9.4: Run the test (it should fail because the dispatcher isn't rewritten yet)**

Run: `PYTHONPATH=. pytest tests/routes/test_store_install_v2.py -v`
Expected: failures because `get_device_capability` doesn't exist yet and the dispatcher still uses the old shape.

- [ ] **Step 9.5: Rewrite `tinyagentos/routes/store_install.py`**

The full rewrite is large. Replace the body of `install_app` (around store_install.py:80–end) with a resolver-driven version. Read the existing file and replace:

```python
"""Store install dispatcher driven by the manifest dependency resolver.

Reads variant.requires.backends, asks the resolver which backend should
serve the model on the target device, and (when the backend is missing)
recursively installs that backend's service manifest first via this same
dispatcher. Recursion is bounded at one level — backend service manifests
must not declare requires.backends themselves (enforced by the audit
script).
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.catalog.resolver import (
    DeviceCapability,
    ResolveErr,
    ResolveOk,
    classify,
    resolve,
)
from tinyagentos.cluster.capabilities import hardware_to_targets
from tinyagentos.installers.base import get_installer

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_device_capability(request: Request, target_remote: str | None) -> DeviceCapability:
    """Build a DeviceCapability snapshot for the (local | remote) target."""
    if not target_remote or target_remote == "local":
        hp = getattr(request.app.state, "hardware_profile", None)
        hw = getattr(hp, "hardware", {}) if hp else {}
        targets = tuple(hardware_to_targets(hw))
        ram_mb = int(hw.get("ram_mb", 0) or 0)
        vram_mb = int((hw.get("gpu") or {}).get("vram_mb", 0) or 0)
        # Free disk lives on the hardware profile's disk dict.
        disk_total = int((hw.get("disk") or {}).get("total_gb", 0) or 0) * 1024
        free_disk_mb = max(0, disk_total - 0)  # we don't track used; assume room.
        registry = getattr(request.app.state, "registry", None)
        installed_backends = tuple(
            b for b in (registry.installed_app_ids() if registry else [])
            if b in {
                "rkllama", "rk-llama-cpp", "ollama", "llama-cpp",
                "mlx", "vllm", "comfyui", "transformers",
            }
        )
        return DeviceCapability(
            device_id="local",
            targets=targets,
            total_ram_mb=ram_mb,
            total_vram_mb=vram_mb,
            free_disk_mb=free_disk_mb,
            installed_backends=installed_backends,
        )

    # Remote: query the worker registry's last-known capacity.
    cluster = getattr(request.app.state, "cluster_manager", None)
    if cluster is None:
        return DeviceCapability(
            device_id=target_remote,
            targets=("cpu",), total_ram_mb=0, total_vram_mb=0,
            free_disk_mb=0, installed_backends=(),
        )
    worker = cluster.get_worker(target_remote)
    if worker is None:
        return DeviceCapability(
            device_id=target_remote,
            targets=("cpu",), total_ram_mb=0, total_vram_mb=0,
            free_disk_mb=0, installed_backends=(),
        )
    targets = tuple(hardware_to_targets(worker.hardware or {}))
    ram_mb = int((worker.hardware or {}).get("ram_mb", 0) or 0)
    vram_mb = int(((worker.hardware or {}).get("gpu") or {}).get("vram_mb", 0) or 0)
    disk_cap = max(0, worker.storage_cap_bytes - worker.storage_used_bytes) // (1024 * 1024)
    installed_backends = tuple(
        b.get("name", "") for b in (worker.backends or []) if b.get("name")
    )
    return DeviceCapability(
        device_id=target_remote,
        targets=targets,
        total_ram_mb=ram_mb,
        total_vram_mb=vram_mb,
        free_disk_mb=int(disk_cap),
        installed_backends=installed_backends,
    )


@router.post("/api/store/install-v2")
async def install_app(request: Request):
    """Resolver-driven install. Chains backend → model in one user click."""
    body = await request.json()
    manifest_id = body.get("manifest_id") or body.get("app_id")
    variant_id = body.get("variant_id", "auto")
    target_remote = body.get("target_remote") or None
    force = bool(body.get("force", False))

    registry = request.app.state.registry
    manifest = registry.get_app(manifest_id) if registry else None
    if manifest is None:
        return JSONResponse({"error": f"manifest {manifest_id!r} not found"}, status_code=404)

    device = await get_device_capability(request, target_remote)
    manifest_dict = {
        "id": manifest.id,
        "type": manifest.type,
        "variants": manifest.variants,
        "context_window": getattr(manifest, "context_window", 0),
    }
    result = resolve(manifest_dict, variant_id, device, force=force)
    if isinstance(result, ResolveErr):
        return JSONResponse(
            {
                "error": result.reason,
                "near_miss": result.near_miss,
                "suggestions": result.suggestions,
            },
            status_code=422,
        )

    chain: list[dict] = []

    # Step 5: install the backend if missing.
    if result.action == "install_chain":
        backend_manifest = registry.get_app(result.backend_id)
        if backend_manifest is None:
            return JSONResponse(
                {"error": f"backend service manifest {result.backend_id!r} not in catalog"},
                status_code=500,
            )
        backend_method = (backend_manifest.install or {}).get("method")
        if not backend_method:
            return JSONResponse(
                {"error": f"backend {result.backend_id!r} has no install.method"},
                status_code=500,
            )
        backend_installer = get_installer(backend_method)
        be_result = await backend_installer.install(
            result.backend_id,
            install_config=backend_manifest.install,
        )
        if not be_result.get("success"):
            return JSONResponse(
                {
                    "error": (
                        f"backend install failed for {result.backend_id!r}: "
                        f"{be_result.get('error', 'unknown')}"
                    ),
                    "chain": chain + [{"step": "backend", "id": result.backend_id, "status": "failed"}],
                },
                status_code=500,
            )
        registry.mark_installed(result.backend_id, metadata={"method": backend_method})
        chain.append({"step": "backend", "id": result.backend_id, "status": "installed"})

    # Step 6: install the model via the chosen backend's installer.
    chosen_variant = next(
        (v for v in manifest.variants if isinstance(v, dict) and v.get("id") == result.variant_id),
        None,
    )
    if chosen_variant is None:
        return JSONResponse(
            {"error": f"variant {result.variant_id!r} not found in manifest"},
            status_code=500,
        )
    model_installer = get_installer(result.backend_id)
    install_config = manifest.install or {}
    install_config = dict(install_config)
    install_config["backend"] = result.backend_id
    model_result = await model_installer.install(
        manifest.id,
        install_config=install_config,
        variant=chosen_variant,
        target_remote=target_remote,
    )
    if not model_result.get("success"):
        return JSONResponse(
            {
                "error": f"model install failed: {model_result.get('error', 'unknown')}",
                "chain": chain + [{"step": "model", "id": manifest.id, "status": "failed"}],
            },
            status_code=500,
        )
    registry.mark_installed(
        manifest.id,
        metadata={
            "backend": result.backend_id,
            "variant": result.variant_id,
            "runtime_location": model_result.get("runtime_location"),
        },
    )
    chain.append({"step": "model", "id": manifest.id, "status": "installed"})

    return {"chain": chain, "compat": classify(manifest_dict, device)}
```

(The above replaces the old store_install.py. If your file diverges, splice in this implementation; preserve any helpers like `_resolve_host` if they're used by other endpoints in the same file.)

- [ ] **Step 9.6: Extend the audit script**

Replace `scripts/audit-manifests.py` with the extended rules:

```python
#!/usr/bin/env python3
"""Audit catalog manifests for the requires.backends schema invariants.

After the migration to requires.backends, every model manifest must:
- declare context_window (non-zero)
- declare variants[].requires.backends with at least one entry
- not declare the deprecated install.method or variants[].backend fields
- only reference backend IDs that exist as service manifests
- only reference targets in the catalog-wide enum

Service manifests must NOT declare requires.backends (they are leaves
in the dependency graph).

Usage:
    python scripts/audit-manifests.py [--root app-catalog]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

VALID_TARGETS = {
    "rockchip-rk3588",
    "apple-silicon",
    "x86-cuda",
    "x86-vulkan",
    "cpu",
}


def _load(path: Path) -> dict | None:
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def audit(root: Path) -> int:
    issues: list[str] = []

    # Step 1: collect every backend service manifest id.
    services_root = root / "services"
    backend_ids: set[str] = set()
    for sp in sorted(services_root.rglob("manifest.yaml")):
        sd = _load(sp)
        if not sd or sd.get("type") != "service":
            continue
        backend_ids.add(sd.get("id", sp.parent.name))
        # Service manifests must not declare requires.backends.
        if (sd.get("requires") or {}).get("backends"):
            issues.append(
                f"{sp}: service manifest declares requires.backends — "
                "backends must be leaves (one-level recursion guard)"
            )

    # Step 2: audit every model manifest.
    models_root = root / "models"
    for mp in sorted(models_root.rglob("manifest.yaml")):
        data = _load(mp)
        if not data or data.get("type") != "model":
            continue
        mid = data.get("id", mp.parent.name)

        # Deprecated fields must be absent.
        if "install" in data and (data["install"] or {}).get("method"):
            issues.append(f"{mid}: deprecated install.method still present — migrate to requires.backends")

        # context_window must be non-zero.
        if int(data.get("context_window") or 0) <= 0:
            issues.append(f"{mid}: context_window missing or 0 — populate from HF config.json")

        variants = data.get("variants") or []
        if not variants:
            issues.append(f"{mid}: model has no variants")
            continue

        for v in variants:
            if not isinstance(v, dict):
                continue
            vid = v.get("id", "?")
            if "backend" in v:
                issues.append(f"{mid}/{vid}: deprecated variants[].backend still present")
            deps = (v.get("requires") or {}).get("backends") or []
            if not deps:
                issues.append(f"{mid}/{vid}: requires.backends missing or empty")
                continue
            for d in deps:
                if not isinstance(d, dict):
                    continue
                bid = d.get("id", "?")
                if bid not in backend_ids:
                    issues.append(f"{mid}/{vid}: references unknown backend id {bid!r}")
                for t in d.get("targets") or []:
                    if t not in VALID_TARGETS:
                        issues.append(f"{mid}/{vid}: target {t!r} not in catalog enum")
                if int(d.get("min_ram_mb") or 0) <= 0:
                    issues.append(f"{mid}/{vid}: backend {bid!r} has min_ram_mb=0")

    if not issues:
        print("clean: catalog matches requires.backends schema")
        return 0
    print(f"\n{len(issues)} manifest issue(s):\n")
    for line in issues:
        print(f"  - {line}")
    print()
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path("app-catalog"),
        help="Catalog root containing models/ and services/ (default: app-catalog)"
    )
    args = parser.parse_args()
    if not args.root.is_dir():
        print(f"error: {args.root} is not a directory", file=sys.stderr)
        return 2
    return audit(args.root)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 9.7: Run the audit**

Run: `python scripts/audit-manifests.py`
Expected: `clean: catalog matches requires.backends schema`. If any issues are listed, fix them in the model manifests until the audit passes.

- [ ] **Step 9.8: Run the dispatcher tests**

Run: `PYTHONPATH=. pytest tests/routes/test_store_install_v2.py -v`
Expected: 3 passed

- [ ] **Step 9.9: Run the full test suite**

Run: `PYTHONPATH=. pytest tests/ -x -q`
Expected: green (or only pre-existing failures unrelated to this change).

- [ ] **Step 9.10: Commit**

```bash
git add app-catalog/models/ tinyagentos/routes/store_install.py scripts/audit-manifests.py tests/routes/test_store_install_v2.py
git commit -m "feat(catalog): migrate manifests to requires.backends + resolver-driven dispatcher"
```

---

## Task 10: `POST /api/store/resolve` endpoint

A wrapper around the resolver so the frontend can ask the server "is this model compatible with my cluster?" without re-implementing the algorithm in TypeScript.

**Files:**
- Modify: `tinyagentos/routes/store.py` (add endpoint)
- Test: `tests/routes/test_store_resolve.py` (new)

- [ ] **Step 10.1: Write the failing test**

Create `tests/routes/test_store_resolve.py`:

```python
"""Tests for POST /api/store/resolve — resolver wrapper for the frontend."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.catalog.resolver import DeviceCapability


def make_qwen_manifest():
    m = MagicMock()
    m.id = "qwen2.5-3b"
    m.type = "model"
    m.variants = [
        {
            "id": "q4_k_m",
            "size_mb": 1900,
            "requires": {
                "backends": [
                    {"id": "rk-llama-cpp", "targets": ["rockchip-rk3588"], "min_ram_mb": 4096},
                ],
            },
        },
    ]
    m.context_window = 32768
    return m


@pytest.fixture
def fake_registry():
    reg = MagicMock()
    reg.get_app = MagicMock(return_value=make_qwen_manifest())
    return reg


class TestStoreResolveEndpoint:
    @pytest.mark.asyncio
    async def test_returns_resolve_ok_with_classification(self, client, fake_registry):
        client._transport.app.state.registry = fake_registry
        pi = DeviceCapability(
            device_id="local",
            targets=("rockchip-rk3588", "cpu"),
            total_ram_mb=16384,
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=("rk-llama-cpp",),
        )
        with patch(
            "tinyagentos.routes.store.get_device_capability",
            new=AsyncMock(return_value=pi),
        ):
            r = await client.post("/api/store/resolve", json={
                "manifest_id": "qwen2.5-3b",
                "variant_id": "auto",
            })
        assert r.status_code == 200
        body = r.json()
        assert body["result"] == "ok"
        assert body["backend_id"] == "rk-llama-cpp"
        assert body["action"] in ("use", "install_chain")
        assert body["compat"] in ("green", "amber", "red")

    @pytest.mark.asyncio
    async def test_returns_resolve_err_with_advice(self, client, fake_registry):
        client._transport.app.state.registry = fake_registry
        tiny = DeviceCapability(
            device_id="local",
            targets=("cpu",),
            total_ram_mb=1024,
            total_vram_mb=0,
            free_disk_mb=50_000,
            installed_backends=(),
        )
        with patch(
            "tinyagentos.routes.store.get_device_capability",
            new=AsyncMock(return_value=tiny),
        ):
            r = await client.post("/api/store/resolve", json={
                "manifest_id": "qwen2.5-3b",
                "variant_id": "q4_k_m",
            })
        assert r.status_code == 200
        body = r.json()
        assert body["result"] == "err"
        assert "near_miss" in body
        assert "suggestions" in body
        assert body["compat"] == "red"
```

- [ ] **Step 10.2: Run the test to verify it fails**

Run: `PYTHONPATH=. pytest tests/routes/test_store_resolve.py -v`
Expected: 404s — endpoint doesn't exist yet.

- [ ] **Step 10.3: Add the endpoint**

In `tinyagentos/routes/store.py`, append:

```python
from tinyagentos.catalog.resolver import (
    DeviceCapability,
    ResolveErr,
    ResolveOk,
    classify,
    resolve,
)
from tinyagentos.routes.store_install import get_device_capability


@router.post("/api/store/resolve")
async def resolve_model(request: Request):
    """Wrapper around the resolver for the Store frontend.

    Returns a JSON envelope with ``result`` ("ok" | "err"), the resolver's
    structured payload, and the green/amber/red compatibility classification
    (so the Store can colour-code the card from a single round-trip).
    """
    body = await request.json()
    manifest_id = body.get("manifest_id") or body.get("app_id")
    variant_id = body.get("variant_id", "auto")
    target_remote = body.get("target_remote") or None
    force = bool(body.get("force", False))

    registry = request.app.state.registry
    manifest = registry.get_app(manifest_id) if registry else None
    if manifest is None:
        return JSONResponse({"error": f"manifest {manifest_id!r} not found"}, status_code=404)

    device = await get_device_capability(request, target_remote)
    manifest_dict = {
        "id": manifest.id,
        "type": manifest.type,
        "variants": manifest.variants,
        "context_window": getattr(manifest, "context_window", 0),
    }
    res = resolve(manifest_dict, variant_id, device, force=force)
    compat = classify(manifest_dict, device)

    if isinstance(res, ResolveOk):
        return {
            "result": "ok",
            "backend_id": res.backend_id,
            "variant_id": res.variant_id,
            "action": res.action,
            "compat": compat,
        }
    return {
        "result": "err",
        "reason": res.reason,
        "near_miss": res.near_miss,
        "suggestions": res.suggestions,
        "compat": compat,
    }
```

- [ ] **Step 10.4: Run tests**

Run: `PYTHONPATH=. pytest tests/routes/test_store_resolve.py -v`
Expected: 2 passed

- [ ] **Step 10.5: Commit**

```bash
git add tinyagentos/routes/store.py tests/routes/test_store_resolve.py
git commit -m "feat(routes): POST /api/store/resolve — server-side compatibility classification"
```

---

## Task 11: Frontend resolver type mirrors

Mirrors of the Python resolver types in TypeScript so the Store can read /api/store/resolve responses with type safety.

**Files:**
- Create: `desktop/src/apps/StoreApp/resolver-types.ts`

- [ ] **Step 11.1: Create the types file**

Create `desktop/src/apps/StoreApp/resolver-types.ts`:

```typescript
/**
 * Mirrors of the Python resolver types in tinyagentos/catalog/resolver.py.
 * The /api/store/resolve endpoint returns one of ResolveOkResp | ResolveErrResp
 * wrapped in an envelope. Keep these in sync if the Python types change.
 */

export interface BackendDep {
  id: string;
  targets: string[];
  min_ram_mb: number;
  min_vram_mb?: number;
}

export type Compat = "green" | "amber" | "red";

export interface ResolveOkResp {
  result: "ok";
  backend_id: string;
  variant_id: string;
  action: "use" | "install_chain";
  compat: Compat;
}

export interface ResolveErrResp {
  result: "err";
  reason: string;
  near_miss: {
    variant?: string;
    blocked_by?: "ram" | "vram" | "disk" | "target" | "schema";
    short_by_mb?: number;
  };
  suggestions: string[];
  compat: Compat;
}

export type ResolveResponse = ResolveOkResp | ResolveErrResp;

/** POST /api/store/resolve helper. */
export async function resolveModel(
  manifestId: string,
  variantId: string = "auto",
  options: { targetRemote?: string; force?: boolean } = {},
): Promise<ResolveResponse> {
  const res = await fetch("/api/store/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      manifest_id: manifestId,
      variant_id: variantId,
      target_remote: options.targetRemote ?? null,
      force: options.force ?? false,
    }),
  });
  return res.json();
}
```

- [ ] **Step 11.2: Verify TypeScript compilation**

Run: `cd desktop && npx tsc -b`
Expected: no new errors. (Don't worry about pre-existing warnings.)

- [ ] **Step 11.3: Commit**

```bash
git add desktop/src/apps/StoreApp/resolver-types.ts
git commit -m "feat(desktop): TypeScript mirrors for resolver types + resolveModel helper"
```

---

## Task 12: Extend `/api/cluster/install-targets` with `targets[]`

The existing endpoint returns each install target with a `tier_id` (legacy fuzzy string). The new resolver wants the concrete `targets[]` enum. Add it alongside `tier_id` so consumers can migrate gradually without a flag day.

**Files:**
- Modify: `tinyagentos/routes/cluster.py:list_install_targets`
- Test: `tests/routes/test_install_targets_resolver.py` (new)

- [ ] **Step 12.1: Write the failing test**

Create `tests/routes/test_install_targets_resolver.py`:

```python
"""install-targets must include targets[] for the resolver-driven Store filter."""
import pytest


class TestInstallTargetsResolverFields:
    @pytest.mark.asyncio
    async def test_each_target_has_targets_list(self, client):
        r = await client.get("/api/cluster/install-targets")
        assert r.status_code == 200
        targets = r.json()
        assert len(targets) >= 1
        for t in targets:
            assert "targets" in t
            assert isinstance(t["targets"], list)
            # At minimum every device has cpu in its targets list.
            assert "cpu" in t["targets"]

    @pytest.mark.asyncio
    async def test_local_target_has_at_least_cpu(self, client):
        r = await client.get("/api/cluster/install-targets")
        local = next(t for t in r.json() if t["name"] == "local")
        assert "cpu" in local["targets"]
```

- [ ] **Step 12.2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/routes/test_install_targets_resolver.py -v`
Expected: KeyError on `t["targets"]` because the field doesn't exist yet.

- [ ] **Step 12.3: Extend the endpoint**

In `tinyagentos/routes/cluster.py:list_install_targets`, in the loop that builds `targets: list[dict]`, populate a `targets` field for the local entry and each remote entry. Replace the `local` entry construction with:

```python
    from tinyagentos.cluster.capabilities import hardware_to_targets

    hp = getattr(request.app.state, "hardware_profile", None)
    local_hw = getattr(hp, "hardware", {}) if hp else {}
    targets: list[dict] = [
        {
            "name": "local",
            "label": "This controller",
            "type": "local",
            "tier_id": getattr(hp, "profile_id", "") if hp else "",
            "targets": hardware_to_targets(local_hw),
            "friendly_name": "Controller",
        }
    ]
```

And in the loop that appends remote workers, change the appended dict to include `targets`:

```python
            targets.append({
                "name": name,
                "label": name,
                "type": "remote",
                "addr": r.get("addr", ""),
                "tier_id": worker_tiers.get(name, ""),
                "targets": hardware_to_targets(
                    next(
                        (w.hardware for w in (cluster.get_workers() if cluster else []) if w.name == name),
                        {},
                    )
                ),
                "friendly_name": name,
            })
```

- [ ] **Step 12.4: Run tests**

Run: `PYTHONPATH=. pytest tests/routes/test_install_targets_resolver.py -v`
Expected: 2 passed

- [ ] **Step 12.5: Commit**

```bash
git add tinyagentos/routes/cluster.py tests/routes/test_install_targets_resolver.py
git commit -m "feat(cluster): /api/cluster/install-targets returns targets[] alongside tier_id"
```

---

## Task 13: Store filter — replace tier_id matching with /api/store/resolve

This is the task that fixes the "Store shows no compatible models on Pi" regression. The current filter matches `manifest.hardware_tiers[device.tier_id]` — fragile across manifests with slightly different tier vocabulary. Replace with a per-card call to `/api/store/resolve` that returns the resolver's authoritative `compat` classification.

**Files:**
- Modify: `desktop/src/apps/StoreApp/filter.ts` (replace tier-string matching)
- Modify: `desktop/src/apps/StoreApp/index.tsx` (call resolver for visible cards)
- Test: `desktop/src/apps/StoreApp/filter.test.ts` (extend)

- [ ] **Step 13.1: Read the existing filter.ts to see what to replace**

Run: `cat desktop/src/apps/StoreApp/filter.ts`

Note the function signature of the current `filterModels` and how it consumes `selectedDevices` / `selectedBackends`.

- [ ] **Step 13.2: Write a failing test for the new behaviour**

Append to `desktop/src/apps/StoreApp/filter.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { compatFromResolver } from "./filter";

describe("compatFromResolver", () => {
  it("treats green resolver result as compatible", () => {
    const compatMap = new Map<string, "green" | "amber" | "red">();
    compatMap.set("qwen2.5-3b", "green");
    expect(compatFromResolver("qwen2.5-3b", compatMap, false)).toBe(true);
  });

  it("treats amber as compatible", () => {
    const compatMap = new Map<string, "green" | "amber" | "red">();
    compatMap.set("qwen2.5-3b", "amber");
    expect(compatFromResolver("qwen2.5-3b", compatMap, false)).toBe(true);
  });

  it("treats red as incompatible by default", () => {
    const compatMap = new Map<string, "green" | "amber" | "red">();
    compatMap.set("qwen2.5-3b", "red");
    expect(compatFromResolver("qwen2.5-3b", compatMap, false)).toBe(false);
  });

  it("shows red when showIncompatible toggle is on", () => {
    const compatMap = new Map<string, "green" | "amber" | "red">();
    compatMap.set("qwen2.5-3b", "red");
    expect(compatFromResolver("qwen2.5-3b", compatMap, true)).toBe(true);
  });

  it("shows unknown manifests by default (no resolver entry → assume compatible)", () => {
    const compatMap = new Map<string, "green" | "amber" | "red">();
    expect(compatFromResolver("brand-new-model", compatMap, false)).toBe(true);
  });
});
```

- [ ] **Step 13.3: Run the test**

Run: `cd desktop && npx vitest run src/apps/StoreApp/filter.test.ts`
Expected: missing-export errors for `compatFromResolver`.

- [ ] **Step 13.4: Implement `compatFromResolver` and wire it into the filter pipeline**

In `desktop/src/apps/StoreApp/filter.ts`, add:

```typescript
import type { Compat } from "./resolver-types";

/**
 * Decide whether a model card should be shown given the resolver's
 * green/amber/red classification.
 *
 * - `green` and `amber` are always shown — the user's cluster can run
 *   the model (with or without acceleration).
 * - `red` is hidden by default but shown when the IncompatibleToggle is on.
 * - When the resolver hasn't classified the manifest yet (no entry in
 *   `compatMap`), default to showing it — incompatibility is an explicit
 *   negative signal, not a default.
 */
export function compatFromResolver(
  manifestId: string,
  compatMap: Map<string, Compat>,
  showIncompatible: boolean,
): boolean {
  const c = compatMap.get(manifestId);
  if (c === undefined) return true;
  if (c === "red") return showIncompatible;
  return true;
}
```

- [ ] **Step 13.5: Wire the resolver into `index.tsx`**

In `desktop/src/apps/StoreApp/index.tsx`, after the existing model fetch:

1. Import: `import { resolveModel, type Compat } from "./resolver-types"; import { compatFromResolver } from "./filter";`
2. Add a `useState<Map<string, Compat>>(new Map())` for `compatMap`.
3. Add a `useEffect` that — on first render or when the model list changes — calls `resolveModel(manifestId, "auto")` for each visible model in batches of 8 (avoid overwhelming the controller) and writes results into `compatMap`.
4. In the existing render loop, replace the legacy `filterModels(...)` compatibility check with: `compatFromResolver(model.id, compatMap, showIncompatible)`.

The exact diff varies based on the existing code; keep the rest of the filter pipeline (device pills, backend pills) intact.

- [ ] **Step 13.6: Run frontend tests**

Run: `cd desktop && npx vitest run`
Expected: green (the new test passes; nothing else regresses).

- [ ] **Step 13.7: Run dev server + check Store on local**

Run: `cd desktop && npm run dev` (in one terminal) and `python -m tinyagentos.app` (in another).
Open the Store, navigate to Models tab, and verify cards now show with their resolver-driven `compat` classification.

If any model that should clearly be compatible (e.g. a Qwen rkllm manifest on a Pi-NPU profile) shows as hidden, check `/api/store/resolve` directly:
```bash
curl -s -X POST http://localhost:6969/api/store/resolve \
     -H 'Content-Type: application/json' \
     -d '{"manifest_id": "qwen-2.5-3b-rkllm", "variant_id": "auto"}'
```
The response should be a `result: "ok"` with `compat: "green"`.

- [ ] **Step 13.8: Commit**

```bash
git add desktop/src/apps/StoreApp/filter.ts desktop/src/apps/StoreApp/filter.test.ts desktop/src/apps/StoreApp/index.tsx
git commit -m "feat(store): wire /api/store/resolve into filter; fixes 'no compatible models' on Pi"
```

---

## Task 14: Final verification + open PR

Run the full suite, lint, audit, build the desktop bundle, document the manual Pi smoke test, push, open PR.

- [ ] **Step 14.1: Run full Python test suite**

Run: `PYTHONPATH=. pytest tests/ -q`
Expected: green (or only pre-existing failures).

- [ ] **Step 14.2: Run lint**

Run: `PYTHONPATH=. ruff check tinyagentos/ tests/ scripts/`
Expected: clean.

- [ ] **Step 14.3: Run the audit script**

Run: `python scripts/audit-manifests.py`
Expected: `clean: catalog matches requires.backends schema`

- [ ] **Step 14.4: Run frontend tests**

Run: `cd desktop && npx vitest run`
Expected: green.

- [ ] **Step 14.5: Build the desktop bundle**

Run: `cd desktop && npm run build`
Expected: build succeeds; `static/desktop/index.html` and `static/desktop/assets/*` updated.

- [ ] **Step 14.6: Force-add the spec + plan if not already in PR**

```bash
git add -f docs/superpowers/specs/2026-05-06-manifest-dep-resolver-design.md
git add -f docs/superpowers/plans/2026-05-06-manifest-dep-resolver-plan.md
git status --short
```

If anything new is staged, commit it:
```bash
git commit -m "docs(plan): manifest-dep resolver implementation plan"
```

- [ ] **Step 14.7: Push and open PR**

```bash
git push -u origin feat/manifest-dep-resolver
gh pr create --base master --head feat/manifest-dep-resolver \
  --title "feat(catalog): manifest dependency resolver — schema, resolver, recursive install dispatcher" \
  --body "$(cat <<'EOF'
## Summary

Replaces implicit \`install.method\` coupling in catalog manifests with a per-variant \`requires.backends\` schema, a pure-function resolver, and a recursive install dispatcher that chains backend + model installs in one user click.

Spec: \`docs/superpowers/specs/2026-05-06-manifest-dep-resolver-design.md\`
Plan: \`docs/superpowers/plans/2026-05-06-manifest-dep-resolver-plan.md\`

## What ships

- \`tinyagentos/catalog/resolver.py\` — pure functions \`resolve()\` (auto + explicit variant, force flag, four gates) and \`classify()\` (green/amber/red).
- \`tinyagentos/cluster/capabilities.py\` — \`hardware_to_targets()\` derives the catalog targets enum from a worker hardware dict.
- \`tinyagentos/installers/script_installer.py\` — \`ScriptInstaller\` for backend service manifests using \`install: {method: script}\`.
- \`tinyagentos/routes/store_install.py\` — rewritten dispatcher that calls the resolver, recursively installs missing backends, returns a structured \`chain\` response.
- \`tinyagentos/routes/store.py\` — new \`POST /api/store/resolve\` endpoint for client-side compatibility classification.
- \`tinyagentos/routes/cluster.py\` — \`/api/cluster/install-targets\` now returns the new \`targets[]\` field alongside the legacy \`tier_id\`.
- \`scripts/migrate-manifests-to-requires-backends.py\` — one-shot migration; ran against the catalog as part of this PR.
- \`scripts/audit-manifests.py\` — extended to enforce the new schema (no deprecated fields, all backend IDs valid, all targets in enum, every variant has \`requires.backends\`, every model has \`context_window\`).
- \`app-catalog/models/*/manifest.yaml\` — migrated.
- \`desktop/src/apps/StoreApp/resolver-types.ts\` + \`filter.ts\` + \`index.tsx\` — frontend reads the new endpoint, fixes the "no compatible models" regression on Pi.

## Test plan

- [x] \`pytest tests/\` — green
- [x] \`ruff check\` — clean
- [x] \`python scripts/audit-manifests.py\` — clean
- [x] \`vitest run\` — green
- [x] \`npm run build\` — succeeds
- [ ] **Pi smoke test (manual, post-merge):**
  1. Pull master to Pi, apply update through Settings → Updates, watch the rebuild run.
  2. In the Store, navigate to Apps tab → uninstall \`rk-llama.cpp\`.
  3. Switch to Models tab — verify Qwen GGUF cards still show as compatible (green/amber border).
  4. Click Install on a Qwen GGUF that lists \`rk-llama-cpp\` as its first backend.
  5. Confirm-then-chain dialog appears: "This needs rk-llama.cpp on this device. Install both?" — click "Install Both".
  6. Watch the chain: backend installs first, then the GGUF downloads, llama-server starts on :8090.
  7. \`curl http://pi:8090/health\` → 200; chat through agent works.
- [ ] **Pi: confirm "Store shows no compatible models" regression is fixed.** Open Store on Pi, Models tab, observe at least the 14 Qwen rkllm manifests show as compatible.

## Out of scope

Separate work tracks (captured in agent memory):
- PR-B: default-backend swap on Pi-NPU (rk-llama.cpp by default; rkllama becomes lazy)
- Store color-coded card borders (uses \`classify\` output)
- Hardware-tier install templates / setup wizard
- Help / Guides app
- Archive-anyway lifecycle promotion (the \`force\` flag is shipped; the move-to-worker flow is its own track)
EOF
)"
```

- [ ] **Step 14.8: Final commit summary**

Verify the resulting PR shows ~14 commits, each focused, with green CI before merging.

---

## Self-review notes

After writing the plan I cross-checked it against the spec:

- Schema (Section "Schema") → Tasks 8 + 9 cover the migration; Task 10 ships the resolve endpoint that consumes the new schema.
- Resolver pure function with `resolve` + `classify` → Tasks 2-6.
- Recursive install dispatcher with confirm-then-chain semantics → Task 9. (The frontend confirm dialog is left to the existing Store install flow which already shows backend deps in the install modal — the dispatcher returns a chain response that the existing modal can render.)
- Force flag for archive-anyway → Tasks 3 + 5 (resolver) + Task 9 (dispatcher accepts and forwards the flag in the request body).
- Migration of all model manifests as a single mechanical task → Tasks 8 (script) + 9 (run on catalog).
- `context_window` field → Tasks 8 (seeding) + 9 (manual audit) + audit script enforces non-zero.
- Audit script extensions → Task 9.
- E2E Pi smoke test → Task 14, Step 14.7 test plan.
- Store filter regression fix → Task 13 explicitly named.
- `hardware_capability` integration with existing `worker_capacity.py` and `WorkerInfo` → Task 9, `get_device_capability()` reads from `cluster_manager.get_worker(...)`.

No placeholders found. Type names are consistent (`ResolveOk`, `ResolveErr`, `BackendDep`, `DeviceCapability`) across all tasks.
