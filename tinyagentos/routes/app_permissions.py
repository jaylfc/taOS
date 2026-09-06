"""Per-app capability grant API (app permission system, #56).

The API surface over AppGrantsStore: a user reviews, grants/denies, and revokes
the capabilities an installed app holds. Decision 6 (manifest declares + runtime
grants via the Decisions/consent flow); this is the runtime-grant layer.

Grants are validated against the closed capability vocabulary in
tinyagentos/userspace/capabilities.py (the same source of truth the broker
enforces and the package parser validates manifests against), so a grant can
never record a typo'd or made-up capability. Grants are scoped to the calling
user.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from tinyagentos.routes.decisions import SERVER_RAISED_KEY

from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.userspace.capabilities import (
    FREE_CAPS,
    NET_PREFIX,
    capability_allowed,
    capability_ceiling,
    default_provenance_for_trust,
    describe_capability,
    is_known_capability,
    is_valid_network_grant,
)

router = APIRouter()


async def _resolve_provenance(request: Request, app_id: str) -> str | None:
    """The app's provenance tier, if `app_id` is a classified userspace app.

    None if `app_id` doesn't match any row in the userspace apps store -- most
    callers of this generic grant ledger are not sandboxed userspace apps at
    all (native features requesting a capability on their own behalf), so
    their pending-consent computation is left exactly as before rather than
    guessed at.
    """
    store = getattr(request.app.state, "userspace_apps", None)
    # Uninitialised (e.g. a test app that never ran the userspace lifespan) is
    # treated the same as "no store" -- best effort, never a 500.
    if store is None or getattr(store, "_db", None) is None:
        return None
    row = await store.get(app_id)
    if row is None:
        return None
    return row.get("provenance") or default_provenance_for_trust(row.get("trust"))


def app_grant_decision_payload(app_id: str, capabilities: list[str]) -> dict:
    """Build the grant-on-install consent Decision for an app (#56, decision 6).

    A multi_select card where each requested capability is an option the user
    grants or leaves unchecked; metadata.kind == "app_grant" routes the answer
    to the app_grants ledger (see _apply_app_grant in routes/decisions.py).
    Returned as kwargs for DecisionStore.create / POST /api/decisions. The
    install flow constructs and posts this; it is not wired into install yet."""
    return {
        "from_agent": "@taos-app-install",
        "question": f"{app_id} would like these permissions",
        "type": "multi_select",
        "priority": "blocking",
        "options": [
            {"label": describe_capability(c), "value": c} for c in capabilities
        ],
        "metadata": {
            SERVER_RAISED_KEY: True,
            "kind": "app_grant",
            "app_id": app_id,
            "capabilities": list(capabilities),
        },
    }


class GrantIn(BaseModel):
    capability: str
    decision: str = "granted"  # "granted" or "denied"


class RevokeIn(BaseModel):
    capability: str


@router.get("/api/apps/{app_id}/permissions")
async def list_app_permissions(
    app_id: str, request: Request, user: CurrentUser = Depends(current_user)
):
    """The current user's capability decisions for an app, plus the granted set.

    Also reports `provenance` (null if `app_id` isn't a classified userspace
    app) and `ceiling` -- the capabilities that tier holds without any of
    `grants` -- so a UI can show why a capability needs consent.
    """
    store = request.app.state.app_grants
    grants = await store.list_grants(user.user_id, app_id)
    granted = sorted(await store.granted_capabilities(user.user_id, app_id))
    provenance = await _resolve_provenance(request, app_id)
    ceiling = sorted(capability_ceiling(provenance)) if provenance else None
    return {
        "app_id": app_id, "grants": grants, "granted": granted,
        "provenance": provenance, "ceiling": ceiling,
    }


@router.post("/api/apps/{app_id}/permissions")
async def set_app_permission(
    app_id: str, body: GrantIn, request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Grant or deny a capability for an app on behalf of the calling user."""
    store = request.app.state.app_grants
    cap = body.capability.strip()
    if not cap:
        return JSONResponse({"error": "capability required"}, status_code=400)
    if not is_known_capability(cap):
        return JSONResponse(
            {"error": f"unknown capability: {cap}"}, status_code=400
        )
    # The parametrized network:<origin> form must carry a well-formed origin; the
    # bare prefix is_known_capability accepts is not enough to record a grant.
    if cap.startswith(NET_PREFIX) and not is_valid_network_grant(cap):
        return JSONResponse(
            {"error": f"invalid network origin: {cap}"}, status_code=400
        )
    try:
        rec = await store.set_decision(user.user_id, app_id, cap, decision=body.decision)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"grant": rec}


