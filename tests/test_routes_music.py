import base64
import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Request as HttpxRequest, Response

from tinyagentos.app import create_app
from taos_test_csrf import csrf_event_hooks


@pytest.fixture
def music_app(tmp_data_dir):
    app = create_app(data_dir=tmp_data_dir)
    app.state.data_dir = str(tmp_data_dir)
    return app


@pytest_asyncio.fixture
async def music_client(music_app):
    store = music_app.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    await music_app.state.qmd_client.init()
    music_app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _rec = music_app.state.auth.find_user("admin")
    _token = music_app.state.auth.create_session(user_id=_rec["id"] if _rec else "", long_lived=True)
    transport = ASGITransport(app=music_app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"taos_session": _token}, event_hooks=csrf_event_hooks()) as c:
        yield c
    await store.close()
    await music_app.state.qmd_client.close()
    await music_app.state.http_client.aclose()


@pytest.mark.asyncio
class TestMusicCompose:
    async def test_compose_no_backend_returns_503(self, music_client):
        with patch("tinyagentos.routes.music.shutil.which", return_value=None):
            resp = await music_client.post("/api/music/compose", json={"prompt": "lofi beat"})
        assert resp.status_code == 503
        assert "error" in resp.json()

    async def test_compose_empty_prompt_returns_400(self, music_app, music_client):
        music_app.state.config.server["music_backend_url"] = "http://localhost:9000"
        resp = await music_client.post("/api/music/compose", json={"prompt": "   "})
        assert resp.status_code == 400

    async def test_compose_with_mocked_http_backend(self, music_app, music_client):
        music_app.state.config.server["music_backend_url"] = "http://localhost:9000"

        fake_wav = base64.b64encode(b"fake-wav-data").decode()
        mock_request = HttpxRequest("POST", "http://localhost:9000/v1/audio/generations")
        mock_response = Response(
            status_code=200,
            json={"data": [{"b64_json": fake_wav}]},
            request=mock_request,
        )

        with (
            patch("tinyagentos.routes.music._http_backend_reachable", new=AsyncMock(return_value=True)),
            patch("tinyagentos.routes.music.httpx.AsyncClient") as MockClient,
        ):
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            resp = await music_client.post("/api/music/compose", json={
                "prompt": "warm lo-fi beat",
                "duration": 8,
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "generated"
        assert data["prompt"] == "warm lo-fi beat"
        assert data["duration"] == 8
        assert data["filename"].endswith(".wav")

        music_dir = music_app.state.config_path.parent / "workspace" / "music" / "generated"
        saved = list(music_dir.glob("*.wav"))
        assert len(saved) == 1
        assert saved[0].read_bytes() == b"fake-wav-data"

        meta_files = list(music_dir.glob("*.json"))
        assert len(meta_files) == 1
        meta = json.loads(meta_files[0].read_text())
        assert meta["prompt"] == "warm lo-fi beat"

    async def test_status_reports_config_backend(self, music_app, music_client):
        music_app.state.config.server["music_backend_url"] = "http://localhost:9000"
        with patch("tinyagentos.routes.music._http_backend_reachable", new=AsyncMock(return_value=True)):
            resp = await music_client.get("/api/music/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert data["mode"] == "http"

    async def test_status_ignores_stable_audio_venv(self, music_app, music_client, tmp_path):
        apps_dir = tmp_path / "apps"
        stable_dir = apps_dir / "stable-audio-open" / "venv" / "bin"
        stable_dir.mkdir(parents=True)
        (stable_dir / "python").write_text("")
        music_app.state.apps_dir = str(apps_dir)

        with patch("tinyagentos.routes.music.shutil.which", return_value=None):
            resp = await music_client.get("/api/music/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "stable-audio-open" not in data["installed"]
        assert data["available"] is False


@pytest.mark.asyncio
class TestMusicList:
    async def test_list_empty(self, music_client):
        resp = await music_client.get("/api/music")
        assert resp.status_code == 200
        assert resp.json()["tracks"] == []