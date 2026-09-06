"""RED test for S2-24 - worker can fabricate unlimited 24 h leases"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.worker_protocol import WorkerInfo


def _make_worker(name: str, backends: list[dict], capabilities: list[str] | None = None,
                 load: float = 0.0, status: str = "online",
                 url: str = "http://localhost:9000") -> WorkerInfo:
    return WorkerInfo(
        name=name,
        url=url,
        capabilities=capabilities or ["chat", "embed"],
        backends=backends,
        load=load,
        status=status,
        platform="linux",
    )


@pytest.mark.asyncio
class TestS224_FabricatedLeases:
    """Test that workers cannot fabricate unlimited leases on unregistered resources (S2-24)."""
    
    async def test_worker_cannot_claim_leases_on_unregistered_resources(self):
        """
        RED test: register a worker with two backends, submit heartbeats claiming
        leases on 1000 fabricated resources -> assert they are rejected
        and the heartbeat still succeeds for valid ones.
        """
        mgr = ClusterManager()
        
        # Register a worker with two registered backends
        worker_backends = [
            {
                "name": "backend-a",
                "type": "nvidia",
                "url": "http://backend-a:11434",
                "capabilities": ["chat"],
                "models": [{"name": "model-a"}]
            },
            {
                "name": "backend-b",
                "type": "nvidia",
                "url": "http://backend-b:11434",
                "capabilities": ["embed"],
                "models": [{"name": "model-b"}]
            }
        ]
        
        worker = _make_worker("test-worker", backends=worker_backends)
        await mgr.register_worker(worker)
        
        # Submit 1000 fabricated resource claims
        fabricated_rejected = 0
        valid_claimed = 0
        
        for i in range(1000):
            # Fabricated resources that don't match worker backends
            fabricated_resource = f"test-worker:fabricated-resource-{i}"
            
            lease = await mgr.claim_lease(fabricated_resource, caller="test")
            
            # All fabricated resources should be rejected
            if lease is None:
                fabricated_rejected += 1
            else:
                # This would be a bug - fabrications should be rejected
                valid_claimed += 1
        
        # Verify all fabricated resources were rejected
        assert fabricated_rejected == 1000, \
            f"Expected all 1000 fabricated resources to be rejected, got {fabricated_rejected}"
        assert valid_claimed == 0, \
            f"Expected no valid claims from fabricated resources, got {valid_claimed}"
        
        # Worker should be able to claim valid resources
        valid_resources = ["test-worker:backend-a", "test-worker:backend-b"]
        
        for resource_id in valid_resources:
            lease = await mgr.claim_lease(resource_id, caller="test")
            assert lease is not None, f"Expected valid resource {resource_id} to be claimable"
            assert lease.resource_id == resource_id, \
                f"Lease resource_id mismatch: expected {resource_id}, got {lease.resource_id}"
        
        # Verify worker lease count is now 2 (the valid claims)
        worker_leases = sum(1 for lease in mgr.get_leases() 
                          if lease.resource_id.startswith("test-worker:"))
        assert worker_leases == 2, \
            f"Expected worker to have 2 leases, got {worker_leases}"
        
        print("✓ RED test passed: All fabricated resources rejected, valid resources claimed")

    async def test_worker_lease_cap_enforced(self):
        """
        Test that workers cannot exceed lease cap (prevents DoS).
        
        This test verifies that the lease cap prevents a worker from claiming
        unlimited leases, even if they try on valid resources.
        """
        mgr = ClusterManager()
        
        # Create worker with 15 registered backends
        # This is more than the lease cap (10), so we can test the cap
        worker_backends = []
        for i in range(15):
            worker_backends.append({
                "name": f"gpu-{i}",
                "type": "nvidia",
                "url": f"http://gpu{i}:11434",
                "capabilities": ["chat"],
                "models": [{"name": f"model-{i}"}]
            })
        
        worker = _make_worker("cap-worker", backends=worker_backends)
        await mgr.register_worker(worker)
        
        # Worker should be able to claim up to the cap (10)
        # Each claim should be on a DIFFERENT registered backend
        claims_made = 0
        claimed_resources = set()
        
        # Try to claim 15 distinct resources (one per backend)
        # We have 15 backends but only 10 leases allowed
        for i in range(15):
            resource_id = f"cap-worker:gpu-{i}"
            
            lease = await mgr.claim_lease(resource_id, caller=f"cap-test{i}")
            
            if lease is not None:
                claims_made += 1
                claimed_resources.add(resource_id)
            # else: claim rejected (either resource already leased or cap reached)
        
        # Exactly 10 claims should succeed (the cap)
        assert claims_made == 10, \
            f"Expected exactly 10 claims (lease cap), got {claims_made}"
        
        # Verify we have 10 distinct resources claimed (one per backend)
        assert len(claimed_resources) == 10, \
            f"Expected 10 distinct resources, got {len(claimed_resources)}"
        
        # Verify worker lease count is exactly 10
        worker_leases = sum(1 for lease in mgr.get_leases() 
                          if lease.resource_id.startswith("cap-worker:"))
        assert worker_leases == 10, \
            f"Expected 10 total leases for worker, got {worker_leases}"
        
        print("✓ Lease cap test passed: Worker limited to 10 leases")

    async def test_resource_validation_against_backends(self):
        """
        Test that _worker_for_resource validates resource against worker's backends.
        """
        mgr = ClusterManager()
        
        # Worker registered with specific backend names
        worker_backends = [
            {"name": "gpu-0", "type": "nvidia", "url": "http://gpu0"},
            {"name": "gpu-1", "type": "nvidia", "url": "http://gpu1"},
        ]
        
        worker = _make_worker("gpu-worker", backends=worker_backends)
        await mgr.register_worker(worker)
        
        # Valid resources should return worker info
        assert mgr._worker_for_resource("gpu-worker:gpu-0") is not None
        assert mgr._worker_for_resource("gpu-worker:gpu-1") is not None
        
        # Invalid resources should return None
        assert mgr._worker_for_resource("gpu-worker:fake-gpu-0") is None
        assert mgr._worker_for_resource("gpu-worker:gpu-2") is None
        assert mgr._worker_for_resource("gpu-worker:gpu-0:extra") is None
        assert mgr._worker_for_resource("gpu-worker:") is None
        assert mgr._worker_for_resource("non-existent:gpu-0") is None
        
        print("✓ Resource validation test passed: Only registered backends accepted")


if __name__ == "__main__":
    # Run the tests
    async def run_all_tests():
        test_obj = TestS224_FabricatedLeases()
        
        print("Running RED test for S2-24...")
        await test_obj.test_worker_cannot_claim_leases_on_unregistered_resources()
        
        print("\nRunning lease cap test...")
        await test_obj.test_worker_lease_cap_enforced()
        
        print("\nRunning resource validation test...")
        await test_obj.test_resource_validation_against_backends()
        
        print("\n✅ All tests passed!")
    
    asyncio.run(run_all_tests())
