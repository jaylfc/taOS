from __future__ import annotations

import time
from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/api/activity")
async def activity(request: Request):
    """Rich system activity: CPU cores, NPU cores, thermals, GPU, disk, net, procs."""
    import psutil

    from tinyagentos.system_stats import (
        get_cpu_per_core,
        get_disk_io_rate,
        get_gpu_load,
        get_network_rates,
        get_npu_frequency,
        get_npu_per_core,
        get_thermal_zones,
        get_top_processes,
        get_vram_usage,
        get_zram_stats,
    )

    hw = request.app.state.hardware_profile
    try:
        hw_data = asdict(hw)
    except TypeError:
        hw_data = {}

    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    gpu_type = getattr(getattr(hw, "gpu", None), "type", None) or ""
    vram_pct, vram_used_mb, vram_total_mb = get_vram_usage(gpu_type)

    try:
        load_avg = list(psutil.getloadavg()) if hasattr(psutil, "getloadavg") else None
    except OSError:
        load_avg = None

    try:
        du = psutil.disk_usage("/")
        disk_info = {
            "io_rate": get_disk_io_rate(),
            "usage_percent": du.percent,
            "total_gb": du.total // (1024 ** 3),
            "used_gb": du.used // (1024 ** 3),
        }
    except OSError:
        disk_info = {
            "io_rate": get_disk_io_rate(),
            "usage_percent": 0,
            "total_gb": 0,
            "used_gb": 0,
        }

    cpu_block = hw_data.get("cpu") if isinstance(hw_data, dict) else None
    gpu_block = hw_data.get("gpu") if isinstance(hw_data, dict) else None
    npu_block = hw_data.get("npu") if isinstance(hw_data, dict) else None

    board = None
    if isinstance(cpu_block, dict):
        board = cpu_block.get("soc") or cpu_block.get("model")

    npu_type = npu_block.get("type") if isinstance(npu_block, dict) else None
    npu_tops = npu_block.get("tops") if isinstance(npu_block, dict) else None
    gpu_name = gpu_block.get("type") if isinstance(gpu_block, dict) else None

    return JSONResponse({
        "timestamp": time.time(),
        "hardware": {
            "board": board,
            "cpu": cpu_block,
            "gpu": gpu_block,
            "npu": npu_block,
            "ram_mb": hw_data.get("ram_mb") if isinstance(hw_data, dict) else None,
        },
        "cpu": {
            "cores": get_cpu_per_core(),
            "load_avg": load_avg,
            "overall_percent": psutil.cpu_percent(),
        },
        "memory": {
            "total_mb": mem.total // (1024 * 1024),
            "used_mb": mem.used // (1024 * 1024),
            "available_mb": mem.available // (1024 * 1024),
            "percent": mem.percent,
            "swap_total_mb": swap.total // (1024 * 1024),
            "swap_used_mb": swap.used // (1024 * 1024),
            "swap_percent": swap.percent,
        },
        "npu": {
            "cores": get_npu_per_core(),
            "freq_hz": get_npu_frequency(),
            "type": npu_type,
            "tops": npu_tops,
        },
        "gpu": {
            "load": get_gpu_load(),
            "vram_percent": vram_pct,
            "vram_used_mb": vram_used_mb,
            "vram_total_mb": vram_total_mb,
            "type": gpu_name,
        },
        "thermal": get_thermal_zones(),
        "zram": get_zram_stats(),
        "disk": disk_info,
        "network": get_network_rates(),
        "processes": get_top_processes(limit=10),
    })
