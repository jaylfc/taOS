"""Authenticated HTTP client for taosctl. Stdlib only (urllib), so the CLI
installs and runs anywhere with zero extra dependencies.

Config resolution (first match wins) for base URL and token:
  1. explicit --url / --token flags (passed in by __main__)
  2. env TAOS_URL / TAOS_TOKEN  (how an agent container has its token injected)
  3. ~/.config/taosctl/config.json  ({"url": ..., "token": ...})
  4. url defaults to http://127.0.0.1:6969; token may be absent (anonymous)

The token is sent as ``Authorization: Bearer <token>`` (the header the taOS
auth middleware accepts in place of a session cookie).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

DEFAULT_URL = "http://127.0.0.1:6969"
CONFIG_PATH = Path(os.path.expanduser("~/.config/taosctl/config.json"))


class ApiError(Exception):
    """Raised on a non-2xx API response. Carries the HTTP status and the
    server's actionable message (from the JSON error/detail field if present)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class TransportError(Exception):
    """Raised on a local/transport failure (connection refused, DNS, etc.)."""


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def resolve(url: Optional[str], token: Optional[str]) -> tuple[str, Optional[str]]:
    cfg = _load_config()
    base = url or os.environ.get("TAOS_URL") or cfg.get("url") or DEFAULT_URL
    tok = token or os.environ.get("TAOS_TOKEN") or cfg.get("token")
    return base.rstrip("/"), tok


class TaosClient:
    def __init__(self, url: Optional[str] = None, token: Optional[str] = None, timeout: float = 30.0):
        self.base_url, self.token = resolve(url, token)
        self.timeout = timeout

    def request(self, method: str, path: str, params: Optional[dict] = None,
                body: Optional[Any] = None) -> Any:
        full = self.base_url + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                full = full + "?" + urllib.parse.urlencode(clean)
        data = None
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(full, data=data, method=method.upper(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read() if exc.fp else b""
            raise ApiError(exc.code, _extract_error(raw, exc.code)) from None
        except urllib.error.URLError as exc:
            raise TransportError(f"cannot reach {self.base_url}: {exc.reason}") from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return raw.decode(errors="replace")

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, body: Optional[Any] = None, params: Optional[dict] = None,
             json: Optional[Any] = None) -> Any:
        # `json` is accepted as an alias for `body`: it is the conventional kwarg
        # for a JSON payload in requests/httpx, so callers reach for it by habit.
        # `body` wins if both are given.
        return self.request("POST", path, params=params, body=body if body is not None else json)

    def put(self, path: str, body: Optional[Any] = None, json: Optional[Any] = None) -> Any:
        return self.request("PUT", path, body=body if body is not None else json)

    def patch(self, path: str, body: Optional[Any] = None, json: Optional[Any] = None) -> Any:
        return self.request("PATCH", path, body=body if body is not None else json)

    def delete(self, path: str, params: Optional[dict] = None) -> Any:
        return self.request("DELETE", path, params=params)


def _extract_error(raw: bytes, status: int) -> str:
    try:
        doc = json.loads(raw)
        if isinstance(doc, dict):
            for key in ("error", "detail", "message"):
                if doc.get(key):
                    return str(doc[key])
    except Exception:
        pass
    text = raw.decode(errors="replace").strip()
    return text or f"HTTP {status}"
