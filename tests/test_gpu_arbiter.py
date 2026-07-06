"""Tests for GPU VRAM tracker + Resource integration (taOS #894 Slice 2)."""
import asyncio, pytest
from tinyagentos.scheduler.vram_tracker import VramTracker, VramAllocation
from tinyagentos.scheduler.resource import Resource, Tier
from tinyagentos.scheduler.types import Capability, Priority, ResourceSignature, Task

@pytest.fixture
def tracker():
    return VramTracker(total_vram_mb=8192, headroom_mb=1024)

@pytest.fixture
def evict_log(): return []

@pytest.fixture
def tracker_with_eviction(evict_log):
    async def _log_evict(tid, mid): evict_log.append((tid, mid))
    return VramTracker(total_vram_mb=8192, headroom_mb=1024, evict_callback=_log_evict)

def _make_task(task_id="t1", vram_mb=0, priority=Priority.INTERACTIVE_AGENT):
    return Task(id=task_id, capability=Capability.LLM_CHAT, payload=lambda r: asyncio.sleep(0),
                preferred_resources=[], priority=priority, estimated_vram_mb=vram_mb)

def _make_resource(name="gpu-cuda-0", tracker=None):
    return Resource(name=name, signature=ResourceSignature(platform="cuda-sm_86", runtime="cuda"),
                    concurrency=2,
                    get_capabilities=lambda: {"llm-chat", "embedding", "image-generation"},
                    backend_lookup=lambda c: "http://localhost:11434",
                    tier=Tier.GPU, vram_tracker=tracker)

class TestVramAccounting:
    def test_initial_free(self, tracker):
        assert tracker.total_vram_mb == 8192; assert tracker.free_vram_mb == 7168
    def test_reserve_updates(self, tracker):
        assert tracker.reserve("a", 2048); assert tracker.used_vram_mb == 2048
    def test_release_frees(self, tracker):
        tracker.reserve("a", 2048); tracker.release("a"); assert tracker.used_vram_mb == 0
    def test_release_idempotent(self, tracker):
        tracker.reserve("a", 2048); tracker.release("a"); assert tracker.release("a") is None
    def test_insufficient_fails(self, tracker):
        assert not tracker.reserve("big", 8000)

class TestAdmission:
    def test_can_admit_ok(self, tracker):
        ok, r = tracker.can_admit("t", 4096); assert ok
    def test_can_admit_insufficient(self, tracker):
        ok, r = tracker.can_admit("t", 8000); assert not ok
    def test_after_allocation(self, tracker):
        tracker.reserve("a", 6000); assert not tracker.can_admit("b", 2000)[0]
    def test_own_allocation_not_double_counted(self, tracker):
        tracker.reserve("a", 4096); assert tracker.can_admit("a", 4096)[0]

class TestEviction:
    def test_empty(self, tracker):
        assert tracker.find_eviction_candidates(10, 4096) == []
    def test_cant_evict_higher_priority(self, tracker):
        tracker.reserve("a", 4096, priority=Priority.INTERACTIVE_USER)
        assert tracker.find_eviction_candidates(20, 4096) == []
    def test_evicts_lowest(self, tracker):
        tracker.reserve("a", 2048, priority=Priority.BATCH)
        tracker.reserve("b", 2048, priority=Priority.BACKGROUND)
        c = tracker.find_eviction_candidates(10, 2048)
        assert len(c) == 1 and c[0].task_id == "a"
    def test_non_evictable_ignored(self, tracker):
        tracker.reserve("a", 4096, priority=Priority.BATCH, evictable=False)
        assert tracker.find_eviction_candidates(10, 4096) == []
    @pytest.mark.asyncio
    async def test_no_candidates(self, tracker):
        tracker.reserve("a", 7000, priority=Priority.INTERACTIVE_USER, evictable=False)
        assert not await tracker.evict_and_reserve("new", 2048, priority=20)

class TestResourceIntegration:
    def test_can_admit_via_resource(self, tracker):
        assert _make_resource(tracker=tracker).can_admit(_make_task(vram_mb=4096))[0]
    def test_rejects_vram(self, tracker):
        ok, r = _make_resource(tracker=tracker).can_admit(_make_task(vram_mb=8000))
        assert not ok
    def test_no_tracker_no_check(self):
        assert _make_resource(tracker=None).can_admit(_make_task(vram_mb=999999))[0]
    @pytest.mark.asyncio
    async def test_run_reserves_releases(self, tracker):
        r = _make_resource(tracker=tracker)
        task = _make_task(vram_mb=4096); task.payload = lambda res: asyncio.sleep(0)
        await r.run(task); assert tracker.used_vram_mb == 0
    @pytest.mark.asyncio
    async def test_run_releases_on_error(self, tracker):
        r = _make_resource(tracker=tracker)
        task = _make_task(vram_mb=4096)
        async def fail(r): raise RuntimeError("boom")
        task.payload = fail
        with pytest.raises(RuntimeError): await r.run(task)
        assert tracker.used_vram_mb == 0

class TestEdgeCases:
    def test_zero_total(self):
        t = VramTracker(total_vram_mb=0, headroom_mb=0)
        assert t.free_vram_mb == 0; assert not t.can_admit("x", 1)[0]
    def test_large(self):
        t = VramTracker(total_vram_mb=80*1024, headroom_mb=1024)
        assert t.can_admit("x", 40*1024)[0]
    def test_stats(self, tracker):
        tracker.reserve("a", 1024, "model-a"); assert tracker.stats()["allocations"] == 1
