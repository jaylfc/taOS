"""API routes for the Temporal Knowledge Graph (taOSmd).

All routes live under /api/kg/. Provides entity and triple CRUD,
temporal queries, timeline views, and memory classification.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


# ------------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------------

class AddEntityRequest(BaseModel):
    name: str
    type: str = "unknown"
    properties: str = "{}"


class AddTripleRequest(BaseModel):
    subject: str
    predicate: str
    object: str
    valid_from: float | None = None
    confidence: float = 1.0
    source: str = ""
    subject_type: str = "unknown"
    object_type: str = "unknown"


class UpdateFactRequest(BaseModel):
    subject: str
    predicate: str
    old_object: str
    new_object: str
    source: str = ""


class InvalidateRequest(BaseModel):
    triple_id: str
    ended_at: float | None = None


class ClassifyRequest(BaseModel):
    text: str


# ------------------------------------------------------------------
# Entities
# ------------------------------------------------------------------

@router.post("/api/kg/entities")
async def add_entity(request: Request, body: AddEntityRequest):
    graph = request.app.state.knowledge_graph
    eid = await graph.add_entity(body.name, body.type, body.properties)
    return {"id": eid, "status": "ok"}


@router.get("/api/kg/entities")
async def list_entities(request: Request, type: str | None = None, limit: int = 100):
    graph = request.app.state.knowledge_graph
    entities = await graph.list_entities(entity_type=type, limit=limit)
    return {"entities": entities, "count": len(entities)}


@router.get("/api/kg/entities/{name}")
async def get_entity(request: Request, name: str):
    graph = request.app.state.knowledge_graph
    entity = await graph.get_entity(name)
    if not entity:
        return JSONResponse({"error": "not found"}, status_code=404)
    return entity


# ------------------------------------------------------------------
# Triples
# ------------------------------------------------------------------

@router.post("/api/kg/triples")
async def add_triple(request: Request, body: AddTripleRequest):
    graph = request.app.state.knowledge_graph
    tid = await graph.add_triple(
        subject=body.subject,
        predicate=body.predicate,
        obj=body.object,
        valid_from=body.valid_from,
        confidence=body.confidence,
        source=body.source,
        subject_type=body.subject_type,
        object_type=body.object_type,
    )
    return {"id": tid, "status": "ok"}


@router.post("/api/kg/triples/invalidate")
async def invalidate_triple(request: Request, body: InvalidateRequest):
    graph = request.app.state.knowledge_graph
    ok = await graph.invalidate(body.triple_id, body.ended_at)
    if not ok:
        return JSONResponse({"error": "not found or already invalidated"}, status_code=404)
    return {"status": "invalidated"}


@router.post("/api/kg/triples/update")
async def update_fact(request: Request, body: UpdateFactRequest):
    graph = request.app.state.knowledge_graph
    tid = await graph.update_fact(
        subject=body.subject,
        predicate=body.predicate,
        old_object=body.old_object,
        new_object=body.new_object,
        source=body.source,
    )
    return {"id": tid, "status": "updated"}


# ------------------------------------------------------------------
# Queries
# ------------------------------------------------------------------

@router.get("/api/kg/query/{name}")
async def query_entity(
    request: Request,
    name: str,
    as_of: float | None = None,
    direction: str = "both",
):
    graph = request.app.state.knowledge_graph
    results = await graph.query_entity(name, as_of=as_of, direction=direction)
    return {"results": results, "count": len(results)}


@router.get("/api/kg/query/predicate/{predicate}")
async def query_predicate(request: Request, predicate: str, as_of: float | None = None):
    graph = request.app.state.knowledge_graph
    results = await graph.query_predicate(predicate, as_of=as_of)
    return {"results": results, "count": len(results)}


@router.get("/api/kg/timeline")
async def timeline(request: Request, name: str | None = None, limit: int = 50):
    graph = request.app.state.knowledge_graph
    events = await graph.timeline(name=name, limit=limit)
    return {"events": events, "count": len(events)}


@router.get("/api/kg/stats")
async def stats(request: Request):
    graph = request.app.state.knowledge_graph
    return await graph.stats()


# ------------------------------------------------------------------
# Classification
# ------------------------------------------------------------------

@router.post("/api/kg/classify")
async def classify(body: ClassifyRequest):
    from taosmd.knowledge_graph import classify_memory_type
    mtype = classify_memory_type(body.text)
    return {"type": mtype, "text": body.text}
