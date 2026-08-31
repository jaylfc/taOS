from __future__ import annotations

"""Routes for agent-requested container provisioning (P1).

POST /api/containers/requests -- an agent with its own registry JWT submits a
provisioning request. The policy engine evaluates quota + threshold against the
agent's existing non-terminal requests:

  - under quota       -> auto-approved (state: approved)
  - over quota        -> pending-approval (manual review)
  - over threshold    -> escalated to the Decisions app for Jay, then
                         pending-approval

No provisioning happens in P1; the executor lands in P2.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from tinyagentos.agent_token_auth import check_agent_identity
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

    # Count EXISTING non-terminal requests BEFORE creating the new one, so the
    # policy evaluates the agent's current quota usage, not the incoming request.
    cfg = getattr(request.app.state, "config", None)
    default_image = ""
    if cfg is not None:
        default_image = cfg.container_provisioning.get("default_image", "")
    image = body.image or default_image or ""

    active_count = await store.count_active_for_agent(canonical_id)
    verdict = policy.evaluate(canonical_id, active_count)

    record = await store.create(
        canonical_id,
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
        # Over threshold: escalate to a Decisions-app item for Jay.
        decision_store = _get_decision_store(request)
        crq_id = record["id"]
        question = (
            f"Agent {canonical_id} has exceeded its container provisioning "
            f"threshold and is requesting a new container "
            f"(request {crq_id}, image={image or 'default'}). "
            f"Approve or deny?"
        )
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
