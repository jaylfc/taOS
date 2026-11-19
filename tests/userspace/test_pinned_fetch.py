"""#971 intermediate hardening: TTL validated-IP cache + pinned fetch client.

The full DNS-pinned fetch for userspace installs is scheduled to land with
the P2 download broker. As an intermediate step (suggested by simonlpaige
on #971, endorsed by jaylfc), url_guard caches the IP(s) it validated for a
hostname for a short TTL, and the httpx client factory used for userspace
fetches pins every connection to the cached validated IP -- so a
nameserver that answers public at validation time and internal at connect
time cannot bounce the fetch onto an internal address. Once the cache
entry expires, the hostname is validated again before any connection is
made, so an answer that changed to an internal address is refused.
"""

from __future__ import annotations

import socket
import sys
import threading
from unittest.mock import patch

import httpx
import pytest

from tinyagentos.userspace import url_guard
from tinyagentos.userspace.url_guard import resolve_safe_public_ip

# example.com's well-known public address -- accepted by the guard.
_PUBLIC = "93.184.216.34"
_INTERNAL = "127.0.0.1"


class _ScriptedResolver:
    """`socket.getaddrinfo` stand-in: per-host answer scripts, one per call.

    `answers` maps a hostname to the addresses it hands out, one per call;
    the last entry repeats once the script runs out. Mirrors the resolver
    in tests/test_ssrf_rebinding.py.
    """

    def __init__(self, answers: dict[str, list[str]]) -> None:
        self.answers = answers
        self.calls: dict[str, int] = {}
        self.total_calls = 0

    def __call__(self, host, port=0, *args, **kwargs):
        self.total_calls += 1
        name = host.decode() if isinstance(host, bytes) else str(host)
        script = self.answers.get(name.lower())
        if script is None:
            raise socket.gaierror(
                socket.EAI_NONAME, f"scripted resolver has no answer for {name!r}"
            )
        index = min(self.calls.get(name.lower(), 0), len(script) - 1)
        self.calls[name.lower()] = index + 1
        addr = script[index]
        if ":" in addr:
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                 "", (addr, 0, 0, 0))
            ]
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
             "", (addr, 0))
        ]


