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
    assert any(
        "disk usage section for taos-agent-alice carried no size token"
        in r.getMessage()
        for r in caplog.records
    )


# ------------------------------------------------------------------
# The shape `incus info` actually emits
# ------------------------------------------------------------------

# Real `incus info` renders the storage figures as a *section*: the
# "Disk usage:" header carries no size token, the value sits on the
# following, more-indented line.
_INCUS_INFO_SECTIONED = """\
Name: taos-agent-alice
Status: RUNNING
Type: container
Resources:
  Processes: 41
  Disk usage:
    root: 1.50TiB
  CPU usage:
    CPU usage (in seconds): 1234
  Memory usage:
    Memory (current): 412.50MiB
    Memory (peak): 1.10GiB
"""


def _quota_monitor():
    from tinyagentos.disk_quota import DiskQuotaMonitor

    cfg = MagicMock()
    cfg.agents = [{"name": "alice", "disk_quota_gib": 4096}]
    notif = MagicMock()
    notif.emit_event = AsyncMock()
    return DiskQuotaMonitor(cfg, MagicMock(), notif)


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_btrfs_qgroup_reads_the_sectioned_incus_info_shape(caplog):
    """The TiB fix only reaches production if the section shape parses."""
    monitor = _quota_monitor()
    with caplog.at_level(logging.WARNING, logger="tinyagentos.disk_quota"):
        with patch("tinyagentos.disk_quota._run",
                   AsyncMock(return_value=(0, _INCUS_INFO_SECTIONED))):
            used_gib = await monitor._sample_btrfs_qgroup("taos-agent-alice")

    assert used_gib == pytest.approx(1.5 * 1024)
    # A readable scan must be silent -- otherwise every container warns
    # once per cycle and the warning stops meaning anything.
    assert _warnings(caplog) == []


@pytest.mark.asyncio
async def test_incus_info_strategy_reads_the_sectioned_shape():
    """`root: 1.50TiB` has no 'disk' in it; the strategy must still see it."""
    monitor = _quota_monitor()
    with patch("tinyagentos.disk_quota._run",
               AsyncMock(return_value=(0, _INCUS_INFO_SECTIONED))):
        used_gib = await monitor._sample_incus_info("taos-agent-alice")

    assert used_gib == pytest.approx(1.5 * 1024)


@pytest.mark.asyncio
async def test_disk_usage_section_without_a_size_token_warns_once(caplog):
    monitor = _quota_monitor()
    out = "Resources:\n  Disk usage:\n    root: lots\n  CPU usage:\n    CPU usage (in seconds): 3\n"
    with caplog.at_level(logging.WARNING, logger="tinyagentos.disk_quota"):
        with patch("tinyagentos.disk_quota._run", AsyncMock(return_value=(0, out))):
            used_gib = await monitor._sample_btrfs_qgroup("taos-agent-alice")

    assert used_gib is None
    msgs = _warnings(caplog)
    assert len(msgs) == 1
    assert "no size token" in msgs[0]
    assert "root: lots" in msgs[0]


@pytest.mark.asyncio
async def test_rejected_size_token_is_reported_distinctly(caplog):
    """'the line had no size' and 'the size was garbage' are different bugs."""
    monitor = _quota_monitor()
    out = "Resources:\n  Disk usage:\n    root: 1.2.3GiB\n"
    with caplog.at_level(logging.WARNING, logger="tinyagentos.disk_quota"):
        with patch("tinyagentos.disk_quota._run", AsyncMock(return_value=(0, out))):
            used_gib = await monitor._sample_btrfs_qgroup("taos-agent-alice")

    assert used_gib is None
    msgs = _warnings(caplog)
    assert len(msgs) == 1
    assert "no size token" not in msgs[0]
    assert "1.2.3GiB" in msgs[0]


@pytest.mark.asyncio
async def test_no_disk_usage_section_at_all_is_silent(caplog):
    monitor = _quota_monitor()
    with caplog.at_level(logging.WARNING, logger="tinyagentos.disk_quota"):
        with patch("tinyagentos.disk_quota._run",
                   AsyncMock(return_value=(0, "Name: x\nStatus: STOPPED\n"))):
            used_gib = await monitor._sample_btrfs_qgroup("taos-agent-alice")

    assert used_gib is None
    assert _warnings(caplog) == []


