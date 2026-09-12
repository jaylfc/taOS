import asyncio
import time

import pytest

from tinyagentos.projects.events import ProjectEvent, ProjectEventBroker


def test_project_event_stores_kind_and_payload():
    ev = ProjectEvent(kind="task.created", payload={"id": "tsk-1", "title": "Demo"})
    assert ev.kind == "task.created"
    assert ev.payload == {"id": "tsk-1", "title": "Demo"}


def test_project_event_defaults_timestamp():
    before = time.time()
    ev = ProjectEvent(kind="ping", payload={})
    after = time.time()
    assert before <= ev.ts <= after


@pytest.mark.asyncio
async def test_subscribe_receives_live_publish():
    broker = ProjectEventBroker()
    queue = await broker.subscribe("alpha")
    await broker.publish("alpha", ProjectEvent(kind="task.updated", payload={"id": "t1"}))
    ev = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert ev.kind == "task.updated"
    assert ev.payload == {"id": "t1"}


@pytest.mark.asyncio
async def test_late_subscriber_replays_buffered_events():
    broker = ProjectEventBroker(replay_size=8)
    await broker.publish("beta", ProjectEvent(kind="a", payload={"n": 1}))
    await broker.publish("beta", ProjectEvent(kind="b", payload={"n": 2}))

    queue = await broker.subscribe("beta")
    first = await asyncio.wait_for(queue.get(), timeout=0.5)
    second = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert first.kind == "a"
    assert second.kind == "b"


@pytest.mark.asyncio
async def test_publish_delivers_same_event_to_all_subscribers():
    broker = ProjectEventBroker()
    q1 = await broker.subscribe("gamma")
    q2 = await broker.subscribe("gamma")
    event = ProjectEvent(kind="task.claimed", payload={"agent": "grok"})
    await broker.publish("gamma", event)

    ev1 = await asyncio.wait_for(q1.get(), timeout=0.5)
    ev2 = await asyncio.wait_for(q2.get(), timeout=0.5)
    assert ev1 is event
    assert ev2 is event


@pytest.mark.asyncio
async def test_events_do_not_cross_project_channels():
    broker = ProjectEventBroker()
    q_a = await broker.subscribe("proj-a")
    q_b = await broker.subscribe("proj-b")
    await broker.publish("proj-a", ProjectEvent(kind="task.created", payload={"id": "x"}))

    ev = await asyncio.wait_for(q_a.get(), timeout=0.5)
    assert ev.payload["id"] == "x"
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q_b.get(), timeout=0.1)


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    broker = ProjectEventBroker()
    queue = await broker.subscribe("delta")
    await broker.unsubscribe("delta", queue)
    await broker.publish("delta", ProjectEvent(kind="task.closed", payload={"id": "t9"}))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.1)


@pytest.mark.asyncio
async def test_replay_buffer_drops_oldest_when_full():
    broker = ProjectEventBroker(replay_size=3)
    for i in range(6):
        await broker.publish("epsilon", ProjectEvent(kind="tick", payload={"i": i}))

    queue = await broker.subscribe("epsilon")
    replayed = []
    for _ in range(3):
        replayed.append(await asyncio.wait_for(queue.get(), timeout=0.5))
    assert [e.payload["i"] for e in replayed] == [3, 4, 5]


@pytest.mark.asyncio
async def test_unsubscribe_missing_queue_is_noop():
    broker = ProjectEventBroker()
    foreign = asyncio.Queue()
    await broker.unsubscribe("zeta", foreign)
    queue = await broker.subscribe("zeta")
    await broker.publish("zeta", ProjectEvent(kind="ok", payload={}))
    ev = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert ev.kind == "ok"


@pytest.mark.asyncio
async def test_publish_does_not_deadlock_when_a_subscriber_queue_is_full():
    broker = ProjectEventBroker(replay_size=32)
    q_slow = await broker.subscribe("proj-x")
    q_fast = await broker.subscribe("proj-x")
    for i in range(32):
        q_slow.put_nowait(ProjectEvent(kind="fill", payload={"i": i}))

    async def do_publish():
        await asyncio.wait_for(
            broker.publish("proj-x", ProjectEvent(kind="live", payload={})),
            timeout=0.5,
        )

    async def do_unsubscribe():
        await asyncio.wait_for(
            broker.unsubscribe("proj-x", q_slow),
            timeout=0.5,
        )

    await asyncio.gather(do_publish(), do_unsubscribe())
    ev = await asyncio.wait_for(q_fast.get(), timeout=0.5)
    assert ev.kind == "live"


@pytest.mark.asyncio
async def test_unsubscribe_preserves_replay_history():
    broker = ProjectEventBroker(replay_size=8)
    await broker.publish("proj-y", ProjectEvent(kind="a", payload={"n": 1}))
    await broker.publish("proj-y", ProjectEvent(kind="b", payload={"n": 2}))

    q1 = await broker.subscribe("proj-y")
    await asyncio.wait_for(q1.get(), timeout=0.5)
    await asyncio.wait_for(q1.get(), timeout=0.5)

    await broker.unsubscribe("proj-y", q1)

    q2 = await broker.subscribe("proj-y")
    first = await asyncio.wait_for(q2.get(), timeout=0.5)
    second = await asyncio.wait_for(q2.get(), timeout=0.5)
    assert first.kind == "a"
    assert second.kind == "b"