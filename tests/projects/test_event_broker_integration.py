import asyncio
import pytest
import pytest_asyncio
from tinyagentos.projects.task_store import ProjectTaskStore
from tinyagentos.projects.events import ProjectEventBroker


@pytest_asyncio.fixture
async def store_with_broker(tmp_path):
    broker = ProjectEventBroker()
    store = ProjectTaskStore(tmp_path / "tasks.db", broker=broker)
    await store.init()
    yield store, broker
    await store.close()


@pytest.mark.asyncio
async def test_create_task_emits_event(store_with_broker):
    store, broker = store_with_broker
    queue = await broker.subscribe("p1")
    t = await store.create_task(project_id="p1", title="t", created_by="u1")
    ev = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert ev.kind == "task.created"
    assert ev.payload["id"] == t["id"]


@pytest.mark.asyncio
async def test_claim_release_close_emit_events(store_with_broker):
    store, broker = store_with_broker
    t = await store.create_task(project_id="p1", title="t", created_by="u1")
    queue = await broker.subscribe("p1")
    # drain replay (the create event)
    await queue.get()

    await store.claim_task(t["id"], "agent")
    ev = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert ev.kind == "task.claimed"

    await store.release_task(t["id"], "agent")
    ev = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert ev.kind == "task.released"

    await store.close_task(t["id"], "agent", reason="done")
    ev = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert ev.kind == "task.closed"


@pytest.mark.asyncio
async def test_update_task_emits_event(store_with_broker):
    store, broker = store_with_broker
    t = await store.create_task(project_id="p1", title="t", created_by="u1")
    queue = await broker.subscribe("p1")
    await queue.get()  # drain
    await store.update_task(t["id"], title="renamed")
    ev = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert ev.kind == "task.updated"


@pytest.mark.asyncio
async def test_relationship_and_comment_events(store_with_broker):
    store, broker = store_with_broker
    a = await store.create_task(project_id="p1", title="a", created_by="u1")
    b = await store.create_task(project_id="p1", title="b", created_by="u1")
    queue = await broker.subscribe("p1")
    while not queue.empty():
        await queue.get()  # drain replay

    await store.add_relationship("p1", a["id"], b["id"], "blocks", "u1")
    ev = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert ev.kind == "relationship.added"

    await store.add_comment(a["id"], "u1", "hi")
    ev = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert ev.kind == "comment.added"
