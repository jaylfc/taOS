"""taOSusb Bluetooth pairing (S1) -- controller-side orchestration.

Ties together the vendored protocol (``proto.py``), the BLE transport
(``transport.py``) and the cluster registry:

  1. ``scan()`` finds pairable boards over BLE and reads their ``info``
     characteristic for the authoritative name/board id/state.
  2. ``start()`` connects, runs the ``PairInitiator`` handshake, and returns
     the 6-digit code for a human to compare against the board.
  3. ``confirm()`` -- only once a human has confirmed the code -- mints the
     node's signing key through the exact same store method the manual
     worker-pairing flow uses (``ClusterPairingStore.register_device_key``,
     see pairing_store.py), registers the node as ``kind="device"``, and
     sends the board its sealed provision payload. A board error or timeout
     rolls back both the registration and the minted key.

No sealed field built here (``node_key``, an ``llm`` key, ``mesh_preauth``)
is ever returned to an HTTP caller -- see routes/cluster_ble.py, which reads
only the plain fields of the dicts this module returns.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from tinyagentos.cluster.ble import proto
from tinyagentos.cluster.ble.transport import (
    Advert,
    BleakTransport,
    BluetoothUnavailable,
    Connection,
    Transport,
    chunk_size,
)
from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.pairing_store import ClusterPairingStore
from tinyagentos.cluster.worker_protocol import WorkerInfo

logger = logging.getLogger(__name__)

MAX_SESSIONS = 2
SESSION_TTL_S = 120.0
_HELLO_TIMEOUT_S = 10.0
_PROVISION_TIMEOUT_S = 15.0
_CONNECT_TIMEOUT_S = 10.0

# proto message ids the controller uses for its own outgoing `pair` writes.
# Independent of whatever mid the board picks for its notifications --
# proto.Reassembler tracks each direction separately.
_MID_HELLO = 0
_MID_PROVISION = 1


class BluetoothError(Exception):
    """bleak or an adapter is missing. Routes map this to 503 bluetooth_unavailable."""


class PairError(Exception):
    """A pairing operation failed. ``status`` is the HTTP status the route should return."""

    def __init__(self, status: int, message: str, *, why: str | None = None):
        self.status = status
        self.why = why
        super().__init__(message)


def _load_or_create_controller_key(data_dir: Path) -> X25519PrivateKey:
    """Load the controller's static X25519 identity, creating it 0600 on first use."""
    path = Path(data_dir) / "ble_controller.key"
    if path.exists():
        raw = path.read_bytes()
        if len(raw) != 32:
            raise PairError(500, "corrupt ble_controller.key")
        return proto.key_from_raw(raw)
    priv, _pub = proto.x25519_keypair()
    raw = proto.raw_from_key(priv)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Lost a race with another process creating the same file first.
        return proto.key_from_raw(path.read_bytes())
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
    return priv


def controller_identity() -> str:
    """The controller's id sent in the sealed provision. No existing
    controller-identity concept exists elsewhere in this codebase (checked
    taosnet/mesh.py, account_proxy.py) -- they use the host's hostname for
    the same purpose, so this does too."""
    return socket.gethostname()


def controller_urls(port: int) -> list[str]:
    """http://<ip>:<port> for each non-loopback, non-link-local IPv4 the
    host has. Reuses the same address-enumeration helper mDNS publishing
    already uses (services/mdns_publisher.py) instead of a second one."""
    from tinyagentos.services.mdns_publisher import _detect_primary_ipv4

    return [f"http://{ip}:{port}" for ip in _detect_primary_ipv4()]


@dataclass
class PairSession:
    session_id: str
    address: str
    board_id: str
    board_name: str
    connection: Connection
    initiator: "proto.PairInitiator"
    code: str
    created_at: float
    reassembler: "proto.Reassembler" = field(default_factory=proto.Reassembler)


