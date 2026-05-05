"""Worker LXC capacity reporting.

Reads btrfs pool size + usage and bees dedup totals. Used by the
worker's heartbeat loop to populate the capacity fields on
HeartbeatBody. Runs INSIDE the worker LXC; pool path and bees status
path are inside the LXC's filesystem.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_POOL_PATH = "/var/lib/incus/storage-pools/taos-worker-pool"
DEFAULT_BEES_STATUS = Path("/var/lib/bees/status.txt")


def _parse_size(value: str) -> int:
    """Parse btrfs sizes like '500.00GiB' / '12.34TiB' into bytes."""
    value = value.strip()
    units = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    m = re.match(r"^([0-9.]+)([KMGT]?iB|B)$", value)
    if not m:
        raise ValueError(f"unparsable btrfs size: {value!r}")
    return int(float(m.group(1)) * units[m.group(2)])


def read_btrfs_pool_size(pool_path: str) -> tuple[int, int]:
    """Return (cap_bytes, used_bytes) for the btrfs pool at `pool_path`.

    Calls `btrfs filesystem show <pool_path>` and parses the output.
    Returns (0, 0) on error — capacity reporting is best-effort, the
    heartbeat shouldn't fail loud if btrfs isn't present (e.g., during
    pre-LXC bootstrap).
    """
    try:
        proc = subprocess.run(
            ["btrfs", "filesystem", "show", pool_path],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode != 0:
            logger.warning("btrfs filesystem show failed: %s", proc.stderr.strip())
            return (0, 0)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("btrfs filesystem show error: %s", exc)
        return (0, 0)

    cap = used = 0
    for line in proc.stdout.splitlines():
        m = re.search(r"size\s+(\S+)\s+used\s+(\S+)\s+path", line)
        if m:
            try:
                cap = _parse_size(m.group(1))
                used = _parse_size(m.group(2))
            except ValueError as exc:
                logger.warning("btrfs size parse error: %s", exc)
            break
    return (cap, used)


def read_bees_deduped_total(status_path: Path = DEFAULT_BEES_STATUS) -> int:
    """Return cumulative bytes deduped reported by bees, or 0 if unavailable.

    bees writes a status file with lines like ``DEDUP_TOTAL: <bytes>``.
    Missing file or parse failure returns 0 (best-effort).
    """
    try:
        text = status_path.read_text()
    except FileNotFoundError:
        return 0
    except OSError as exc:
        logger.warning("bees status read error: %s", exc)
        return 0
    for line in text.splitlines():
        if line.startswith("DEDUP_TOTAL:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                continue
    return 0


def capacity_snapshot(
    pool_path: str = DEFAULT_POOL_PATH,
    bees_status_path: Path = DEFAULT_BEES_STATUS,
) -> dict:
    """One-call helper that returns the three heartbeat fields as a dict
    with keys: storage_cap_bytes, storage_used_bytes, bytes_deduped_total.
    """
    cap, used = read_btrfs_pool_size(pool_path)
    return {
        "storage_cap_bytes": cap,
        "storage_used_bytes": used,
        "bytes_deduped_total": read_bees_deduped_total(bees_status_path),
    }
