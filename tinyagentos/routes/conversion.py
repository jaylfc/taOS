from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.conversion import CONVERSION_PATHS

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateConversionJobRequest(BaseModel):
    source_model: str
    source_format: str
    target_format: str
    target_quantization: str = ""


@router.get("/api/conversion/jobs")
async def list_conversion_jobs(request: Request):
    mgr = request.app.state.conversion
    return await mgr.list_jobs()


@router.post("/api/conversion/jobs")
async def create_conversion_job(request: Request, body: CreateConversionJobRequest):
    mgr = request.app.state.conversion
    # Validate the conversion path exists
    valid = any(
        p["from"] == body.source_format and p["to"] == body.target_format
        for p in CONVERSION_PATHS
    )
    if not valid:
        return JSONResponse(
            {"error": f"No conversion path from '{body.source_format}' to '{body.target_format}'"},
            status_code=400,
        )
    job_id = await mgr.create_job(
        source_model=body.source_model,
        source_format=body.source_format,
        target_format=body.target_format,
        target_quantization=body.target_quantization,
    )
    return {"id": job_id, "status": "queued"}


@router.get("/api/conversion/jobs/{job_id}")
async def get_conversion_job(request: Request, job_id: str):
    mgr = request.app.state.conversion
    job = await mgr.get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return job


@router.delete("/api/conversion/jobs/{job_id}")
async def delete_conversion_job(request: Request, job_id: str):
    mgr = request.app.state.conversion
    deleted = await mgr.delete_job(job_id)
    if not deleted:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return {"status": "deleted"}


@router.get("/api/conversion/formats")
async def list_conversion_formats(request: Request):
    cap_checker = request.app.state.capabilities
    result = []
    for path in CONVERSION_PATHS:
        entry = {
            "from": path["from"],
            "to": path["to"],
            "description": path["description"],
            "capability": path["capability"],
            "available": True,
        }
        if path["capability"]:
            entry["available"] = cap_checker.is_available(path["capability"])
            if not entry["available"]:
                entry["unlock_hint"] = cap_checker.get_unlock_hint(path["capability"])
        result.append(entry)
    return result
