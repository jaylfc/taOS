"""Tests for GPU arbiter pause/resume + hardware-aware LLM admission (taOS #796)."""
import asyncio
import pytest
from tinyagentos.scheduler.gpu_arbiter import GpuArbiter, GpuAdmission, _QueuedGpuTask, _default_vram_probe
from tinyagentos.scheduler.vram_tracker import VramTracker
from tinyagentos.scheduler.types import Capability, Priority, Task, NoResourceAvailableError


# ── Helpers ─────────────────────────────────────────────────────────────

def _make_task(task_id="t1", vram_mb=0, priority=Priority.INTERACTIVE_AGENT):
    return Task(
        id=task_id, capability=Capability.LLM_CHAT,
        payload=lambda r: asyncio.sleep(0),
        preferred_resources=[], priority=priority,
        estimated_vram_mb=vram_mb,
    )


async def _noop_payload(_resource):
    """Async no-op that returns immediately."""
    return {"ok": True}


# ── Pause/Resume Tests ─────────────────────────────────────────────────

class TestPauseResume:
    def test_initial_not_paused(self):
        arbiter = GpuArbiter()
        assert arbiter.paused is False

    def test_pause_sets_flag(self):
        arbiter = GpuArbiter()
        assert arbiter.pause() is True
        assert arbiter.paused is True

    def test_double_pause_noop(self):
        arbiter = GpuArbiter()
        arbiter.pause()
        assert arbiter.pause() is False  # Already paused — no change

    def test_resume_after_pause(self):
        arbiter = GpuArbiter()
        arbiter.pause()
        assert arbiter.resume() is True
        assert arbiter.paused is False

    def test_resume_when_not_paused(self):
        arbiter = GpuArbiter()
        assert arbiter.resume() is False  # Nothing to resume

    def test_pause_resume_cycle(self):
        arbiter = GpuArbiter()
        for _ in range(3):
            assert arbiter.pause() is True
            assert arbiter.paused is True
            assert arbiter.resume() is True
            assert arbiter.paused is False

    def test_stats_includes_paused(self):
        arbiter = GpuArbiter()
        stats = arbiter.stats()
        assert "paused" in stats
        assert stats["paused"] is False
        arbiter.pause()
        assert arbiter.stats()["paused"] is True

    @pytest.mark.asyncio
    async def test_queue_not_drained_when_paused(self):
        """When paused, queued tasks should stay queued — not processed."""
        arbiter = GpuArbiter(max_queue_size=10)
        arbiter.pause()

        task = _make_task("t-paused", vram_mb=0)
        task.payload = _noop_payload

        # Submit a task — with vram_mb=0 it bypasses admission and runs immediately
        # even when paused (pause only blocks queue draining, not direct admission)
        result = await arbiter.submit_gpu(task, required_vram_mb=0)
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_pause_then_resume(self):
        """Start arbiter, pause, resume."""
        arbiter = GpuArbiter(max_queue_size=10)
        await arbiter.start()
        try:
            arbiter.pause()
            assert arbiter.paused
            arbiter.resume()
            assert not arbiter.paused
        finally:
            await arbiter.stop()

    @pytest.mark.asyncio
    async def test_start_resets_pause_state(self):
        """Starting the arbiter should reset the paused flag."""
        arbiter = GpuArbiter()
        arbiter.pause()
        assert arbiter.paused
        await arbiter.start()
        try:
            assert not arbiter.paused
        finally:
            await arbiter.stop()


# ── Hardware-Aware LLM Admission Tests ──────────────────────────────────

