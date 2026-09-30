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
    # From the advert's taOS manufacturer-data marker (proto.parse_advert_mfr):
    # True/False when the board sent the marker, None when it matched on the
    # service UUID alone (an older board, or a BlueZ that dropped the mfr data).
    paired: bool | None = None


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
        """Scan for ``seconds`` and return taOS board adverts (see :func:`match_advert`)."""

    @abc.abstractmethod
    async def connect(self, address: str) -> Connection:
        """Open a GATT connection to ``address``."""


def chunk_size(mtu: int) -> int:
    """The wire-fragment payload budget for a given ATT MTU.

    MTU minus 3 bytes of ATT overhead minus our own 2-byte (flags, mid)
    header -- see proto.fragment()'s docstring.
    """
    return max(1, mtu - 3 - 2)


def match_advert(address: str, name: str, rssi: int | None,
                 service_uuids, manufacturer_data) -> Advert | None:
    """The advert filter: a taOS board, or None for every other BLE device.

    A board is recognised when EITHER its advert lists ``proto.SERVICE_UUID``
    OR it carries a valid taOS marker under company id ``proto.MFR_ID``. A
    128-bit service UUID does not always fit in the 31-byte legacy advert
    beside the name, so the 6-byte marker is the one a board can always send;
    the UUID stays accepted for a board that sends only that.

    Only ``proto.parse_advert_mfr`` decides what a marker is -- junk, a
    truncated marker, or a marker under another company id is not one.
    """
    uuids = [str(u).lower() for u in (service_uuids or [])]
    mfr = manufacturer_data if isinstance(manufacturer_data, dict) else {}
    marker = proto.parse_advert_mfr(mfr[proto.MFR_ID]) if proto.MFR_ID in mfr else None
    if proto.SERVICE_UUID not in uuids and marker is None:
        return None
    return Advert(
        address=address,
        name=name or "",
        rssi=rssi,
        service_uuids=uuids,
        paired=marker["paired"] if marker is not None else None,
    )


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
            matched = match_advert(
                device.address,
                getattr(adv, "local_name", None) or getattr(device, "name", None) or "",
                getattr(adv, "rssi", None),
                getattr(adv, "service_uuids", None),
                getattr(adv, "manufacturer_data", None),
            )
            if matched is not None:
                out.append(matched)
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
