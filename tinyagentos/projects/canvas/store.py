from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from tinyagentos.base_store import BaseStore
from tinyagentos.projects.ids import new_id

if TYPE_CHECKING:
    from tinyagentos.projects.events import ProjectEventBroker


CANVAS_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_canvas_elements (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    author_kind TEXT NOT NULL,
    author_id TEXT NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL,
    w REAL NOT NULL,
    h REAL NOT NULL,
    rotation REAL NOT NULL DEFAULT 0,
    z_index INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL
);
CREATE INDEX IF NOT EXISTS idx_canvas_project ON project_canvas_elements(project_id, deleted_at);
CREATE INDEX IF NOT EXISTS idx_canvas_updated ON project_canvas_elements(project_id, updated_at);
"""

_CANVAS_JSON_FIELDS = ("payload",)
_VALID_KINDS = {"note", "link", "image", "user_shape"}
_AGENT_ALLOWED_KINDS = {"note", "link", "image"}


class CanvasPermissionError(PermissionError):
    """Raised when an agent without can_edit_canvas tries to update/delete."""


def _row_to_element(row, description) -> dict:
    keys = [d[0] for d in description]
    e = dict(zip(keys, row))
    for f in _CANVAS_JSON_FIELDS:
        if f in e and e[f] is not None:
            e[f] = json.loads(e[f])
    return e


class ProjectCanvasStore(BaseStore):
    SCHEMA = CANVAS_SCHEMA

    def __init__(self, db_path, *, broker: "ProjectEventBroker | None" = None) -> None:
        super().__init__(db_path)
        self._broker = broker

    async def _publish(self, project_id: str, kind: str, payload: dict) -> None:
        if self._broker is not None:
            from tinyagentos.projects.events import ProjectEvent
            await self._broker.publish(project_id, ProjectEvent(kind=kind, payload=payload))