class _RecordingTransport(httpx.AsyncBaseTransport):
    """Inner-transport stand-in: records the request it was handed."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, content=b"ok", request=request)


@pytest.fixture(autouse=True)
def _clear_validated_cache():
    cache = getattr(url_guard, "_validated_ip_cache", None)
    if cache is not None:
        cache.clear()
    yield
    if cache is not None:
        cache.clear()


def _fetch_client(inner: httpx.AsyncBaseTransport) -> httpx.AsyncClient:
    factory = getattr(url_guard, "userspace_fetch_client", None)
    assert factory is not None, (
        "url_guard provides no pinned fetch client factory: a caller that "
        "validates with url_guard and then fetches by hostname lets the "
        "HTTP client re-resolve at connect time, reopening the #971 "
        "DNS-rebinding window"
    )
    return factory(transport=inner)


@pytest.mark.asyncio
async def test_connection_uses_cached_validated_ip_not_rebound_answer():
    """Public at validation time, loopback at connect time: pin wins."""
    resolver = _ScriptedResolver({"rebind.test": [_PUBLIC, _INTERNAL]})
    inner = _RecordingTransport()
    with patch("socket.getaddrinfo", resolver):
        # Validation time: the nameserver answers the public address.
        assert resolve_safe_public_ip("http://rebind.test/app.taosapp") == _PUBLIC
        # Connect time: the nameserver now answers loopback. The fetch must
        # still go to the validated public IP held in the TTL cache.
        async with _fetch_client(inner) as client:
            resp = await client.get("http://rebind.test/app.taosapp")
    assert resp.status_code == 200
    assert len(inner.requests) == 1
    seen = inner.requests[0]
    assert seen.url.host == _PUBLIC
    assert seen.headers["host"] == "rebind.test"
    # The cached entry was used: DNS was never consulted a second time.
    assert resolver.total_calls == 1


@pytest.mark.asyncio
async def test_expired_cache_revalidates_and_refuses_internal_answer():
    """After the TTL, validation runs again; a now-internal answer is refused."""
    resolver = _ScriptedResolver({"rebind.test": [_PUBLIC]})
    inner = _RecordingTransport()
    with patch("socket.getaddrinfo", resolver):
        assert resolve_safe_public_ip("http://rebind.test/app.taosapp") == _PUBLIC
        # The nameserver's answer changes to loopback and the cached
        # validation ages out (expiry forced into the past).
        resolver.answers["rebind.test"] = [_INTERNAL]
        cache = getattr(url_guard, "_validated_ip_cache", {})
        assert "rebind.test" in cache, "validation did not populate the IP cache"
        chosen, _expiry = cache["rebind.test"]
        cache["rebind.test"] = (chosen, 0.0)
        async with _fetch_client(inner) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get("http://rebind.test/app.taosapp")
    # Nothing was ever handed to the inner transport, and the refusal came
    # from a real second validation, not from a stale cache hit.
    assert inner.requests == []
    assert resolver.total_calls == 2


@pytest.mark.asyncio
async def test_unvalidated_hostname_is_validated_then_pinned():
    """Control: no explicit pre-validation; the client validates on use."""
    resolver = _ScriptedResolver({"stable.test": [_PUBLIC]})
    inner = _RecordingTransport()
    with patch("socket.getaddrinfo", resolver):
        async with _fetch_client(inner) as client:
            resp = await client.get("http://stable.test/page")
    assert resp.status_code == 200
    assert len(inner.requests) == 1
    assert inner.requests[0].url.host == _PUBLIC
    assert inner.requests[0].headers["host"] == "stable.test"


@pytest.mark.asyncio
async def test_https_fetch_preserves_sni_hostname():
    """Pinning must not break TLS: SNI still carries the original hostname."""
    resolver = _ScriptedResolver({"secure.test": [_PUBLIC]})
    inner = _RecordingTransport()
    with patch("socket.getaddrinfo", resolver):
        assert resolve_safe_public_ip("https://secure.test/app.taosapp") == _PUBLIC
        async with _fetch_client(inner) as client:
            resp = await client.get("https://secure.test/app.taosapp")
    assert resp.status_code == 200
    seen = inner.requests[0]
    assert seen.url.host == _PUBLIC
    assert seen.extensions["sni_hostname"] == "secure.test"


@pytest.mark.asyncio
async def test_ip_literal_targets_are_checked_too():
    """A literal internal address is refused; a public one passes through."""
    inner = _RecordingTransport()
    async with _fetch_client(inner) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("http://127.0.0.1/admin")
        resp = await client.get("http://8.8.8.8/app.taosapp")
    assert resp.status_code == 200
    assert [r.url.host for r in inner.requests] == ["8.8.8.8"]
def test_insert_sweeps_expired_entries_for_other_hostnames():
    """Expired entries are swept on insert, not only on same-host lookup.

    Regression (CodeRabbit on #4364): expiry used to be enforced only
    when the SAME hostname was looked up again via
    cached_validated_ip, so a hostname validated once and never looked
    up again lingered in the cache past its TTL indefinitely.
    """
    resolver = _ScriptedResolver({"old.test": [_PUBLIC], "new.test": [_PUBLIC]})
    with patch("socket.getaddrinfo", resolver):
        assert resolve_safe_public_ip("http://old.test/app.taosapp") == _PUBLIC
        cache = url_guard._validated_ip_cache
        chosen, _expiry = cache["old.test"]
        cache["old.test"] = (chosen, 0.0)  # force the entry expired
        # Validating a DIFFERENT hostname must sweep the expired entry.
        assert resolve_safe_public_ip("http://new.test/app.taosapp") == _PUBLIC
    assert "old.test" not in url_guard._validated_ip_cache
    assert "new.test" in url_guard._validated_ip_cache


def test_cache_size_is_bounded_oldest_evicted_first():
    """The cache cannot grow without limit; the oldest entries go first."""
    cap = url_guard._VALIDATED_IP_CACHE_MAX_ENTRIES
    answers = {f"host{i}.test": [_PUBLIC] for i in range(cap + 5)}
    resolver = _ScriptedResolver(answers)
    with patch("socket.getaddrinfo", resolver):
        for i in range(cap + 5):
            assert resolve_safe_public_ip(f"http://host{i}.test/app.taosapp") == _PUBLIC
    cache = url_guard._validated_ip_cache
    assert len(cache) == cap
    # The five oldest validations were evicted; recent ones still pin.
    for i in range(5):
        assert f"host{i}.test" not in cache
    assert url_guard.cached_validated_ip(f"host{cap + 4}.test") is not None


@pytest.mark.asyncio
async def test_cache_miss_validation_runs_off_the_event_loop():
    """resolve_safe_public_ip does blocking DNS; on a cache miss the async
    transport must run it via asyncio.to_thread, not on the event-loop
    thread (jaylfc on #4364)."""
    loop_thread = threading.get_ident()
    resolve_threads: list[int] = []
    real_resolve = url_guard.resolve_safe_public_ip

    def spy_resolve(url):
        resolve_threads.append(threading.get_ident())
        return real_resolve(url)

    resolver = _ScriptedResolver({"stable.test": [_PUBLIC]})
    inner = _RecordingTransport()
    with patch("socket.getaddrinfo", resolver), \
            patch.object(url_guard, "resolve_safe_public_ip", spy_resolve):
        async with _fetch_client(inner) as client:
            resp = await client.get("http://stable.test/page")
    assert resp.status_code == 200
    assert resolve_threads, "transport never validated on the cache miss"
    assert all(t != loop_thread for t in resolve_threads), (
        "validation ran on the event-loop thread: "
        f"{resolve_threads} vs loop thread {loop_thread}"
    )


def test_concurrent_cache_mutation_does_not_raise():
    """The validated-IP cache is mutated from many threads at once.

    The install route validates on a to_thread worker and the pinning
    transport validates/looks up from to_thread workers too, so inserts
    (with their sweep of expired entries and oldest-first eviction),
    lookups that drop an expired entry, and clears all interleave in
    production. Regression (CodeRabbit on #4364): the sweep collected
    the expired keys and then deleted them one by one, so two threads
    sweeping concurrently deleted the same key twice -- KeyError out of
    resolve_safe_public_ip, failing an otherwise valid install (the
    iteration itself could also raise RuntimeError). A tight GIL switch
    interval makes the interleavings below fire on the unfixed code
    with high reliability; the cache lock must make them all benign.
    """
    url_guard.clear_validated_ip_cache()
    cache = url_guard._validated_ip_cache
    # A large set of pre-expired entries: every concurrent insert sweeps.
    for i in range(2000):
        cache[f"stale{i}.test"] = (_PUBLIC, 0.0)
    # One shared expired entry every lookup thread tries to drop.
    cache["shared.test"] = (_PUBLIC, 0.0)

    errors: list[BaseException] = []
    barrier = threading.Barrier(18)

    def run(fn):
        def wrapped():
            try:
                barrier.wait()
                fn()
            except BaseException as exc:  # collected; asserted below
                errors.append(exc)

        return wrapped

    def inserter(n: int) -> None:
        for j in range(50):
            url_guard._cache_validated_ip(f"t{n}-{j}.test", _PUBLIC)

    def looker() -> None:
        for _ in range(200):
            url_guard.cached_validated_ip("shared.test")

    def clearer() -> None:
        for _ in range(20):
            url_guard.clear_validated_ip_cache()

    threads = [
        threading.Thread(target=run(lambda n=n: inserter(n))) for n in range(8)
    ]
    threads += [threading.Thread(target=run(looker)) for _ in range(8)]
    threads += [threading.Thread(target=run(clearer)) for _ in range(2)]

    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-5)
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(old_interval)

    assert errors == []
    assert len(cache) <= url_guard._VALIDATED_IP_CACHE_MAX_ENTRIES
