"""Share destinations - discover ingest endpoints the calling device can write to."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.device_auth import require_device

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/share/destinations")
async def list_share_destinations(request: Request):
    device = await require_device(request)
    user_id = device.get("user_id", "")
    destinations = []

    destinations.append({
        "kind": "library",
        "id": "library",
        "label": "Library",
    })

    pstore = request.app.state.project_store
    for project in await pstore.list_for_user(user_id):
        destinations.append({
            "kind": "project_files",
            "id": project["slug"],
            "label": project["name"],
        })

    ch_store = request.app.state.chat_channels
    seen = set()
    registry = getattr(request.app.state, "agent_registry", None)
    user_id_str = str(user_id)
    for ch in await ch_store.list_channels():
        members = ch.get("members") or []
        if user_id_str not in members and "user" not in members:
            continue
        for member in members:
            if member in (user_id_str, "user"):
                continue
            if member in seen:
                continue
            label = member
            # A multi-user channel can carry another HUMAN username as a
            # member; a username that happens to equal an agent slug must not
            # resolve to that agent's chat. Skip known usernames outright.
            auth = getattr(request.app.state, "auth", None)
            if auth is not None:
                try:
                    if auth.find_user(member) is not None:
                        continue
                except Exception:
                    # Fail CLOSED: if we cannot verify the member is not a
                    # human, do not offer it as an agent destination.
                    continue
            if registry is not None:
                try:
                    agent = await registry.get(member)
                    if agent is None:
                        # DM channels store the agent slug (not its canonical_id)
                        # as the member, so slug members need a slug lookup.
                        agent = await registry.get_by_slug(member)
                    if agent and agent.get("status") == "active":
                        seen.add(member)
                        destinations.append({
                            "kind": "agent_chat",
                            "id": member,
                            "label": agent.get("display_name") or member,
                        })
                except RuntimeError:
                    pass

    return {"destinations": destinations}
