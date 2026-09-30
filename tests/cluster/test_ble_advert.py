"""The scan's advert filter (transport.match_advert) and what scan() does with
the advert's ``paired`` marker.

Hostile adverts first: junk manufacturer data, a valid marker under the wrong
company id, a truncated marker, a marker with the wrong magic, and an advert
with neither the service UUID nor a marker. Each must be dropped. Then the
accepting arms, each on its own, so neither can carry the other.
"""
from __future__ import annotations

import pytest

from tinyagentos.cluster.ble import proto
from tinyagentos.cluster.ble.pairing import BlePairingManager
from tinyagentos.cluster.ble.transport import match_advert
from tinyagentos.cluster.manager import ClusterManager

from cluster.conftest import FakeBoard, FakeTransport

GOOD_UNPAIRED = proto.advert_mfr_data(False)
GOOD_PAIRED = proto.advert_mfr_data(True)


def _match(uuids=(), mfr=None):
    return match_advert("AA:BB", "taOSusb-TEST", -60, list(uuids), mfr or {})


# -- hostile: every one of these is some other device -------------------------

@pytest.mark.parametrize("junk", [
    b"",
    b"\x00",
    b"\xff" * 6,
    b"taOS",                      # magic only, truncated before version/state
    GOOD_UNPAIRED[:-1],           # truncated by one byte
    GOOD_UNPAIRED + b"\x00",      # one byte too long
    b"TAOS\x01\x00",              # wrong-case magic
    b"xaOS\x01\x00",
    "taOS\x01\x00",               # a str, not bytes
    None,
    12345,
], ids=lambda v: repr(v)[:24])
def test_junk_mfr_data_under_our_company_id_is_dropped(junk):
    assert _match(mfr={proto.MFR_ID: junk}) is None


def test_valid_marker_under_the_wrong_company_id_is_dropped():
    assert _match(mfr={0x004C: GOOD_UNPAIRED}) is None      # Apple's id
    assert _match(mfr={proto.MFR_ID - 1: GOOD_PAIRED}) is None


def test_no_uuid_and_no_mfr_is_dropped():
    assert _match() is None
    assert _match(uuids=["0000180f-0000-1000-8000-00805f9b34fb"]) is None


def test_non_dict_mfr_data_is_dropped_not_raised():
    assert match_advert("AA", "x", -1, [], [(proto.MFR_ID, GOOD_UNPAIRED)]) is None
    assert match_advert("AA", "x", -1, None, None) is None


# -- accepting arms, each alone ------------------------------------------------

def test_marker_alone_accepts_and_reads_unpaired():
    adv = _match(mfr={proto.MFR_ID: GOOD_UNPAIRED})
    assert adv is not None and adv.paired is False


def test_marker_alone_accepts_and_reads_paired():
    adv = _match(mfr={proto.MFR_ID: GOOD_PAIRED})
    assert adv is not None and adv.paired is True


def test_marker_accepts_bytearray_as_bleak_delivers_it():
    adv = _match(mfr={proto.MFR_ID: bytearray(GOOD_PAIRED)})
    assert adv is not None and adv.paired is True


def test_service_uuid_alone_accepts_with_paired_unknown():
    adv = _match(uuids=[proto.SERVICE_UUID.upper()])
    assert adv is not None and adv.paired is None


def test_service_uuid_with_junk_marker_still_accepts_as_unknown():
    adv = _match(uuids=[proto.SERVICE_UUID], mfr={proto.MFR_ID: b"junk"})
    assert adv is not None and adv.paired is None


# -- scan(): paired comes out of the advert ------------------------------------

def _manager(tmp_path, boards):
    transport = FakeTransport(boards)
    mgr = BlePairingManager(
        data_dir=tmp_path, cluster_manager=ClusterManager(), pairing_store=None,
        bind_port=6969, transport=transport,
    )
    return mgr, transport


@pytest.mark.asyncio
async def test_scan_lists_a_paired_board_from_its_advert_without_connecting(tmp_path):
    board = FakeBoard(board_id="PRD1", state="paired", pairable=False)
    mgr, transport = _manager(tmp_path, {"addr-p": board})
    devices = await mgr.scan(2)
    assert [d["paired"] for d in devices] == [True]
    assert devices[0]["pairable"] is False
    assert transport.connected == []


@pytest.mark.asyncio
async def test_scan_marks_an_unpaired_board_unpaired(tmp_path):
    board = FakeBoard(board_id="UNP1")
    mgr, transport = _manager(tmp_path, {"addr-u": board})
    devices = await mgr.scan(2)
    assert [(d["paired"], d["pairable"], d["board_id"]) for d in devices] == [(False, True, "UNP1")]
    assert transport.connected == ["addr-u"]


@pytest.mark.asyncio
async def test_scan_finds_a_marker_only_board(tmp_path):
    board = FakeBoard(board_id="MKR1", advert_uuid=False)
    mgr, _ = _manager(tmp_path, {"addr-m": board})
    devices = await mgr.scan(2)
    assert [d["board_id"] for d in devices] == ["MKR1"]


@pytest.mark.asyncio
async def test_scan_falls_back_to_info_when_advert_has_no_marker(tmp_path):
    """A UUID-only board that is paired: the advert cannot say so, the info read does."""
    board = FakeBoard(board_id="OLD1", state="paired", pairable=False, advert_mfr=False)
    mgr, transport = _manager(tmp_path, {"addr-o": board})
    devices = await mgr.scan(2)
    assert [d["paired"] for d in devices] == [True]
    assert transport.connected == ["addr-o"]
