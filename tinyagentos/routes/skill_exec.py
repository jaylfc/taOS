"""Skill execution runtime.

Exposes assigned skills as HTTP endpoints so deployed agents can discover and
invoke them at runtime. Each built-in skill maps to an in-process implementation
function; agents first hit ``GET /api/skill-exec/tools`` to discover the tool
schemas for their assigned skills, then POST to ``/api/skill-exec/{id}/call``
to execute them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Hold references to in-flight fire-and-forget receipt tasks so they are not
# garbage-collected before they run (asyncio keeps only a weak reference).
_bg_receipt_tasks: set = set()


def _is_admin_or_local_token(request: Request) -> bool:
    """True if the caller is an admin session or presented the host's local token.

    Skill EXECUTION (unlike the read-only discovery GET) must never be reachable
    by a plain non-admin user session -- built-in skills include ``code_exec``,
    which runs ``subprocess.run(["python3", "-c", code])`` against attacker-
    supplied code, i.e. host RCE (GHSA-h24f-gp4c-8qjm). The legitimate callers
    are: (a) an admin session, or (b) a deployed agent presenting the
    ``TAOS_LOCAL_TOKEN`` local token (see ``deployer.py`` + ``AuthManager.
    validate_local_token``).

    ``AuthMiddleware`` (tinyagentos/auth_middleware.py) sets both signals on
    ``request.state``: ``is_admin`` is True for an admin session AND for a
    local token that maps to the primary user, while ``via == "local_token"``
    is set for a valid local token even in the pre-onboarding edge case where
    there is no primary user yet (so ``is_admin`` is False there). Checking
    both keeps agent tool-calls working in every state the middleware allows,
    without ever accepting a bare non-admin user session.
    """
    if getattr(request.state, "is_admin", False):
        return True
    return getattr(request.state, "via", None) == "local_token"


# ---------------------------------------------------------------------------
# Built-in skill implementations
# ---------------------------------------------------------------------------


async def _skill_memory_search(args: dict, request: Request) -> dict:
    """Search agent memory via QMD.

    Agents have their own QMD — this is a stub that would proxy to the agent's
    QMD_SERVER. For now, return an empty result with a hint.
    """
    _ = args.get("query", "")
    _ = args.get("limit", 10)
    return {
        "status": "ok",
        "results": [],
        "note": "Route queries via agent's QMD_SERVER",
    }


def _resolve_agent_workspace(request: Request, args: dict) -> Path:
    """Return the per-agent workspace directory on the host.

    Same directory that ``deployer.deploy_agent`` bind-mounts into the
    container at ``/workspace``. Skills executed on behalf of an agent
    read and write through this path so the state survives a container
    rebuild or framework swap. See ``docs/design/framework-agnostic-runtime.md``.
    """
    agent_name = (
        getattr(request.state, "agent_name", None)
        or args.get("agent_name")
        or request.query_params.get("agent_name")
        or "default"
    )
    # agent_name feeds a filesystem path, so a value like "../../etc" would
    # escape the workspaces base and let a caller read or enumerate arbitrary
    # host directories through file_read / file_write / list_files. Resolve and
    # require the result to stay within the workspaces root before creating it.
    root = Path(request.app.state.agent_workspaces_dir).resolve()
    base = (root / agent_name).resolve()
    if base != root and root not in base.parents:
        raise ValueError("invalid agent_name (escapes the workspaces directory)")
    base.mkdir(parents=True, exist_ok=True)
    return base


async def _skill_file_read(args: dict, request: Request) -> dict:
    """Read a file from the calling agent's workspace."""
    path = args.get("path", "")
    try:
        workspace = _resolve_agent_workspace(request, args)
        target = (workspace / path).resolve()
        if workspace not in target.parents and target != workspace:
            return {"error": "Path outside workspace"}
        if not target.is_file():
            return {"error": "File not found"}
        return {"content": target.read_text(errors="replace")}
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_file_write(args: dict, request: Request) -> dict:
    """Write a file to the calling agent's workspace."""
    path = args.get("path", "")
    content = args.get("content", "")
    try:
        workspace = _resolve_agent_workspace(request, args)
        target = (workspace / path).resolve()
        if workspace not in target.parents and target != workspace:
            return {"error": "Path outside workspace"}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return {"status": "written", "bytes": len(content)}
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_list_files(args: dict, request: Request) -> dict:
    """List a directory in the calling agent's workspace (read complement to
    file_read / file_write). Sandboxed to the workspace, same as file_read."""
    path = args.get("path", "") or ""
    try:
        workspace = _resolve_agent_workspace(request, args)
        target = (workspace / path).resolve()
        if workspace not in target.parents and target != workspace:
            return {"error": "Path outside workspace"}
        if not target.is_dir():
            return {"error": "Directory not found"}
        entries = []
        for child in sorted(target.iterdir(), key=lambda c: c.name):
            is_dir = child.is_dir()
            entries.append({
                "name": child.name,
                "type": "dir" if is_dir else "file",
                "size": (child.stat().st_size if child.is_file() else None),
            })
        return {"entries": entries, "count": len(entries)}
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_web_search(args: dict, request: Request) -> dict:
    """Search the web via SearXNG (if available)."""
    import httpx

    query = args.get("query", "")
    max_results = args.get("max_results", 5)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"http://localhost:8888/search?q={query}&format=json"
            )
            if resp.status_code == 200:
                data = resp.json()
                return {"results": data.get("results", [])[:max_results]}
    except Exception:
        pass
    return {"error": "Web search not configured. Install SearXNG."}


