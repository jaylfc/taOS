from __future__ import annotations

import hashlib
import json
import subprocess
import time
from typing import Any, Optional

# In-process cache: (agent_name, source_hash) -> (value, expires_at)
_cache: dict[tuple[str, str], tuple[Optional[str], float]] = {}
_CACHE_TTL = 60.0  # seconds


def _source_hash(source: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(source, sort_keys=True).encode()
    ).hexdigest()[:16]


def _resolve_json_pointer(doc: dict[str, Any], pointer: str) -> Any:
    """Resolve a JSON Pointer (RFC 6901) against doc."""
    if pointer == "" or pointer == "/":
        return doc
    parts = pointer.lstrip("/").split("/")
    node: Any = doc
    for part in parts:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict):
            node = node[part]
        elif isinstance(node, list):
            node = node[int(part)]
        else:
            raise KeyError(f"cannot traverse {type(node).__name__} with key '{part}'")
    return node


def read_token_source(agent_name: str, source: dict[str, Any]) -> Optional[str]:
    """Read a token from the declared source. Returns None on failure.

    Results are cached for 60 seconds per (agent_name, source_hash) pair.
    Raises ValueError for an unknown source kind.
    """
    kind = source.get("kind")
    if kind not in ("container_file", "container_env", "static"):
        raise ValueError(f"unknown token_source kind '{kind}'")

    if kind == "static":
        return source["value"]

    cache_key = (agent_name, _source_hash(source))
    now = time.monotonic()
    if cache_key in _cache:
        value, expires_at = _cache[cache_key]
        if now < expires_at:
            return value

    value = _exec_token_source(agent_name, source, kind)
    _cache[cache_key] = (value, now + _CACHE_TTL)
    return value


def invalidate_agent_cache(agent_name: str) -> None:
    """Remove all cached token_source results for the given agent.

    Called when the agent container restarts so stale tokens are not served.
    """
    to_remove = [k for k in _cache if k[0] == agent_name]
    for k in to_remove:
        del _cache[k]


def _exec_token_source(
    agent_name: str, source: dict[str, Any], kind: str
) -> Optional[str]:
    container = f"taos-agent-{agent_name}"

    if kind == "container_env":
        var = source["var"]
        result = subprocess.run(
            ["incus", "exec", container, "--", "sh", "-c", f"echo ${var}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    if kind == "container_file":
        path = source["path"]
        json_pointer = source.get("json_pointer", "")
        result = subprocess.run(
            ["incus", "exec", container, "--", "cat", path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        try:
            doc = json.loads(result.stdout)
            value = _resolve_json_pointer(doc, json_pointer)
            return str(value)
        except (json.JSONDecodeError, KeyError, IndexError, ValueError):
            return None

    return None
