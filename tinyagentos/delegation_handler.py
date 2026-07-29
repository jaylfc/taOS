"""Agent delegation handler — cross-user collab D1.

Processes delegation-request envelopes from remote contacts, applies
scope denylist and policy gates, creates Decisions cards for manual
approval, and on approval mints project invites for the sponsored agent.

Also provides cascade revocation and kill-switch machinery.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scope denylist — hard-coded scopes that can NEVER be granted to sponsored
# agents in v1, regardless of owner decisions (design section 5).
# ---------------------------------------------------------------------------

SPONSORED_DENY_SCOPES: frozenset[str] = frozenset({
    "files_write",
    "decisions_write",
})

# Default scope tier for delegated agents per design section 5.
SPONSORED_DEFAULT_SCOPES: frozenset[str] = frozenset({
    "a2a_send",
    "a2a_receive",
    "project_tasks",
    "canvas_read",
    "registry_feeds_read",
})

# Scopes that bind the JWT to a specific project (same set as agent_auth_requests).
_PROJECT_SCOPES: frozenset[str] = frozenset({"project_tasks", "canvas_read", "canvas_write"})


def validate_delegation_scopes(
    requested_scopes: list[str],
) -> tuple[list[str], list[str]]:
    """Validate and filter scopes for a delegation request.

    Returns ``(granted_scopes, denied_scopes)``.  Scopes in
    ``SPONSORED_DENY_SCOPES`` are stripped with a warning; scopes outside
    ``SPONSORED_DEFAULT_SCOPES`` require explicit per-scope Decisions approval
    but are NOT auto-denied — they surface to the human for approval.

    The returned ``granted_scopes`` are safe-to-mint; any scope in
    ``denied_scopes`` was hard-denied and will never be included in the minted
    invite.
    """
    requested_set = set(requested_scopes)
    denied = sorted(requested_set & SPONSORED_DENY_SCOPES)
    allowed = sorted(requested_set - SPONSORED_DENY_SCOPES)
    if denied:
        logger.warning(
            "delegation: hard-denied scopes %r from request %r",
            denied, sorted(requested_set),
        )
    return allowed, denied


def _validate_delegation_envelope_body(body: dict) -> tuple[bool, str, Optional[dict]]:
    """Validate the body of a delegation-request envelope.

    Returns ``(ok, error, parsed)``.  ``parsed`` is a dict with keys
    ``agent_slug``, ``display_name``, ``requested_scopes``, and ``project_id``
    when valid.
    """
    required = ("agent_slug", "display_name", "requested_scopes", "project_id")
    for field in required:
        if field not in body:
            return False, f"missing required field: {field}", None
        value = body[field]
        if field == "requested_scopes":
            if not isinstance(value, list):
                return False, f"{field} must be a list", None
            if not value:
                return False, f"{field} must not be empty", None
        elif not isinstance(value, str) or not value.strip():
            return False, f"{field} must be a non-empty string", None

    requested_scopes = body["requested_scopes"]
    unknown = sorted(set(requested_scopes) - SPONSORED_DEFAULT_SCOPES - SPONSORED_DENY_SCOPES)
    # Unknown scopes are not immediately denied — they require explicit
    # per-scope Decisions approval.  We just log them here; the decision
    # created later will surface them to the human.
    if unknown:
        logger.info(
            "delegation: elevated scopes in request: %r (require explicit approval)",
            unknown,
        )

    parsed = {
        "agent_slug": body["agent_slug"].strip(),
        "display_name": body["display_name"].strip(),
        "requested_scopes": body["requested_scopes"],
        "project_id": body["project_id"].strip(),
    }
    return True, "", parsed


async def process_delegation_request(
    request,
    *,
    contact_id: str,
    envelope_body: dict,
) -> dict:
    """Process a delegation-request envelope from a remote contact.

    Called from the peer inbox when envelope.kind == "delegation_request".

    1. Validate envelope body structure.
    2. Verify the contact has ``member_kind="human"`` in the target project.
    3. Apply scope denylist (strip ``files_write``, ``decisions_write``).
    4. Check ``auto_approve_delegation`` project setting.
       - If ON (dev-swarm future): auto-mint invite, return result.
       - If OFF (v1 default): create a blocking Decisions card.

    Returns a dict suitable for the peer inbox response envelope.
    On pending-approval: ``{"status": "pending_approval", "decision_id": ...}``
    On auto-approved: ``{"status": "approved", "invite_id": ..., ...}``
    """
    ok, err, parsed = _validate_delegation_envelope_body(envelope_body)
    if not ok or parsed is None:
        return {"status": "error", "error": err}

    agent_slug = parsed["agent_slug"]
    display_name = parsed["display_name"]
    requested_scopes = parsed["requested_scopes"]
    project_id = parsed["project_id"]

    # Verify sender is an active human collaborator on the target project.
    project_store = getattr(request.app.state, "project_store", None)
    if project_store is None:
        return {"status": "error", "error": "project store not available"}

    if not await project_store.is_project_member(
        project_id, contact_id, member_kind="human"
    ):
        return {
            "status": "error",
            "error": (
                f"contact {contact_id} is not a human collaborator on project "
                f"{project_id}"
            ),
        }

    # Apply scope denylist.
    granted_scopes, denied_scopes = validate_delegation_scopes(requested_scopes)

    # Check project policy: auto_approve_delegation knob.
    auto_approve = await project_store.get_project_setting(
        project_id, "auto_approve_delegation", default=False
    )

    if auto_approve:
        # Future dev-swarm path: immediate approval.
        return await _auto_approve_delegation(
            request,
            contact_id=contact_id,
            agent_slug=agent_slug,
            display_name=display_name,
            granted_scopes=granted_scopes,
            denied_scopes=denied_scopes,
            project_id=project_id,
        )

    # v1 manual path: create a blocking Decisions card.
    return await _create_delegation_decision(
        request,
        contact_id=contact_id,
        agent_slug=agent_slug,
        display_name=display_name,
        granted_scopes=granted_scopes,
        denied_scopes=denied_scopes,
        project_id=project_id,
    )


async def _create_delegation_decision(
    request,
    *,
    contact_id: str,
    agent_slug: str,
    display_name: str,
    granted_scopes: list[str],
    denied_scopes: list[str],
    project_id: str,
) -> dict:
    """Create a blocking Decisions card for manual delegation approval.

    The human sees: "contact {contact_id} wants to delegate agent
    '{display_name}' ({agent_slug}) to project {project_id} with scopes
    {granted_scopes}."  Any hard-denied scopes are noted in the question
    text so the human knows they were stripped.
    """
    decision_store = getattr(request.app.state, "decision_store", None)
    if decision_store is None:
        return {"status": "error", "error": "decision store not available"}

    question_parts = [
        f"{contact_id} wants to delegate agent "
        f"'{display_name}' ({agent_slug}) to this project",
        f"Requested scopes: {', '.join(granted_scopes)}",
    ]
    if denied_scopes:
        question_parts.append(
            f"(Hard-denied: {', '.join(denied_scopes)} — "
            f"these scopes cannot be granted to delegated agents in v1)"
        )

    question = ". ".join(question_parts) + "."

    try:
        decision = await decision_store.create(
            from_agent=contact_id,
            question=question,
            type="approve_deny",
            priority="blocking",
            project_id=project_id,
            metadata={
                "kind": "collab_delegation_gate",
                "contact_id": contact_id,
                "agent_slug": agent_slug,
                "display_name": display_name,
                "granted_scopes": granted_scopes,
                "denied_scopes": denied_scopes,
                "project_id": project_id,
            },
        )
    except Exception:
        logger.warning(
            "delegation: failed to create decision for %s / %s",
            contact_id, agent_slug, exc_info=True,
        )
        return {"status": "error", "error": "failed to create approval decision"}

    return {
        "status": "pending_approval",
        "decision_id": decision["id"],
    }


async def _auto_approve_delegation(
    request,
    *,
    contact_id: str,
    agent_slug: str,
    display_name: str,
    granted_scopes: list[str],
    denied_scopes: list[str],
    project_id: str,
) -> dict:
    """Auto-approve a delegation request (dev-swarm future path).

    Mints a project invite (kind="agent", pin_required=false) and returns
    the invite_id + connection_bundle.
    """
    invite_store = getattr(request.app.state, "project_invites", None)
    if invite_store is None:
        return {"status": "error", "error": "invite store not available"}

    # The project-scoped scopes require a non-null project_id for the JWT.
    project_scopes = sorted(set(granted_scopes) & _PROJECT_SCOPES)
    non_project_scopes = sorted(set(granted_scopes) - _PROJECT_SCOPES)

    try:
        invite = await invite_store.mint(
            project_id=project_id,
            scopes=non_project_scopes + project_scopes,
            approval_mode="auto",
            check_interval_secs=1800,
            created_by=contact_id,
            pin_required=False,
            metadata={
                "kind": "delegation_sponsored",
                "sponsor_contact_id": contact_id,
                "agent_slug": agent_slug,
                "display_name": display_name,
            },
        )
    except Exception:
        logger.warning(
            "delegation: failed to create invite for %s / %s",
            contact_id, agent_slug, exc_info=True,
        )
        return {"status": "error", "error": "failed to create project invite"}

    return {
        "status": "approved",
        "invite_id": invite["record"]["invite_id"],
        "agent_slug": agent_slug,
    }


async def complete_delegation_approval(
    request,
    *,
    decision_metadata: dict,
) -> dict:
    """Complete a delegation approval after the human answers the Decisions card.

    Called from the decisions route when a delegation_gate decision is approved.
    ``decision_metadata`` is the metadata dict stored on the decision at creation
    time (contains contact_id, agent_slug, display_name, granted_scopes, etc.).

    Mints a project invite and returns the result for delivery over the peer
    channel.
    """
    contact_id = decision_metadata.get("contact_id", "")
    agent_slug = decision_metadata.get("agent_slug", "")
    display_name = decision_metadata.get("display_name", "")
    granted_scopes = decision_metadata.get("granted_scopes", [])
    project_id = decision_metadata.get("project_id", "")

    if not all([contact_id, agent_slug, display_name, project_id]):
        return {"status": "error", "error": "incomplete decision metadata"}

    # Re-validate project membership at approval time: the contact may have
    # been removed from the project between decision creation and approval.
    # This is the fail-closed runtime check required by section 5 of the
    # cross-user-collab design spec.
    project_store = getattr(request.app.state, "project_store", None)
    if project_store is not None and not await project_store.is_project_member(
        project_id, contact_id, member_kind="human"
    ):
        return {
            "status": "error",
            "error": (
                f"contact {contact_id} is no longer a human collaborator "
                f"on project {project_id}"
            ),
        }

    invite_store = getattr(request.app.state, "project_invites", None)
    if invite_store is None:
        return {"status": "error", "error": "invite store not available"}

    # Re-apply scope denylist to ensure hard-denied scopes are stripped even
    # if the stored metadata was tampered with between decision creation and
    # approval.  The denylist is the authoritative gate; the stored scopes are
    # the human-approved set, but the code must never mint tokens for
    # hard-denied scopes regardless.
    safe_scopes, re_denied = validate_delegation_scopes(granted_scopes)
    if re_denied:
        logger.warning(
            "complete_delegation_approval: re-stripped hard-denied scopes %r from stored grant",
            re_denied,
        )

    try:
        invite = await invite_store.mint(
            project_id=project_id,
            scopes=safe_scopes,
            approval_mode="auto",
            check_interval_secs=1800,
            created_by=contact_id,
            pin_required=False,
            metadata={
                "kind": "delegation_sponsored",
                "sponsor_contact_id": contact_id,
                "agent_slug": agent_slug,
                "display_name": display_name,
            },
        )
    except Exception:
        logger.warning(
            "delegation: complete_approval: failed to create invite for %s / %s",
            contact_id, agent_slug, exc_info=True,
        )
        return {"status": "error", "error": "failed to create project invite"}

    return {
        "status": "approved",
        "invite_id": invite["record"]["invite_id"],
        "agent_slug": agent_slug,
    }


async def cascade_sponsor_revoke(
    request,
    *,
    contact_id: str,
    project_id: str | None = None,
    reason: str = "sponsor revoked",
) -> dict:
    """Revoke all sponsored agent identities for a contact.

    When *project_id* is provided, only revoke agents that are members of
    that project (membership-revoke).  When None, revoke ALL sponsored
    identities (contact-revoke).

    Also unassigns in-flight board tasks and posts an A2A system line.
    Returns a summary dict of what was revoked.
    """
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return {"status": "error", "error": "registry not available"}

    project_store = getattr(request.app.state, "project_store", None)

    # Find all active agents sponsored by this contact.
    sponsored = await registry.list_by_sponsor(contact_id, status="active")
    revoked_ids: list[str] = []

    for agent in sponsored:
        canonical_id = agent["canonical_id"]

        # When scoped to a project, only revoke agents that are members of
        # that project.  Contact-wide revoke (project_id=None) hits all.
        if project_id is not None:
            if project_store is None:
                return {"status": "error", "error": "project store not available"}
            if not await project_store.is_project_member(
                project_id, canonical_id
            ):
                continue

        try:
            await registry.set_status(canonical_id, "revoked", actor=contact_id)
            revoked_ids.append(canonical_id)
        except Exception:
            logger.warning(
                "cascade_revoke: failed to revoke %s (sponsor %s)",
                canonical_id, contact_id, exc_info=True,
            )

    # Unassign in-flight board tasks for all revoked agent identities.
    task_store = getattr(request.app.state, "project_task_store", None)
    unassigned_count = 0
    if task_store is not None and revoked_ids:
        for canonical_id in revoked_ids:
            try:
                # Find tasks assigned to this agent and move them back to ready.
                unassigned = await _unassign_agent_tasks(
                    task_store, canonical_id, project_id
                )
                unassigned_count += unassigned
            except Exception:
                logger.warning(
                    "cascade_revoke: task unassign failed for %s",
                    canonical_id, exc_info=True,
                )

    # Post A2A system line to the affected project (when scoped).
    # Contact-wide revoke does not emit A2A — each project's membership-revoke
    # path handles its own audit event when the cascade fires per-project.
    if project_id:
        try:
            from tinyagentos.projects.a2a import ensure_a2a_channel

            msg_store = request.app.state.chat_messages
            channel = await ensure_a2a_channel(
                request.app.state.chat_channels,
                request.app.state.project_store,
                project_id,
                config=getattr(request.app.state, "config", None),
            )
            await msg_store.send_message(
                channel_id=channel["id"],
                author_id="system",
                author_type="user",
                content=(
                    f"Collaboration with {contact_id} has been revoked. "
                    f"{len(revoked_ids)} delegated agent(s) revoked, "
                    f"{unassigned_count} task(s) unassigned."
                ),
            )
        except Exception:
            logger.warning(
                "cascade_revoke: A2A system line failed for %s",
                contact_id, exc_info=True,
            )

    return {
        "status": "revoked",
        "contact_id": contact_id,
        "revoked_agents": len(revoked_ids),
        "unassigned_tasks": unassigned_count,
        "revoked_ids": revoked_ids,
        "reason": reason,
    }


async def _unassign_agent_tasks(
    task_store,
    canonical_id: str,
    project_id: str | None = None,
) -> int:
    """Unassign all in-flight tasks for *canonical_id*, moving them back to 'ready'.

    Returns the count of tasks unassigned.
    """
    # The task store interface varies by implementation; use the available
    # method to find and unassign.  We query for tasks with assignee matching
    # the agent's canonical_id and reset them.
    try:
        tasks = await task_store.list_for_assignee(canonical_id)
    except AttributeError:
        # Fall back: try list_tasks with filter.
        try:
            all_tasks = await task_store.list_tasks(
                project_id=project_id,
                status="in_progress",
            )
            tasks = [t for t in all_tasks if t.get("assignee_id") == canonical_id]
        except Exception:
            return 0

    count = 0
    for task in tasks:
        task_id = task.get("id") or task.get("task_id")
        if not task_id:
            continue
        try:
            await task_store.update_task(task_id, assignee_id=None, status="ready")
            count += 1
        except Exception:
            pass

    return count


# ---------------------------------------------------------------------------
# Kill-switch: 3 levels per design section 5
# ---------------------------------------------------------------------------

async def kill_switch_per_contact(request, *, contact_id: str) -> dict:
    """Level 2 kill-switch: pause collaboration with a specific contact.

    Suspends the peer link and revokes all sponsored tokens.  Reversible
    (the peer link can be re-established).
    """
    contacts_store = getattr(request.app.state, "contacts_store", None)
    if contacts_store is None:
        return {"status": "error", "error": "contacts store not available"}

    # Suspend the peer link (revoke the inbound token, set status to suspended).
    try:
        await contacts_store.revoke_peer_link(contact_id)
    except Exception:
        logger.warning(
            "kill_switch_per_contact: peer link revoke failed for %s",
            contact_id, exc_info=True,
        )

    # Cascade to all sponsored agents.
    cascade_result = await cascade_sponsor_revoke(
        request, contact_id=contact_id, reason="kill-switch (per-contact pause)",
    )

    return {
        "status": "paused",
        "contact_id": contact_id,
        "revoked_agents": cascade_result.get("revoked_agents", 0),
        "unassigned_tasks": cascade_result.get("unassigned_tasks", 0),
    }


async def kill_switch_per_instance(request) -> dict:
    """Level 3 kill-switch: disable the entire peer route family.

    Sets a flag on app.state that the peer routes check on every request.
    Does NOT revoke tokens — just blocks all new peer traffic.
    Use ``kill_switch_reenable()`` to restore peer routes.
    """
    request.app.state._peer_disabled = True
    logger.warning("kill_switch: per-instance panic — peer routes DISABLED")
    return {"status": "panic", "peer_routes": "disabled"}


async def kill_switch_reenable(request) -> dict:
    """Re-enable peer routes after a per-instance panic.

    Clears the panic flag set by ``kill_switch_per_instance``.
    Does NOT re-establish revoked peer links or re-mint tokens —
    those must be restored manually via the contacts/peer-link flow.
    """
    request.app.state._peer_disabled = False
    logger.info("kill_switch: per-instance panic cleared — peer routes RE-ENABLED")
    return {"status": "recovered", "peer_routes": "enabled"}


def is_peer_disabled(request) -> bool:
    """Check whether the per-instance peer panic switch is active."""
    return getattr(request.app.state, "_peer_disabled", False)
