"""Thin sync HTTP wrapper using httpx. Auth and base-URL applied automatically."""
from __future__ import annotations

import httpx

from taosctl import config


def _client() -> httpx.Client:
    headers = {}
    token = config.resolve_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(
        base_url=config.resolve_url(),
        headers=headers,
        timeout=30.0,
    )


def get(path: str, **kwargs):
    with _client() as c:
        r = c.get(path, **kwargs)
        r.raise_for_status()
        return r.json()


def post(path: str, **kwargs):
    with _client() as c:
        r = c.post(path, **kwargs)
        r.raise_for_status()
        return r.json()


def delete(path: str, **kwargs):
    with _client() as c:
        r = c.delete(path, **kwargs)
        r.raise_for_status()
        return r.status_code
