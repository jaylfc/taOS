"""Thin async Exa search client."""

from __future__ import annotations

import httpx

_BASE_URL = "https://api.exa.ai"


async def exa_search(api_key: str, query: str, num_results: int = 5) -> list[dict]:
    """Search via the Exa neural search API.

    Returns the raw ``results`` list from the API response.
    Raises ``RuntimeError`` on non-200 status.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_BASE_URL}/search",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "numResults": num_results},
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Exa search failed: {resp.status_code} {resp.text[:200]}"
        )
    return resp.json().get("results", [])
