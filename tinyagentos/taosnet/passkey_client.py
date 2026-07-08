"""Headless taOSnet passkey fetch (Phase 2 of the model torrent mesh).

A background model download has no browser session cookie, so it cannot use
the cookie-authenticated passkey path the desktop uses. Instead the taOS
controller stores a ``controller_token`` minted at cluster-join (bound to the
account host row, scoped to ``taosnet:passkey`` only) and presents it as a
Bearer credential to fetch this account's taOSnet passkey.

Contract (taos.my, see docs and jaylfc/taos-website#83):

- ``GET /api/taosnet/passkey`` with ``Authorization: Bearer <controller_token>``
- ``200 {"passkey": "<opaque>"}`` when the account has a passkey issued
- ``200 {"passkey": null}`` when none has been issued yet
- ``401`` when the token is missing, invalid, or revoked (host row gone)

Fallback rule (mirrors the DownloadManager): a null passkey, a 401, or any
transport failure all mean "web-seed only" (no taOSnet tracker). A non-null
passkey enables the private taOSnet tracker alongside the BEP-19 web seeds.
This module never raises, so a passkey lookup can never block a download.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_BASE = "https://taos.my"


def taosnet_base() -> str:
    """The taos.my base URL for account API calls, overridable in tests and
    self-hosted setups via ``TAOS_TAOSNET_BASE``."""
    return os.getenv("TAOS_TAOSNET_BASE", _DEFAULT_BASE).rstrip("/")


def get_controller_token() -> Optional[str]:
    """The controller token persisted when this host joined an account mesh,
    or None if it has not joined one.

    Read from ``TAOS_CONTROLLER_TOKEN`` for now; this is the seam the
    cluster-join client writes to (alongside the Headscale key) once that
    client lands. None means the headless passkey fetch is skipped and the
    download stays on the web-seed baseline.
    """
    return os.getenv("TAOS_CONTROLLER_TOKEN") or None


async def fetch_passkey(
    controller_token: Optional[str],
    *,
    base: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = 15.0,
) -> Optional[str]:
    """Fetch the account taOSnet passkey using the controller token.

    Returns the passkey string, or None when there is no token, the account
    has no passkey yet, the token is unauthorized/revoked (401), or any
    transport error occurs. None always means "fall back to web-seed only".
    Never raises, so a download is never blocked by a passkey lookup.

    ``client`` lets a caller (or a test) inject an ``httpx.AsyncClient``;
    otherwise a short-lived one is created for the single request.
    """
    if not controller_token:
        return None
    url = f"{base or taosnet_base()}/api/taosnet/passkey"
    headers = {"Authorization": f"Bearer {controller_token}"}
    try:
        if client is not None:
            resp = await client.get(url, headers=headers, timeout=timeout)
        else:
            async with httpx.AsyncClient(timeout=timeout) as owned:
                resp = await owned.get(url, headers=headers)
        if resp.status_code == 401:
            logger.info(
                "taosnet passkey fetch: 401 (token missing/invalid/revoked), "
                "using web seeds only"
            )
            return None
        resp.raise_for_status()
        return resp.json().get("passkey") or None
    except Exception as exc:  # noqa: BLE001 - any failure means web-seed fallback
        logger.warning(
            "taosnet passkey fetch failed (%s), using web seeds only", exc
        )
        return None
