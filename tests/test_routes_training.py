import pytest


class TestTrainingRoutes:
    @pytest.mark.asyncio
    async def test_list_jobs_empty(self, client):
        resp = await client.get("/api/training/jobs")
        assert resp.status_code == 200
        assert resp.json()["jobs"] == []

    @pytest.mark.asyncio
    async def test_create_and_get_job(self, client):
        resp = await client.post("/api/training/jobs", json={
            "base_model": "qwen3-0.6b",
            "agent_name": "test-agent",
        })
        assert resp.status_code == 200
        job_id = resp.json()["id"]
        resp = await client.get(f"/api/training/jobs/{job_id}")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_job(self, client):
        resp = await client.post("/api/training/jobs", json={"base_model": "qwen3-0.6b"})
        job_id = resp.json()["id"]
        resp = await client.delete(f"/api/training/jobs/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_list_presets(self, client):
        resp = await client.get("/api/training/presets")
        assert resp.status_code == 200
        assert "presets" in resp.json()
