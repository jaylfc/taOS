"""HTTP routes for taOS Orb status (S2) -- the controller side of
docs/taosusb-plan.md's S2 section.

Admin-gated exactly like ``/api/cluster/ble/scan`` (routes/cluster_ble.py):
a session cookie for an admin user, checked with the same ``_require_admin``
helper -- not the broader ``require_admin`` that also accepts the host
local token, since these routes reveal attached hardware.

Returns plain ``port``, ``vid``, ``pid``, ``serial_number``, ``description``
fields, no signing key, no mesh_preauth.

GET /api/orb/catalog returns the signed Orb catalog (spec s10).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.orb.catalog_fetch import fetch_catalog, load_cached_catalog
from tinyagentos.orb.catalog_sig import (
    CatalogFetchError,
    CatalogSignatureMissing,
    CatalogKeyUnknown,
    CatalogSignatureInvalid,
    CatalogSerialRegression,
    CatalogStateCorrupt,
    default_state_path,
)
from tinyagentos.orb.discovery import browse_lan_orbs, list_usb_orbs, probe_agent_buddy
from tinyagentos.orb.frames import TransportUnavailable
from tinyagentos.routes.auth import _require_admin

router = APIRouter()


@router.get("/api/orb/status")
async def orb_status(request: Request):
    ok, err = _require_admin(request)
    if not ok:
        return err
    try:
        orbs = await asyncio.to_thread(list_usb_orbs)
    except TransportUnavailable:
        return JSONResponse({"error": "orb_transport_unavailable"}, status_code=503)
    return {"orbs": orbs}


@router.get("/api/orb/lan")
async def orb_lan(request: Request):
    ok, err = _require_admin(request)
    if not ok:
        return err
    results = await asyncio.gather(
        asyncio.to_thread(browse_lan_orbs),
        asyncio.to_thread(probe_agent_buddy),
        return_exceptions=True,
    )
    lan_res, buddy_res = results[0], results[1]
    lan_orbs = []
    agent_buddy = []
    errors = []
    if isinstance(lan_res, Exception):
        if isinstance(lan_res, (TransportUnavailable, OSError)):
            lan_orbs = []
            errors.append({"source": "mdns", "error": "unavailable"})
        else:
            raise lan_res
    else:
        lan_orbs = lan_res
    if isinstance(buddy_res, Exception):
        if isinstance(buddy_res, (TransportUnavailable, OSError)):
            agent_buddy = []
            errors.append({"source": "agent_buddy", "error": "unavailable"})
        else:
            raise buddy_res
    else:
        agent_buddy = buddy_res
    return {"lan_orbs": lan_orbs, "agent_buddy": agent_buddy, "errors": errors}


@router.get("/api/orb/catalog")
async def orb_catalog(request: Request):
    ok, err = _require_admin(request)
    if not ok:
        return err

    data_dir = request.app.state.data_dir
    cache_dir = Path(data_dir) / "orb" / "catalog"
    state_path = default_state_path(data_dir)

    base = ((request.app.state.config.orb or {}).get("catalog_url") or "").strip()

    # Misconfigured: non-empty but not https:// -> 503 immediately, no fallback
    if base and not base.startswith("https://"):
        return JSONResponse({"error": "orb_catalog_misconfigured"}, status_code=503)

    source = "remote" if base else "cache"

    try:
        if base:
            cat = await fetch_catalog(base, cache_dir, state_path)
        else:
            cat = await asyncio.to_thread(load_cached_catalog, cache_dir, state_path)
    except CatalogFetchError:
        if base:
            # Only fall back to cache on CatalogFetchError from remote fetch
            try:
                cat = await asyncio.to_thread(load_cached_catalog, cache_dir, state_path)
                source = "cache"
            except CatalogFetchError:
                return JSONResponse({"error": "orb_catalog_fetch_failed"}, status_code=503)
            except (
                CatalogSignatureMissing,
                CatalogKeyUnknown,
                CatalogSignatureInvalid,
                CatalogSerialRegression,
                CatalogStateCorrupt,
            ) as e:
                error_map = {
                    CatalogSignatureMissing: "orb_catalog_signature_missing",
                    CatalogKeyUnknown: "orb_catalog_key_unknown",
                    CatalogSignatureInvalid: "orb_catalog_signature_invalid",
                    CatalogSerialRegression: "orb_catalog_serial_regression",
                    CatalogStateCorrupt: "orb_catalog_state_corrupt",
                }
                return JSONResponse({"error": error_map[type(e)]}, status_code=503)
        else:
            # base empty and cache fetch failed
            return JSONResponse({"error": "orb_catalog_unconfigured"}, status_code=503)
    except (
        CatalogSignatureMissing,
        CatalogKeyUnknown,
        CatalogSignatureInvalid,
        CatalogSerialRegression,
        CatalogStateCorrupt,
    ) as e:
        # Signature/key/serial/state errors from remote fetch never fall back to cache
        error_map = {
            CatalogSignatureMissing: "orb_catalog_signature_missing",
            CatalogKeyUnknown: "orb_catalog_key_unknown",
            CatalogSignatureInvalid: "orb_catalog_signature_invalid",
            CatalogSerialRegression: "orb_catalog_serial_regression",
            CatalogStateCorrupt: "orb_catalog_state_corrupt",
        }
        return JSONResponse({"error": error_map[type(e)]}, status_code=503)

    # Read serial from the verified cache file
    catalog_json = cache_dir / "catalog.json"
    serial = json.loads(catalog_json.read_bytes())["serial"]

    # Build response without file paths or extents
    apps_out = []
    for app in cat.apps.values():
        versions_out = []
        for v in app.get("versions", []):
            versions_out.append({"version": v.get("version"), "yanked": bool(v.get("yanked"))})
        apps_out.append({
            "id": app["id"],
            "name": app.get("name", app["id"]),
            "versions": versions_out,
        })

    return {"source": source, "serial": serial, "apps": apps_out}
