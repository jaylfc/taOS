"""Tests for tinyagentos/orb/catalog_fetch."""
import base64
import hashlib
import json
import pytest
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tinyagentos.orb import catalog_sig
from tinyagentos.orb.catalog_sig import (
    CatalogFetchError,
    CatalogSerialRegression,
    CatalogSignatureInvalid,
    CatalogSignatureMissing,
)
from tinyagentos.orb.frames import OrbError


@pytest.fixture
def test_key():
    """Generate a test Ed25519 keypair and return (private_key, public_key_raw, key_id)."""
    private_key = Ed25519PrivateKey.generate()
    public_key_raw = private_key.public_key().public_bytes_raw()
    key_id = hashlib.sha256(public_key_raw).hexdigest()[:16]
    return private_key, public_key_raw, key_id


@pytest.fixture
def keys_dict(test_key):
    """Build keys dict mapping key_id to base64-encoded raw public key."""
    _, public_key_raw, key_id = test_key
    return {key_id: base64.b64encode(public_key_raw).decode("ascii")}


def make_sig_json(key_id: str, sig: bytes) -> bytes:
    """Helper to create sig JSON bytes."""
    return json.dumps({"key_id": key_id, "sig": base64.b64encode(sig).decode("ascii")}).encode("utf-8")


def sign_catalog(private_key: Ed25519PrivateKey, catalog_bytes: bytes) -> bytes:
    """Sign catalog bytes and return sig JSON bytes."""
    sig = private_key.sign(catalog_bytes)
    return make_sig_json(hashlib.sha256(private_key.public_key().public_bytes_raw()).hexdigest()[:16], sig)


