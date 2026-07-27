"""Shared notes REST API.

Notes are created by a user, who can invite agent members. Each agent
member carries a standing_instruction that describes what the agent should
do when a new entry is added. When an entry is added, the route posts a
message to each agent's DM channel (best-effort).

Todo lists have been split into their own API at ``/api/todo`` using a
dedicated ``TodoStore`` (``tinyagentos.todo``).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.agent_db import find_agent
from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.notes.shared_docs_store import VALID_ACTIONS, VALID_PERMISSIONS

logger = logging.getLogger(__name__)

router = APIRouter()

# Action directives used in agent trigger messages.
_ACTION_DIRECTIVE = {
    "research": "Research this:",
    "plan": "Plan this:",
    "critique": "Critique this:",
    "build": "Start building:",
    "discuss": "Discuss this with the user, ask clarifying questions to develop it:",
}


# --------------------------------------------------------------------- models

class CreateDocIn(BaseModel):
    title: str = ""


class PatchDocIn(BaseModel):
    title: str | None = None
    archived: bool | None = None


class AddEntryIn(BaseModel):
    text: str


class PatchEntryIn(BaseModel):
    done: bool


class EditEntryTextIn(BaseModel):
    text: str


class AddMemberIn(BaseModel):
    member_type: str
    member_id: str
    standing_instruction: str = ""
    permission: str = "contributor"
    action: str | None = None


# -------------------------------------------------------------------- helpers

def _get_store(request: Request):
    return request.app.state.shared_docs_store


def _check_owner(doc: dict, user: CurrentUser):
    """Return a 403 JSONResponse if the caller does not own the doc, else None."""
    if not user.is_admin and doc["owner_user_id"] != user.user_id:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return None


async def _check_read_access(store, doc: dict, user: CurrentUser):
    """Allow the owner, an admin, or any user-type member to read the doc."""
    if user.is_admin or doc["owner_user_id"] == user.user_id:
        return None
    for m in await store.list_members(doc["id"]):
        if m.get("member_type") == "user" and m.get("member_id") == user.user_id:
            return None
    return JSONResponse({"error": "forbidden"}, status_code=403)


async def _check_add_access(store, doc: dict, user: CurrentUser):
    """Owner/admin can always add. User members need contributor or editor."""
    if user.is_admin or doc["owner_user_id"] == user.user_id:
        return None
    perm = await store.member_permission(doc["id"], "user", user.user_id, doc["owner_user_id"])
    if perm in ("contributor", "editor"):
        return None
    return JSONResponse({"error": "forbidden"}, status_code=403)


async def _check_edit_access(store, doc: dict, user: CurrentUser):
    """Owner/admin can always edit/delete/toggle. User members need editor."""
    if user.is_admin or doc["owner_user_id"] == user.user_id:
        return None
    perm = await store.member_permission(doc["id"], "user", user.user_id, doc["owner_user_id"])
    if perm == "editor":
        return None
    return JSONResponse({"error": "forbidden"}, status_code=403)


# ------------------------------------------------------------------ doc routes

@router.get("/api/notes")
async def list_docs(
    request: Request,
    include_archived: bool = False,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    return await store.list_docs(user.user_id, include_archived=include_archived)


@router.post("/api/notes")
async def create_doc(
    body: CreateDocIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    try:
        doc = await store.create_doc(user.user_id, "note", body.title)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return doc


@router.get("/api/notes/{doc_id}")
async def get_doc(
    doc_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    doc = await store.get_doc(doc_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    err = await _check_read_access(store, doc, user)
    if err:
        return err
    return doc


@router.patch("/api/notes/{doc_id}")
async def patch_doc(
    doc_id: str,
    body: PatchDocIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    doc = await store.get_doc(doc_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    err = _check_owner(doc, user)
    if err:
        return err
    if body.title is not None:
        await store.set_title(doc_id, body.title)
    if body.archived is True:
        await store.archive_doc(doc_id)
    return await store.get_doc(doc_id)


# --------------------------------------------------------------- entry routes

async def _ensure_discuss_channel(request: Request, doc: dict, agent_name: str) -> str | None:
    """Create or reuse the topic channel an agent's "discuss" action threads in.

    Returns the channel id, or None if the chat channel store is unavailable.
    The discussion stays threaded per (doc, agent): repeated entries land in one
    channel instead of forking a new one each time. The agent is set as a lead so
    it actively asks the user clarifying questions rather than staying quiet.
    """
    store = _get_store(request)
    channels = getattr(request.app.state, "chat_channels", None)
    if channels is None:
        return None
    existing = await store.discuss_channel_for(doc["id"], agent_name)
    if existing:
        ch = await channels.get_channel(existing)
        if ch is not None:
            return existing
    owner = doc.get("owner_user_id") or "system"
    title = doc.get("title", "") or "shared doc"
    created = await channels.create_channel(
        name=f"Discuss: {title}",
        type="topic",
        created_by=owner,
        members=sorted({owner, agent_name}),
        description=f"Idea discussion for a shared doc with {agent_name}.",
        settings={"kind": "notes-discuss", "leads": [agent_name], "response_mode": "lively"},
    )
    ch_id = created.get("id") if isinstance(created, dict) else None
    if ch_id:
        await store.set_discuss_channel(doc["id"], agent_name, ch_id)
    return ch_id


async def _trigger_agent_notifications(
    request: Request,
    doc: dict,
    entry_text: str,
    skip_agent: str | None = None,
) -> None:
    """Notify each agent member on a new entry. Never raises.

    Most agents are pinged on their DM channel. An agent whose action is
    "discuss" instead gets a dedicated topic channel (created or reused) so it
    can develop the idea with the user there; if that fails it falls back to the
    DM so the agent is never silently skipped. skip_agent names the agent that
    authored this change, if any, so an agent does not get notified about its
    own entry.
    """
    store = _get_store(request)
    agents = await store.agent_members(doc["id"])
    if not agents:
        return

    message_store = getattr(request.app.state, "chat_messages", None)
    config = getattr(request.app.state, "config", None)
    if message_store is None or config is None:
        return

    doc_title = doc.get("title", "")

    for am in agents:
        agent_name = am["agent"]
        if skip_agent is not None and agent_name == skip_agent:
            continue
        action = am.get("action")
        instruction = am["standing_instruction"]
        if action:
            directive = _ACTION_DIRECTIVE.get(action, "New entry:")
            content = f"[{doc_title}] {directive} {entry_text}"
            if instruction:
                content += f" ({instruction})"
        elif instruction:
            content = f"[{doc_title}] {instruction}: {entry_text}"
        else:
            content = f"[{doc_title}] New entry: {entry_text}"
        if action == "discuss":
            try:
                ch_id = await _ensure_discuss_channel(request, doc, agent_name)
                if ch_id:
                    await message_store.send_message(
                        channel_id=ch_id,
                        author_id="system",
                        author_type="system",
                        content=content,
                    )
                    continue
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "notes: discuss channel failed for %r, falling back to DM: %s",
                    agent_name,
                    exc,
                )
        try:
            agent_cfg = find_agent(config, agent_name)
            channel_id = (agent_cfg or {}).get("chat_channel_id")
            if not channel_id:
                logger.debug(
                    "notes: no DM channel for agent %r, skipping trigger", agent_name
                )
                continue
            await message_store.send_message(
                channel_id=channel_id,
                author_id="system",
                author_type="system",
                content=content,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("notes: failed to notify agent %r: %s", agent_name, exc)


@router.post("/api/notes/{doc_id}/entries")
async def add_entry(
    doc_id: str,
    body: AddEntryIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    doc = await store.get_doc(doc_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    err = await _check_add_access(store, doc, user)
    if err:
        return err
    entry = await store.add_entry(doc_id, body.text, author=user.user_id)
    # Best-effort: agent notification failure must not fail the request.
    try:
        await _trigger_agent_notifications(request, doc, body.text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("notes: agent trigger raised unexpectedly: %s", exc)
    return entry


@router.patch("/api/notes/{doc_id}/entries/{entry_id}")
async def patch_entry(
    doc_id: str,
    entry_id: str,
    body: PatchEntryIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    doc = await store.get_doc(doc_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    err = await _check_edit_access(store, doc, user)
    if err:
        return err
    await store.set_entry_done(entry_id, body.done)
    return JSONResponse({"ok": True})


@router.delete("/api/notes/{doc_id}/entries/{entry_id}")
async def delete_entry(
    doc_id: str,
    entry_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    doc = await store.get_doc(doc_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    err = await _check_edit_access(store, doc, user)
    if err:
        return err
    await store.delete_entry(entry_id)
    return JSONResponse({"ok": True})


@router.patch("/api/notes/{doc_id}/entries/{entry_id}/text")
async def edit_entry_text(
    doc_id: str,
    entry_id: str,
    body: EditEntryTextIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    doc = await store.get_doc(doc_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    err = await _check_edit_access(store, doc, user)
    if err:
        return err
    try:
        entry = await store.edit_entry(entry_id, body.text, editor_id=user.user_id, editor_type="user")
    except KeyError:
        return JSONResponse({"error": "entry not found"}, status_code=404)
    return entry


@router.get("/api/notes/{doc_id}/entries/{entry_id}/history")
async def entry_history(
    doc_id: str,
    entry_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    doc = await store.get_doc(doc_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    err = await _check_read_access(store, doc, user)
    if err:
        return err
    return await store.list_revisions(entry_id)


@router.get("/api/notes/{doc_id}/entries/{entry_id}/at/{rev_index}")
async def entry_at_revision(
    doc_id: str,
    entry_id: str,
    rev_index: int,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    doc = await store.get_doc(doc_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    err = await _check_read_access(store, doc, user)
    if err:
        return err
    try:
        text = await store.entry_text_at(entry_id, rev_index)
        diff = await store.revision_diff(entry_id, rev_index)
    except KeyError:
        return JSONResponse({"error": "not found"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return {"text": text, "diff": diff}


# -------------------------------------------------------------- member routes

@router.get("/api/notes/{doc_id}/members")
async def list_members(
    doc_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    doc = await store.get_doc(doc_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    err = await _check_read_access(store, doc, user)
    if err:
        return err
    return await store.list_members(doc_id)


@router.post("/api/notes/{doc_id}/members")
async def add_member(
    doc_id: str,
    body: AddMemberIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    doc = await store.get_doc(doc_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    err = _check_owner(doc, user)
    if err:
        return err
    if body.permission not in VALID_PERMISSIONS:
        return JSONResponse(
            {"error": f"invalid permission {body.permission!r}; expected one of {VALID_PERMISSIONS}"},
            status_code=400,
        )
    if body.action is not None and body.action not in VALID_ACTIONS:
        return JSONResponse(
            {"error": f"invalid action {body.action!r}; expected one of {VALID_ACTIONS} or null"},
            status_code=400,
        )
    try:
        await store.add_member(
            doc_id,
            body.member_type,
            body.member_id,
            body.standing_instruction,
            permission=body.permission,
            action=body.action,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return await store.list_members(doc_id)


@router.delete("/api/notes/{doc_id}/members/{member_type}/{member_id}")
async def remove_member(
    doc_id: str,
    member_type: str,
    member_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    doc = await store.get_doc(doc_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    err = _check_owner(doc, user)
    if err:
        return err
    await store.remove_member(doc_id, member_type, member_id)
    return JSONResponse({"ok": True})
