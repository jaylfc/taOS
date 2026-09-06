from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from tinyagentos.agent_templates import (
    list_templates, get_template, CATEGORIES, EXTERNAL_SOURCES,
    fetch_external_index, fetch_external_template, template_stats,
    TEMPLATES, vendored_templates,
)

router = APIRouter()

_VALID_SOURCES = {"builtin", "awesome-openclaw", "prompt-library", "user"}

# Vendored JSON tags items with the long upstream source IDs; the UI and
# /api/personas/library expose shorter aliases.
_DATA_SOURCE_TO_UI = {
    "awesome-openclaw-agents": "awesome-openclaw",
    "system-prompt-library": "prompt-library",
}
_UI_TO_DATA_SOURCE = {v: k for k, v in _DATA_SOURCE_TO_UI.items()}


@router.get("/api/templates/stats")
async def api_template_stats():
    """Return template count stats."""
    return template_stats()


@router.get("/api/templates")
async def api_list_templates(category: str | None = None, source: str | None = None,
                             limit: int = 50, offset: int = 0):
    """List agent templates with filtering and pagination.

    source: "builtin", "awesome-openclaw-agents", "system-prompt-library", or omit for all.
    """
    all_templates = list_templates(category=category, source=source)
    total = len(all_templates)
    page = all_templates[offset:offset + limit]
    # Strip system_prompt from list view to reduce payload
    compact = [
        {k: v for k, v in t.items() if k != "system_prompt"}
        for t in page
    ]
    return {"templates": compact, "total": total, "categories": CATEGORIES}


@router.get("/api/templates/sources")
async def list_template_sources():
    """List external sources, IDs normalised to UI aliases so responses can
    be fed straight back into /api/personas/library?source=…"""
    return {
        "sources": [
            {**s, "id": _DATA_SOURCE_TO_UI.get(s["id"], s["id"])}
            for s in EXTERNAL_SOURCES
        ]
    }


@router.get("/api/templates/external/{source_id}")
async def list_external_templates(request: Request, source_id: str):
    """Fetch template index from an external source. Accepts UI aliases
    (e.g. prompt-library) as advertised by /api/templates/sources."""
    http_client = request.app.state.http_client
    data_id = _UI_TO_DATA_SOURCE.get(source_id, source_id)
    templates = await fetch_external_index(data_id, http_client)
    return {"source": source_id, "templates": templates, "count": len(templates)}


@router.get("/api/templates/external/{source_id}/fetch")
async def fetch_external(request: Request, source_id: str, path: str = ""):
    """Fetch a single template's full content from an external source. Accepts
    UI aliases (e.g. prompt-library) as advertised by /api/templates/sources."""
    if not path:
        return JSONResponse({"error": "path parameter required"}, status_code=400)
    http_client = request.app.state.http_client
    data_id = _UI_TO_DATA_SOURCE.get(source_id, source_id)
    template = await fetch_external_template(data_id, path, http_client)
    if not template:
        return JSONResponse({"error": "Template not found or source unavailable"}, status_code=404)
    return template


@router.get("/api/personas/library")
async def api_personas_library(
    request: Request,
    source: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Aggregated persona library: builtin + vendored (awesome-openclaw,
    prompt-library) + user-authored personas.

    source filter returns 400 for unrecognised values.
    """
    if source is not None and source not in _VALID_SOURCES:
        return JSONResponse(
            {"error": f"unknown source '{source}'; valid: {sorted(_VALID_SOURCES)}"},
            status_code=400,
        )

    personas: list[dict] = []

    want_builtin = source in (None, "builtin")
    want_user = source in (None, "user")
    want_openclaw = source in (None, "awesome-openclaw")
    want_promptlib = source in (None, "prompt-library")

    # Order: user → builtin → vendored. Keeps the user's own personas and the
    # small curated builtin list on the first page when paginating the
    # "All sources" view (vendored libraries are ~1.5k items).
    if want_user:
        user_persona_store = getattr(request.app.state, "user_personas", None)
        if user_persona_store is not None:
            for row in user_persona_store.list():
                soul = row.get("soul_md", "") or ""
                personas.append({
                    "source": "user",
                    "id": row["id"],
                    "name": row["name"],
                    "description": row.get("description"),
                    "preview": soul[:120],
                })

    if want_builtin:
        for tpl in TEMPLATES:
            sp = tpl.get("system_prompt", "") or ""
            personas.append({
                "source": "builtin",
                "id": tpl["id"],
                "name": tpl["name"],
                "description": tpl.get("description"),
                "preview": sp[:120],
            })

    if want_openclaw or want_promptlib:
        for tpl in vendored_templates():
            ui_src = _DATA_SOURCE_TO_UI.get(tpl.get("source"))
            if ui_src is None:
                continue
            if ui_src == "awesome-openclaw" and not want_openclaw:
                continue
            if ui_src == "prompt-library" and not want_promptlib:
                continue
            sp = tpl.get("system_prompt", "") or ""
            personas.append({
                "source": ui_src,
                "id": tpl["id"],
                "name": tpl["name"],
                "description": tpl.get("description"),
                "preview": sp[:120],
            })

    if q:
        q_lower = q.lower()
        personas = [
            p for p in personas
            if q_lower in (p["name"] or "").lower()
            or q_lower in (p["description"] or "").lower()
            or q_lower in (p["preview"] or "").lower()
        ]

    total = len(personas)
    page = personas[offset:offset + limit]
    return {"personas": page, "total": total}


@router.get("/api/templates/{template_id}")
async def api_get_template(template_id: str):
    """Get a single template by ID."""
    tmpl = get_template(template_id)
    if not tmpl:
        return JSONResponse({"error": "Template not found"}, status_code=404)
    return tmpl
