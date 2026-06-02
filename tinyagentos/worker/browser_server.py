from __future__ import annotations

"""Browser-worker HTTP API.

Exposes two endpoints the taOS controller calls to start/stop Neko
Chromium containers on a capable cluster node.
"""

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.worker.browser_container import BrowserContainerError, BrowserContainerRunner

logger = logging.getLogger(__name__)


class _StartRequest(BaseModel):
    session_id: str
    profile_volume: str


class _StopRequest(BaseModel):
    container_id: str
    http_port: int | None = None


def create_browser_worker_app(
    runner: BrowserContainerRunner,
    *,
    auth_token: str | None = None,
) -> FastAPI:
    """Return a FastAPI app exposing the browser-worker start/stop API.

    If ``auth_token`` is set, every request must carry
    ``Authorization: Bearer <auth_token>``; missing or wrong tokens are
    rejected with 401.  If ``auth_token`` is None, no auth is required
    (test / development).
    """
    app = FastAPI(title="taOS Browser Worker")

    def _check_auth(request: Request) -> None:
        if auth_token is None:
            return
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or header[len("Bearer "):] != auth_token:
            raise HTTPException(status_code=401, detail="Unauthorized")

    @app.post("/worker/browser/start")
    async def start_session(body: _StartRequest, request: Request) -> dict:
        _check_auth(request)
        try:
            result = await runner.start(
                session_id=body.session_id,
                profile_volume=body.profile_volume,
            )
        except BrowserContainerError as exc:
            logger.warning("start_session failed: %s", exc)
            return JSONResponse(status_code=500, content={"error": str(exc)})
        return result

    @app.post("/worker/browser/stop")
    async def stop_session(body: _StopRequest, request: Request) -> dict:
        _check_auth(request)
        return await runner.stop(
            container_id=body.container_id,
            http_port=body.http_port,
        )

    return app