async def _skill_code_exec(args: dict, request: Request) -> dict:
    """Execute Python code in a basic sandbox.

    SECURITY: this is host RCE by design -- ``subprocess.run`` against caller-
    supplied code with no sandboxing (GHSA-h24f-gp4c-8qjm). ``execute_skill``
    already gates every skill EXECUTION to admin-or-local-token before this
    function is ever reached; the check below is deliberate defense-in-depth
    so a regression in that route-level gate (or any future call site that
    invokes this function directly) cannot resurrect the non-admin RCE. Do
    not remove it because it looks redundant.

    TODO(#131): move this to a sandboxed runner (container/seccomp/gVisor)
    instead of a bare subprocess against the host's python3 -- this comment
    marks the follow-up; it is out of scope for the authz fix.
    """
    import subprocess

    if not _is_admin_or_local_token(request):
        return {"error": "forbidden: code_exec requires admin or local-token auth"}

    code = args.get("code", "")
    try:
        result = subprocess.run(
            ["python3", "-c", code],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Execution timed out (10s limit)"}
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_http_request(args: dict, request: Request) -> dict:
    """Make an HTTP request to an external URL."""
    import httpx

    url = args.get("url", "")
    method = args.get("method", "GET")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.request(method, url)
            return {
                "status": resp.status_code,
                "headers": dict(resp.headers),
                "body": resp.text[:10000],
            }
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_image_generation(args: dict, request: Request) -> dict:
    """Generate an image via local Stable Diffusion."""
    try:
        from tinyagentos.tools.image_tool import execute_image_generation

        result = await execute_image_generation(
            prompt=args.get("prompt", ""),
            size=args.get("size", "512x512"),
            steps=args.get("steps", 4),
            model=args.get("model") or None,
            guidance_scale=float(args.get("guidance_scale", 7.5)),
            negative_prompt=args.get("negative_prompt", ""),
            seed=args.get("seed"),
        )
        return result
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_list_image_models(args: dict, request: Request) -> dict:
    """List installed image-generation models."""
    try:
        from tinyagentos.tools.image_tool import execute_list_image_models

        result = await execute_list_image_models()
        return result
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_open_app(args: dict, request: Request) -> dict:
    """Open an app on the user's desktop (agent OS control)."""
    try:
        from tinyagentos.tools.desktop_tools import execute_open_app

        return await execute_open_app(args, request)
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_arrange_windows(args: dict, request: Request) -> dict:
    """Arrange the user's desktop windows (agent OS control)."""
    try:
        from tinyagentos.tools.desktop_tools import execute_arrange_windows

        return await execute_arrange_windows(args, request)
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_read_layout(args: dict, request: Request) -> dict:
    """Read the user's desktop layout: screen + window bounds (agent OS control)."""
    try:
        from tinyagentos.tools.desktop_tools import execute_read_layout

        return await execute_read_layout(args, request)
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_request_decision(args: dict, request: Request) -> dict:
    """Ask the user a question via the Decisions inbox (human-in-the-loop)."""
    try:
        from tinyagentos.tools.decision_tools import execute_request_decision

        return await execute_request_decision(args, request)
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_notify_user(args: dict, request: Request) -> dict:
    """Send the user a notification in their bell (async heads-up)."""
    try:
        from tinyagentos.tools.notify_tools import execute_notify_user

        return await execute_notify_user(args, request)
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_list_frameworks(args: dict, request: Request) -> dict:
    """List the agent frameworks taOS can deploy (so the agent can offer one)."""
    try:
        from tinyagentos.tools.framework_tools import execute_list_frameworks

        return await execute_list_frameworks(args, request)
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_list_store_apps(args: dict, request: Request) -> dict:
    """List installable Store apps/backends so the agent can offer to install one."""
    try:
        from tinyagentos.tools.store_tools import execute_list_store_apps

        return await execute_list_store_apps(args, request)
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_get_capabilities(args: dict, request: Request) -> dict:
    """Report hardware-aware capabilities so the agent gives accurate advice."""
    try:
        from tinyagentos.tools.capability_tools import execute_get_capabilities

        return await execute_get_capabilities(args, request)
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_list_projects(args: dict, request: Request) -> dict:
    """List the user's projects so the agent can pick one."""
    try:
        from tinyagentos.tools.project_tools import execute_list_projects

        return await execute_list_projects(args, request)
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_list_tasks(args: dict, request: Request) -> dict:
    """List a project's tasks so the agent can review progress or pick next."""
    try:
        from tinyagentos.tools.project_tools import execute_list_tasks

        return await execute_list_tasks(args, request)
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_create_project(args: dict, request: Request) -> dict:
    """Create a project the user can see."""
    try:
        from tinyagentos.tools.project_tools import execute_create_project

        return await execute_create_project(args, request)
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_add_task(args: dict, request: Request) -> dict:
    """Add a task to a project's board (streams live)."""
    try:
        from tinyagentos.tools.project_tools import execute_add_task

        return await execute_add_task(args, request)
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_canvas_add_image(args: dict, request: Request) -> dict:
    """Place a generated image on a project's canvas (streams live)."""
    try:
        from tinyagentos.tools.project_tools import execute_canvas_add_image

        return await execute_canvas_add_image(args, request)
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_describe_image_capabilities(args: dict, request: Request) -> dict:
    """Describe the cluster's image-gen tiers + tools (agent OS control)."""
    try:
        from tinyagentos.tools.cluster_tools import execute_describe_image_capabilities

        return await execute_describe_image_capabilities(args, request)
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_export_storybook(args: dict, request: Request) -> dict:
    """Render a project's pages + illustrations into a storybook PDF."""
    try:
        from tinyagentos.tools.project_tools import execute_export_storybook

        return await execute_export_storybook(args, request)
    except Exception as exc:
        return {"error": str(exc)}



async def _skill_notes_list_shared_docs(args: dict, request: Request) -> dict:
    """List non-archived shared docs the calling agent is a member of."""
    try:
        from tinyagentos.tools.notes_tools import execute_notes_list_shared_docs

        return await execute_notes_list_shared_docs(args, request)
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_notes_add_entry(args: dict, request: Request) -> dict:
    """Append an entry to a shared doc the agent is a member of."""
    try:
        from tinyagentos.tools.notes_tools import execute_notes_add_entry

        return await execute_notes_add_entry(args, request)
    except Exception as exc:
        return {"error": str(exc)}


async def _skill_todo_list_lists(args: dict, request: Request) -> dict:
    """List non-archived todo lists the calling agent has access to."""
    from tinyagentos.tools.todo_tools import execute_todo_list_lists

    return await execute_todo_list_lists(args, request)


async def _skill_todo_add_item(args: dict, request: Request) -> dict:
    """Append an item to a todo list the calling agent has access to."""
    from tinyagentos.tools.todo_tools import execute_todo_add_item

    return await execute_todo_add_item(args, request)


async def _skill_todo_set_done(args: dict, request: Request) -> dict:
    """Mark a todo item done/not-done on a list the agent has access to."""
    from tinyagentos.tools.todo_tools import execute_todo_set_done

    return await execute_todo_set_done(args, request)


SKILL_IMPLEMENTATIONS = {
    "memory_search": _skill_memory_search,
    "file_read": _skill_file_read,
    "file_write": _skill_file_write,
    "list_files": _skill_list_files,
    "web_search": _skill_web_search,
    "code_exec": _skill_code_exec,
    "http_request": _skill_http_request,
    "image_generation": _skill_image_generation,
    "list_image_models": _skill_list_image_models,
    "open_app": _skill_open_app,
    "arrange_windows": _skill_arrange_windows,
    "read_layout": _skill_read_layout,
    "request_decision": _skill_request_decision,
    "notify_user": _skill_notify_user,
    "list_frameworks": _skill_list_frameworks,
    "list_store_apps": _skill_list_store_apps,
    "get_capabilities": _skill_get_capabilities,
    "create_project": _skill_create_project,
    "list_projects": _skill_list_projects,
    "list_tasks": _skill_list_tasks,
    "add_task": _skill_add_task,
    "canvas_add_image": _skill_canvas_add_image,
    "describe_image_capabilities": _skill_describe_image_capabilities,
    "export_storybook": _skill_export_storybook,
    "notes_list_shared_docs": _skill_notes_list_shared_docs,
    "notes_add_entry": _skill_notes_add_entry,
    "todo_list_lists": _skill_todo_list_lists,
    "todo_add_item": _skill_todo_add_item,
    "todo_set_done": _skill_todo_set_done,
}


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


@router.get("/api/skill-exec/tools")
async def list_tools(request: Request, agent_name: str):
    """Return tool schemas for an agent's assigned skills.

    Agents call this on startup to discover their available tools. The response
    format matches the OpenAI / MCP tool definition so adapters can pass it
    straight through to framework tool registries.
    """
    credential_agent = getattr(request.state, "agent_name", None)
    if credential_agent and agent_name != credential_agent:
        return JSONResponse(
            {"error": "forbidden: agent_name does not match credential"},
            status_code=403,
        )
    skill_store = request.app.state.skills
    skills = await skill_store.get_agent_skills(agent_name)

    tools = []
    for skill in skills:
        schema = skill.get("tool_schema") or {}
        if not schema:
            continue
        skill_id = skill["id"]
        # Only advertise skills that have a wired implementation — orphaned
        # rows (seeded with INSERT OR IGNORE, then removed from the default
        # set) survive in the skills table but would 501 on call.
        if skill_id not in SKILL_IMPLEMENTATIONS:
            continue
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": schema.get("name", skill_id),
                    "description": schema.get(
                        "description", skill.get("description", "")
                    ),
                    "parameters": schema.get("input_schema", {}),
                },
                "skill_id": skill_id,
                "exec_url": f"/api/skill-exec/{skill_id}/call",
            }
        )

    return JSONResponse({"tools": tools})


