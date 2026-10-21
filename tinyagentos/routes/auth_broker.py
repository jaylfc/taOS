"""Sign-in broker routes (tsk-diqs7d). Store and lifecycle: tinyagentos/auth_broker.py.

Who may call what:

- ``POST /api/auth-broker/requests``: an AGENT only (a bound agent local token,
  or a registry JWT). A user session gets 403: the broker exists so an agent can
  ask the user, never the other way round.
- ``POST /api/auth-broker/requests/{id}/code``: a USER SESSION only (the agent's
  owner, or an admin). An agent token gets 403, so an agent can never answer its
  own sign-in card.
- ``GET /api/auth-broker/requests/{id}``: the requesting agent only (anyone else
  gets 404). Returns the pasted code once, then clears it.
- ``POST /api/auth-broker/requests/{id}/token``: the requesting agent only. The
  token goes to Secrets under a fixed name granted to that agent alone, and is
  never logged or echoed back.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from tinyagentos.agent_token_auth import check_agent_identity
from tinyagentos.auth_broker import EXPIRY_SECONDS, AuthBrokerStore, signin_host
from tinyagentos.auth_context import (
    current_user,
    require_agent_owner_or_admin,
    resolve_agent_owner,
)
from tinyagentos.claude_code_runtime import CLAUDE_CODE_TOKEN_SECRET
from tinyagentos.native_agent_identity import NATIVE_AGENT_ORIGIN

logger = logging.getLogger(__name__)

router = APIRouter()

_REGISTRY_VIAS = ("registry_jwt_candidate", "registry_jwt_native_agent")


class CreateBody(BaseModel):
    kind: str
    url: str = Field(max_length=4096)
    prompt: str = Field(default="", max_length=500)


class CodeBody(BaseModel):
    code: str = Field(min_length=1, max_length=1024)


class TokenBody(BaseModel):
    token: str = Field(min_length=1, max_length=8192)


def _store(request: Request) -> AuthBrokerStore:
    store = getattr(request.app.state, "auth_broker", None)
    if store is None:
        raise HTTPException(status_code=503, detail="sign-in broker not available")
    return store


async def _agent(request: Request) -> tuple[str, str, bool]:
    """Return (requester id, display label, is the native taOS Agent).

    403 when the caller is not an agent (a user session, the unbound host token)."""
    bound = getattr(request.state, "agent_name", None)
    if bound:
        return bound, bound, False
    if getattr(request.state, "via", None) in _REGISTRY_VIAS:
        canonical_id = await check_agent_identity(request)
        if canonical_id:
            registry = getattr(request.app.state, "agent_registry", None)
            record = await registry.get(canonical_id) if registry is not None else None
            record = record or {}
            label = record.get("display_name") or record.get("handle") or canonical_id
            return canonical_id, label, record.get("origin") == NATIVE_AGENT_ORIGIN
    raise HTTPException(status_code=403, detail="an agent token is required")


def _secret_name(requester: str, native: bool) -> str:
    # The root taOS Agent's token is the one the claude_code harness reads
    # (claude_code_runtime.CLAUDE_CODE_TOKEN_SECRET). Every other agent gets its
    # own name, so no agent can overwrite another's token or the harness one.
    return CLAUDE_CODE_TOKEN_SECRET if native else f"{CLAUDE_CODE_TOKEN_SECRET}:{requester}"


def _public(rec: dict) -> dict:
    status = "expired" if AuthBrokerStore.expired(rec) else rec["status"]
    return {
        "id": rec["id"],
        "kind": rec["kind"],
        "status": status,
        "host": signin_host(rec["url"]),
        "expires_in_seconds": EXPIRY_SECONDS,
    }


@router.post("/api/auth-broker/requests")
async def create_request(request: Request, body: CreateBody):
    requester, label, _native = await _agent(request)
    host = signin_host(body.url)
    if host is None:
        raise HTTPException(
            status_code=422,
            detail="url must be https on an allowed sign-in host: claude.ai, console.anthropic.com",
        )
    # The card goes to the agent's owner. An agent the registry cannot attribute
    # (a bound local-token agent with no registry row) goes to the admins, who
    # are the only users allowed to answer it. Never user_id=None: that is a
    # broadcast to every account.
    owner = await resolve_agent_owner(request, requester)
    if owner is not None:
        recipients = [owner]
    else:
        recipients = [u["id"] for u in request.app.state.auth.list_users() if u.get("is_admin")]
        if not recipients:
            raise HTTPException(status_code=409, detail="no user to receive the sign-in card")
    try:
        rec = await _store(request).create(
            requester=requester, kind=body.kind, url=body.url, prompt=body.prompt.strip()
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    notifs = getattr(request.app.state, "notifications", None)
    if notifs is not None:
        for user_id in recipients:
            try:
                await notifs.add(
                    title="Sign-in request",
                    message=f"{label} asks you to sign in to Claude ({host}).",
                    level="info",
                    source="auth_broker",
                    data={
                        "request_id": rec["id"],
                        "agent": label,
                        "url": rec["url"],
                        "host": host,
                        "prompt": rec["prompt"],
                    },
                    user_id=user_id,
                )
            except Exception:
                logger.warning("auth_broker: sign-in notification failed", exc_info=True)
    return _public(rec)


@router.post("/api/auth-broker/requests/{request_id}/code")
async def submit_code(request: Request, request_id: str, body: CodeBody):
    if getattr(request.state, "via", None) != "session":
        raise HTTPException(status_code=403, detail="only a signed-in user may answer a sign-in card")
    user = current_user(request)
    store = _store(request)
    rec = await store.get(request_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="sign-in request not found")
    await require_agent_owner_or_admin(request, user, rec["requester"])
    outcome = await store.set_code(request_id, body.code.strip())
    if outcome == "missing":
        raise HTTPException(status_code=404, detail="sign-in request not found")
    if outcome == "expired":
        raise HTTPException(status_code=410, detail="sign-in request expired")
    if outcome == "conflict":
        raise HTTPException(status_code=409, detail="a code was already given for this request")
    return {"ok": True}


@router.get("/api/auth-broker/requests/{request_id}")
async def poll_request(request: Request, request_id: str):
    requester, _label, _native = await _agent(request)
    store = _store(request)
    rec = await store.get(request_id)
    if rec is None or rec["requester"] != requester:
        raise HTTPException(status_code=404, detail="sign-in request not found")
    rec, code = await store.take_code(request_id)
    out = _public(rec)
    if code is not None:
        out["code"] = code
    return out


@router.post("/api/auth-broker/requests/{request_id}/token")
async def store_token(request: Request, request_id: str, body: TokenBody):
    requester, _label, native = await _agent(request)
    store = _store(request)
    rec = await store.get(request_id)
    if rec is None or rec["requester"] != requester:
        raise HTTPException(status_code=404, detail="sign-in request not found")
    secrets_store = getattr(request.app.state, "secrets", None)
    if secrets_store is None:
        raise HTTPException(status_code=503, detail="secrets store not available")

    # Claim the request (under the store lock) before the secret is written, so
    # an overlapping post or an expiry can never leave a stored token behind a
    # 409 or 410. A failed write hands the claim back so the agent can retry.
    outcome = await store.complete(request_id)
    if outcome == "expired":
        raise HTTPException(status_code=410, detail="sign-in request expired")
    if outcome != "ok":
        raise HTTPException(status_code=409, detail="the sign-in code has not been read, or a token was already stored")

    name = _secret_name(requester, native)
    description = "Claude subscription token, stored by the sign-in broker"
    try:
        if await secrets_store.get(name) is None:
            await secrets_store.add(
                name, body.token, category="tokens", description=description, agents=[requester]
            )
        else:
            await secrets_store.update(name, value=body.token, description=description, agents=[requester])
    except BaseException:
        await store.release(request_id)
        raise
    logger.info("auth_broker: stored a sign-in token for %s as secret %s", requester, name)
    return {"ok": True, "secret_name": name}
