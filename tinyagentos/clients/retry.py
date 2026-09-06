"""Retry wrapper for outbound inference HTTP calls.

Every inference path (embed, rerank, chat completions) and every framework
adapter's ``/message`` proxy uses this module so transient connection errors
and retryable HTTP statuses are handled uniformly.

Design:
- Exponential backoff with jitter, capped at ``max_delay`` per sleep.
- Retries the whole transient half of httpx's transport hierarchy — both the
  network errors (``ConnectError``, ``ReadError``, …) and the timeouts
  (``ConnectTimeout``, ``PoolTimeout``, …).  A connect *timeout* is what a
  lazily-started local backend produces while it is booting and it does NOT
  inherit ``ConnectError``, so it has to be named through its own base class.
- Retries 429 (honouring ``Retry-After``) and the retryable 5xx codes.  No
  other 4xx is ever retried — client errors are not transient.
- Bounds total elapsed time when the caller passes ``max_total_seconds``: the
  loop stops rather than starting an attempt the budget cannot cover, so a
  retry loop cannot outlive the request handler waiting on it.
- Caller passes a zero-arg factory so the coroutine can be re-created on
  each attempt (httpx coroutines cannot be awaited twice).

The loop itself is tenacity (Apache-2.0, pure-Python, no runtime deps); this
module keeps the ``with_retry()`` signature and maps its knobs onto tenacity's
stop/wait/retry primitives.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable, Tuple, Type

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    stop_before_delay,
    wait_exponential,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

# The transient half of httpx's transport hierarchy.  ``TimeoutException``
# covers ConnectTimeout/ReadTimeout/WriteTimeout/PoolTimeout and
# ``NetworkError`` covers ConnectError/ReadError/WriteError/CloseError.  The
# two remaining TransportError branches are deliberately excluded:
# ``UnsupportedProtocol`` and ``LocalProtocolError`` mean the request itself is
# malformed, and retrying a bug seven times just delays the traceback.
DEFAULT_RETRY_ON: Tuple[Type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
    httpx.ProxyError,
)
# 429 belongs here even though it is a 4xx: it is the one client-error code
# that explicitly means "transient, come back later".
DEFAULT_RETRY_ON_STATUS = frozenset({429, 500, 502, 503, 504})

# Upper bound on a server-supplied ``Retry-After``.  A misconfigured upstream
# can name an hour; the retry loop lives inside a request handler, so honour
# the hint only as far as it is still useful.
MAX_RETRY_AFTER_SECONDS = 60.0


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Seconds to wait per the response's ``Retry-After`` header, or None.

    RFC 9110 allows either delay-seconds or an HTTP-date; both are accepted.
    A missing, unparseable, or already-past value yields None so the normal
    exponential backoff applies.
    """
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    raw = raw.strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:  # HTTP-dates are GMT even when unmarked
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _status_error(response: httpx.Response) -> httpx.HTTPStatusError:
    """Turn a retryable status on a *returned* Response into the public error.

    Callers wrap ``client.post()`` directly, so a 5xx/429 usually arrives as a
    Response rather than as a raised exception.  The retry loop needs an
    exception to carry, and it has to be the very ``httpx.HTTPStatusError``
    that ``raise_for_status()`` would have produced — anything module-private
    slips past every upstream ``except httpx.HTTPStatusError``.
    """
    try:
        request = response.request
    except RuntimeError:
        # Response built without a request (hand-rolled stubs).  httpx needs
        # one to construct the error; only its URL is ever read back.
        request = httpx.Request("POST", "about:blank")
    return httpx.HTTPStatusError(
        f"HTTP {response.status_code} {response.reason_phrase}".strip(),
        request=request,
        response=response,
    )


async def _sleep(seconds: float) -> None:
    """Wait between attempts.

    Given to tenacity in place of its own sleep so the backoff goes through
    this module's ``asyncio`` reference: tests suppress real waiting by
    patching ``tinyagentos.clients.retry.asyncio.sleep``, and tenacity's
    portable sleep would otherwise route around that (and through sniffio)
    to decide between asyncio and trio, which this process never uses.
    """
    await asyncio.sleep(seconds)