@pytest.mark.asyncio
async def test_good_fetch_writes_cache_and_returns_catalog(tmp_path, test_key, keys_dict):
    private_key, _, key_id = test_key
    catalog = {"serial": 1, "apps": [{"id": "app1", "name": "App1", "versions": []}]}
    catalog_bytes = json.dumps(catalog).encode("utf-8")
    sig_bytes = sign_catalog(private_key, catalog_bytes)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"

    def handler(request: httpx.Request):
        if request.url.path.endswith("/catalog.json.sig"):
            return httpx.Response(200, content=sig_bytes)
        if request.url.path.endswith("/catalog.json"):
            return httpx.Response(200, content=catalog_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    from tinyagentos.orb import catalog_fetch

    result = await catalog_fetch.fetch_catalog(
        "https://example.com/",
        cache_dir,
        state_path,
        keys=keys_dict,
        transport=transport,
    )
    assert "app1" in result.apps
    # both cache files written byte-identical
    assert (cache_dir / "catalog.json").read_bytes() == catalog_bytes
    assert (cache_dir / "catalog.json.sig").read_bytes() == sig_bytes
    # state serial recorded
    assert state_path.exists()


@pytest.mark.asyncio
async def test_base_url_http_rejected(tmp_path, keys_dict):
    from tinyagentos.orb import catalog_fetch

    called = {"n": 0}

    def handler(request: httpx.Request):
        called["n"] += 1
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    with pytest.raises(CatalogFetchError):
        await catalog_fetch.fetch_catalog("http://example.com", tmp_path / "cache", tmp_path / "state.json", keys=keys_dict, transport=transport)
    with pytest.raises(CatalogFetchError):
        await catalog_fetch.fetch_catalog("", tmp_path / "cache", tmp_path / "state.json", keys=keys_dict, transport=transport)
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_catalog_404_raises(tmp_path, keys_dict):
    from tinyagentos.orb import catalog_fetch

    def handler(request: httpx.Request):
        if request.url.path.endswith("/catalog.json.sig"):
            return httpx.Response(200, content=b'{}')
        if request.url.path.endswith("/catalog.json"):
            return httpx.Response(404)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with pytest.raises(CatalogFetchError):
        await catalog_fetch.fetch_catalog("https://example.com", tmp_path / "cache", tmp_path / "state.json", keys=keys_dict, transport=transport)


@pytest.mark.asyncio
async def test_sig_404_raises_catalog_signature_missing(tmp_path, test_key, keys_dict):
    private_key, _, key_id = test_key
    catalog = {"serial": 1, "apps": []}
    catalog_bytes = json.dumps(catalog).encode("utf-8")

    cache_dir = tmp_path / "cache"

    def handler(request: httpx.Request):
        if request.url.path.endswith("/catalog.json.sig"):
            return httpx.Response(404)
        if request.url.path.endswith("/catalog.json"):
            return httpx.Response(200, content=catalog_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    from tinyagentos.orb import catalog_fetch

    with pytest.raises(CatalogSignatureMissing):
        await catalog_fetch.fetch_catalog("https://example.com", cache_dir, tmp_path / "state.json", keys=keys_dict, transport=transport)
    # no cache written
    assert not (cache_dir / "catalog.json").exists()


@pytest.mark.asyncio
async def test_catalog_too_large_raises(tmp_path, keys_dict):
    from tinyagentos.orb import catalog_fetch

    big = b"x" * (catalog_fetch.CATALOG_MAX_BYTES + 1)

    def handler(request: httpx.Request):
        if request.url.path.endswith("/catalog.json.sig"):
            return httpx.Response(200, content=b'{}')
        if request.url.path.endswith("/catalog.json"):
            return httpx.Response(200, content=big)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with pytest.raises(CatalogFetchError):
        await catalog_fetch.fetch_catalog("https://example.com", tmp_path / "cache", tmp_path / "state.json", keys=keys_dict, transport=transport)


@pytest.mark.asyncio
async def test_sig_too_large_raises(tmp_path, keys_dict):
    from tinyagentos.orb import catalog_fetch

    def handler(request: httpx.Request):
        if request.url.path.endswith("/catalog.json.sig"):
            return httpx.Response(200, content=b"x" * 257)
        if request.url.path.endswith("/catalog.json"):
            return httpx.Response(200, content=b'{"serial": 1, "apps": []}')
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with pytest.raises(CatalogFetchError):
        await catalog_fetch.fetch_catalog("https://example.com", tmp_path / "cache", tmp_path / "state.json", keys=keys_dict, transport=transport)


@pytest.mark.asyncio
async def test_timeout_raises_catalog_fetch_error(tmp_path, keys_dict):
    from tinyagentos.orb import catalog_fetch

    def handler(request: httpx.Request):
        raise httpx.ReadTimeout("timeout")

    transport = httpx.MockTransport(handler)
    with pytest.raises(CatalogFetchError):
        await catalog_fetch.fetch_catalog("https://example.com", tmp_path / "cache", tmp_path / "state.json", keys=keys_dict, transport=transport)


@pytest.mark.asyncio
async def test_status_500_raises(tmp_path, keys_dict):
    from tinyagentos.orb import catalog_fetch

    def handler(request: httpx.Request):
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    with pytest.raises(CatalogFetchError):
        await catalog_fetch.fetch_catalog("https://example.com", tmp_path / "cache", tmp_path / "state.json", keys=keys_dict, transport=transport)


@pytest.mark.asyncio
async def test_status_302_no_redirect_raises(tmp_path, keys_dict):
    from tinyagentos.orb import catalog_fetch

    def handler(request: httpx.Request):
        return httpx.Response(302, headers={"Location": "https://other.com/x"})

    transport = httpx.MockTransport(handler)
    with pytest.raises(CatalogFetchError):
        await catalog_fetch.fetch_catalog("https://example.com", tmp_path / "cache", tmp_path / "state.json", keys=keys_dict, transport=transport)


@pytest.mark.asyncio
async def test_bad_signature_keeps_existing_cache_unchanged(tmp_path, test_key, keys_dict):
    private_key, _, _ = test_key
    from tinyagentos.orb import catalog_fetch

    good_catalog = {"serial": 1, "apps": [{"id": "good"}]}
    good_catalog_bytes = json.dumps(good_catalog).encode("utf-8")
    good_sig = sign_catalog(private_key, good_catalog_bytes)

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "catalog.json").write_bytes(good_catalog_bytes)
    (cache_dir / "catalog.json.sig").write_bytes(good_sig)
    state_path = tmp_path / "state.json"
    from tinyagentos.orb import catalog_sig
    import hashlib
    catalog_sig.record_serial(state_path, 1, hashlib.sha256(good_catalog_bytes).hexdigest())

    bad_catalog = {"serial": 2, "apps": [{"id": "bad"}]}
    bad_catalog_bytes = json.dumps(bad_catalog).encode("utf-8")
    # Sign bad catalog with correct key but sign different bytes? Or create sig over wrong content
    bad_sig = sign_catalog(private_key, bad_catalog_bytes)  # this will verify against bad_catalog_bytes but we serve bad_catalog_bytes? Wait - sign_catalog signs the bytes passed to it
    # But we need sig that doesn't verify for the catalog bytes being served
    # Or easier: sign good catalog then corrupt sig
    bad_sig = bytearray(good_sig)
    bad_sig[-1] ^= 0x01
    bad_sig = bytes(bad_sig)

    def handler(request: httpx.Request):
        if request.url.path.endswith("/catalog.json.sig"):
            return httpx.Response(200, content=bad_sig)
        if request.url.path.endswith("/catalog.json"):
            return httpx.Response(200, content=bad_catalog_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with pytest.raises(CatalogSignatureInvalid):
        await catalog_fetch.fetch_catalog("https://example.com", cache_dir, state_path, keys=keys_dict, transport=transport)
    assert (cache_dir / "catalog.json").read_bytes() == good_catalog_bytes
    assert (cache_dir / "catalog.json.sig").read_bytes() == good_sig


def test_load_cached_catalog_after_good_fetch(tmp_path, test_key, keys_dict):
    private_key, _, _ = test_key
    catalog = {"serial": 5, "apps": [{"id": "app1"}]}
    catalog_bytes = json.dumps(catalog).encode("utf-8")
    sig_bytes = sign_catalog(private_key, catalog_bytes)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"
    cache_dir.mkdir(parents=True)
    (cache_dir / "catalog.json").write_bytes(catalog_bytes)
    (cache_dir / "catalog.json.sig").write_bytes(sig_bytes)
    from tinyagentos.orb import catalog_sig
    import hashlib
    catalog_sig.record_serial(state_path, 5, hashlib.sha256(catalog_bytes).hexdigest())

    from tinyagentos.orb import catalog_fetch
    result = catalog_fetch.load_cached_catalog(cache_dir, state_path, keys=keys_dict)
    assert "app1" in result.apps


def test_load_cached_catalog_tamper_catalog_invalid(tmp_path, test_key, keys_dict):
    private_key, _, _ = test_key
    catalog = {"serial": 6, "apps": [{"id": "app1"}]}
    catalog_bytes = json.dumps(catalog).encode("utf-8")
    sig_bytes = sign_catalog(private_key, catalog_bytes)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"
    cache_dir.mkdir(parents=True)
    (cache_dir / "catalog.json").write_bytes(catalog_bytes)
    (cache_dir / "catalog.json.sig").write_bytes(sig_bytes)
    from tinyagentos.orb import catalog_sig
    import hashlib
    catalog_sig.record_serial(state_path, 6, hashlib.sha256(catalog_bytes).hexdigest())

    tampered = bytearray(catalog_bytes)
    tampered[0] ^= 0xFF
    (cache_dir / "catalog.json").write_bytes(bytes(tampered))

    from tinyagentos.orb import catalog_fetch
    with pytest.raises(CatalogSignatureInvalid):
        catalog_fetch.load_cached_catalog(cache_dir, state_path, keys=keys_dict)


def test_load_cached_catalog_missing_sig_raises(tmp_path, test_key, keys_dict):
    private_key, _, _ = test_key
    catalog = {"serial": 7, "apps": []}
    catalog_bytes = json.dumps(catalog).encode("utf-8")

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"
    cache_dir.mkdir(parents=True)
    (cache_dir / "catalog.json").write_bytes(catalog_bytes)

    from tinyagentos.orb import catalog_fetch
    with pytest.raises(CatalogSignatureMissing):
        catalog_fetch.load_cached_catalog(cache_dir, state_path, keys=keys_dict)


def test_load_cached_catalog_empty_dir_raises(tmp_path, keys_dict):
    from tinyagentos.orb import catalog_fetch
    with pytest.raises(CatalogFetchError):
        catalog_fetch.load_cached_catalog(tmp_path / "empty", tmp_path / "state.json", keys=keys_dict)


def test_load_cached_catalog_serial_regression(tmp_path, test_key, keys_dict):
    private_key, _, _ = test_key
    catalog = {"serial": 1, "apps": []}
    catalog_bytes = json.dumps(catalog).encode("utf-8")
    sig_bytes = sign_catalog(private_key, catalog_bytes)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"
    cache_dir.mkdir(parents=True)
    (cache_dir / "catalog.json").write_bytes(catalog_bytes)
    (cache_dir / "catalog.json.sig").write_bytes(sig_bytes)
    from tinyagentos.orb import catalog_sig
    import hashlib
    catalog_sig.record_serial(state_path, 5, hashlib.sha256(b"different").hexdigest())

    from tinyagentos.orb import catalog_fetch
    with pytest.raises(CatalogSerialRegression):
        catalog_fetch.load_cached_catalog(cache_dir, state_path, keys=keys_dict)


@pytest.mark.asyncio
async def test_slow_drip_catalog_bounded_by_total_timeout(tmp_path, keys_dict):
    """A server that drips one byte per read never trips httpx's per-read timeout
    or the size cap; the whole fetch must still end within `timeout`."""
    import asyncio

    from tinyagentos.orb import catalog_fetch

    class _Drip(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.01)
                yield b" "

    def handler(request: httpx.Request):
        return httpx.Response(200, stream=_Drip())

    transport = httpx.MockTransport(handler)
    with pytest.raises(CatalogFetchError):
        await asyncio.wait_for(
            catalog_fetch.fetch_catalog(
                "https://example.com/", tmp_path / "cache", tmp_path / "state.json",
                keys=keys_dict, timeout=0.3, transport=transport,
            ),
            timeout=5,
        )
    assert not (tmp_path / "cache" / "catalog.json").exists()


@pytest.mark.asyncio
async def test_fetch_sends_accept_encoding_identity(tmp_path, test_key, keys_dict):
    """Both GETs send Accept-Encoding: identity so compressed responses
    cannot defeat the size cap."""
    private_key, _, _ = test_key
    catalog = {"serial": 1, "apps": [{"id": "app1", "name": "App1", "versions": []}]}
    catalog_bytes = json.dumps(catalog).encode("utf-8")
    sig_bytes = sign_catalog(private_key, catalog_bytes)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"

    seen_accept_encoding = []

    def handler(request: httpx.Request):
        seen_accept_encoding.append(request.headers.get("accept-encoding"))
        if request.url.path.endswith("/catalog.json.sig"):
            return httpx.Response(200, content=sig_bytes)
        if request.url.path.endswith("/catalog.json"):
            return httpx.Response(200, content=catalog_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    from tinyagentos.orb import catalog_fetch

    result = await catalog_fetch.fetch_catalog(
        "https://example.com/",
        cache_dir,
        state_path,
        keys=keys_dict,
        transport=transport,
    )
    assert "app1" in result.apps
    assert len(seen_accept_encoding) == 2
    assert all(enc == "identity" for enc in seen_accept_encoding)


@pytest.mark.asyncio
async def test_compressed_response_rejected_before_reading_body(tmp_path, test_key, keys_dict):
    """A server that ignores Accept-Encoding: identity and returns gzip content
    is rejected before the body is read, so no cache file is written."""
    private_key, _, _ = test_key
    catalog = {"serial": 1, "apps": [{"id": "app1", "name": "App1", "versions": []}]}
    catalog_bytes = json.dumps(catalog).encode("utf-8")
    sig_bytes = sign_catalog(private_key, catalog_bytes)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"

    def handler(request: httpx.Request):
        if request.url.path.endswith("/catalog.json"):
            return httpx.Response(200, content=catalog_bytes,
                                  headers={"Content-Encoding": "gzip"})
        if request.url.path.endswith("/catalog.json.sig"):
            return httpx.Response(200, content=sig_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    from tinyagentos.orb import catalog_fetch

    with pytest.raises(CatalogFetchError):
        await catalog_fetch.fetch_catalog(
            "https://example.com/",
            cache_dir,
            state_path,
            keys=keys_dict,
            transport=transport,
        )
    assert not (cache_dir / "catalog.json").exists()
    assert not (cache_dir / "catalog.json.sig").exists()


@pytest.mark.asyncio
async def test_fetch_catalog_file_io_runs_in_thread(tmp_path, test_key, keys_dict, monkeypatch):
    """accept_catalog and the cache writes run off the event loop via
    asyncio.to_thread."""
    import threading
    from tinyagentos import atomic_io
    from tinyagentos.orb import catalog_fetch

    private_key, _, _ = test_key
    catalog = {"serial": 1, "apps": [{"id": "app1", "name": "App1", "versions": []}]}
    catalog_bytes = json.dumps(catalog).encode("utf-8")
    sig_bytes = sign_catalog(private_key, catalog_bytes)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"

    def handler(request: httpx.Request):
        if request.url.path.endswith("/catalog.json.sig"):
            return httpx.Response(200, content=sig_bytes)
        if request.url.path.endswith("/catalog.json"):
            return httpx.Response(200, content=catalog_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    call_thread_ids = []
    original_write = atomic_io.atomic_write_bytes

    def recording_write_bytes(path, data, **kwargs):
        call_thread_ids.append(threading.get_ident())
        return original_write(path, data, **kwargs)

    monkeypatch.setattr("tinyagentos.atomic_io.atomic_write_bytes", recording_write_bytes)

    result = await catalog_fetch.fetch_catalog(
        "https://example.com/",
        cache_dir,
        state_path,
        keys=keys_dict,
        transport=transport,
    )
    assert "app1" in result.apps
    test_thread_id = threading.get_ident()
    assert len(call_thread_ids) == 3
    assert all(tid != test_thread_id for tid in call_thread_ids)


def test_load_cached_catalog_oversize_rejected(tmp_path, keys_dict, monkeypatch):
    """A cached catalog exceeding the cap raises CatalogFetchError without
    using Path.read_bytes (which is patched to fail)."""
    from tinyagentos.orb import catalog_fetch

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "catalog.json").write_bytes(b"x" * (catalog_fetch.CATALOG_MAX_BYTES + 10))
    state_path = tmp_path / "state.json"

    def raise_on_read_bytes(self):
        raise AssertionError("Path.read_bytes must not be used")

    monkeypatch.setattr(Path, "read_bytes", raise_on_read_bytes)

    with pytest.raises(CatalogFetchError, match="cached catalog exceeds cap"):
        catalog_fetch.load_cached_catalog(cache_dir, state_path, keys=keys_dict)


@pytest.mark.asyncio
async def test_fetch_catalog_desktop_kind_rejected(tmp_path, test_key, keys_dict):
    """A signed desktop-apps catalog must be rejected before serial is recorded."""
    private_key, _, key_id = test_key
    catalog = {"serial": 5, "kind": "orb-desktop-apps", "apps": []}
    catalog_bytes = json.dumps(catalog).encode("utf-8")
    sig_bytes = sign_catalog(private_key, catalog_bytes)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"

    def handler(request: httpx.Request):
        if request.url.path.endswith("/catalog.json.sig"):
            return httpx.Response(200, content=sig_bytes)
        if request.url.path.endswith("/catalog.json"):
            return httpx.Response(200, content=catalog_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    from tinyagentos.orb import catalog_fetch

    with pytest.raises(OrbError) as exc:
        await catalog_fetch.fetch_catalog(
            "https://example.com/", cache_dir, state_path, keys=keys_dict, transport=transport
        )
    assert "kind" in str(exc.value).lower()
    assert "orb-desktop-apps" in str(exc.value)
    # state file must not be created
    assert not state_path.exists()
    # cache must not be written
    assert not (cache_dir / "catalog.json").exists()


@pytest.mark.asyncio
async def test_fetch_catalog_firmware_kind_accepted(tmp_path, test_key, keys_dict):
    """A catalog with kind orb-firmware is accepted like one with no kind."""
    private_key, _, key_id = test_key
    catalog = {"serial": 5, "kind": "orb-firmware", "apps": [{"id": "app1", "name": "App1", "versions": []}]}
    catalog_bytes = json.dumps(catalog).encode("utf-8")
    sig_bytes = sign_catalog(private_key, catalog_bytes)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"

    def handler(request: httpx.Request):
        if request.url.path.endswith("/catalog.json.sig"):
            return httpx.Response(200, content=sig_bytes)
        if request.url.path.endswith("/catalog.json"):
            return httpx.Response(200, content=catalog_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    from tinyagentos.orb import catalog_fetch

    result = await catalog_fetch.fetch_catalog(
        "https://example.com/", cache_dir, state_path, keys=keys_dict, transport=transport
    )
    assert "app1" in result.apps
    assert state_path.exists()
    assert (cache_dir / "catalog.json").read_bytes() == catalog_bytes


@pytest.mark.asyncio
async def test_fetch_catalog_no_kind_accepted(tmp_path, test_key, keys_dict):
    """A catalog with no kind field is accepted (backward compatible)."""
    private_key, _, key_id = test_key
    catalog = {"serial": 5, "apps": [{"id": "app1", "name": "App1", "versions": []}]}
    catalog_bytes = json.dumps(catalog).encode("utf-8")
    sig_bytes = sign_catalog(private_key, catalog_bytes)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"

    def handler(request: httpx.Request):
        if request.url.path.endswith("/catalog.json.sig"):
            return httpx.Response(200, content=sig_bytes)
        if request.url.path.endswith("/catalog.json"):
            return httpx.Response(200, content=catalog_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    from tinyagentos.orb import catalog_fetch

    result = await catalog_fetch.fetch_catalog(
        "https://example.com/", cache_dir, state_path, keys=keys_dict, transport=transport
    )
    assert "app1" in result.apps
    assert state_path.exists()
    assert (cache_dir / "catalog.json").read_bytes() == catalog_bytes


def test_load_cached_catalog_desktop_kind_rejected(tmp_path, test_key, keys_dict):
    """A cached desktop-kind catalog must be rejected, state unchanged."""
    private_key, _, _ = test_key
    catalog = {"serial": 8, "kind": "orb-desktop-apps", "apps": [{"id": "app1"}]}
    catalog_bytes = json.dumps(catalog).encode("utf-8")
    sig_bytes = sign_catalog(private_key, catalog_bytes)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"
    cache_dir.mkdir(parents=True)
    (cache_dir / "catalog.json").write_bytes(catalog_bytes)
    (cache_dir / "catalog.json.sig").write_bytes(sig_bytes)
    from tinyagentos.orb import catalog_sig
    import hashlib
    catalog_sig.record_serial(state_path, 8, hashlib.sha256(catalog_bytes).hexdigest())

    from tinyagentos.orb import catalog_fetch
    with pytest.raises(OrbError) as exc:
        catalog_fetch.load_cached_catalog(cache_dir, state_path, keys=keys_dict)
    assert "kind" in str(exc.value).lower()
    assert "orb-desktop-apps" in str(exc.value)
    # state must be unchanged (serial still 8)
    state_text = state_path.read_text()
    assert '"serial": 8' in state_text
