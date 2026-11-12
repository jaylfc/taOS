"""Thin async Keenable client: web search and page fetch.

Keenable serves the same API keyed (``X-API-Key`` header) and keyless (the
``/public`` endpoints, rate limited per IP, which require an
``X-Keenable-Title`` app identifier). The broker injects the real key; an
empty key falls back to the public endpoints so a missing secret degrades to
the shared pool instead of failing outright.
"""

from __future__ import annotations

import httpx

KEENABLE_BASE_URL = "https://api.keenable.ai"
KEENABLE_SEARCH_URL = f"{KEENABLE_BASE_URL}/v1/search"
KEENABLE_FETCH_URL = f"{KEENABLE_BASE_URL}/v1/fetch"
KEENABLE_APP_TITLE = "taOS"


def _endpoint(url: str, api_key: str) -> tuple[str, dict[str, str]]:
    if api_key:
        return url, {"X-API-Key": api_key}
    return f"{url}/public", {"X-Keenable-Title": KEENABLE_APP_TITLE}


async def keenable_search(api_key: str, query: str, num_results: int = 5) -> list[dict]:
    """Run a Keenable web search and return the raw ``results`` list.

    Raises ``RuntimeError`` on a non-200 status.
    """
    url, headers = _endpoint(KEENABLE_SEARCH_URL, api_key)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            url,
            headers=headers,
            json={"query": query, "max_results": num_results},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Keenable search failed: {resp.status_code} {resp.text[:200]}")
    return resp.json().get("results", [])


async def keenable_fetch(api_key: str, page_url: str, max_chars: int | None = None) -> dict:
    """Fetch a page as clean markdown. Returns ``{url, title, content, ...}``.

    Raises ``RuntimeError`` on a non-200 status.
    """
    url, headers = _endpoint(KEENABLE_FETCH_URL, api_key)
    params: dict[str, str | int] = {"url": page_url}
    if max_chars:
        params["max_chars"] = max_chars
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        raise RuntimeError(f"Keenable fetch failed: {resp.status_code} {resp.text[:200]}")
    return resp.json()
