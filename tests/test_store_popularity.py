"""Tests for store popularity: GitHub stars, caching, graceful degradation."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tinyagentos import store_popularity as sp


@pytest.fixture(autouse=True)
def _clear_cache():
    sp._reset_cache_for_tests()
    yield
    sp._reset_cache_for_tests()


def _client_returning(status_code: int, json_body: dict | None = None, headers: dict | None = None):
    """Build a fake httpx.AsyncClient whose .get returns the given response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_body or {})
    resp.headers = headers or {}
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    return client


class TestParseRepo:
    def test_github_repo_homepage(self):
        assert sp.parse_repo("https://github.com/jellyfin/jellyfin") == "jellyfin/jellyfin"

    def test_www_github_host(self):
        assert sp.parse_repo("https://www.github.com/owner/repo") == "owner/repo"

    def test_trailing_slash_and_git_suffix(self):
        assert sp.parse_repo("https://github.com/owner/repo/") == "owner/repo"
        assert sp.parse_repo("https://github.com/owner/repo.git") == "owner/repo"

    def test_non_github_homepage(self):
        assert sp.parse_repo("https://excalidraw.com") is None

    def test_bare_profile_url(self):
        assert sp.parse_repo("https://github.com/owner") is None

    def test_rejects_gist_subdomain(self):
        assert sp.parse_repo("https://gist.github.com/owner/abc123") is None

    def test_rejects_raw_githubusercontent(self):
        assert sp.parse_repo("https://raw.githubusercontent.com/owner/repo/main/f") is None

    def test_rejects_github_io_pages(self):
        assert sp.parse_repo("https://owner.github.io/repo") is None

    def test_rejects_github_com_gist_path(self):
        # github.com/gist/... is not an owner/repo even on the right host.
        assert sp.parse_repo("https://github.com/gist/abc") is None

    def test_empty(self):
        assert sp.parse_repo("") is None
        assert sp.parse_repo(None) is None


class TestFetchStars:
    @pytest.mark.asyncio
    async def test_github_repo_gets_stars(self):
        client = _client_returning(200, {"stargazers_count": 72400})
        stars = await sp.fetch_stars("jellyfin/jellyfin", client=client)
        assert stars == 72400
        client.get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_200_long_caches(self):
        client = _client_returning(200, {"stargazers_count": 100})
        await sp.fetch_stars("owner/repo", client=client)
        expires_at, stars = sp._star_cache["owner/repo"]
        assert stars == 100
        # Long TTL: the entry is good for hours, not just the short retry window.
        import time
        assert expires_at - time.time() > sp._RETRY_TTL + 60

    @pytest.mark.asyncio
    async def test_404_yields_null_and_long_caches(self):
        client = _client_returning(404, {})
        stars = await sp.fetch_stars("owner/missing-repo", client=client)
        assert stars is None
        import time
        expires_at, cached = sp._star_cache["owner/missing-repo"]
        assert cached is None
        assert expires_at - time.time() > sp._RETRY_TTL + 60  # genuine 404 = long

    @pytest.mark.asyncio
    async def test_rate_limit_403_yields_null_with_short_ttl(self):
        client = _client_returning(403, {"message": "rate limit exceeded"})
        stars = await sp.fetch_stars("owner/repo", client=client)
        assert stars is None
        import time
        expires_at, cached = sp._star_cache["owner/repo"]
        assert cached is None
        # SHORT retry TTL, not the long star TTL.
        assert expires_at - time.time() <= sp._RETRY_TTL + 1

    @pytest.mark.asyncio
    async def test_rate_limit_429_yields_null_with_short_ttl(self):
        client = _client_returning(429, {})
        stars = await sp.fetch_stars("owner/repo", client=client)
        assert stars is None
        import time
        expires_at, _ = sp._star_cache["owner/repo"]
        assert expires_at - time.time() <= sp._RETRY_TTL + 1

    @pytest.mark.asyncio
    async def test_rate_limit_remaining_header_zero(self):
        client = _client_returning(
            403, {}, headers={"X-RateLimit-Remaining": "0"}
        )
        stars = await sp.fetch_stars("owner/repo", client=client)
        assert stars is None
        # The warmer back-off window is armed.
        assert sp._rate_limited_until > 0

    @pytest.mark.asyncio
    async def test_transient_refetched_on_next_pass(self):
        # A transient 403 must NOT be cached for the long TTL: a follow-up
        # fetch should re-hit GitHub (and succeed) rather than serve stale null.
        rl = _client_returning(403, {"message": "rate limit exceeded"})
        first = await sp.fetch_stars("owner/repo", client=rl)
        assert first is None
        # Manually expire the short retry entry to simulate the next pass.
        import time
        sp._star_cache["owner/repo"] = (time.time() - 1, None)
        sp._rate_limited_until = 0.0
        ok = _client_returning(200, {"stargazers_count": 42})
        second = await sp.fetch_stars("owner/repo", client=ok)
        assert second == 42
        ok.get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_network_error_yields_null_without_raising(self):
        client = MagicMock()
        client.get = AsyncMock(side_effect=RuntimeError("boom"))
        stars = await sp.fetch_stars("owner/repo", client=client)
        assert stars is None
        import time
        expires_at, _ = sp._star_cache["owner/repo"]
        assert expires_at - time.time() <= sp._RETRY_TTL + 1  # transient = short

    @pytest.mark.asyncio
    async def test_caching_avoids_second_call_within_ttl(self):
        client = _client_returning(200, {"stargazers_count": 100})
        first = await sp.fetch_stars("owner/repo", client=client)
        second = await sp.fetch_stars("owner/repo", client=client)
        assert first == second == 100
        client.get.assert_awaited_once()  # second call served from cache

    @pytest.mark.asyncio
    async def test_persist_cache_does_not_run_on_the_event_loop_thread(self):
        """``_persist_cache`` does two blocking fsyncs; every warmer fetch
        going through it on the event loop stalls all concurrent handling."""
        import threading

        loop_thread_id = threading.get_ident()
        seen_thread_ids: list[int] = []

        def spying_persist_cache():
            seen_thread_ids.append(threading.get_ident())

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sp, "_persist_cache", spying_persist_cache)
            client = _client_returning(200, {"stargazers_count": 100})
            await sp.fetch_stars("owner/repo", client=client)

        assert seen_thread_ids, "_persist_cache was never called"
        assert loop_thread_id not in seen_thread_ids, (
            "_persist_cache ran on the event-loop thread -- its fsyncs "
            "block all concurrent request handling"
        )


