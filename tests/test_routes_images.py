import base64
import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Request as HttpxRequest, Response

from tinyagentos.app import create_app
from taos_test_csrf import csrf_event_hooks


@pytest.fixture
def images_app(tmp_data_dir, tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    app = create_app(data_dir=tmp_data_dir)
    app.state.data_dir = str(tmp_data_dir)
    # Point images to our tmp dir
    (tmp_data_dir / "images").mkdir(exist_ok=True)
    return app


@pytest_asyncio.fixture
async def images_client(images_app):
    store = images_app.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    await images_app.state.qmd_client.init()
    images_app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _rec = images_app.state.auth.find_user("admin")
    _token = images_app.state.auth.create_session(user_id=_rec["id"] if _rec else "", long_lived=True)
    transport = ASGITransport(app=images_app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"taos_session": _token}, event_hooks=csrf_event_hooks()) as c:
        yield c
    await store.close()
    await images_app.state.qmd_client.close()
    await images_app.state.http_client.aclose()


@pytest.mark.asyncio
class TestImagesGenerate:
    async def test_generate_with_mocked_rkllama(self, images_app, images_client):
        fake_image = base64.b64encode(b"fake-png-data").decode()
        mock_request = HttpxRequest("POST", "http://localhost:8080/v1/images/generations")
        mock_response = Response(
            status_code=200,
            json={"data": [{"b64_json": fake_image}]},
            request=mock_request,
        )

        with patch("tinyagentos.routes.images.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            resp = await images_client.post("/api/images/generate", json={
                "prompt": "a test image",
                "seed": 42,
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "generated"
        assert data["prompt"] == "a test image"
        assert data["seed"] == 42
        assert data["filename"].endswith("_42.png")

        # Verify file was saved
        images_dir = images_app.state.config_path.parent / "workspace" / "images" / "generated"
        saved_files = list(images_dir.glob("*.png"))
        assert len(saved_files) == 1
        assert saved_files[0].read_bytes() == b"fake-png-data"

        # Verify metadata sidecar
        meta_files = list(images_dir.glob("*.json"))
        assert len(meta_files) == 1
        meta = json.loads(meta_files[0].read_text())
        assert meta["prompt"] == "a test image"

    async def test_generate_no_rkllama_backend(self, tmp_data_dir):
        """If no rkllama backend configured, return 503."""
        import yaml
        config_path = tmp_data_dir / "config.yaml"
        config = yaml.safe_load(config_path.read_text())
        config["backends"] = []  # Remove all backends
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
        async with AsyncClient(transport=transport, base_url="http://test", cookies={"taos_session": _token}, event_hooks=csrf_event_hooks()) as c:
            resp = await c.post("/api/images/generate", json={"prompt": "test"})
        assert resp.status_code == 503
        assert "error" in resp.json()
        await store.close()
        await app.state.qmd_client.close()
        await app.state.http_client.aclose()

    async def test_generate_rkllama_connection_error(self, images_app, images_client):
        with patch("tinyagentos.routes.images.httpx.AsyncClient") as MockClient:
            import httpx as real_httpx
            mock_instance = AsyncMock()
            mock_instance.post.side_effect = real_httpx.ConnectError("Connection refused")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            resp = await images_client.post("/api/images/generate", json={
                "prompt": "test",
            })

        assert resp.status_code == 503
        assert "Cannot connect" in resp.json()["error"]

    async def test_notify_task_complete_called_on_generate_success(self, images_app, images_client):
        """notify_task_complete must fire after a successful legacy generate call."""
        from tinyagentos.routes import images as images_mod

        fake_image = base64.b64encode(b"fake-png-data").decode()
        mock_request = HttpxRequest("POST", "http://localhost:8080/v1/images/generations")
        mock_response = Response(
            status_code=200,
            json={"data": [{"b64_json": fake_image}]},
            request=mock_request,
        )

        called: list[str] = []

        class _FakeLifecycle:
            def notify_task_complete(self, name: str) -> None:
                called.append(name)

        images_app.state.lifecycle_manager = _FakeLifecycle()

        # Stub _get_image_backend to return a known (url, type, name) triple.
        def _fake_backend(req, model=None):
            return "http://image.test", "rkllama", "local-test-sd"

        with patch.object(images_mod, "_get_image_backend", _fake_backend), \
             patch("tinyagentos.routes.images.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            resp = await images_client.post("/api/images/generate", json={"prompt": "a dog", "seed": 1})

        assert resp.status_code == 200
        assert called == ["local-test-sd"]

    async def test_notify_task_complete_called_on_generate_failure(self, images_app, images_client):
        """notify_task_complete must fire even when the backend call fails (try/finally)."""
        import httpx as real_httpx
        from tinyagentos.routes import images as images_mod

        called: list[str] = []

        class _FakeLifecycle:
            def notify_task_complete(self, name: str) -> None:
                called.append(name)

        images_app.state.lifecycle_manager = _FakeLifecycle()

        def _fake_backend(req, model=None):
            return "http://image.test", "rkllama", "local-test-sd"

        with patch.object(images_mod, "_get_image_backend", _fake_backend), \
             patch("tinyagentos.routes.images.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post.side_effect = real_httpx.ConnectError("down")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            resp = await images_client.post("/api/images/generate", json={"prompt": "a dog"})

        assert resp.status_code == 503
        # Notify must still fire — idle timer starts even after a failed attempt.
        assert called == ["local-test-sd"]


@pytest.mark.asyncio
class TestImagesList:
    async def test_list_empty(self, images_client):
        resp = await images_client.get("/api/images")
        assert resp.status_code == 200
        data = resp.json()
        assert data["images"] == []

    async def test_list_with_images(self, images_app, images_client):
        images_dir = images_app.state.config_path.parent / "workspace" / "images" / "generated"
        images_dir.mkdir(parents=True, exist_ok=True)
        # Create a fake image + metadata
        (images_dir / "1234_42.png").write_bytes(b"fake-png")
        (images_dir / "1234_42.json").write_text(json.dumps({
            "prompt": "hello", "model": "lcm", "size": "512x512",
            "steps": 4, "seed": 42, "guidance_scale": 7.5,
        }))

        resp = await images_client.get("/api/images")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["images"]) == 1
        assert data["images"][0]["prompt"] == "hello"
        assert data["images"][0]["filename"] == "1234_42.png"


@pytest.mark.asyncio
class TestImagesDelete:
    async def test_delete_nonexistent(self, images_client):
        resp = await images_client.delete("/api/images/nonexistent.png")
        assert resp.status_code == 404

    async def test_delete_image(self, images_app, images_client):
        images_dir = images_app.state.config_path.parent / "workspace" / "images" / "generated"
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / "1234_42.png").write_bytes(b"fake-png")
        (images_dir / "1234_42.json").write_text('{"prompt": "test"}')

        resp = await images_client.delete("/api/images/1234_42.png")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["filename"] == "1234_42.png"

        # Both files should be gone
        assert not (images_dir / "1234_42.png").exists()
        assert not (images_dir / "1234_42.json").exists()

    async def test_delete_path_traversal(self, images_client):
        resp = await images_client.delete("/api/images/..%2F..%2Fetc%2Fpasswd")
        # URL-decoded path with ".." should be rejected
        assert resp.status_code in (400, 404)