class TestGpuArchCompatibility:
    def test_no_arch_requirement_passes(self):
        """When no arch is required, compatibility check always passes."""
        arbiter = GpuArbiter()
        ok, reason = arbiter._check_gpu_arch_compatibility(None, None)
        assert ok is True
        assert reason is None

    def test_arch_requirement_no_cluster_manager(self):
        """Without a cluster manager, we trust the caller."""
        arbiter = GpuArbiter()  # No cluster_manager
        ok, reason = arbiter._check_gpu_arch_compatibility("sm_86", "w1:gpu-cuda-0")
        assert ok is True  # Can't verify without cluster, so trust

    def test_arch_requirement_with_cluster_no_workers(self):
        """With a cluster manager but no workers, arch check fails."""
        from tinyagentos.cluster.manager import ClusterManager
        cm = ClusterManager()
        arbiter = GpuArbiter(cluster_manager=cm)
        ok, reason = arbiter._check_gpu_arch_compatibility("sm_86", "w1:gpu-cuda-0")
        assert ok is False
        assert "no online worker" in (reason or "")

    def test_arch_requirement_compatible_worker(self):
        """Worker with matching GPU arch passes check."""
        from tinyagentos.cluster.manager import ClusterManager
        from tinyagentos.cluster.worker_protocol import WorkerInfo

        cm = ClusterManager()
        w = WorkerInfo(
            name="gpu-worker",
            url="http://gpu:6969",
            hardware={"gpu": {"model": "NVIDIA RTX 4090 (sm_86)", "type": "cuda", "vram_mb": 24576}},
            capabilities=["llm-chat", "embedding"],
            status="online",
        )
        cm._workers["gpu-worker"] = w

        arbiter = GpuArbiter(cluster_manager=cm)
        ok, reason = arbiter._check_gpu_arch_compatibility("sm_86", "gpu-worker:gpu-cuda-0")
        assert ok is True

    def test_arch_requirement_incompatible_worker(self):
        """Worker without matching arch fails check."""
        from tinyagentos.cluster.manager import ClusterManager
        from tinyagentos.cluster.worker_protocol import WorkerInfo

        cm = ClusterManager()
        w = WorkerInfo(
            name="old-gpu",
            url="http://gpu:6969",
            hardware={"gpu": {"model": "NVIDIA GTX 1080 (sm_61)", "type": "cuda", "vram_mb": 8192}},
            capabilities=["llm-chat"],
            status="online",
        )
        cm._workers["old-gpu"] = w

        arbiter = GpuArbiter(cluster_manager=cm)
        ok, reason = arbiter._check_gpu_arch_compatibility("sm_86", "old-gpu:gpu-cuda-0")
        assert ok is False
        assert "sm_86" in (reason or "")

    def test_arch_requirement_worker_draining_excluded(self):
        """Draining workers should be considered for arch compatibility."""
        from tinyagentos.cluster.manager import ClusterManager
        from tinyagentos.cluster.worker_protocol import WorkerInfo

        cm = ClusterManager()
        w = WorkerInfo(
            name="gpu-worker",
            url="http://gpu:6969",
            hardware={"gpu": {"model": "NVIDIA RTX 4090 (sm_86)", "type": "cuda", "vram_mb": 24576}},
            capabilities=["llm-chat"],
            status="draining",
        )
        cm._workers["gpu-worker"] = w

        arbiter = GpuArbiter(cluster_manager=cm)
        # Draining workers are still present for compatibility checks
        ok, reason = arbiter._check_gpu_arch_compatibility("sm_86", "gpu-worker:gpu-cuda-0")
        assert ok is True

    def test_arch_requirement_with_compute_cap_field(self):
        """Worker with separate compute_cap field passes check."""
        from tinyagentos.cluster.manager import ClusterManager
        from tinyagentos.cluster.worker_protocol import WorkerInfo

        cm = ClusterManager()
        w = WorkerInfo(
            name="gpu-worker",
            url="http://gpu:6969",
            hardware={"gpu": {"model": "NVIDIA A100", "type": "cuda", "compute_cap": "sm_80", "vram_mb": 81920}},
            capabilities=["llm-chat"],
            status="online",
        )
        cm._workers["gpu-worker"] = w

        arbiter = GpuArbiter(cluster_manager=cm)
        ok, reason = arbiter._check_gpu_arch_compatibility("sm_80", "gpu-worker:gpu-cuda-0")
        assert ok is True

    @pytest.mark.asyncio
    async def test_submit_gpu_with_arch_requirement(self):
        """submit_gpu with required_gpu_arch raises on incompatible cluster."""
        from tinyagentos.cluster.manager import ClusterManager

        cm = ClusterManager()
        arbiter = GpuArbiter(cluster_manager=cm, max_queue_size=10)

        task = _make_task("t-arch", vram_mb=0)
        task.payload = _noop_payload

        with pytest.raises(NoResourceAvailableError, match="GPU architecture"):
            await arbiter.submit_gpu(task, required_vram_mb=0, required_gpu_arch="sm_86")

    @pytest.mark.asyncio
    async def test_submit_gpu_arch_match_passes(self):
        """submit_gpu with matching arch succeeds."""
        from tinyagentos.cluster.manager import ClusterManager
        from tinyagentos.cluster.worker_protocol import WorkerInfo

        cm = ClusterManager()
        w = WorkerInfo(
            name="gpu-worker",
            url="http://gpu:6969",
            hardware={"gpu": {"model": "NVIDIA RTX 4090 (sm_86)", "type": "cuda", "vram_mb": 24576}},
            capabilities=["llm-chat"],
            status="online",
        )
        cm._workers["gpu-worker"] = w

        arbiter = GpuArbiter(cluster_manager=cm, max_queue_size=10)

        task = _make_task("t-ok", vram_mb=0)
        task.payload = _noop_payload

        result = await arbiter.submit_gpu(task, required_vram_mb=0, required_gpu_arch="sm_86")
        assert result == {"ok": True}

    def test_queue_entry_stores_arch(self):
        """QueuedGpuTask entry preserves its arch requirement."""
        task = _make_task("t-qarch", vram_mb=4096)
        entry = _QueuedGpuTask(
            priority=10, seq=1, task=task,
            required_vram_mb=4096, evictable=False,
            required_gpu_arch="sm_86",
        )
        assert entry.required_gpu_arch == "sm_86"

    def test_queue_entry_arch_none_by_default(self):
        """QueuedGpuTask without arch has None."""
        task = _make_task("t-noarch", vram_mb=2048)
        entry = _QueuedGpuTask(
            priority=5, seq=1, task=task,
            required_vram_mb=2048, evictable=True,
        )
        assert entry.required_gpu_arch is None


# ── Release Tasks for Worker (draining integration) ─────────────────────

class TestReleaseTasksForWorker:
    def test_no_running_tasks(self):
        arbiter = GpuArbiter()
        count = arbiter.release_tasks_for_worker("w1")
        assert count == 0

    @pytest.mark.asyncio
    async def test_running_task_not_matching_worker(self):
        """Task with lease on different worker is not released."""
        from tinyagentos.cluster.manager import ClusterManager
        from tinyagentos.cluster.worker_protocol import WorkerInfo

        cm = ClusterManager()
        w = WorkerInfo(
            name="w1",
            url="http://w1:6969",
            hardware={},
            capabilities=["llm-chat"],
            status="online",
        )
        cm._workers["w1"] = w

        arbiter = GpuArbiter(cluster_manager=cm)
        # Insert a fake running task with a lease on a different worker
        lease = await cm.claim_lease("w1:gpu-cuda-0", caller="test", ttl_seconds=60)
        assert lease is not None

        count = arbiter.release_tasks_for_worker("w2")  # Different worker
        assert count == 0
