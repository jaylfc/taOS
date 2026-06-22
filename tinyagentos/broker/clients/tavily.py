"""Tavily search client: thin async wrapper around the Tavily search API."""

from __future__ import annotations

import httpx

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


async def tavily_search(api_key: str, query: str, num_results: int = 5) -> list[dict]:
    """Run a Tavily search and return the raw results list.

    Args:
        api_key: Tavily API key, sent in the JSON body (not a header).
        query: Search query string.
        num_results: Maximum number of results to return.

    Returns:
        A list of result dicts from the ``results`` key of the response.

    Raises:
        RuntimeError: If the API returns a non-200 status code.
    """
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": num_results,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(TAVILY_SEARCH_URL, json=payload)
    if resp.status_code != 200:
        body = resp.text[:200]
        raise RuntimeError(f"Tavily search failed: {resp.status_code} {body}")
    data = resp.json()
    return data.get("results", [])