@router.post("/api/apps/{app_id}/permissions/revoke")
async def revoke_app_permission(
    app_id: str, body: RevokeIn, request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Revoke a capability (records a denial, keeps the row)."""
    store = request.app.state.app_grants
    cap = body.capability.strip()
    if not cap:
        return JSONResponse({"error": "capability required"}, status_code=400)
    await store.revoke(user.user_id, app_id, cap)
    return {"ok": True}


class RequestConsentIn(BaseModel):
    capabilities: list[str]


@router.post("/api/apps/{app_id}/request-consent")
async def request_app_consent(
    app_id: str, body: RequestConsentIn, request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Raise an app-grant consent Decision for the capabilities that need it.

    The shared entry point for both grant-on-install and lazy first-use:
    validate the requested capabilities against the closed vocabulary, drop the
    free caps (granted without consent) and any the user has already decided on
    for this app, and if any remain create a multi_select consent Decision
    routed to the app_grants ledger on answer (see _apply_app_grant). Returns
    {decision, pending}; decision is null when nothing needs consent."""
    grants = request.app.state.app_grants
    caps: list[str] = []
    for raw in body.capabilities or []:
        cap = raw.strip()
        if not cap or not is_known_capability(cap):
            return JSONResponse({"error": f"unknown capability: {cap}"}, status_code=400)
        if cap.startswith(NET_PREFIX) and not is_valid_network_grant(cap):
            return JSONResponse({"error": f"invalid network origin: {cap}"}, status_code=400)
        caps.append(cap)

    # Which caps are auto-free (no consent needed) depends on the app's
    # provenance tier. Only a classified userspace app (found in the
    # userspace_apps store) gets tier-aware treatment; every other caller of
    # this generic ledger (a native feature requesting a capability on its own
    # behalf, not a sandboxed app) keeps the plain FREE_CAPS-are-free rule it
    # always had, unchanged.
    provenance = await _resolve_provenance(request, app_id)

    def _auto_free(cap: str) -> bool:
        if provenance is not None:
            return capability_allowed(provenance, cap, set())
        return cap in FREE_CAPS

    # Caps the user already granted or denied for this app are not re-prompted
    # (the Permissions app revisits).
    decided = {g["capability"] for g in await grants.list_grants(user.user_id, app_id)}
    # Caps that already have an unanswered app_grant Decision for this app: the
    # lazy first-use path re-fires on every denied access until the user answers,
    # so skip them or it would pile up duplicate pending consent cards.
    store = request.app.state.decision_store
    awaiting: set[str] = set()
    for d in await store.list(status="pending", user_id=user.user_id):
        meta = d.get("metadata") or {}
        if meta.get("kind") == "app_grant" and meta.get("app_id") == app_id:
            awaiting.update(meta.get("capabilities") or [])
    # De-duplicate while preserving order: a repeated cap would otherwise become
    # a colliding option whose value the dedup suffixer rewrites to a non-cap.
    pending: list[str] = []
    for c in caps:
        if _auto_free(c) or c in decided or c in awaiting or c in pending:
            continue
        pending.append(c)
    if not pending:
        return {"decision": None, "pending": []}

    decision = await store.create(user_id=user.user_id, **app_grant_decision_payload(app_id, pending))

    notifs = getattr(request.app.state, "notifications", None)
    if notifs is not None:
        # Best effort: a notification failure must not fail the queued decision.
        try:
            await notifs.add(
                title="Permission needed",
                message=f"{app_id} is requesting permissions",
                level="warning",
                source="decisions",
            )
        except Exception:
            pass
    return {"decision": decision, "pending": pending}
