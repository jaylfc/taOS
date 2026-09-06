"""Unit tests for tinyagentos.cluster.worker_capacity."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tinyagentos.cluster.worker_capacity import (
    _parse_size,
    capacity_snapshot,
    read_btrfs_pool_size,
    read_bees_deduped_total,
    gpu_vram_snapshot,
)


def test_parse_size_valid_gib() -> None:
    """Test parsing GiB units to bytes."""
    assert _parse_size("500.00GiB") == 500 * 1024**3


def test_parse_size_valid_tib() -> None:
    """Test parsing TiB units to bytes."""
    assert _parse_size("12.34TiB") == int(12.34 * 1024**4)


def test_parse_size_valid_mib() -> None:
    """Test parsing MiB units to bytes."""
    assert _parse_size("1.5MiB") == int(1.5 * 1024**2)


def test_parse_size_valid_kib() -> None:
    """Test parsing KiB units to bytes."""
    assert _parse_size("512KiB") == 512 * 1024


def test_parse_size_valid_b() -> None:
    """Test parsing B units to bytes."""
    assert _parse_size("0B") == 0


def test_parse_size_invalid() -> None:
    """Test that invalid size strings raise ValueError."""
    with pytest.raises(ValueError, match="unparsable btrfs size"):
        _parse_size("invalid")


def test_parse_size_empty() -> None:
    """Test that empty string raises ValueError."""
    with pytest.raises(ValueError, match="unparsable btrfs size"):
        _parse_size("")


def test_read_btrfs_pool_size_parses_btrfs_filesystem_show() -> None:
    """Test parsing valid btrfs filesystem show output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pool_path = Path(tmpdir) / "pool"
        fake_output = """Label: 'taos-worker-pool'  uuid: 1234-5678
        Total devices 1 FS bytes used 12.34GiB
        devid    1 size 500.00GiB used 50.00GiB path /dev/loop0
    """
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = fake_output
            mock_run.return_value.returncode = 0
            cap, used = read_btrfs_pool_size(str(pool_path))
            assert cap == 500 * 1024**3
            assert used == int(50.00 * 1024**3)


def test_read_btrfs_pool_size_returns_zeros_on_error() -> None:
    """Test handling of non-zero return code from btrfs command."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pool_path = Path(tmpdir) / "pool"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "no such pool"
            mock_run.return_value.stdout = ""
            cap, used = read_btrfs_pool_size(str(pool_path))
            assert cap == 0
            assert used == 0


def test_read_btrfs_pool_size_handles_btrfs_missing(tmp_path) -> None:
    """Test handling of FileNotFoundError from subprocess.run."""
    with patch("subprocess.run", side_effect=FileNotFoundError):
        cap, used = read_btrfs_pool_size("/anywhere")
        assert cap == 0
        assert used == 0


def test_read_btrfs_pool_size_handles_timeout() -> None:
    """Test handling of subprocess.TimeoutExpired."""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="btrfs", timeout=5)):
        cap, used = read_btrfs_pool_size("/anywhere")
        assert cap == 0
        assert used == 0


def test_read_btrfs_pool_size_returns_zeros_on_unparsable_size() -> None:
    """Test handling of unparsable size in btrfs output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pool_path = Path(tmpdir) / "pool"
        fake_output = """Label: 'p' uuid: x
        devid 1 size UNKNOWN used UNKNOWN path /dev/loop0
    """
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = fake_output
            mock_run.return_value.returncode = 0
            cap, used = read_btrfs_pool_size(str(pool_path))
            assert cap == 0
            assert used == 0


def test_read_bees_deduped_total_returns_zero_if_status_missing(tmp_path) -> None:
    """Test handling of missing DEDUP_TOTAL line."""
    bees_status = tmp_path / "bees-status.txt"
    bees_status.write_text("")
    assert read_bees_deduped_total(bees_status) == 0


def test_read_bees_deduped_total_parses_status_file(tmp_path) -> None:
    """Test parsing valid DEDUP_TOTAL line."""
    bees_status = tmp_path / "bees-status.txt"
    bees_status.write_text(
        "DEDUP: 12345678 bytes deduplicated\n"
        "DEDUP_TOTAL: 9876543210\n"
    )
    assert read_bees_deduped_total(bees_status) == 9876543210


def test_capacity_snapshot_returns_dict(tmp_path) -> None:
    """Test capacity_snapshot with mocked subprocess."""
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


def test_parse_size_handles_all_btrfs_units() -> None:
    """Test parsing with different unit prefixes."""
    test_cases = [
        ("500.00GiB", 500 * 1024**3),
        ("12.34TiB", int(12.34 * 1024**4)),
        ("1.5MiB", int(1.5 * 1024**2)),
        ("512KiB", 512 * 1024),
        ("0B", 0),
    ]
    for input_str, expected_bytes in test_cases:
        assert _parse_size(input_str) == expected_bytes


def test_gpu_vram_snapshot_unavailable() -> None:
    """Test gpu_vram_snapshot when nvidia-smi is not available."""
    with patch("shutil.which", return_value=None):
        assert gpu_vram_snapshot() is None


def test_gpu_vram_snapshot_success() -> None:
    """Test gpu_vram_snapshot with successful nvidia-smi output."""
    with patch("tinyagentos.system_stats.read_nvidia_vram") as mock_read:
        mock_read.return_value = (0, 8192)
        result = gpu_vram_snapshot()
        assert result == {"free_vram_mb": 8192, "used_vram_mb": 0}


def test_gpu_vram_snapshot_read_nvidia_vram_none() -> None:
    """Test gpu_vram_snapshot when read_nvidia_vram returns None."""
    with patch("tinyagentos.system_stats.read_nvidia_vram", return_value=None):
        assert gpu_vram_snapshot() is None
