import pytest
from unittest.mock import AsyncMock

from tinyagentos.projects.lifecycle import index_closed_task


@pytest.mark.asyncio
async def test_index_closed_task_calls_qmd_with_tags():
    qmd = AsyncMock()
    task = {
        "id": "tsk-aaa",
        "project_id": "prj-bbb",
        "title": "Draft",
        "body": "outline",
        "closed_at": 1700000000.0,
        "closed_by": "agent-1",
        "labels": ["docs"],
    }
    project = {"id": "prj-bbb", "slug": "alpha", "name": "Alpha"}
    await index_closed_task(qmd, project, task)
    qmd.upsert_document.assert_awaited_once()
    args, kwargs = qmd.upsert_document.await_args
    assert kwargs["collection"] == "project-alpha"
    assert "project:prj-bbb" in kwargs["tags"]
    assert "task:tsk-aaa" in kwargs["tags"]
    assert kwargs["body"].startswith("Draft")