def _capture_tool_receipt(request: Request, skill_id: str, args: dict, result) -> None:
    """Schedule a fire-and-forget action receipt for this tool call (#155).

    Fail-soft and off the response path: extracts the plain values it needs, then
    schedules the write on the loop so the tool response returns first. Any error
    here is swallowed; a receipt must never disrupt or slow a tool call.
    """
    try:
        from tinyagentos.receipts import emit_tool_receipt

        store = getattr(request.app.state, "receipt_store", None)
        agent = (
            getattr(request.state, "agent_name", None)
            or (args.get("agent_name") or "").strip()
        )
        if store is None or not agent:
            return
        # agent_name is identity, not a tool argument; keep it out of tool_args.
        # input_refs + files_changed (with content hashes) are derived in receipts.
        tool_args = {k: v for k, v in args.items() if k != "agent_name"}
        task = asyncio.create_task(emit_tool_receipt(
            store, agent=agent, tool_name=skill_id, args=tool_args, result=result,
        ))
        # Keep a strong reference until the task finishes, else it can be GC'd.
        _bg_receipt_tasks.add(task)
        task.add_done_callback(_bg_receipt_tasks.discard)
    except Exception:  # noqa: BLE001 - never let receipt capture touch the tool path
        # Log so a silent integration break (bad import, missing store) is visible;
        # still swallowed so the tool path is never affected.
        logger.warning("receipt capture dispatch failed for %s", skill_id, exc_info=True)


