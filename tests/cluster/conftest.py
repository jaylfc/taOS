"""Shared fakes for taOSusb BLE pairing (S1) tests.

FakeBoard/FakeConnection/FakeTransport wire tinyagentos.cluster.ble.transport's
interface to a REAL ``proto.PairResponder`` (the vendored board-side protocol
code, unmodified) so these tests exercise the actual wire handshake instead
of a mock of it. Only the BLE radio itself (bleak/BlueZ) is faked.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from tinyagentos.cluster.ble import proto
from tinyagentos.cluster.ble.transport import Advert, Connection, Transport, chunk_size


class FakeBoard:
    """An in-process stand-in for a taOSusb board, backed by a real PairResponder."""

    def __init__(self, *, board_id: str = "TEST", name: str | None = None,
                 state: str = "unpaired", pairable: bool = True):
        self.board_id = board_id
        self.name = name or f"taOSusb-{board_id}"
        self.state = state
        self.pairable = pairable
        self.priv, self.pub = proto.x25519_keypair()
        self.responder = proto.PairResponder(board_id, self.priv, paired=(state == "paired"))

    def info_bytes(self) -> bytes:
        return proto.info_frame(
            self.board_id, self.name, self.state, self.pairable,
            proto.pub_bytes(self.pub),
        )


class FakeConnection(Connection):
    """One fake GATT connection to a FakeBoard.

    ``corrupt_info`` / ``corrupt_hello_reply`` / ``no_reply`` simulate a
    hostile or broken board for the malformed-response test cases.
    """

    def __init__(
        self,
        board: FakeBoard,
        *,
        mtu: int = 155,
        corrupt_info: bool = False,
        corrupt_hello_reply: bool = False,
        no_reply: bool = False,
    ):
        self.board = board
        self.mtu = mtu
        self._corrupt_info = corrupt_info
        self._corrupt_hello_reply = corrupt_hello_reply
        self._no_reply = no_reply
        self._board_reassembler = proto.Reassembler()
        self._notify_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.closed = False
        self._first_reply_sent = False

    async def read_info(self) -> bytes:
        if self._corrupt_info:
            return b"{not valid json at all"
        return self.board.info_bytes()

    async def write_pair(self, fragment: bytes) -> None:
        msg = self._board_reassembler.feed(fragment)
        if msg is None:
            return  # not the last fragment yet
        if self._no_reply:
            return
        if self._corrupt_hello_reply and not self._first_reply_sent:
            self._first_reply_sent = True
            reply = b"{not valid json either"
        else:
            self._first_reply_sent = True
            reply = self.board.responder.handle_message(msg)
        for frag in proto.fragment(reply, 0, chunk_size(self.mtu)):
            self._notify_queue.put_nowait(frag)

    async def wait_pair_notify(self, timeout: float) -> bytes:
        return await asyncio.wait_for(self._notify_queue.get(), timeout)

    async def close(self) -> None:
        self.closed = True


class FakeTransport(Transport):
    """Maps addresses to FakeBoards. ``conn_kwargs`` is forwarded to every
    FakeConnection it opens (for corrupt_info/corrupt_hello_reply/no_reply)."""

    def __init__(self, boards: dict[str, FakeBoard] | None = None, **conn_kwargs):
        self.boards = boards or {}
        self._conn_kwargs = conn_kwargs
        self.connected: list[str] = []

    async def scan(self, seconds: float) -> list[Advert]:
        return [
            Advert(address=addr, name=b.name, rssi=-50, service_uuids=[proto.SERVICE_UUID])
            for addr, b in self.boards.items()
        ]

    async def connect(self, address: str) -> Connection:
        self.connected.append(address)
        if address not in self.boards:
            raise ConnectionError(f"no fake board at {address!r}")
        return FakeConnection(self.boards[address], **self._conn_kwargs)


@pytest.fixture
def fake_board():
    return FakeBoard()
