"""DNS-rebinding regression for the shared SSRF guard.

The guard used to resolve a hostname, validate every returned address and
then hand the caller nothing but permission. The caller's HTTP client then
performed its *own* lookup, so an attacker running the authoritative
nameserver could answer public to the check and loopback to the connection.
Blocking `http://127.0.0.1/` proves nothing about that bug -- it is already
refused at validation time. The bug only shows up when the two lookups
disagree, so that is what these tests script.

`_ScriptedResolver` stands in for `socket.getaddrinfo` and answers a
different address on each call for the same hostname. The connection layer
is intercepted at `httpcore`'s network backend -- the exact place a real
connection resolves the name and opens the socket -- so the second lookup is
made for real and the socket really is opened, against a local stand-in for
whichever service that lookup pointed at.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpcore._backends.anyio import AnyIOBackend

from tinyagentos.library_pipeline import WebProcessor
from tinyagentos.library_store import LibraryStore
from tinyagentos.routes.desktop_browser.ssrf import SsrfBlockedError

# example.com — a plain public address, accepted by the guard.
_PUBLIC = "93.184.216.34"
_INTERNAL = "127.0.0.1"

_EXTERNAL_PAGE = (
    b"<html><head><title>Public page</title></head><body><article>"
    b"<p>This is the public page the user actually asked for. It carries "
    b"enough prose that the readability extractor keeps it instead of "
    b"falling back to the bare tag stripper for very short documents.</p>"
    b"</article></body></html>"
)
_INTERNAL_PAGE = (
    b"<html><head><title>Admin</title></head><body><article>"
    b"<p>INTERNAL-SERVICE-SECRET: this response only exists on the loopback "
    b"interface and must never be reachable through a user-supplied URL, "
    b"however the attacker's nameserver answers the second lookup.</p>"
    b"</article></body></html>"
)


class _ScriptedResolver:
    """A `socket.getaddrinfo` stand-in that answers a low-TTL nameserver.

    `answers` maps a hostname to the addresses it hands out, one per call;
    the last entry repeats once the script runs out. IP literals resolve to
    themselves, as the real resolver does.
    """

    def __init__(self, answers: dict[str, list[str]]) -> None:
        self._answers = answers
        self.calls: dict[str, int] = {}

    def __call__(self, host, port=0, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else str(host)
        try:
            ipaddress.ip_address(name)
        except ValueError:
            script = self._answers.get(name.lower())
            if script is None:
                raise socket.gaierror(
                    socket.EAI_NONAME, f"scripted resolver has no answer for {name!r}"
                )
            index = min(self.calls.get(name.lower(), 0), len(script) - 1)
            self.calls[name.lower()] = index + 1
            addr = script[index]
        else:
            addr = name

        sock_port = port if isinstance(port, int) else 0
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (addr, sock_port),
            )
        ]


async def _start_page_server(body: bytes) -> tuple[asyncio.AbstractServer, int]:
    """Serve `body` once per connection over minimal HTTP/1.1 on loopback."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/html; charset=utf-8\r\n"
                b"Content-Length: %d\r\n"
                b"Connection: close\r\n\r\n" % len(body)
                + body
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


class _ConnectionRecorder:
    """Intercepts the connection so the address it lands on is observable.

    Stands where `httpcore` opens the socket: it performs the connection's
    own hostname lookup (the second lookup) exactly as the real backend
    does, records the address that lookup produced, and then opens a real
    socket to the local stand-in for that address -- the internal service
    for a loopback answer, the public site otherwise.
    """

    def __init__(self, *, internal_port: int, external_port: int) -> None:
        self.internal_port = internal_port
        self.external_port = external_port
        self.connected: list[str] = []
        self._real_connect_tcp = AnyIOBackend.connect_tcp

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        addr = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0][4][0]
        self.connected.append(addr)
        stand_in_port = (
            self.internal_port
            if ipaddress.ip_address(addr).is_loopback
            else self.external_port
        )
        return await self._real_connect_tcp(
            AnyIOBackend(), "127.0.0.1", stand_in_port, timeout=timeout,
        )

    def patched(self):
        return patch.object(AnyIOBackend, "connect_tcp", self.connect_tcp)


@pytest_asyncio.fixture
async def lib_store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    store = LibraryStore(db_path)
    await store.init()
    yield store
    await store.close()
    db_path.unlink(missing_ok=True)


@pytest_asyncio.fixture
async def page_servers():
    """Loopback stand-ins for the internal service and the public site."""
    internal, internal_port = await _start_page_server(_INTERNAL_PAGE)
    external, external_port = await _start_page_server(_EXTERNAL_PAGE)
    recorder = _ConnectionRecorder(
        internal_port=internal_port, external_port=external_port,
    )
    yield recorder
    for server in (internal, external):
        server.close()
        await server.wait_closed()


@pytest.fixture
def storage_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


async def _fetch_through_web_processor(
    store: LibraryStore, storage_dir: Path, url: str,
) -> list[dict]:
    """Drive a real `validate_url_or_raise` caller end to end."""
    item_id = await store.create_item(kind="url:web", source_url=url, title="")
    item = await store.get_item(item_id)
    return await WebProcessor(store, storage_dir).process(item)


@pytest.mark.asyncio
async def test_second_lookup_to_loopback_is_refused(lib_store, storage_dir, page_servers):
    """A nameserver that answers public, then loopback, must not be followed."""
    resolver = _ScriptedResolver({"rebind.test": [_PUBLIC, _INTERNAL]})

    blocked: SsrfBlockedError | None = None
    with patch("socket.getaddrinfo", resolver), page_servers.patched():
        try:
            await _fetch_through_web_processor(
                lib_store, storage_dir, "http://rebind.test/page",
            )
        except SsrfBlockedError as e:
            blocked = e

    reached = page_servers.connected[-1] if page_servers.connected else "(no connection)"
    assert blocked is not None, (
        f"expected SsrfBlockedError, but the fetch reached {reached}"
    )
    assert _INTERNAL not in page_servers.connected, (
        f"the connection was opened against {page_servers.connected}"
    )


@pytest.mark.asyncio
async def test_agreeing_lookups_still_fetch(lib_store, storage_dir, page_servers):
    """Control: two lookups agreeing on a public address must still fetch."""
    resolver = _ScriptedResolver({"stable.test": [_PUBLIC, _PUBLIC]})

    with patch("socket.getaddrinfo", resolver), page_servers.patched():
        artifacts = await _fetch_through_web_processor(
            lib_store, storage_dir, "http://stable.test/page",
        )

    assert page_servers.connected == [_PUBLIC], page_servers.connected
    text_artifacts = [a for a in artifacts if a["kind"] == "text"]
    assert len(text_artifacts) == 1
    body = Path(text_artifacts[0]["path"]).read_text(encoding="utf-8")
    assert "public page" in body
    assert "INTERNAL-SERVICE-SECRET" not in body