class TestCachedReads:
    def test_cached_stars_none_when_unknown(self):
        assert sp.cached_stars("owner/repo") is None

    def test_popularity_for_homepage_cached_no_call(self):
        # No cache entry -> github_stars None, and crucially no GitHub call.
        pop = sp.popularity_for_homepage_cached("https://github.com/owner/repo")
        assert pop["github_stars"] is None
        assert pop["score"] == 0.0

    def test_cached_stars_after_warm(self):
        import time
        sp._star_cache["owner/repo"] = (time.time() + 3600, 999)
        assert sp.cached_stars("owner/repo") == 999
        pop = sp.popularity_for_homepage_cached("https://github.com/owner/repo")
        assert pop["github_stars"] == 999

    def test_cached_stars_expired(self):
        import time
        sp._star_cache["owner/repo"] = (time.time() - 1, 5)
        assert sp.cached_stars("owner/repo") is None


class TestWarmer:
    @pytest.mark.asyncio
    async def test_warm_populates_cache(self):
        client = _client_returning(200, {"stargazers_count": 7})
        await sp.warm_popularity_cache(["a/b", "c/d"], client=client)
        assert sp.cached_stars("a/b") == 7
        assert sp.cached_stars("c/d") == 7

    @pytest.mark.asyncio
    async def test_warm_respects_concurrency_bound(self, monkeypatch):
        # Track peak concurrency through fetch_stars; it must never exceed
        # the warmer's semaphore bound.
        live = 0
        peak = 0

        async def _fake_fetch(repo, *, client=None):
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.01)
            live -= 1
            return 1

        monkeypatch.setattr(sp, "fetch_stars", _fake_fetch)
        repos = [f"o/{i}" for i in range(20)]
        await sp.warm_popularity_cache(repos)
        assert peak <= sp._WARM_CONCURRENCY

    @pytest.mark.asyncio
    async def test_warm_backs_off_when_rate_limited(self, monkeypatch):
        import time
        sp._rate_limited_until = time.time() + 3600
        called = False

        async def _fake_fetch(repo, *, client=None):
            nonlocal called
            called = True
            return 1

        monkeypatch.setattr(sp, "fetch_stars", _fake_fetch)
        await sp.warm_popularity_cache(["a/b"])
        assert called is False  # backed off, no fetches issued


class TestScore:
    def test_score_is_stars_today(self):
        assert sp.popularity_shape(500)["score"] == 500.0

    def test_score_with_no_signals_is_zero(self):
        assert sp.popularity_shape(None)["score"] == 0.0

    def test_installs_weighed_when_present_forward_compat(self):
        # #15: installs fill in without a shape change; weighted above a star.
        shape = sp.popularity_shape(100, installs=10)
        assert shape["installs"] == 10
        assert shape["score"] == 100.0 + 10 * 10.0


class TestPersistence:
    def test_round_trip(self, tmp_path):
        import time
        sp.configure_persistence(tmp_path)
        sp._star_cache["owner/repo"] = (time.time() + 3600, 123)
        sp._persist_cache()

        # Simulate a restart: clear in-memory state, reconfigure, reload.
        sp._reset_cache_for_tests()
        assert sp.cached_stars("owner/repo") is None
        sp.configure_persistence(tmp_path)
        assert sp.cached_stars("owner/repo") == 123
