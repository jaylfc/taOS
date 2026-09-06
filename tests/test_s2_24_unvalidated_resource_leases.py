"""RED test for S2-24: Worker can fabricate unlimited 24h leases (_worker_for_resource unvalidated)

This test demonstrates the vulnerability where a worker can fabricate unlimited
24h leases by supplying resource IDs that are not validated against the worker's
registered capabilities.

Acceptance: RED test: register a worker with two backends, submit heartbeats
claiming leases on 1000 fabricated resources -> assert they are rejected
(count of leases for the worker stays at the registered number) and the
heartbeat still succeeds for valid ones.
"""
from __future__ import annotations

import asyncio

import pytest

from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.worker_protocol import WorkerInfo


def _make_worker(name: str, capabilities: list[str] | None = None,
                 backends: list[dict] | None = None,
                 load: float = 0.0, status: str = "online") -> WorkerInfo:
    """Create a worker with registered backends."""
    return WorkerInfo(
        name=name,
        url=f"http://{name}:9000",
        capabilities=capabilities or ["llm-chat"],
        load=load,
        status=status,
        platform="linux",
        backends=backends or [
            {"name": "gpu-cuda-0", "type": "cuda", "url": "http://localhost:8000"},
            {"name": "gpu-cuda-1", "type": "cuda", "url": "http://localhost:8001"},
        ],
    )


@pytest.mark.asyncio
class TestS2_24_UnvalidatedResourceLeases:
    """Test for S2-24: Validate resource half against worker's registered capabilities."""

    async def test_vulnerability_exposed_before_fix(self):
        """
        RED Test - Should FAIL before fix, PASS after fix.
        
        This test reproduces the S2-24 vulnerability:
        A worker can fabricate unlimited leases by supplying resource IDs that are
        not validated against the worker's registered capabilities.
        
        Before fix: worker with 2 backends can claim 1000+ fabricated resources
        After fix: worker with 2 backends can only claim 2 resources (one per backend)
        
        Acceptance criteria:
        - Claims on fabricated resources are REJECTED (worker can't claim unlimited leases)
        - Claims on valid resources are ACCEPTED (worker can still use registered backends)
        - Count of leases for the worker stays at the registered number (2 max)
        """
        mgr = ClusterManager()
        
        # Create a worker with 2 registered backends
        worker = _make_worker("test-worker", backends=[
            {"name": "gpu-cuda-0", "type": "cuda", "url": "http://localhost:8000"},
            {"name": "gpu-cuda-1", "type": "cuda", "url": "http://localhost:8001"},
        ])
        
        await mgr.register_worker(worker)
        
        # Verify worker has 2 registered backends
        assert len(worker.backends) == 2
        print(f"Worker has {len(worker.backends)} registered backends: {[b['name'] for b in worker.backends]}")
        
        # Try to claim 1000 fabricated resources (simulating a compromised or buggy worker)
        # These are resource IDs that DON'T match the worker's registered backends
        claim_count = 0
        successful_fabricated_claims = 0
        
        for i in range(1000):
            # Fabricated resource IDs that don't match registered backends
            # Format: "worker-name:resource-name" where resource-name is not in worker.backends
            fabricated_resource_id = f"test-worker:gpu-fabricated-{i}"
            
            # This should be REJECTED after fix because resource_name must match backend name
            lease = await mgr.claim_lease(
                fabricated_resource_id,
                caller="malicious-worker",
                ttl_seconds=24 * 3600,  # 24 hours
            )
            
            if lease is not None:
                successful_fabricated_claims += 1
            
            claim_count += 1
            
            # Early exit if we hit 1000 claims (as per test requirement)
            if claim_count >= 1000:
                break
        
        # Count leases for this worker
        worker_leases = [
            lease for lease in mgr._leases.values()
            if (parsed := mgr._parse_resource_id(lease.resource_id))
            and parsed[0] == "test-worker"
        ]
        
        print(f"Attempted {claim_count} claims on fabricated resources")
        print(f"Successfully claimed {successful_fabricated_claims} fabricated leases")
        print(f"Total leases for worker: {len(worker_leases)}")
        
        # ACCEPTANCE: Claims on fabricated resources MUST be rejected
        # The worker should NOT be able to claim leases on resources that don't match
        # its registered backends. After fix, only 0 fabricated claims should succeed.
        assert successful_fabricated_claims == 0, \
            f"VULNERABILITY: Worker claimed {successful_fabricated_claims} leases on " \
            f"fabricated resources. Expected 0 after fix. The vulnerability S2-24 " \
            f"(unvalidated resource half) must be fixed."
        
        # ACCEPTANCE: Count of leases for the worker should stay at 0 for fabricated resources
        assert len(worker_leases) == 0, \
            f"Worker has {len(worker_leases)} leases. Expected 0 because all claims " \
            f"on fabricated resources should be rejected."

    async def test_valid_resources_still_work(self):
        """
        YELLOW Test - Should PASS before and after fix.
        
        Test that valid resource IDs (matching registered backends) are still accepted
        after the fix. This ensures we don't break legitimate functionality.
        """
        mgr = ClusterManager()
        
        # Create a worker with 2 registered backends
        worker = _make_worker("test-worker", backends=[
            {"name": "gpu-cuda-0", "type": "cuda", "url": "http://localhost:8000"},
            {"name": "gpu-cuda-1", "type": "cuda", "url": "http://localhost:8001"},
        ])
        
        await mgr.register_worker(worker)
        
        # Claim leases on VALID resource IDs that match registered backends
        valid_resources = [
            "test-worker:gpu-cuda-0",  # Matches first backend
            "test-worker:gpu-cuda-1",  # Matches second backend
        ]
        
        successful_claims = 0
        for resource_id in valid_resources:
            lease = await mgr.claim_lease(
                resource_id,
                caller="legitimate-worker",
                ttl_seconds=30,
            )
            if lease is not None:
                successful_claims += 1
        
        # Both valid resources should be claimable
        assert successful_claims == 2, \
            f"Expected 2 claims on valid resources, got {successful_claims}"
        
        # Only 2 leases should exist total (one per registered backend)
        worker_leases = [
            lease for lease in mgr._leases.values()
            if (parsed := mgr._parse_resource_id(lease.resource_id))
            and parsed[0] == "test-worker"
        ]
        assert len(worker_leases) == 2, \
            f"Expected 2 leases for legitimate claims, got {len(worker_leases)}"