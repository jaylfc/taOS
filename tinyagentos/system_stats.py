"""Live system resource stats: NPU and VRAM usage helpers.

CPU and RAM are read directly from psutil in the caller; this module
focuses on accelerators whose stats require hardware-specific probes.

All helpers return ``None`` when data is unavailable, so callers can
pass the value straight through JSON and let the frontend hide the
indicator.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path

import psutil

_RKNPU_LOAD_PATHS = (
    "/sys/kernel/debug/rknpu/load",
    "/sys/class/devfreq/fdab0000.npu/load",
)


def read_rknpu_load() -> float | None:
    """Return RK3588 NPU load as a percentage (0-100) or None.

    The rknpu debugfs entry typically looks like::

        NPU load:  Core0:  12%, Core1:   0%, Core2:   0%,

    We average the cores we can parse.
    """
    for path in _RKNPU_LOAD_PATHS:
        try:
            raw = Path(path).read_text()
        except (FileNotFoundError, PermissionError, OSError):
            continue
        pcts: list[float] = []
        for token in raw.replace(",", " ").split():
            if token.endswith("%"):
                try:
                    pcts.append(float(token.rstrip("%")))
                except ValueError:
                    pass
        if pcts:
            return sum(pcts) / len(pcts)
    return None


def read_nvidia_vram() -> tuple[int, int] | None:
    """Return (used_mb, total_mb) for the first NVIDIA GPU, or None."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if out.returncode != 0:
        return None
    line = out.stdout.strip().splitlines()[:1]
    if not line:
        return None
    try:
        used_str, total_str = [p.strip() for p in line[0].split(",", 1)]
        return int(used_str), int(total_str)
    except (ValueError, IndexError):
        return None


def read_rocm_vram() -> tuple[int, int] | None:
    """Return (used_mb, total_mb) for the first AMD/ROCm GPU, or None."""
    if not shutil.which("rocm-smi"):
        return None
    try:
        out = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram", "--json"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if out.returncode != 0:
        return None
    try:
        import json

        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data:
        return None
    first_card = sorted(data.keys())[0]
    card = data[first_card]
    if not isinstance(card, dict):
        return None
    try:
        total_b = int(card["VRAM Total Memory (B)"])
        used_b = int(card["VRAM Total Used Memory (B)"])
    except (KeyError, ValueError):
        return None
    return used_b // (1024 * 1024), total_b // (1024 * 1024)


