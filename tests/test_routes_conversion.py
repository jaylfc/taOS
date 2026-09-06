import pytest


class TestConversionRoutes:
    @pytest.mark.asyncio
    async def test_list_conversion_jobs(self, client):
        resp = await client.get("/api/conversion/jobs")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_list_formats(self, client):
        resp = await client.get("/api/conversion/formats")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_create_job_invalid_path(self, client):
        resp = await client.post("/api/conversion/jobs", json={
            "source_model": "test-model",
            "source_format": "invalid",
            "target_format": "also-invalid",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_get_job_not_found(self, client):
        resp = await client.get("/api/conversion/jobs/nonexistent")
        assert resp.status_code == 404