def _emit_policy_receipt(
    request: Request,
    skill_id: str,
    agent_name: str,
    *,
    stop_reason: str,
    human_approval: str | None = None,
    decision_id: str = "",
) -> None:
    """Fire-and-forget receipt for a policy-gated call that never reached
    ``impl(...)`` (deny or pending-approval). Mirrors ``_capture_tool_receipt``:
    fail-soft, off the response path, never disrupts the tool call."""
    try:
        store = getattr(request.app.state, "receipt_store", None)
        if store is None or not agent_name:
            return
        task = asyncio.create_task(store.record(
            agent_name, tool_name=skill_id, stop_reason=stop_reason,
            human_approval=human_approval, decision_id=decision_id,
        ))
        _bg_receipt_tasks.add(task)
        task.add_done_callback(_bg_receipt_tasks.discard)
    except Exception:  # noqa: BLE001 - never let receipt capture touch the tool path
        logger.warning("policy receipt dispatch failed for %s", skill_id, exc_info=True)


async def _check_execution_policy(
    request: Request, skill_id: str, agent_name: str
) -> JSONResponse | None:
    """Agent governance gate (#160 slice 1): decide whether this agent's tool
    call is allowed, denied, or needs a human approval. Returns a JSONResponse
    to short-circuit the call (deny / pending-approval), or None to let
    ``execute_skill`` proceed to ``impl(...)``.

    This is a SEPARATE, ADDITIVE check that runs strictly after
    ``_is_admin_or_local_token`` has already authorized the caller -- it never
    substitutes for that gate, only narrows what an already-authorized caller's
    agent may do.
    """
    # Governance applies only to AGENT callers (a deployed agent presenting the
    # host local token, via == "local_token"). An admin HUMAN session is the
    # user themselves driving the OS directly, so it is never gated -- the human
    # IS the approval authority, and self-approving their own tool calls is
    # noise. Local-token callers remain fully governed below.
    if (
        getattr(request.state, "via", None) == "session"
        and getattr(request.state, "is_admin", False)
    ):
        return None

    from tinyagentos.governance.action_classes import classify

    action_class = classify(skill_id)
    if action_class is None:
        return None

    policies = getattr(request.app.state, "execution_policies", None)
    if policies is None:
        # The startup wiring in app.py sets app.state.execution_policies
        # unconditionally, so an absent store here signals a misconfigured
        # app. Deny rather than silently allow.
        return JSONResponse(
            {"error": "execution_policies not configured"},
            status_code=403,
        )

    effect = await policies.effective_effect(agent_name, action_class)
    if effect == "allow":
        return None

    if effect == "deny":
        _emit_policy_receipt(request, skill_id, agent_name, stop_reason="policy_deny")
        board_audit = getattr(request.app.state, "board_audit", None)
        if board_audit is not None:
            try:
                await board_audit.record(
                    task_id=f"agent:{agent_name}",
                    event="execution_policy_deny",
                    actor=agent_name,
                    detail={"skill_id": skill_id, "action_class": action_class},
                )
            except Exception:
                logger.warning(
                    "board_audit record failed for policy deny of %s", skill_id, exc_info=True
                )
        return JSONResponse(
            {"error": "forbidden_by_policy", "action_class": action_class},
            status_code=403,
        )

    # effect == "require_approval"
    if await policies.has_live_grant(agent_name, action_class, time.time()):
        return None

    decision_id = ""
    decision_store = getattr(request.app.state, "decision_store", None)
    if decision_store is not None:
        try:
            decision = await decision_store.create(
                from_agent=agent_name,
                question=f"Agent {agent_name} wants to run {skill_id} ({action_class})",
                type="approve_deny",
                priority="blocking",
                metadata={
                    "kind": "execution_gate",
                    "agent_name": agent_name,
                    "action_class": action_class,
                    "tool": skill_id,
                },
            )
            decision_id = decision["id"]
        except Exception:
            logger.warning(
                "decision create failed for execution gate on %s", skill_id, exc_info=True
            )

    _emit_policy_receipt(
        request, skill_id, agent_name,
        stop_reason="awaiting_approval", human_approval="pending", decision_id=decision_id,
    )
    return JSONResponse(
        {"status": "pending_approval", "decision_id": decision_id, "action_class": action_class},
        status_code=202,
    )


