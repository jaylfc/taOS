from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


class TaskCreate(BaseModel):
    name: str
    schedule: str
    command: str
    agent_name: str | None = None
    description: str = ""


class TaskUpdate(BaseModel):
    name: str | None = None
    schedule: str | None = None
    command: str | None = None
    description: str | None = None
    enabled: bool | None = None


class PresetApply(BaseModel):
    agent_name: str


@router.get("/api/tasks")
async def list_tasks(request: Request, agent: str | None = None):
    scheduler = request.app.state.scheduler
    return await scheduler.list_tasks(agent_name=agent)


@router.post("/api/tasks")
async def create_task(request: Request, body: TaskCreate):
    scheduler = request.app.state.scheduler
    if not body.name or not body.schedule or not body.command:
        return JSONResponse({"error": "name, schedule, and command are required"}, status_code=400)
    task_id = await scheduler.add_task(
        name=body.name,
        schedule=body.schedule,
        command=body.command,
        agent_name=body.agent_name,
        description=body.description,
    )
    return {"status": "created", "id": task_id}


@router.get("/api/tasks/presets")
async def list_presets(request: Request):
    scheduler = request.app.state.scheduler
    return await scheduler.get_presets()


@router.post("/api/tasks/presets/{preset_id}/apply")
async def apply_preset(request: Request, preset_id: int, body: PresetApply):
    scheduler = request.app.state.scheduler
    if not body.agent_name:
        return JSONResponse({"error": "agent_name is required"}, status_code=400)
    count = await scheduler.apply_preset(preset_id, body.agent_name)
    if count == 0:
        return JSONResponse({"error": "Preset not found or has no tasks"}, status_code=404)
    return {"status": "applied", "tasks_created": count}


@router.get("/api/tasks/{task_id}")
async def get_task(request: Request, task_id: int):
    scheduler = request.app.state.scheduler
    task = await scheduler.get_task(task_id)
    if task is None:
        return JSONResponse({"error": "Task not found"}, status_code=404)
    return task


@router.put("/api/tasks/{task_id}")
async def update_task(request: Request, task_id: int, body: TaskUpdate):
    scheduler = request.app.state.scheduler
    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    if not kwargs:
        return JSONResponse({"error": "No fields to update"}, status_code=400)
    await scheduler.update_task(task_id, **kwargs)
    return {"status": "updated", "id": task_id}


@router.delete("/api/tasks/{task_id}")
async def delete_task(request: Request, task_id: int):
    scheduler = request.app.state.scheduler
    deleted = await scheduler.delete_task(task_id)
    if not deleted:
        return JSONResponse({"error": "Task not found"}, status_code=404)
    return {"status": "deleted", "id": task_id}


@router.post("/api/tasks/{task_id}/toggle")
async def toggle_task(request: Request, task_id: int):
    scheduler = request.app.state.scheduler
    # Get current state
    tasks = await scheduler.list_tasks()
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)
    new_state = not task["enabled"]
    await scheduler.update_task(task_id, enabled=new_state)
    return {"status": "toggled", "id": task_id, "enabled": new_state}
