from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any


async def index_closed_task(qmd_client: Any, project: dict, task: dict) -> None:
    """Embed a closed task into the project's QMD index.

    The project's per-project collection is `project-<slug>`. Tags include
    project, task, label and close-date markers so the memory layer can
    favour recently-closed work and decay older items naturally.
    """
    closed_at = task.get("closed_at") or time.time()
    iso_date = datetime.fromtimestamp(closed_at, tz=timezone.utc).strftime("%Y-%m-%d")
    body_parts = [task.get("title", ""), task.get("body", "")]
    body = "\n\n".join(p for p in body_parts if p).strip()

    tags = [
        f"project:{project['id']}",
        f"task:{task['id']}",
        f"closed:{iso_date}",
    ]
    for label in task.get("labels") or []:
        tags.append(f"label:{label}")

    await qmd_client.upsert_document(
        collection=f"project-{project['slug']}",
        path=f"tasks/{task['id']}.md",
        title=task.get("title", task["id"]),
        body=body,
        tags=tags,
        metadata={
            "task_id": task["id"],
            "project_id": project["id"],
            "closed_at": closed_at,
            "closed_by": task.get("closed_by"),
        },
    )