class BlePairingManager:
    """Owns the controller's static BLE identity and in-memory pairing sessions."""

    def __init__(
        self,
        *,
        data_dir: Path,
        cluster_manager: ClusterManager,
        pairing_store: ClusterPairingStore,
        bind_port: int,
        transport: Transport | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._cluster = cluster_manager
        self._pairing_store = pairing_store
        self._bind_port = bind_port
        # When a transport is injected (tests) it is used as-is, bleak or
        # not. In production this stays None until first use so app boot
        # never requires bleak to be installed.
        self._transport = transport
        self._priv: X25519PrivateKey | None = None
        self._sessions: dict[str, PairSession] = {}
        self._lock = asyncio.Lock()

    # -- setup ------------------------------------------------------------

    def _controller_priv(self) -> X25519PrivateKey:
        if self._priv is None:
            self._priv = _load_or_create_controller_key(self._data_dir)
        return self._priv

    def _get_transport(self) -> Transport:
        if self._transport is None:
            try:
                self._transport = BleakTransport()
            except BluetoothUnavailable as exc:
                raise BluetoothError(str(exc)) from exc
        return self._transport

    # -- session bookkeeping -----------------------------------------------

    def _sweep_expired_locked(self) -> None:
        """Drop sessions past their TTL. Caller must hold ``self._lock``."""
        now = time.time()
        dead = [sid for sid, s in self._sessions.items() if now - s.created_at > SESSION_TTL_S]
        for sid in dead:
            sess = self._sessions.pop(sid)
            asyncio.ensure_future(self._close_session(sess))

    @staticmethod
    async def _close_session(sess: PairSession) -> None:
        try:
            await sess.connection.close()
        except Exception:
            logger.debug("ble pairing: closing session %s failed", sess.session_id, exc_info=True)

    # -- wire helpers -------------------------------------------------------

    @staticmethod
    async def _read_info(conn: Connection) -> dict | None:
        try:
            raw = await conn.read_info()
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    async def _write_pair_message(conn: Connection, payload: bytes, mid: int) -> None:
        for frag in proto.fragment(payload, mid, chunk_size(conn.mtu)):
            await conn.write_pair(frag)

    @staticmethod
    async def _read_pair_message(
        conn: Connection, reassembler: "proto.Reassembler", timeout: float
    ) -> bytes:
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for board reply")
            frag = await conn.wait_pair_notify(remaining)
            msg = reassembler.feed(frag)
            if msg is not None:
                return msg

    # -- public API -----------------------------------------------------

    async def scan(self, seconds: float) -> list[dict]:
        """Scan for pairable boards. Connects to each match to read the
        authoritative ``info`` (the advert name may be truncated)."""
        transport = self._get_transport()
        try:
            adverts: list[Advert] = await transport.scan(seconds)
        except BluetoothUnavailable as exc:
            raise BluetoothError(str(exc)) from exc

        out: list[dict] = []
        for adv in adverts:
            try:
                conn = await asyncio.wait_for(transport.connect(adv.address), timeout=_CONNECT_TIMEOUT_S)
            except Exception:
                # Best-effort: one unreachable board must not fail the whole scan.
                continue
            try:
                info = await self._read_info(conn)
            finally:
                await conn.close()
            if info is None:
                continue
            out.append(
                {
                    "address": adv.address,
                    "name": info.get("name") or adv.name,
                    "board_id": info.get("id", ""),
                    "state": info.get("state", ""),
                    "pairable": bool(info.get("pairable", False)),
                    "rssi": adv.rssi,
                }
            )
        return out

    async def start(self, address: str) -> dict:
        """Connect, verify the board is unpaired + pairable, and run the
        handshake. Returns {"session", "code", "board_id", "name"}."""
        async with self._lock:
            self._sweep_expired_locked()
            if len(self._sessions) >= MAX_SESSIONS:
                raise PairError(429, "too many concurrent pairing sessions")

        transport = self._get_transport()
        try:
            conn = await asyncio.wait_for(transport.connect(address), timeout=_CONNECT_TIMEOUT_S)
        except BluetoothUnavailable as exc:
            raise BluetoothError(str(exc)) from exc
        except Exception as exc:
            raise PairError(504, f"could not connect to board: {exc}") from exc

        try:
            info = await self._read_info(conn)
            if info is None:
                raise PairError(504, "bad or missing info from board")
            board_id = info.get("id")
            name = info.get("name")
            state = info.get("state")
            pairable = bool(info.get("pairable", False))
            if not isinstance(board_id, str) or not board_id:
                raise PairError(504, "bad or missing info from board")
            if state != "unpaired" or not pairable:
                raise PairError(409, "board is not pairable")

            initiator = proto.PairInitiator(controller_identity(), self._controller_priv())
            hello = initiator.start_hello()
            reassembler = proto.Reassembler()
            try:
                await self._write_pair_message(conn, hello, _MID_HELLO)
                reply_raw = await self._read_pair_message(conn, reassembler, _HELLO_TIMEOUT_S)
                code = initiator.on_hello_reply(board_id, reply_raw)
            except PairError:
                raise
            except (asyncio.TimeoutError, TimeoutError) as exc:
                raise PairError(504, f"BLE handshake timed out: {exc}") from exc
            except Exception as exc:
                raise PairError(504, f"BLE handshake failed: {exc}") from exc
        except Exception:
            await conn.close()
            raise

        session_id = secrets.token_hex(8)
        sess = PairSession(
            session_id=session_id,
            address=address,
            board_id=board_id,
            board_name=name or "",
            connection=conn,
            initiator=initiator,
            code=code,
            created_at=time.time(),
            reassembler=reassembler,
        )
        async with self._lock:
            self._sweep_expired_locked()
            if len(self._sessions) >= MAX_SESSIONS:
                await conn.close()
                raise PairError(429, "too many concurrent pairing sessions")
            self._sessions[session_id] = sess
        return {"session": session_id, "code": code, "board_id": board_id, "name": sess.board_name}

    async def confirm(self, session_id: str) -> dict:
        """Mint the node credential, register kind=device, and send the
        sealed provision. Rolls back the credential + registration on any
        board error or transport failure."""
        async with self._lock:
            self._sweep_expired_locked()
            sess = self._sessions.pop(session_id, None)
        if sess is None:
            raise PairError(404, "unknown or expired pairing session")

        # The board's own advertised name (e.g. "taOSusb-7K3Q") is already the
        # node's identity -- reuse it as the registry name rather than
        # re-deriving one, so it matches what the board itself displays.
        name = sess.board_name or f"taOSusb-{sess.board_id}"
        try:
            key = await self._pairing_store.register_device_key(name)
        except Exception as exc:
            await self._close_session(sess)
            raise PairError(500, f"failed to mint node credential: {exc}") from exc

        worker = WorkerInfo(
            name=name,
            url="",
            kind="device",
            platform="taosusb",
            capabilities=["agent"],
            signing_key=key,
        )
        try:
            ok, reason = await self._cluster.register_worker(worker)
            if not ok:
                raise RuntimeError(reason)
        except Exception as exc:
            await self._rollback(name, sess)
            raise PairError(500, f"failed to register node: {exc}") from exc

        provision = {
            "controller_id": controller_identity(),
            "controller_urls": controller_urls(self._bind_port),
            "node_key": key.hex(),
            "wifi": [],
            "mesh_preauth": None,
            "llm": None,
        }
        try:
            sealed = sess.initiator.seal_provision(provision)
            await self._write_pair_message(sess.connection, sealed, _MID_PROVISION)
            reply_raw = await self._read_pair_message(sess.connection, sess.reassembler, _PROVISION_TIMEOUT_S)
            reply = sess.initiator.unseal_reply(reply_raw)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            await self._rollback(name, sess)
            raise PairError(504, f"board did not respond to provision: {exc}") from exc
        except Exception as exc:
            await self._rollback(name, sess)
            raise PairError(502, "board_rejected", why=str(exc))
        finally:
            await self._close_session(sess)

        if not isinstance(reply, dict) or reply.get("t") != "ok":
            why = reply.get("why") if isinstance(reply, dict) else "malformed reply"
            await self._rollback(name, sess)
            raise PairError(502, "board_rejected", why=why)

        return {"name": name, "kind": "device", "board_id": sess.board_id}

    async def cancel(self, session_id: str) -> None:
        """Drop a pairing session. Idempotent -- an unknown/expired id is a no-op."""
        async with self._lock:
            self._sweep_expired_locked()
            sess = self._sessions.pop(session_id, None)
        if sess is not None:
            await self._close_session(sess)

    async def _rollback(self, name: str, sess: PairSession) -> None:
        """Undo a partial pair: unregister the node and dead-letter its key.

        Uses `unregister_worker` + `revoke` (the existing revoke path) rather
        than a bespoke delete, so a rolled-back node leaves no live
        credential and no `get_workers()` entry -- the same state a never-
        registered node would be in.
        """
        try:
            await self._cluster.unregister_worker(name)
        except Exception:
            logger.exception("ble pairing rollback: failed to unregister '%s'", name)
        try:
            await self._pairing_store.revoke(name)
        except Exception:
            logger.exception("ble pairing rollback: failed to revoke key for '%s'", name)
