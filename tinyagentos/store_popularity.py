"""Store app popularity, sourced from GitHub stars today and structured to
accept real install telemetry later (#15).

The popularity shape is telemetry-ready and forward-compatible:

    {
        "github_stars": int | None,   # live star count, None when unknown
        "installs": int | None,       # None today; filled by #15 telemetry
        "score": float,               # derived from whatever signals exist
    }

GitHub stars are fetched unauthenticated from api.github.com (allowed by the
network policy). Unauthenticated calls share a ~60 req/hr/IP limit, so the
catalog can have far more GitHub homepages than the limit allows in one pass.

The request path NEVER calls GitHub. The Store list endpoint reads only the
in-memory cache and returns immediately (null/absent for not-yet-warmed apps).
A bounded background warmer (small concurrency) walks the uncached/stale repos
over time, backing off when GitHub signals the rate limit, so we never hammer
past the limit. With more repos than the hourly budget it takes a few passes to
fully warm, which is correct; stars read null in the meantime.

Caching distinguishes error classes so a momentary rate-limit does not blank a
repo for hours:
  - 200 success and a genuine 404 (repo gone) cache for the long TTL.
  - transient failures (403/429 rate-limit, 5xx, timeouts, connection errors)
    cache for a short retry TTL so the next warmer pass re-fetches them.

The cache persists to ``data_dir/store_popularity.json`` so a cold start does
not re-fetch everything. Any GitHub failure degrades the entry to
github_stars=None and never raises.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

# Stars move slowly and the list endpoint is hit often, so 200s and genuine
# 404s cache for hours. Transient failures use a short retry TTL so the warmer
# picks them up on its next pass instead of blanking the repo for hours.
_STAR_TTL = 6 * 60 * 60  # 6 hours: successful lookups and confirmed 404s
_RETRY_TTL = 10 * 60  # 10 minutes: transient rate-limit / network failures

# Background warmer: a few concurrent fetches at most, short per-request timeout.
_WARM_CONCURRENCY = 4
_FETCH_TIMEOUT = 8.0

# key (owner/repo) -> (expires_at_wall_clock, github_stars | None)
# expires_at is wall-clock (time.time()) so it survives the persisted JSON.
_star_cache: dict[str, tuple[float, int | None]] = {}

# When GitHub reports the rate limit is exhausted we record a wall-clock instant
# until which the warmer must not call GitHub. 0.0 means "not blocked".
_rate_limited_until: float = 0.0

_cache_path: Path | None = None

_GITHUB_HOSTS = {"github.com", "www.github.com"}
# Path segments that are not user/repo owners even though they live on
# github.com (gists, raw blobs, github pages org pages, etc).
_NON_REPO_OWNERS = {"gist", "raw", "about", "features", "topics", "marketplace"}


def parse_repo(homepage: str | None) -> str | None:
    """Return owner/repo if homepage points at a GitHub repo, else None.

    Parsed with urllib so only a real github.com (or www.github.com) host with
    exactly owner/repo as the first two path segments yields a repo. Rejects
    gist.github.com, raw.githubusercontent.com, *.github.io pages, and
    host-only or owner-only URLs.
    """
    if not homepage:
        return None
    try:
        parts = urlsplit(homepage if "//" in homepage else "//" + homepage)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host not in _GITHUB_HOSTS:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2:
        return None
    owner, repo = segments[0], segments[1]
    if owner.lower() in _NON_REPO_OWNERS:
        return None
    repo = repo.removesuffix(".git")
    if not owner or not repo:
        return None
    return f"{owner}/{repo}"


def _compute_score(github_stars: int | None, installs: int | None) -> float:
    """Combine the available signals into a single popularity score.

    Today only stars exist, so the score is the star count. When #15 lands
    real install telemetry, installs weigh in here without changing the shape
    or any caller. An entry with no signals scores 0.0.
    """
    score = 0.0
    if github_stars:
        score += float(github_stars)
    if installs:
        # Installs are a stronger signal than a star; weight them when present.
        score += float(installs) * 10.0
    return score


def popularity_shape(github_stars: int | None, installs: int | None = None) -> dict[str, Any]:
    """Build the telemetry-ready popularity dict from the known signals."""
    return {
        "github_stars": github_stars,
        "installs": installs,
        "score": _compute_score(github_stars, installs),
    }


def cached_stars(repo: str | None) -> int | None:
    """Return the cached star count for owner/repo without ever calling GitHub.

    None when the repo is unknown or its cache entry has expired. This is what
    the request path uses so the catalog list never blocks on a live fetch.
    """
    if not repo:
        return None
    cached = _star_cache.get(repo)
    if cached is None:
        return None
    expires_at, stars = cached
    if time.time() >= expires_at:
        return None
    return stars


def popularity_for_homepage_cached(homepage: str | None) -> dict[str, Any]:
    """Resolve the popularity shape for a catalog entry from cache only.

    Never calls GitHub: not-yet-warmed entries get github_stars=None. installs
    is always None until #15.
    """
    return popularity_shape(cached_stars(parse_repo(homepage)))


def _is_rate_limited(resp: httpx.Response) -> bool:
    """True when the response signals the unauthenticated rate limit."""
    if resp.status_code in (403, 429):
        return True
    return resp.headers.get("X-RateLimit-Remaining") == "0"


async def fetch_stars(repo: str, *, client: httpx.AsyncClient | None = None) -> int | None:
    """Fetch and cache the GitHub star count for owner/repo. Never raises.

    Used by the background warmer (not the request path). A fresh cache entry
    short-circuits the call. Caching is error-class aware:
      - 200 success and a genuine 404 cache for the long TTL.
      - 403/429 rate-limit (or X-RateLimit-Remaining: 0), 5xx, timeouts and
        connection errors cache for the short retry TTL and return None, so the
        next warmer pass retries instead of blanking the repo for hours.
    A rate-limit also arms the warmer back-off window.
    """
    global _rate_limited_until
    if _has_fresh_entry(repo):
        return cached_stars(repo)

    owns_client = client is None
    stars: int | None = None
    ttl = _RETRY_TTL  # default: transient unless we prove otherwise
    try:
        if owns_client:
            client = httpx.AsyncClient(timeout=_FETCH_TIMEOUT)
        assert client is not None
        resp = await client.get(
            f"https://api.github.com/repos/{repo}",
            headers={"Accept": "application/vnd.github+json"},
        )
        if resp.status_code == 200:
            raw = resp.json().get("stargazers_count")
            stars = int(raw) if isinstance(raw, (int, float)) else None
            ttl = _STAR_TTL
        elif resp.status_code == 404:
            # Repo genuinely gone: long-cache the None so we stop asking.
            stars = None
            ttl = _STAR_TTL
        elif _is_rate_limited(resp):
            # Transient: short retry TTL and back the warmer off until reset.
            _rate_limited_until = _reset_at(resp)
            stars = None
            ttl = _RETRY_TTL
        else:
            # Other non-200 (5xx etc): transient, short retry TTL.
            stars = None
            ttl = _RETRY_TTL
    except Exception as exc:  # network error, timeout, bad JSON, etc.
        logger.debug("GitHub star fetch failed for %s: %s", repo, exc)
        stars = None
        ttl = _RETRY_TTL
    finally:
        if owns_client and client is not None:
            await client.aclose()

    _star_cache[repo] = (time.time() + ttl, stars)
    _persist_cache()
    return stars


def _reset_at(resp: httpx.Response) -> float:
    """Wall-clock instant the rate limit resets, from X-RateLimit-Reset.

    Falls back to now + retry TTL when the header is missing or unparseable.
    """
    raw = resp.headers.get("X-RateLimit-Reset")
    try:
        reset = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return time.time() + _RETRY_TTL
    # Never block longer than necessary; clamp absurd values.
    return min(reset, time.time() + _STAR_TTL)


def _has_fresh_entry(repo: str) -> bool:
    """True when the repo has any unexpired cache entry (including a cached None)."""
    cached = _star_cache.get(repo)
    return cached is not None and time.time() < cached[0]


async def warm_popularity_cache(
    repos: list[str], *, client: httpx.AsyncClient | None = None
) -> None:
    """Populate the cache for the given repos with bounded concurrency.

    Skips repos with a fresh entry and stops early when GitHub has signalled
    the rate limit is exhausted (until its reset). Designed to be called on a
    schedule by a background loop; one pass warms only as many repos as the
    rate limit allows, which is correct.
    """
    stale = [r for r in repos if not _has_fresh_entry(r)]
    if not stale:
        return
    if time.time() < _rate_limited_until:
        return  # still backing off from a prior rate-limit signal

    owns_client = client is None
    sem = asyncio.Semaphore(_WARM_CONCURRENCY)

    async def _one(repo: str) -> None:
        if time.time() < _rate_limited_until:
            return  # a sibling fetch hit the limit; stop spending budget
        async with sem:
            await fetch_stars(repo, client=client)

    try:
        if owns_client:
            client = httpx.AsyncClient(timeout=_FETCH_TIMEOUT)
        await asyncio.gather(*(_one(r) for r in stale))
    finally:
        if owns_client and client is not None:
            await client.aclose()


def configure_persistence(data_dir: Path) -> None:
    """Point the cache at ``data_dir/store_popularity.json`` and load it.

    Idempotent. Safe to call before the warmer starts so a cold boot reuses the
    last warmed values instead of re-fetching everything.
    """
    global _cache_path
    _cache_path = Path(data_dir) / "store_popularity.json"
    _load_cache()


def _load_cache() -> None:
    if _cache_path is None or not _cache_path.exists():
        return
    try:
        raw = json.loads(_cache_path.read_text())
    except Exception as exc:
        logger.debug("store popularity cache load failed: %s", exc)
        return
    if not isinstance(raw, dict):
        return
    for repo, entry in raw.items():
        try:
            expires_at, stars = entry
            _star_cache[str(repo)] = (
                float(expires_at),
                int(stars) if stars is not None else None,
            )
        except (TypeError, ValueError):
            continue


def _persist_cache() -> None:
    if _cache_path is None:
        return
    try:
        _cache_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: a crash mid-write must not corrupt the persisted cache.
        tmp_path = _cache_path.with_name(_cache_path.name + ".tmp")
        tmp_path.write_text(
            json.dumps({r: [exp, stars] for r, (exp, stars) in _star_cache.items()})
        )
        tmp_path.replace(_cache_path)
    except Exception as exc:
        logger.debug("store popularity cache persist failed: %s", exc)


def _reset_cache_for_tests() -> None:
    """Clear the star cache and rate-limit gate. For tests only."""
    global _rate_limited_until, _cache_path
    _star_cache.clear()
    _rate_limited_until = 0.0
    _cache_path = None
