"""Todo list REST API.

Todo lists are owned by a single user. Items are ordered by position
and can have optional due dates and reminders.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from tinyagentos.auth_context import CurrentUser, current_user

router = APIRouter()


# --------------------------------------------------------------------- models

class CreateTodoListIn(BaseModel):
    title: str = ""


class PatchTodoListIn(BaseModel):
    title: str | None = None
    archived: bool | None = None


class AddTodoItemIn(BaseModel):
    text: str = Field(..., min_length=1)
    due_at: str | None = None
    remind_at: str | None = None

    @field_validator("text")
    @classmethod
    def _strip_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("text must not be empty or whitespace-only")
        return v


class PatchTodoItemIn(BaseModel):
    text: str | None = None
    done: bool | None = None
    due_at: str | None = None
    remind_at: str | None = None

    @field_validator("text")
    @classmethod
    def _validate_text(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("text must not be empty or whitespace-only")
        return v


class ReorderEntry(BaseModel):
    id: str
    position: int


class ReorderItemsIn(BaseModel):
    items: list[ReorderEntry]


# -------------------------------------------------------------------- helpers

def _get_store(request: Request):
    store = getattr(request.app.state, "todo_store", None)
    if store is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="todo_store not available")
    return store


def _check_owner(doc: dict, user: CurrentUser):
    """Return a 403 JSONResponse if the caller does not own the list, else None."""
    if not user.is_admin and doc["owner_user_id"] != user.user_id:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return None


# ----------------------------------------------------------------- list routes

@router.get("/api/todo")
async def list_lists(
    request: Request,
    include_archived: bool = False,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    return await store.list_lists(user.user_id, include_archived=include_archived)


@router.post("/api/todo")
async def create_list(
    body: CreateTodoListIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    return await store.create_list(user.user_id, body.title)


@router.get("/api/todo/{list_id}")
async def get_list(
    list_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    doc = await store.get_list(list_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    err = _check_owner(doc, user)
    if err:
        return err
    return doc


@router.patch("/api/todo/{list_id}")
async def patch_list(
    list_id: str,
    body: PatchTodoListIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    doc = await store.get_list(list_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    err = _check_owner(doc, user)
    if err:
        return err
    if body.title is not None:
        await store.set_title(list_id, body.title)
    if body.archived is True:
        await store.archive_list(list_id)
    elif body.archived is False:
        await store.unarchive_list(list_id)
    return await store.get_list(list_id)


# ---------------------------------------------------------------- item routes

@router.post("/api/todo/{list_id}/items")
async def add_item(
    list_id: str,
    body: AddTodoItemIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    doc = await store.get_list(list_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    err = _check_owner(doc, user)
    if err:
        return err

    from datetime import datetime, timezone

    due_at = None
    if body.due_at:
        try:
            dt = datetime.fromisoformat(body.due_at)
        except ValueError:
            return JSONResponse(
                {"error": f"invalid due_at: {body.due_at!r}"}, status_code=400
            )
        if dt.tzinfo is None:
            return JSONResponse(
                {"error": f"due_at requires timezone offset: {body.due_at!r}"},
                status_code=400,
            )
        due_at = dt.timestamp()
    remind_at = None
    if body.remind_at:
        try:
            dt = datetime.fromisoformat(body.remind_at)
        except ValueError:
            return JSONResponse(
                {"error": f"invalid remind_at: {body.remind_at!r}"}, status_code=400
            )
        if dt.tzinfo is None:
            return JSONResponse(
                {"error": f"remind_at requires timezone offset: {body.remind_at!r}"},
                status_code=400,
            )
        remind_at = dt.timestamp()

    return await store.add_item(
        list_id, body.text, author=user.user_id, due_at=due_at, remind_at=remind_at
    )


@router.patch("/api/todo/{list_id}/items/{item_id}")
async def patch_item(
    list_id: str,
    item_id: str,
    body: PatchTodoItemIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    doc = await store.get_list(list_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    err = _check_owner(doc, user)
    if err:
        return err

    item = await store.get_item(item_id)
    if item is None:
        return JSONResponse({"error": "item not found"}, status_code=404)
    if item["list_id"] != list_id:
        return JSONResponse({"error": "item not in list"}, status_code=404)

    from datetime import datetime, timezone

    _CLEAR_SENTINEL = -1.0

    def _parse_ts(val):
        """None = no change, '' = clear, ISO str = set.

        Returns None (not sent), _CLEAR_SENTINEL (clear), a float
        timestamp (valid aware datetime), or None (error — handled below).
        Rejects naive (offsetless) datetime strings.
        """
        if val is None:
            return None  # not sent
        if val == "":
            return _CLEAR_SENTINEL
        try:
            dt = datetime.fromisoformat(val)

        except ValueError:
            return None  # Will be handled below
        if dt.tzinfo is None:
            return None  # naive — handled below as error
        return dt.timestamp()

    due_at = _parse_ts(body.due_at)
    if due_at is None and body.due_at is not None and body.due_at != "":
        return JSONResponse(
            {"error": f"invalid due_at: {body.due_at!r}"}, status_code=400
        )

    remind_at = _parse_ts(body.remind_at)
    if remind_at is None and body.remind_at is not None and body.remind_at != "":
        return JSONResponse(
            {"error": f"invalid remind_at: {body.remind_at!r}"}, status_code=400
        )

    kwargs = {}
    if body.text is not None:
        kwargs["text"] = body.text
    if body.done is not None:
        kwargs["done"] = body.done
    if body.due_at is not None:
        kwargs["due_at"] = None if due_at == _CLEAR_SENTINEL else due_at
    if body.remind_at is not None:
        kwargs["remind_at"] = None if remind_at == _CLEAR_SENTINEL else remind_at

    return await store.patch_item(item_id, **kwargs)


@router.delete("/api/todo/{list_id}/items/{item_id}")
async def delete_item(
    list_id: str,
    item_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    doc = await store.get_list(list_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    err = _check_owner(doc, user)
    if err:
        return err

    item = await store.get_item(item_id)
    if item is None:
        return JSONResponse({"error": "item not found"}, status_code=404)
    if item["list_id"] != list_id:
        return JSONResponse({"error": "item not in list"}, status_code=404)

    await store.delete_item(item_id)
    return JSONResponse({"ok": True})


@router.put("/api/todo/{list_id}/items/reorder")
async def reorder_items(
    list_id: str,
    body: ReorderItemsIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = _get_store(request)
    doc = await store.get_list(list_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    err = _check_owner(doc, user)
    if err:
        return err

    items = [{"id": e.id, "position": e.position} for e in body.items]
    try:
        await store.reorder_items(list_id, items)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


# ------------------------------------------------------------ migration route


@router.post("/api/todo/migrate")
async def migrate_notes_lists(
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Migrate kind=list docs from shared notes into Todo lists.

    Admin-only. Idempotent — safe to run multiple times.
    Reads all non-archived kind=list docs, converts each to a todo
    list with items, then deletes the originals from shared_docs.
    """
    if not user.is_admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    shared = getattr(request.app.state, "shared_docs_store", None)
    if shared is None:
        return JSONResponse(
            {"error": "shared_docs_store not available"}, status_code=500
        )

    todo = _get_store(request)

    from tinyagentos.todo.migration import migrate_list_docs

    result = await migrate_list_docs(shared, todo)
    return result
