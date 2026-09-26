"""A man in the middle must not be able to forge the BLE pairing code.

The attack (release security audit H1): an adversary in radio range sits
between the controller and a real board. It runs its own hello with the
board, which fixes the board-side 6-digit code. It then answers the
controller with its OWN keys and picks its own board nonce. If nothing
commits that nonce before the controller's nonce is known, the adversary can
grind it until the controller shows the same code as the board. The admin
sees two matching codes, confirms, and the controller seals the new node
signing key to the adversary.

The test drives the real BlePairingManager (real ClusterPairingStore, real
ClusterManager) through a FakeTransport whose connection is the adversary.
The adversary talks to a real ``proto.PairResponder`` on the board side and
speaks whatever wire version the controller speaks, so the same test ran red
against the old protocol and runs green against the commit/reveal one.

The code space is cut from 10^6 to 10^3 for the test (the adversary's grind
is then milliseconds instead of about five seconds); the loop is the same
loop, and a smaller space only helps the attacker.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey

import tinyagentos.cluster.ble.pairing as pairing_mod
from tinyagentos.cluster.ble import proto
from tinyagentos.cluster.ble.pairing import BlePairingManager, PairError
from tinyagentos.cluster.ble.transport import Connection, Transport, chunk_size
from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.pairing_store import ClusterPairingStore

from cluster.conftest import FakeBoard

GRIND_LIMIT = 200_000


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ClusterPairingStore(tmp_path / "pairing.db")
    await s.init()
    yield s
    await s.close()


@pytest.fixture
def cluster():
    return ClusterManager()


@pytest.fixture(autouse=True)
def small_code_space(monkeypatch):
    real = proto.derive_code
    monkeypatch.setattr(proto, "derive_code", lambda T: real(T)[-3:])
    monkeypatch.setattr(pairing_mod, "_HELLO_TIMEOUT_S", 2.0)
    monkeypatch.setattr(pairing_mod, "_PROVISION_TIMEOUT_S", 2.0)
    monkeypatch.setattr(pairing_mod, "_CONNECT_TIMEOUT_S", 2.0)


def _j(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")


class MitmConnection(Connection):
    """The controller's GATT connection, answered by an adversary that relays
    nothing honestly: it pairs with the real board itself and impersonates the
    board to the controller with its own keys."""

    def __init__(self, board: FakeBoard, mtu: int = 155):
        self.board = board
        self.mtu = mtu
        self._reasm = proto.Reassembler()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.static_priv, self.static_pub = proto.x25519_keypair()
        self.eph_priv, self.eph_pub = proto.x25519_keypair()
        self.board_code: str | None = None
        self.ctrl_hello: dict | None = None
        self.session_key: bytes | None = None
        self.forced = False
        self.stolen: dict | None = None

    # -- board side: a real handshake with the real responder --------------

    def _pair_with_board(self) -> None:
        adv = proto.PairInitiator("mitm", proto.x25519_keypair()[0])
        reply = self.board.responder.handle_message(adv.start_hello())
        if hasattr(adv, "on_hello_reply"):          # protocol v1
            self.board_code = adv.on_hello_reply(self.board.board_id, reply)
        else:                                        # protocol v2 (commit/reveal)
            nonce_msg = adv.on_hello_commit(self.board.board_id, reply)
            reveal = self.board.responder.handle_message(nonce_msg)
            self.board_code = adv.on_reveal(reveal)
        assert self.board_code == self.board.responder.current_code()

    # -- controller side: grind the board nonce toward the board's code -----

    def _grind(self, c_n: bytes) -> bytes:
        h = self.ctrl_hello
        cpub, c_epub = proto.unb64(h["cpub"], 32), proto.unb64(h["epub"], 32)
        bpub, b_epub = proto.pub_bytes(self.static_pub), proto.pub_bytes(self.eph_pub)
        for _ in range(GRIND_LIMIT):
            b_n = os.urandom(16)
            T = proto.transcript(self.board.board_id, cpub, c_epub, c_n, bpub, b_epub, b_n)
            if proto.derive_code(T) == self.board_code:
                self.forced = True
                self.session_key = proto.derive_session_key(
                    self.eph_priv, X25519PublicKey.from_public_bytes(c_epub),
                    self.static_priv, X25519PublicKey.from_public_bytes(cpub), T)
                return b_n
        raise AssertionError("grind limit hit; raise GRIND_LIMIT")

    def _answer(self, msg: dict) -> bytes:
        t = msg.get("t")
        if t == "hello":
            self.ctrl_hello = msg
            self._pair_with_board()
            bpub = proto.b64(proto.pub_bytes(self.static_pub))
            epub = proto.b64(proto.pub_bytes(self.eph_pub))
            if "n" in msg:
                # v1: the controller's nonce is already on the table, so the
                # board nonce can be chosen last.
                b_n = self._grind(proto.unb64(msg["n"], 16))
                return _j({"t": "hello", "bpub": bpub, "epub": epub, "n": proto.b64(b_n)})
            # v2: the adversary must commit before it learns c_n. It commits
            # to a throwaway nonce and hopes to reveal a different one.
            cpub, c_epub = proto.unb64(msg["cpub"], 32), proto.unb64(msg["epub"], 32)
            commit = proto.commitment(self.board.board_id, cpub, c_epub,
                                      proto.pub_bytes(self.static_pub),
                                      proto.pub_bytes(self.eph_pub), os.urandom(16))
            return _j({"t": "hello", "v": proto.PROTO_VERSION, "bpub": bpub, "epub": epub,
                       "commit": proto.b64(commit)})
        if t == "nonce":
            b_n = self._grind(proto.unb64(msg["n"], 16))
            return _j({"t": "reveal", "n": proto.b64(b_n)})
        if t == "sealed":
            pt = proto.unseal(self.session_key, b"pair", proto.unb64(msg["c"]))
            self.stolen = json.loads(pt.decode("utf-8"))
            nonce = os.urandom(12)
            blob = proto.seal(self.session_key, b"pair", _j({"t": "ok"}), nonce)
            return _j({"t": "sealed", "c": proto.b64(blob)})
        return _j({"t": "error", "why": "?"})

    # -- Connection interface ----------------------------------------------

    async def read_info(self) -> bytes:
        return self.board.info_bytes()      # relayed honestly

    async def write_pair(self, fragment: bytes) -> None:
        raw = self._reasm.feed(fragment)
        if raw is None:
            return
        reply = self._answer(json.loads(raw.decode("utf-8")))
        for frag in proto.fragment(reply, 0, chunk_size(self.mtu)):
            self._queue.put_nowait(frag)

    async def wait_pair_notify(self, timeout: float) -> bytes:
        return await asyncio.wait_for(self._queue.get(), timeout)

    async def close(self) -> None:
        pass


class MitmTransport(Transport):
    def __init__(self, conn: MitmConnection):
        self.conn = conn

    async def scan(self, seconds: float):
        return []

    async def connect(self, address: str) -> Connection:
        return self.conn


@pytest.mark.asyncio
async def test_mitm_cannot_force_matching_codes_and_steal_the_node_key(cluster, store, tmp_path):
    board = FakeBoard(board_id="MITM")
    mitm = MitmConnection(board)
    mgr = BlePairingManager(
        data_dir=tmp_path, cluster_manager=cluster, pairing_store=store,
        bind_port=6969, transport=MitmTransport(mitm), llm_gateway_enabled=False,
    )

    try:
        started = await mgr.start("AA:BB")
    except PairError as exc:
        started = None
        refused = exc
    # The adversary really did pair with the real board and knows its code.
    assert mitm.board_code is not None
    assert board.responder.current_code() == mitm.board_code

    if started is not None and started["code"] == board.responder.current_code():
        # The admin compares the controller's code with the board's, sees a
        # match, and confirms.
        await mgr.confirm(started["session"])

    got_provision = mitm.stolen is not None
    assert not got_provision, (
        "man in the middle forced matching codes (forced=%s) and received the sealed "
        "provision: node_key=%s..." % (mitm.forced, (mitm.stolen or {}).get("node_key", "")[:8])
    )
    assert started is None, "the handshake with a lying board must be refused"
    assert "commitment" in str(refused)
    assert await store.get_signing_key(board.name) is None
    assert cluster.get_worker(board.name) is None


# ---------------------------------------------------------------------------
# The commit/reveal state machine, one rule at a time (real proto, both ends)
# ---------------------------------------------------------------------------

def _pair_objects():
    rsp = proto.PairResponder("UNIT", proto.x25519_keypair()[0])
    ini = proto.PairInitiator("ctrl", proto.x25519_keypair()[0])
    return rsp, ini


def _is_error(raw: bytes) -> bool:
    return json.loads(raw.decode("utf-8")).get("t") == "error"


def test_honest_handshake_agrees_on_the_code_only_after_the_reveal():
    rsp, ini = _pair_objects()
    hello = json.loads(ini.start_hello())
    assert "n" not in hello, "the controller nonce must not travel before the board commits"
    reply = rsp.handle_message(_j(hello))
    assert "n" not in json.loads(reply) and "commit" in json.loads(reply)
    assert rsp.current_code() is None
    nonce = ini.on_hello_commit("UNIT", reply)
    code = ini.on_reveal(rsp.handle_message(nonce))
    assert code == rsp.current_code()


def test_board_refuses_a_second_nonce_after_revealing():
    """Once b_n is public, a second nonce would let an attacker choose c_n
    knowing b_n and grind the board's code. The board must refuse it and keep
    the code it already derived."""
    rsp, ini = _pair_objects()
    nonce = ini.on_hello_commit("UNIT", rsp.handle_message(ini.start_hello()))
    ini.on_reveal(rsp.handle_message(nonce))
    code = rsp.current_code()
    again = rsp.handle_message(_j({"t": "nonce", "n": proto.b64(os.urandom(16))}))
    assert _is_error(again)
    assert rsp.current_code() == code


def test_board_refuses_a_nonce_before_any_hello_and_sealed_before_the_reveal():
    rsp, ini = _pair_objects()
    assert _is_error(rsp.handle_message(_j({"t": "nonce", "n": proto.b64(os.urandom(16))})))
    rsp.handle_message(ini.start_hello())
    sealed = rsp.handle_message(_j({"t": "sealed", "c": proto.b64(os.urandom(40))}))
    assert json.loads(sealed) == {"t": "error", "why": "no session"}


def test_controller_refuses_a_reveal_that_does_not_match_the_commitment():
    rsp, ini = _pair_objects()
    ini.on_hello_commit("UNIT", rsp.handle_message(ini.start_hello()))
    with pytest.raises(ValueError, match="commitment"):
        ini.on_reveal(_j({"t": "reveal", "n": proto.b64(os.urandom(16))}))
    assert ini.current_code() is None


def test_v1_and_v2_peers_refuse_each_other():
    rsp, ini = _pair_objects()
    # A v1 controller's hello: nonce up front, no version.
    v1_hello = {"t": "hello", "cpub": proto.b64(os.urandom(32)),
                "epub": proto.b64(os.urandom(32)), "n": proto.b64(os.urandom(16))}
    reply = json.loads(rsp.handle_message(_j(v1_hello)))
    assert reply["t"] == "error" and "version" in reply["why"]
    # A v1 board's reply: its nonce in the clear, no commitment, no version.
    ini.start_hello()
    v1_reply = {"t": "hello", "bpub": proto.b64(os.urandom(32)),
                "epub": proto.b64(os.urandom(32)), "n": proto.b64(os.urandom(16))}
    with pytest.raises(ValueError, match="protocol v1"):
        ini.on_hello_commit("UNIT", _j(v1_reply))


def test_advert_carries_protocol_version_2():
    assert proto.PROTO_VERSION == 2
    assert proto.parse_advert_mfr(proto.advert_mfr_data(False))["v"] == 2
