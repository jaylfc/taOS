from __future__ import annotations

import json
import re

import httpx
from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse

router = APIRouter()

WALLHAVEN_BASE = "https://wallhaven.cc/api/v1"
CATEGORY_PURITY_RE = re.compile(r"^[01]{3}$")
_VALID_SORTING = frozenset(
    {"relevance", "random", "date_added", "views", "favorites", "toplist"}
)
_MAX_RESPONSE_BYTES = 1_000_000  # 1 MB cap


@router.get("/api/wallhaven/search")
async def wallhaven_search(
    request: Request,
    q: str = Query(
        default="",
        max_length=200,
        description="Search term",
    ),
    page: int = Query(default=1, ge=1, description="Page number"),
    categories: str = Query(
        default="111", description="Category flags (3 chars: general/anime/people)"
    ),
    purity: str = Query(
        default="100", description="Purity flags (3 chars: sfw/sketchy/nsfw)"
    ),
    sorting: str = Query(
        default="relevance",
        max_length=200,
        description="Sort order",
    ),
):
    """Proxy search to Wallhaven API.

    Keyless by default (~45 req/min). Pass WALLHAVEN_API_KEY env var for higher
    rate limits and NSFW access.
    """
    if not CATEGORY_PURITY_RE.match(categories):
        return JSONResponse(
            {"error": "categories must be 3 characters of 0 or 1"},
            status_code=400,
        )
    if not CATEGORY_PURITY_RE.match(purity):
        return JSONResponse(
            {"error": "purity must be 3 characters of 0 or 1"},
            status_code=400,
        )
    if sorting not in _VALID_SORTING:
        return JSONResponse(
            {
                "error": f"sorting must be one of {', '.join(sorted(_VALID_SORTING))}"
            },
            status_code=400,
        )

    config = request.app.state.config
    api_key: str | None = getattr(config, "wallhaven_api_key", None)

    headers: dict[str, str] = {}
    if api_key:
        headers["X-API-Key"] = api_key

    params: dict[str, str | int] = {
        "q": q,
        "page": page,
        "categories": categories,
        "purity": purity,
        "sorting": sorting,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            async with client.stream(
                "GET",
                f"{WALLHAVEN_BASE}/search",
                params=params,
                headers=headers,
            ) as resp:
                if resp.status_code == 429:
                    return JSONResponse(
                        {
                            "error": (
                                "Rate limited by Wallhaven. "
                                "Wait a moment and try again."
                            )
                        },
                        status_code=429,
                    )

                if resp.status_code != 200:
                    return JSONResponse(
                        {"error": f"Wallhaven returned {resp.status_code}"},
                        status_code=502,
                    )

                body = b""
                async for chunk in resp.aiter_bytes():
                    body += chunk
                    if len(body) > _MAX_RESPONSE_BYTES:
                        return JSONResponse(
                            {"error": "Wallhaven response too large"},
                            status_code=502,
                        )

                try:
                    data = json.loads(body)
                except (ValueError, json.JSONDecodeError):
                    return JSONResponse(
                        {"error": "Wallhaven returned invalid JSON"},
                        status_code=502,
                    )
                return JSONResponse(data)

    except httpx.TimeoutException:
        return JSONResponse(
            {"error": "Wallhaven API timed out. Please try again."},
            status_code=504,
        )
    except httpx.RequestError:
        return JSONResponse(
            {"error": "Cannot reach Wallhaven API. Check your internet connection."},
            status_code=502,
        )
