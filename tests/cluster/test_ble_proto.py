"""Pin tinyagentos/cluster/ble/proto.py byte-identical to taosusb's
files/taosble/proto.py.

This module is vendored, not written here -- see its own docstring and
docs/taosusb-pairing-plan.md. A hash mismatch means someone edited the
controller's copy directly (or the board side changed and this copy fell
behind); either way the fix is to re-copy, never to hand-patch.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import tinyagentos.cluster.ble.proto as proto

# Recorded when the file was vendored (see cluster/ble/proto.py's own
# docstring). Update this only by re-running:
#   cp <taosusb>/files/taosble/proto.py tinyagentos/cluster/ble/proto.py
# and recomputing the hash below from that fresh copy.
# 2026-09: protocol v2 (commit/reveal nonces, release audit H1) was written
# here first; taosusb's files/taosble/proto.py must be re-copied FROM this
# file so the two stay byte-identical.
EXPECTED_SHA256 = "ce802c42fcd66c521c3dc0c39578b72f68037d4d57da73e231e5cd7847363e66"


def test_proto_is_byte_identical_to_vendored_source():
    path = Path(proto.__file__)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == EXPECTED_SHA256, (
        "tinyagentos/cluster/ble/proto.py no longer matches its vendored "
        "sha256 -- re-vendor from taosusb files/taosble/proto.py"
    )


def test_proto_exposes_the_expected_protocol_surface():
    """A lightweight sanity check alongside the hash pin: the names the
    controller side (pairing.py) imports must exist."""
    for name in (
        "SERVICE_UUID", "CHAR_INFO_UUID", "CHAR_PAIR_UUID", "CHAR_LINK_UUID",
        "fragment", "Reassembler", "PairInitiator", "PairResponder",
        "x25519_keypair", "key_from_raw", "raw_from_key",
        "validate_provision", "info_frame", "commitment",
    ):
        assert hasattr(proto, name), f"proto.py is missing {name!r}"
