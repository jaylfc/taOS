"""Shared utility for resolving agent QMD database paths."""
from __future__ import annotations

from pathlib import Path

from tinyagentos.qmd_db import QmdDatabase

DEFAULT_QMD_CACHE_DIR = Path.home() / ".cache" / "qmd"

# Module-level so tests can monkeypatch
QMD_CACHE_DIR = DEFAULT_QMD_CACHE_DIR


def find_agent(config, agent_name: str) -> dict | None:
    """Look up an agent by name in config."""
    return next((a for a in config.agents if a["name"] == agent_name), None)


def get_agent_db(agent: dict) -> QmdDatabase | None:
    """Open the QMD database for an agent.

    Checks agent config for explicit 'qmd_db_path' first,
    then falls back to ~/.cache/qmd/{qmd_index}.sqlite.
    """
    # Explicit path takes priority (e.g. mounted from LXC container)
    explicit_path = agent.get("qmd_db_path")
    if explicit_path:
        db_path = Path(explicit_path)
    else:
        index_name = agent.get("qmd_index", "index")
        db_path = QMD_CACHE_DIR / f"{index_name}.sqlite"
    try:
        return QmdDatabase(db_path)
    except FileNotFoundError:
        return None


def get_agent_summaries(config) -> list[dict]:
    """Return a list of agents enriched with live DB stats."""
    result = []
    for agent in config.agents:
        db = get_agent_db(agent)
        result.append({
            "name": agent["name"],
            "host": agent.get("host", ""),
            "qmd_index": agent.get("qmd_index", ""),
            "color": agent.get("color", "#888"),
            "status": "ok" if db else "error",
            "vectors": db.vector_count() if db else 0,
            "last_embedded": db.last_embedded_at() if db else None,
        })
    return result