def _describe(exc: BaseException | None) -> str:
    """Short label for an attempt failure, for the retry log lines."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


async def with_retry(
    coro_factory: Callable[[], Awaitable],
    *,
    max_attempts: int = 5,
    base_delay: float = 0.1,
    multiplier: float = 3.0,
    max_delay: float = 3.0,
    jitter: bool = True,
    retry_on: Tuple[Type[Exception], ...] = DEFAULT_RETRY_ON,
    retry_on_status: frozenset[int] | set[int] = DEFAULT_RETRY_ON_STATUS,
    max_total_seconds: float | None = None,
):
    """Run ``coro_factory()`` with exponential-backoff retry.

    Parameters
    ----------
    coro_factory:
        Zero-arg callable that returns a fresh coroutine each time it is
        called.  Must be re-called on every attempt because coroutines
        cannot be awaited more than once.
    max_attempts:
        Total number of attempts including the first.  Default 5 gives
        delays of 100ms, 300ms, 900ms, 2700ms before giving up.
    base_delay:
        Initial delay in seconds.
    multiplier:
        Multiplicative factor applied after each retry.
    max_delay:
        Upper bound on the per-retry delay in seconds.
    jitter:
        If True (default), add a random offset in ``[0, base_delay)`` to each
        delay to avoid thundering-herd effects.  Set to False for
        deterministic timing in tests.
    retry_on:
        Tuple of exception types that warrant a retry.
    retry_on_status:
        Set of HTTP status codes that warrant a retry, whether the caller
        returned the ``httpx.Response`` or raised ``httpx.HTTPStatusError``.
        Any status outside this set propagates on the first attempt.
    max_total_seconds:
        Optional wall-clock budget for the whole loop.  The loop ends rather
        than starting an attempt the budget cannot cover: a backoff that would
        not finish inside it stops the loop instead of being slept, so no
        attempt ever begins after the deadline and the worst case is the
        budget plus one per-call HTTP timeout.
        Callers running inside a request handler should set this below the
        handler's own timeout (see ``tinyagentos/adapters/retry_policy.py``).

    Returns
    -------
    Whatever the coroutine returns on success.

    Raises
    ------
    The last exception seen after the attempts or the deadline are exhausted.
    A retryable status always surfaces as ``httpx.HTTPStatusError``.
    """
    stop = stop_after_attempt(max_attempts)
    if max_total_seconds is not None:
        # stop_BEFORE_delay, not stop_after_delay: tenacity computes the next
        # sleep first and only then asks the stop condition, so a post-hoc
        # "have we passed the deadline" check lands the loop exactly on the
        # budget and then starts one more attempt -- a whole extra per-call
        # HTTP timeout past the deadline the caller sized against its own
        # handler.  stop_before_delay compares elapsed + upcoming_sleep, so
        # the loop ends instead of overrunning, and the sleep it lets through
        # is by construction one that finishes inside the budget.
        stop = stop | stop_before_delay(max_total_seconds)

    if jitter:
        backoff = wait_exponential_jitter(
            initial=base_delay, max=max_delay, exp_base=multiplier, jitter=base_delay
        )
    else:
        backoff = wait_exponential(
            multiplier=base_delay, exp_base=multiplier, max=max_delay
        )

    def _should_retry(exc: BaseException) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in retry_on_status
        return isinstance(exc, retry_on)

    def _wait(retry_state: RetryCallState) -> float:
        delay = backoff(retry_state)
        outcome = retry_state.outcome
        exc = outcome.exception() if outcome is not None and outcome.failed else None
        if isinstance(exc, httpx.HTTPStatusError):
            retry_after = _parse_retry_after(exc.response)
            if retry_after is not None:
                # The server named a time; never come back sooner than that.
                delay = max(delay, min(retry_after, MAX_RETRY_AFTER_SECONDS))
        # No clamp against the remaining budget: stop_before_delay above sees
        # this value and ends the loop when it would not fit.  Truncating the
        # sleep instead would both overrun the deadline and come back sooner
        # than a Retry-After the server explicitly asked us to honour.
        return delay

    def _log_retry(retry_state: RetryCallState) -> None:
        outcome = retry_state.outcome
        exc = outcome.exception() if outcome is not None else None
        logger.warning(
            "retry: attempt %d/%d failed with %s, retrying in %.1fs",
            retry_state.attempt_number,
            max_attempts,
            _describe(exc),
            retry_state.upcoming_sleep,
        )

    attempts_made = 0
    try:
        async for attempt in AsyncRetrying(
            sleep=_sleep,
            stop=stop,
            wait=_wait,
            retry=retry_if_exception(_should_retry),
            before_sleep=_log_retry,
            reraise=True,
        ):
            with attempt:
                attempts_made = attempt.retry_state.attempt_number
                result = await coro_factory()
                # If the caller returns an httpx.Response we check its status.
                # This lets callers wrap e.g. client.post() directly without
                # manually calling raise_for_status() before returning.
                if (
                    isinstance(result, httpx.Response)
                    and result.status_code in retry_on_status
                ):
                    raise _status_error(result)
                return result
    except Exception as exc:
        # Log every *exhaustion*, however few attempts it took.  The adapters
        # run a 45 s budget behind a 60 s per-call timeout, so attempt 1 can
        # outlive the deadline on its own: the loop ends after one attempt, no
        # before_sleep warning ever fires, and this line is the only signal the
        # retry loop ran.  A non-retryable failure is a pass-through, not an
        # exhaustion, and stays out of the log.
        if attempts_made >= 1 and _should_retry(exc):
            logger.error(
                "retry: all %d attempts exhausted, last error: %s", attempts_made, exc
            )
        raise
    # tenacity always either returns a result or re-raises (reraise=True), so
    # falling out of the loop would mean the contract changed underneath us.
    raise RuntimeError("with_retry: retry loop ended without a result")
