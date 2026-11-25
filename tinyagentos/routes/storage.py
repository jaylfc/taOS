"""Storage areas: list disks with their safety class, add a disk, set a person's primary home.

Admin or owner only. An agent (a request carrying an agent identity) can never
format a disk or assign a home directly: it files a proposal, and a person
approves it, typing the disk's model and serial for an add.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import current_user, require_admin
from tinyagentos.storage_engine import StorageError

router = APIRouter()


def _engine(request: Request):
    return request.app.state.storage_engine


def _agent(request: Request) -> str | None:
    return getattr(request.state, "agent_name", None)


def _person_only(request: Request) -> None:
    """Direct, state-changing storage calls are for a person, never an agent."""
    if _agent(request):
        raise HTTPException(
            status_code=403,
            detail="agents cannot change storage directly: file a proposal (POST /api/storage/proposals) "
                   "and the person approves it",
        )


def _actor(request: Request) -> str:
    return getattr(request.state, "user_id", None) or "local"


async def _run(coro):
    try:
        return await coro
    except StorageError as exc:
        return JSONResponse({"error": str(exc), **exc.detail}, status_code=exc.status)


class AddArea(BaseModel):
    disk: str
    name: str
    kind: str = "private"
    confirm: str


class SetPrimary(BaseModel):
    person: str
    area: str


class Propose(BaseModel):
    action: str
    params: dict


class Approve(BaseModel):
    confirm: str | None = None


@router.get("/api/storage/status", dependencies=[Depends(require_admin)])
async def storage_status(request: Request):
    return await _run(_engine(request).helper_status())


@router.get("/api/storage/disks", dependencies=[Depends(require_admin)])
async def list_disks(request: Request):
    """Every disk with class ``available`` or ``protected`` and the reasons."""
    return await _run(_engine(request).list_disks())


@router.get("/api/storage/areas", dependencies=[Depends(require_admin)])
async def list_areas(request: Request):
    try:
        return {"areas": _engine(request).list_areas()}
    except StorageError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)


@router.post("/api/storage/areas", dependencies=[Depends(require_admin)])
async def add_area(body: AddArea, request: Request):
    _person_only(request)
    return await _run(_engine(request).add_area(body.disk, body.name, body.kind, body.confirm, _actor(request)))


@router.put("/api/storage/primary", dependencies=[Depends(require_admin)])
async def set_primary(body: SetPrimary, request: Request):
    _person_only(request)
    return await _run(_engine(request).set_primary(body.person, body.area, _actor(request)))


@router.get("/api/storage/primary")
async def get_primary(request: Request, person: str | None = None):
    """A person's primary home with its capacity and free space (what a phone shows).
    A person reads their own; an admin may read anyone's."""
    user = current_user(request)
    target = person or user.user_id
    if target != user.user_id and not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")
    try:
        home = _engine(request).primary_home(target)
    except StorageError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)
    if home is None:
        raise HTTPException(status_code=404, detail="no primary home set")
    return home


@router.post("/api/storage/proposals", dependencies=[Depends(require_admin)])
async def propose(body: Propose, request: Request):
    agent = _agent(request)
    try:
        return _engine(request).propose(body.action, body.params, agent or _actor(request),
                                        getattr(request.state, "user_id", None))
    except StorageError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)


@router.get("/api/storage/proposals", dependencies=[Depends(require_admin)])
async def list_proposals(request: Request):
    try:
        return {"proposals": _engine(request).proposals()}
    except StorageError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)


@router.post("/api/storage/proposals/{pid}/approve", dependencies=[Depends(require_admin)])
async def approve(pid: str, body: Approve, request: Request):
    _person_only(request)
    return await _run(_engine(request).approve(pid, body.confirm, _actor(request)))


@router.post("/api/storage/proposals/{pid}/reject", dependencies=[Depends(require_admin)])
async def reject(pid: str, request: Request):
    _person_only(request)
    try:
        return _engine(request).reject(pid)
    except StorageError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)
