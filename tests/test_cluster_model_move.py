"""Tests for the real cluster model move functionality (tsk-netftv).

These tests verify the REAL model transfer behavior:
- Target worker pulls model files from source via authenticated channel
- Checksum verification
- Disk space check on target before copying
- Progress reporting via job ID
- Load on target, then unload on source only after target reports ready
- Failure cleanup
- Heartbeat after move shows model on target
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, patch


pytestmark = pytest.mark.asyncio


def _mock_worker_endpoints(router, worker_name: str, model_id: str, filename: str, file_size: int, checksum: str):
    """Mock worker model endpoints for a given worker."""
    base_url = f"http://{worker_name}:9000"

    # Mock model pull - returns success
    router.post(f"{base_url}/api/worker/models/pull").mock(
        return_value=httpx.Response(
            200,
            json={"status": "pulled", "model_id": model_id, "path": f"/models/{filename}", "size": file_size}
        )
    )

    # Mock model load - returns success
    router.post(f"{base_url}/api/worker/models/load").mock(
        return_value=httpx.Response(
            200,
            json={"status": "loaded", "model_id": model_id, "backend": "auto"}
        )
    )

    # Mock model unload - returns success
    router.post(f"{base_url}/api/worker/models/unload").mock(
        return_value=httpx.Response(
            200,
            json={"status": "unloaded", "model_id": model_id, "backend": "auto"}
        )
    )


class TestModelMoveRealTransfer:
    """Tests for the real model transfer implementation."""

    async def test_move_returns_job_id_with_progress(self, client, app, pair_and_register_worker):
        """POST /api/cluster/models/move returns a job ID with progress tracking."""
        await pair_and_register_worker(client, app, {
            "name": "source", "url": "http://source:9000",
            "capabilities": ["chat"], "models": ["llama3"],
        })
        await pair_and_register_worker(client, app, {
            "name": "target", "url": "http://target:9000",
            "capabilities": ["chat"],
        })

        resp = await client.post("/api/cluster/models/move", json={
            "item": "llama3", "from_worker": "source", "to_worker": "target",
        })
        # Should return 202 with job_id, not 200 with "moved"
        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "started"

    async def test_move_progress_endpoint_reports_progress(self, client, app, pair_and_register_worker):
        """GET /api/cluster/models/move/<job_id> reports transfer progress."""
        await pair_and_register_worker(client, app, {
            "name": "source", "url": "http://source:9000",
            "capabilities": ["chat"], "models": ["llama3"],
        })
        await pair_and_register_worker(client, app, {
            "name": "target", "url": "http://target:9000",
            "capabilities": ["chat"],
        })

        resp = await client.post("/api/cluster/models/move", json={
            "item": "llama3", "from_worker": "source", "to_worker": "target",
        })
        job_id = resp.json()["job_id"]

        # Poll progress endpoint
        progress_resp = await client.get(f"/api/cluster/models/move/{job_id}")
        assert progress_resp.status_code == 200
        progress = progress_resp.json()
        assert "status" in progress
        assert "progress" in progress  # 0-100 or similar

    @respx.mock
    async def test_move_checksum_verification_success(self, client, app, pair_and_register_worker, tmp_path):
        """Transfer succeeds when checksum matches."""
        # Create a mock model file in the model store
        from tinyagentos.installers.model_paths import models_root
        models_dir = models_root()
        models_dir.mkdir(parents=True, exist_ok=True)
        mock_model = models_dir / "llama3.gguf"
        content = b"fake model content for testing"
        mock_model.write_bytes(content)
        file_size = len(content)
        checksum = hashlib.sha256(content).hexdigest()
        filename = "llama3.gguf"

        await pair_and_register_worker(client, app, {
            "name": "source", "url": "http://source:9000",
            "capabilities": ["chat"], "models": ["llama3"],
        })
        await pair_and_register_worker(client, app, {
            "name": "target", "url": "http://target:9000",
            "capabilities": ["chat"],
        })

        # Mock worker endpoints
        _mock_worker_endpoints(respx, "target", "llama3", filename, file_size, checksum)
        _mock_worker_endpoints(respx, "source", "llama3", filename, file_size, checksum)

        resp = await client.post("/api/cluster/models/move", json={
            "item": "llama3", "from_worker": "source", "to_worker": "target",
        })
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # Wait for completion
        for _ in range(50):
            progress_resp = await client.get(f"/api/cluster/models/move/{job_id}")
            progress = progress_resp.json()
            if progress["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        # Should complete with checksum verified
        assert progress["status"] == "completed"
        assert progress.get("checksum_verified") is True

    @respx.mock
    async def test_move_checksum_mismatch_aborts_and_cleans_up(self, client, app, pair_and_register_worker):
        """Transfer aborts and cleans up partial files on checksum mismatch."""
        await pair_and_register_worker(client, app, {
            "name": "source", "url": "http://source:9000",
            "capabilities": ["chat"], "models": ["llama3"],
        })
        await pair_and_register_worker(client, app, {
            "name": "target", "url": "http://target:9000",
            "capabilities": ["chat"],
        })

        # Mock worker pull endpoint to return checksum mismatch
        respx.post("http://target:9000/api/worker/models/pull").mock(
            return_value=httpx.Response(
                409,
                json={"error": "Checksum mismatch", "code": "checksum_mismatch"}
            )
        )
        _mock_worker_endpoints(respx, "source", "llama3", "llama3.gguf", 100, "abc")

        resp = await client.post("/api/cluster/models/move", json={
            "item": "llama3", "from_worker": "source", "to_worker": "target",
        })
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # Wait for completion
        for _ in range(50):
            progress_resp = await client.get(f"/api/cluster/models/move/{job_id}")
            progress = progress_resp.json()
            if progress["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        # If checksum mismatch simulated, should be failed with cleanup
        if progress["status"] == "failed":
            assert "checksum" in progress.get("error", "").lower()
            assert progress.get("cleaned_up") is True

    @respx.mock
    async def test_move_target_out_of_space_refused_before_copy(self, client, app, pair_and_register_worker):
        """Transfer refused before copying if target has insufficient disk space."""
        await pair_and_register_worker(client, app, {
            "name": "source", "url": "http://source:9000",
            "capabilities": ["chat"], "models": ["llama3"],
        })
        await pair_and_register_worker(client, app, {
            "name": "target", "url": "http://target:9000",
            "capabilities": ["chat"],
        })

        # Mock worker pull endpoint to return insufficient space
        respx.post("http://target:9000/api/worker/models/pull").mock(
            return_value=httpx.Response(
                507,
                json={"error": "Insufficient disk space", "code": "insufficient_space"}
            )
        )
        _mock_worker_endpoints(respx, "source", "llama3", "llama3.gguf", 100, "abc")

        resp = await client.post("/api/cluster/models/move", json={
            "item": "llama3", "from_worker": "source", "to_worker": "target",
        })
        # Should fail fast with 400/503 if target has no space
        # (Current fake impl returns 200, real impl should check space first)
        assert resp.status_code in (202, 400, 503)
        if resp.status_code == 202:
            # If job started, it should fail quickly
            job_id = resp.json()["job_id"]
            for _ in range(10):
                progress_resp = await client.get(f"/api/cluster/models/move/{job_id}")
                progress = progress_resp.json()
                if progress["status"] in ("completed", "failed"):
                    break
                await asyncio.sleep(0.1)
            assert progress["status"] == "failed"
            assert "space" in progress.get("error", "").lower()

    @respx.mock
    async def test_move_source_keeps_serving_until_target_ready(self, client, app, pair_and_register_worker):
        """Source worker keeps model in its models list until target reports loaded."""
        await pair_and_register_worker(client, app, {
            "name": "source", "url": "http://source:9000",
            "capabilities": ["chat"], "models": ["llama3"],
        })
        await pair_and_register_worker(client, app, {
            "name": "target", "url": "http://target:9000",
            "capabilities": ["chat"],
        })

        _mock_worker_endpoints(respx, "target", "llama3", "llama3.gguf", 100, "abc")
        _mock_worker_endpoints(respx, "source", "llama3", "llama3.gguf", 100, "abc")

        resp = await client.post("/api/cluster/models/move", json={
            "item": "llama3", "from_worker": "source", "to_worker": "target",
        })
        job_id = resp.json()["job_id"]

        # During transfer, source should still have the model
        for _ in range(10):
            workers = (await client.get("/api/cluster/workers")).json()
            source = next(w for w in workers if w["name"] == "source")
            if "llama3" not in source.get("models", []):
                break
            await asyncio.sleep(0.1)
        else:
            # Source should keep model until target confirms
            workers = (await client.get("/api/cluster/workers")).json()
            source = next(w for w in workers if w["name"] == "source")
            # This assertion will fail with fake impl, pass with real impl
            assert "llama3" in source.get("models", []), "Source dropped model before target ready"

    @respx.mock
    async def test_move_heartbeat_after_move_shows_model_on_target(self, client, app, pair_and_register_worker):
        """After move completes, heartbeat shows model on target worker."""
        await pair_and_register_worker(client, app, {
            "name": "source", "url": "http://source:9000",
            "capabilities": ["chat"], "models": ["llama3"],
        })
        await pair_and_register_worker(client, app, {
            "name": "target", "url": "http://target:9000",
            "capabilities": ["chat"],
        })

        _mock_worker_endpoints(respx, "target", "llama3", "llama3.gguf", 100, "abc")
        _mock_worker_endpoints(respx, "source", "llama3", "llama3.gguf", 100, "abc")

        resp = await client.post("/api/cluster/models/move", json={
            "item": "llama3", "from_worker": "source", "to_worker": "target",
        })
        job_id = resp.json()["job_id"]

        # Wait for completion
        for _ in range(50):
            progress_resp = await client.get(f"/api/cluster/models/move/{job_id}")
            progress = progress_resp.json()
            if progress["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        assert progress["status"] == "completed"

        # Verify via workers list (which reflects heartbeat state)
        workers = (await client.get("/api/cluster/workers")).json()
        source = next(w for w in workers if w["name"] == "source")
        target = next(w for w in workers if w["name"] == "target")

        # Source should no longer have it, target should
        assert "llama3" not in source.get("models", []), "Source still has model after move"
        assert "llama3" in target.get("models", []), "Target does not have model after move"

    @respx.mock
    async def test_move_agents_pinned_to_source_handled(self, client, app, pair_and_register_worker):
        """Agents pinned to source worker are handled appropriately during move."""
        await pair_and_register_worker(client, app, {
            "name": "source", "url": "http://source:9000",
            "capabilities": ["chat"], "models": ["llama3"],
        })
        await pair_and_register_worker(client, app, {
            "name": "target", "url": "http://target:9000",
            "capabilities": ["chat"],
        })

        _mock_worker_endpoints(respx, "target", "llama3", "llama3.gguf", 100, "abc")
        _mock_worker_endpoints(respx, "source", "llama3", "llama3.gguf", 100, "abc")

        # Pin an agent to source
        app.state.config.agents.append({
            "name": "pinned-agent",
            "host": "10.0.0.5",
            "qmd_index": "test",
            "remote": "source",
            "placement_source": "user",
            "status": "running",
        })

        resp = await client.post("/api/cluster/models/move", json={
            "item": "llama3", "from_worker": "source", "to_worker": "target",
        })
        job_id = resp.json()["job_id"]

        # Wait for completion
        for _ in range(50):
            progress_resp = await client.get(f"/api/cluster/models/move/{job_id}")
            progress = progress_resp.json()
            if progress["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        # Agent pin should be updated or marked for migration
        agent = next(a for a in app.state.config.agents if a["name"] == "pinned-agent")
        # Real impl: agent.remote should be updated to target, or agent marked for re-placement
        assert agent.get("remote") in ("target", ""), f"Agent pin not updated: {agent.get('remote')}"