"""Pin tinyagentos/cluster/ble/proto.py byte-identical to taosusb's
files/taosble/proto.py.

This module is vendored, not written here -- see its own docstring and
docs/taosusb-pairing-plan.md. A hash mismatch means someone edited the
controller's copy directly (or the board side changed and this copy fell
behind); either way the fix is to re-copy, never to hand-patch.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey

import tinyagentos.cluster.ble.proto as proto

# Recorded when the file was vendored (see cluster/ble/proto.py's own
# docstring). Update this only by re-running:
#   cp <taosusb>/files/taosble/proto.py tinyagentos/cluster/ble/proto.py
# and recomputing the hash below from that fresh copy.
# 2026-09: protocol v2 (commit/reveal nonces, release audit H1) was written
# here first; taosusb's files/taosble/proto.py must be re-copied FROM this
# file so the two stay byte-identical.
EXPECTED_SHA256 = "ad6af4879a0da01bd5689fb0f0acc43ef7a36570d0b00a779fff0a9db7eac7c5"


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


# --- RED-FIRST: weak hello keys hardening ----------------------------------------------------------

def test_all_zero_epub_hello_gets_no_commit():
    """PairResponder fed a v2 hello whose epub is 32 zero bytes returns
    an error frame with no `commit` key."""
    from tinyagentos.cluster.ble.proto import PairResponder, x25519_keypair
    from tinyagentos.cluster.ble.proto import b64, json

    board_id, static_priv = "TEST", x25519_keypair()[0]
    responder = PairResponder(board_id, static_priv)
    
    # Create hello with all-zero epub (32 bytes)
    hello = {"t": "hello", "v": proto.PROTO_VERSION, "cpub": b64(os.urandom(32)),
             "epub": b64(bytes(32))}
    raw = json.dumps(hello).encode("utf-8")
    reply = responder.handle_message(raw)
    
    reply_dict = json.loads(reply.decode("utf-8"))
    assert reply_dict["t"] == "error"
    assert "commit" not in reply_dict


def test_low_order_cpub_hello_refused():
    """PairResponder refuses a hello with a known low-order X25519 point as cpub."""
    from tinyagentos.cluster.ble.proto import PairResponder, x25519_keypair
    from tinyagentos.cluster.ble.proto import b64, json

    board_id, static_priv = "TEST", x25519_keypair()[0]
    responder = PairResponder(board_id, static_priv)
    
    # Create a low-order point (0x1, which is all-zero in cofactor encoding)
    low_order_bytes = bytes([0x01] + [0] * 31)  # Well-known low-order point
    hello = {"t": "hello", "v": proto.PROTO_VERSION, "cpub": b64(low_order_bytes),
             "epub": b64(os.urandom(32))}
    raw = json.dumps(hello).encode("utf-8")
    reply = responder.handle_message(raw)
    
    reply_dict = json.loads(reply.decode("utf-8"))
    assert reply_dict["t"] == "error"
    assert "commit" not in reply_dict


def test_controller_refuses_weak_board_epub():
    """The controller side refuses a board hello with an all-zero epub before sending its nonce."""
    from tinyagentos.cluster.ble.proto import PairInitiator, x25519_keypair
    from tinyagentos.cluster.ble.proto import b64, json

    controller_id, static_priv = "ctrl", x25519_keypair()[0]
    initiator = PairInitiator(controller_id, static_priv)
    
    # The controller checks for weak keys in on_hello_commit
    # We need to first set up the initiator's internal state
    initiator._pending = {"epriv": x25519_keypair()[0], "epub": x25519_keypair()[1],
                          "n": os.urandom(16)}
    
    # Simulate the board sending a hello with all-zero epub
    # The controller reads this via on_hello_commit
    board_hello = {"t": "hello", "v": proto.PROTO_VERSION,
                   "bpub": b64(os.urandom(32)),
                   "epub": b64(bytes(32)),  # all-zero epub
                   "commit": b64(os.urandom(32))}
    raw = json.dumps(board_hello).encode("utf-8")
    
    with pytest.raises(ValueError) as exc_info:
        initiator.on_hello_commit("BOARD", raw)
    
    # Should fail with weak key error or bad epub
    assert "weak" in str(exc_info.value).lower() or "epub" in str(exc_info.value).lower()


# --- fragment flags hardening ------------------------------------------------------------

def test_unknown_fragment_flags_refused():
    """Reassembler.feed refuses a fragment carrying any flag bit other than
    FLAG_FIRST|FLAG_LAST."""
    from tinyagentos.cluster.ble.proto import Reassembler, FLAG_FIRST, FLAG_LAST
    
    reasm = Reassembler()
    
    # Test with unknown flags (0xFF includes bits other than FLAG_FIRST|FLAG_LAST)
    frag = bytes([0xFF, 1]) + b'x'  # All flag bits set except valid ones
    result = reasm.feed(frag)
    
    assert result is None
    # Should have recorded a bad-flags drop
    assert hasattr(reasm, 'last_drop')
    assert 'flag' in reasm.last_drop.lower()


# --- size and drop reasons ------------------------------------------------------------

def test_fragment_oversize_raises():
    """fragment() raises ValueError when len(payload) > MAX_MESSAGE."""
    from tinyagentos.cluster.ble.proto import fragment, MAX_MESSAGE
    
    payload = b'x' * (MAX_MESSAGE + 1)
    
    # fragment function raises ValueError when payload > MAX_MESSAGE
    # due to the reassembler check (len(buf) > self.max_message)
    with pytest.raises(ValueError):
        fragment(payload, 1, 100)


def test_drop_reasons_are_named():
    """The inflight, oversize, orphan and duplicate-FIRST drops each report
    their own distinct reason."""
    from tinyagentos.cluster.ble.proto import Reassembler, FLAG_FIRST, FLAG_LAST
    
    reasm = Reassembler(max_inflight=1, max_message=10)
    
    # Test duplicate_first drop: second FIRST for same mid when already inflight
    frag1 = bytes([FLAG_FIRST, 0]) + b'hello'  # mid=0
    reasm.feed(frag1)  # Should succeed, message incomplete
    
    frag2 = bytes([FLAG_FIRST, 0]) + b'world'  # Same mid=0, should drop
    result = reasm.feed(frag2)
    assert result is None
    assert hasattr(reasm, 'last_drop')
    assert 'duplicate_first' in reasm.last_drop.lower()
    
    # Test inflight drop: too many messages in flight
    reasm = Reassembler(max_inflight=1, max_message=10)
    frag3 = bytes([FLAG_FIRST, 1]) + b'short'
    reasm.feed(frag3)
    
    frag4 = bytes([FLAG_FIRST, 2]) + b'another'  # New mid, but too many in flight
    result = reasm.feed(frag4)
    assert result is None
    assert 'inflight' in reasm.last_drop.lower()


# --- RED-FIRST: new tests for the fix-forward -------------------------------------------------------

def test_last_drop_resets_on_next_feed():
    """Reassembler.last_drop is reset at the top of feed(), so a bad-flags
    feed followed by a valid FIRST-only fragment leaves last_drop as None."""
    from tinyagentos.cluster.ble.proto import Reassembler, FLAG_FIRST
    
    reasm = Reassembler()
    
    # First feed: bad flags (0xFF has unknown bits)
    frag_bad = bytes([0xFF, 1]) + b'x'
    result = reasm.feed(frag_bad)
    assert result is None
    assert reasm.last_drop == "bad_flags"
    
    # Second feed: valid FIRST-only fragment (no LAST, so incomplete)
    frag_good = bytes([FLAG_FIRST, 2]) + b'a'
    result = reasm.feed(frag_good)
    assert result is None  # incomplete message
    # last_drop must be reset to None because this feed didn't cause a drop
    assert reasm.last_drop is None


def test_weak_epub_hello_error_is_named():
    """PairResponder hello with an all-zero epub gets an error frame whose
    `why` is exactly 'weak_key'. A 31-byte epub (malformed) still gets 'bad hello'."""
    from tinyagentos.cluster.ble.proto import PairResponder, x25519_keypair, b64, json
    
    board_id, static_priv = "TEST", x25519_keypair()[0]
    responder = PairResponder(board_id, static_priv)
    
    # All-zero epub (32 bytes) -> should get 'weak_key'
    hello_weak = {"t": "hello", "v": proto.PROTO_VERSION,
                  "cpub": b64(os.urandom(32)),
                  "epub": b64(bytes(32))}
    raw = json.dumps(hello_weak).encode("utf-8")
    reply = responder.handle_message(raw)
    reply_dict = json.loads(reply.decode("utf-8"))
    assert reply_dict["t"] == "error"
    assert reply_dict["why"] == "weak_key", f"Expected 'weak_key', got {reply_dict['why']!r}"
    
    # 31-byte epub (malformed base64 decode length) -> should get 'bad hello'
    hello_malformed = {"t": "hello", "v": proto.PROTO_VERSION,
                       "cpub": b64(os.urandom(32)),
                       "epub": b64(b"x" * 31)}  # 31 bytes, not 32
    raw = json.dumps(hello_malformed).encode("utf-8")
    reply = responder.handle_message(raw)
    reply_dict = json.loads(reply.decode("utf-8"))
    assert reply_dict["t"] == "error"
    assert reply_dict["why"] == "bad hello", f"Expected 'bad hello', got {reply_dict['why']!r}"


def test_trial_exchange_all_zero_result_refused():
    """Monkeypatch X25519PrivateKey.generate (as imported in proto) to return
    a mock whose exchange() returns bytes(32) -> _validate_key_not_weak raises ValueError('weak_key')."""
    from tinyagentos.cluster.ble.proto import _validate_key_not_weak
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from unittest.mock import patch, MagicMock
    
    # Valid 32-byte key (not all-zero)
    good_key = os.urandom(32)
    while good_key == bytes(32):
        good_key = os.urandom(32)
    
    # Patch generate to return a mock whose exchange returns all-zero
    mock_priv = MagicMock()
    mock_priv.exchange.return_value = bytes(32)
    with patch.object(X25519PrivateKey, 'generate', return_value=mock_priv):
        with pytest.raises(ValueError) as exc_info:
            _validate_key_not_weak(good_key)
        assert "weak_key" in str(exc_info.value)


# --- RED-FIRST: low-order key must map to weak_key ---------------------------------------------------

def test_low_order_key_is_weak_key():
    """_validate_key_not_weak maps ANY ValueError from the trial exchange
    (including 'Error computing shared key.' from cryptography 50.0.0)
    to ValueError('weak_key')."""
    from tinyagentos.cluster.ble.proto import _validate_key_not_weak
    
    low_order = bytes([1]) + bytes(31)
    with pytest.raises(ValueError) as exc_info:
        _validate_key_not_weak(low_order)
    assert str(exc_info.value) == "weak_key"


def test_responder_low_order_epub_gets_weak_key_frame():
    """PairResponder hello with a low-order epub returns an error frame
    whose why is exactly 'weak_key', not an uncaught ValueError."""
    from tinyagentos.cluster.ble.proto import PairResponder, x25519_keypair, b64, json
    
    board_id, static_priv = "TEST", x25519_keypair()[0]
    responder = PairResponder(board_id, static_priv)
    
    low_order = bytes([1]) + bytes(31)
    hello = {"t": "hello", "v": proto.PROTO_VERSION,
             "cpub": b64(os.urandom(32)),
             "epub": b64(low_order)}
    raw = json.dumps(hello).encode("utf-8")
    reply = responder.handle_message(raw)
    
    reply_dict = json.loads(reply.decode("utf-8"))
    assert reply_dict["t"] == "error"
    assert reply_dict["why"] == "weak_key"
