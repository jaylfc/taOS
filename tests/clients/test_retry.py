"""Tests for tinyagentos.clients.retry — tsk-xzcx24.

The suite pins the four behaviours the retry helper got wrong:

(a) only ``httpx.ConnectError`` was retried, so the *timeout* half of httpx's
    transport hierarchy (``ConnectTimeout``, ``PoolTimeout``) and ``ReadError``
    fell straight through to the caller;
(b) an exhausted 5xx raised a module-private ``_StatusError`` that no upstream
    ``except httpx.HTTPStatusError`` could catch;
(c) nothing bounded total elapsed time, so an adapter could sit in the retry
    loop for minutes while the channel-hub router had already given up;
(d) 429 was not retried and ``Retry-After`` was ignored.
"""
from __future__ import annotations

import importlib
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from tinyagentos.clients.retry import with_retry

# Every adapter whose /message proxy runs behind the channel-hub router and so
# must fit inside the router's own timeout. deer_flow is deliberately absent:
# it drives a long-horizon run with its own 600 s call timeout and retries only
# connection-level failures (see deer_flow_adapter._RETRY_KWARGS).
PROXY_ADAPTERS = [
    "hermes",
    "moltis",
    "nanoclaw",
    "nullclaw",
    "openclaw",
    "picoclaw",
    "shibaclaw",
    "zeroclaw",
]


def _response(status_code: int, headers: dict | None = None) -> httpx.Response:
    """Build a real httpx.Response the way an AsyncClient.post() would."""
    request = httpx.Request("POST", "http://localhost:8100/message")
    return httpx.Response(status_code, request=request, headers=headers or {})


# ---------------------------------------------------------------------------
# (a) the uncovered transport-error subclasses
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(httpx.ConnectTimeout("connect timed out"), id="ConnectTimeout"),
        pytest.param(httpx.PoolTimeout("pool exhausted"), id="PoolTimeout"),
        pytest.param(httpx.ReadError("peer reset"), id="ReadError"),
    ],
)
@pytest.mark.asyncio
async def test_transport_error_subclasses_are_retried(exc):
    """ConnectTimeout/PoolTimeout/ReadError are transient and must be retried.

    ConnectTimeout is what a lazily-started local backend produces while it is
    booting, and it does NOT inherit ConnectError — it inherits
    TimeoutException — so the old DEFAULT_RETRY_ON tuple never matched it.
    """
    assert not issubclass(type(exc), httpx.ConnectError), (
        "this test is only meaningful for subclasses the old tuple missed"
    )

    attempts = {"n": 0}

    async def _factory():
        attempts["n"] += 1
        raise exc

    with pytest.raises(type(exc)):
        await with_retry(
            _factory, max_attempts=4, base_delay=0.001, multiplier=2.0, max_delay=0.01
        )

    assert attempts["n"] == 4, f"{type(exc).__name__} was not retried"


# ---------------------------------------------------------------------------
# (b) the private exception must not escape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exhausted_5xx_response_raises_public_exception():
    """A retryable status on a returned Response must surface as HTTPStatusError.

    Callers wrap ``client.post()`` directly, so the 5xx arrives as a Response
    rather than as a raised HTTPStatusError. Once the attempts are exhausted
    the caller's ``except httpx.HTTPStatusError`` has to see it.
    """
    attempts = {"n": 0}

    async def _factory():
        attempts["n"] += 1
        return _response(503)

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await with_retry(
            _factory, max_attempts=3, base_delay=0.001, multiplier=2.0, max_delay=0.01
        )

    assert attempts["n"] == 3
    assert excinfo.value.response.status_code == 503


# ---------------------------------------------------------------------------
# (c) total-elapsed deadline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_total_elapsed_is_bounded_by_max_total_seconds():
    """max_total_seconds stops the loop well before max_attempts is reached."""
    attempts = {"n": 0}

    async def _factory():
        attempts["n"] += 1
        raise httpx.ConnectTimeout("still booting")

    started = time.monotonic()
    with pytest.raises(httpx.ConnectTimeout):
        await with_retry(
            _factory,
            max_attempts=1000,
            base_delay=0.02,
            multiplier=1.0,
            max_delay=0.02,
            jitter=False,
            max_total_seconds=0.2,
        )
    elapsed = time.monotonic() - started

    assert elapsed < 2.0, f"deadline not enforced: {elapsed:.1f}s"
    assert attempts["n"] < 1000, "every attempt ran despite the deadline"


