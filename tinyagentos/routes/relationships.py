from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


class GroupCreate(BaseModel):
    name: str
    description: str = ""
    lead_agent: Optional[str] = None
    color: str = "#888888"


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    lead_agent: Optional[str] = None
    color: Optional[str] = None


class MemberAdd(BaseModel):
    agent_name: str
    role: str = "member"


class PermissionSet(BaseModel):
    from_agent: str
    to_agent: str


# --- Groups ---

@router.get("/api/relationships/groups")
async def list_groups(request: Request):
    """List all groups with members."""
    mgr = request.app.state.relationships
    return await mgr.list_groups()


@router.post("/api/relationships/groups")
async def create_group(request: Request, body: GroupCreate):
    """Create a new agent group."""
    mgr = request.app.state.relationships
    try:
        group_id = await mgr.create_group(
            name=body.name,
            description=body.description,
            lead_agent=body.lead_agent,
            color=body.color,
        )
    except Exception as e:
        if "UNIQUE" in str(e):
            return JSONResponse({"error": f"Group '{body.name}' already exists"}, status_code=409)
        raise
    return {"id": group_id, "status": "created"}


@router.put("/api/relationships/groups/{group_id}")
async def update_group(request: Request, group_id: int, body: GroupUpdate):
    """Update a group's properties."""
    mgr = request.app.state.relationships
    kwargs = {}
    if body.name is not None:
        kwargs["name"] = body.name
    if body.description is not None:
        kwargs["description"] = body.description
    if body.lead_agent is not None:
        kwargs["lead_agent"] = body.lead_agent
    if body.color is not None:
        kwargs["color"] = body.color
    await mgr.update_group(group_id, **kwargs)
    return {"status": "updated"}


@router.delete("/api/relationships/groups/{group_id}")
async def delete_group(request: Request, group_id: int):
    """Delete a group and its memberships."""
    mgr = request.app.state.relationships
    deleted = await mgr.delete_group(group_id)
    if not deleted:
        return JSONResponse({"error": "Group not found"}, status_code=404)
    return {"status": "deleted"}


# --- Members ---

@router.post("/api/relationships/groups/{group_id}/members")
async def add_member(request: Request, group_id: int, body: MemberAdd):
    """Add a member to a group."""
    mgr = request.app.state.relationships
    await mgr.add_member(group_id, body.agent_name, body.role)
    return {"status": "added"}


@router.delete("/api/relationships/groups/{group_id}/members/{agent_name}")
async def remove_member(request: Request, group_id: int, agent_name: str):
    """Remove a member from a group."""
    mgr = request.app.state.relationships
    await mgr.remove_member(group_id, agent_name)
    return {"status": "removed"}


# --- Agent info ---

@router.get("/api/relationships/agent/{name}")
async def get_agent_info(request: Request, name: str):
    """Get an agent's groups and permissions."""
    mgr = request.app.state.relationships
    groups = await mgr.get_agent_groups(name)
    permissions = await mgr.get_agent_permissions(name)
    return {"groups": groups, "permissions": permissions}


# --- Permissions ---

@router.post("/api/relationships/permissions")
async def set_permission(request: Request, body: PermissionSet):
    """Allow from_agent to message to_agent."""
    mgr = request.app.state.relationships
    await mgr.set_permission(body.from_agent, body.to_agent)
    return {"status": "granted"}


@router.delete("/api/relationships/permissions")
async def revoke_permission(request: Request, body: PermissionSet):
    """Revoke messaging permission."""
    mgr = request.app.state.relationships
    await mgr.revoke_permission(body.from_agent, body.to_agent)
    return {"status": "revoked"}
