"""Tests for tinyagentos.clients.retry.with_retry."""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from tinyagentos.clients.retry import with_retry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_factory(value="ok"):
    """Factory that always succeeds."""
    async def _coro():
        return value
    return _coro


def _fail_then_succeed(fail_count: int, exc_type=httpx.ConnectError, value="ok"):
    """Factory that raises exc_type for the first fail_count calls, then succeeds."""
    calls = {"n": 0}

    async def _coro():
        calls["n"] += 1
        if calls["n"] <= fail_count:
            raise exc_type("simulated failure")
        return value

    return _coro, calls


def _always_fail_exc(exc_type=httpx.ConnectError):
    async def _coro():
        raise exc_type("always fails")
    return _coro


def _status_error_factory(status_code: int):
    """Factory that raises an HTTPStatusError with the given status code."""
    async def _coro():
        request = httpx.Request("GET", "http://example.com")
        response = httpx.Response(status_code, request=request)
        raise httpx.HTTPStatusError("error", request=request, response=response)
    return _coro


def _response_factory(status_code: int):
    """Factory that returns an httpx.Response (no raise_for_status)."""
    async def _coro():
        request = httpx.Request("GET", "http://example.com")
        return httpx.Response(status_code, request=request)
    return _coro


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestWithRetry:
    async def test_success_on_first_try(self):
        result = await with_retry(_ok_factory("hello"))
        assert result == "hello"

    async def test_success_on_third_try(self):
        factory, calls = _fail_then_succeed(2, httpx.ConnectError)
        result = await with_retry(factory, base_delay=0.001, multiplier=2, max_delay=0.01)
        assert result == "ok"
        assert calls["n"] == 3

    async def test_gives_up_after_max_attempts(self):
        factory = _always_fail_exc(httpx.ConnectError)
        with pytest.raises(httpx.ConnectError):
            await with_retry(
                factory,
                max_attempts=3,
                base_delay=0.001,
                multiplier=2,
                max_delay=0.01,
            )

    async def test_no_retry_on_4xx(self):
        """HTTPStatusError with a 4xx code must propagate immediately without retrying."""
        call_count = {"n": 0}

        async def _factory():
            call_count["n"] += 1
            request = httpx.Request("GET", "http://example.com")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await with_retry(_factory, max_attempts=5, base_delay=0.001)

        assert exc_info.value.response.status_code == 404
        # Must NOT have retried - only 1 call
        assert call_count["n"] == 1

    async def test_retries_on_500(self):
        call_count = {"n": 0}

        async def _factory():
            call_count["n"] += 1
            request = httpx.Request("GET", "http://example.com")
            response = httpx.Response(500, request=request)
            raise httpx.HTTPStatusError("server error", request=request, response=response)

        with pytest.raises(httpx.HTTPStatusError):
            await with_retry(
                _factory, max_attempts=3, base_delay=0.001, multiplier=2, max_delay=0.01
            )
        assert call_count["n"] == 3

    async def test_retries_on_503(self):
        call_count = {"n": 0}

        async def _factory():
            call_count["n"] += 1
            request = httpx.Request("GET", "http://example.com")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("unavailable", request=request, response=response)

        with pytest.raises(httpx.HTTPStatusError):
            await with_retry(
                _factory, max_attempts=2, base_delay=0.001, multiplier=2, max_delay=0.01
            )
        assert call_count["n"] == 2

    async def test_exponential_backoff_timing(self):
        """Verify delays grow exponentially and respect max_delay (jitter disabled
        for deterministic assertions)."""
        sleep_calls: list[float] = []

        async def _fake_sleep(seconds):
            sleep_calls.append(seconds)
            # Don't actually sleep in tests
            return

        factory = _always_fail_exc(httpx.ConnectError)

        with patch("tinyagentos.clients.retry.asyncio.sleep", side_effect=_fake_sleep):
            with pytest.raises(httpx.ConnectError):
                await with_retry(
                    factory,
                    max_attempts=4,
                    base_delay=0.1,
                    multiplier=3.0,
                    max_delay=0.5,
                    jitter=False,
                )

        # Should have slept 3 times (between 4 attempts)
        assert len(sleep_calls) == 3
        assert sleep_calls[0] == pytest.approx(0.1)
        assert sleep_calls[1] == pytest.approx(0.3)
        # 0.9 would exceed max_delay of 0.5, so it's capped
        assert sleep_calls[2] == pytest.approx(0.5)

    async def test_honours_max_delay_cap(self):
        sleep_calls: list[float] = []

        async def _fake_sleep(seconds):
            sleep_calls.append(seconds)

        factory = _always_fail_exc(httpx.ConnectError)

        with patch("tinyagentos.clients.retry.asyncio.sleep", side_effect=_fake_sleep):
            with pytest.raises(httpx.ConnectError):
                await with_retry(
                    factory,
                    max_attempts=5,
                    base_delay=1.0,
                    multiplier=10.0,
                    max_delay=2.0,
                    jitter=False,
                )

        for delay in sleep_calls:
            assert delay <= 2.0

    async def test_retries_on_connect_error(self):
        factory, calls = _fail_then_succeed(1, httpx.ConnectError)
        result = await with_retry(factory, base_delay=0.001)
        assert result == "ok"
        assert calls["n"] == 2

    async def test_retries_on_read_timeout(self):
        factory, calls = _fail_then_succeed(1, httpx.ReadTimeout)
        result = await with_retry(factory, base_delay=0.001)
        assert result == "ok"
        assert calls["n"] == 2

    async def test_retries_on_remote_protocol_error(self):
        factory, calls = _fail_then_succeed(1, httpx.RemoteProtocolError)
        result = await with_retry(factory, base_delay=0.001)
        assert result == "ok"
        assert calls["n"] == 2

    async def test_response_object_triggers_retry_on_5xx(self):
        """If the coro returns an httpx.Response with 5xx, should retry."""
        call_count = {"n": 0}

        async def _factory():
            call_count["n"] += 1
            request = httpx.Request("GET", "http://example.com")
            return httpx.Response(503, request=request)

        with pytest.raises(Exception):
            await with_retry(
                _factory,
                max_attempts=2,
                base_delay=0.001,
                multiplier=2,
                max_delay=0.01,
            )
        assert call_count["n"] == 2

    async def test_response_object_200_not_retried(self):
        """A 200 response returned as object should succeed immediately."""
        call_count = {"n": 0}

        async def _factory():
            call_count["n"] += 1
            request = httpx.Request("GET", "http://example.com")
            return httpx.Response(200, request=request)

        result = await with_retry(_factory, max_attempts=3, base_delay=0.001)
        assert result.status_code == 200
        assert call_count["n"] == 1

    async def test_jitter_varies_delays(self):
        """With jitter=True (the default), retry delays should be
        randomized — not all identical across runs."""
        sleep_calls: list[float] = []

        async def _fake_sleep(seconds):
            sleep_calls.append(seconds)

        factory = _always_fail_exc(httpx.ConnectError)

        # Run several retries to collect enough delay samples.
        for _ in range(10):
            with patch("tinyagentos.clients.retry.asyncio.sleep", side_effect=_fake_sleep):
                with pytest.raises(httpx.ConnectError):
                    await with_retry(
                        factory,
                        max_attempts=4,
                        base_delay=0.1,
                        multiplier=3.0,
                        max_delay=5.0,
                        jitter=True,
                    )

        # With jitter on, delays should vary within [0.5, 1.5] × nominal.
        # Collect first-retry delays (index 0, 3, 6, ...) and check spread.
        first_delays = sleep_calls[0::3]
        assert len(set(round(d, 4) for d in first_delays)) > 1, (
            "jitter should produce varied delays, got all identical"
        )
        # With the max_delay cap applied after jitter, all jittered delays
        # must stay within [0.5*base, max_delay] — never exceed the documented bound.
        for d in sleep_calls:
            assert d > 0, f"delay should be positive, got {d}"
            assert d <= 5.0, f"delay {d} should not exceed max_delay"
