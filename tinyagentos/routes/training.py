from __future__ import annotations

import json
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


class CreateJobRequest(BaseModel):
    base_model: str
    agent_name: str | None = None
    dataset_description: str = ""
    preset: str | None = None
    config: dict | None = None


@router.get("/api/training/jobs")
async def list_training_jobs(request: Request, agent: str | None = None):
    training = request.app.state.training
    jobs = await training.list_jobs(agent_name=agent)
    return {"jobs": jobs}


@router.get("/api/training/jobs/{job_id}")
async def get_training_job(request: Request, job_id: str):
    training = request.app.state.training
    job = await training.get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    from dataclasses import asdict
    return asdict(job)


@router.post("/api/training/jobs")
async def create_training_job(request: Request, body: CreateJobRequest):
    training = request.app.state.training
    config = body.config or {}

    # If a preset is specified, merge preset config as defaults
    if body.preset:
        presets = await training.get_presets()
        preset = next((p for p in presets if p["id"] == body.preset), None)
        if preset:
            merged = dict(preset["config"])
            merged.update(config)
            config = merged

    job_id = await training.create_job(
        base_model=body.base_model,
        agent_name=body.agent_name,
        dataset_description=body.dataset_description,
        config=config,
    )
    return {"id": job_id, "status": "queued"}


@router.delete("/api/training/jobs/{job_id}")
async def delete_training_job(request: Request, job_id: str):
    training = request.app.state.training
    deleted = await training.delete_job(job_id)
    if not deleted:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return {"status": "deleted", "id": job_id}


@router.get("/api/training/presets")
async def list_presets(request: Request):
    training = request.app.state.training
    presets = await training.get_presets()
    return {"presets": presets}


@router.post("/api/training/retrain/{agent_name}")
async def retrain_agent(request: Request, agent_name: str):
    """Create a training job from an agent's conversation memory (simulated audit)."""
    training = request.app.state.training
    description = f"Retrain based on {agent_name}'s conversation memory"

    # Use balanced preset as default for agent retrain
    presets = await training.get_presets()
    preset = next((p for p in presets if p["id"] == "balanced"), None)
    config = dict(preset["config"]) if preset else {}

    # Default base model for agent retrain
    base_model = "qwen3-1.7b"

    job_id = await training.create_job(
        base_model=base_model,
        agent_name=agent_name,
        dataset_description=description,
        config=config,
    )
    return {"id": job_id, "status": "queued", "agent_name": agent_name}
