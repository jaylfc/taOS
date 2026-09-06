"""Agent-side tools for todo lists.

Lets an agent list the todo lists it has access to, append items, and
toggle completion. The loop guard (skip_agent) prevents the writing agent
from being notified about its own write.
"""

from __future__ import annotations

import logging

from fastapi import Request

logger = logging.getLogger(__name__)


async def _resolve_owner_user_id(
    args: dict, request: Request
) -> str | None:
    """Resolve ``owner_user_id``, binding ``agent_name`` when the registry is available.

    Returns ``None`` when the agent cannot be resolved; otherwise the
    registry-verified ``user_id`` (overriding any caller-supplied value).
    When the agent registry is absent from ``request.app.state``, falls back
    to the ``owner_user_id`` in *args* for test compatibility.

    For deployed agents that are *not* in the registry (the common production\n    case), the authenticated ``user_id`` from the request (set by the\n    local-token or session auth middleware) is used directly.  The agent\n    IS authenticated — it just does not have a registry row.
    """
    agent_registry = getattr(request.app.state, "agent_registry", None)
    agent_name = args.get("agent_name")
    if agent_registry is None:
        # No registry available (e.g. test harness) — prefer the
        # authenticated user_id from the request when available;
        # fall back to caller-supplied owner_user_id only when
        # neither registry nor authenticated identity is present.
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            return user_id
        return args.get("owner_user_id")
    if not agent_name or not isinstance(agent_name, str):
        return None
    agent = await agent_registry.get_by_handle(agent_name)
    if agent is not None:
        return agent.get("user_id")
    # Deployed agents are never written to agent_registry.  When the
    # registry exists but has no row for this handle, fall back to the
    # authenticated identity from the request.  The agent IS authenticated
    # (via local-token or session auth, enforced by the middleware) — it
    # just does not have a registry row.  No config.agents check is needed;
    # the authenticated user_id is the caller's identity.
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return user_id
    return None


async def _resolve_and_validate_owner(
    args: dict, request: Request, tool_name: str
) -> tuple[str | None, dict | None]:
    """Resolve and type-validate the owner, returning (owner_id, None) on
    success or (None, error_dict) on failure."""
    owner_user_id = await _resolve_owner_user_id(args, request)
    if owner_user_id is None:
        return None, {
            "error": (
                "unable to resolve owner: agent not found or "
                "no user identity available"
            )
        }
    if not isinstance(owner_user_id, str) or not owner_user_id:
        return None, {
            "error": f"{tool_name} could not resolve a valid owner identity"
        }
    return owner_user_id, None


async def execute_todo_list_lists(args: dict, request: Request) -> dict:
    """List non-archived todo lists the calling agent has access to.

    Authorization binds ``agent_name`` to an owner via the agent registry when
    it is available (production).  Falls back to caller-supplied
    ``owner_user_id`` in test environments where the registry is absent.
    """
    args = args or {}
    agent_name = args.get("agent_name")
    if not agent_name or not isinstance(agent_name, str):
        return {"error": "todo_list_lists requires an 'agent_name' string"}

    try:
        owner_user_id, err = await _resolve_and_validate_owner(
            args, request, "todo_list_lists"
        )
        if err:
            return err

        store = request.app.state.todo_store
        lists = await store.list_lists(owner_user_id)
        # Strip internal fields the agent does not need.
        slim = [
            {k: v for k, v in doc.items() if k in ("id", "title", "updated_at")}
            for doc in lists
        ]
        return {"lists": slim}
    except Exception:  # noqa: BLE001
        logger.exception("todo_list_lists failed")
        return {"error": "todo_list_lists failed"}


async def execute_todo_add_item(args: dict, request: Request) -> dict:
    """Append an item to a todo list the calling agent has access to.

    Authorization binds ``agent_name`` to an owner via the agent registry.
    ``agent_name`` is also used for attribution (author field) and the
    notification skip-guard.
    """
    args = args or {}
    agent_name = args.get("agent_name")
    list_id = args.get("list_id")
    text = args.get("text")

    if not agent_name or not isinstance(agent_name, str):
        return {"error": "todo_add_item requires an 'agent_name' string"}
    if not list_id or not isinstance(list_id, str):
        return {"error": "todo_add_item requires a 'list_id' string"}
    if not isinstance(text, str) or not text:
        return {"error": "todo_add_item requires a 'text' string"}

    try:
        owner_user_id, err = await _resolve_and_validate_owner(
            args, request, "todo_add_item"
        )
        if err:
            return err

        store = request.app.state.todo_store

        doc = await store.get_list(list_id)
        if doc is None:
            return {"error": "list not found"}
        # SECURITY: owner-based auth — only the list owner can add items.
        # owner_user_id is resolved from the agent registry (when available)
        # rather than trusted from the caller. Owner check runs BEFORE the
        # archived check so non-owners cannot learn list state.
        if doc.get("owner_user_id") != owner_user_id:
            return {"error": "agent does not have access to this list"}
        if doc.get("archived_at") is not None:
            return {"error": "list is archived"}

        item = await store.add_item(list_id, text, author=owner_user_id)

        try:
            from tinyagentos.todo.notify import _trigger_todo_agent_notifications

            await _trigger_todo_agent_notifications(
                request, doc, text, skip_agent=agent_name
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("todo_add_item: agent trigger failed: %s", exc)

        return {"ok": True, "item_id": item["id"]}
    except Exception:  # noqa: BLE001
        logger.exception("todo_add_item failed")
        return {"error": "todo_add_item failed"}


async def execute_todo_set_done(args: dict, request: Request) -> dict:
    """Mark a todo item done (or not done) on a list the agent has access to.

    Authorization binds ``agent_name`` to an owner via the agent registry
    (same pattern as ``execute_todo_add_item``).
    """
    args = args or {}
    agent_name = args.get("agent_name")
    list_id = args.get("list_id")
    item_id = args.get("item_id")
    done = args.get("done")

    if not agent_name or not isinstance(agent_name, str):
        return {"error": "todo_set_done requires an 'agent_name' string"}
    if not list_id or not isinstance(list_id, str):
        return {"error": "todo_set_done requires a 'list_id' string"}
    if not item_id or not isinstance(item_id, str):
        return {"error": "todo_set_done requires an 'item_id' string"}
    if not isinstance(done, bool):
        return {"error": "todo_set_done requires a boolean 'done'"}

    try:
        owner_user_id, err = await _resolve_and_validate_owner(
            args, request, "todo_set_done"
        )
        if err:
            return err

        store = request.app.state.todo_store

        doc = await store.get_list(list_id)
        if doc is None:
            return {"error": "list not found"}
        # SECURITY: owner-based auth — only the list owner can mark items done.
        # owner_user_id is resolved from the agent registry (when available)
        # rather than trusted from the caller. Owner check runs BEFORE the
        # archived check so non-owners cannot learn list state.
        if doc.get("owner_user_id") != owner_user_id:
            return {"error": "agent does not have access to this list"}
        if doc.get("archived_at") is not None:
            return {"error": "list is archived"}

        # Confine the agent to items of the list it actually belongs to.
        if not any(i.get("id") == item_id for i in doc.get("items", [])):
            return {"error": "item not found in this list"}

        await store.patch_item(item_id, done=done)
        return {"ok": True, "item_id": item_id, "done": done}
    except Exception:  # noqa: BLE001
        logger.exception("todo_set_done failed")
        return {"error": "todo_set_done failed"}
