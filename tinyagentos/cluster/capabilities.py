"""Helpers to derive potential capabilities from worker hardware and the app catalog.

The catalog manifests (app-catalog/models/*/manifest.yaml) declare
``hardware_tiers`` keys like ``x86-cuda-12gb``.  For GPU-accelerated tiers
(cuda / rocm) the ``{n}gb`` suffix is VRAM; for every other accelerator
type (cpu, npu, vulkan, apple-silicon) it is system RAM.  This mirrors
the logic in ``HardwareProfile.profile_id`` with one correction: CUDA/ROCm
tiers use VRAM so that a 64 GB RAM machine with a 12 GB RTX 3060 maps to
``x86-cuda-12gb``, not ``x86-cuda-64gb``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tinyagentos.registry import AppRegistry


_RAM_BUCKETS_GB = (2, 4, 8, 16, 32, 64, 128)


def _snap_to_bucket(raw_gb: int) -> int:
    """Snap raw GB up to the nearest canonical bucket used by catalog manifests.

    Catalog manifests only list canonical sizes (4gb / 8gb / 16gb / ...);
    if a device reports a value between buckets (e.g. 15GB on a 16GB Pi after
    kernel reservation), snap up so the tier_id matches the bucket the
    device IS, not the slightly-smaller value it self-reports.
    """
    raw_gb = max(1, raw_gb)
    for bucket in _RAM_BUCKETS_GB:
        if raw_gb <= bucket:
            return bucket
    return _RAM_BUCKETS_GB[-1]


def worker_tier_id(hardware: dict) -> str:
    """Derive a catalog-compatible tier id from a worker's hardware dict.

    Parameters
    ----------
    hardware:
        The ``hardware`` dict stored on a :class:`~tinyagentos.cluster.worker_protocol.WorkerInfo`
        (originally reported by the worker agent via ``/api/cluster/workers``).

    Returns
    -------
    str
        A tier id like ``x86-cuda-12gb`` or ``arm-npu-16gb``.
    """
    if not hardware:
        return "cpu-only"

    cpu_raw = hardware.get("cpu") or {}
    # Guard: workers running older agent versions may send cpu as a plain string
    cpu: dict = cpu_raw if isinstance(cpu_raw, dict) else {}
    arch_raw = cpu.get("arch", "")
    arch = "arm" if arch_raw in ("aarch64", "armv7l", "arm64") else "x86"

    gpu = hardware.get("gpu") or {}
    npu = hardware.get("npu") or {}
    ram_mb = hardware.get("ram_mb", 0)

    gpu_type = gpu.get("type", "none") or "none"
    npu_type = npu.get("type", "none") or "none"

    # Determine accelerator class
    if npu_type != "none":
        accel = "npu"
        # NPU tiers use RAM gb
        gb = _snap_to_bucket(ram_mb // 1024)
        return f"{arch}-{accel}-{gb}gb"

    if gpu_type == "nvidia" and gpu.get("cuda"):
        accel = "cuda"
        vram_mb = gpu.get("vram_mb", 0) or 0
        gb = _snap_to_bucket(vram_mb // 1024) if vram_mb else _snap_to_bucket(ram_mb // 1024)
        return f"{arch}-{accel}-{gb}gb"

    if gpu_type == "amd" and gpu.get("rocm"):
        accel = "rocm"
        vram_mb = gpu.get("vram_mb", 0) or 0
        gb = _snap_to_bucket(vram_mb // 1024) if vram_mb else _snap_to_bucket(ram_mb // 1024)
        return f"{arch}-{accel}-{gb}gb"

    if gpu_type == "apple":
        # Apple Silicon — unified memory; a single tier covers all M-series
        return "apple-silicon"

    if gpu.get("vulkan"):
        accel = "vulkan"
        vram_mb = gpu.get("vram_mb", 0) or 0
        gb = _snap_to_bucket(vram_mb // 1024) if vram_mb else _snap_to_bucket(ram_mb // 1024)
        return f"{arch}-{accel}-{gb}gb"

    # CPU-only fallback
    gb = _snap_to_bucket(ram_mb // 1024)
    return f"{arch}-cpu-{gb}gb"


# TODO(rockchip-target): The controller's hardware_to_targets sometimes returns
# only ["cpu"] instead of ["rockchip", "cpu"] on Orange Pi 5+ production.
# Root cause: the worker hardware dict for the *controller* is sourced from
# HardwareProfile.hardware, which may report npu.type as something other than
# "rk3588" or "rknpu" in older agent versions or when the NPU driver hasn't
# initialised. Fix tracked separately — do not conflate with this PR.
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
    if npu_type in ("rk3588", "rknpu"):
        targets.append("rockchip")
    elif gpu_type == "apple":
        targets.append("apple-silicon")
    elif gpu_type == "nvidia" and gpu.get("cuda"):
        targets.append("x86-cuda")
    elif (gpu_type in ("amd", "intel") and gpu.get("vulkan")) or (
        gpu_type != "none" and gpu.get("vulkan")
    ):
        # Vulkan is cross-vendor — works on ARM (Mali, Adreno, Jetson) and
        # x86 (AMD, Intel, NVIDIA without CUDA). Emit the matching arch tier.
        targets.append("arm-vulkan" if arch == "arm" else "x86-vulkan")

    targets.append("cpu")
    return targets


def _parse_numeric_tier(tier_id: str) -> tuple[str, str, int] | None:
    """Decompose a numeric tier id like ``x86-cuda-12gb`` into
    ``(arch, accel, gb)``. Non-numeric tiers (``apple-silicon``,
    ``cpu-only``) return ``None`` — they only ever match exactly.
    """
    parts = tier_id.split("-")
    if len(parts) < 3 or not parts[-1].endswith("gb"):
        return None
    try:
        gb = int(parts[-1][:-2])
    except ValueError:
        return None
    arch = parts[0]
    accel = "-".join(parts[1:-1])
    if not arch or not accel:
        return None
    return arch, accel, gb


def tier_compatible(worker_tier: str, manifest_tier: str) -> bool:
    """A manifest tier is compatible with a worker tier when either:

    - The two strings are equal (existing exact-match behaviour), OR
    - Both are numeric tiers (``<arch>-<accel>-<N>gb``) with matching arch
      and accel, and the manifest's required gb is at most the worker's gb.

    Bigger machines inherit smaller-tier compatibility — a worker on
    ``x86-cuda-16gb`` qualifies for any manifest declaring
    ``x86-cuda-12gb`` or ``x86-cuda-8gb``, since the larger card has
    enough VRAM by definition. This means manifest authors only need to
    declare the *minimum* tier per arch/accel; bigger workers pick it up
    automatically. Same machine never inherits across arches or
    accelerator types (CUDA ≠ Vulkan ≠ ROCm even on the same card).

    Apple Silicon and ``cpu-only`` are flat tiers, so they only match
    exactly — there's no ladder to walk.
    """
    if worker_tier == manifest_tier:
        return True
    w = _parse_numeric_tier(worker_tier)
    m = _parse_numeric_tier(manifest_tier)
    if w is None or m is None:
        return False
    return w[0] == m[0] and w[1] == m[1] and m[2] <= w[2]


def _tier_value_compatible(tier_val: object) -> bool:
    """A hardware_tiers entry counts as compatible iff it's a non-empty
    declaration that isn't the explicit ``"unsupported"`` sentinel.
    """
    if isinstance(tier_val, str):
        return tier_val != "unsupported"
    if isinstance(tier_val, dict):
        return (
            tier_val.get("recommended") is not None
            or tier_val.get("fallback") is not None
        )
    return False


def potential_capabilities(hardware: dict, registry: "AppRegistry") -> tuple[str, list[str]]:
    """Return the tier id and list of capabilities the hardware *could* support.

    Walks every model in the catalog and collects the distinct capability
    strings from any manifest with at least one ``hardware_tiers`` entry
    that's compatible with the worker's tier (per :func:`tier_compatible`
    — exact match or same-arch-accel ladder match).

    Parameters
    ----------
    hardware:
        Worker hardware dict.
    registry:
        The loaded :class:`~tinyagentos.registry.AppRegistry` with all
        manifests already parsed.

    Returns
    -------
    tuple[str, list[str]]
        ``(tier_id, sorted_unique_capabilities)``
    """
    tier_id = worker_tier_id(hardware)
    caps: set[str] = set()

    for manifest in registry.list_available(type_filter="model"):
        tiers = manifest.hardware_tiers or {}
        for manifest_tier, tier_val in tiers.items():
            if not tier_compatible(tier_id, manifest_tier):
                continue
            if not _tier_value_compatible(tier_val):
                continue
            caps.update(manifest.capabilities or [])
            break  # one matching tier is enough

    return tier_id, sorted(caps)
