"""Agent tool for hardware-aware capability awareness.

Lets the agent know what this taOS install can actually do on its current
hardware + cluster (run a large chat model, generate images on a GPU, train a
LoRA, etc.) and, for anything not yet available, the unlock hint. With this the
agent gives accurate advice ("you can run this locally" vs "you would need a GPU
worker") instead of guessing. Reads the in-process CapabilityChecker (the same
one the cluster-capability surface uses). Read-only.
"""
from __future__ import annotations

from fastapi import Request

GET_CAPABILITIES_TOOL = {
    "name": "get_capabilities",
    "description": (
        "Check what this taOS install can do on its current hardware + cluster: "
        "each capability (chat-small, chat-large, image-generation-gpu, embedding, "
        "tts, lora-training, ...) with whether it is available now and, if not, an "
        "unlock hint (e.g. 'add a GPU worker'). Use before promising a "
        "hardware-bound action so you give accurate advice instead of guessing. "
        "Optional available_only=true returns just the usable capabilities."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "available_only": {
                "type": "boolean",
                "description": "If true, return only currently-available capabilities. Default false (all, with hints).",
            },
        },
    },
}


async def execute_get_capabilities(args: dict, request: Request) -> dict:
    checker = getattr(request.app.state, "capabilities", None)
    if checker is None:
        return {"error": "capability checker not available"}
    available_only = bool((args or {}).get("available_only", False))
    try:
        all_caps = checker.get_all_capabilities()
    except Exception as exc:
        return {"error": f"capability check failed: {exc}"}

    capabilities = []
    for name, info in sorted(all_caps.items()):
        avail = bool(info.get("available"))
        if available_only and not avail:
            continue
        entry = {"capability": name, "available": avail}
        hint = info.get("hint")
        if hint and not avail:
            entry["unlock_hint"] = hint
        capabilities.append(entry)
    return {
        "ok": True,
        "capabilities": capabilities,
        "count": len(capabilities),
        "available_count": sum(1 for c in capabilities if c["available"]),
    }
