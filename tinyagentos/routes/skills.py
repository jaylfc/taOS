from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


def _store(request: Request):
    return request.app.state.skills


@router.get("/api/skills")
async def list_skills(request: Request, category: str | None = None):
    skills = await _store(request).list_skills(category)
    return JSONResponse({"skills": skills})


@router.get("/api/skills/{skill_id}")
async def get_skill(request: Request, skill_id: str):
    skill = await _store(request).get_skill(skill_id)
    if not skill:
        return JSONResponse({"error": "Skill not found"}, status_code=404)
    return JSONResponse(skill)


@router.get("/api/skills/compatible/{framework}")
async def compatible(request: Request, framework: str):
    skills = await _store(request).get_compatible_skills(framework)
    return JSONResponse({"skills": skills})


@router.get("/api/agents/{agent_id}/skills")
async def get_agent_skills(request: Request, agent_id: str):
    skills = await _store(request).get_agent_skills(agent_id)
    return JSONResponse({"skills": skills})


@router.post("/api/agents/{agent_id}/skills")
async def assign_skill(request: Request, agent_id: str):
    body = await request.json()
    skill_id = body.get("skill_id")
    if not skill_id:
        return JSONResponse({"error": "skill_id required"}, status_code=400)
    try:
        await _store(request).assign_skill(agent_id, skill_id, body.get("config"))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return JSONResponse({"ok": True})


@router.delete("/api/agents/{agent_id}/skills/{skill_id}")
async def unassign_skill(request: Request, agent_id: str, skill_id: str):
    await _store(request).unassign_skill(agent_id, skill_id)
    return JSONResponse({"ok": True})
