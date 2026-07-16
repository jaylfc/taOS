from __future__ import annotations

"""Gated agent-to-agent delegation (#161, option A: org metadata + gated
delegation, no approval rerouting).

``POST /api/agents/{from_agent}/delegate`` lets one agent hand a task to
another. The reporting line on the agent registry (see
``agent_registry_store.set_reporting``) is org metadata only here - it does
NOT change who approves a delegation. Every delegation is gated by the same
governance layer as ``routes/skill_exec.py``'s tool-execution gate: the
``delegate`` action class defaults to ``require_approval`` (see
``governance/action_classes.SENSITIVE_ACTION_CLASSES``), so an ungranted
delegation queues a blocking Decision instead of running immediately.

The gate/decision-creation flow below mirrors
``routes.skill_exec._check_execution_policy`` closely but is not a direct
call into it - that function is keyed on a ``skill_id`` classified via
``governance.action_classes.classify``, whereas delegation is not a skill
call. Keeping a small, self-contained mirror here avoids reshaping the
well-tested skill-exec gate for a caller it was never designed around.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.agent_db import find_agent

logger = logging.getLogger(__name__)

router = APIRouter()

ACTION_CLASS = "delegate"


def _is_admin_or_local_token(request: Request) -> bool:
    """True if the caller is an admin session or presented the host's local
    token. Mirrors ``routes.skill_exec._is_admin_or_local_token`` - delegation
    is an agent action, gated the same way skill execution is."""
    if getattr(request.state, "is_admin", False):
        return True
    return getattr(request.state, "via", None) == "local_token"


async def _resolve_agent_name(request: Request, ref: str) -> str | None:
    """Resolve *ref* to the config agent slug (the identity used everywhere
    else for policy grants, task assignee_id, A2A membership, board audit).

    Accepts either the slug itself or a registry canonical_id: a canonical_id
    is looked up in the agent registry and its display_name/handle matched
    back to a config agent. Returns None if *ref* does not resolve.
    """
    if not ref:
        return None
    config = request.app.state.config
    agent = find_agent(config, ref)
    if agent is not None:
        return agent["name"]

    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None or getattr(registry, "_db", None) is None:
        # Not wired/initialised in this deployment -- fall back to "not
        # found via registry" rather than raising, since the common case
        # (a plain config-agent slug) never needs the registry at all.
        return None
    record = await registry.get(ref)
    if record is None:
        return None
    for candidate in (record.get("display_name"), (record.get("handle") or "").lstrip("@")):
        if candidate:
            agent = find_agent(config, candidate)
            if agent is not None:
                return agent["name"]
    return None


def _resolve_agent_id(request: Request, name: str) -> str:
    """Resolve a config-agent *name* (slug) to its stable hex id.

    Task assignee_id is keyed on the config hex id everywhere else that reads
    it - beads (projects/beads_bridge._resolve_assignee -> agent["id"]),
    project members (member_id), and the heartbeat sweep
    (agent_heartbeat.list_ready_tasks_for_assignee(agent["id"])). The name is
    kept only for display/notify text. Falls back to *name* when the agent has
    no id (older/test agents where id == name) or cannot be found.
    """
    config = getattr(request.app.state, "config", None)
    if config is None:
        return name
    agent = find_agent(config, name)
    if agent is None:
        return name
    return agent.get("id") or name


class DelegateRequest(BaseModel):
    to_agent: str
    task_id: str | None = None
    task_title: str | None = None
    # Required only when creating a brand-new task from task_title (an
    # existing task_id already carries its project). Not part of the
    # original design note's body shape, but tasks cannot exist outside a
    # project in this codebase - see projects/task_store.py.
    project_id: str | None = None
    note: str = ""


async def _check_delegation_policy(
    request: Request,
    *,
    from_agent: str,
    to_agent: str,
    task_id: str | None,
    task_title: str | None,
    project_id: str | None,
    note: str,
    existing_title: str | None = None,
) -> JSONResponse | None:
    """Agent governance gate for delegation. Returns a JSONResponse to
    short-circuit the call (deny / pending-approval), or None to let the
    caller proceed with the delegation."""
    # Mirrors routes.skill_exec._check_execution_policy: governance applies
    # only to AGENT callers (local-token). An admin HUMAN session is the
    # approval authority themselves, so self-gating their own delegation is
    # noise -- it never substitutes for the _is_admin_or_local_token check in
    # delegate_task, which already keeps out plain non-admin sessions.
    if (
        getattr(request.state, "via", None) == "session"
        and getattr(request.state, "is_admin", False)
    ):
        return None

    policies = getattr(request.app.state, "execution_policies", None)
    if policies is None:
        # Not wired in this deployment; fail open rather than break
        # delegation over an additive, not-yet-present store.
        return None

    effect = await policies.effective_effect(from_agent, ACTION_CLASS)
    if effect == "allow":
        return None

    if effect == "deny":
        board_audit = getattr(request.app.state, "board_audit", None)
        if board_audit is not None:
            try:
                # Key the deny on the real task when one exists (so it shows in
                # that task's history) and always carry the real project_id so
                # recent_for_project surfaces it in the project feed. Without a
                # task, record a policy-level event rather than fabricating an
                # agent:{from_agent} task_id - the old synthetic id plus a blank
                # project_id left every deny event unfindable in any feed.
                await board_audit.record(
                    task_id=task_id or f"policy:{ACTION_CLASS}",
                    project_id=project_id or "",
                    event="delegation_policy_deny",
                    actor=from_agent,
                    detail={"to_agent": to_agent, "action_class": ACTION_CLASS},
                )
            except Exception:
                logger.warning(
                    "board_audit record failed for delegation deny by %s", from_agent, exc_info=True
                )
        receipt_store = getattr(request.app.state, "receipt_store", None)
        if receipt_store is not None:
            try:
                await receipt_store.record(
                    from_agent, tool_name="delegate", stop_reason="policy_deny"
                )
            except Exception:
                logger.warning(
                    "receipt capture failed for delegation deny by %s", from_agent, exc_info=True
                )
        return JSONResponse(
            {"error": "forbidden_by_policy", "action_class": ACTION_CLASS},
            status_code=403,
        )

    # effect == "require_approval"
    if await policies.has_live_grant(from_agent, ACTION_CLASS):
        return None

    decision_store = getattr(request.app.state, "decision_store", None)
    if decision_store is None:
        # The action needs approval but there is no inbox to raise it in; fail
        # loud rather than returning a fake pending_approval the caller cannot
        # poll.
        return JSONResponse(
            {"error": "approval required but no decision store is available"},
            status_code=503,
        )
    try:
        # Prefer a human-readable title in the approval question: the existing
        # task's fetched title, then a new-task task_title, then the raw id.
        display_title = existing_title or task_title
        target = f'"{display_title}"' if display_title else (task_id or "")
        decision = await decision_store.create(
            from_agent=from_agent,
            question=f"Agent {from_agent} wants to delegate {target} to {to_agent}".strip(),
            type="approve_deny",
            priority="blocking",
            project_id=project_id,
            metadata={
                "kind": "delegation_gate",
                "from_agent": from_agent,
                "to_agent": to_agent,
                "task_id": task_id,
                "task_title": task_title,
                "note": note,
            },
        )
    except Exception:
        logger.warning(
            "decision create failed for delegation gate (%s -> %s)",
            from_agent, to_agent, exc_info=True,
        )
        return JSONResponse(
            {"error": "failed to create the delegation approval request"},
            status_code=503,
        )

    return JSONResponse(
        {"status": "pending_approval", "decision_id": decision["id"], "action_class": ACTION_CLASS},
        status_code=202,
    )


async def _notify_delegate(
    request: Request, *, from_agent: str, to_agent: str, task: dict, note: str
) -> None:
    """Best-effort: post a mention in the project's A2A channel so to_agent
    sees the handoff. Never raises - the delegation has already completed
    (task assigned) by the time this is called."""
    try:
        from tinyagentos.projects.a2a import ensure_a2a_channel

        channel = await ensure_a2a_channel(
            request.app.state.chat_channels,
            request.app.state.project_store,
            task["project_id"],
            config=getattr(request.app.state, "config", None),
        )
        content = f'@{to_agent} {from_agent} delegated "{task["title"]}" to you.'
        if note:
            content += f" Note: {note}"
        msg_store = request.app.state.chat_messages
        await msg_store.send_message(
            channel_id=channel["id"],
            author_id=from_agent,
            author_type="agent",
            content=content,
        )
        await request.app.state.chat_channels.update_last_message_at(channel["id"])
    except Exception:
        logger.warning(
            "delegation notify failed (%s -> %s, task %s)",
            from_agent, to_agent, task.get("id"), exc_info=True,
        )


async def complete_delegation(
    request: Request,
    *,
    from_agent: str,
    to_agent: str,
    task_id: str | None,
    task_title: str | None,
    project_id: str | None,
    note: str,
) -> dict:
    """Assign the task to to_agent (creating it from task_title if no task_id
    was given) and notify to_agent. Called both from the direct-allow path in
    ``delegate_task`` below and from the approved-decision path in
    ``routes.decisions._apply_delegation_grant``.

    Raises ``ValueError`` on a bad task reference - the caller maps that to a
    4xx in the direct path; the approval path logs it (the answer is already
    persisted by then).
    """
    task_store = request.app.state.project_task_store
    # to_agent is the config slug (name); task assignee_id must be the agent's
    # stable hex id so the heartbeat sweep and id-keyed member lookups can see
    # the delegated task. The name is still used below for the notify text.
    assignee_id = _resolve_agent_id(request, to_agent)
    if task_id:
        task = await task_store.get_task(task_id)
        if task is None:
            raise ValueError(f"task '{task_id}' not found")
        await task_store.update_task(task_id, assignee_id=assignee_id)
        task = await task_store.get_task(task_id)
    else:
        if not task_title:
            raise ValueError("task_id or task_title is required")
        if not project_id:
            raise ValueError("project_id is required to create a new task")
        task = await task_store.create_task(
            project_id=project_id, title=task_title, created_by=from_agent, assignee_id=assignee_id,
        )

    await _notify_delegate(request, from_agent=from_agent, to_agent=to_agent, task=task, note=note)
    return {"status": "delegated", "task_id": task["id"], "to_agent": to_agent}


@router.post("/api/agents/{from_agent}/delegate")
async def delegate_task(request: Request, from_agent: str, body: DelegateRequest):
    """Delegate a task from from_agent to to_agent, gated by the delegate
    action class (conservative default: require_approval).

    On require_approval with no live grant, returns 202 pending_approval and
    queues a blocking Decision; approving it (routes/decisions.py) grants the
    action and completes the delegation.
    """
    if not _is_admin_or_local_token(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    from_name = await _resolve_agent_name(request, from_agent)
    if from_name is None:
        return JSONResponse({"error": f"agent '{from_agent}' not found"}, status_code=404)
    to_name = await _resolve_agent_name(request, body.to_agent)
    if to_name is None:
        return JSONResponse({"error": f"agent '{body.to_agent}' not found"}, status_code=404)

    if not body.task_id and not body.task_title:
        return JSONResponse({"error": "task_id or task_title is required"}, status_code=400)

    project_id = body.project_id
    existing_title: str | None = None
    if body.task_id:
        task_store = request.app.state.project_task_store
        existing_task = await task_store.get_task(body.task_id)
        if existing_task is None:
            return JSONResponse({"error": f"task '{body.task_id}' not found"}, status_code=404)
        project_id = existing_task["project_id"]
        existing_title = existing_task.get("title")
    elif not project_id:
        return JSONResponse(
            {"error": "project_id is required when delegating a new task_title"},
            status_code=400,
        )

    gate_response = await _check_delegation_policy(
        request,
        from_agent=from_name,
        to_agent=to_name,
        task_id=body.task_id,
        task_title=body.task_title,
        project_id=project_id,
        note=body.note or "",
        existing_title=existing_title,
    )
    if gate_response is not None:
        return gate_response

    try:
        return await complete_delegation(
            request,
            from_agent=from_name,
            to_agent=to_name,
            task_id=body.task_id,
            task_title=body.task_title,
            project_id=project_id,
            note=body.note or "",
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
