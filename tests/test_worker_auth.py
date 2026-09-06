from __future__ import annotations

import hashlib
import hmac
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from tinyagentos.cluster.worker_auth import _err, _HMACError, require_worker_hmac


# ---------------------------------------------------------------------------
# _err
# ---------------------------------------------------------------------------

class TestErr:
    def test_returns_json_response(self):
        resp = _err("some_code", "some message", 403)
        assert resp.status_code == 403
        import json as _json
        data = _json.loads(resp.body)
        assert data == {"error": "some message", "code": "some_code"}

    def test_status_401(self):
        resp = _err("worker_not_paired", "not paired", 401)
        assert resp.status_code == 401

    def test_empty_code_and_message(self):
        resp = _err("", "", 500)
        assert resp.status_code == 500
        import json as _json
        data = _json.loads(resp.body)
        assert data == {"error": "", "code": ""}


# ---------------------------------------------------------------------------
# _HMACError
# ---------------------------------------------------------------------------

class TestHMACError:
    def test_wraps_response(self):
        resp = _err("c", "m", 401)
        err = _HMACError(resp)
        assert err.response is resp

    def test_is_exception(self):
        resp = _err("c", "m", 401)
        err = _HMACError(resp)
        assert isinstance(err, Exception)

    def test_response_body_is_valid_json(self):
        resp = _err("c", "m", 401)
        import json as _json
        data = _json.loads(resp.body)
        assert "error" in data
        assert "code" in data


# ---------------------------------------------------------------------------
# require_worker_hmac -- missing headers
# ---------------------------------------------------------------------------

class TestRequireWorkerHmacMissingHeaders:
    @pytest.fixture
    def make_request(self):
        def _make(**headers):
            req = MagicMock()
            req.headers = MagicMock()
            req.headers.get = lambda key, default="": headers.get(key, default)
            return req
        return _make

    @pytest.mark.asyncio
    async def test_missing_worker_name_raises(self, make_request):
        req = make_request(
            **{"x-taos-timestamp": "123", "x-taos-signature": "abc"}
        )
        with pytest.raises(_HMACError) as exc_info:
            await require_worker_hmac(req)
        assert exc_info.value.response.status_code == 401
        assert b"worker_not_paired" in exc_info.value.response.body

    @pytest.mark.asyncio
    async def test_missing_timestamp_raises(self, make_request):
        req = make_request(
            **{"x-taos-worker-name": "w1", "x-taos-signature": "abc"}
        )
        with pytest.raises(_HMACError) as exc_info:
            await require_worker_hmac(req)
        assert exc_info.value.response.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_signature_raises(self, make_request):
        req = make_request(
            **{"x-taos-worker-name": "w1", "x-taos-timestamp": "123"}
        )
        with pytest.raises(_HMACError) as exc_info:
            await require_worker_hmac(req)
        assert exc_info.value.response.status_code == 401

    @pytest.mark.asyncio
    async def test_all_headers_missing_raises(self, make_request):
        req = make_request()
        with pytest.raises(_HMACError) as exc_info:
            await require_worker_hmac(req)
        assert exc_info.value.response.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_worker_name_raises(self, make_request):
        req = make_request(
            **{"x-taos-worker-name": "", "x-taos-timestamp": "123", "x-taos-signature": "abc"}
        )
        with pytest.raises(_HMACError) as exc_info:
            await require_worker_hmac(req)
        assert exc_info.value.response.status_code == 401

    @pytest.mark.asyncio
    async def test_whitespace_only_worker_name_raises(self, make_request):
        req = make_request(
            **{"x-taos-worker-name": "   ", "x-taos-timestamp": "123", "x-taos-signature": "abc"}
        )
        with pytest.raises(_HMACError) as exc_info:
            await require_worker_hmac(req)
        assert exc_info.value.response.status_code == 401


# ---------------------------------------------------------------------------
# require_worker_hmac -- invalid timestamp
# ---------------------------------------------------------------------------

