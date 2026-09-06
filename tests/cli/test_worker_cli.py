"""Parser-level tests for `taos worker` subcommands.

These tests drive build_parser() directly — no subprocesses, no incus,
no filesystem side-effects.
"""
import pytest
from tinyagentos.cli.worker import build_parser


def test_convert_to_lxc_parses():
    parser = build_parser()
    ns = parser.parse_args(["convert-to-lxc", "http://controller:6969"])
    assert ns.cmd == "convert-to-lxc"
    assert ns.controller_url == "http://controller:6969"
    assert ns.yes is False


def test_convert_to_lxc_with_yes_flag():
    parser = build_parser()
    ns = parser.parse_args(["convert-to-lxc", "http://c:6969", "-y"])
    assert ns.yes is True


def test_convert_to_lxc_long_yes_flag():
    parser = build_parser()
    ns = parser.parse_args(["convert-to-lxc", "http://c:6969", "--yes"])
    assert ns.yes is True


def test_dedup_enable_parses():
    parser = build_parser()
    ns = parser.parse_args(["dedup", "enable"])
    assert ns.cmd == "dedup"
    assert ns.action == "enable"


def test_dedup_disable_parses():
    parser = build_parser()
    ns = parser.parse_args(["dedup", "disable"])
    assert ns.cmd == "dedup"
    assert ns.action == "disable"


def test_dedup_invalid_action_rejected():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["dedup", "toggle"])


def test_resize_storage_requires_size():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["resize-storage"])


def test_resize_storage_with_size():
    parser = build_parser()
    ns = parser.parse_args(["resize-storage", "--size", "500G"])
    assert ns.cmd == "resize-storage"
    assert ns.size == "500G"


def test_resize_storage_terabyte():
    parser = build_parser()
    ns = parser.parse_args(["resize-storage", "--size", "1T"])
    assert ns.size == "1T"


def test_no_subcommand_exits():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parse_iec_bytes_handles_units():
    from tinyagentos.cli.worker import _parse_iec_bytes
    assert _parse_iec_bytes("500G") == 500 * 1024**3
    assert _parse_iec_bytes("1T") == 1024**4
    assert _parse_iec_bytes("512M") == 512 * 1024**2
    assert _parse_iec_bytes("4096") == 4096


def test_parse_iec_bytes_rejects_invalid():
    import pytest as _pytest
    from tinyagentos.cli.worker import _parse_iec_bytes
    with _pytest.raises(ValueError):
        _parse_iec_bytes("")


def test_each_subcommand_has_func():
    """Every parsed namespace must carry a callable .func for dispatch."""
    parser = build_parser()

    ns1 = parser.parse_args(["convert-to-lxc", "http://x:6969"])
    assert callable(ns1.func)

    ns2 = parser.parse_args(["dedup", "enable"])
    assert callable(ns2.func)

    ns3 = parser.parse_args(["resize-storage", "--size", "200G"])
    assert callable(ns3.func)
