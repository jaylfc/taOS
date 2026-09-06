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
