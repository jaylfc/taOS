"""Human-readable names for ids that appear in notification text.

A notification is often the only surface a decision is made from, so its text
must name things the way the reader knows them: the project's name, the agent's
registered handle, the user's name, the task's title. These helpers resolve an
id to that name and fall back to the id itself when no name exists (or a
lookup fails), so building a notification can never fail on a lookup.
"""
from __future__ import annotations

from typing import Any, Optional


async def project_label(app_state: Any, project_id: Optional[str]) -> str:
    """The project's name, or *project_id* when it has none or cannot be read."""
    if not project_id:
        return ""
    store = getattr(app_state, "project_store", None)
    if store is not None:
        try:
            project = await store.get_project(project_id)
        except Exception:
            project = None
        name = (project or {}).get("name")
        if name:
            return name
    return project_id


async def actor_label(app_state: Any, actor_id: Optional[str]) -> str:
    """The display name of an agent canonical id or a user id.

    Agents are looked up first (registered handle, then display name), then
    users (full name, then username). An id that resolves to neither, or a free
    text actor such as ``"user"``, is returned unchanged.
    """
    if not actor_id:
        return actor_id or ""
    registry = getattr(app_state, "agent_registry", None)
    if registry is not None:
        try:
            record = await registry.get(actor_id)
        except Exception:
            record = None
        if record:
            name = record.get("handle") or record.get("display_name")
            if name:
                return name
    auth = getattr(app_state, "auth", None)
    if auth is not None:
        try:
            user = auth.get_user_by_id(actor_id)
        except Exception:
            user = None
        if user:
            name = user.get("full_name") or user.get("username")
            if name:
                return name
    return actor_id


def task_label(task: Optional[dict], task_id: str) -> str:
    """The task's title in quotes, or *task_id* when the task has no title."""
    title = (task or {}).get("title")
    return f'"{title}"' if title else task_id
