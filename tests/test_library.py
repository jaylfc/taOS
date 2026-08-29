"""Tests for the Library app — store, pipeline, routes, and collections handoff."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from tinyagentos.library_pipeline import (
    FileProcessor,
    HeavyDownloadProcessor,
    ImageProcessor,
    PdfProcessor,
    TextProcessor,
    WebProcessor,
    YouTubeProcessor,
    detect_kind,
    run_pipeline,
)
from tinyagentos.library_store import LibraryStore
from tinyagentos.library_collections import handoff_to_collections


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def lib_store():
    """Create a LibraryStore backed by a temporary SQLite database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    store = LibraryStore(db_path)
    await store.init()

    yield store

    await store.close()
    try:
        db_path.unlink()
    except OSError:
        pass


@pytest.fixture
def storage_dir():
    """Create a temporary directory for file artifacts."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# ---------------------------------------------------------------------------
# Kind detection
# ---------------------------------------------------------------------------


class TestKindDetection:
    def test_detect_youtube_url(self):
        assert detect_kind(source_url="https://www.youtube.com/watch?v=abc123") == "url:youtube"
        assert detect_kind(source_url="https://youtube.com/watch?v=abc123") == "url:youtube"
        assert detect_kind(source_url="https://youtu.be/abc123") == "url:youtube"
        assert detect_kind(source_url="https://m.youtube.com/watch?v=abc123") == "url:youtube"

    def test_detect_web_url(self):
        assert detect_kind(source_url="https://example.com") == "url:web"
        assert detect_kind(source_url="http://blog.example.com/post") == "url:web"

    def test_detect_by_mime(self):
        assert detect_kind(content_type="text/plain") == "text"
        assert detect_kind(content_type="application/pdf") == "pdf"
        assert detect_kind(content_type="image/png") == "image"
        assert detect_kind(content_type="image/jpeg") == "image"
        assert detect_kind(content_type="application/zip") == "archive"

    def test_detect_by_filename(self):
        assert detect_kind(file_path="doc.txt") == "text"
        assert detect_kind(file_path="report.pdf") == "pdf"
        assert detect_kind(file_path="photo.jpg") == "image"
        assert detect_kind(file_path="icon.png") == "image"

    def test_detect_fallback(self):
        assert detect_kind(file_path="unknown.xyz") == "file"
        assert detect_kind() == "file"


# ---------------------------------------------------------------------------
# LibraryStore
# ---------------------------------------------------------------------------


class TestLibraryStore:
    @pytest.mark.asyncio
    async def test_create_and_get_item(self, lib_store):
        item_id = await lib_store.create_item(
            kind="text",
            title="test.txt",
            source_url="",
            storage_path="/tmp/test.txt",
            size_bytes=42,
        )
        assert item_id

        item = await lib_store.get_item(item_id)
        assert item is not None
        assert item["kind"] == "text"
        assert item["title"] == "test.txt"
        assert item["status"] == "pending"
        assert item["bytes"] == 42

    @pytest.mark.asyncio
    async def test_get_nonexistent_item(self, lib_store):
        item = await lib_store.get_item("nonexistent")
        assert item is None

    @pytest.mark.asyncio
    async def test_list_items(self, lib_store):
        id1 = await lib_store.create_item(kind="text", title="a.txt")
        id2 = await lib_store.create_item(kind="pdf", title="b.pdf")
        id3 = await lib_store.create_item(kind="image", title="c.png")

        items = await lib_store.list_items()
        assert len(items) == 3

        text_items = await lib_store.list_items(kind="text")
        assert len(text_items) == 1
        assert text_items[0]["title"] == "a.txt"

        assert len(await lib_store.list_items(status="pending")) == 3
        assert len(await lib_store.list_items(status="ready")) == 0

    @pytest.mark.asyncio
    async def test_update_item(self, lib_store):
        item_id = await lib_store.create_item(kind="text", title="old")
        await lib_store.update_item(item_id, title="new", status="ready")

        item = await lib_store.get_item(item_id)
        assert item["title"] == "new"
        assert item["status"] == "ready"

    @pytest.mark.asyncio
    async def test_update_item_meta(self, lib_store):
        item_id = await lib_store.create_item(kind="text", title="test")
        await lib_store.update_item(item_id, meta_json={"preview": "hello"})

        item = await lib_store.get_item(item_id)
        meta = json.loads(item["meta_json"])
        assert meta["preview"] == "hello"

    @pytest.mark.asyncio
    async def test_update_invalid_status(self, lib_store):
        item_id = await lib_store.create_item(kind="text", title="test")
        with pytest.raises(ValueError):
            await lib_store.update_item_status(item_id, "invalid_status")

    @pytest.mark.asyncio
    async def test_delete_item(self, lib_store):
        item_id = await lib_store.create_item(kind="text", title="test")
        item = await lib_store.get_item(item_id)
        assert item is not None

        await lib_store.delete_item(item_id)
        item = await lib_store.get_item(item_id)
        assert item is None

    @pytest.mark.asyncio
    async def test_artifacts(self, lib_store):
        item_id = await lib_store.create_item(kind="text", title="test")
        art_id = await lib_store.add_artifact(item_id, kind="text", path="/tmp/test.txt")

        artifacts = await lib_store.get_artifacts(item_id)
        assert len(artifacts) == 1
        assert artifacts[0]["kind"] == "text"

        await lib_store.delete_artifact(art_id)
        assert len(await lib_store.get_artifacts(item_id)) == 0

    @pytest.mark.asyncio
    async def test_cascade_delete_artifacts(self, lib_store):
        item_id = await lib_store.create_item(kind="text", title="test")
        await lib_store.add_artifact(item_id, kind="text", path="/tmp/a.txt")
        await lib_store.add_artifact(item_id, kind="thumbnail", path="/tmp/thumb.jpg")

        await lib_store.delete_item(item_id)
        assert len(await lib_store.get_artifacts(item_id)) == 0

    @pytest.mark.asyncio
    async def test_jobs(self, lib_store):
        item_id = await lib_store.create_item(kind="text", title="test")
        job_id = await lib_store.create_job(item_id, "ingest")

        job = await lib_store.get_job(job_id)
        assert job is not None
        assert job["stage"] == "ingest"
        assert job["state"] == "queued"

        await lib_store.update_job(job_id, state="done")
        job = await lib_store.get_job(job_id)
        assert job["state"] == "done"


# ---------------------------------------------------------------------------
# Pipeline processors
# ---------------------------------------------------------------------------


class TestFileProcessor:
    @pytest.mark.asyncio
    async def test_process_existing_file(self, lib_store, storage_dir):
        file_path = storage_dir / "test.txt"
        file_path.write_text("hello world")

        item_id = await lib_store.create_item(
            kind="file", title="test.txt", storage_path=str(file_path)
        )
        item = await lib_store.get_item(item_id)

        proc = FileProcessor(lib_store, storage_dir)
        artifacts = await proc.process(item)
        assert len(artifacts) == 1
        assert artifacts[0]["kind"] == "metadata"

    @pytest.mark.asyncio
    async def test_process_missing_file(self, lib_store, storage_dir):
        item_id = await lib_store.create_item(
            kind="file", title="missing.txt", storage_path="/nonexistent/file.txt"
        )
        item = await lib_store.get_item(item_id)

        proc = FileProcessor(lib_store, storage_dir)
        artifacts = await proc.process(item)
        assert len(artifacts) == 0


class TestTextProcessor:
    @pytest.mark.asyncio
    async def test_extract_text(self, lib_store, storage_dir):
        file_path = storage_dir / "notes.txt"
        file_path.write_text("line one\nline two\nline three")

        item_id = await lib_store.create_item(
            kind="text", title="notes.txt", storage_path=str(file_path)
        )
        item = await lib_store.get_item(item_id)

        proc = TextProcessor(lib_store, storage_dir)
        artifacts = await proc.process(item)
        assert len(artifacts) == 1
        assert artifacts[0]["kind"] == "text"
        assert artifacts[0]["meta"]["line_count"] == 3
        assert artifacts[0]["meta"]["char_count"] == 28

        text_path = Path(artifacts[0]["path"])
        assert text_path.exists()
        assert "line one" in text_path.read_text()

    @pytest.mark.asyncio
    async def test_text_auto_title(self, lib_store, storage_dir):
        file_path = storage_dir / "notes.txt"
        file_path.write_text("My Title\nmore content here")

        item_id = await lib_store.create_item(
            kind="text", title="", storage_path=str(file_path)
        )
        item = await lib_store.get_item(item_id)

        proc = TextProcessor(lib_store, storage_dir)
        await proc.process(item)

        updated = await lib_store.get_item(item_id)
        assert updated["title"] == "My Title"

    @pytest.mark.asyncio
    async def test_text_preview(self, lib_store, storage_dir):
        file_path = storage_dir / "long.txt"
        file_path.write_text("A" * 500)

        item_id = await lib_store.create_item(
            kind="text", title="long.txt", storage_path=str(file_path)
        )
        item = await lib_store.get_item(item_id)

        proc = TextProcessor(lib_store, storage_dir)
        await proc.process(item)

        updated = await lib_store.get_item(item_id)
        meta = json.loads(updated["meta_json"])
        assert "preview" in meta
        assert len(meta["preview"]) == 200


class TestPdfProcessor:
    @pytest.mark.asyncio
    async def test_process_pdf(self, lib_store, storage_dir):
        file_path = storage_dir / "test.pdf"
        _create_minimal_pdf(file_path)

        item_id = await lib_store.create_item(
            kind="pdf", title="test.pdf", storage_path=str(file_path)
        )
        item = await lib_store.get_item(item_id)

        proc = PdfProcessor(lib_store, storage_dir)
        artifacts = await proc.process(item)

        meta_artifacts = [a for a in artifacts if a["kind"] == "metadata"]
        assert len(meta_artifacts) >= 1
        assert meta_artifacts[0]["meta"]["page_count"] >= 0

    @pytest.mark.asyncio
    async def test_process_missing_pdf(self, lib_store, storage_dir):
        item_id = await lib_store.create_item(
            kind="pdf", title="missing.pdf", storage_path="/nonexistent/file.pdf"
        )
        item = await lib_store.get_item(item_id)

        proc = PdfProcessor(lib_store, storage_dir)
        artifacts = await proc.process(item)
        assert len(artifacts) == 0


class TestImageProcessor:
    @pytest.mark.asyncio
    async def test_process_image(self, lib_store, storage_dir):
        file_path = storage_dir / "test.png"
        _create_test_image(file_path)

        item_id = await lib_store.create_item(
            kind="image", title="test.png", storage_path=str(file_path)
        )
        item = await lib_store.get_item(item_id)

        proc = ImageProcessor(lib_store, storage_dir)
        artifacts = await proc.process(item)

        kinds = {a["kind"] for a in artifacts}
        assert "metadata" in kinds
        assert "thumbnail" in kinds

        thumb_art = [a for a in artifacts if a["kind"] == "thumbnail"][0]
        assert Path(thumb_art["path"]).exists()

    @pytest.mark.asyncio
    async def test_process_missing_image(self, lib_store, storage_dir):
        item_id = await lib_store.create_item(
            kind="image", title="missing.png", storage_path="/nonexistent/file.png"
        )
        item = await lib_store.get_item(item_id)

        proc = ImageProcessor(lib_store, storage_dir)
        artifacts = await proc.process(item)
        assert len(artifacts) == 0


# ---------------------------------------------------------------------------
# run_pipeline
# ---------------------------------------------------------------------------


class TestRunPipeline:
    @pytest.mark.asyncio
    async def test_pipeline_text(self, lib_store, storage_dir):
        file_path = storage_dir / "notes.txt"
        file_path.write_text("sample content")

        item_id = await lib_store.create_item(
            kind="text", title="notes.txt", storage_path=str(file_path)
        )
        await run_pipeline(lib_store, item_id, storage_dir)

        item = await lib_store.get_item(item_id)
        assert item["status"] == "ready"

        artifacts = await lib_store.get_artifacts(item_id)
        artifact_kinds = {a["kind"] for a in artifacts}
        assert "metadata" in artifact_kinds
        assert "text" in artifact_kinds

    @pytest.mark.asyncio
    async def test_pipeline_file(self, lib_store, storage_dir):
        file_path = storage_dir / "unknown.xyz"
        file_path.write_text("raw data")

        item_id = await lib_store.create_item(
            kind="file", title="unknown.xyz", storage_path=str(file_path)
        )
        await run_pipeline(lib_store, item_id, storage_dir)

        item = await lib_store.get_item(item_id)
        assert item["status"] == "ready"

    @pytest.mark.asyncio
    async def test_pipeline_error_status(self, lib_store, storage_dir):
        item_id = await lib_store.create_item(
            kind="file", title="missing.txt", storage_path="/missing/file.txt"
        )
        await run_pipeline(lib_store, item_id, storage_dir)
        item = await lib_store.get_item(item_id)
        assert item["status"] == "error"


# ---------------------------------------------------------------------------
# YouTube processor
# ---------------------------------------------------------------------------


class TestYouTubeProcessor:
    @pytest.mark.asyncio
    async def test_process_youtube_url(self, lib_store, storage_dir):
        """YouTube processor fetches metadata, transcript, chapters."""
        item_id = await lib_store.create_item(
            kind="url:youtube",
            source_url="https://youtube.com/watch?v=test123",
        )
        item = await lib_store.get_item(item_id)

        mock_result = {
            "title": "Test Video",
            "author": "TestChannel",
            "content": "This is a transcript.",
            "thumbnail": None,
            "metadata": {
                "video_id": "test123",
                "channel": "TestChannel",
                "duration": 120,
                "views": 1000,
                "upload_date": "20260101",
                "chapters": [
                    {"start_time": 0, "title": "Intro"},
                    {"start_time": 60, "title": "Main"},
                ],
            },
        }

        from unittest.mock import patch
        with patch(
            "tinyagentos.knowledge_fetchers.youtube.fetch",
            _async_return(mock_result),
        ):
            proc = YouTubeProcessor(lib_store, storage_dir)
            artifacts = await proc.process(item)

        kinds = {a["kind"] for a in artifacts}
        assert "metadata" in kinds
        assert "transcript" in kinds
        assert "chapters" in kinds

        updated = await lib_store.get_item(item_id)
        meta = json.loads(updated["meta_json"])
        assert meta["video_id"] == "test123"
        assert meta["channel"] == "TestChannel"

    @pytest.mark.asyncio
    async def test_process_youtube_url_no_source(self, lib_store, storage_dir):
        """YouTube processor returns empty when source_url is missing."""
        item_id = await lib_store.create_item(
            kind="url:youtube", source_url="", title="",
        )
        item = await lib_store.get_item(item_id)

        proc = YouTubeProcessor(lib_store, storage_dir)
        artifacts = await proc.process(item)
        assert artifacts == []

    @pytest.mark.asyncio
    async def test_youtube_pipeline_integration(self, lib_store, storage_dir):
        """run_pipeline routes url:youtube items to YouTubeProcessor."""
        item_id = await lib_store.create_item(
            kind="url:youtube",
            source_url="https://youtube.com/watch?v=pipeline-test",
        )

        mock_result = {
            "title": "Pipeline Video",
            "author": "PipelineChannel",
            "content": "Pipeline transcript.",
            "thumbnail": None,
            "metadata": {
                "video_id": "pipeline-test",
                "channel": "PipelineChannel",
            },
        }

        from unittest.mock import patch
        with patch(
            "tinyagentos.knowledge_fetchers.youtube.fetch",
            _async_return(mock_result),
        ):
            await run_pipeline(lib_store, item_id, storage_dir)

        item = await lib_store.get_item(item_id)
        assert item["status"] == "ready"

        artifacts = await lib_store.get_artifacts(item_id)
        artifact_kinds = {a["kind"] for a in artifacts}
        assert "metadata" in artifact_kinds
        assert "transcript" in artifact_kinds


# ---------------------------------------------------------------------------
# Web processor
# ---------------------------------------------------------------------------


class TestWebProcessor:
    @pytest.mark.asyncio
    async def test_process_web_url(self, lib_store, storage_dir):
        """Web processor fetches HTML, extracts text, stores artifacts."""
        html = (
            "<html><head><title>Test Page</title></head>"
            "<body><article><p>This is the main article text "
            "with enough content to pass the readability minimum "
            "character count for extraction purposes now.</p>"
            "<p>Second paragraph with more detailed content about "
            "the topic being discussed on this test page.</p></article></body>"
            "</html>"
        )

        item_id = await lib_store.create_item(
            kind="url:web",
            source_url="https://example.com/article",
            title="",
        )
        item = await lib_store.get_item(item_id)

        proc = WebProcessor(lib_store, storage_dir)

        mock_resp = _mock_httpx_response(html, 200)
        from unittest.mock import patch
        with (
            patch("httpx.AsyncClient") as mock_client_cls,
            patch(
                "tinyagentos.routes.desktop_browser.ssrf.validate_url_or_raise",
            ),
        ):
            mock_client_cls.return_value.__aenter__.return_value.stream = (
                _mock_stream_callable(mock_resp)
            )
            artifacts = await proc.process(item)

        kinds = {a["kind"] for a in artifacts}
        assert "metadata" in kinds
        assert "text" in kinds

        text_artifacts = [a for a in artifacts if a["kind"] == "text"]
        assert len(text_artifacts) == 1
        text_path = Path(text_artifacts[0]["path"])
        assert text_path.exists()

        updated = await lib_store.get_item(item_id)
        meta = json.loads(updated["meta_json"])
        assert "preview" in meta

    @pytest.mark.asyncio
    async def test_process_web_url_no_source(self, lib_store, storage_dir):
        """Web processor returns empty when source_url is missing."""
        item_id = await lib_store.create_item(
            kind="url:web", source_url="", title="",
        )
        item = await lib_store.get_item(item_id)

        proc = WebProcessor(lib_store, storage_dir)
        artifacts = await proc.process(item)
        assert artifacts == []

    @pytest.mark.asyncio
    async def test_web_extracts_title_from_html(self, lib_store, storage_dir):
        """Web processor auto-titles from <title> tag when item has no title."""
        html = (
            "<html><head><title>Auto Title Here</title></head>"
            "<body><article><p>This article has enough text content "
            "to ensure that the readability extractor returns a full "
            "result instead of falling back to the simple tag stripper "
            "which would otherwise happen for very short pages.</p></article>"
            "</body></html>"
        )

        item_id = await lib_store.create_item(
            kind="url:web",
            source_url="https://example.com/titled",
            title="https://example.com/titled",
        )
        item = await lib_store.get_item(item_id)

        proc = WebProcessor(lib_store, storage_dir)

        mock_resp = _mock_httpx_response(html, 200)
        from unittest.mock import patch
        with (
            patch("httpx.AsyncClient") as mock_client_cls,
            patch(
                "tinyagentos.routes.desktop_browser.ssrf.validate_url_or_raise",
            ),
        ):
            mock_client_cls.return_value.__aenter__.return_value.stream = (
                _mock_stream_callable(mock_resp)
            )
            await proc.process(item)

        updated = await lib_store.get_item(item_id)
        assert "Auto Title Here" in updated["title"]

    @pytest.mark.asyncio
    async def test_web_pipeline_integration(self, lib_store, storage_dir):
        """run_pipeline routes url:web items to WebProcessor."""
        html = (
            "<html><head><title>Pipeline Web</title></head>"
            "<body><p>Pipeline test content that is sufficiently "
            "long to pass the readability minimum character count."
            "</p></body></html>"
        )

        item_id = await lib_store.create_item(
            kind="url:web",
            source_url="https://example.com/pipeline-web",
        )

        mock_resp = _mock_httpx_response(html, 200)
        from unittest.mock import patch
        with (
            patch("httpx.AsyncClient") as mock_client_cls,
            patch(
                "tinyagentos.routes.desktop_browser.ssrf.validate_url_or_raise",
            ),
        ):
            mock_client_cls.return_value.__aenter__.return_value.stream = (
                _mock_stream_callable(mock_resp)
            )
            await run_pipeline(lib_store, item_id, storage_dir)

        item = await lib_store.get_item(item_id)
        assert item["status"] == "ready"

        artifacts = await lib_store.get_artifacts(item_id)
        artifact_kinds = {a["kind"] for a in artifacts}
        assert "metadata" in artifact_kinds
        assert "text" in artifact_kinds

    # -- SSRF, size cap, content-type, and redirect tests — these test
    #    actual guard paths rather than patching them out.

    @pytest.mark.asyncio
    async def test_web_ssrf_block_loopback(self, lib_store, storage_dir):
        """validate_url_or_raise blocks loopback addresses."""
        from tinyagentos.routes.desktop_browser.ssrf import (
            SsrfBlockedError,
            validate_url_or_raise,
        )
        import pytest as _pytest
        with _pytest.raises(SsrfBlockedError):
            validate_url_or_raise("http://127.0.0.1:8080/")

    @pytest.mark.asyncio
    async def test_web_ssrf_block_private(self, lib_store, storage_dir):
        """validate_url_or_raise blocks RFC1918 private addresses."""
        from tinyagentos.routes.desktop_browser.ssrf import (
            SsrfBlockedError,
            validate_url_or_raise,
        )
        import pytest as _pytest
        with _pytest.raises(SsrfBlockedError):
            validate_url_or_raise("http://10.0.0.1/admin")

    @pytest.mark.asyncio
    async def test_web_redirect_hop_validated(self, lib_store, storage_dir):
        """Each redirect hop passes through validate_url_or_raise."""
        from tinyagentos.routes.desktop_browser.ssrf import (
            SsrfBlockedError,
            validate_url_or_raise,
        )
        from unittest.mock import patch, MagicMock, AsyncMock
        import pytest as _pytest

        html = "<html><p>Redirected content that is long enough for readability extraction now.</p></html>"
        item_id = await lib_store.create_item(
            kind="url:web",
            source_url="https://safe.example.com/page",
        )
        item = await lib_store.get_item(item_id)
        proc = WebProcessor(lib_store, storage_dir)

        # Simulate one redirect: safe → safe
        mock_resp1 = MagicMock()
        mock_resp1.status_code = 302
        mock_resp1.headers = {"location": "https://safe.example.com/real", "content-type": "text/html"}
        mock_resp1.is_redirect = True

        mock_resp2 = _mock_httpx_response(html, 200)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.stream = MagicMock(
            side_effect=[_mock_stream_ctx(mock_resp1), _mock_stream_ctx(mock_resp2)]
        )

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(
                "tinyagentos.routes.desktop_browser.ssrf.validate_url_or_raise",
            ) as mock_validate,
        ):
            artifacts = await proc.process(item)

        # Both hops should have been validated
        assert mock_validate.call_count == 2

    @pytest.mark.asyncio
    async def test_web_size_cap(self, lib_store, storage_dir):
        """Responses exceeding the size cap raise ValueError."""
        from unittest.mock import patch, MagicMock
        import pytest as _pytest

        html = "x" * 1000  # small content
        item_id = await lib_store.create_item(
            kind="url:web",
            source_url="https://example.com/huge",
        )
        item = await lib_store.get_item(item_id)
        proc = WebProcessor(lib_store, storage_dir)

        # Override cap to a tiny value for test speed
        proc._MAX_WEB_BYTES = 100

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
        mock_resp.encoding = "utf-8"

        async def _aiter_bytes_oversized(_chunk_size=8192):
            yield b"x" * 200

        mock_resp.aiter_bytes = _aiter_bytes_oversized

        with (
            patch("httpx.AsyncClient") as mock_client_cls,
            patch(
                "tinyagentos.routes.desktop_browser.ssrf.validate_url_or_raise",
            ),
        ):
            mock_client_cls.return_value.__aenter__.return_value.stream = (
                _mock_stream_callable(mock_resp)
            )
            with _pytest.raises(ValueError, match="exceeds"):
                await proc.process(item)

    @pytest.mark.asyncio
    async def test_web_content_type_gate(self, lib_store, storage_dir):
        """Non-text/* content types raise ValueError."""
        from unittest.mock import patch, MagicMock
        import pytest as _pytest

        item_id = await lib_store.create_item(
            kind="url:web",
            source_url="https://example.com/video.mp4",
        )
        item = await lib_store.get_item(item_id)
        proc = WebProcessor(lib_store, storage_dir)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "video/mp4"}

        with (
            patch("httpx.AsyncClient") as mock_client_cls,
            patch(
                "tinyagentos.routes.desktop_browser.ssrf.validate_url_or_raise",
            ),
        ):
            mock_client_cls.return_value.__aenter__.return_value.stream = (
                _mock_stream_callable(mock_resp)
            )
            with _pytest.raises(ValueError, match="Non-text content-type"):
                await proc.process(item)


# ---------------------------------------------------------------------------
# Collections handoff
# ---------------------------------------------------------------------------


class TestCollectionsHandoff:
    @pytest.mark.asyncio
    async def test_handoff_files_copied(self, lib_store, storage_dir):
        """Text artifacts are copied to collections dir even without qmd;
        returns 0 when qmd is unreachable (no silent ImportError success)."""
        file_path = storage_dir / "notes.txt"
        file_path.write_text("content for collections")

        item_id = await lib_store.create_item(
            kind="text", title="notes.txt", storage_path=str(file_path)
        )
        item = await lib_store.get_item(item_id)

        proc = TextProcessor(lib_store, storage_dir)
        await proc.process(item)

        collections_dir = storage_dir / "collections"
        count = await handoff_to_collections(lib_store, item_id, collections_dir)

        # Files copied but no qmd → 0 indexed
        assert count == 0

        # Verify files were still copied to collections dir
        item_dir = collections_dir / item_id
        assert item_dir.exists()
        text_files = list(item_dir.glob("*.txt"))
        assert len(text_files) >= 1
        assert "content for collections" in text_files[0].read_text()

    @pytest.mark.asyncio
    async def test_handoff_with_qmd(self, lib_store, storage_dir):
        """Handoff indexes into taosmd when the API is reachable."""
        from unittest.mock import AsyncMock, patch

        file_path = storage_dir / "notes.txt"
        file_path.write_text("hello from library")

        item_id = await lib_store.create_item(
            kind="text", title="notes.txt", storage_path=str(file_path)
        )
        item = await lib_store.get_item(item_id)

        proc = TextProcessor(lib_store, storage_dir)
        await proc.process(item)

        collections_dir = storage_dir / "collections"
        taosmd_url = "http://localhost:17900"
        taosmd_token = "test-admin-token"

        # Mock httpx.AsyncClient to simulate a working taosmd
        from unittest.mock import AsyncMock, MagicMock

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        # POST /collections → nested response shape (taosmd 0.4.0)
        mock_create_resp = MagicMock()
        mock_create_resp.status_code = 200
        mock_create_resp.json.return_value = {"collection": {"id": "coll-123"}}

        # POST /collections/{id}/index → 202 (no body)
        mock_index_resp = MagicMock()
        mock_index_resp.status_code = 202

        # POST /collections/{id}/link → 200
        mock_link_resp = MagicMock()
        mock_link_resp.status_code = 200

        # GET /collections/{id} → nested shape (taosmd 0.4.0), status=ready with stats
        mock_poll_resp = MagicMock()
        mock_poll_resp.status_code = 200
        mock_poll_resp.json.return_value = {
            "collection": {
                "status": "ready",
                "stats": {
                    "files_indexed": 1,
                    "files_total": 1,
                    "chunks_ingested": 3,
                    "chunks_skipped": 0,
                },
            },
        }

        mock_client.post = AsyncMock(
            side_effect=[mock_create_resp, mock_index_resp, mock_link_resp]
        )
        mock_client.get = AsyncMock(return_value=mock_poll_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            count = await handoff_to_collections(
                lib_store, item_id, collections_dir,
                taosmd_url=taosmd_url,
                taosmd_admin_token=taosmd_token,
            )

        assert count == 1  # One file indexed per stats

        # Verify request sequence — create → index → link
        assert mock_client.post.call_count == 3
        post_calls = [c.args[0] for c in mock_client.post.call_args_list]
        assert post_calls[0] == f"{taosmd_url}/collections"
        assert post_calls[1] == f"{taosmd_url}/collections/coll-123/index"
        assert post_calls[2] == f"{taosmd_url}/collections/coll-123/link"

        # Verify POST /collections body contains required fields
        create_kwargs = mock_client.post.call_args_list[0].kwargs
        create_body = create_kwargs.get("json", {})
        assert create_body["name"] == f"library-{item_id[:12]}"
        assert create_body["kind"] == "mixed"
        assert create_body["source_path"] == str(collections_dir / item_id)

        # Verify poll GET called
        mock_client.get.assert_called_once_with(
            f"{taosmd_url}/collections/coll-123",
            headers={"Authorization": f"Bearer {taosmd_token}"},
        )


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


class TestLibraryRoutes:
    @pytest.mark.asyncio
    async def test_library_page_gone(self, client):
        """The server-rendered /library page was dropped (fold 6) —
        the Library UI is the React desktop app."""
        resp = await client.get("/library")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_ingest_no_input(self, client):
        resp = await client.post("/api/library/ingest")
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data

    @pytest.mark.asyncio
    async def test_ingest_url(self, client):
        resp = await client.post("/api/library/ingest", data={"url": "https://example.com/page"})
        assert resp.status_code == 202
        data = resp.json()
        assert "item_id" in data
        assert data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_ingest_file(self, client, tmp_path):
        test_file = tmp_path / "hello.txt"
        test_file.write_text("hello world")

        with open(test_file, "rb") as f:
            resp = await client.post(
                "/api/library/ingest",
                files={"file": ("hello.txt", f, "text/plain")},
            )
        assert resp.status_code == 202
        data = resp.json()
        assert "item_id" in data

    @pytest.mark.asyncio
    async def test_list_items(self, client):
        resp = await client.get("/api/library/items")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "count" in data

    @pytest.mark.asyncio
    async def test_get_nonexistent_item(self, client):
        resp = await client.get("/api/library/items/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_nonexistent_item(self, client):
        resp = await client.delete("/api/library/items/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_reprocess_nonexistent_item(self, client):
        resp = await client.post("/api/library/items/nonexistent/reprocess")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_ingest_and_get(self, client):
        resp = await client.post("/api/library/ingest", data={"url": "https://example.com"})
        assert resp.status_code == 202
        item_id = resp.json()["item_id"]

        resp = await client.get(f"/api/library/items/{item_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["item"]["id"] == item_id
        assert "artifacts" in data

    @pytest.mark.asyncio
    async def test_filter_by_kind(self, client):
        await client.post("/api/library/ingest", data={"url": "https://example.com"})
        await client.post("/api/library/ingest", data={"url": "https://youtube.com/watch?v=abc"})

        resp = await client.get("/api/library/items", params={"kind": "url:youtube"})
        data = resp.json()
        for item in data["items"]:
            assert item["kind"] == "url:youtube"

    # -- Auth: all endpoints require a session (CSRF is bypassed in tests, but
    #    unauthenticated requests still hit the global auth middleware).  Test
    #    that the 6 new endpoints return 401 when no session cookie/header is
    #    present.

    @pytest.mark.asyncio
    async def test_unauth_library_endpoints(self, app):
        """All 6 library endpoints return 401 without authentication."""
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as unauth_client:
            endpoints: list[tuple[str, str]] = [
                ("post", "/api/library/ingest"),
                ("get", "/api/library/items"),
                ("get", "/api/library/items/abc123"),
                ("delete", "/api/library/items/abc123"),
                ("post", "/api/library/items/abc123/reprocess"),
            ]
            for method, url in endpoints:
                if method == "post":
                    resp = await unauth_client.post(url, data={"url": "https://example.com"})
                elif method == "delete":
                    resp = await unauth_client.delete(url)
                else:
                    resp = await unauth_client.get(url)
                assert resp.status_code == 401, f"{method.upper()} {url} returned {resp.status_code}, expected 401"

    # -- 413: oversized upload (tested at the HTTP level in integration;
    #    ASGI transport does not propagate Content-Length to file.size)

    @pytest.mark.asyncio
    async def test_reprocess_idempotent(self, client, tmp_path):
        """Reprocessing an item deletes old artifacts first — no duplicates."""
        test_file = tmp_path / "notes.txt"
        test_file.write_text("test content")

        with open(test_file, "rb") as f:
            resp = await client.post(
                "/api/library/ingest",
                files={"file": ("notes.txt", f, "text/plain")},
            )
        assert resp.status_code == 202
        item_id = resp.json()["item_id"]

        # Wait for pipeline to finish (background task)
        import asyncio
        await asyncio.sleep(0.5)

        # Check initial artifact count
        resp = await client.get(f"/api/library/items/{item_id}")
        data = resp.json()
        initial_artifact_count = len(data["artifacts"])
        assert initial_artifact_count > 0, "Pipeline should produce artifacts"

        # First reprocess
        resp = await client.post(f"/api/library/items/{item_id}/reprocess")
        assert resp.status_code == 202
        await asyncio.sleep(0.5)

        # After first reprocess — exact same artifact count, not doubled.
        resp = await client.get(f"/api/library/items/{item_id}")
        data = resp.json()
        after_first = len(data["artifacts"])
        assert after_first == initial_artifact_count, (
            f"Reprocess should not duplicate artifacts: "
            f"initial={initial_artifact_count} after_first={after_first}"
        )
        assert data["item"].get("status") == "ready", (
            f"Item status should be ready after reprocess, got {data['item'].get('status')}"
        )

        # Second reprocess — should still work, no duplicates
        resp = await client.post(f"/api/library/items/{item_id}/reprocess")
        assert resp.status_code == 202

    @pytest.mark.asyncio
    async def test_reprocess_while_processing_returns_409(self, client, app):
        """Reprocessing an item that is already pending/processing returns 409."""
        # Ingest normally and wait for pipeline to finish
        import asyncio

        resp = await client.post(
            "/api/library/ingest",
            data={"url": "https://example.com"},
        )
        assert resp.status_code == 202
        item_id = resp.json()["item_id"]
        await asyncio.sleep(0.5)

        # Force status to "processing" to simulate an in-flight pipeline
        store = app.state.library_store
        await store.update_item_status(item_id, "processing")

        # Reprocess should be rejected
        resp = await client.post(f"/api/library/items/{item_id}/reprocess")
        assert resp.status_code == 409

    # -- Jobs endpoints --

    @pytest.mark.asyncio
    async def test_list_jobs_empty(self, client):
        resp = await client.get("/api/library/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert "jobs" in data
        assert data["jobs"] == []

    @pytest.mark.asyncio
    async def test_list_jobs_filter_by_state(self, client, app):
        """?state=error returns only error jobs, not jobs in other states."""
        resp = await client.get("/api/library/jobs")
        assert resp.status_code == 200
        store = app.state.library_store
        item_id = await store.create_item(kind="text", title="test")
        error_job_id = await store.create_job(item_id, "ingest")
        done_job_id = await store.create_job(item_id, "transcript")
        await store.update_job(error_job_id, state="error", error="boom")
        await store.update_job(done_job_id, state="done")

        resp = await client.get("/api/library/jobs", params={"state": "error"})
        assert resp.status_code == 200
        data = resp.json()
        returned_ids = {j["id"] for j in data["jobs"]}
        assert error_job_id in returned_ids
        assert done_job_id not in returned_ids

    @pytest.mark.asyncio
    async def test_retry_error_job(self, client, app):
        """POST /api/library/jobs/{id}/retry re-queues an error job."""
        resp = await client.get("/api/library/jobs")
        assert resp.status_code == 200
        store = app.state.library_store
        item_id = await store.create_item(kind="text", title="test")
        job_id = await store.create_job(item_id, "ingest")
        await store.update_job(job_id, state="error", error="boom")

        resp = await client.post(f"/api/library/jobs/{job_id}/retry")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == job_id
        assert data["status"] == "queued"

        job = await store.get_job(job_id)
        assert job["state"] == "queued"
        assert job["error"] == ""

    @pytest.mark.asyncio
    async def test_retry_unknown_job_returns_404(self, client):
        resp = await client.post("/api/library/jobs/nonexistent/retry")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_retry_non_error_job_returns_409(self, client, app):
        """Retrying a job that is not in error state is rejected."""
        resp = await client.get("/api/library/jobs")
        assert resp.status_code == 200
        store = app.state.library_store
        item_id = await store.create_item(kind="text", title="test")
        job_id = await store.create_job(item_id, "ingest")
        await store.update_job(job_id, state="done")

        resp = await client.post(f"/api/library/jobs/{job_id}/retry")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# P3: LibraryStore — rules + storage accounting
# ---------------------------------------------------------------------------


class TestLibraryStoreRules:
    @pytest.mark.asyncio
    async def test_create_and_list_rules(self, lib_store):
        rid = await lib_store.create_rule(
            source_pattern="*.youtube.com/*",
            quality="1080",
            auto_download=True,
        )
        assert rid

        rules = await lib_store.list_rules()
        assert len(rules) == 1
        assert rules[0]["source_pattern"] == "*.youtube.com/*"
        assert rules[0]["quality"] == "1080"
        assert rules[0]["auto_download"] == 1

    @pytest.mark.asyncio
    async def test_get_rule(self, lib_store):
        rid = await lib_store.create_rule(source_pattern="*.example.com/*")
        rule = await lib_store.get_rule(rid)
        assert rule is not None
        assert rule["source_pattern"] == "*.example.com/*"

        assert await lib_store.get_rule("nonexistent") is None

    @pytest.mark.asyncio
    async def test_delete_rule(self, lib_store):
        rid = await lib_store.create_rule(source_pattern="*.youtube.com/*")
        await lib_store.delete_rule(rid)
        assert len(await lib_store.list_rules()) == 0

    @pytest.mark.asyncio
    async def test_match_rules(self, lib_store):
        await lib_store.create_rule(
            source_pattern="*youtube.com/*", quality="720", auto_download=True,
        )
        await lib_store.create_rule(
            source_pattern="*vimeo.com/*", quality="1080", auto_download=False,
        )

        matched = await lib_store.match_rules("https://www.youtube.com/watch?v=abc")
        assert len(matched) == 1
        assert matched[0]["quality"] == "720"

        matched_vimeo = await lib_store.match_rules("https://vimeo.com/12345")
        assert len(matched_vimeo) == 1

        matched_none = await lib_store.match_rules("https://example.com")
        assert len(matched_none) == 0

    @pytest.mark.asyncio
    async def test_match_rules_disabled_ignored(self, lib_store):
        rid = await lib_store.create_rule(
            source_pattern="*example.com/*",
            enabled=False,
        )
        matched = await lib_store.match_rules("https://example.com/page")
        assert len(matched) == 0

    @pytest.mark.asyncio
    async def test_storage_summary(self, lib_store):
        await lib_store.create_item(kind="text", title="a.txt", size_bytes=100)
        await lib_store.create_item(kind="pdf", title="b.pdf", size_bytes=500)
        await lib_store.create_item(kind="url:youtube", title="c", size_bytes=0)

        summary = await lib_store.get_storage_summary()
        assert summary["total_count"] == 3
        assert summary["total_bytes"] == 600
        assert "text" in summary["by_kind"]
        assert "pdf" in summary["by_kind"]

    @pytest.mark.asyncio
    async def test_storage_summary_empty(self, lib_store):
        summary = await lib_store.get_storage_summary()
        assert summary["total_count"] == 0
        assert summary["total_bytes"] == 0


# ---------------------------------------------------------------------------
# P3: HeavyDownloadProcessor
# ---------------------------------------------------------------------------


class TestHeavyDownloadProcessor:
    @pytest.mark.asyncio
    async def test_process_heavy_download(self, lib_store, storage_dir):
        item_id = await lib_store.create_item(
            kind="url:youtube",
            source_url="https://youtube.com/watch?v=heavy-test",
        )
        await lib_store.update_item(item_id, quality="480")
        item = await lib_store.get_item(item_id)

        proc = HeavyDownloadProcessor(lib_store, storage_dir)

        from unittest.mock import patch

        mock_path = str(storage_dir / "downloads" / "test123.mp4")
        storage_dir.joinpath("downloads").mkdir(parents=True, exist_ok=True)
        storage_dir.joinpath("downloads", "test123.mp4").write_text("fake video data")

        with patch(
            "tinyagentos.knowledge_fetchers.youtube.download_video",
            _async_return(mock_path),
        ):
            artifacts = await proc.process(item)

        assert len(artifacts) == 1
        assert artifacts[0]["kind"] == "download"
        assert artifacts[0]["meta"]["quality"] == "480"

        updated = await lib_store.get_item(item_id)
        assert updated["download_path"] == mock_path
        assert updated["download_bytes"] > 0

    @pytest.mark.asyncio
    async def test_process_heavy_download_non_youtube(self, lib_store, storage_dir):
        item_id = await lib_store.create_item(
            kind="url:web",
            source_url="https://example.com",
        )
        item = await lib_store.get_item(item_id)

        proc = HeavyDownloadProcessor(lib_store, storage_dir)
        artifacts = await proc.process(item)
        assert artifacts == []

    @pytest.mark.asyncio
    async def test_process_heavy_download_no_source(self, lib_store, storage_dir):
        item_id = await lib_store.create_item(
            kind="url:youtube", source_url="",
        )
        item = await lib_store.get_item(item_id)

        proc = HeavyDownloadProcessor(lib_store, storage_dir)
        artifacts = await proc.process(item)
        assert artifacts == []

    @pytest.mark.asyncio
    async def test_process_heavy_download_invalid_quality(self, lib_store, storage_dir):
        """Invalid quality should fall back to 720."""
        item_id = await lib_store.create_item(
            kind="url:youtube",
            source_url="https://youtube.com/watch?v=qtest",
        )
        await lib_store.update_item(item_id, quality="9999")
        item = await lib_store.get_item(item_id)

        proc = HeavyDownloadProcessor(lib_store, storage_dir)

        from unittest.mock import patch

        mock_path = str(storage_dir / "downloads" / "test.mp4")
        storage_dir.joinpath("downloads").mkdir(parents=True, exist_ok=True)
        storage_dir.joinpath("downloads", "test.mp4").write_text("data")

        with patch(
            "tinyagentos.knowledge_fetchers.youtube.download_video",
            _async_return(mock_path),
        ):
            artifacts = await proc.process(item)

        # Quality should have been normalized to 720
        assert artifacts[0]["meta"]["quality"] == "720"


# ---------------------------------------------------------------------------
# P3: run_heavy_pipeline
# ---------------------------------------------------------------------------


class TestRunHeavyPipeline:
    @pytest.mark.asyncio
    async def test_run_heavy_pipeline_rule_quality(self, lib_store, storage_dir):
        """When a matching rule exists, its quality is used."""
        await lib_store.create_rule(
            source_pattern="*youtube.com/*",
            quality="480",
            auto_download=False,
        )

        item_id = await lib_store.create_item(
            kind="url:youtube",
            source_url="https://youtube.com/watch?v=rule-test",
        )
        storage_dir.joinpath("downloads").mkdir(parents=True, exist_ok=True)

        mock_path = str(storage_dir / "downloads" / "test.mp4")
        storage_dir.joinpath("downloads", "test.mp4").write_text("fake data")

        from unittest.mock import patch
        from tinyagentos.library_pipeline import run_heavy_pipeline

        with patch(
            "tinyagentos.knowledge_fetchers.youtube.download_video",
            _async_return(mock_path),
        ):
            result = await run_heavy_pipeline(
                lib_store, item_id, storage_dir, quality="",
            )

        assert result is not None
        assert result["quality"] == "480"  # From rule

    @pytest.mark.asyncio
    async def test_run_heavy_pipeline_explicit_quality(self, lib_store, storage_dir):
        """Explicit quality parameter overrides rule quality."""
        await lib_store.create_rule(
            source_pattern="*youtube.com/*",
            quality="480",
        )

        item_id = await lib_store.create_item(
            kind="url:youtube",
            source_url="https://youtube.com/watch?v=explicit-test",
        )
        storage_dir.joinpath("downloads").mkdir(parents=True, exist_ok=True)

        mock_path = str(storage_dir / "downloads" / "test.mp4")
        storage_dir.joinpath("downloads", "test.mp4").write_text("fake data")

        from unittest.mock import patch
        from tinyagentos.library_pipeline import run_heavy_pipeline

        with patch(
            "tinyagentos.knowledge_fetchers.youtube.download_video",
            _async_return(mock_path),
        ):
            result = await run_heavy_pipeline(
                lib_store, item_id, storage_dir, quality="1080",
            )

        assert result["quality"] == "1080"  # Explicit wins

    @pytest.mark.asyncio
    async def test_run_heavy_pipeline_non_youtube(self, lib_store, storage_dir):
        """run_heavy_pipeline returns None for non-YouTube items."""
        item_id = await lib_store.create_item(
            kind="url:web",
            source_url="https://example.com/page",
        )

        from tinyagentos.library_pipeline import run_heavy_pipeline
        result = await run_heavy_pipeline(
            lib_store, item_id, storage_dir,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_run_heavy_pipeline_creates_job(self, lib_store, storage_dir):
        """Heavy pipeline creates a job entry."""
        item_id = await lib_store.create_item(
            kind="url:youtube",
            source_url="https://youtube.com/watch?v=job-test",
        )
        storage_dir.joinpath("downloads").mkdir(parents=True, exist_ok=True)

        mock_path = str(storage_dir / "downloads" / "test.mp4")
        storage_dir.joinpath("downloads", "test.mp4").write_text("fake data")

        from unittest.mock import patch
        from tinyagentos.library_pipeline import run_heavy_pipeline

        with patch(
            "tinyagentos.knowledge_fetchers.youtube.download_video",
            _async_return(mock_path),
        ):
            await run_heavy_pipeline(lib_store, item_id, storage_dir, quality="720")

        jobs = await lib_store.get_item_jobs(item_id)
        heavy_jobs = [j for j in jobs if j["stage"] == "heavy_download"]
        assert len(heavy_jobs) >= 1


# ---------------------------------------------------------------------------
# P3: Library routes — download, rules, usage
# ---------------------------------------------------------------------------


class TestLibraryRoutesP3:
    @pytest.mark.asyncio
    async def test_trigger_download(self, client):
        """POST /api/library/items/{id}/download triggers heavy download."""
        # Ingest a YouTube URL first
        resp = await client.post(
            "/api/library/ingest", data={"url": "https://youtube.com/watch?v=dl-test"}
        )
        assert resp.status_code == 202
        item_id = resp.json()["item_id"]

        # Allow pipeline to finish
        import asyncio as _asyncio
        await _asyncio.sleep(0.5)

        resp = await client.post(
            f"/api/library/items/{item_id}/download",
            data={"quality": "480"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "downloading"
        assert data["quality"] == "480"

    @pytest.mark.asyncio
    async def test_trigger_download_nonexistent(self, client):
        resp = await client.post("/api/library/items/nonexistent/download")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_trigger_download_non_youtube(self, client):
        """Download endpoint rejects non-YouTube items."""
        resp = await client.post(
            "/api/library/ingest", data={"url": "https://example.com/page"}
        )
        assert resp.status_code == 202
        item_id = resp.json()["item_id"]

        import asyncio as _asyncio
        await _asyncio.sleep(0.5)

        resp = await client.post(f"/api/library/items/{item_id}/download")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_download_status(self, client):
        resp = await client.post(
            "/api/library/ingest", data={"url": "https://youtube.com/watch?v=status"}
        )
        item_id = resp.json()["item_id"]

        import asyncio as _asyncio
        await _asyncio.sleep(0.5)

        resp = await client.get(f"/api/library/items/{item_id}/download/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["item_id"] == item_id
        assert "downloaded" in data

    @pytest.mark.asyncio
    async def test_create_rule(self, client):
        resp = await client.post(
            "/api/library/rules",
            data={"source_pattern": "*.youtube.com/*", "quality": "1080", "auto_download": "true"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["rule_id"]
        assert data["source_pattern"] == "*.youtube.com/*"

    @pytest.mark.asyncio
    async def test_list_rules(self, client):
        # Create a rule first
        await client.post(
            "/api/library/rules", data={"source_pattern": "*.example.com/*"}
        )
        resp = await client.get("/api/library/rules")
        assert resp.status_code == 200
        data = resp.json()
        assert "rules" in data
        assert data["count"] >= 1

    @pytest.mark.asyncio
    async def test_delete_rule(self, client):
        resp = await client.post(
            "/api/library/rules", data={"source_pattern": "*.test.com/*"}
        )
        rule_id = resp.json()["rule_id"]

        resp = await client.delete(f"/api/library/rules/{rule_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        # Verify it's gone
        resp = await client.get("/api/library/rules")
        data = resp.json()
        rule_ids = [r["id"] for r in data["rules"]]
        assert rule_id not in rule_ids

    @pytest.mark.asyncio
    async def test_delete_nonexistent_rule(self, client):
        resp = await client.delete("/api/library/rules/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_storage_usage(self, client):
        resp = await client.get("/api/library/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_count" in data
        assert "total_bytes" in data
        assert "by_kind" in data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_minimal_pdf(path: Path):
    """Create a minimal valid PDF file for testing."""
    pdf_content = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\n"
        b"startxref\n190\n%%EOF\n"
    )
    path.write_bytes(pdf_content)


def _create_test_image(path: Path):
    """Create a simple test image using PIL."""
    from PIL import Image
    img = Image.new("RGB", (100, 50), color="blue")
    img.save(path)


def _async_return(value):
    """Return an async callable that returns ``value`` (for mocking async functions)."""
    async def _inner(*args, **kwargs):
        return value
    return _inner


def _mock_httpx_response(html: str, status_code: int = 200):
    """Return a mock httpx Response with the given HTML body."""
    from unittest.mock import MagicMock
    mock = MagicMock()
    mock.text = html
    mock.status_code = status_code
    mock.is_redirect = False
    mock.encoding = "utf-8"
    mock.headers = {"content-type": "text/html; charset=utf-8"}

    async def _aiter_bytes(_chunk_size: int = 8192):
        yield html.encode("utf-8")

    mock.aiter_bytes = _aiter_bytes
    return mock


def _mock_stream_ctx(mock_resp):
    """Return an async context manager that yields ``mock_resp`` from stream()."""
    class _StreamCtx:
        async def __aenter__(self):
            return mock_resp

        async def __aexit__(self, *args):
            pass

    return _StreamCtx()


def _mock_stream_callable(mock_resp):
    """Return a callable that when invoked returns a stream async context manager."""
    from unittest.mock import MagicMock
    ctx = _mock_stream_ctx(mock_resp)
    fn = MagicMock(return_value=ctx)
    return fn
