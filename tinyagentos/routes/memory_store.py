"""Memory store status and the explicit vector conversion.

    GET  /api/memory-store          what the taOSmd probe found, last conversion, whether one is running
    POST /api/memory-store/convert  {"confirm": true} start the one-way vector conversion

Both are admin only (an admin session or the host local token), like the rest of
Settings. The conversion is never automatic. Two callers, one engine
(``tinyagentos.memory_store_guard``):

* The owner in a signed-in session: the Settings button posts ``confirm: true``
  and the conversion starts straight away.
* An agent or the CLI (host local token): the request does not run anything. It
  raises a consent Decision for the owner; approving the card in the Decisions
  app starts the same conversion, and the outcome is written back to the card
  as a note the agent can read.

The conversion runs in the background (it can take minutes); GET reports it.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos import memory_store_guard as guard
from tinyagentos.auth_context import require_admin
from tinyagentos.task_utils import _create_supervised_task

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])

DECISION_KIND = "memory_store_convert"

CONSENT_QUESTION = (
    "Convert the memory store vectors to the newer float32 format? This is optional "
    "and one way: older taOSmd releases cannot read the store afterwards. taOSmd takes "
    "a verified backup first and puts everything back if anything fails."
)


class ConvertBody(BaseModel):
    confirm: bool = False


def _lock(app) -> asyncio.Lock:
    lock = getattr(app.state, "memory_store_convert_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app.state.memory_store_convert_lock = lock
    return lock


def _data_dir(app):
    return app.state.data_dir


def _snapshot(app) -> dict:
    status = getattr(app.state, "memory_store_status", None)
    last = getattr(app.state, "memory_store_last_convert", None)
    return {
        "status": status.to_dict() if status is not None else None,
        "converting": _lock(app).locked(),
        "last_convert": last,
    }


async def run_conversion(app) -> dict:
    """Convert the vectors (one at a time), then re-probe. Used by the route and by the consent card."""
    lock = _lock(app)
    if lock.locked():
        return {"ok": False, "state": "already_running", "message": "A conversion is already running."}
    async with lock:
        started = time.time()
        app.state.memory_store_last_convert = {"state": "running", "started_at": started, "message": "Converting."}
        try:
            outcome = await guard.convert_vectors(_data_dir(app))
        except Exception as exc:  # noqa: BLE001 - recorded, reported, never swallowed
            logger.exception("memory store conversion crashed")
            app.state.memory_store_last_convert = {
                "state": "error", "ok": False, "started_at": started, "finished_at": time.time(),
                "message": (
                    f"The conversion stopped unexpectedly ({type(exc).__name__}). Check Settings, "
                    "Memory for the store state; taOSmd restores the store from its backup on failure."
                ),
            }
            raise
        record = {**outcome.to_dict(), "started_at": started, "finished_at": time.time()}
        app.state.memory_store_last_convert = record
        try:
            app.state.memory_store_status = await guard.probe(_data_dir(app))
        except Exception:  # noqa: BLE001
            logger.exception("memory store re-check after conversion failed")
        notifier = getattr(app.state, "notifications", None)
        if notifier is not None:
            try:
                await notifier.add(
                    title="Memory store converted" if outcome.ok else "Memory store conversion did not finish",
                    message=outcome.message,
                    level="info" if outcome.ok else "warning",
                    source="system.memory",
                )
            except Exception:  # noqa: BLE001
                logger.exception("could not record the memory store conversion notification")
        return record


def _start_background(app) -> None:
    tasks = getattr(app.state, "_background_tasks", None)
    coro = run_conversion(app)
    if tasks is None:
        asyncio.create_task(coro)
    else:
        _create_supervised_task(coro, tasks)


@router.get("/api/memory-store")
async def memory_store_status(request: Request, refresh: bool = False):
    if refresh and not _lock(request.app).locked():
        request.app.state.memory_store_status = await guard.probe(_data_dir(request.app))
    return _snapshot(request.app)


@router.post("/api/memory-store/convert")
async def convert_memory_store(request: Request, body: ConvertBody):
    app = request.app
    if not body.confirm:
        return JSONResponse(
            {
                "error": (
                    'Conversion is never automatic. Send {"confirm": true} to start it. '
                    "It is optional and one way: older taOSmd releases cannot read the "
                    "store afterwards, and a verified backup is kept."
                ),
            },
            status_code=400,
        )
    status = getattr(app.state, "memory_store_status", None)
    if status is not None and not status.convert_available and status.state != "old":
        return JSONResponse(
            {"error": f"Nothing to convert: {status.message}", "status": status.to_dict()},
            status_code=409,
        )
    if _lock(app).locked():
        return JSONResponse({"error": "A conversion is already running."}, status_code=409)

    signed_in_owner = (
        getattr(request.state, "via", None) == "session" and bool(getattr(request.state, "is_admin", False))
    )
    if signed_in_owner:
        _start_background(app)
        return JSONResponse({"state": "started", "message": "Conversion started."}, status_code=202)

    # Agent or CLI: ask the owner. Nothing runs until the card is approved.
    from tinyagentos.routes.decisions import SERVER_RAISED_KEY

    store = getattr(app.state, "decision_store", None)
    if store is None:
        return JSONResponse({"error": "Decisions are not available, so consent cannot be asked."}, status_code=503)
    existing = [
        d for d in await store.find_by_metadata("kind", DECISION_KIND)
        if d.get("status") == "pending"
    ]
    if existing:
        return JSONResponse(
            {"state": "consent_requested", "decision_id": existing[0]["id"], "message": "The owner has already been asked."},
            status_code=202,
        )
    decision = await store.create(
        from_agent="taOS",
        question=CONSENT_QUESTION,
        type="approve_deny",
        priority="normal",
        context="memory store vector conversion",
        metadata={SERVER_RAISED_KEY: True, "kind": DECISION_KIND},
    )
    return JSONResponse(
        {
            "state": "consent_requested",
            "decision_id": decision["id"],
            "message": "The owner was asked. Nothing changes until they approve the card in Decisions.",
        },
        status_code=202,
    )