class TestRequireWorkerHmacInvalidTimestamp:
    @pytest.mark.asyncio
    async def test_non_numeric_timestamp_raises(self):
        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda key, default="": {
            "x-taos-worker-name": "w1",
            "x-taos-timestamp": "not-a-number",
            "x-taos-signature": "abc",
        }.get(key, default)
        with pytest.raises(_HMACError) as exc_info:
            await require_worker_hmac(req)
        assert exc_info.value.response.status_code == 401
        assert b"stale_timestamp" in exc_info.value.response.body

    @pytest.mark.asyncio
    async def test_empty_timestamp_raises(self):
        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda key, default="": {
            "x-taos-worker-name": "w1",
            "x-taos-timestamp": "",
            "x-taos-signature": "abc",
        }.get(key, default)
        with pytest.raises(_HMACError) as exc_info:
            await require_worker_hmac(req)
        assert exc_info.value.response.status_code == 401

    @pytest.mark.asyncio
    async def test_stale_timestamp_raises(self):
        old_ts = str(int(time.time()) - 600)
        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda key, default="": {
            "x-taos-worker-name": "w1",
            "x-taos-timestamp": old_ts,
            "x-taos-signature": "abc",
        }.get(key, default)
        with pytest.raises(_HMACError) as exc_info:
            await require_worker_hmac(req)
        assert exc_info.value.response.status_code == 401
        assert b"stale_timestamp" in exc_info.value.response.body

    @pytest.mark.asyncio
    async def test_future_timestamp_within_window_passes(self):
        future_ts = str(int(time.time()) + 60)
        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda key, default="": {
            "x-taos-worker-name": "w1",
            "x-taos-timestamp": future_ts,
            "x-taos-signature": "abc",
        }.get(key, default)
        req.app.state.cluster_pairing = None
        with pytest.raises(_HMACError) as exc_info:
            await require_worker_hmac(req)
        assert exc_info.value.response.status_code == 401
        assert b"worker_not_paired" in exc_info.value.response.body

    @pytest.mark.asyncio
    async def test_timestamp_exactly_at_boundary_raises(self):
        boundary_ts = str(int(time.time()) - 301)
        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda key, default="": {
            "x-taos-worker-name": "w1",
            "x-taos-timestamp": boundary_ts,
            "x-taos-signature": "abc",
        }.get(key, default)
        with pytest.raises(_HMACError) as exc_info:
            await require_worker_hmac(req)
        assert exc_info.value.response.status_code == 401
        assert b"stale_timestamp" in exc_info.value.response.body


# ---------------------------------------------------------------------------
# require_worker_hmac -- no pairing store
# ---------------------------------------------------------------------------

class TestRequireWorkerHmacNoPairingStore:
    @pytest.mark.asyncio
    async def test_no_cluster_pairing_attr_raises(self):
        ts = str(int(time.time()))
        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda key, default="": {
            "x-taos-worker-name": "w1",
            "x-taos-timestamp": ts,
            "x-taos-signature": "abc",
        }.get(key, default)
        req.app.state = MagicMock(spec=[])
        with pytest.raises(_HMACError) as exc_info:
            await require_worker_hmac(req)
        assert exc_info.value.response.status_code == 401
        assert b"worker_not_paired" in exc_info.value.response.body

    @pytest.mark.asyncio
    async def test_cluster_pairing_is_none_raises(self):
        ts = str(int(time.time()))
        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda key, default="": {
            "x-taos-worker-name": "w1",
            "x-taos-timestamp": ts,
            "x-taos-signature": "abc",
        }.get(key, default)
        req.app.state.cluster_pairing = None
        with pytest.raises(_HMACError) as exc_info:
            await require_worker_hmac(req)
        assert exc_info.value.response.status_code == 401
        assert b"worker_not_paired" in exc_info.value.response.body


# ---------------------------------------------------------------------------
# require_worker_hmac -- unknown worker
# ---------------------------------------------------------------------------

class TestRequireWorkerHmacUnknownWorker:
    @pytest.mark.asyncio
    async def test_unknown_worker_raises(self):
        ts = str(int(time.time()))
        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda key, default="": {
            "x-taos-worker-name": "unknown-worker",
            "x-taos-timestamp": ts,
            "x-taos-signature": "abc",
        }.get(key, default)
        store = AsyncMock()
        store.get_signing_key.return_value = None
        req.app.state.cluster_pairing = store
        with pytest.raises(_HMACError) as exc_info:
            await require_worker_hmac(req)
        assert exc_info.value.response.status_code == 401
        assert b"worker_not_paired" in exc_info.value.response.body
        store.get_signing_key.assert_awaited_once_with("unknown-worker")


# ---------------------------------------------------------------------------
# require_worker_hmac -- bad signature
# ---------------------------------------------------------------------------

class TestRequireWorkerHmacBadSignature:
    @pytest.mark.asyncio
    async def test_wrong_signature_raises(self):
        ts = str(int(time.time()))
        signing_key = b"\x01" * 32
        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda key, default="": {
            "x-taos-worker-name": "w1",
            "x-taos-timestamp": ts,
            "x-taos-signature": "deadbeef",
        }.get(key, default)
        req.method = "POST"
        req.url.path = "/api/cluster/workers"
        req.body = AsyncMock(return_value=b'{"name":"w1"}')
        store = AsyncMock()
        store.get_signing_key.return_value = signing_key
        req.app.state.cluster_pairing = store
        with pytest.raises(_HMACError) as exc_info:
            await require_worker_hmac(req)
        assert exc_info.value.response.status_code == 401
        assert b"bad_signature" in exc_info.value.response.body


