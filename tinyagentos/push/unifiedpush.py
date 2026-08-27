from __future__ import annotations

import json
import logging
from typing import Protocol
from urllib.parse import urlparse

import httpx

from tinyagentos.routes.desktop_browser.ssrf import SsrfBlockedError, validate_url_or_raise

logger = logging.getLogger(__name__)


class UnifiedPushSender(Protocol):
    async def send(self, push_token: str, payload: dict) -> bool:
        ...

    async def aclose(self) -> None:
        ...


class NullUnifiedPushSender:
    async def send(self, push_token: str, payload: dict) -> bool:
        logger.info("UnifiedPush not configured; dropping push to %s", push_token[:8])
        return False

    async def aclose(self) -> None:
        return None


def build_unifiedpush_payload(
    *, title: str, body: str, data: dict | None = None, actions: list[dict] | None = None
) -> dict:
    payload: dict = {"title": title, "body": body}
    if data:
        payload["data"] = data
    if actions:
        payload["actions"] = actions
    return payload


def _actions_for_row(row: dict) -> list[dict] | None:
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    actions = data.get("actions")
    if isinstance(actions, list) and actions:
        return actions
    decision_type = data.get("decision_type")
    if decision_type == "approve_deny":
        return [{"id": "approve", "label": "Approve"}, {"id": "deny", "label": "Deny"}]
    if decision_type in ("single_select", "multi_select"):
        opts = data.get("options") or []
        return [{"id": o.get("value", o.get("label", "")), "label": o.get("label", "")} for o in opts]
    if decision_type == "free_text":
        return [{"id": "quick_reply", "label": "Reply"}]
    return None


class HttpUnifiedPushSender:
    def __init__(self, *, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def send(self, push_token: str, payload: dict) -> bool:
        try:
            parsed = urlparse(push_token)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("invalid push_token URL")
            validate_url_or_raise(push_token, allow_private=True)
            resp = await self._client.post(
                push_token,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, SsrfBlockedError):
            logger.warning("UnifiedPush send failed for %s", push_token[:8], exc_info=True)
            return False
        return resp.status_code == 200

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
