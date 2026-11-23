"""Tests for tinyagentos/orb/apps_store fetch/download."""
import asyncio
import base64
import hashlib
import json
import os
import pytest
import threading
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tinyagentos.orb import apps_store
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
    private_key = Ed25519PrivateKey.generate()
    public_key_raw = private_key.public_key().public_bytes_raw()
    key_id = hashlib.sha256(public_key_raw).hexdigest()[:16]
    return private_key, public_key_raw, key_id


@pytest.fixture
def keys_dict(test_key):
    _, public_key_raw, key_id = test_key
    return {key_id: base64.b64encode(public_key_raw).decode("ascii")}


def make_sig_json(key_id: str, sig: bytes) -> bytes:
    return json.dumps({"key_id": key_id, "sig": base64.b64encode(sig).decode("ascii")}).encode("utf-8")


def sign_manifest(private_key: Ed25519PrivateKey, manifest_bytes: bytes) -> bytes:
    sig = private_key.sign(manifest_bytes)
    return make_sig_json(hashlib.sha256(private_key.public_key().public_bytes_raw()).hexdigest()[:16], sig)


def make_valid_manifest():
    return {
        "kind": apps_store.MANIFEST_KIND,
        "serial": 1,
        "apps": [{
            "id": "openharness-taos",
            "versions": [
                {
                    "version": "2.0.0",
                    "yanked": False,
                    "files": {
                        "linux-x64": {"url": "openharness/2.0.0/oh-linux-x64.zip", "sha256": "b" * 64, "size": 5},
                    }
                },
            ]
        }]
    }


