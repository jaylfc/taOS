"""Agent-side tools for shared notes and lists.

Lets an agent list the docs it is a member of, and append a new entry to any
doc it belongs to. The loop guard (skip_agent) prevents the writing agent from
being notified about its own write.
"""

from __future__ import annotations

import logging

from fastapi import Request

logger = logging.getLogger(__name__)


async def execute_notes_list_shared_docs(args: dict, request: Request) -> dict:
    """List non-archived shared docs the calling agent is a member of."""
    args = args or {}
    agent_name = args.get("agent_name")
    if not agent_name or not isinstance(agent_name, str):
        return {"error": "notes_list_shared_docs requires an 'agent_name' string"}
    try:
        store = request.app.state.shared_docs_store
        docs = await store.docs_for_agent(agent_name)
        return {"docs": docs}
    except Exception as exc:
        return {"error": str(exc)}


async def execute_notes_add_entry(args: dict, request: Request) -> dict:
    """Append an entry to a shared doc the calling agent is a member of.

    The agent must have 'contributor' or 'editor' permission on the doc.
    'viewer' agents are rejected.
    """
    args = args or {}
    agent_name = args.get("agent_name")
    doc_id = args.get("doc_id")
    text = args.get("text")

    if not agent_name or not isinstance(agent_name, str):
        return {"error": "notes_add_entry requires an 'agent_name' string"}
    if not doc_id or not isinstance(doc_id, str):
        return {"error": "notes_add_entry requires a 'doc_id' string"}
    if not isinstance(text, str) or not text:
        return {"error": "notes_add_entry requires a 'text' string"}

    try:
        store = request.app.state.shared_docs_store

        members = await store.agent_members(doc_id)
        agent_member = next((m for m in members if m["agent"] == agent_name), None)
        if agent_member is None:
            return {"error": "agent does not have write permission on this doc"}

        perm = agent_member.get("permission", "contributor")
        if perm not in ("contributor", "editor"):
            return {"error": "agent does not have write permission on this doc"}

        doc = await store.get_doc(doc_id)
        if doc is None:
            return {"error": "doc not found"}
        if doc.get("archived_at") is not None:
            return {"error": "doc is archived"}

        entry = await store.add_entry(doc_id, text, author=agent_name, editor_type="agent")

        from tinyagentos.routes.notes import _trigger_agent_notifications

        try:
            await _trigger_agent_notifications(request, doc, text, skip_agent=agent_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("notes_add_entry: agent trigger failed: %s", exc)

        return {"ok": True, "entry_id": entry["id"]}
    except Exception as exc:
        return {"error": str(exc)}

