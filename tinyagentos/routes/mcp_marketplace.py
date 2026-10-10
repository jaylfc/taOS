"""MCP marketplace routes: browse the curated registry, install, uninstall.

Registered alongside ``tinyagentos/routes/mcp.py``.  The split follows the same
rule the MCP router already uses: the browse reads (list, categories, detail)
carry no dependency so the marketplace renders for any signed-in user, while
the state-changing actions (install, uninstall, registry reload) are gated
admin-or-local-token via ``require_admin``.  Installing a server adds a process
the platform will launch — that is an operator action, not a member one.

``request.app.state.mcp_marketplace`` is built eagerly in ``create_app()`` and
its supervisor is attached by the lifespan; the handlers treat a missing
marketplace as ``503`` rather than raising ``AttributeError``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from tinyagentos.auth_context import require_admin
from tinyagentos.mcp.marketplace import MCPMarketplaceError

router = APIRouter()
_ADMIN = [Depends(require_admin)]


def _marketplace(request: Request):
    return getattr(request.app.state, "mcp_marketplace", None)


def _unavailable() -> JSONResponse:
    return JSONResponse(
        {"error": "mcp marketplace is not available"}, status_code=503
    )


def _error_response(exc: MCPMarketplaceError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=exc.status_code)


@router.get("/api/mcp/marketplace/servers")
async def list_marketplace_servers(
    request: Request,
    q: str | None = None,
    category: str | None = None,
):
    """Browse (and search) the curated registry.

    ``q`` is a case-insensitive substring match over id/name/description/author/
    categories; ``category`` is an exact, case-insensitive category match. Each
    entry is annotated with ``installed`` and ``running``.
    """
    marketplace = _marketplace(request)
    if marketplace is None:
        return _unavailable()
    entries = await marketplace.browse(query=q, category=category)
    return JSONResponse({
        "servers": entries,
        "count": len(entries),
        "categories": marketplace.categories(),
    })


@router.get("/api/mcp/marketplace/categories")
async def list_marketplace_categories(request: Request):
    marketplace = _marketplace(request)
    if marketplace is None:
        return _unavailable()
    return JSONResponse({"categories": marketplace.categories()})


@router.get("/api/mcp/marketplace/servers/{manifest_id}")
async def get_marketplace_server(manifest_id: str, request: Request):
    marketplace = _marketplace(request)
    if marketplace is None:
        return _unavailable()
    try:
        entry = await marketplace.detail(manifest_id)
    except MCPMarketplaceError as exc:
        return _error_response(exc)
    return JSONResponse(entry)


@router.post(
    "/api/mcp/marketplace/servers/{manifest_id}/install",
    dependencies=_ADMIN,
)
async def install_marketplace_server(manifest_id: str, request: Request):
    """Resolve a manifest, run its install command, register a server config.

    404 unknown entry, 409 already installed, 502 the install command failed
    (the store is left untouched in that case).
    """
    marketplace = _marketplace(request)
    if marketplace is None:
        return _unavailable()
    try:
        result = await marketplace.install(manifest_id)
    except MCPMarketplaceError as exc:
        return _error_response(exc)
    return JSONResponse(result, status_code=201)


@router.delete(
    "/api/mcp/marketplace/servers/{manifest_id}",
    dependencies=_ADMIN,
)
async def uninstall_marketplace_server(manifest_id: str, request: Request):
    marketplace = _marketplace(request)
    if marketplace is None:
        return _unavailable()
    try:
        result = await marketplace.uninstall(manifest_id)
    except MCPMarketplaceError as exc:
        return _error_response(exc)
    return JSONResponse(result)


@router.post("/api/mcp/marketplace/reload", dependencies=_ADMIN)
async def reload_marketplace_registry(request: Request):
    """Re-read the registry directory (used after a registry checkout updates)."""
    marketplace = _marketplace(request)
    if marketplace is None:
        return _unavailable()
    marketplace.registry.reload()
    return JSONResponse({
        "ok": True,
        "count": len(marketplace.registry.list()),
        "errors": marketplace.registry.errors,
    })
