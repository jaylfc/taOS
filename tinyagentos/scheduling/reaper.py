"""Reaper for hung executor.sh processes."""

from __future__ import annotations

import time

import psutil


def reap_hung_executor_sh(cap_seconds: int) -> list[dict]:
    """Kill executor.sh processes older than cap_seconds, skipping PPID-1 orphans.

    Returns a list of dicts with keys: pid, age, cmdline.
    """
    now = time.time()
    reaped: list[dict] = []
    for proc in psutil.process_iter(attrs=["pid", "cmdline", "create_time", "name"]):
        cmdline = proc.info.get("cmdline") or []
        if not any("executor.sh" in str(part) for part in cmdline):
            continue
        create_time = proc.info.get("create_time") or 0
        age = now - create_time
        if age <= cap_seconds:
            continue
        try:
            ppid = proc.ppid()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if ppid == 1:
            continue
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (psutil.NoSuchProcess, psutil.TimeoutExpired):
            pass
        reaped.append({
            "pid": proc.info.get("pid"),
            "age": age,
            "cmdline": cmdline,
        })
    return reaped
