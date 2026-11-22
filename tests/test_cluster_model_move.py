"""Tests for the real cluster model move functionality (tsk-netftv).

Real transfer behaviour covered:
- Target worker pulls model files into its store over the authenticated
  worker channel, staged via the controller.
- Checksum verified against the source's recorded hash.
- Disk-space check done on the target before any copy.
- Progress through the existing job/progress mechanism.
- Load on target, then wait for the target's heartbeat to report it,
  only then unload on source.
- Failure cleanup on checksum mismatch.
- Agent pins updated only after success.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.asyncio


class TestModelMoveRealTransfer:
    """Tests for the real model transfer implementation."""

    # ── helpers (same class as the tests that call them) ────────────

    async def _pair_register_and_key(
        self, client, app, payload: dict, code_prefix: str = "test-pairing-code"
    ):
        """Pair + register a worker and return (signing_key, register_response)."""
        name = payload["name"]
        url = payload.get("url", "http://localhost:9000")
        platform = payload.get("platform", "linux")
        code = code_prefix + name

        if app.state.cluster_pairing._db is None:  # noqa: SLF001
            await app.state.cluster_pairing.init()
        ch = hashlib.sha256(code.encode()).hexdigest()

        resp = await client.post(
            "/api/cluster/pairing/announce",
            json={"name": name, "url": url, "platform": platform, "code_hash": ch},
        )
        assert resp.status_code == 200, f"announce failed for {name!r}: {resp.text}"
        resp = await client.post(
            "/api/cluster/pairing/confirm", json={"name": name, "code": code}
        )
        assert resp.status_code == 200, f"confirm failed for {name!r}: {resp.text}"
        resp = await client.post(
            "/api/cluster/pairing/claim", json={"name": name, "code": code}
        )
        assert resp.status_code == 200, f"claim failed for {name!r}: {resp.text}"
        key = bytes.fromhex(resp.json()["signing_key"])

        body = json.dumps(payload).encode()
        headers = self._sign(key, name, "POST", "/api/cluster/workers", body)
        resp = await client.post(
            "/api/cluster/workers",
            content=body,
            headers={**headers, "content-type": "application/json"},
        )
        assert resp.status_code == 200, f"register failed for {name!r}: {resp.text}"
        return key, resp

    def _sign(self, key: bytes, name: str, method: str, path: str, body: bytes) -> dict:
        ts = str(int(time.time()))
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{ts}.{method.upper()}.{path}.{body_hash}".encode()
        sig = hmac.new(key, message, hashlib.sha256).hexdigest()
        return {
            "X-TAOS-Worker-Name": name,
            "X-TAOS-Timestamp": ts,
            "X-TAOS-Signature": sig,
        }

    async def _heartbeat(
        self,
        client,
        app,
        key: bytes,
        name: str,
        models: list[str],
        storage_cap_bytes: int | None = None,
        storage_used_bytes: int | None = None,
    ):
        """POST a real, HMAC-signed worker heartbeat payload."""
        payload: dict = {"name": name, "load": 0.0, "models": models}
        if storage_cap_bytes is not None:
            payload["storage_cap_bytes"] = storage_cap_bytes
        if storage_used_bytes is not None:
            payload["storage_used_bytes"] = storage_used_bytes
        body = json.dumps(payload).encode()
        headers = self._sign(key, name, "POST", "/api/cluster/heartbeat", body)
        return await client.post(
            "/api/cluster/heartbeat",
            content=body,
            headers={**headers, "content-type": "application/json"},
        )

    def _models_root(self, monkeypatch, tmp_path: Path) -> Path:
        """Hermetic model store for the test; returns the root path."""
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path))
        from tinyagentos.installers.model_paths import models_root

        return models_root()

    # ── tests ───────────────────────────────────────────────────────

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

        progress_resp = await client.get(f"/api/cluster/models/move/{job_id}")
        assert progress_resp.status_code == 200
        progress = progress_resp.json()
        assert "status" in progress
        assert "progress" in progress  # 0-100 or similar

    async def test_move_unknown_source_404(self, client, app, pair_and_register_worker):
        """Move from an unknown worker is rejected."""
        await pair_and_register_worker(client, app, {
            "name": "target", "url": "http://target:9000", "capabilities": ["chat"],
        })
        resp = await client.post("/api/cluster/models/move", json={
            "item": "llama3", "from_worker": "ghost", "to_worker": "target",
        })
        assert resp.status_code == 404, resp.text

    async def test_move_unknown_target_404(self, client, app, pair_and_register_worker):
        """Move to an unknown worker is rejected."""
        await pair_and_register_worker(client, app, {
            "name": "source", "url": "http://source:9000",
            "capabilities": ["chat"], "models": ["llama3"],
        })
        resp = await client.post("/api/cluster/models/move", json={
            "item": "llama3", "from_worker": "source", "to_worker": "ghost",
        })
        assert resp.status_code == 404, resp.text

    async def test_move_checksum_verification_success(
        self, client, app, pair_and_register_worker, monkeypatch, tmp_path
    ):
        """Transfer succeeds when the checksum matches the source's recorded hash."""
        models_dir = self._models_root(monkeypatch, tmp_path)
        mock_model = models_dir / "llama3.gguf"
        mock_model.write_bytes(b"fake model content for testing")

        await self._pair_register_and_key(client, app, {
            "name": "source", "url": "http://source:9000",
            "capabilities": ["chat"], "models": ["llama3"],
        })
        target_key, _ = await self._pair_register_and_key(client, app, {
            "name": "target", "url": "http://target:9000",
            "capabilities": ["chat"],
        })

        resp = await client.post("/api/cluster/models/move", json={
            "item": "llama3", "from_worker": "source", "to_worker": "target",
        })
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # The transfer stages the file, verifies it, then waits for the
        # target's heartbeat to report the model before unloading source.
        for _ in range(100):
            progress = (await client.get(f"/api/cluster/models/move/{job_id}")).json()
            if progress.get("stage") == "loading_on_target":
                break
            await asyncio.sleep(0.1)
        assert progress.get("stage") == "loading_on_target", progress

        # Source still serves the model until the target reports ready.
        workers = (await client.get("/api/cluster/workers")).json()
        source = next(w for w in workers if w["name"] == "source")
        assert "llama3" in source.get("models", [])

        # Real heartbeat payload from the target reporting the model loaded.
        hr = await self._heartbeat(client, app, target_key, "target", ["llama3"])
        assert hr.status_code == 200, hr.text

        for _ in range(100):
            progress = (await client.get(f"/api/cluster/models/move/{job_id}")).json()
            if progress["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        assert progress["status"] == "completed", progress
        assert progress.get("checksum_verified") is True

        workers = (await client.get("/api/cluster/workers")).json()
        source = next(w for w in workers if w["name"] == "source")
        target = next(w for w in workers if w["name"] == "target")
        assert "llama3" not in source.get("models", []), "Source unloaded before target ready"
        assert "llama3" in target.get("models", []), "Target does not have model after move"

    async def test_move_checksum_mismatch_aborts_and_cleans_up(
        self, client, app, pair_and_register_worker, monkeypatch, tmp_path
    ):
        """Transfer aborts and cleans up partial files on checksum mismatch."""
        models_dir = self._models_root(monkeypatch, tmp_path)
        mock_model = models_dir / "llama3.gguf"
        mock_model.write_bytes(b"fake model content for testing")

        await self._pair_register_and_key(client, app, {
            "name": "source", "url": "http://source:9000",
            "capabilities": ["chat"], "models": ["llama3"],
        })
        await self._pair_register_and_key(client, app, {
            "name": "target", "url": "http://target:9000", "capabilities": ["chat"],
        })

        import tinyagentos.routes.cluster as cluster_routes

        def _fake_hash(path):
            p = str(path)
            if "transferred" in p:
                # Staged copy: real content hash.
                return hashlib.sha256(Path(p).read_bytes()).hexdigest()
            # Source's recorded hash: wrong on purpose.
            return "0" * 64

        with patch.object(cluster_routes, "_hash_file", side_effect=_fake_hash):
            resp = await client.post("/api/cluster/models/move", json={
                "item": "llama3", "from_worker": "source", "to_worker": "target",
            })
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        for _ in range(100):
            progress = (await client.get(f"/api/cluster/models/move/{job_id}")).json()
            if progress["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        assert progress["status"] == "failed", progress
        assert "checksum" in progress.get("error", "").lower()
        assert progress.get("cleaned_up") is True
        target_dir = models_dir / "transferred" / "target"
        staged = list(target_dir.iterdir()) if target_dir.exists() else []
        assert not staged, f"Staged file not cleaned up: {staged}"

    async def test_move_target_out_of_space_refused_before_copy(
        self, client, app, pair_and_register_worker, monkeypatch, tmp_path
    ):
        """Transfer refused before copying if target has insufficient disk space."""
        models_dir = self._models_root(monkeypatch, tmp_path)
        mock_model = models_dir / "llama3.gguf"
        mock_model.write_bytes(b"fake model content for testing")

        await self._pair_register_and_key(client, app, {
            "name": "source", "url": "http://source:9000",
            "capabilities": ["chat"], "models": ["llama3"],
        })
        target_key, _ = await self._pair_register_and_key(client, app, {
            "name": "target", "url": "http://target:9000", "capabilities": ["chat"],
        })

        # Target reports a capacity smaller than the model file.
        hr = await self._heartbeat(
            client, app, target_key, "target", [],
            storage_cap_bytes=1024, storage_used_bytes=1000,
        )
        assert hr.status_code == 200, hr.text

        resp = await client.post("/api/cluster/models/move", json={
            "item": "llama3", "from_worker": "source", "to_worker": "target",
        })
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["job_id"]

        for _ in range(100):
            progress = (await client.get(f"/api/cluster/models/move/{job_id}")).json()
            if progress["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        assert progress["status"] == "failed", progress
        assert "space" in progress.get("error", "").lower()
        # Nothing was copied to the target's staging area.
        target_dir = models_dir / "transferred" / "target"
        staged = list(target_dir.iterdir()) if target_dir.exists() else []
        assert not staged, f"Copy happened despite insufficient space: {staged}"

    async def test_move_source_keeps_serving_until_target_ready(
        self, client, app, pair_and_register_worker, monkeypatch, tmp_path
    ):
        """Source worker keeps the model until the target reports loaded."""
        models_dir = self._models_root(monkeypatch, tmp_path)
        mock_model = models_dir / "llama3.gguf"
        mock_model.write_bytes(b"fake model content for testing")

        await self._pair_register_and_key(client, app, {
            "name": "source", "url": "http://source:9000",
            "capabilities": ["chat"], "models": ["llama3"],
        })
        target_key, _ = await self._pair_register_and_key(client, app, {
            "name": "target", "url": "http://target:9000", "capabilities": ["chat"],
        })

        resp = await client.post("/api/cluster/models/move", json={
            "item": "llama3", "from_worker": "source", "to_worker": "target",
        })
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # Wait for the transfer to reach "waiting for target heartbeat".
        for _ in range(100):
            progress = (await client.get(f"/api/cluster/models/move/{job_id}")).json()
            if progress.get("stage") == "loading_on_target":
                break
            await asyncio.sleep(0.1)
        assert progress.get("stage") == "loading_on_target", progress

        # Source still has the model while we wait for the target heartbeat.
        workers = (await client.get("/api/cluster/workers")).json()
        source = next(w for w in workers if w["name"] == "source")
        assert "llama3" in source.get("models", []), "Source dropped model before target ready"

        # Real heartbeat from the target reporting the model.
        hr = await self._heartbeat(client, app, target_key, "target", ["llama3"])
        assert hr.status_code == 200, hr.text

        for _ in range(100):
            progress = (await client.get(f"/api/cluster/models/move/{job_id}")).json()
            if progress["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        assert progress["status"] == "completed", progress
        workers = (await client.get("/api/cluster/workers")).json()
        source = next(w for w in workers if w["name"] == "source")
        target = next(w for w in workers if w["name"] == "target")
        assert "llama3" not in source.get("models", []), "Source still has model after target ready"
        assert "llama3" in target.get("models", [])

    async def test_move_heartbeat_after_move_shows_model_on_target(
        self, client, app, pair_and_register_worker, monkeypatch, tmp_path
    ):
        """After move completes, a real heartbeat shows the model on the target."""
        models_dir = self._models_root(monkeypatch, tmp_path)
        mock_model = models_dir / "llama3.gguf"
        mock_model.write_bytes(b"fake model content for testing")

        await self._pair_register_and_key(client, app, {
            "name": "source", "url": "http://source:9000",
            "capabilities": ["chat"], "models": ["llama3"],
        })
        target_key, _ = await self._pair_register_and_key(client, app, {
            "name": "target", "url": "http://target:9000", "capabilities": ["chat"],
        })

        resp = await client.post("/api/cluster/models/move", json={
            "item": "llama3", "from_worker": "source", "to_worker": "target",
        })
        job_id = resp.json()["job_id"]

        for _ in range(100):
            progress = (await client.get(f"/api/cluster/models/move/{job_id}")).json()
            if progress.get("stage") == "loading_on_target":
                break
            await asyncio.sleep(0.1)

        hr = await self._heartbeat(client, app, target_key, "target", ["llama3"])
        assert hr.status_code == 200, hr.text

        for _ in range(100):
            progress = (await client.get(f"/api/cluster/models/move/{job_id}")).json()
            if progress["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        assert progress["status"] == "completed", progress

        workers = (await client.get("/api/cluster/workers")).json()
        source = next(w for w in workers if w["name"] == "source")
        target = next(w for w in workers if w["name"] == "target")
        assert "llama3" not in source.get("models", []), "Source still has model after move"
        assert "llama3" in target.get("models", []), "Target does not have model after move"

    async def test_move_agents_pinned_to_source_handled(
        self, client, app, pair_and_register_worker, monkeypatch, tmp_path
    ):
        """Agents pinned to source worker are updated only after success."""
        models_dir = self._models_root(monkeypatch, tmp_path)
        mock_model = models_dir / "llama3.gguf"
        mock_model.write_bytes(b"fake model content for testing")

        await self._pair_register_and_key(client, app, {
            "name": "source", "url": "http://source:9000",
            "capabilities": ["chat"], "models": ["llama3"],
        })
        target_key, _ = await self._pair_register_and_key(client, app, {
            "name": "target", "url": "http://target:9000", "capabilities": ["chat"],
        })

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

        for _ in range(100):
            progress = (await client.get(f"/api/cluster/models/move/{job_id}")).json()
            if progress.get("stage") == "loading_on_target":
                break
            await asyncio.sleep(0.1)

        hr = await self._heartbeat(client, app, target_key, "target", ["llama3"])
        assert hr.status_code == 200, hr.text

        for _ in range(100):
            progress = (await client.get(f"/api/cluster/models/move/{job_id}")).json()
            if progress["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        assert progress["status"] == "completed", progress
        agent = next(a for a in app.state.config.agents if a["name"] == "pinned-agent")
        assert agent.get("remote") == "target", f"Agent pin not updated after success: {agent.get('remote')}"