def read_nvidia_gpu_load() -> float | None:
    """Return NVIDIA GPU utilisation as a percentage, or None."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if out.returncode != 0:
        return None
    line = out.stdout.strip().splitlines()[:1]
    if not line:
        return None
    try:
        return float(line[0].strip())
    except ValueError:
        return None


def get_npu_usage(npu_type: str) -> float | None:
    """Dispatch to the right NPU load reader based on detected hardware."""
    if npu_type == "rknpu":
        return read_rknpu_load()
    return None


def get_vram_usage(gpu_type: str) -> tuple[float | None, int | None, int | None]:
    """Return (percent, used_mb, total_mb) for the given GPU type.

    Falls back to ``(None, None, None)`` when unavailable.
    """
    if gpu_type == "nvidia":
        pair = read_nvidia_vram()
        if pair is None:
            return None, None, None
        used_mb, total_mb = pair
        pct = (used_mb / total_mb * 100.0) if total_mb else None
        return pct, used_mb, total_mb
    return None, None, None


def read_all_nvidia_gpus() -> list[dict] | None:
    """Return list of dicts with stats for all NVIDIA GPUs.

    Each dict has: index, name, util_percent, freq_mhz, mem_used_mb, mem_total_mb, pci_bus_id.
    Returns None if nvidia-smi is unavailable.
    """
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,utilization.gpu,clocks.gr,memory.used,memory.total,pci.bus_id",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if out.returncode != 0:
        return None
    gpus: list[dict] = []
    for line in out.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 7:
            continue
        try:
            idx = int(parts[0])
            name = parts[1]
            util = float(parts[2]) if parts[2] else 0.0
            freq = int(parts[3]) if parts[3] else 0
            mem_used = int(parts[4]) if parts[4] else 0
            mem_total = int(parts[5]) if parts[5] else 0
            pci_bus_id = parts[6]
        except ValueError:
            continue
        # Normalize PCI address to standard 4-digit domain format
        # nvidia-smi returns 8-digit domain (00000000:01:00.0), convert to 4-digit (0000:01:00.0)
        pci_parts = pci_bus_id.split(":")
        if len(pci_parts) >= 3 and len(pci_parts[0]) == 8:
            pci_bus_id = ":".join([pci_parts[0][-4:].lower()] + pci_parts[1:])
        else:
            pci_bus_id = pci_bus_id.lower()
        gpus.append({
            "index": idx,
            "name": name,
            "util_percent": util,
            "freq_hz": freq * 1_000_000,
            "mem_used_mb": mem_used,
            "mem_total_mb": mem_total,
            "pci_bus_id": pci_bus_id,
        })
    # Sort by PCI bus ID for stable cross-vendor ordering
    gpus.sort(key=lambda g: g["pci_bus_id"])
    return gpus if gpus else None


def read_all_amd_gpus() -> list[dict] | None:
    """Return list of dicts with stats for all AMD GPUs via rocm-smi.

    Each dict has: index, name, util_percent, freq_hz, mem_used_mb, mem_total_mb.
    Returns None if rocm-smi is unavailable.
    """
    if not shutil.which("rocm-smi"):
        return None
    try:
        out = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram", "--showuse", "--json"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if out.returncode != 0:
        return None
    try:
        import json

        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data:
        return None
    gpus: list[dict] = []
    for idx, (card_key, card) in enumerate(sorted(data.items())):
        if not isinstance(card, dict):
            continue
        try:
            total_b = int(card.get("VRAM Total Memory (B)", 0))
            used_b = int(card.get("VRAM Total Used Memory (B)", 0))
            util = float(card.get("GPU use (%)", 0)) if card.get("GPU use (%)") else 0.0
            # Frequency not easily available from rocm-smi JSON
            freq_hz = 0
        except (KeyError, ValueError):
            continue
        gpus.append({
            "index": idx,
            "name": card.get("Card series", f"AMD GPU {idx}"),
            "util_percent": util,
            "freq_hz": freq_hz,
            "mem_used_mb": used_b // (1024 * 1024) if used_b else 0,
            "mem_total_mb": total_b // (1024 * 1024) if total_b else 0,
        })
    return gpus if gpus else None


def _read_int_file(path: Path) -> int | None:
    """Read an integer from a sysfs file, or None when unreadable/missing."""
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _intel_rc6_ms(card: Path) -> int | None:
    """RC6 residency milliseconds for an i915 card, or the xe equivalent.

    i915: /sys/class/drm/cardN/gt/gt0/rc6_residency_ms
          fallback /sys/class/drm/cardN/power/rc6_residency_ms
          (Documentation/gpu/i915/rc6.rst, drivers/gpu/drm/i915/i915_pmu.c)
    xe:   /sys/class/drm/cardN/device/tile0/gt0/gtidle/idle_residency_ms
          (Documentation/gpu/xe/xe_sysfs.rst, drivers/gpu/drm/xe/xe_gt_sysfs.c)
    """
    for path in (
        card / "gt" / "gt0" / "rc6_residency_ms",
        card / "power" / "rc6_residency_ms",
        card / "device" / "tile0" / "gt0" / "gtidle" / "idle_residency_ms",
    ):
        value = _read_int_file(path)
        if value is not None:
            return value
    return None


def _intel_freq_mhz(card: Path) -> int | None:
    """Current gt frequency in MHz.

    gt_act_freq_mhz reports 0 when idle, so fall back to gt_cur_freq_mhz.
    i915 exposes frequency files directly under cardN/ (gt_act_freq_mhz, gt_cur_freq_mhz).
          (Documentation/gpu/i915/frequency.rst, drivers/gpu/drm/i915/i915_frequency.c)
    xe keeps its frequency files under .../tile0/gt0/freq0/.
          (Documentation/gpu/xe/xe_sysfs.rst, drivers/gpu/drm/xe/xe_gt_sysfs.c)
    """
    # i915 paths (directly under cardN/)
    for path in (
        card / "gt_act_freq_mhz",
        card / "gt_cur_freq_mhz",
    ):
        value = _read_int_file(path)
        if value is not None and value > 0:
            return value
    # xe paths
    for path in (
        card / "device" / "tile0" / "gt0" / "freq0" / "gt_act_freq_mhz",
        card / "device" / "tile0" / "gt0" / "freq0" / "gt_cur_freq_mhz",
    ):
        value = _read_int_file(path)
        if value is not None and value > 0:
            return value
    # i915 fallback: gt_cur_freq_mhz can be 0 when idle, but we already checked > 0 above
    # Try gt_cur_freq_mhz even if 0 (idle)
    for path in (
        card / "gt_cur_freq_mhz",
        card / "device" / "tile0" / "gt0" / "freq0" / "gt_cur_freq_mhz",
    ):
        value = _read_int_file(path)
        if value is not None:
            return value
    return None


def _intel_max_freq_mhz(card: Path) -> int | None:
    """Maximum gt frequency in MHz, when exposed.

    i915 exposes gt_max_freq_mhz directly under cardN/.
          (Documentation/gpu/i915/frequency.rst, drivers/gpu/drm/i915/i915_frequency.c)
    xe keeps it under .../tile0/gt0/freq0/.
          (Documentation/gpu/xe/xe_sysfs.rst, drivers/gpu/drm/xe/xe_gt_sysfs.c)
    """
    for path in (
        card / "gt_max_freq_mhz",
        card / "device" / "tile0" / "gt0" / "freq0" / "gt_max_freq_mhz",
    ):
        value = _read_int_file(path)
        if value is not None:
            return value
    return None


def read_all_intel_gpus() -> list[dict] | None:
    """Return per-card stats for Intel integrated GPUs (i915/xe).

    Each dict: index, pci_address, name, load_percent, freq_hz, freq_max_hz,
    vram_percent/used/total (always None, shared system memory),
    shared_memory (always True).

    load_percent uses RC6 residency deltas between two samples:
    busy% = clamp(100 * (1 - delta_rc6_ms / delta_wall_ms), 0, 100).
    The first sample reports None because there is no previous point.
    A residency counter that goes backwards (reset) yields null for that sample.
    Returns None when no Intel DRM card is present.
    """
    if not _DRM_ROOT.exists():
        return None
    entries: list[dict] = []
    now = time.monotonic()
    try:
        cards = sorted(_DRM_ROOT.glob("card*"))
    except OSError:
        return None
    for card in cards:
        device = card / "device"
        try:
            vendor = (device / "vendor").read_text().strip()
        except OSError:
            continue
        if vendor != "0x8086":
            continue
        # Get PCI address from device symlink target
        pci_addr = ""
        try:
            real_device = device.resolve()
            parts = real_device.parts
            for part in reversed(parts):
                if part.startswith("0000:") and part.count(":") >= 2:
                    pci_addr = part
                    break
        except (OSError, RuntimeError):
            pass
        rc6_ms = _intel_rc6_ms(card)
        prev = _INTEL_LAST.get(str(card))
        load_percent = None
        if rc6_ms is not None and prev is not None and prev.get("rc6_ms") is not None:
            delta_wall_ms = (now - prev["ts"]) * 1000.0
            delta_rc6 = rc6_ms - prev["rc6_ms"]
            # Residency counter reset (goes backwards) -> invalid sample, report null
            if delta_wall_ms > 0 and delta_rc6 >= 0:
                load_percent = max(
                    0, min(100, round(100.0 * (1.0 - delta_rc6 / delta_wall_ms)))
                )
        if rc6_ms is not None:
            _INTEL_LAST[str(card)] = {"ts": now, "rc6_ms": rc6_ms}
        freq_mhz = _intel_freq_mhz(card)
        max_mhz = _intel_max_freq_mhz(card)
        entries.append(
            {
                "index": len(entries),
                "pci_address": pci_addr,
                "name": "",
                "load_percent": load_percent,
                "freq_hz": freq_mhz * 1_000_000 if freq_mhz is not None else None,
                "freq_max_hz": max_mhz * 1_000_000 if max_mhz is not None else None,
                "vram_percent": None,
                "vram_used_mb": None,
                "vram_total_mb": None,
                "shared_memory": True,
            }
        )
    return entries if entries else None


def _amd_device_dirs() -> list[tuple[Path, str]]:
    """DRM card device dirs bound to an AMD GPU (vendor 0x1002).
    
    Returns list of (device_path, pci_address) tuples.
    """
    dirs: list[tuple[Path, str]] = []
    if not _DRM_ROOT.exists():
        return dirs
    try:
        for card in sorted(_DRM_ROOT.glob("card*")):
            device = card / "device"
            try:
                if (device / "vendor").read_text().strip() != "0x1002":
                    continue
            except OSError:
                continue
            # Get PCI address from device symlink target (LAST component for bridges)
            pci_addr = ""
            try:
                real_device = device.resolve()
                parts = real_device.parts
                for part in reversed(parts):
                    if part.startswith("0000:") and part.count(":") >= 2:
                        pci_addr = part
                        break
            except (OSError, RuntimeError):
                pass
            dirs.append((device, pci_addr))
    except OSError:
        pass
    return dirs


def read_amd_sysfs_loads() -> list[dict] | None:
    """AMD iGPU load from device/gpu_busy_percent when the file is present.

    Returns one entry per AMD card, or None when no AMD DRM card exists.
    Each entry includes pci_address.
    """
    dirs = _amd_device_dirs()
    if not dirs:
        return None
    return [
        {
            "index": i,
            "pci_address": pci_addr,
            "load_percent": _read_int_file(device / "gpu_busy_percent"),
            "freq_hz": None,
            "vram_percent": None,
            "vram_used_mb": None,
            "vram_total_mb": None,
        }
        for i, (device, pci_addr) in enumerate(dirs)
    ]


# ─── GPU load (all vendors) ────────────────────────────────────


def get_all_gpu_loads(gpus: list[dict]) -> list[dict]:
    """Return load and VRAM stats for all GPUs.

    Args:
        gpus: List of GPU dicts from hardware detection, each with keys:
            type, model, vram_mb, pci_address (optional)

    Returns:
        List of dicts with keys: type, load_percent, freq_hz, freq_max_hz,
        vram_percent, vram_used_mb, vram_total_mb, shared_memory (intel only)
    """
    result: list[dict] = []
    nvidia_stats = read_all_nvidia_gpus() if any(g.get("type") == "nvidia" for g in gpus) else None
    amd_stats = read_all_amd_gpus() if any(g.get("type") == "amd" for g in gpus) else None
    amd_sysfs = read_amd_sysfs_loads() if any(g.get("type") == "amd" for g in gpus) else None
    intel_stats = read_all_intel_gpus() if any(g.get("type") == "intel" for g in gpus) else None

    # Build lookup maps by PCI address for stats that have them
    nvidia_by_pci = {s.get("pci_bus_id", "").lower(): s for s in (nvidia_stats or []) if s.get("pci_bus_id")}
    amd_by_pci = {s.get("pci_address", "").lower(): s for s in (amd_sysfs or []) if s.get("pci_address")}
    intel_by_pci = {s.get("pci_address", "").lower(): s for s in (intel_stats or []) if s.get("pci_address")}

    # Full stats lists for vendor-index fallback (when PCI match fails or missing)
    nvidia_full_list = list(nvidia_stats or [])
    amd_rocm_list = list(amd_stats or [])
    intel_full_list = list(intel_stats or [])

    # Vendor-specific indices for matching by vendor order
    nvidia_vendor_idx = 0
    amd_vendor_idx = 0
    intel_vendor_idx = 0

    for gpu in gpus:
        gtype = gpu.get("type", "")
        pci_addr = (gpu.get("pci_address") or "").lower()
        entry = {"type": gtype}
        if gtype == "nvidia":
            # Try to match by PCI address first
            stat = None
            if pci_addr and pci_addr in nvidia_by_pci:
                stat = nvidia_by_pci[pci_addr]
            # Fallback to vendor index if PCI match failed or no PCI address
            if stat is None and nvidia_vendor_idx < len(nvidia_full_list):
                stat = nvidia_full_list[nvidia_vendor_idx]
            if stat:
                entry["load_percent"] = stat.get("util_percent")
                entry["freq_hz"] = stat.get("freq_hz")
                used = stat.get("mem_used_mb")
                total = stat.get("mem_total_mb")
                if total is not None and total > 0:
                    entry["vram_percent"] = round(used / total * 100.0, 1)
                else:
                    entry["vram_percent"] = None
                entry["vram_used_mb"] = used
                entry["vram_total_mb"] = total
            else:
                entry["load_percent"] = None
                entry["freq_hz"] = None
                entry["vram_percent"] = None
                entry["vram_used_mb"] = None
                entry["vram_total_mb"] = None
            nvidia_vendor_idx += 1
        elif gtype == "amd":
            # Try sysfs first (has PCI address), then rocm-smi (fallback by vendor index)
            sysfs_stat = None
            rocm_stat = None
            if pci_addr and pci_addr in amd_by_pci:
                sysfs_stat = amd_by_pci[pci_addr]
            # Also try to get sysfs stat by vendor index (fallback when no PCI match)
            if sysfs_stat is None and amd_vendor_idx < len(amd_sysfs or []):
                sysfs_stat = (amd_sysfs or [])[amd_vendor_idx]
            # Also try to get ROCm stat by vendor index (for VRAM data)
            if amd_vendor_idx < len(amd_rocm_list):
                rocm_stat = amd_rocm_list[amd_vendor_idx]
            # Merge: prefer sysfs for load, ROCm for VRAM
            load_percent = None
            if sysfs_stat is not None:
                lp = sysfs_stat.get("load_percent")
                if lp is not None:
                    load_percent = lp
            if load_percent is None and rocm_stat is not None:
                up = rocm_stat.get("util_percent")
                if up is not None:
                    load_percent = up
            entry["load_percent"] = load_percent
            # Frequency: prefer sysfs (though both typically None)
            freq_hz = None
            if sysfs_stat is not None:
                fh = sysfs_stat.get("freq_hz")
                if fh is not None:
                    freq_hz = fh
            if freq_hz is None and rocm_stat is not None:
                fh = rocm_stat.get("freq_hz")
                if fh is not None:
                    freq_hz = fh
            entry["freq_hz"] = freq_hz
            # VRAM: prefer ROCm (has actual VRAM data), sysfs has None
            used = None
            total = None
            if rocm_stat is not None:
                used = rocm_stat.get("mem_used_mb")
                total = rocm_stat.get("mem_total_mb")
            if used is None and sysfs_stat is not None:
                used = sysfs_stat.get("mem_used_mb")
                total = sysfs_stat.get("mem_total_mb")
            if total is not None and total > 0:
                entry["vram_percent"] = round(used / total * 100.0, 1)
            else:
                entry["vram_percent"] = None
            entry["vram_used_mb"] = used
            entry["vram_total_mb"] = total
            amd_vendor_idx += 1
        elif gtype == "intel":
            # Try to match by PCI address first, then fallback by vendor index
            stat = None
            if pci_addr and pci_addr in intel_by_pci:
                stat = intel_by_pci[pci_addr]
            # Fallback to vendor index if PCI match failed or no PCI address
            if stat is None and intel_vendor_idx < len(intel_full_list):
                stat = intel_full_list[intel_vendor_idx]
            if stat:
                entry["load_percent"] = stat.get("load_percent")
                entry["freq_hz"] = stat.get("freq_hz")
                entry["freq_max_hz"] = stat.get("freq_max_hz")
                entry["vram_percent"] = None
                entry["vram_used_mb"] = None
                entry["vram_total_mb"] = None
                entry["shared_memory"] = True
            else:
                entry["load_percent"] = None
                entry["freq_hz"] = None
                entry["freq_max_hz"] = None
                entry["vram_percent"] = None
                entry["vram_used_mb"] = None
                entry["vram_total_mb"] = None
                entry["shared_memory"] = True
            intel_vendor_idx += 1
        else:
            # Mali, Apple, or unknown - no runtime stats available
            entry["load_percent"] = None
            entry["freq_hz"] = None
            entry["freq_max_hz"] = None
            entry["vram_percent"] = None
            entry["vram_used_mb"] = None
            entry["vram_total_mb"] = None
        result.append(entry)
    return result


# ─── NPU per-core (RK3588 specific) ────────────────────────────

_NPU_CORE_RE = re.compile(r"Core(\d+):\s*(\d+)%")


def get_npu_per_core() -> list[dict] | None:
    """Read per-core NPU load from /sys/kernel/debug/rknpu/load.

    Requires root (debugfs). Returns list of {core, load_percent} or None.
    """
    path = Path("/sys/kernel/debug/rknpu/load")
    try:
        text = path.read_text()
    except (OSError, PermissionError, FileNotFoundError):
        return None
    cores: list[dict] = []
    for match in _NPU_CORE_RE.finditer(text):
        cores.append({"core": int(match.group(1)), "load_percent": int(match.group(2))})
    return cores if cores else None


def get_npu_frequency() -> int | None:
    """Current NPU frequency in Hz, None if unavailable."""
    for path in (
        Path("/sys/class/devfreq/fdab0000.npu/cur_freq"),
        Path("/sys/kernel/debug/rknpu/freq"),
    ):
        try:
            return int(path.read_text().strip())
        except (OSError, PermissionError, FileNotFoundError, ValueError):
            pass
    return None


# ─── CPU per-core with freq and governor ───────────────────────


def get_cpu_per_core() -> list[dict]:
    """Per-core CPU info: load, freq_khz, governor, min/max freq.

    Uses psutil for load, sysfs for freq/governor (Linux only).
    """

    loads = psutil.cpu_percent(percpu=True)
    cores: list[dict] = []

    for i, load in enumerate(loads):
        core: dict = {"core": i, "load_percent": load}
        base = Path(f"/sys/devices/system/cpu/cpu{i}/cpufreq")
        if base.exists():
            try:
                core["freq_khz"] = int((base / "scaling_cur_freq").read_text().strip())
                core["min_khz"] = int((base / "scaling_min_freq").read_text().strip())
                core["max_khz"] = int((base / "scaling_max_freq").read_text().strip())
                core["governor"] = (base / "scaling_governor").read_text().strip()
            except (OSError, ValueError):
                pass
        cores.append(core)

    return cores


# ─── Thermal zones ─────────────────────────────────────────────


@lru_cache(maxsize=1)
def _thermal_zones() -> tuple[tuple[str, Path], ...]:
    """Scan /sys/class/thermal once, cache (name, temp_path) tuples."""
    zones: list[tuple[str, Path]] = []
    base = Path("/sys/class/thermal")
    if not base.exists():
        return tuple(zones)
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return tuple(zones)
    for zone_dir in entries:
        if not zone_dir.name.startswith("thermal_zone"):
            continue
        try:
            name = (zone_dir / "type").read_text().strip()
            zones.append((name, zone_dir / "temp"))
        except (OSError, ValueError):
            pass
    return tuple(zones)


def get_thermal_zones() -> list[dict]:
    """Return list of {name, temp_c}. Empty on platforms without sysfs thermal."""
    out: list[dict] = []
    for name, temp_path in _thermal_zones():
        try:
            millidegrees = int(temp_path.read_text().strip())
            out.append({"name": name, "temp_c": millidegrees / 1000.0})
        except (OSError, ValueError):
            pass
    return out


# ─── GPU load (Mali / Panthor) ─────────────────────────────────


def get_gpu_load() -> dict | None:
    """Mali/Panthor GPU load on Rockchip. Returns {load_percent, freq_hz} or None."""
    # mainline panthor: /sys/class/devfreq/fb000000.gpu/load
    # format: "120@800000000" (util@freq_hz)
    panthor = Path("/sys/class/devfreq/fb000000.gpu/load")
    try:
        text = panthor.read_text().strip()
        if "@" in text:
            util, freq = text.split("@", 1)
            return {"load_percent": int(util), "freq_hz": int(freq)}
    except (OSError, PermissionError, FileNotFoundError, ValueError):
        pass

    # mali blob (debugfs, root)
    mali = Path("/sys/kernel/debug/mali0/dvfs_utilization")
    try:
        text = mali.read_text()
        busy_match = re.search(r"busy_time:\s*(\d+)", text)
        idle_match = re.search(r"idle_time:\s*(\d+)", text)
        if busy_match and idle_match:
            busy = int(busy_match.group(1))
            idle = int(idle_match.group(1))
            total = busy + idle
            if total > 0:
                return {"load_percent": round(100 * busy / total), "freq_hz": None}
    except (OSError, PermissionError, FileNotFoundError, ValueError):
        pass

    return None


# ─── ZRAM compression stats ────────────────────────────────────


def get_zram_stats() -> list[dict]:
    """ZRAM device stats: original size, compressed size, ratio."""
    out: list[dict] = []
    block_base = Path("/sys/block")
    if not block_base.exists():
        return out
    try:
        devs = sorted(block_base.glob("zram*"))
    except OSError:
        return out
    for dev in devs:
        mm_stat = dev / "mm_stat"
        if not mm_stat.exists():
            continue
        try:
            parts = mm_stat.read_text().split()
            if len(parts) >= 4:
                orig = int(parts[0])
                compr = int(parts[1])
                mem_used = int(parts[2])
                ratio = (orig / compr) if compr > 0 else 0
                out.append({
                    "device": dev.name,
                    "orig_mb": orig // (1024 * 1024),
                    "compr_mb": compr // (1024 * 1024),
                    "used_mb": mem_used // (1024 * 1024),
                    "ratio": round(ratio, 2),
                })
        except (OSError, ValueError):
            pass
    return out


# ─── Disk I/O (rate calculation) ───────────────────────────────

_DISK_IO_LAST: dict = {"ts": 0.0, "read": 0, "write": 0}

# Last RC6 residency sample per Intel DRM card, used to compute GPU load
# between two samples. First sample reports null (no previous point).
_INTEL_LAST: dict[str, dict] = {}

# Overridable in tests so a fake sysfs tree can be used.
_DRM_ROOT = Path("/sys/class/drm")


def get_disk_io_rate() -> dict:
    """Overall disk read/write bytes per second (across all disks)."""

    io = psutil.disk_io_counters()
    if io is None:
        return {"read_bps": 0, "write_bps": 0}
    now = time.time()
    global _DISK_IO_LAST
    last = _DISK_IO_LAST

    dt = now - last["ts"] if last["ts"] > 0 else 0
    read_bps = 0
    write_bps = 0
    if dt > 0:
        read_bps = int((io.read_bytes - last["read"]) / dt)
        write_bps = int((io.write_bytes - last["write"]) / dt)

    _DISK_IO_LAST = {"ts": now, "read": io.read_bytes, "write": io.write_bytes}
    return {"read_bps": max(0, read_bps), "write_bps": max(0, write_bps)}


# ─── Network I/O (rate calculation) ────────────────────────────

_NET_IO_LAST: dict[str, dict] = {}


def get_network_rates() -> list[dict]:
    """Per-interface network rx/tx bytes per second."""

    now = time.time()
    stats = psutil.net_io_counters(pernic=True)
    out: list[dict] = []
    global _NET_IO_LAST

    for name, io in stats.items():
        if name == "lo" or name.startswith(("docker", "br-", "veth", "virbr", "dummy")):
            continue
        last = _NET_IO_LAST.get(name, {"ts": 0.0, "rx": 0, "tx": 0})
        dt = now - last["ts"] if last["ts"] > 0 else 0
        rx_bps = 0
        tx_bps = 0
        if dt > 0:
            rx_bps = int((io.bytes_recv - last["rx"]) / dt)
            tx_bps = int((io.bytes_sent - last["tx"]) / dt)
        out.append({
            "name": name,
            "rx_bps": max(0, rx_bps),
            "tx_bps": max(0, tx_bps),
            "rx_total": io.bytes_recv,
            "tx_total": io.bytes_sent,
        })
        _NET_IO_LAST[name] = {"ts": now, "rx": io.bytes_recv, "tx": io.bytes_sent}

    return out


# ─── Top processes by RAM/CPU ──────────────────────────────────


def get_top_processes(limit: int = 10) -> list[dict]:
    """Top processes by memory usage."""

    procs: list[dict] = []
    for p in psutil.process_iter(['pid', 'name', 'memory_info', 'cpu_percent', 'username']):
        try:
            info = p.info
            mem = info['memory_info']
            procs.append({
                "pid": info['pid'],
                "name": info['name'] or "?",
                "user": info['username'] or "?",
                "rss_mb": (mem.rss if mem else 0) // (1024 * 1024),
                "cpu_percent": info['cpu_percent'] or 0.0,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    procs.sort(key=lambda p: p["rss_mb"], reverse=True)
    return procs[:limit]
