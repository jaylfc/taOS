"""Local hub API the Hub app consumes -- hub social slice 2.

See ``docs/design/hub-social-network-foundation.md`` ("The Hub app (client)").
This is the controller-side local API, mirroring how the chat routes wrap the
chat stores: it reads and writes the node's own signed objects in the local hub
store (``tinyagentos/hub/store.py``) and mints/uses the node's identity keypair
(slice 1). Directory calls stay in ``account_proxy.py``; peer traffic is a later
slice. Nothing here talks to a peer.

Slice 2 surfaces the node's own profile: render it and create/update it with a
version bump. Every response carries an explicit ``state`` so the app can render
the standard degrade states (design "Client resilience") without guessing:

- ``no-identity``: the node has not minted a hub identity yet.
- ``no-profile``: identity exists but no profile has been published.
- ``ok``: a profile is present (returned under ``profile``).

The account signed-out state is handled one layer up by the ``current_user``
dependency (401), exactly like every other local app route.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.hub import identity, store as hub_store

logger = logging.getLogger(__name__)

router = APIRouter()


class ProfileIn(BaseModel):
    kind: str = "personal"
    display_name: str = ""
    bio: str = ""
    avatar: str | None = None
    links: list | None = None


async def _get_store(request: Request) -> hub_store.HubStore:
    """Return the node's hub store, opening it lazily on first use.

    Cached on ``app.state`` so repeated calls share one connection. The path is
    resolved the same way as the identity keystore (``TAOS_DATA_DIR``), so the
    store and identity file live together under ``<data_dir>/hub/`` and tests get
    an isolated dir for free.
    """
    existing = getattr(request.app.state, "hub_store", None)
    if existing is not None:
        return existing
    store = hub_store.HubStore(Path(hub_store.default_db_path()))
    await store.init()
    request.app.state.hub_store = store
    return store


@router.get("/api/hub/profile")
async def get_own_profile(
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Render the node's own profile, with explicit degrade states."""
    if not identity.exists():
        return {"state": "no-identity"}
    store = await _get_store(request)
    fingerprint = identity.signing_fingerprint()
    profile = await store.get_profile(fingerprint)
    if profile is None:
        return {"state": "no-profile", "identity": identity.public_identity()}
    return {"state": "ok", "profile": profile}


@router.put("/api/hub/profile")
async def put_own_profile(
    body: ProfileIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Create or update the node's own profile, bumping the version.

    Minting the identity on first use (design: the free username mints the
    keypair) so publishing a profile is the natural first act. The new version is
    one past the current profile's, keeping the highest-version-wins invariant.
    """
    # Mint the identity if this is the node's first hub action.
    identity.load_or_create()
    store = await _get_store(request)
    fingerprint = identity.signing_fingerprint()
    current = await store.get_profile(fingerprint)
    next_version = (current["version"] + 1) if current else 1
    try:
        profile = hub_store.build_profile(
            version=next_version,
            kind=body.kind,
            display_name=body.display_name,
            bio=body.bio,
            avatar=body.avatar,
            links=body.links,
            author=fingerprint,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    signed = hub_store.sign_object(profile)
    stored = await store.put_profile(signed)
    return {"state": "ok", "profile": stored}