# ------------------------------------------------------------------
# Values the parser must refuse or keep exact
# ------------------------------------------------------------------

@pytest.mark.parametrize("value", ["-1GiB", "-512m", "-1", "-0.5TB", "-1024"])
def test_parse_size_bytes_rejects_negatives(value):
    """A negative memory_mb / used_gib is nonsense at every call site."""
    with pytest.raises(ValueError, match="unparsable size"):
        parse_size_bytes(value)
    assert parse_size_bytes_or(value) == 0


@pytest.mark.parametrize("value", ["inf", "-inf", "nan", "infGiB", "NaN"])
def test_parse_size_bytes_rejects_non_finite(value):
    """`int(float('inf'))` raises OverflowError, which no caller catches."""
    with pytest.raises(ValueError, match="unparsable size"):
        parse_size_bytes(value)
    assert parse_size_bytes_or(value, -1) == -1


def test_parse_size_bytes_keeps_large_byte_counts_exact():
    """Going through float() silently rounds byte counts past 2**53."""
    assert parse_size_bytes("9007199254740993") == 9007199254740993
    assert parse_size_bytes("1e309") == 10 ** 309


def test_resize_storage_cli_survives_a_huge_size(monkeypatch):
    """`taos worker resize-storage --size inf` must exit 2, not traceback."""
    from tinyagentos.cli import worker

    def _must_not_run(*a, **k):
        raise AssertionError("pre-flight ran on an unparsable size")

    monkeypatch.setattr(worker.subprocess, "check_output", _must_not_run)
    ns = worker.build_parser().parse_args(["resize-storage", "--size", "inf"])
    assert worker._resize_storage(ns) == 2


# ------------------------------------------------------------------
# The CLI hands truncate(1) something truncate(1) understands
# ------------------------------------------------------------------

def test_resize_storage_hands_truncate_a_byte_count(monkeypatch):
    """truncate(1) has no IEC suffixes -- pass it the parsed byte count."""
    from tinyagentos.cli import worker

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return MagicMock(returncode=0)

    monkeypatch.setattr(worker.subprocess, "check_output", lambda *a, **k: "1000\n")
    monkeypatch.setattr(worker.subprocess, "run", _fake_run)

    ns = worker.build_parser().parse_args(["resize-storage", "--size", "1.5TiB"])
    assert worker._resize_storage(ns) == 0

    truncate = [c for c in calls if "truncate" in c]
    assert truncate == [[
        "sudo", "truncate", "-s", str(1024**4 * 3 // 2),
        "/var/lib/incus/disks/taos-worker-pool.img",
    ]]


def test_resize_storage_help_does_not_understate_the_parser():
    from tinyagentos.cli.worker import build_parser

    action = next(
        a for a in build_parser()._subparsers._group_actions[0]
        .choices["resize-storage"]._actions if a.dest == "size"
    )
    assert "truncate(1)" not in action.help
    assert "iB" in action.help


# ------------------------------------------------------------------
# Shipped templates must not silently shrink now that SI != IEC
# ------------------------------------------------------------------

def test_shipped_template_memory_limits_are_whole_binary_units():
    """`"1GB"` parses to 953 MiB; the templates mean 1024."""
    import json
    from pathlib import Path

    from tinyagentos.size_units import BYTES_PER_MIB

    root = Path(__file__).resolve().parents[1]
    for name in ("openclaw-agents.json", "system-prompts.json"):
        path = root / "data" / "templates" / name
        entries = json.loads(path.read_text())
        limits = sorted({
            e["memory_limit"] for e in entries if e.get("memory_limit")
        })
        assert limits, f"{name} declares no memory_limit"
        for limit in limits:
            parsed = parse_size_bytes(limit)
            assert parsed % BYTES_PER_MIB == 0, f"{name}: {limit} is not whole MiB"
            assert parsed & (parsed - 1) == 0, (
                f"{name}: {limit} -> {parsed} bytes is not a power of two"
            )
