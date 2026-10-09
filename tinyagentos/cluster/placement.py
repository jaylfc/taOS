from __future__ import annotations

import hashlib

import math

def eligible(worker) -> bool:
    status = worker.get("status") if isinstance(worker, dict) else getattr(worker, "status", None)
    if status not in ("online", "update-available"):
        return False
    kind = worker.get("kind", "worker") if isinstance(worker, dict) else getattr(worker, "kind", "worker")
    return kind != "device"

def score(key: str, node: str, weight: float = 1.0) -> float:
    h = int.from_bytes(hashlib.blake2b(f"{node}\0{key}".encode(), digest_size=8).digest(), "big")
    u = ((h >> 12) + 0.5) / 2**52
    return -weight / math.log(u)

def rank(key: str, candidates) -> list[str]:
    if not candidates:
        return []
    if isinstance(candidates, dict):
        items = [(name, w) for name, w in candidates.items() if w > 0]
    else:
        items = [(name, 1.0) for name in candidates]
    return [name for name, _ in sorted(items, key=lambda x: (-score(key, x[0], x[1]), x[0]))]

def placement_source(agent: dict) -> str | None:
    if "placement_source" in agent and agent["placement_source"]:
        return agent["placement_source"]
    if agent.get("remote"):
        return "user"
    return None