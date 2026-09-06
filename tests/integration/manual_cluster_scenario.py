#!/usr/bin/env python3
"""Cluster Integration Test — real Pi controller + Fedora GPU + Pi4 CPU worker.

Drives the resource manager through real-world scenarios using actual
Ollama instances running in containers and on the host.

Requirements:
  - Fedora host: Ollama with qwen3:4b and qwen3.5:9b
  - pi4-emulated LXC: Ollama with qwen3:4b (CPU-only)
  - taosmd installed on all

Usage:
  python3 tests/integration/cluster_scenario_test.py

This is NOT a pytest test — it's a manual integration test that prints
results and needs the full cluster environment.
"""

import asyncio
import json
import sys
import time

sys.path.insert(0, ".")

import httpx
from taosmd.resource_manager import ResourceManager, ResourceSnapshot
from taosmd.job_queue import JobQueue


# Endpoints — adjust for your network
FEDORA_OLLAMA = "http://localhost:11434"          # Fedora host Ollama (GPU)
PI4_OLLAMA = "http://10.228.114.133:11434"         # Pi4 container Ollama (CPU)


async def check_endpoint(url: str, name: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{url}/api/tags")
            if r.status_code == 200:
                models = [m["name"] for m in r.json().get("models", [])]
                print(f"  {name}: OK — {len(models)} models: {', '.join(models[:5])}")
                return True
    except Exception as e:
        print(f"  {name}: FAILED — {e}")
    return False


async def simulate_gpu_utilisation(url: str, busy: bool) -> dict:
    """Query nvidia-smi style info. In real deployment this comes from worker heartbeat."""
    # For now, return mock data. Real integration would read from worker API.
    return {"gpu_utilisation": 95 if busy else 5}


async def scenario_1_pi_only():
    """Scenario 1: Pi-only, no workers. Memory runs on local models."""
    print("\n" + "=" * 60)
    print("Scenario 1: Pi-Only Operation")
    print("=" * 60)

    mgr = ResourceManager(ollama_url=FEDORA_OLLAMA)  # Using Fedora as "local" for testing
    snap = await mgr.refresh()

    print(f"  CPU cores: {snap.cpu_cores}")
    print(f"  NPU cores: {snap.npu_cores}")
    print(f"  GPU: {snap.gpu.get('name', 'none')}")
    print(f"  Models: {snap.ollama_models}")
    print(f"  Workers: {len(snap.cluster_workers)}")

    best = await mgr.best_model_for_task("extract")
    print(f"  Best model for extraction: {best}")
    print("  PASS" if best else "  FAIL — no model available")
    return bool(best)


async def scenario_2_worker_joins():
    """Scenario 2: GPU worker joins, system upgrades to larger model."""
    print("\n" + "=" * 60)
    print("Scenario 2: GPU Worker Joins")
    print("=" * 60)

    mgr = ResourceManager(ollama_url=FEDORA_OLLAMA)

    # Baseline — no workers
    mgr._prev_snapshot = ResourceSnapshot()
    mgr._prev_snapshot.cluster_workers = []

    # Simulate worker join by creating a snapshot with a worker
    snap = await mgr.refresh()
    # Inject a fake worker (in real life, the taOS controller API provides this)
    snap.cluster_workers = [{
        "name": "fedora-gpu",
        "gpu": True,
        "models": ["qwen3.5:9b"],
        "gpu_utilisation": 0,
    }]
    mgr._snapshot = snap

    async def mock_snap(force_refresh=False):
        return snap
    mgr.get_snapshot = mock_snap

    action = await mgr.evaluate_migration()
    if action:
        print(f"  Migration: {action['action']} → {action['to_model']} on {action['to_location']}")
        print(f"  Reason: {action['reason']}")
        print("  PASS — correctly upgraded to GPU worker")
        return True
    else:
        print("  FAIL — no migration triggered")
        return False


async def scenario_3_contention():
    """Scenario 3: GPU gets busy (video gen), falls back to local."""
    print("\n" + "=" * 60)
    print("Scenario 3: GPU Contention → Fallback")
    print("=" * 60)

    mgr = ResourceManager(ollama_url=FEDORA_OLLAMA, contention_threshold=0)

    # Previous: worker was fine
    prev = ResourceSnapshot()
    prev.cluster_workers = [{"name": "fedora-gpu", "gpu": True, "models": ["qwen3.5:9b"], "gpu_utilisation": 10}]
    mgr._prev_snapshot = prev

    # Current: worker is busy
    curr = ResourceSnapshot()
    curr.cluster_workers = [{"name": "fedora-gpu", "gpu": True, "models": ["qwen3.5:9b"], "gpu_utilisation": 95}]
    curr.npu_cores = 3  # Pi fallback
    curr.ollama_models = ["qwen3:4b"]  # Local fallback
    mgr._snapshot = curr
    mgr._worker_busy_since["fedora-gpu"] = time.time() - 1

    async def mock_snap(force_refresh=False):
        return curr
    mgr.get_snapshot = mock_snap

    action = await mgr.evaluate_migration()
    if action and action["action"] == "downgrade":
        print(f"  Migration: {action['action']} → local")
        print(f"  Reason: {action['reason']}")
        print("  PASS — correctly downgraded on contention")
        return True
    else:
        print(f"  FAIL — expected downgrade, got: {action}")
        return False


async def scenario_4_recovery():
    """Scenario 4: GPU becomes idle, upgrades back."""
    print("\n" + "=" * 60)
    print("Scenario 4: GPU Idle → Upgrade Back")
    print("=" * 60)

    mgr = ResourceManager(ollama_url=FEDORA_OLLAMA, idle_upgrade_delay=0)

    prev = ResourceSnapshot()
    prev.cluster_workers = [{"name": "fedora-gpu", "gpu": True, "models": ["qwen3.5:9b"], "gpu_utilisation": 95}]
    mgr._prev_snapshot = prev

    curr = ResourceSnapshot()
    curr.cluster_workers = [{"name": "fedora-gpu", "gpu": True, "models": ["qwen3.5:9b"], "gpu_utilisation": 5}]
    mgr._snapshot = curr
    mgr._worker_idle_since["fedora-gpu"] = time.time() - 1

    async def mock_snap(force_refresh=False):
        return curr
    mgr.get_snapshot = mock_snap

    action = await mgr.evaluate_migration()
    if action and action["action"] == "upgrade":
        print(f"  Migration: {action['action']} → {action['to_model']} on {action['to_location']}")
        print(f"  Reason: {action['reason']}")
        print("  PASS — correctly upgraded back after idle")
        return True
    else:
        print(f"  FAIL — expected upgrade, got: {action}")
        return False


async def scenario_5_disconnect():
    """Scenario 5: Worker disconnects, immediate fallback."""
    print("\n" + "=" * 60)
    print("Scenario 5: Worker Disconnects")
    print("=" * 60)

    mgr = ResourceManager(ollama_url=FEDORA_OLLAMA)

    prev = ResourceSnapshot()
    prev.cluster_workers = [{"name": "fedora-gpu", "gpu": True, "models": ["qwen3.5:9b"]}]
    mgr._prev_snapshot = prev

    curr = ResourceSnapshot()
    curr.cluster_workers = []  # Worker gone
    curr.npu_cores = 3
    curr.ollama_models = ["qwen3:4b"]
    mgr._snapshot = curr

    async def mock_snap(force_refresh=False):
        return curr
    mgr.get_snapshot = mock_snap

    action = await mgr.evaluate_migration()
    if action and action["action"] == "downgrade":
        print(f"  Migration: {action['action']} → {action.get('to_model', 'local')}")
        print(f"  Reason: {action['reason']}")
        print("  PASS — correctly fell back on disconnect")
        return True
    else:
        print(f"  FAIL — expected downgrade, got: {action}")
        return False


async def scenario_6_job_queue_integration():
    """Scenario 6: Job queue respects resource limits."""
    print("\n" + "=" * 60)
    print("Scenario 6: Job Queue + Resource Manager")
    print("=" * 60)

    import tempfile, os
    tmp = tempfile.mkdtemp()
    q = JobQueue(os.path.join(tmp, "q.db"))
    await q.init()

    mgr = ResourceManager(job_queue=q, ollama_url=FEDORA_OLLAMA)
    snap = await mgr.refresh()

    limits = await q.get_limits()
    print(f"  Detected limits: {limits}")
    print(f"  CPU: {limits.get('cpu', 0)} | NPU: {limits.get('npu', 0)} | GPU: {limits.get('gpu', 0)}")

    # Enqueue more jobs than GPU limit
    j1 = await q.enqueue("extract", resource_type="gpu")
    j2 = await q.enqueue("extract", resource_type="gpu")

    claimed1 = await q.dequeue(resource_types=["gpu"])
    claimed2 = await q.dequeue(resource_types=["gpu"])

    gpu_limit = limits.get("gpu", 0)
    if gpu_limit == 0:
        print(f"  No GPU detected — both jobs should fail to dequeue")
        ok = claimed1 is None and claimed2 is None
    elif gpu_limit == 1:
        print(f"  1 GPU — first should dequeue, second should wait")
        ok = claimed1 is not None and claimed2 is None
    else:
        print(f"  {gpu_limit} GPUs — both should dequeue")
        ok = claimed1 is not None and claimed2 is not None

    print(f"  {'PASS' if ok else 'FAIL'}")
    await q.close()
    return ok


async def main():
    print("=" * 60)
    print("taOSmd Cluster Integration Test")
    print("=" * 60)

    # Check endpoints
    print("\nChecking endpoints:")
    fedora_ok = await check_endpoint(FEDORA_OLLAMA, "Fedora (GPU)")

    results = []
    results.append(("Pi-Only", await scenario_1_pi_only()))
    results.append(("Worker Joins", await scenario_2_worker_joins()))
    results.append(("Contention", await scenario_3_contention()))
    results.append(("Recovery", await scenario_4_recovery()))
    results.append(("Disconnect", await scenario_5_disconnect()))
    results.append(("Job Queue", await scenario_6_job_queue_integration()))

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'} — {name}")
    print(f"\n  {passed}/{len(results)} passed")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
