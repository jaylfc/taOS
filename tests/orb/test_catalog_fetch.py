"""Tests for tinyagentos/orb/catalog_fetch."""
import json
import pytest
from pathlib import Path

import httpx

from tinyagentos.orb import catalog_sig
from tinyagentos.orb.catalog_sig import (
    CatalogFetchError,
    CatalogSerialRegression,
    CatalogSignatureInvalid,
    CatalogSignatureMissing,
)
from tests.orb.test_catalog_sig import make_sig_json, sign_catalog, test_key, keys_dict


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
