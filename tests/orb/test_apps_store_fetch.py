"""Tests for tinyagentos/orb/apps_store fetch/download."""
import base64
import hashlib
import json
import os
import pytest
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
    assert result == dest_dir / "oh-linux-x64.zip"
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
    assert list(dest_dir.glob("oh-linux-x64.zip")) == []
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
    final = dest_dir / "oh-linux-x64.zip"
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
    assert list(dest_dir.glob("oh-linux-x64.zip")) == []
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
