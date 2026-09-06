from __future__ import annotations

"""Route-level tests for /api/youtube/* endpoints."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tinyagentos.routes.youtube import router as youtube_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_item(item_id: str = "yt-item-1", media_path: str | None = None, metadata: dict | None = None) -> dict:
    return {
        "id": item_id,
        "source_url": "https://www.youtube.com/watch?v=test123",
        "source_type": "youtube",
        "title": "Test Video",
        "author": "Test Channel",
        "content": "transcript text",
        "media_path": media_path,
        "metadata": metadata or {
            "video_id": "test123",
            "channel": "Test Channel",
            "transcript_segments": [
                {"start": 0.0, "end": 2.0, "text": "Hello"},
                {"start": 2.0, "end": 4.0, "text": "World"},
            ],
            "chapters": [
                {"title": "Intro", "start_time": 0.0, "end_time": 60.0},
            ],
        },
    }


def _build_test_app(item: dict | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(youtube_router)

    mock_store = AsyncMock()
    mock_store.get_item = AsyncMock(return_value=item)
    mock_store.update_item = AsyncMock(return_value=True)

    mock_pipeline = AsyncMock()
    mock_pipeline.submit_background = AsyncMock(return_value="new-item-id")

    app.state.knowledge_store = mock_store
    app.state.ingest_pipeline = mock_pipeline
    return app


@pytest_asyncio.fixture
async def yt_client():
    app = _build_test_app(item=_make_item())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.app = app
        yield c


@pytest_asyncio.fixture
async def yt_client_no_item():
    app = _build_test_app(item=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.app = app
        yield c


# ---------------------------------------------------------------------------
# POST /api/youtube/ingest
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_returns_pending(yt_client):
    resp = await yt_client.post(
        "/api/youtube/ingest",
        json={"url": "https://www.youtube.com/watch?v=test123"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "new-item-id"
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_ingest_with_title(yt_client):
    resp = await yt_client.post(
        "/api/youtube/ingest",
        json={"url": "https://www.youtube.com/watch?v=test123", "title": "My title"},
    )
    assert resp.status_code == 200
    # Verify pipeline was called with the title
    yt_client.app.state.ingest_pipeline.submit_background.assert_called_once()
    call_kwargs = yt_client.app.state.ingest_pipeline.submit_background.call_args[1]
    assert call_kwargs.get("title") == "My title"


@pytest.mark.asyncio
async def test_ingest_pipeline_error_returns_500(yt_client):
    yt_client.app.state.ingest_pipeline.submit_background = AsyncMock(
        side_effect=Exception("pipeline boom")
    )
    resp = await yt_client.post(
        "/api/youtube/ingest",
        json={"url": "https://www.youtube.com/watch?v=bad"},
    )
    assert resp.status_code == 500
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_ingest_missing_url(yt_client):
    resp = await yt_client.post("/api/youtube/ingest", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/youtube/download
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_unknown_item_returns_404(yt_client_no_item):
    resp = await yt_client_no_item.post(
        "/api/youtube/download",
        json={"item_id": "nonexistent", "quality": "720"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_download_returns_downloading_immediately(yt_client):
    # Patch asyncio.create_task to avoid running the background coroutine in the test loop
    with patch("tinyagentos.routes.youtube.asyncio.create_task") as mock_task:
        mock_task.return_value = MagicMock()
        resp = await yt_client.post(
            "/api/youtube/download",
            json={"item_id": "yt-item-1", "quality": "720"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "downloading"
    assert data["item_id"] == "yt-item-1"
    assert mock_task.called


@pytest.mark.asyncio
async def test_download_item_with_no_source_url_returns_400():
    item_no_url = {
        "id": "yt-item-nurl",
        "source_url": "",
        "source_type": "youtube",
        "title": "No URL",
        "metadata": {},
    }
    app = _build_test_app(item=item_no_url)
    transport = ASGITransport(app=app)
    with patch("tinyagentos.routes.youtube.asyncio.create_task", MagicMock()):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/youtube/download",
                json={"item_id": "yt-item-nurl", "quality": "720"},
            )
    assert resp.status_code == 400
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# GET /api/youtube/download-status/{item_id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_status_idle(yt_client):
    # Reset in-memory status
    import tinyagentos.routes.youtube as yt_routes
    yt_routes._download_status.pop("yt-item-1", None)

    resp = await yt_client.get("/api/youtube/download-status/yt-item-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "idle"


@pytest.mark.asyncio
async def test_download_status_not_found(yt_client_no_item):
    resp = await yt_client_no_item.get("/api/youtube/download-status/bad-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_download_status_complete_with_real_file(yt_client, tmp_path):
    # Create a fake media file
    fake_file = tmp_path / "video.mp4"
    fake_file.write_bytes(b"fake mp4 content")

    # Return item with media_path pointing to real file
    yt_client.app.state.knowledge_store.get_item = AsyncMock(
        return_value={**_make_item(), "media_path": str(fake_file)}
    )

    resp = await yt_client.get("/api/youtube/download-status/yt-item-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "complete"
    assert data["path"] == str(fake_file)
    assert data["file_size"] > 0


# ---------------------------------------------------------------------------
# GET /api/youtube/transcript/{item_id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transcript_returns_segments_and_chapters(yt_client):
    resp = await yt_client.get("/api/youtube/transcript/yt-item-1")
    assert resp.status_code == 200
    data = resp.json()
    assert "segments" in data
    assert "chapters" in data
    assert len(data["segments"]) == 2
    assert data["segments"][0]["text"] == "Hello"
    assert data["segments"][1]["text"] == "World"
    assert len(data["chapters"]) == 1
    assert data["chapters"][0]["title"] == "Intro"


@pytest.mark.asyncio
async def test_transcript_not_found(yt_client_no_item):
    resp = await yt_client_no_item.get("/api/youtube/transcript/bad-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_transcript_empty_metadata(yt_client):
    yt_client.app.state.knowledge_store.get_item = AsyncMock(
        return_value={**_make_item(), "metadata": {}}
    )
    resp = await yt_client.get("/api/youtube/transcript/yt-item-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["segments"] == []
    assert data["chapters"] == []