@router.post("/api/skill-exec/{skill_id}/call")
async def execute_skill(skill_id: str, request: Request):
    """Execute a skill with the given arguments.

    Gated to admin or the host local token (see ``_is_admin_or_local_token``
    above) -- this EXECUTES skills, including ``code_exec`` (host RCE), so a
    plain non-admin user session must never reach ``impl(...)`` below
    (GHSA-h24f-gp4c-8qjm). Checked first, before touching the skill store or
    parsing args, so no attacker-controlled work happens pre-authorization.
    """
    if not _is_admin_or_local_token(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    body = await request.json()
    args = body.get("args", {})

    # Agent identity must come from the CREDENTIAL, not the body.
    # Local-token callers are bound to a single agent at deploy time; a
    # deployed agent passing another agent's handle must be rejected.
    credential_agent = getattr(request.state, "agent_name", None)
    body_agent = body.get("agent_name")
    if credential_agent:
        if body_agent and body_agent != credential_agent:
            return JSONResponse(
                {"error": "forbidden: agent_name does not match credential"},
                status_code=403,
            )
        args["agent_name"] = credential_agent
    elif body_agent:
        args["agent_name"] = body_agent

    skill_store = request.app.state.skills
    skill = await skill_store.get_skill(skill_id)
    if not skill:
        return JSONResponse(
            {"error": f"Skill {skill_id} not found"}, status_code=404
        )

    impl = SKILL_IMPLEMENTATIONS.get(skill_id)
    if not impl:
        return JSONResponse(
            {"error": f"No implementation for {skill_id}"}, status_code=501
        )

    # Agent governance (#160 slice 1): runs after the auth gate above, before
    # the skill actually executes. agent_name mirrors the receipt capture's
    # extraction below (args, populated from the request body).
    agent_name = (args.get("agent_name") or "").strip()
    gate_response = await _check_execution_policy(request, skill_id, agent_name)
    if gate_response is not None:
        return gate_response

    try:
        result = await impl(args, request)
        _capture_tool_receipt(request, skill_id, args, result)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
