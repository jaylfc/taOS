"""Owner-facing management API for Agent-as-a-Model consent keys (decision 19).

The companion to routes/agent_model_api.py: that module is the OpenAI-compatible
/v1 surface an external caller hits with a consent key; THIS module is the
authenticated owner surface where a taOS user mints, reviews, and revokes the
keys that expose their own agent(s). The owner is the issuer, so authenticating
the request IS the consent; an external third party requesting access is the
richer Decisions-mediated flow built on top of this, not this slice.

Keys are scoped to the calling user: list and revoke only ever touch keys the
user issued. mint returns the plaintext token exactly once (the store keeps only
its hash), so the response is the sole opportunity to show it to the user.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import CurrentUser, current_user

router = APIRouter()

# Agent ids are used as filesystem path components (opencode home dirs) and
# LiteLLM model identifiers; reject traversal, dot-segments, and anything
# that isn't a safe slug so a malicious mint can't write configs outside
# the data_dir boundary.  Matches the sanitizer in taos_agent_runtime.py.
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_agent_id(v: str) -> str:
    if not _AGENT_ID_RE.fullmatch(v):
        raise ValueError(
            f"agent_id {v!r} contains invalid characters; "
            "only A-Z, a-z, 0-9, '.', '_', '-' are allowed"
        )
    return v


class MintIn(BaseModel):
    agent_ids: list[str]
    scopes: list[str] = []
    rate_cap: Optional[int] = None
    expires_at: Optional[str] = None  # ISO-8601, timezone-aware


@router.get("/api/agent-model-keys")
async def list_keys(request: Request, user: CurrentUser = Depends(current_user)):
    """List the consent keys this user issued (newest first, no secret material)."""
    store = request.app.state.agent_model_keys
    return {"keys": await store.list_for_user(user.user_id)}


@router.post("/api/agent-model-keys")
async def mint_key(
    body: MintIn, request: Request, user: CurrentUser = Depends(current_user)
):
    """Mint a consent key exposing the caller's agent(s). Returns the token ONCE."""
    store = request.app.state.agent_model_keys
    try:
        if not body.agent_ids:
            raise ValueError("at least one agent_id is required")
        for aid in body.agent_ids:
            _validate_agent_id(aid)
        token, rec = await store.mint(
            user.user_id,
            body.agent_ids,
            body.scopes,
            rate_cap=body.rate_cap,
            expires_at=body.expires_at,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    # The token is shown exactly once; the stored record never carries it.
    return {"key": token, "record": rec}


@router.post("/api/agent-model-keys/{key_id}/revoke")
async def revoke_key(
    key_id: int, request: Request, user: CurrentUser = Depends(current_user)
):
    """Revoke one of the caller's own keys.

    Ownership is enforced here because the store revokes by row id alone; a user
    must never be able to revoke a key another user issued.
    """
    store = request.app.state.agent_model_keys
    owned = {k["id"] for k in await store.list_for_user(user.user_id)}
    if key_id not in owned:
        return JSONResponse({"error": "not found"}, status_code=404)
    await store.revoke(key_id)
    return {"ok": True}
