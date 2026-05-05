"""Task 2: Tests for worker_capacity — btrfs pool size + bees dedup reporting."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from tinyagentos.cluster.worker_capacity import (
    _parse_size,
    read_btrfs_pool_size,
    read_bees_deduped_total,
    capacity_snapshot,
)


def test_read_btrfs_pool_size_parses_btrfs_filesystem_show():
    fake_output = """Label: 'taos-worker-pool'  uuid: 1234-5678
        Total devices 1 FS bytes used 12.34GiB
        devid    1 size 500.00GiB used 50.00GiB path /dev/loop0
"""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = fake_output
        mock_run.return_value.returncode = 0
        cap, used = read_btrfs_pool_size("/var/lib/incus/storage-pools/taos-worker-pool")
    assert cap == 500 * 1024**3
    assert used == int(50.00 * 1024**3)


def test_read_btrfs_pool_size_returns_zeros_on_error():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "no such pool"
        mock_run.return_value.stdout = ""
        cap, used = read_btrfs_pool_size("/nonexistent")
    assert cap == 0
    assert used == 0


def test_read_btrfs_pool_size_handles_btrfs_missing(tmp_path):
    with patch("subprocess.run", side_effect=FileNotFoundError):
        cap, used = read_btrfs_pool_size("/anywhere")
    assert cap == 0
    assert used == 0


def test_read_bees_deduped_total_returns_zero_if_status_missing(tmp_path):
    bees_status = tmp_path / "bees-status.txt"
    assert read_bees_deduped_total(bees_status) == 0


def test_read_bees_deduped_total_parses_status_file(tmp_path):
    bees_status = tmp_path / "bees-status.txt"
    bees_status.write_text(
        "DEDUP: 12345678 bytes deduplicated\n"
        "DEDUP_TOTAL: 9876543210\n"
    )
    assert read_bees_deduped_total(bees_status) == 9876543210


def test_capacity_snapshot_returns_dict(tmp_path):
    bees_status = tmp_path / "bees-status.txt"
    bees_status.write_text("DEDUP_TOTAL: 100\n")
    fake_btrfs = """Label: 'p'  uuid: 1
        devid 1 size 10.00GiB used 1.00GiB path /dev/loop0
"""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = fake_btrfs
        mock_run.return_value.returncode = 0
        snap = capacity_snapshot(
            pool_path="/var/lib/incus/storage-pools/taos-worker-pool",
            bees_status_path=bees_status,
        )
    assert snap == {
        "storage_cap_bytes": 10 * 1024**3,
        "storage_used_bytes": 1 * 1024**3,
        "bytes_deduped_total": 100,
    }


def test_read_btrfs_pool_size_handles_timeout():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="btrfs", timeout=5)):
        cap, used = read_btrfs_pool_size("/anywhere")
    assert cap == 0
    assert used == 0


@pytest.mark.parametrize("input_str, expected_bytes", [
    ("500.00GiB", 500 * 1024**3),
    ("12.34TiB", int(12.34 * 1024**4)),
    ("1.5MiB", int(1.5 * 1024**2)),
    ("512KiB", 512 * 1024),
    ("0B", 0),
])
def test_parse_size_handles_all_btrfs_units(input_str, expected_bytes):
    assert _parse_size(input_str) == expected_bytes


def test_read_btrfs_pool_size_returns_zeros_on_unparsable_size():
    fake_output = """Label: 'p' uuid: x
        devid 1 size UNKNOWN used UNKNOWN path /dev/loop0
"""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = fake_output
        mock_run.return_value.returncode = 0
        cap, used = read_btrfs_pool_size("/anywhere")
    assert cap == 0
    assert used == 0
