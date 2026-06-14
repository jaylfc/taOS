"""Agent tool: describe the cluster's image-generation capabilities.

Gives the agent read-only awareness of what hardware tiers exist (this host's
NPU/GPU/CPU plus any cluster workers like an NVIDIA box) and which image tools
live on each tier, including what's loaded right now. The agent uses this to
pick the best tool by intent — a fast NPU draft vs the good GPU model for a
cover — and to tell the user what it's doing.

The agent does NOT manage queues/load/unload; the scheduler + lifecycle manager
do that. This tool is the menu, not the controls.
"""
from __future__ import annotations

from fastapi import Request

# Map a backend type to the hardware tier it runs on, for the agent's benefit.
_TIER = {
    "rkllama": "npu",
    "rk-llama-cpp": "npu",
    "ezrknpu": "npu",
    "sd-cpp": "cpu/gpu",
    "comfyui": "gpu",
    "ollama": "gpu/cpu",
}


def _json_safe(v):
    """Coerce a value to something JSON-serialisable (the tool result is
    returned as JSON, so nested dataclasses/objects would 500)."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return str(v)


def _hw_summary(hw) -> dict:
    """Best-effort, JSON-safe summary of a hardware profile (object or dict)."""
    if hw is None:
        return {}
    keys = ("cpu", "gpu", "npu", "vram", "ram", "tier", "platform")
    if isinstance(hw, dict):
        return {k: _json_safe(hw.get(k)) for k in keys if hw.get(k) is not None}
    return {k: _json_safe(getattr(hw, k)) for k in keys if getattr(hw, k, None) is not None}


def _image_backends_from_catalog(catalog) -> list[dict]:
    out = []
    if not catalog:
        return out
    try:
        for be in catalog.backends_with_capability("image-generation"):
            out.append({
                "name": be.name,
                "type": be.type,
                "tier": _TIER.get(be.type, "unknown"),
                "loaded": getattr(be, "lifecycle_state", "running") == "running",
                "models": [m.get("id") or m.get("name") for m in (be.models or [])][:10],
            })
    except Exception:
        pass
    return out


def _image_backends_from_worker(worker) -> list[dict]:
    out = []
    for b in (getattr(worker, "backends", None) or []):
        caps = b.get("capabilities") or []
        if "image-generation" in caps or b.get("type") in ("sd-cpp", "rkllama", "comfyui"):
            ls = b.get("lifecycle_state")
            out.append({
                "name": b.get("name"),
                "type": b.get("type"),
                "tier": _TIER.get(b.get("type"), "unknown"),
                # mirror the 'loaded' field local backends report; None = unknown
                "loaded": b.get("loaded") if "loaded" in b else (ls == "running" if ls else None),
                "models": [m.get("id") or m.get("name") if isinstance(m, dict) else m for m in (b.get("models") or [])][:10],
            })
    return out


async def execute_describe_image_capabilities(args: dict, request: Request) -> dict:
    state = request.app.state
    tiers = [{
        "node": "local",
        "hardware": _hw_summary(getattr(state, "hardware_profile", None)),
        "image_backends": _image_backends_from_catalog(getattr(state, "backend_catalog", None)),
    }]
    cluster = getattr(state, "cluster_manager", None)
    if cluster is not None:
        try:
            for w in cluster.get_workers():
                if getattr(w, "status", "online") != "online":
                    continue
                tiers.append({
                    "node": w.name,
                    "hardware": _hw_summary(getattr(w, "hardware", None)),
                    "image_backends": _image_backends_from_worker(w),
                })
        except Exception:
            pass
    return {
        "tiers": tiers,
        "hint": "Pick a model on the tier that fits the task (npu = fast draft, gpu = best quality), then call generate_image with that model. The system loads/unloads and queues for you.",
    }
