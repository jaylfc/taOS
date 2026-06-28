"""Shared notes and lists REST API.

Documents (notes and lists) are created by a user, who can invite agent
members. Each agent member carries a standing_instruction that describes
what the agent should do when a new entry is added. When an entry is
added, the route posts a message to each agent's DM channel (best-effort).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.agent_db import find_agent
from tinyagentos.auth_context import CurrentUser, current_user

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------- models

class CreateDocIn(BaseModel):
    kind: str
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


# -------------------------------------------------------------------- helpers

def _get_store(request: Request):
    return request.app.state.shared_docs_store


def _check_owner(doc: dict, user: CurrentUser):
    """Return a 403 JSONResponse if the caller does not own the doc, else None.

    Used for writes and doc/member management, which stay owner-only in this
    foundation. Member and agent writes land with the revisions work.
    """
    if not user.is_admin and doc["owner_user_id"] != user.user_id:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return None


async def _check_read_access(store, doc: dict, user: CurrentUser):
    """Allow the owner, an admin, or a user-type member to read the doc.

    Consistent with list_docs, which surfaces a doc to the users it is shared
    with as a user-type member; without this they would see the doc in their
    listing and then get a 403 opening it.
    """
    if user.is_admin or doc["owner_user_id"] == user.user_id:
        return None
    for m in await store.list_members(doc["id"]):
        if m.get("member_type") == "user" and m.get("member_id") == user.user_id:
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
        doc = await store.create_doc(user.user_id, body.kind, body.title)
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

async def _trigger_agent_notifications(
    request: Request,
    doc: dict,
    entry_text: str,
) -> None:
    """Post a message to each agent member's DM channel. Never raises."""
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
        instruction = am["standing_instruction"]
        if instruction:
            content = f"[{doc_title}] {instruction}: {entry_text}"
        else:
            content = f"[{doc_title}] New entry: {entry_text}"
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
    err = _check_owner(doc, user)
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
    err = _check_owner(doc, user)
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
    err = _check_owner(doc, user)
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
    err = _check_owner(doc, user)
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
    try:
        await store.add_member(
            doc_id,
            body.member_type,
            body.member_id,
            body.standing_instruction,
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
