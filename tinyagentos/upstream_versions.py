"""Upstream release detection for docker-image catalog apps.

The Store's ``update_available`` signal used to be purely internal: it
compared the version pinned in the catalog manifest against the version
recorded at install time. When a catalog pin goes stale (the manifest
still says ``2024.12.0`` while the image publisher has shipped
``2026.10.2``), that comparison can never fire and the app never
appears in the Store's Updates tab.

This module adds the missing half: a periodic, cached check of the
container registry for every catalog app with ``install.method:
docker``. For each app it records the newest registry tag whose shape
matches the pinned tag's shape (see :func:`select_upstream_tag`), plus
when it was checked, so the catalog API can expose:

    upstream_version            -- newest matching registry tag, or null
    upstream_pinned_version     -- the image tag it was compared against
    upstream_update_available   -- true when it is newer than the pin
    upstream_checked_at         -- epoch seconds of the last check

The pin is the image tag from ``install.image``, never the manifest's
``version:`` field: the two drift apart on real apps (code-server
declares ``version: 4.96.0`` while pinning a 4.135.0 image, uptime-kuma
declares ``1.0.0`` while pinning ``1.23``), so comparing an upstream
tag against the catalog version claims an update that no catalog
release can ever clear. Pre-release tags (``-rc``, ``-beta``) are not
releases: they are skipped unless the pin is itself a pre-release.

The request path NEVER calls the registry. The Store catalog endpoint
reads only the in-memory cache (persisted to
``data_dir/upstream_versions.json``) and returns immediately. A
background warmer refreshes entries on a daily TTL; a transient
network failure keeps the previous value and is retried on a short
TTL, so an outage reads as "last known", never as "no update".

Nothing here upgrades anything: detection only. The install path keeps
pinning whatever the catalog manifest says until the catalog itself is
updated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx
from packaging.version import InvalidVersion, Version

from tinyagentos.atomic_io import atomic_write_text

logger = logging.getLogger(__name__)

# Registry tags move at release cadence, so a daily check is plenty.
# The request path only reads the cache; the warmer refreshes entries
# whose TTL has expired. Transient failures retry sooner.
_CHECK_TTL = 24 * 60 * 60  # 24 hours: successful registry checks
_RETRY_TTL = 60 * 60  # 1 hour: network / registry failures
_FETCH_TIMEOUT = 10.0
_WARM_CONCURRENCY = 2

# Docker Hub's tag listing API. GHCR (and other registries) speak the
# Docker Registry v2 protocol: ``/v2/<name>/tags/list``.
_DOCKER_HUB_TAGS = "https://hub.docker.com/v2/repositories/{path}/tags"
_GHCR_TOKEN = "https://ghcr.io/token"
_REGISTRY_V2_TAGS = "https://{host}/v2/{path}/tags/list"

# A version-shaped tag: optional ``v`` prefix, dot-separated numerics,
# optional pre-release/build suffix (``1.23.1-beta``, ``2024.12.0``,
# ``v1.13.0``, ``20241115``). Anything else (``latest``, ``stable``,
# ``alpine``, ``bookworm``...) has no shape and never matches a pin.
_VERSION_TAG_RE = re.compile(r"^(v?)(\d+(?:\.\d+)*)(?:[-+][0-9A-Za-z.-]+)?$")

# app_id -> {"upstream_version": str | None, "checked_at": float,
#            "expires_at": float}
_cache: dict[str, dict[str, Any]] = {}

_cache_path: Path | None = None


# --------------------------------------------------------------------------- #
# Image reference parsing
# --------------------------------------------------------------------------- #


def parse_image_ref(image: str) -> tuple[str, str] | None:
    """Split an image reference into ``(path, tag)``.

    ``searxng/searxng:2024.12.0`` -> ``("searxng/searxng", "2024.12.0")``.
    The tag is the text after the last ``:`` that is followed by no
    ``/`` (so host:port registries and LXC-style refs like
    ``images:debian/bookworm`` are rejected, returning None).
    """
    if not image or ":" not in image:
        return None
    path, _, tag = image.rpartition(":")
    if not path or not tag or "/" in tag:
        return None
    return path, tag


def _registry_host(path: str) -> str | None:
    """Registry hostname for an image path, or None for Docker Hub.

    A leading component containing ``.`` or ``:`` (e.g. ``ghcr.io``,
    ``lscr.io``, ``registry:5000``) names an explicit registry; bare
    paths like ``searxng/searxng`` default to Docker Hub.
    """
    first = path.split("/", 1)[0]
    if "." in first or ":" in first:
        return first
    return None


def pinned_tag(app: Any) -> str | None:
    """The image tag a docker-installed catalog app pins, or None.

    This is the baseline every upstream comparison is made against.
    The manifest's ``version:`` field cannot serve: it is the catalog's
    own revision of the app and drifts from the image it installs
    (code-server ``version: 4.96.0`` with a ``:4.135.0`` image,
    uptime-kuma ``version: 1.0.0`` with a ``:1.23`` image). Returns
    None for non-docker apps and for image refs with no tag.
    """
    install = getattr(app, "install", None)
    if not isinstance(install, dict) or install.get("method") != "docker":
        return None
    image = install.get("image")
    if not isinstance(image, str) or not image:
        return None
    ref = parse_image_ref(image)
    return None if ref is None else ref[1]


def _hub_path(path: str) -> str:
    """Docker Hub repository path, prefixing official images with ``library/``."""
    if "/" not in path:
        return f"library/{path}"
    return path


# --------------------------------------------------------------------------- #
# Tag selection
# --------------------------------------------------------------------------- #


def tag_shape(tag: str) -> tuple[bool, int] | None:
    """Shape of a version-shaped tag: ``(v-prefix?, component count)``.

    ``2024.12.0`` -> ``(False, 3)``, ``v1.13.0`` -> ``(True, 3)``,
    ``1.22`` -> ``(False, 2)``, ``20241115`` -> ``(False, 1)``.
    Returns None for non-version tags (``latest``, ``stable``,
    ``alpine``...), which are never eligible.
    """
    match = _VERSION_TAG_RE.match(tag)
    if match is None:
        return None
    return (match.group(1) == "v", match.group(2).count(".") + 1)


def select_upstream_tag(pinned_tag: str, tags: list[str]) -> str | None:
    """Newest registry tag matching the pinned tag's shape.

    Only tags with the same shape as the pin are eligible, so a
    ``1.22`` pin is never compared against ``1.23.0`` tags and a
    ``v1.13.0`` pin never picks an unprefixed tag. ``latest`` and all
    other non-version tags are ignored. Pre-release tags (``-rc``,
    ``-beta``, ``-alpha``) are not releases and are skipped, unless the
    pin is itself a pre-release. Returns None when the pin is not
    version-shaped or no eligible tag exists.
    """
    pinned_shape = tag_shape(pinned_tag)
    if pinned_shape is None:
        return None
    try:
        pin_is_prerelease = Version(pinned_tag).is_prerelease
    except (InvalidVersion, TypeError):
        return None
    best: tuple[Version, str] | None = None
    for tag in tags:
        if tag_shape(tag) != pinned_shape:
            continue
        try:
            parsed = Version(tag)
        except (InvalidVersion, TypeError):
            continue
        if parsed.is_prerelease and not pin_is_prerelease:
            continue
        if best is None or parsed > best[0]:
            best = (parsed, tag)
    return best[1] if best is not None else None


# --------------------------------------------------------------------------- #
# Registry fetchers (background warmer only; the request path never calls)
# --------------------------------------------------------------------------- #


async def fetch_registry_tags(
    image: str, *, client: httpx.AsyncClient | None = None
) -> list[str] | None:
    """List tags for an image reference from its registry.

    Docker Hub uses its v2 repositories API (``ordering=last_updated``
    so the newest releases come first); GHCR and other registries use
    the Docker Registry v2 ``tags/list`` endpoint (GHCR via a bearer
    token from its token service). Returns None on any failure -- the
    caller records "unknown", never "no update".
    """
    ref = parse_image_ref(image)
    if ref is None:
        return None
    path, _tag = ref
    host = _registry_host(path)
    if host is not None:
        # An explicit registry names the host as the first path
        # component; the repository path is everything after it.
        path = path.split("/", 1)[1] if "/" in path else ""
    if host is None:
        return await _fetch_docker_hub_tags(path, client=client)
    if host == "ghcr.io":
        return await _fetch_ghcr_tags(path, client=client)
    return await _fetch_registry_v2_tags(host, path, client=client)


async def _fetch_docker_hub_tags(
    path: str, *, client: httpx.AsyncClient | None = None
) -> list[str] | None:
    """Docker Hub v2 repositories API: list tags for an image path.

    Returns a list of tag names, or None on any failure -- the caller
    records "unknown", never "no update". Docker Hub's tag listing API
    supports pagination (100 tags per page). This implementation follows
    the ``next`` link and accumulates all tags from every page, bounded to
    10 pages in practice.
    """
    repo = _hub_path(path)
    owns_client = client is None
    try:
        if owns_client:
            client = httpx.AsyncClient(timeout=_FETCH_TIMEOUT)
        assert client is not None
        
        tags: list[str] = []
        next_url = _DOCKER_HUB_TAGS.format(path=repo)
        page_count = 0
        max_pages = 10  # bounded page count
        
        while next_url and page_count < max_pages:
            # For the first page, pass query parameters; for subsequent pages, use the next URL which already has them
            if page_count == 0:
                resp = await client.get(
                    next_url,
                    params={"ordering": "last_updated", "page_size": 100},
                )
            else:
                resp = await client.get(next_url)
            if resp.status_code != 200:
                return None
            data = resp.json()
            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, list):
                return None
            page_tags = [
                t["name"]
                for t in results
                if isinstance(t, dict) and isinstance(t.get("name"), str)
            ]
            tags.extend(page_tags)
            
            # Get the next URL for pagination (already contains params)
            next_url = data.get("next")
            # SECURITY FIX: Only follow Docker Hub pagination links
            if isinstance(next_url, str) and not next_url.startswith("https://hub.docker.com/"):
                # Stop paginating for external hosts, return tags gathered so far
                return tags
            # Validate next_url is a string
            if not isinstance(next_url, str):
                next_url = None
            page_count += 1
            
        return tags
    except Exception as exc:  # network error, timeout, bad JSON
        logger.debug("docker hub tag fetch failed for %s: %s", repo, exc)
        return None
    finally:
        if owns_client and client is not None:
            await client.aclose()


# --------------------------------------------------------------------------- #
# Test: Security fix for Docker Hub pagination
# --------------------------------------------------------------------------- #


async def _fetch_registry_v2_tags(
    host: str, path: str, *, client: httpx.AsyncClient | None = None
) -> list[str] | None:
    """Plain Docker Registry v2 ``tags/list`` (unauthenticated)."""
    owns_client = client is None
    try:
        if owns_client:
            client = httpx.AsyncClient(timeout=_FETCH_TIMEOUT)
        assert client is not None
        resp = await client.get(
            _REGISTRY_V2_TAGS.format(host=host, path=path)
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        tags = data.get("tags") if isinstance(data, dict) else None
        if not isinstance(tags, list):
            return None
        return [t for t in tags if isinstance(t, str)]
    except Exception as exc:
        logger.debug("registry v2 tag fetch failed for %s/%s: %s", host, path, exc)
        return None
    finally:
        if owns_client and client is not None:
            await client.aclose()


async def _fetch_ghcr_tags(
    path: str, *, client: httpx.AsyncClient | None = None
) -> list[str] | None:
    """GHCR: bearer token from the token service, then v2 ``tags/list``."""
    owns_client = client is None
    try:
        if owns_client:
            client = httpx.AsyncClient(timeout=_FETCH_TIMEOUT)
        assert client is not None
        token: str | None = None
        token_resp = await client.get(
            _GHCR_TOKEN,
            params={"scope": f"repository:{path}:pull", "service": "ghcr.io"},
        )
        if token_resp.status_code == 200:
            token_data = token_resp.json()
            if isinstance(token_data, dict):
                raw_token = token_data.get("token")
                if isinstance(raw_token, str):
                    token = raw_token
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = await client.get(
            _REGISTRY_V2_TAGS.format(host="ghcr.io", path=path),
            headers=headers,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        tags = data.get("tags") if isinstance(data, dict) else None
        if not isinstance(tags, list):
            return None
        return [t for t in tags if isinstance(t, str)]
    except Exception as exc:
        logger.debug("ghcr tag fetch failed for %s: %s", path, exc)
        return None
    finally:
        if owns_client and client is not None:
            await client.aclose()


# --------------------------------------------------------------------------- #
# Cache (request path) + warmer (background)
# --------------------------------------------------------------------------- #


def upstream_info(app_id: str) -> dict[str, Any] | None:
    """Last-known upstream state for ``app_id``, or None when unknown.

    Never performs network I/O: this is what the request path reads.
    ``upstream_version`` is None when nothing has been determined yet
    (never checked, or every check failed). ``pinned_version`` is the
    image tag that version was compared against.
    """
    entry = _cache.get(app_id)
    if entry is None:
        return None
    return {
        "upstream_version": entry["upstream_version"],
        "pinned_version": entry.get("pinned_version"),
        "upstream_checked_at": entry["checked_at"],
    }


def compare_versions(catalog_version: str, upstream_version: str | None) -> bool | None:
    """True when ``upstream_version`` is newer than the pinned version.

    Callers pass the pinned image tag (see :func:`pinned_tag`), not the
    manifest's ``version:`` field. None means unknown (no upstream
    version, or a version string that does not parse) -- which must not
    read as "no update".
    """
    if upstream_version is None:
        return None
    try:
        pinned = Version(catalog_version)
        upstream = Version(upstream_version)
    except (InvalidVersion, TypeError):
        return None
    return upstream > pinned


def record_upstream(
    app_id: str,
    upstream_version: str | None,
    *,
    ok: bool,
    pinned_version: str | None = None,
) -> None:
    """Record the outcome of one upstream check in the cache.

    ``ok`` marks whether the registry was actually reached: a reached
    registry with no matching tag is a stable answer (24h TTL), while
    a network failure is transient (1h retry TTL) so the warmer asks
    again soon. A transient failure KEEPS the last known version (and
    its pin) rather than wiping it, so a short outage does not make a
    real update vanish from the Store. The cache is persisted on every
    record so a cold boot reloads the last known versions instead of
    reading "unknown".
    """
    now = time.time()
    previous = _cache.get(app_id)
    if not ok and previous is not None:
        if upstream_version is None:
            upstream_version = previous["upstream_version"]
        if pinned_version is None:
            pinned_version = previous.get("pinned_version")
    _cache[app_id] = {
        "upstream_version": upstream_version,
        "pinned_version": pinned_version,
        "checked_at": now,
        "expires_at": now + (_CHECK_TTL if ok else _RETRY_TTL),
    }
    _persist_cache()


def _has_fresh_entry(app_id: str) -> bool:
    entry = _cache.get(app_id)
    return entry is not None and time.time() < entry["expires_at"]


async def check_upstream(
    app: Any, *, client: httpx.AsyncClient | None = None
) -> tuple[bool, str | None]:
    """Check one docker-installed catalog app against its registry.

    Returns ``(registry_reached, upstream_tag)``. Non-docker apps (or
    non-version-shaped pins) return ``(True, None)`` -- nothing to
    check, not a failure. Never raises.
    """
    tag = pinned_tag(app)
    if tag is None:
        return True, None
    image = app.install["image"]
    tags = await fetch_registry_tags(image, client=client)
    if tags is None:
        return False, None
    return True, select_upstream_tag(tag, tags)


async def warm_upstream_cache(
    apps: list[Any], *, client: httpx.AsyncClient | None = None
) -> None:
    """Refresh upstream versions for docker-image apps, bounded concurrency.

    Skips apps whose cache entry is still fresh so a pass over the
    catalog costs at most one request per app per day. Designed to be
    called on a schedule by a background loop; never raises.
    """
    docker_apps = [
        a for a in apps
        if isinstance(getattr(a, "install", None), dict)
        and a.install.get("method") == "docker"
        and isinstance(a.install.get("image"), str)
    ]
    stale = [a for a in docker_apps if not _has_fresh_entry(a.id)]
    if not stale:
        return

    owns_client = client is None
    sem = asyncio.Semaphore(_WARM_CONCURRENCY)

    async def _one(app: Any) -> None:
        pin = pinned_tag(app)
        async with sem:
            try:
                reached, version = await check_upstream(app, client=client)
            except Exception as exc:  # noqa: BLE001 -- never kill the loop
                logger.debug("upstream check failed for %s: %s", app.id, exc)
                record_upstream(app.id, None, ok=False, pinned_version=pin)
                return
            record_upstream(app.id, version, ok=reached, pinned_version=pin)

    try:
        if owns_client:
            client = httpx.AsyncClient(timeout=_FETCH_TIMEOUT)
        await asyncio.gather(*(_one(a) for a in stale))
    finally:
        if owns_client and client is not None:
            await client.aclose()


def configure_persistence(data_dir: Path) -> None:
    """Point the cache at ``data_dir/upstream_versions.json`` and load it.

    Idempotent. Safe to call before the warmer starts so a cold boot
    reuses the last known versions instead of showing "unknown" until
    the first pass completes.
    """
    global _cache_path
    _cache_path = Path(data_dir) / "upstream_versions.json"
    _load_cache()


def _load_cache() -> None:
    if _cache_path is None or not _cache_path.exists():
        return
    try:
        raw = json.loads(_cache_path.read_text())
    except Exception as exc:
        logger.debug("upstream versions cache load failed: %s", exc)
        return
    if not isinstance(raw, dict):
        return
    for app_id, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        try:
            _cache[str(app_id)] = {
                "upstream_version": entry["upstream_version"],
                # Absent in cache files written before the pin was
                # recorded; the request path falls back to the manifest.
                "pinned_version": entry.get("pinned_version"),
                "checked_at": float(entry["checked_at"]),
                "expires_at": float(entry["expires_at"]),
            }
        except (KeyError, TypeError, ValueError):
            continue


def _persist_cache(snapshot: dict | None = None) -> None:
    if _cache_path is None:
        return
    if snapshot is None:
        snapshot = {k: v for k, v in _cache.items()}
    try:
        atomic_write_text(_cache_path, json.dumps(snapshot))
    except Exception as exc:
        logger.debug("upstream versions cache persist failed: %s", exc)


def _reset_cache_for_tests() -> None:
    """Clear the cache and persistence path. For tests only."""
    global _cache_path
    _cache.clear()
    _cache_path = None