# ---------------------------------------------------------------------------
# require_worker_hmac -- happy path
# ---------------------------------------------------------------------------

class TestRequireWorkerHmacHappyPath:
    @pytest.mark.asyncio
    async def test_valid_request_sets_hmac_worker_name(self):
        signing_key = b"\x42" * 32
        worker_name = "my-worker"
        method = "POST"
        path = "/api/cluster/workers"
        raw_body = b'{"name":"my-worker"}'
        ts = str(int(time.time()))
        body_hash = hashlib.sha256(raw_body).hexdigest()
        message = f"{ts}.{method}.{path}.{body_hash}".encode()
        sig = hmac.new(signing_key, message, hashlib.sha256).hexdigest()

        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda key, default="": {
            "x-taos-worker-name": worker_name,
            "x-taos-timestamp": ts,
            "x-taos-signature": sig,
        }.get(key, default)
        req.method = method
        req.url.path = path
        req.body = AsyncMock(return_value=raw_body)
        store = AsyncMock()
        store.get_signing_key.return_value = signing_key
        req.app.state.cluster_pairing = store

        result = await require_worker_hmac(req)

        assert result is None
        assert req.state.hmac_worker_name == worker_name
        store.get_signing_key.assert_awaited_once_with(worker_name)

    @pytest.mark.asyncio
    async def test_get_method_works(self):
        signing_key = b"\x01" * 32
        worker_name = "w-get"
        method = "GET"
        path = "/api/cluster/workers/status"
        raw_body = b""
        ts = str(int(time.time()))
        body_hash = hashlib.sha256(raw_body).hexdigest()
        message = f"{ts}.{method}.{path}.{body_hash}".encode()
        sig = hmac.new(signing_key, message, hashlib.sha256).hexdigest()

        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda key, default="": {
            "x-taos-worker-name": worker_name,
            "x-taos-timestamp": ts,
            "x-taos-signature": sig,
        }.get(key, default)
        req.method = method
        req.url.path = path
        req.body = AsyncMock(return_value=raw_body)
        store = AsyncMock()
        store.get_signing_key.return_value = signing_key
        req.app.state.cluster_pairing = store

        result = await require_worker_hmac(req)
        assert result is None
        assert req.state.hmac_worker_name == worker_name

    @pytest.mark.asyncio
    async def test_lowercase_method_normalized(self):
        signing_key = b"\x02" * 32
        worker_name = "w-lower"
        method = "post"
        path = "/api/test"
        raw_body = b'{"key":"val"}'
        ts = str(int(time.time()))
        body_hash = hashlib.sha256(raw_body).hexdigest()
        message = f"{ts}.POST.{path}.{body_hash}".encode()
        sig = hmac.new(signing_key, message, hashlib.sha256).hexdigest()

        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda key, default="": {
            "x-taos-worker-name": worker_name,
            "x-taos-timestamp": ts,
            "x-taos-signature": sig,
        }.get(key, default)
        req.method = method
        req.url.path = path
        req.body = AsyncMock(return_value=raw_body)
        store = AsyncMock()
        store.get_signing_key.return_value = signing_key
        req.app.state.cluster_pairing = store

        result = await require_worker_hmac(req)
        assert result is None

    @pytest.mark.asyncio
    async def test_large_body(self):
        signing_key = b"\x03" * 32
        worker_name = "w-large"
        method = "POST"
        path = "/api/upload"
        raw_body = b"x" * 100_000
        ts = str(int(time.time()))
        body_hash = hashlib.sha256(raw_body).hexdigest()
        message = f"{ts}.{method}.{path}.{body_hash}".encode()
        sig = hmac.new(signing_key, message, hashlib.sha256).hexdigest()

        req = MagicMock()
        req.headers = MagicMock()
        req.headers.get = lambda key, default="": {
            "x-taos-worker-name": worker_name,
            "x-taos-timestamp": ts,
            "x-taos-signature": sig,
        }.get(key, default)
        req.method = method
        req.url.path = path
        req.body = AsyncMock(return_value=raw_body)
        store = AsyncMock()
        store.get_signing_key.return_value = signing_key
        req.app.state.cluster_pairing = store

        result = await require_worker_hmac(req)
        assert result is None