@pytest.mark.asyncio
async def test_good_signed_manifest_returned_cache_written_state_has_serial(tmp_path, test_key, keys_dict):
    private_key, _, key_id = test_key
    manifest = make_valid_manifest()
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    sig_bytes = sign_manifest(private_key, manifest_bytes)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"

    def handler(request: httpx.Request):
        if request.url.path.endswith("/desktop.json.sig"):
            return httpx.Response(200, content=sig_bytes)
        if request.url.path.endswith("/desktop.json"):
            return httpx.Response(200, content=manifest_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    result = await apps_store.fetch_manifest(
        "https://example.com/",
        cache_dir,
        state_path,
        keys=keys_dict,
        transport=transport,
    )
    assert result == manifest
    assert (cache_dir / "desktop.json").read_bytes() == manifest_bytes
    assert (cache_dir / "desktop.json.sig").read_bytes() == sig_bytes
    assert state_path.exists()


@pytest.mark.asyncio
async def test_firmware_shaped_catalog_served_as_desktop_refused_kind_error_and_no_state(tmp_path, test_key, keys_dict):
    private_key, _, key_id = test_key
    catalog = {"serial": 1, "apps": [{"id": "app1", "name": "App1", "versions": []}]}
    catalog_bytes = json.dumps(catalog).encode("utf-8")
    sig_bytes = sign_manifest(private_key, catalog_bytes)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"

    def handler(request: httpx.Request):
        if request.url.path.endswith("/desktop.json.sig"):
            return httpx.Response(200, content=sig_bytes)
        if request.url.path.endswith("/desktop.json"):
            return httpx.Response(200, content=catalog_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    from tinyagentos.orb.apps_store import DesktopManifestKindError
    with pytest.raises(DesktopManifestKindError):
        await apps_store.fetch_manifest(
            "https://example.com/",
            cache_dir,
            state_path,
            keys=keys_dict,
            transport=transport,
        )
    assert not state_path.exists()


@pytest.mark.asyncio
async def test_signed_manifest_with_bad_file_entry_desktop_manifest_invalid_state_not_written(tmp_path, test_key, keys_dict):
    private_key, _, key_id = test_key
    manifest = make_valid_manifest()
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["url"] = "../bad"
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    sig_bytes = sign_manifest(private_key, manifest_bytes)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"

    def handler(request: httpx.Request):
        if request.url.path.endswith("/desktop.json.sig"):
            return httpx.Response(200, content=sig_bytes)
        if request.url.path.endswith("/desktop.json"):
            return httpx.Response(200, content=manifest_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    from tinyagentos.orb.apps_store import DesktopManifestInvalid
    with pytest.raises(DesktopManifestInvalid):
        await apps_store.fetch_manifest(
            "https://example.com/",
            cache_dir,
            state_path,
            keys=keys_dict,
            transport=transport,
        )
    assert not state_path.exists()


@pytest.mark.asyncio
async def test_missing_sig_raises_catalog_signature_missing(tmp_path, test_key, keys_dict):
    private_key, _, key_id = test_key
    manifest = make_valid_manifest()
    manifest_bytes = json.dumps(manifest).encode("utf-8")

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"

    def handler(request: httpx.Request):
        if request.url.path.endswith("/desktop.json.sig"):
            return httpx.Response(404)
        if request.url.path.endswith("/desktop.json"):
            return httpx.Response(200, content=manifest_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with pytest.raises(CatalogSignatureMissing):
        await apps_store.fetch_manifest(
            "https://example.com/",
            cache_dir,
            state_path,
            keys=keys_dict,
            transport=transport,
        )


@pytest.mark.asyncio
async def test_bad_sig_raises_catalog_signature_invalid(tmp_path, test_key, keys_dict):
    private_key, _, key_id = test_key
    manifest = make_valid_manifest()
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    bad_sig = make_sig_json(key_id, b"x" * 64)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"

    def handler(request: httpx.Request):
        if request.url.path.endswith("/desktop.json.sig"):
            return httpx.Response(200, content=bad_sig)
        if request.url.path.endswith("/desktop.json"):
            return httpx.Response(200, content=manifest_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with pytest.raises(CatalogSignatureInvalid):
        await apps_store.fetch_manifest(
            "https://example.com/",
            cache_dir,
            state_path,
            keys=keys_dict,
            transport=transport,
        )


@pytest.mark.asyncio
async def test_serial_lower_than_state_raises_catalog_serial_regression(tmp_path, test_key, keys_dict):
    private_key, _, key_id = test_key
    # first fetch with serial 5
    manifest = make_valid_manifest()
    manifest["serial"] = 5
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    sig_bytes = sign_manifest(private_key, manifest_bytes)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"

    def handler1(request: httpx.Request):
        if request.url.path.endswith("/desktop.json.sig"):
            return httpx.Response(200, content=sig_bytes)
        if request.url.path.endswith("/desktop.json"):
            return httpx.Response(200, content=manifest_bytes)
        return httpx.Response(404)

    transport1 = httpx.MockTransport(handler1)
    await apps_store.fetch_manifest("https://example.com/", cache_dir, state_path, keys=keys_dict, transport=transport1)

    # second fetch with lower serial
    manifest2 = make_valid_manifest()
    manifest2["serial"] = 3
    manifest_bytes2 = json.dumps(manifest2).encode("utf-8")
    sig_bytes2 = sign_manifest(private_key, manifest_bytes2)

    def handler2(request: httpx.Request):
        if request.url.path.endswith("/desktop.json.sig"):
            return httpx.Response(200, content=sig_bytes2)
        if request.url.path.endswith("/desktop.json"):
            return httpx.Response(200, content=manifest_bytes2)
        return httpx.Response(404)

    transport2 = httpx.MockTransport(handler2)
    with pytest.raises(CatalogSerialRegression):
        await apps_store.fetch_manifest("https://example.com/", cache_dir, state_path, keys=keys_dict, transport=transport2)


@pytest.mark.asyncio
async def test_http_base_url_rejected(tmp_path, keys_dict):
    called = {"n": 0}

    def handler(request: httpx.Request):
        called["n"] += 1
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    with pytest.raises(CatalogFetchError):
        await apps_store.fetch_manifest("http://example.com", tmp_path / "cache", tmp_path / "state.json", keys=keys_dict, transport=transport)
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_manifest_302_rejected(tmp_path, test_key, keys_dict):
    private_key, _, key_id = test_key
    manifest = make_valid_manifest()
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    sig_bytes = sign_manifest(private_key, manifest_bytes)

    def handler(request: httpx.Request):
        if request.url.path.endswith("/desktop.json.sig"):
            return httpx.Response(200, content=sig_bytes)
        if request.url.path.endswith("/desktop.json"):
            return httpx.Response(302, headers={"Location": "https://other.com/x"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with pytest.raises(CatalogFetchError):
        await apps_store.fetch_manifest("https://example.com", tmp_path / "cache", tmp_path / "state.json", keys=keys_dict, transport=transport)


@pytest.mark.asyncio
async def test_load_cached_manifest_round_trip_then_tampered_byte_raises_catalog_signature_invalid(tmp_path, test_key, keys_dict):
    private_key, _, key_id = test_key
    manifest = make_valid_manifest()
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    sig_bytes = sign_manifest(private_key, manifest_bytes)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"

    cache_dir.mkdir(parents=True)
    (cache_dir / "desktop.json").write_bytes(manifest_bytes)
    (cache_dir / "desktop.json.sig").write_bytes(sig_bytes)

    result = apps_store.load_cached_manifest(cache_dir, state_path, keys=keys_dict)
    assert result["kind"] == apps_store.MANIFEST_KIND

    # tamper
    bad = bytearray(manifest_bytes)
    bad[0] ^= 0xFF
    (cache_dir / "desktop.json").write_bytes(bytes(bad))
    with pytest.raises(CatalogSignatureInvalid):
        apps_store.load_cached_manifest(cache_dir, state_path, keys=keys_dict)


@pytest.mark.asyncio
async def test_download_good_bytes_file_at_dest_no_dot_dl_left(tmp_path, test_key, keys_dict):
    manifest = make_valid_manifest()
    data = b"hello"
    h = hashlib.sha256(data).hexdigest()
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["sha256"] = h
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["size"] = len(data)

    cache_dir = tmp_path / "cache"
    dest_dir = tmp_path / "downloads"

    def handler(request: httpx.Request):
        if request.url.path.endswith("/openharness/2.0.0/oh-linux-x64.zip"):
            return httpx.Response(200, content=data)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    result = await apps_store.download_app(manifest, "openharness-taos", "https://example.com/", dest_dir, transport=transport)
    assert result == dest_dir / f"{hashlib.sha256(data).hexdigest()[:16]}-oh-linux-x64.zip"
    assert result.read_bytes() == data
    # no .dl-* left
    assert list(dest_dir.glob(".dl-*")) == []


@pytest.mark.asyncio
async def test_download_sends_more_than_size_plus1_raises_app_download_error_no_final_no_dot_dl(tmp_path):
    manifest = make_valid_manifest()
    data = b"hello"
    h = hashlib.sha256(data).hexdigest()
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["sha256"] = h
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["size"] = 5

    dest_dir = tmp_path / "downloads"

    def handler(request: httpx.Request):
        if request.url.path.endswith("/openharness/2.0.0/oh-linux-x64.zip"):
            return httpx.Response(200, content=b"hello!" + b"x")  # 6 bytes
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    from tinyagentos.orb.apps_store import AppDownloadError
    with pytest.raises(AppDownloadError):
        await apps_store.download_app(manifest, "openharness-taos", "https://example.com/", dest_dir, transport=transport)
    assert list(dest_dir.glob(f"{h[:16]}-oh-linux-x64.zip")) == []
    assert list(dest_dir.glob(".dl-*")) == []


@pytest.mark.asyncio
async def test_download_right_size_wrong_content_raises_app_download_error_existing_file_untouched(tmp_path):
    manifest = make_valid_manifest()
    data = b"hello"
    h = hashlib.sha256(data).hexdigest()
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["sha256"] = h
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["size"] = len(data)

    dest_dir = tmp_path / "downloads"
    dest_dir.mkdir(parents=True)
    final = dest_dir / f"{h[:16]}-oh-linux-x64.zip"
    final.write_bytes(b"oldcontent")

    def handler(request: httpx.Request):
        if request.url.path.endswith("/openharness/2.0.0/oh-linux-x64.zip"):
            return httpx.Response(200, content=b"world")  # same size? "hello" is 5, "world" is 5
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    from tinyagentos.orb.apps_store import AppDownloadError
    with pytest.raises(AppDownloadError):
        await apps_store.download_app(manifest, "openharness-taos", "https://example.com/", dest_dir, transport=transport)
    assert final.read_bytes() == b"oldcontent"
    assert list(dest_dir.glob(".dl-*")) == []


@pytest.mark.asyncio
async def test_download_short_body_raises_app_download_error(tmp_path):
    manifest = make_valid_manifest()
    data = b"hello"
    h = hashlib.sha256(data).hexdigest()
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["sha256"] = h
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["size"] = 5

    dest_dir = tmp_path / "downloads"

    def handler(request: httpx.Request):
        if request.url.path.endswith("/openharness/2.0.0/oh-linux-x64.zip"):
            return httpx.Response(200, content=b"hell")  # 4 bytes
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    from tinyagentos.orb.apps_store import AppDownloadError
    with pytest.raises(AppDownloadError):
        await apps_store.download_app(manifest, "openharness-taos", "https://example.com/", dest_dir, transport=transport)
    assert list(dest_dir.glob(f"{h[:16]}-oh-linux-x64.zip")) == []
    assert list(dest_dir.glob(".dl-*")) == []


@pytest.mark.asyncio
async def test_download_404_and_302_raises_app_download_error(tmp_path):
    manifest = make_valid_manifest()
    data = b"hello"
    h = hashlib.sha256(data).hexdigest()
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["sha256"] = h
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["size"] = 5

    dest_dir = tmp_path / "downloads"

    def handler404(request: httpx.Request):
        return httpx.Response(404)

    transport404 = httpx.MockTransport(handler404)
    from tinyagentos.orb.apps_store import AppDownloadError
    with pytest.raises(AppDownloadError):
        await apps_store.download_app(manifest, "openharness-taos", "https://example.com/", dest_dir, transport=transport404)

    def handler302(request: httpx.Request):
        return httpx.Response(302, headers={"Location": "https://other.com/x"})
    transport302 = httpx.MockTransport(handler302)
    with pytest.raises(AppDownloadError):
        await apps_store.download_app(manifest, "openharness-taos", "https://example.com/", dest_dir, transport=transport302)


@pytest.mark.asyncio
async def test_download_request_url_equals_base_plus_slash_plus_relative_url(tmp_path):
    manifest = make_valid_manifest()
    data = b"hello"
    h = hashlib.sha256(data).hexdigest()
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["sha256"] = h
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["size"] = 5

    dest_dir = tmp_path / "downloads"
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        return httpx.Response(200, content=data)

    transport = httpx.MockTransport(handler)
    await apps_store.download_app(manifest, "openharness-taos", "https://example.com/", dest_dir, transport=transport)
    assert seen["url"] == "https://example.com/openharness/2.0.0/oh-linux-x64.zip"


@pytest.mark.asyncio
async def test_download_file_entry_with_size_app_max_bytes_plus1_raises_app_download_error_transport_never_called(tmp_path):
    manifest = make_valid_manifest()
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["size"] = apps_store.APP_MAX_BYTES + 1
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["sha256"] = "a" * 64

    dest_dir = tmp_path / "downloads"
    called = {"n": 0}

    def handler(request: httpx.Request):
        called["n"] += 1
        return httpx.Response(200, content=b"x")

    transport = httpx.MockTransport(handler)
    from tinyagentos.orb.apps_store import AppDownloadError
    with pytest.raises(AppDownloadError):
        await apps_store.download_app(manifest, "openharness-taos", "https://example.com/", dest_dir, transport=transport)
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_fetch_manifest_concurrent_high_water_cache_matches_serial(tmp_path, test_key, keys_dict):
    """Concurrent fetches serialize verify+accept+cache under _fetch_manifest_lock.

    Serial 5 answers instantly and accepts first (state 5); serial 6 answers after
    a short delay and accepts second (state 6).  Without the lock, serial 5's
    cache write is held until serial 6's completes, so serial 5 writes last and
    leaves an older manifest (serial 5) cached behind serial 6 state.  With the
    lock the sequence is serialized so the cache ends at serial 6.
    """
    private_key, _, key_id = test_key

    def signed(serial: int) -> tuple[bytes, bytes]:
        manifest = make_valid_manifest()
        manifest["serial"] = serial
        manifest_bytes = json.dumps(manifest).encode("utf-8")
        return manifest_bytes, sign_manifest(private_key, manifest_bytes)

    m5_bytes, m5_sig = signed(5)
    m6_bytes, m6_sig = signed(6)

    cache_dir = tmp_path / "cache"
    state_path = tmp_path / "state.json"

    six_done = threading.Event()
    original_awb = apps_store.atomic_io.atomic_write_bytes

    def awb(path, data, *a, **kwargs):
        path = Path(path)
        if path.name == "desktop.json" and b'"serial": 6' in data:
            result = original_awb(path, data, *a, **kwargs)
            six_done.set()
            return result
        if path.name == "desktop.json":
            six_done.wait(timeout=0.5)
        return original_awb(path, data, *a, **kwargs)

    apps_store.atomic_io.atomic_write_bytes = awb
    try:
        async def handler(request, serial_bytes, serial_sig, delay_s: float):
            if delay_s:
                await asyncio.sleep(delay_s)
            if request.url.path.endswith("/desktop.json.sig"):
                return httpx.Response(200, content=serial_sig)
            if request.url.path.endswith("/desktop.json"):
                return httpx.Response(200, content=serial_bytes)
            return httpx.Response(404)

        results = await asyncio.gather(
            apps_store.fetch_manifest(
                "https://example.com/", cache_dir, state_path, keys=keys_dict,
                transport=httpx.MockTransport(lambda req, sb=m5_bytes, ss=m5_sig: handler(req, sb, ss, 0.0)),
            ),
            apps_store.fetch_manifest(
                "https://example.com/", cache_dir, state_path, keys=keys_dict,
                transport=httpx.MockTransport(lambda req, sb=m6_bytes, ss=m6_sig: handler(req, sb, ss, 0.15)),
            ),
            return_exceptions=True,
        )
    finally:
        apps_store.atomic_io.atomic_write_bytes = original_awb

    for result in results:
        assert not isinstance(result, Exception) or isinstance(result, catalog_sig.CatalogSerialRegression)

    state = json.loads(state_path.read_text())
    assert state["serial"] == 6
    cached = json.loads((cache_dir / "desktop.json").read_bytes())
    assert cached["serial"] == 6


@pytest.mark.asyncio
async def test_download_stalled_stream_times_out_no_partial_file(tmp_path):
    manifest = make_valid_manifest()
    data = b"hello"
    h = hashlib.sha256(data).hexdigest()
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["sha256"] = h
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["size"] = len(data)

    dest_dir = tmp_path / "downloads"

    class StalledStream(httpx.AsyncByteStream):
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(3600)
            raise StopAsyncIteration

    class StalledTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=StalledStream(), request=request)

    transport = StalledTransport()
    from tinyagentos.orb.apps_store import AppDownloadError
    with pytest.raises(AppDownloadError, match="stalled"):
        await apps_store.download_app(
            manifest, "openharness-taos", "https://example.com/", dest_dir,
            transport=transport, idle_timeout=1,
        )
    assert list(dest_dir.glob(f"{h[:16]}-oh-linux-x64.zip")) == []
    assert list(dest_dir.glob(".dl-*")) == []


@pytest.mark.asyncio
async def test_download_versions_sharing_basename_keep_distinct_files(tmp_path):
    def entry(version: str, url: str, content: bytes) -> dict:
        return {
            "version": version,
            "yanked": False,
            "files": {
                "linux-x64": {"url": url, "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)},
            },
        }

    manifest = {
        "kind": apps_store.MANIFEST_KIND,
        "serial": 1,
        "apps": [{
            "id": "openharness-taos",
            "versions": [
                entry("2.0.0", "app/2.0.0/app.zip", b"v2-content"),
                entry("1.0.0", "app/1.0.0/app.zip", b"v1-content"),
            ],
        }],
    }

    def handler(request: httpx.Request):
        if request.url.path.endswith("/app/2.0.0/app.zip"):
            return httpx.Response(200, content=b"v2-content")
        if request.url.path.endswith("/app/1.0.0/app.zip"):
            return httpx.Response(200, content=b"v1-content")
        return httpx.Response(404)

    dest_dir = tmp_path / "downloads"
    transport = httpx.MockTransport(handler)

    result2 = await apps_store.download_app(
        manifest, "openharness-taos", "https://example.com/", dest_dir,
        version="2.0.0", platform="linux-x64", transport=transport,
    )
    result1 = await apps_store.download_app(
        manifest, "openharness-taos", "https://example.com/", dest_dir,
        version="1.0.0", platform="linux-x64", transport=transport,
    )

    assert result1 != result2
    assert result1.read_bytes() == b"v1-content"
    assert result2.read_bytes() == b"v2-content"
    assert result1.name != "app.zip"
    assert result2.name != "app.zip"


@pytest.mark.asyncio
async def test_download_cancelled_mid_stream_cleans_temp_file_and_propagates(tmp_path):
    """Cancelling the download task mid-stream removes the temp file and propagates CancelledError."""
    manifest = make_valid_manifest()
    data = b"hello world"  # 11 bytes, will be sent in chunks
    h = hashlib.sha256(data).hexdigest()
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["sha256"] = h
    manifest["apps"][0]["versions"][0]["files"]["linux-x64"]["size"] = len(data)

    dest_dir = tmp_path / "downloads"

    class SlowStream(httpx.AsyncByteStream):
        def __init__(self, content: bytes, chunk_size: int = 3, delay: float = 0.1):
            self.content = content
            self.chunk_size = chunk_size
            self.delay = delay
            self.offset = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(self.delay)
            if self.offset >= len(self.content):
                raise StopAsyncIteration
            chunk = self.content[self.offset : self.offset + self.chunk_size]
            self.offset += len(chunk)
            return chunk

    class SlowTransport(httpx.AsyncBaseTransport):
        def __init__(self, stream: httpx.AsyncByteStream):
            self._stream = stream

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=self._stream, request=request)

    stream = SlowStream(data, chunk_size=3, delay=0.05)
    transport = SlowTransport(stream)

    async def download_task():
        return await apps_store.download_app(
            manifest, "openharness-taos", "https://example.com/", dest_dir,
            transport=transport,
        )

    task = asyncio.create_task(download_task())
    # Let it start downloading and write a couple of chunks
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Temp file should be cleaned up
    assert list(dest_dir.glob(".dl-*")) == []
    # Final file should not exist
    assert list(dest_dir.glob(f"{h[:16]}-oh-linux-x64.zip")) == []
