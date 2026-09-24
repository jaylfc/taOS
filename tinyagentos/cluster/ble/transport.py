"""BLE transport for the taOSusb S1 controller side (see docs/taosusb-pairing-plan.md).

``pairing.py`` drives the handshake in terms of this small interface so it
never imports a BLE library directly. ``bleak`` is optional (see
pyproject.toml's ``ble`` extra) -- a controller with no Bluetooth need is not
forced to install it, and ``BleakTransport`` raises :class:`BluetoothUnavailable`
(mapped to HTTP 503 ``bluetooth_unavailable`` by the routes) when bleak or an
adapter is missing, rather than failing the whole app at import time.
"""
from __future__ import annotations

import abc
import asyncio
from dataclasses import dataclass

from tinyagentos.cluster.ble import proto


class BluetoothUnavailable(Exception):
    """bleak is not installed, or no usable Bluetooth adapter is present."""


@dataclass(frozen=True)
class Advert:
    """One scan result. ``name`` may be a truncated advertisement name --
    callers should prefer the ``info`` characteristic for the authoritative
    name/board id/state once connected."""

    address: str
    name: str
    rssi: int | None
    service_uuids: list[str]


class Connection(abc.ABC):
    """One open GATT connection to a board.

    ``mtu`` is the negotiated ATT MTU; callers derive the fragment chunk
    size from it via :func:`chunk_size` and ``proto.fragment()``.
    """

    mtu: int

    @abc.abstractmethod
    async def read_info(self) -> bytes:
        """Read the ``info`` characteristic (see proto.info_frame)."""

    @abc.abstractmethod
    async def write_pair(self, fragment: bytes) -> None:
        """Write one wire fragment to the ``pair`` characteristic."""

    @abc.abstractmethod
    async def wait_pair_notify(self, timeout: float) -> bytes:
        """Return the next ``pair`` notification fragment.

        Raises ``asyncio.TimeoutError`` if none arrives within ``timeout``
        seconds.
        """

    @abc.abstractmethod
    async def close(self) -> None:
        """Release the connection. Must be safe to call more than once."""


class Transport(abc.ABC):
    """A BLE central capable of scanning for and connecting to taOSusb boards."""

    @abc.abstractmethod
    async def scan(self, seconds: float) -> list[Advert]:
        """Scan for ``seconds`` and return adverts matching ``proto.SERVICE_UUID``."""

    @abc.abstractmethod
    async def connect(self, address: str) -> Connection:
        """Open a GATT connection to ``address``."""


def chunk_size(mtu: int) -> int:
    """The wire-fragment payload budget for a given ATT MTU.

    MTU minus 3 bytes of ATT overhead minus our own 2-byte (flags, mid)
    header -- see proto.fragment()'s docstring.
    """
    return max(1, mtu - 3 - 2)


class BleakTransport(Transport):
    """``bleak``-backed :class:`Transport`.

    Imports ``bleak`` only when instantiated, so a controller process that
    never touches BLE pairing never needs the package installed.
    """

    def __init__(self) -> None:
        try:
            import bleak  # noqa: F401
        except ImportError as exc:
            raise BluetoothUnavailable("bleak is not installed") from exc

    async def scan(self, seconds: float) -> list[Advert]:
        try:
            from bleak import BleakScanner
        except ImportError as exc:
            raise BluetoothUnavailable("bleak is not installed") from exc
        try:
            found = await BleakScanner.discover(timeout=seconds, return_adv=True)
        except Exception as exc:  # bleak raises BleakError/OSError when no adapter
            raise BluetoothUnavailable(str(exc)) from exc
        out: list[Advert] = []
        for device, adv in found.values():
            uuids = [u.lower() for u in (getattr(adv, "service_uuids", None) or [])]
            if proto.SERVICE_UUID not in uuids:
                continue
            out.append(
                Advert(
                    address=device.address,
                    name=getattr(adv, "local_name", None) or getattr(device, "name", None) or "",
                    rssi=getattr(adv, "rssi", None),
                    service_uuids=uuids,
                )
            )
        return out

    async def connect(self, address: str) -> Connection:
        try:
            from bleak import BleakClient
        except ImportError as exc:
            raise BluetoothUnavailable("bleak is not installed") from exc
        client = BleakClient(address)
        try:
            await client.connect()
        except Exception as exc:
            raise BluetoothUnavailable(str(exc)) from exc
        return _BleakConnection(client)


class _BleakConnection(Connection):
    def __init__(self, client) -> None:
        self._client = client
        self.mtu = int(getattr(client, "mtu_size", 23) or 23)
        self._notify_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._notifying = False

    async def _ensure_notify(self) -> None:
        if self._notifying:
            return

        def _cb(_handle, data: bytearray) -> None:
            self._notify_queue.put_nowait(bytes(data))

        await self._client.start_notify(proto.CHAR_PAIR_UUID, _cb)
        self._notifying = True

    async def read_info(self) -> bytes:
        data = await self._client.read_gatt_char(proto.CHAR_INFO_UUID)
        return bytes(data)

    async def write_pair(self, fragment: bytes) -> None:
        await self._ensure_notify()
        await self._client.write_gatt_char(proto.CHAR_PAIR_UUID, fragment, response=True)

    async def wait_pair_notify(self, timeout: float) -> bytes:
        await self._ensure_notify()
        return await asyncio.wait_for(self._notify_queue.get(), timeout)

    async def close(self) -> None:
        try:
            if self._notifying:
                await self._client.stop_notify(proto.CHAR_PAIR_UUID)
        except Exception:
            pass
        try:
            await self._client.disconnect()
        except Exception:
            pass
