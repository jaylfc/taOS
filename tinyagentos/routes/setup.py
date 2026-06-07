"""Setup-status API — drives the frontend onboarding checklist.

GET  /api/setup/status  → current completion state across all checklist items
POST /api/setup/dismiss → persist user's dismissal of the checklist
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

_PREF_NAMESPACE = "setup"


@router.get("/api/setup/status")
async def setup_status(request: Request):
    """Return onboarding checklist completion state.

    Fields:
      account         — always True for an authenticated request
      has_provider    — any backend configured in config.backends
      taos_model_set  — the taOS agent has a model configured
      has_agent       — at least one deployed agent exists
      memory_enabled  — user completed the taOSmd memory setup wizard
      dismissed       — user dismissed the checklist
      complete        — has_provider AND taos_model_set (the core two steps)
    """
    config = request.app.state.config
    store = request.app.state.desktop_settings
    data_dir: Path = getattr(request.app.state, "data_dir", Path("data"))

    # has_provider: any configured backend (cloud or local)
    has_provider = bool(config.backends)

    # taos_model_set: taOS agent's model pref is non-empty
    taos_agent_prefs = await store.get_preference("user", "taos_agent")
    taos_model_set = bool(taos_agent_prefs.get("model"))

    # has_agent: at least one agent in config
    has_agent = bool(config.agents)

    # memory_enabled: taosmd setup wizard completed (taosmd_default.json written)
    memory_enabled = (data_dir / "taosmd_default.json").exists()

    # dismissed: user explicitly dismissed the setup checklist
    setup_prefs = await store.get_preference("user", _PREF_NAMESPACE)
    dismissed = bool(setup_prefs.get("dismissed", False))

    # complete: the two core steps done
    complete = has_provider and taos_model_set

    return JSONResponse({
        "account": True,
        "has_provider": has_provider,
        "taos_model_set": taos_model_set,
        "has_agent": has_agent,
        "memory_enabled": memory_enabled,
        "dismissed": dismissed,
        "complete": complete,
    })


@router.post("/api/setup/dismiss")
async def setup_dismiss(request: Request):
    """Persist the user's dismissal of the setup checklist."""
    store = request.app.state.desktop_settings
    prefs = await store.get_preference("user", _PREF_NAMESPACE)
    prefs["dismissed"] = True
    await store.save_preference("user", _PREF_NAMESPACE, prefs)
    return JSONResponse({"ok": True, "dismissed": True})