@pytest.mark.asyncio
async def test_sleep_never_overshoots_the_remaining_budget():
    """A backoff longer than what is left of the budget is clamped, not slept."""
    attempts = {"n": 0}

    async def _factory():
        attempts["n"] += 1
        raise httpx.ConnectTimeout("still booting")

    started = time.monotonic()
    with pytest.raises(httpx.ConnectTimeout):
        await with_retry(
            _factory,
            max_attempts=5,
            base_delay=30.0,
            multiplier=2.0,
            max_delay=60.0,
            jitter=False,
            max_total_seconds=0.3,
        )
    elapsed = time.monotonic() - started

    assert elapsed < 2.0, f"slept past the deadline: {elapsed:.1f}s"


@pytest.mark.parametrize("framework", PROXY_ADAPTERS)
def test_adapter_worst_case_stays_under_the_router_timeout(framework):
    """Every proxying adapter's worst case must fit inside the router's budget.

    ``channel_hub/router.py`` waits ``ROUTER_TIMEOUT_SECONDS`` for an adapter's
    ``/message``; an adapter that can burn longer than that is guaranteed to be
    abandoned mid-retry.
    """
    from tinyagentos.adapters import retry_policy

    module = importlib.import_module(f"tinyagentos.adapters.{framework}_adapter")

    assert module._RETRY_KWARGS is retry_policy.RETRY_KWARGS, (
        f"{framework} adapter does not use the shared retry policy"
    )
    budget = retry_policy.RETRY_KWARGS.get("max_total_seconds")
    assert budget is not None, "no total-elapsed deadline"

    # The deadline stops the loop between attempts, so the last attempt can
    # still run for a full per-call timeout after it trips.
    worst_case = budget + retry_policy.CONTROLLER_TIMEOUT_SECONDS
    assert worst_case < retry_policy.ROUTER_TIMEOUT_SECONDS, (
        f"worst case {worst_case}s >= router timeout "
        f"{retry_policy.ROUTER_TIMEOUT_SECONDS}s"
    )


# ---------------------------------------------------------------------------
# (d) 429 + Retry-After
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_429_response_is_retried():
    """429 is a transient rate-limit signal, not a permanent client error."""
    attempts = {"n": 0}

    async def _factory():
        attempts["n"] += 1
        if attempts["n"] < 3:
            return _response(429)
        return _response(200)

    result = await with_retry(
        _factory, max_attempts=5, base_delay=0.001, multiplier=2.0, max_delay=0.01
    )

    assert result.status_code == 200
    assert attempts["n"] == 3


@pytest.mark.asyncio
async def test_429_http_status_error_is_retried():
    """The same holds when the caller raised HTTPStatusError itself."""
    attempts = {"n": 0}

    async def _factory():
        attempts["n"] += 1
        response = _response(429)
        if attempts["n"] < 2:
            raise httpx.HTTPStatusError(
                "Too Many Requests", request=response.request, response=response
            )
        return _response(200)

    result = await with_retry(
        _factory, max_attempts=5, base_delay=0.001, multiplier=2.0, max_delay=0.01
    )

    assert result.status_code == 200
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_retry_after_header_overrides_the_backoff():
    """Retry-After wins over the computed backoff when it is longer."""
    attempts = {"n": 0}

    async def _factory():
        attempts["n"] += 1
        if attempts["n"] < 2:
            return _response(429, {"Retry-After": "0.4"})
        return _response(200)

    started = time.monotonic()
    result = await with_retry(
        _factory, max_attempts=3, base_delay=0.001, multiplier=2.0, max_delay=0.01
    )
    elapsed = time.monotonic() - started

    assert result.status_code == 200
    assert elapsed >= 0.4, (
        f"Retry-After ignored: waited {elapsed:.3f}s, backoff alone is 0.001s"
    )


def test_parse_retry_after_accepts_both_rfc_forms():
    """RFC 9110 allows delay-seconds and an HTTP-date; both must parse."""
    from tinyagentos.clients.retry import _parse_retry_after

    assert _parse_retry_after(_response(429, {"Retry-After": "120"})) == 120.0

    when = datetime.now(timezone.utc) + timedelta(seconds=30)
    parsed = _parse_retry_after(_response(429, {"Retry-After": format_datetime(when)}))
    assert parsed is not None and 20.0 <= parsed <= 40.0

    assert _parse_retry_after(_response(429)) is None
    assert _parse_retry_after(_response(429, {"Retry-After": "soon"})) is None
