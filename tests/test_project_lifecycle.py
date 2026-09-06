from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from tinyagentos.projects.lifecycle import index_closed_task


@pytest.mark.asyncio
async def test_index_closed_task_upserts_full_document():
    qmd = AsyncMock()
    task = {
        "id": "tsk-aaa",
        "title": "Draft chapter",
        "body": "Outline notes",
        "closed_at": 1700000000.0,
        "closed_by": "agent-1",
        "labels": ["docs", "mvp"],
    }
    project = {"id": "prj-bbb", "slug": "alpha"}

    await index_closed_task(qmd, project, task)

    qmd.upsert_document.assert_awaited_once_with(
        collection="project-alpha",
        path="tasks/tsk-aaa.md",
        title="Draft chapter",
        body="Draft chapter\n\nOutline notes",
        tags=[
            "project:prj-bbb",
            "task:tsk-aaa",
            "closed:2023-11-14",
            "label:docs",
            "label:mvp",
        ],
        metadata={
            "task_id": "tsk-aaa",
            "project_id": "prj-bbb",
            "closed_at": 1700000000.0,
            "closed_by": "agent-1",
        },
    )


@pytest.mark.asyncio
async def test_index_closed_task_defaults_closed_at_to_now():
    qmd = AsyncMock()
    fixed = 1710000000.0
    iso_date = datetime.fromtimestamp(fixed, tz=timezone.utc).strftime("%Y-%m-%d")
    task = {"id": "tsk-now", "title": "Quick fix"}
    project = {"id": "prj-1", "slug": "beta"}

    with patch("tinyagentos.projects.lifecycle.time.time", return_value=fixed):
        await index_closed_task(qmd, project, task)

    kwargs = qmd.upsert_document.await_args.kwargs
    assert kwargs["tags"][2] == f"closed:{iso_date}"
    assert kwargs["metadata"]["closed_at"] == fixed
    assert kwargs["metadata"]["closed_by"] is None


@pytest.mark.asyncio
async def test_index_closed_task_title_only_body():
    qmd = AsyncMock()
    task = {"id": "tsk-title", "title": "Only title", "closed_at": 1.0}
    project = {"id": "prj-1", "slug": "gamma"}

    await index_closed_task(qmd, project, task)

    kwargs = qmd.upsert_document.await_args.kwargs
    assert kwargs["body"] == "Only title"
    assert kwargs["title"] == "Only title"


@pytest.mark.asyncio
async def test_index_closed_task_body_only_uses_task_id_title():
    qmd = AsyncMock()
    task = {"id": "tsk-body", "body": "Details only", "closed_at": 1.0}
    project = {"id": "prj-1", "slug": "delta"}

    await index_closed_task(qmd, project, task)

    kwargs = qmd.upsert_document.await_args.kwargs
    assert kwargs["body"] == "Details only"
    assert kwargs["title"] == "tsk-body"


@pytest.mark.asyncio
async def test_index_closed_task_empty_title_and_body():
    qmd = AsyncMock()
    task = {"id": "tsk-empty", "closed_at": 1.0}
    project = {"id": "prj-1", "slug": "epsilon"}

    await index_closed_task(qmd, project, task)

    kwargs = qmd.upsert_document.await_args.kwargs
    assert kwargs["body"] == ""
    assert kwargs["title"] == "tsk-empty"
    assert kwargs["tags"] == [
        "project:prj-1",
        "task:tsk-empty",
        "closed:1970-01-01",
    ]


@pytest.mark.asyncio
async def test_index_closed_task_omits_label_tags_when_none():
    qmd = AsyncMock()
    task = {"id": "tsk-plain", "title": "Plain", "closed_at": 1.0, "labels": None}
    project = {"id": "prj-1", "slug": "zeta"}

    await index_closed_task(qmd, project, task)

    kwargs = qmd.upsert_document.await_args.kwargs
    assert all(not tag.startswith("label:") for tag in kwargs["tags"])