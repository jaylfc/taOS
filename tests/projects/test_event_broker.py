import asyncio
import pytest
from tinyagentos.projects.events import ProjectEventBroker, ProjectEvent


@pytest.mark.asyncio
async def test_publish_assigns_id():
    broker = ProjectEventBroker()
    queue = await broker.subscribe("p1")
    await broker.publish("p1", ProjectEvent(kind="task.created", payload={"id": "t1"}))
    ev = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert ev.id is not None
    assert isinstance(ev.id, str)


@pytest.mark.asyncio
async def test_publish_assigns_unique_ids():
    broker = ProjectEventBroker()
    queue = await broker.subscribe("p1")
    ids = []
    for i in range(10):
        await broker.publish("p1", ProjectEvent(kind="task.created", payload={"id": f"t{i}"}))
    for _ in range(10):
        ev = await asyncio.wait_for(queue.get(), timeout=0.5)
        ids.append(ev.id)
    assert len(set(ids)) == 10


@pytest.mark.asyncio
async def test_subscribe_with_last_event_id_skips_old():
    broker = ProjectEventBroker()
    for i in range(5):
        await broker.publish("p1", ProjectEvent(kind="task.created", payload={"id": f"t{i}"}))
    queue = await broker.subscribe("p1", last_event_id="2")
    items = []
    for _ in range(2):
        items.append(await asyncio.wait_for(queue.get(), timeout=0.5))
    assert [e.payload["id"] for e in items] == ["t3", "t4"]


@pytest.mark.asyncio
async def test_publish_many_events_queue_stays_bounded():
    broker = ProjectEventBroker()
    queue = await broker.subscribe("p1")
    for i in range(10000):
        await broker.publish("p1", ProjectEvent(kind="task.created", payload={"id": f"t{i}"}))
    assert queue.qsize() <= 256


@pytest.mark.asyncio
async def test_publish_then_subscribe_replays_recent():
    broker = ProjectEventBroker(replay_size=4)
    await broker.publish("p1", ProjectEvent(kind="task.created", payload={"id": "t1"}))
    await broker.publish("p1", ProjectEvent(kind="task.claimed", payload={"id": "t1"}))

    queue = await broker.subscribe("p1")
    first = await asyncio.wait_for(queue.get(), timeout=0.5)
    second = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert first.kind == "task.created"
    assert second.kind == "task.claimed"


@pytest.mark.asyncio
async def test_publish_fans_out_to_all_subscribers():
    broker = ProjectEventBroker()
    a = await broker.subscribe("p1")
    b = await broker.subscribe("p1")
    await broker.publish("p1", ProjectEvent(kind="task.updated", payload={"id": "t1"}))
    ev_a = await asyncio.wait_for(a.get(), timeout=0.5)
    ev_b = await asyncio.wait_for(b.get(), timeout=0.5)
    assert ev_a.kind == "task.updated"
    assert ev_b.kind == "task.updated"


@pytest.mark.asyncio
async def test_publish_isolated_per_project():
    broker = ProjectEventBroker()
    a = await broker.subscribe("p1")
    b = await broker.subscribe("p2")
    await broker.publish("p1", ProjectEvent(kind="task.created", payload={"id": "t1"}))
    ev_a = await asyncio.wait_for(a.get(), timeout=0.5)
    assert ev_a.kind == "task.created"
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(b.get(), timeout=0.1)


@pytest.mark.asyncio
async def test_unsubscribe_removes_queue():
    broker = ProjectEventBroker()
    queue = await broker.subscribe("p1")
    await broker.unsubscribe("p1", queue)
    await broker.publish("p1", ProjectEvent(kind="task.created", payload={"id": "t1"}))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.1)


@pytest.mark.asyncio
async def test_replay_buffer_respects_size():
    broker = ProjectEventBroker(replay_size=2)
    for i in range(5):
        await broker.publish("p1", ProjectEvent(kind="task.created", payload={"id": f"t{i}"}))
    queue = await broker.subscribe("p1")
    items = []
    for _ in range(2):
        items.append(await asyncio.wait_for(queue.get(), timeout=0.5))
    assert [e.payload["id"] for e in items] == ["t3", "t4"]
