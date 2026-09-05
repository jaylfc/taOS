"""Byte-size parsing across the call sites that read taOS's own values.

Every assertion here uses a string taOS or one of its runtimes actually
writes -- ``512m`` (``userspace/container_deploy.py``), ``2GiB`` and
``1.50TiB`` (incus's canonical rendering) -- rather than the tidy
``2GB``/``512MB`` forms the older unit tests used.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.size_units import parse_size_bytes, parse_size_bytes_or


def test_parse_memory_parses_the_value_container_deploy_writes():
    """``container_deploy._MEMORY_LIMIT`` is ``"512m"``; it must not read back as 0."""
    from tinyagentos.containers.backend import _parse_memory
    from tinyagentos.userspace.container_deploy import _MEMORY_LIMIT

    assert _MEMORY_LIMIT == "512m"
    assert _parse_memory(_MEMORY_LIMIT) == 512


def test_parse_memory_parses_incus_canonical_2gib():
    """incus renders ``limits.memory`` back as IEC, e.g. ``2GiB``."""
    from tinyagentos.containers.backend import _parse_memory

    assert _parse_memory("2GiB") == 2048


@pytest.mark.asyncio
async def test_disk_quota_parses_a_tib_rootfs():
    """A rootfs over 1 TiB must still produce a usage figure, not silence."""
    from tinyagentos.disk_quota import DiskQuotaMonitor

    cfg = MagicMock()
    cfg.agents = [{"name": "alice", "disk_quota_gib": 4096}]
    notif = MagicMock()
    notif.emit_event = AsyncMock()
    monitor = DiskQuotaMonitor(cfg, MagicMock(), notif)

    incus_info = "Resources:\n  Disk usage: 1.50TiB\n"
    with patch("tinyagentos.disk_quota._run", AsyncMock(return_value=(0, incus_info))):
        used_gib = await monitor._sample_usage("taos-agent-alice")

    assert used_gib is not None
    assert used_gib == pytest.approx(1.5 * 1024)


def test_worker_capacity_and_containers_agree_on_512m():
    """The two parsers must not disagree -- let alone in opposite directions."""
    from tinyagentos.cluster.worker_capacity import _parse_size
    from tinyagentos.containers.backend import _parse_memory

    assert _parse_size("512m") == 512 * 1024**2
    assert _parse_memory("512m") == _parse_size("512m") // 1024**2


# ------------------------------------------------------------------
# The shared parser itself
# ------------------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Bare unit letters -- docker / truncate(1), 1024-based.
        ("512m", 512 * 1024**2),
        ("100G", 100 * 1024**3),
        ("1T", 1024**4),
        # IEC, 1024-based.
        ("2GiB", 2 * 1024**3),
        ("1.5TiB", int(1.5 * 1024**4)),
        ("412.50MiB", int(412.50 * 1024**2)),
        ("500.00GiB", 500 * 1024**3),
        ("0B", 0),
        # SI, 1000-based -- how incus and btrfs document these.
        ("2GB", 2 * 1000**3),
        ("512MB", 512 * 1000**2),
        # Bare numbers are byte counts.
        ("4096", 4096),
        ("0", 0),
        # Case and internal whitespace are not significant.
        ("1.50 tib", int(1.5 * 1024**4)),
    ],
)
def test_parse_size_bytes_table(value, expected):
    assert parse_size_bytes(value) == expected


@pytest.mark.parametrize("value", ["", "   ", "invalid", "MB", "1.2.3GiB", None])
def test_parse_size_bytes_rejects_garbage(value):
    with pytest.raises(ValueError, match="unparsable size"):
        parse_size_bytes(value)


def test_parse_size_bytes_or_falls_back():
    assert parse_size_bytes_or("invalid") == 0
    assert parse_size_bytes_or("invalid", -1) == -1
    assert parse_size_bytes_or("512m") == 512 * 1024**2


# ------------------------------------------------------------------
# The remaining call sites now share it
# ------------------------------------------------------------------

def test_image_size_parser_shares_the_table():
    from tinyagentos.routes.agent_images import _parse_size_bytes

    assert _parse_size_bytes("412.50MiB") == int(412.50 * 1024**2)
    assert _parse_size_bytes("nonsense") == 0


def test_worker_cli_parser_shares_the_table():
    from tinyagentos.cli.worker import _parse_iec_bytes

    assert _parse_iec_bytes("100G") == 100 * 1024**3
    assert _parse_iec_bytes("1.5TiB") == int(1.5 * 1024**4)
    with pytest.raises(ValueError):
        _parse_iec_bytes("")


def test_worker_capacity_parses_a_tib_pool():
    from tinyagentos.cluster.worker_capacity import _parse_size

    assert _parse_size("1.5TiB") == int(1.5 * 1024**4)
    with pytest.raises(ValueError, match="unparsable btrfs size"):
        _parse_size("invalid")


@pytest.mark.asyncio
async def test_disk_quota_logs_instead_of_skipping_silently(caplog):
    """An unreadable usage line must be audible -- silence reads as 'no usage'."""
    from tinyagentos.disk_quota import DiskQuotaMonitor

    cfg = MagicMock()
    cfg.agents = [{"name": "alice", "disk_quota_gib": 40}]
    notif = MagicMock()
    notif.emit_event = AsyncMock()
    monitor = DiskQuotaMonitor(cfg, MagicMock(), notif)

    incus_info = "Resources:\n  Disk usage: lots\n"
    with caplog.at_level(logging.WARNING, logger="tinyagentos.disk_quota"):
        with patch("tinyagentos.disk_quota._run", AsyncMock(return_value=(0, incus_info))):
            used_gib = await monitor._sample_usage("taos-agent-alice")

    assert used_gib is None
    assert any("unparsable disk usage line" in r.getMessage() for r in caplog.records)
