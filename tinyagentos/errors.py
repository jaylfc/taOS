"""Standardised error response shape for the agent-friendly REST API.

Every error response in scope returns the same JSON shape so agents can
parse failures uniformly. The `fix` and `doc_url` fields turn errors into
self-healing hints: an agent that hits a 403 sees a concrete next step
and a link to the relevant recipe.
"""
from __future__ import annotations

from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Canonical error response shape used across the agent-friendly REST API."""

    error: str
    detail: str
    fix: str | None = None
    doc_url: str | None = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "error": "agent_not_found",
                "detail": "No agent named 'tok-agent' exists.",
                "fix": "List agents with `GET /api/agents` to see valid names.",
                "doc_url": "/docs/agents/recipes/managing-agents#list-agents",
            }
        }
    }


def error_response(
    *,
    status_code: int,
    error: str,
    detail: str,
    fix: str | None = None,
    doc_url: str | None = None,
) -> JSONResponse:
    """Build a JSONResponse with the canonical `{error, detail, fix, doc_url}` shape."""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": error,
            "detail": detail,
            "fix": fix,
            "doc_url": doc_url,
        },
    )
