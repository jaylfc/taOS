"""Orb catalog fetch + cached re-verify (spec s10)."""
import asyncio
from pathlib import Path

import httpx

from tinyagentos import atomic_io
from tinyagentos.orb import catalog
from tinyagentos.orb.catalog_sig import (
    CatalogFetchError,
    CatalogSignatureMissing,
    CatalogSerialRegression,
    CatalogSignatureInvalid,
    CatalogKeyUnknown,
    accept_catalog,
)

CATALOG_MAX_BYTES = 1 << 20
SIG_MAX_BYTES = 256


async def _get_capped(client, url: str, cap: int) -> bytes | None:
    try:
        async with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as resp:
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                raise CatalogFetchError(f"failed to fetch {url}: status {resp.status_code}")
            content_encoding = resp.headers.get("content-encoding", "")
            if content_encoding and content_encoding.lower() != "identity":
                raise CatalogFetchError(f"server returned compressed response for {url}: content-encoding {content_encoding}")
            total = bytearray()
            async for chunk in resp.aiter_bytes():
                total.extend(chunk)
                if len(total) > cap:
                    raise CatalogFetchError(f"response from {url} exceeds cap")
            return bytes(total)
    except CatalogFetchError:
        raise
    except httpx.HTTPError as e:
        raise CatalogFetchError(f"failed to fetch {url}: {e}") from e


async def fetch_catalog(base_url: str, cache_dir, state_path, keys=None, timeout: float = 10.0, transport=None):
    if not base_url or not base_url.startswith("https://"):
        raise CatalogFetchError("base_url must start with https://")
    base = base_url.rstrip("/")
    catalog_url = f"{base}/catalog.json"
    sig_url = f"{base}/catalog.json.sig"

    # httpx's timeout is per read, so a server dripping a byte at a time never
    # trips it; bound the whole fetch instead.
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, transport=transport) as client:
                catalog_bytes = await _get_capped(client, catalog_url, CATALOG_MAX_BYTES)
                if catalog_bytes is None:
                    raise CatalogFetchError("catalog not found")
                sig_bytes = await _get_capped(client, sig_url, SIG_MAX_BYTES)
                if sig_bytes is None:
                    sig_bytes = None  # so accept_catalog raises CatalogSignatureMissing
    except TimeoutError as e:
        raise CatalogFetchError(f"catalog fetch exceeded {timeout}s") from e

    # accept_catalog records the serial before the cache is written. If the
    # write then fails, state is ahead of the cache and load_cached_catalog
    # refuses the older cache as a regression: fails closed, refetch recovers.
    return await asyncio.to_thread(
        _accept_and_cache, catalog_bytes, sig_bytes, state_path, keys, cache_dir
    )


def _accept_and_cache(catalog_bytes, sig_bytes, state_path, keys, cache_dir):
    # accept_catalog records the serial before the cache is written. If the
    # write then fails, state is ahead of the cache and load_cached_catalog
    # refuses the older cache as a regression: fails closed, refetch recovers.
    accept_catalog(catalog_bytes, sig_bytes, state_path, keys)

    cache_dir_path = Path(cache_dir)
    cache_dir_path.mkdir(parents=True, exist_ok=True)
    atomic_io.atomic_write_bytes(cache_dir_path / "catalog.json", catalog_bytes)
    atomic_io.atomic_write_bytes(cache_dir_path / "catalog.json.sig", sig_bytes if sig_bytes is not None else b"")

    return catalog.load_catalog(str(cache_dir_path / "catalog.json"))


def load_cached_catalog(cache_dir, state_path, keys=None):
    cache_dir_path = Path(cache_dir)
    catalog_path = cache_dir_path / "catalog.json"
    sig_path = cache_dir_path / "catalog.json.sig"

    if not catalog_path.exists():
        raise CatalogFetchError("no cached catalog")

    with catalog_path.open("rb") as f:
        catalog_bytes = f.read(CATALOG_MAX_BYTES + 1)
    if len(catalog_bytes) > CATALOG_MAX_BYTES:
        raise CatalogFetchError("cached catalog exceeds cap")

    if not sig_path.exists():
        sig_bytes = None
    else:
        with sig_path.open("rb") as f:
            sig_bytes = f.read(SIG_MAX_BYTES + 1)
        if len(sig_bytes) > SIG_MAX_BYTES:
            raise CatalogFetchError("cached catalog signature exceeds cap")
        if len(sig_bytes) == 0:
            sig_bytes = None

    accept_catalog(catalog_bytes, sig_bytes, state_path, keys)
    return catalog.load_catalog(str(catalog_path))
