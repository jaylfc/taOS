from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.training import TrainingManager
from taos_test_csrf import csrf_event_hooks


@pytest_asyncio.fixture
async def training_mgr(tmp_path):
    mgr = TrainingManager(tmp_path / "training.db")
    await mgr.init()
    yield mgr
    await mgr.close()


@pytest.fixture
def training_app(tmp_data_dir):
    app = create_app(data_dir=tmp_data_dir)
    return app


@pytest_asyncio.fixture
async def training_client(training_app):
    store = training_app.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    await training_app.state.qmd_client.init()
    training = training_app.state.training
    if training._db is not None:
        await training.close()
    await training.init()
    training_app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _rec = training_app.state.auth.find_user("admin")
    _token = training_app.state.auth.create_session(user_id=_rec["id"] if _rec else "", long_lived=True)
    transport = ASGITransport(app=training_app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"taos_session": _token}, event_hooks=csrf_event_hooks()) as c:
        yield c
    await training.close()
    await store.close()
    await training_app.state.qmd_client.close()
    await training_app.state.http_client.aclose()


@pytest.mark.asyncio
class TestTrainingManager:
    async def test_create_job(self, training_mgr):
        job_id = await training_mgr.create_job(base_model="qwen3-1.7b")
        assert len(job_id) == 8
        job = await training_mgr.get_job(job_id)
        assert job is not None
        assert job.base_model == "qwen3-1.7b"
        assert job.status == "queued"

    async def test_create_job_with_agent(self, training_mgr):
        job_id = await training_mgr.create_job(
            base_model="llama3-8b",
            agent_name="test-agent",
            dataset_description="Test dataset",
            config={"epochs": 5},
        )
        job = await training_mgr.get_job(job_id)
        assert job.agent_name == "test-agent"
        assert job.dataset_description == "Test dataset"
        assert job.config == {"epochs": 5}

    async def test_get_nonexistent_job(self, training_mgr):
        job = await training_mgr.get_job("nonexist")
        assert job is None

    async def test_list_jobs(self, training_mgr):
        await training_mgr.create_job(base_model="model-a")
        await training_mgr.create_job(base_model="model-b", agent_name="agent-x")
        await training_mgr.create_job(base_model="model-c", agent_name="agent-x")

        all_jobs = await training_mgr.list_jobs()
        assert len(all_jobs) == 3

        agent_jobs = await training_mgr.list_jobs(agent_name="agent-x")
        assert len(agent_jobs) == 2

    async def test_update_job(self, training_mgr):
        job_id = await training_mgr.create_job(base_model="model-a")
        await training_mgr.update_job(job_id, status="training", progress=0.5, worker_name="gpu-1")
        job = await training_mgr.get_job(job_id)
        assert job.status == "training"
        assert job.progress == 0.5
        assert job.worker_name == "gpu-1"

    async def test_update_job_metrics(self, training_mgr):
        job_id = await training_mgr.create_job(base_model="model-a")
        await training_mgr.update_job(job_id, metrics={"loss": 0.42, "accuracy": 0.95})
        job = await training_mgr.get_job(job_id)
        assert job.metrics == {"loss": 0.42, "accuracy": 0.95}

    async def test_delete_job(self, training_mgr):
        job_id = await training_mgr.create_job(base_model="model-a")
        deleted = await training_mgr.delete_job(job_id)
        assert deleted is True
        job = await training_mgr.get_job(job_id)
        assert job is None

    async def test_delete_nonexistent_job(self, training_mgr):
        deleted = await training_mgr.delete_job("nonexist")
        assert deleted is False

    async def test_presets(self, training_mgr):
        presets = await training_mgr.get_presets()
        assert len(presets) == 3
        ids = [p["id"] for p in presets]
        assert "quick" in ids
        assert "balanced" in ids
        assert "thorough" in ids
        balanced = next(p for p in presets if p["id"] == "balanced")
        assert balanced["config"]["epochs"] == 3
        assert balanced["config"]["lora_rank"] == 16


@pytest.mark.asyncio
class TestTrainingAPI:
    async def test_list_jobs_empty(self, training_client):
        resp = await training_client.get("/api/training/jobs")
        assert resp.status_code == 200
        assert resp.json()["jobs"] == []

    async def test_create_and_get_job(self, training_client):
        resp = await training_client.post("/api/training/jobs", json={
            "base_model": "qwen3-1.7b",
            "dataset_description": "test data",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        job_id = data["id"]

        resp2 = await training_client.get(f"/api/training/jobs/{job_id}")
        assert resp2.status_code == 200
        assert resp2.json()["base_model"] == "qwen3-1.7b"

    async def test_create_job_with_preset(self, training_client):
        resp = await training_client.post("/api/training/jobs", json={
            "base_model": "qwen3-1.7b",
            "preset": "thorough",
        })
        assert resp.status_code == 200
        job_id = resp.json()["id"]

        resp2 = await training_client.get(f"/api/training/jobs/{job_id}")
        config = resp2.json()["config"]
        assert config["epochs"] == 5
        assert config["lora_rank"] == 32

    async def test_get_nonexistent_job(self, training_client):
        resp = await training_client.get("/api/training/jobs/nope")
        assert resp.status_code == 404

    async def test_delete_job(self, training_client):
        resp = await training_client.post("/api/training/jobs", json={
            "base_model": "model-a",
        })
        job_id = resp.json()["id"]

        resp2 = await training_client.delete(f"/api/training/jobs/{job_id}")
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "deleted"

    async def test_delete_nonexistent_job(self, training_client):
        resp = await training_client.delete("/api/training/jobs/nope")
        assert resp.status_code == 404

    async def test_list_presets(self, training_client):
        resp = await training_client.get("/api/training/presets")
        assert resp.status_code == 200
        presets = resp.json()["presets"]
        assert len(presets) == 3

    async def test_retrain_agent(self, training_client):
        resp = await training_client.post("/api/training/retrain/test-agent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_name"] == "test-agent"
        assert data["status"] == "queued"

        # Verify the job was created with correct description
        job_id = data["id"]
        resp2 = await training_client.get(f"/api/training/jobs/{job_id}")
        job = resp2.json()
        assert "test-agent" in job["dataset_description"]
        assert job["agent_name"] == "test-agent"

    async def test_list_jobs_by_agent(self, training_client):
        await training_client.post("/api/training/jobs", json={
            "base_model": "m1", "agent_name": "agent-a",
        })
        await training_client.post("/api/training/jobs", json={
            "base_model": "m2", "agent_name": "agent-b",
        })
        resp = await training_client.get("/api/training/jobs?agent=agent-a")
        assert resp.status_code == 200
        jobs = resp.json()["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["agent_name"] == "agent-a"


