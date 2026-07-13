"""Unit tests for the project element store (slice 1).

Covers schema, CRUD, slug handling, the counts query, archive, and the
delete modes (strict vs untag). The store is exercised directly against a
temp database; the task store shares the same projects.db so element item
counts can be verified without the HTTP layer.
"""
from __future__ import annotations

import pytest

from tinyagentos.projects.element_store import ProjectElementStore, slugify_element_name
from tinyagentos.projects.task_store import ProjectTaskStore


@pytest.mark.asyncio
async def test_slugify_element_name():
    assert slugify_element_name("Website") == "website"
    assert slugify_element_name("My Cool Design!") == "my-cool-design"
    assert slugify_element_name("") == "element"


@pytest.mark.asyncio
async def test_create_and_get_element(tmp_path):
    estore = ProjectElementStore(tmp_path / "projects.db")
    await estore.init()
    el = await estore.create_element(project_id="prj-1", name="Website")
    assert el["id"].startswith("elm-")
    assert el["slug"] == "website"
    assert el["type"] == "generic"
    assert el["project_id"] == "prj-1"
    assert el["archived_at"] is None

    fetched = await estore.get_element(el["id"])
    assert fetched["id"] == el["id"]
    by_slug = await estore.get_element_by_slug("prj-1", "website")
    assert by_slug["id"] == el["id"]


@pytest.mark.asyncio
async def test_create_element_explicit_slug_and_type(tmp_path):
    estore = ProjectElementStore(tmp_path / "projects.db")
    await estore.init()
    el = await estore.create_element(
        project_id="prj-1",
        name="Designs",
        slug="designs",
        type="design",
        description="art",
        assignee_id="agent-9",
        settings={"repo_url": "https://x"},
    )
    assert el["slug"] == "designs"
    assert el["type"] == "design"
    assert el["description"] == "art"
    assert el["assignee_id"] == "agent-9"
    assert el["settings"] == {"repo_url": "https://x"}


@pytest.mark.asyncio
async def test_create_element_bad_slug_rejected(tmp_path):
    estore = ProjectElementStore(tmp_path / "projects.db")
    await estore.init()
    with pytest.raises(ValueError):
        await estore.create_element(project_id="prj-1", name="X", slug="UPPER")


@pytest.mark.asyncio
async def test_duplicate_slug_raises(tmp_path):
    estore = ProjectElementStore(tmp_path / "projects.db")
    await estore.init()
    await estore.create_element(project_id="prj-1", name="Website", slug="website")
    with pytest.raises(ValueError):
        await estore.create_element(project_id="prj-1", name="Website 2", slug="website")
    # A different project may reuse the slug.
    other = await estore.create_element(project_id="prj-2", name="Website", slug="website")
    assert other["project_id"] == "prj-2"


@pytest.mark.asyncio
async def test_update_element(tmp_path):
    estore = ProjectElementStore(tmp_path / "projects.db")
    await estore.init()
    el = await estore.create_element(project_id="prj-1", name="Website")
    updated = await estore.update_element(
        el["id"], name="Site", type="website", description="d", assignee_id="agent-1",
        settings={"url": "https://s"},
    )
    assert updated["name"] == "Site"
    assert updated["type"] == "website"
    assert updated["assignee_id"] == "agent-1"
    assert updated["settings"] == {"url": "https://s"}
    # Updating only one field leaves the rest intact.
    again = await estore.update_element(el["id"], description="changed")
    assert again["name"] == "Site"
    assert again["description"] == "changed"


@pytest.mark.asyncio
async def test_archive_element(tmp_path):
    estore = ProjectElementStore(tmp_path / "projects.db")
    await estore.init()
    el = await estore.create_element(project_id="prj-1", name="Website")
    assert await estore.archive_element(el["id"]) is True
    archived = await estore.get_element(el["id"])
    assert archived["archived_at"] is not None
    # list_elements still includes archived elements (the grid hides them).
    assert any(e["id"] == el["id"] for e in await estore.list_elements("prj-1"))


@pytest.mark.asyncio
async def test_list_elements_counts(tmp_path):
    estore = ProjectElementStore(tmp_path / "projects.db")
    await estore.init()
    tstore = ProjectTaskStore(tmp_path / "projects.db")
    await tstore.init()

    el = await estore.create_element(project_id="prj-1", name="Website")
    # untagged task (project-level)
    await tstore.create_task(project_id="prj-1", title="general", created_by="u")
    # two tasks tagged with the element, one open and one closed
    open_t = await tstore.create_task(project_id="prj-1", title="open", element_id=el["id"], created_by="u")
    await tstore.create_task(project_id="prj-1", title="closed", element_id=el["id"], created_by="u")
    await tstore.close_task(open_t["id"], closed_by="x")

    items = await estore.list_elements("prj-1")
    assert len(items) == 1
    e = items[0]
    assert e["open_tasks"] == 1
    assert e["total_tasks"] == 2
    # canvas_items is 0 in slice 1 (canvas element_id column lands in slice 4).
    assert e["canvas_items"] == 0

    counts = await estore.count_element_items("prj-1", el["id"])
    assert counts == {"open_tasks": 1, "total_tasks": 2, "canvas_items": 0}


@pytest.mark.asyncio
async def test_delete_element_strict_and_untag(tmp_path):
    estore = ProjectElementStore(tmp_path / "projects.db")
    await estore.init()
    tstore = ProjectTaskStore(tmp_path / "projects.db")
    await tstore.init()

    el = await estore.create_element(project_id="prj-1", name="Website")
    t = await tstore.create_task(project_id="prj-1", title="t", element_id=el["id"], created_by="u")

    # Strict delete refuses while items are tagged (caller returns 409).
    counts = await estore.count_element_items("prj-1", el["id"])
    assert counts["total_tasks"] == 1
    tagged = await tstore.get_task(t["id"])
    assert tagged["element_id"] == el["id"]

    # Untag mode nulls the tags then deletes the row.
    await estore.delete_element(el["id"], untag=True)
    cleared = await tstore.get_task(t["id"])
    assert cleared["element_id"] is None
    assert await estore.get_element(el["id"]) is None
    # Untagged task remains.
    assert (await tstore.get_task(t["id"]))["id"] == t["id"]


@pytest.mark.asyncio
async def test_delete_empty_element(tmp_path):
    estore = ProjectElementStore(tmp_path / "projects.db")
    await estore.init()
    el = await estore.create_element(project_id="prj-1", name="Empty")
    await estore.delete_element(el["id"])
    assert await estore.get_element(el["id"]) is None
