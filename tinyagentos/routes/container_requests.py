from __future__ import annotations

"""Routes for agent-requested container provisioning (P1 + P2).

POST /api/containers/requests -- an agent with its own registry JWT submits a
provisioning request. The policy engine evaluates quota + threshold against the
agent's existing non-terminal requests:

  - under quota       -> auto-approved (state: approved)
  - over quota        -> pending-approval (manual review)
  - over threshold    -> escalated to the Decisions app for Jay, then
                         pending-approval

P2 adds the provisioning executor: on an approved request, create the incus
container, bind it to the agent + project, and record the container id. A
destroy path releases the record and returns quota. Quota accounting is atomic:
count+create is serialized so a quota=1 race cannot double-approve.
"""

import logging
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from tinyagentos.agent_token_auth import check_agent_identity
from tinyagentos.containers.lxc import LXCBackend
from tinyagentos.containers.provisioning_policy import (
    ESCALATE,
    PENDING,
    APPROVE,
    PolicyConfig,
    ProvisioningPolicy,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateContainerRequest(BaseModel):
    image: str | None = None
    reason: str = ""
    config: dict | None = None


def _get_request_store(request: Request):
    store = getattr(request.app.state, "container_request_store", None)
    if store is None:
        raise RuntimeError("container_request_store not on app.state")
    return store


def _get_decision_store(request: Request):
    store = getattr(request.app.state, "decision_store", None)
    if store is None:
        raise RuntimeError("decision_store not on app.state")
    return store


def _get_policy(request: Request) -> ProvisioningPolicy:
    policy = getattr(request.app.state, "provisioning_policy", None)
    if policy is None:
        cfg = getattr(request.app.state, "config", None)
        policy = ProvisioningPolicy(PolicyConfig.from_app_config(cfg))
    return policy


def _default_image(request: Request) -> str:
    cfg = getattr(request.app.state, "config", None)
    if cfg is not None:
        return getattr(cfg, "container_provisioning", {}).get("default_image", "")
    return ""


@router.post("/api/containers/requests")
async def create_container_request(
    request: Request,
    body: CreateContainerRequest,
):
    """Submit a container provisioning request.

    Auth: the agent's own registry JWT (Bearer). The verified canonical_id is
    taken from the token, never from the request body, so an agent cannot
    request a container billed to another identity.

    Returns ``{request_id, canonical_id, status}``. ``status`` is the
    post-policy state: ``approved`` or ``pending-approval``.
    """
    canonical_id = await check_agent_identity(request)
    if canonical_id is None:
        raise HTTPException(status_code=401, detail="agent identity required")

    store = _get_request_store(request)
    policy = _get_policy(request)
    image = body.image or _default_image(request) or ""

    record, verdict, active_count = await store.create_with_policy_check(
        canonical_id,
        policy,
        image=image,
        reason=body.reason,
        config=body.config or {},
    )

    decision_id = None
    if verdict == APPROVE:
        status = "approved"
    elif verdict == PENDING:
        status = "pending-approval"
    else:
        decision_store = _get_decision_store(request)
        crq_id = record["id"]
        question = (
            f"Agent {canonical_id} has exceeded its container provisioning "
            f"threshold and is requesting a new container "
            f"(request {crq_id}, image={image or 'default'}). "
            f"Approve or deny?"
        )
        try:
            decision = await decision_store.create(
                from_agent=canonical_id,
                question=question,
                type="approve_deny",
                priority="normal",
                project_id=None,
                user_id="",
                context=f"container_request:{crq_id}",
                metadata={
                    "request_id": crq_id,
                    "canonical_id": canonical_id,
                    "image": image,
                    "reason": body.reason,
                    "active_count": active_count,
                },
            )
            decision_id = decision["id"]
            await store.link_decision(crq_id, decision_id)
        except Exception:
            # escalate-failure: if DecisionStore.create raises, strand
            # prevention -- mark the request failed so it releases quota.
            await store.set_status(crq_id, "failed", error="decision creation failed")
            raise
        status = "pending-approval"

    updated = await store.set_status(record["id"], status, decision_id=decision_id)

    result = {
        "request_id": updated["id"],
        "canonical_id": updated["canonical_id"],
        "status": updated["status"],
    }
    if decision_id:
        result["decision_id"] = decision_id
    return result


@router.post("/api/container-requests")
async def create_container_request_alias(
    request: Request,
    body: CreateContainerRequest,
):
    """Alias for POST /api/containers/requests."""
    return await create_container_request(request, body)


@router.post("/api/containers/requests/{id}/provision")
async def provision_container_request(
    id: str,
    request: Request,
):
    """Provision an incus container for an approved request.

    Auth: the agent's own registry JWT (Bearer). Only the requesting agent
    may provision its own request, and only when the request is in the
    ``approved`` state.

    On success, transitions the request to ``provisioned`` and records the
    container name. Returns ``{request_id, container_name, status}``.
    """
    canonical_id = await check_agent_identity(request)
    if canonical_id is None:
        raise HTTPException(status_code=401, detail="agent identity required")

    store = _get_request_store(request)
    record = await store.get(id)
    if record is None:
        raise HTTPException(status_code=404, detail="request not found")
    if record["canonical_id"] != canonical_id:
        raise HTTPException(status_code=403, detail="not your request")
    if record["status"] != "approved":
        raise HTTPException(status_code=400, detail=f"request is {record['status']}, not approved")

    backend = LXCBackend()
    safe_cid = re.sub(r"[^a-zA-Z0-9_-]", "-", canonical_id)
    safe_cid = safe_cid[:43]  # ensure container_name stays <= 63 chars: "taos-agent-" (11) + "-" (1) + safe_cid (max 43) + "-" (1) + id[:8] (8) = 63
    container_name = f"taos-agent-{safe_cid}-{id[:8]}"

    try:
        result = await backend.create_container(
            name=container_name,
            image=record.get("image") or _default_image(request) or "images:debian/bookworm",
        )
    except Exception as exc:
        await store.set_status(id, "failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"provisioning failed: {exc}")

    if not result.get("success"):
        error_msg = result.get("error", "provisioning failed")
        await store.set_status(id, "failed", error=error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

    config_json = record.get("config_json") or {}
    project_id = config_json.get("project_id")
    env_result = await backend.set_env(container_name, "TAOS_AGENT_CANONICAL_ID", canonical_id)
    if not env_result.get("success"):
        await backend.destroy_container(container_name)
        error_msg = env_result.get("output", "set_env failed")
        await store.set_status(id, "failed", error=error_msg)
        raise HTTPException(status_code=500, detail=error_msg)
    if project_id:
        project_env_result = await backend.set_env(container_name, "TAOS_PROJECT_ID", str(project_id))
        if not project_env_result.get("success"):
            await backend.destroy_container(container_name)
            error_msg = project_env_result.get("output", "set_env failed")
            await store.set_status(id, "failed", error=error_msg)
            raise HTTPException(status_code=500, detail=error_msg)

    updated = await store.set_status(id, "provisioned", container_name=container_name)

    return {
        "request_id": updated["id"],
        "container_name": container_name,
        "status": "provisioned",
    }


@router.post("/api/containers/requests/{id}/destroy")
async def destroy_container_request(
    id: str,
    request: Request,
):
    """Destroy a container request, releasing its quota.

    Auth: the agent's own registry JWT (Bearer). Only the requesting agent
    may destroy its own request. If the request was already provisioned,
    the underlying incus container is also deleted.

    Returns ``{request_id, released: true}``.
    """
    canonical_id = await check_agent_identity(request)
    if canonical_id is None:
        raise HTTPException(status_code=401, detail="agent identity required")

    store = _get_request_store(request)
    record = await store.get(id)
    if record is None:
        raise HTTPException(status_code=404, detail="request not found")
    if record["canonical_id"] != canonical_id:
        raise HTTPException(status_code=403, detail="not your request")

    container_name = record.get("container_name")
    if container_name:
        backend = LXCBackend()
        result = await backend.destroy_container(container_name)
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("output", "destroy failed"))

    released = await store.destroy(id)
    if not released:
        raise HTTPException(status_code=404, detail="request not found")

    return {"request_id": id, "released": True}


@router.get("/api/agents/containers/quota")
async def get_agent_container_quota(request: Request):
    """Return the calling agent's container provisioning quota.

    Auth: the agent's own registry JWT (Bearer). No scope grant required.

    Returns ``{canonical_id, quota, threshold, active_count, remaining}``.
    """
    canonical_id = await check_agent_identity(request)
    if canonical_id is None:
        raise HTTPException(status_code=401, detail="agent identity required")

    store = _get_request_store(request)
    policy = _get_policy(request)
    active_count = await store.count_active_for_agent(canonical_id)
    quota, threshold = policy._effective_limits(canonical_id)

    return {
        "canonical_id": canonical_id,
        "quota": quota,
        "threshold": threshold,
        "active_count": active_count,
        "remaining": max(0, quota - active_count),
    }
