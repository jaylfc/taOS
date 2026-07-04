import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Request as HttpxRequest, Response

from tinyagentos.app import create_app


async def _drain_background_tasks(app) -> None:
    """Wait for the video generation background task(s) spawned by the last
    request to finish, so a job status poll immediately after sees the
    terminal state instead of racing the still-running task."""
    pending = [t for t in app.state._background_tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending)


@pytest.fixture
def video_app(tmp_data_dir):
    app = create_app(data_dir=tmp_data_dir)
    app.state.data_dir = str(tmp_data_dir)
    (tmp_data_dir / "videos").mkdir(exist_ok=True)
    return app


@pytest_asyncio.fixture
async def video_client(video_app):
    store = video_app.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    await video_app.state.qmd_client.init()
    video_app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _rec = video_app.state.auth.find_user("admin")
    _token = video_app.state.auth.create_session(user_id=_rec["id"] if _rec else "", long_lived=True)
    transport = ASGITransport(app=video_app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"taos_session": _token}) as c:
        yield c
    await store.close()
    await video_app.state.qmd_client.close()
    await video_app.state.http_client.aclose()


@pytest.mark.asyncio
class TestVideoGenerate:
    async def test_generate_no_backend_returns_503(self, tmp_data_dir):
        """If no video backend is configured, return 503."""
        import yaml
        config_path = tmp_data_dir / "config.yaml"
        config = yaml.safe_load(config_path.read_text())
        config["backends"] = []
        config_path.write_text(yaml.dump(config))

        app = create_app(data_dir=tmp_data_dir)
        app.state.data_dir = str(tmp_data_dir)
        store = app.state.metrics
        if store._db is not None:
            await store.close()
        await store.init()
        await app.state.qmd_client.init()
        app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
        _rec = app.state.auth.find_user("admin")
        _token = app.state.auth.create_session(user_id=_rec["id"] if _rec else "", long_lived=True)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", cookies={"taos_session": _token}) as c:
            resp = await c.post("/api/video/generate", json={"prompt": "test"})
        assert resp.status_code == 503
        assert "error" in resp.json()
        await store.close()
        await app.state.qmd_client.close()
        await app.state.http_client.aclose()

    async def test_generate_returns_job_id_then_completes(self, video_app, video_client):
        """Generate enqueues a job (202 + job_id) and, once the background
        task finishes, the job's status flips to done with the saved video."""
        import base64

        # Set video_backend_url in config
        video_app.state.config.server["video_backend_url"] = "http://localhost:9000"

        fake_mp4 = base64.b64encode(b"fake-mp4-data").decode()
        mock_request = HttpxRequest("POST", "http://localhost:9000/v1/videos/generations")
        mock_response = Response(
            status_code=200,
            json={"data": [{"b64_json": fake_mp4}]},
            request=mock_request,
        )

        with patch("tinyagentos.routes.video.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            resp = await video_client.post("/api/video/generate", json={
                "prompt": "a test video",
                "seed": 99,
            })
            assert resp.status_code == 202
            data = resp.json()
            assert data["status"] == "queued"
            job_id = data["job_id"]
            assert job_id

            await _drain_background_tasks(video_app)

        status_resp = await video_client.get(f"/api/video/jobs/{job_id}")
        assert status_resp.status_code == 200
        status = status_resp.json()
        assert status["status"] == "done"
        result = status["result"]
        assert result["prompt"] == "a test video"
        assert result["seed"] == 99
        assert result["filename"].endswith("_99.mp4")

        # Verify file was saved
        videos_dir = video_app.state.config.config_path.parent / "videos"
        saved_files = list(videos_dir.glob("*.mp4"))
        assert len(saved_files) == 1
        assert saved_files[0].read_bytes() == b"fake-mp4-data"

        # Verify metadata sidecar
        meta_files = list(videos_dir.glob("*.json"))
        assert len(meta_files) == 1
        meta = json.loads(meta_files[0].read_text())
        assert meta["prompt"] == "a test video"

        # Cleanup config mutation
        del video_app.state.config.server["video_backend_url"]

    async def test_generate_connection_error(self, video_app, video_client):
        video_app.state.config.server["video_backend_url"] = "http://localhost:9000"

        with patch("tinyagentos.routes.video.httpx.AsyncClient") as MockClient:
            import httpx as real_httpx
            mock_instance = AsyncMock()
            mock_instance.post.side_effect = real_httpx.ConnectError("Connection refused")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            resp = await video_client.post("/api/video/generate", json={"prompt": "test"})
            assert resp.status_code == 202
            job_id = resp.json()["job_id"]

            await _drain_background_tasks(video_app)

        status_resp = await video_client.get(f"/api/video/jobs/{job_id}")
        status = status_resp.json()
        assert status["status"] == "error"
        assert "Cannot connect" in status["error"]

        del video_app.state.config.server["video_backend_url"]

    async def test_generate_timeout_error(self, video_app, video_client):
        video_app.state.config.server["video_backend_url"] = "http://localhost:9000"

        with patch("tinyagentos.routes.video.httpx.AsyncClient") as MockClient:
            import httpx as real_httpx
            mock_instance = AsyncMock()
            mock_instance.post.side_effect = real_httpx.TimeoutException("Timeout")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            resp = await video_client.post("/api/video/generate", json={"prompt": "test"})
            assert resp.status_code == 202
            job_id = resp.json()["job_id"]

            await _drain_background_tasks(video_app)

        status_resp = await video_client.get(f"/api/video/jobs/{job_id}")
        status = status_resp.json()
        assert status["status"] == "error"
        assert "timed out" in status["error"]

        del video_app.state.config.server["video_backend_url"]

    async def test_generate_bad_response_format(self, video_app, video_client):
        video_app.state.config.server["video_backend_url"] = "http://localhost:9000"

        mock_request = HttpxRequest("POST", "http://localhost:9000/v1/videos/generations")
        mock_response = Response(
            status_code=200,
            json={"unexpected": "format"},
            request=mock_request,
        )

        with patch("tinyagentos.routes.video.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            resp = await video_client.post("/api/video/generate", json={"prompt": "test"})
            assert resp.status_code == 202
            job_id = resp.json()["job_id"]

            await _drain_background_tasks(video_app)

        status_resp = await video_client.get(f"/api/video/jobs/{job_id}")
        status = status_resp.json()
        assert status["status"] == "error"
        assert "Unexpected response format" in status["error"]

        del video_app.state.config.server["video_backend_url"]


    async def test_generate_unexpected_exception_marks_job_error(self, video_app, video_client):
        """An unexpected (non-httpx) backend exception still ends the job in a
        terminal 'error' state -- never stuck in 'running'."""
        video_app.state.config.server["video_backend_url"] = "http://localhost:9000"

        with patch("tinyagentos.routes.video.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post.side_effect = RuntimeError("boom")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            resp = await video_client.post("/api/video/generate", json={"prompt": "test"})
            assert resp.status_code == 202
            job_id = resp.json()["job_id"]

            await _drain_background_tasks(video_app)

        status_resp = await video_client.get(f"/api/video/jobs/{job_id}")
        status = status_resp.json()
        assert status["status"] == "error"
        assert "boom" in status["error"]

        del video_app.state.config.server["video_backend_url"]


@pytest.mark.asyncio
class TestVideoJobStatus:
    async def test_job_not_found_returns_404(self, video_client):
        resp = await video_client.get("/api/video/jobs/does-not-exist")
        assert resp.status_code == 404
        assert "error" in resp.json()

    async def test_rapid_jobs_get_distinct_ids(self, video_app, video_client):
        """Two jobs created back-to-back must get distinct (full-length) ids --
        a truncated id would risk a PRIMARY KEY collision."""
        from tinyagentos.routes.video import _get_video_job_store

        job_store = await _get_video_job_store(video_app)
        id1 = await job_store.create_job()
        id2 = await job_store.create_job()
        assert id1 != id2
        # Full uuid4 hex is 32 chars -- not truncated.
        assert len(id1) == 32
        assert len(id2) == 32


@pytest.mark.asyncio
class TestVideoList:
    async def test_list_empty(self, video_client):
        resp = await video_client.get("/api/video")
        assert resp.status_code == 200
        data = resp.json()
        assert data["videos"] == []

    async def test_list_with_videos(self, video_app, video_client):
        videos_dir = video_app.state.config.config_path.parent / "videos"
        videos_dir.mkdir(exist_ok=True)
        (videos_dir / "1234_42.mp4").write_bytes(b"fake-mp4")
        (videos_dir / "1234_42.json").write_text(json.dumps({
            "prompt": "hello world", "model": "wan2.1-1.3b",
            "duration": 5, "resolution": "480x832", "seed": 42,
        }))

        resp = await video_client.get("/api/video")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["videos"]) == 1
        assert data["videos"][0]["prompt"] == "hello world"
        assert data["videos"][0]["filename"] == "1234_42.mp4"
        assert data["videos"][0]["seed"] == 42


@pytest.mark.asyncio
class TestVideoDelete:
    async def test_delete_nonexistent(self, video_client):
        resp = await video_client.delete("/api/video/nonexistent.mp4")
        assert resp.status_code == 404

    async def test_delete_video(self, video_app, video_client):
        videos_dir = video_app.state.config.config_path.parent / "videos"
        videos_dir.mkdir(exist_ok=True)
        (videos_dir / "1234_42.mp4").write_bytes(b"fake-mp4")
        (videos_dir / "1234_42.json").write_text('{"prompt": "test"}')

        resp = await video_client.delete("/api/video/1234_42.mp4")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["filename"] == "1234_42.mp4"

        assert not (videos_dir / "1234_42.mp4").exists()
        assert not (videos_dir / "1234_42.json").exists()

    async def test_delete_path_traversal(self, video_client):
        resp = await video_client.delete("/api/video/..%2F..%2Fetc%2Fpasswd")
        assert resp.status_code in (400, 404)

    async def test_delete_invalid_filename_backslash(self, video_client):
        resp = await video_client.delete("/api/video/foo%5Cbar.mp4")
        assert resp.status_code in (400, 404)
