from __future__ import annotations

import pytest
import pytest_asyncio

from tinyagentos.conversion import ConversionManager, CONVERSION_PATHS


@pytest_asyncio.fixture
async def conversion_mgr(tmp_path):
    mgr = ConversionManager(tmp_path / "conversion.db")
    await mgr.init()
    yield mgr
    await mgr.close()


@pytest.mark.asyncio
class TestConversionManager:
    async def test_create_job(self, conversion_mgr):
        job_id = await conversion_mgr.create_job(
            source_model="qwen3-1.7b.gguf",
            source_format="gguf",
            target_format="rkllm",
        )
        assert len(job_id) == 8
        job = await conversion_mgr.get_job(job_id)
        assert job is not None
        assert job["source_model"] == "qwen3-1.7b.gguf"
        assert job["source_format"] == "gguf"
        assert job["target_format"] == "rkllm"
        assert job["status"] == "queued"
        assert job["progress"] == 0.0

    async def test_create_job_with_quantization(self, conversion_mgr):
        job_id = await conversion_mgr.create_job(
            source_model="llama3-8b",
            source_format="huggingface",
            target_format="gguf",
            target_quantization="Q4_K_M",
        )
        job = await conversion_mgr.get_job(job_id)
        assert job["target_quantization"] == "Q4_K_M"

    async def test_list_jobs(self, conversion_mgr):
        await conversion_mgr.create_job("model-a", "gguf", "rkllm")
        await conversion_mgr.create_job("model-b", "safetensors", "gguf")
        jobs = await conversion_mgr.list_jobs()
        assert len(jobs) == 2
        # Most recent first
        assert jobs[0]["source_model"] == "model-b"

    async def test_update_job(self, conversion_mgr):
        job_id = await conversion_mgr.create_job("model-a", "gguf", "rkllm")
        await conversion_mgr.update_job(job_id, status="running", progress=0.5, worker_name="gpu-1")
        job = await conversion_mgr.get_job(job_id)
        assert job["status"] == "running"
        assert job["progress"] == 0.5
        assert job["worker_name"] == "gpu-1"

    async def test_delete_job(self, conversion_mgr):
        job_id = await conversion_mgr.create_job("model-a", "gguf", "rkllm")
        deleted = await conversion_mgr.delete_job(job_id)
        assert deleted is True
        assert await conversion_mgr.get_job(job_id) is None

    async def test_delete_nonexistent(self, conversion_mgr):
        deleted = await conversion_mgr.delete_job("nope")
        assert deleted is False

    async def test_get_nonexistent(self, conversion_mgr):
        job = await conversion_mgr.get_job("nope")
        assert job is None


class TestConversionPaths:
    def test_paths_have_required_fields(self):
        for path in CONVERSION_PATHS:
            assert "from" in path
            assert "to" in path
            assert "description" in path
            assert "capability" in path

    def test_gguf_to_rkllm_requires_capability(self):
        rkllm = [p for p in CONVERSION_PATHS if p["from"] == "gguf" and p["to"] == "rkllm"]
        assert len(rkllm) == 1
        assert rkllm[0]["capability"] == "rknn-conversion"


# --- Route tests ---

@pytest.mark.asyncio
async def test_list_conversion_jobs_empty(client):
    resp = await client.get("/api/conversion/jobs")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_and_get_conversion_job(client):
    resp = await client.post("/api/conversion/jobs", json={
        "source_model": "qwen3-1.7b.gguf",
        "source_format": "gguf",
        "target_format": "rkllm",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    job_id = data["id"]

    resp = await client.get(f"/api/conversion/jobs/{job_id}")
    assert resp.status_code == 200
    job = resp.json()
    assert job["source_model"] == "qwen3-1.7b.gguf"
    assert job["status"] == "queued"


@pytest.mark.asyncio
async def test_create_invalid_conversion_path(client):
    resp = await client.post("/api/conversion/jobs", json={
        "source_model": "model",
        "source_format": "gguf",
        "target_format": "pytorch",
    })
    assert resp.status_code == 400
    assert "No conversion path" in resp.json()["error"]


@pytest.mark.asyncio
async def test_delete_conversion_job(client):
    resp = await client.post("/api/conversion/jobs", json={
        "source_model": "model",
        "source_format": "safetensors",
        "target_format": "gguf",
    })
    job_id = resp.json()["id"]

    resp = await client.request("DELETE", f"/api/conversion/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

    resp = await client.get(f"/api/conversion/jobs/{job_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_conversion_job(client):
    resp = await client.request("DELETE", "/api/conversion/jobs/nope")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_conversion_formats_endpoint(client):
    resp = await client.get("/api/conversion/formats")
    assert resp.status_code == 200
    formats = resp.json()
    assert len(formats) == len(CONVERSION_PATHS)
    # Each entry has required fields
    for f in formats:
        assert "from" in f
        assert "to" in f
        assert "description" in f
        assert "available" in f
    # Paths without capability should be available
    no_cap = [f for f in formats if f["capability"] is None]
    assert all(f["available"] for f in no_cap)
