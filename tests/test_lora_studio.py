"""Tests for LoRA Studio -- URL parsing, the Civitai ingest job, routes,
the Models-app scan exclusion, and the Library hook.

Mirrors tests/test_library.py's mocking style: httpx.AsyncClient is patched
at the module under test so no real network I/O ever happens.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from tinyagentos.lora_store import LoraStore
from tinyagentos.routes import lora_studio as ls
from tinyagentos.routes.lora_studio import (
    CivitaiIngestError,
    CivitaiUrlError,
    is_civitai_url,
    lora_slug,
    parse_civitai_url,
    run_civitai_ingest,
)
from tinyagentos.routes.models import get_downloaded_models


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def lora_store():
    """LoraStore backed by a temporary SQLite database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    store = LoraStore(db_path)
    await store.init()
    yield store
    await store.close()
    try:
        db_path.unlink()
    except OSError:
        pass


@pytest.fixture
def isolated_loras_root(tmp_path, monkeypatch):
    """Point loras_root() at a scratch dir so ingest tests never touch the
    real repo's models/ tree."""
    fake_root = tmp_path / "loras"
    monkeypatch.setattr(ls, "loras_root", lambda: fake_root)
    return fake_root


CIVITAI_MODEL_JSON = {
    "id": 2851174,
    "name": "Test LoRA",
    "description": "<p>A test LoRA</p>",
    "type": "LORA",
    "nsfw": False,
    "tags": ["anime", "style"],
    "creator": {"username": "testcreator"},
    "modelVersions": [
        {
            "id": 999,
            "baseModel": "SDXL 1.0",
            "trainedWords": ["mytrigger"],
            "images": [
                {"url": "https://image.civitai.com/preview1.jpeg"},
                {"url": "https://image.civitai.com/preview2.jpeg"},
            ],
            "files": [
                {
                    "primary": True,
                    "name": "test-lora.safetensors",
                    "downloadUrl": "https://civitai.com/api/download/models/999",
                    "hashes": {"SHA256": "ABCDEF0123456789"},
                }
            ],
        }
    ],
}


def _civitai_response(status_code: int, json_body: dict | None = None) -> httpx.Response:
    """A real httpx.Response so .raise_for_status()/.json() behave exactly
    like the live client would (no mock-plumbing gaps)."""
    request = httpx.Request("GET", "https://civitai.com/api/v1/models/1")
    content = json.dumps(json_body if json_body is not None else {}).encode()
    return httpx.Response(status_code, request=request, content=content)


def _patch_get(monkeypatch, result):
    """Patch httpx.AsyncClient used by _fetch_model to return/raise *result*
    from .get(). *result* is an httpx.Response or an Exception instance."""

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, *a, **kw):
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(ls.httpx, "AsyncClient", lambda *a, **kw: _FakeClient())


async def _fake_download_ok(url, dest, expected_sha256=None, **kwargs):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = b"safetensors-bytes" if dest.suffix == ".safetensors" else b"jpg-bytes"
    dest.write_bytes(content)
    return dest


async def _fake_download_sha_mismatch(url, dest, expected_sha256=None, **kwargs):
    """Mirrors download_file's real contract: write, detect mismatch, unlink, raise."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"wrong-bytes")
    dest.unlink()
    raise ValueError(f"SHA256 mismatch: expected {expected_sha256}, got deadbeef")


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


class TestParseCivitaiUrl:
    def test_accepts_bare_model_id(self):
        model_id, slug, version_id = parse_civitai_url("https://civitai.com/models/2851174")
        assert model_id == 2851174
        assert slug is None
        assert version_id is None

    def test_accepts_model_id_with_slug(self):
        model_id, slug, version_id = parse_civitai_url(
            "https://civitai.com/models/2851174/some-cool-lora"
        )
        assert model_id == 2851174
        assert slug == "some-cool-lora"
        assert version_id is None

    def test_accepts_model_version_id_query(self):
        model_id, slug, version_id = parse_civitai_url(
            "https://civitai.com/models/2851174/some-cool-lora?modelVersionId=456"
        )
        assert model_id == 2851174
        assert slug == "some-cool-lora"
        assert version_id == 456

    def test_accepts_civitai_red(self):
        model_id, slug, version_id = parse_civitai_url("https://civitai.red/models/123")
        assert model_id == 123

    def test_accepts_www_prefix(self):
        model_id, slug, version_id = parse_civitai_url("https://www.civitai.com/models/123")
        assert model_id == 123

    def test_rejects_disallowed_host(self):
        with pytest.raises(CivitaiUrlError):
            parse_civitai_url("https://evil.example.com/models/123")

    def test_rejects_huggingface_host(self):
        with pytest.raises(CivitaiUrlError):
            parse_civitai_url("https://huggingface.co/models/123")

    def test_rejects_missing_model_path(self):
        with pytest.raises(CivitaiUrlError):
            parse_civitai_url("https://civitai.com/")

    def test_rejects_wrong_path_prefix(self):
        with pytest.raises(CivitaiUrlError):
            parse_civitai_url("https://civitai.com/images/123")

    def test_rejects_non_numeric_id(self):
        with pytest.raises(CivitaiUrlError):
            parse_civitai_url("https://civitai.com/models/not-a-number")


class TestIsCivitaiUrl:
    def test_true_for_civitai_hosts(self):
        assert is_civitai_url("https://civitai.com/models/1")
        assert is_civitai_url("https://civitai.red/models/1")
        assert is_civitai_url("https://www.civitai.com/models/1")

    def test_false_for_other_hosts(self):
        assert not is_civitai_url("https://example.com/models/1")
        assert not is_civitai_url("https://civitai.com.evil.com/models/1")


class TestLoraSlug:
    def test_uses_url_slug_when_present(self):
        assert lora_slug("Some Cool LoRA!", 123) == "some-cool-lora-123"

    def test_falls_back_to_model_id(self):
        assert lora_slug(None, 123) == "123"


# ---------------------------------------------------------------------------
# Ingest job -- loud-failure paths
# ---------------------------------------------------------------------------


class TestRunCivitaiIngestFailures:
    @pytest.mark.asyncio
    async def test_451_fails_loudly_no_file(
        self, lora_store, isolated_loras_root, monkeypatch
    ):
        """Civitai's own 451 geo-block must fail loud with an actionable
        message and leave nothing on disk."""
        lora_id = "lora-test-2851174"
        await lora_store.create_pending(
            lora_id, source_url="https://civitai.com/models/2851174",
            civitai_model_id=2851174, civitai_version_id=None,
        )
        _patch_get(monkeypatch, _civitai_response(451, {"error": "unavailable"}))

        with pytest.raises(CivitaiIngestError):
            await run_civitai_ingest(lora_store, lora_id, proxy_url="")

        row = await lora_store.get(lora_id)
        assert row["status"] == "failed"
        assert "451" in row["error"]
        assert "lora_ingest_proxy_url" in row["error"]
        assert not isolated_loras_root.exists() or not any(isolated_loras_root.rglob("*"))

    @pytest.mark.asyncio
    async def test_connect_error_fails_loudly(
        self, lora_store, isolated_loras_root, monkeypatch
    ):
        """An unreachable/broken proxy must fail loud, not silently succeed."""
        lora_id = "lora-test-2851174"
        await lora_store.create_pending(
            lora_id, source_url="https://civitai.com/models/2851174",
            civitai_model_id=2851174, civitai_version_id=None,
        )
        _patch_get(monkeypatch, httpx.ConnectError("connection refused"))

        with pytest.raises(CivitaiIngestError):
            await run_civitai_ingest(lora_store, lora_id, proxy_url="socks5://dead:1080")

        row = await lora_store.get(lora_id)
        assert row["status"] == "failed"
        assert "lora_ingest_proxy_url" in row["error"]

    @pytest.mark.asyncio
    async def test_non_lora_type_refused(
        self, lora_store, isolated_loras_root, monkeypatch
    ):
        """v1 archives LoRAs only -- a Checkpoint must be refused, not
        silently accepted."""
        lora_id = "lora-test-2851174"
        await lora_store.create_pending(
            lora_id, source_url="https://civitai.com/models/2851174",
            civitai_model_id=2851174, civitai_version_id=None,
        )
        checkpoint_json = {**CIVITAI_MODEL_JSON, "type": "Checkpoint"}
        _patch_get(monkeypatch, _civitai_response(200, checkpoint_json))

        with pytest.raises(CivitaiIngestError):
            await run_civitai_ingest(lora_store, lora_id, proxy_url="")

        row = await lora_store.get(lora_id)
        assert row["status"] == "failed"
        assert "Checkpoint" in row["error"]
        assert "LoRA" in row["error"]

    @pytest.mark.asyncio
    async def test_sha256_mismatch_fails_and_removes_file(
        self, lora_store, isolated_loras_root, monkeypatch
    ):
        lora_id = "lora-test-2851174"
        await lora_store.create_pending(
            lora_id, source_url="https://civitai.com/models/2851174",
            civitai_model_id=2851174, civitai_version_id=None,
        )
        _patch_get(monkeypatch, _civitai_response(200, CIVITAI_MODEL_JSON))
        monkeypatch.setattr(ls, "download_file", _fake_download_sha_mismatch)

        with pytest.raises(CivitaiIngestError, match="SHA256 mismatch"):
            await run_civitai_ingest(lora_store, lora_id, proxy_url="")

        row = await lora_store.get(lora_id)
        assert row["status"] == "failed"
        assert "SHA256 mismatch" in row["error"]

        dest = isolated_loras_root / "test-2851174" / "test-lora.safetensors"
        assert not dest.exists()
        # Loud failure cleans up the whole partial directory, not just the file.
        assert not (isolated_loras_root / "test-2851174").exists()


# ---------------------------------------------------------------------------
# Ingest job -- happy path
# ---------------------------------------------------------------------------


class TestRunCivitaiIngestHappyPath:
    @pytest.mark.asyncio
    async def test_happy_path_populates_row_and_downloads(
        self, lora_store, isolated_loras_root, monkeypatch
    ):
        lora_id = "lora-test-2851174"
        await lora_store.create_pending(
            lora_id, source_url="https://civitai.com/models/2851174/test-lora",
            civitai_model_id=2851174, civitai_version_id=None,
        )
        _patch_get(monkeypatch, _civitai_response(200, CIVITAI_MODEL_JSON))
        monkeypatch.setattr(ls, "download_file", _fake_download_ok)

        await run_civitai_ingest(lora_store, lora_id, proxy_url="")

        row = await lora_store.get(lora_id)
        assert row["status"] == "ready"
        assert row["error"] == ""
        assert row["name"] == "Test LoRA"
        assert row["creator"] == "testcreator"
        assert row["base_model"] == "SDXL 1.0"
        assert json.loads(row["trigger_words"]) == ["mytrigger"]
        assert json.loads(row["tags"]) == ["anime", "style"]
        assert row["sha256"] == "abcdef0123456789"  # lowercased
        assert row["file_name"] == "test-lora.safetensors"
        assert Path(row["file_path"]).exists()
        assert row["bytes"] > 0

        previews = json.loads(row["preview_paths"])
        assert len(previews) == 2
        for p in previews:
            assert Path(p).exists()

    @pytest.mark.asyncio
    async def test_explicit_version_id_selected(
        self, lora_store, isolated_loras_root, monkeypatch
    ):
        multi_version_json = {
            **CIVITAI_MODEL_JSON,
            "modelVersions": [
                {**CIVITAI_MODEL_JSON["modelVersions"][0], "id": 1, "baseModel": "SD 1.5"},
                {**CIVITAI_MODEL_JSON["modelVersions"][0], "id": 2, "baseModel": "SDXL 1.0"},
            ],
        }
        lora_id = "lora-test-2851174"
        await lora_store.create_pending(
            lora_id, source_url="https://civitai.com/models/2851174?modelVersionId=2",
            civitai_model_id=2851174, civitai_version_id=2,
        )
        _patch_get(monkeypatch, _civitai_response(200, multi_version_json))
        monkeypatch.setattr(ls, "download_file", _fake_download_ok)

        await run_civitai_ingest(lora_store, lora_id, proxy_url="")

        row = await lora_store.get(lora_id)
        assert row["status"] == "ready"
        assert row["base_model"] == "SDXL 1.0"


# ---------------------------------------------------------------------------
# Models-app scan exclusion
# ---------------------------------------------------------------------------


class TestModelsScanExcludesLoras:
    def test_loras_subtree_excluded(self, tmp_path):
        models_dir = tmp_path / "models"
        (models_dir / "loras" / "some-lora").mkdir(parents=True)
        (models_dir / "loras" / "some-lora" / "weights.safetensors").write_bytes(b"x" * 100)
        (models_dir / "real-model.safetensors").write_bytes(b"y" * 100)

        results = get_downloaded_models(models_dir)
        filenames = {r["filename"] for r in results}
        assert "weights.safetensors" not in filenames
        assert "real-model.safetensors" in filenames

    def test_loras_subtree_excluded_even_when_only_content(self, tmp_path):
        models_dir = tmp_path / "models"
        (models_dir / "loras" / "x").mkdir(parents=True)
        (models_dir / "loras" / "x" / "weights.safetensors").write_bytes(b"x" * 100)

        assert get_downloaded_models(models_dir) == []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class TestIngestRoute:
    @pytest.mark.asyncio
    async def test_ingest_rejects_bad_host(self, client):
        resp = await client.post(
            "/api/loras/ingest", data={"url": "https://evil.example.com/models/1"},
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_ingest_creates_pending_row(self, client, monkeypatch):
        # Never actually run the background ingest job in a route test --
        # the ingest job itself is covered directly above.
        monkeypatch.setattr(
            ls, "_run_ingest_background", AsyncMock(return_value=None),
        )
        resp = await client.post(
            "/api/loras/ingest",
            data={"url": "https://civitai.com/models/999888/cool-lora"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["id"] == "lora-cool-lora-999888"
        assert data["status"] == "pending"
        assert data["civitai_model_id"] == 999888
        assert data["source_url"] == "https://civitai.com/models/999888/cool-lora"


class TestListGetRoutes:
    @pytest.mark.asyncio
    async def test_list_and_get(self, client, app, monkeypatch):
        monkeypatch.setattr(
            ls, "_run_ingest_background", AsyncMock(return_value=None),
        )
        resp = await client.post(
            "/api/loras/ingest", data={"url": "https://civitai.com/models/111"},
        )
        lora_id = resp.json()["id"]

        resp = await client.get("/api/loras")
        assert resp.status_code == 200
        ids = {r["id"] for r in resp.json()["loras"]}
        assert lora_id in ids

        resp = await client.get(f"/api/loras/{lora_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == lora_id

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, client):
        resp = await client.get("/api/loras/lora-does-not-exist")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_filters_by_status(self, client, app):
        store = app.state.lora_store
        await store.create_pending(
            "lora-a", source_url="https://civitai.com/models/1",
            civitai_model_id=1, civitai_version_id=None,
        )
        await store.create_pending(
            "lora-b", source_url="https://civitai.com/models/2",
            civitai_model_id=2, civitai_version_id=None,
        )
        await store.update("lora-b", status="ready")

        resp = await client.get("/api/loras", params={"status": "ready"})
        ids = {r["id"] for r in resp.json()["loras"]}
        assert ids == {"lora-b"}


class TestPreviewRoute:
    @pytest.mark.asyncio
    async def test_preview_serves_stored_image(self, client, app, isolated_loras_root):
        store = app.state.lora_store
        lora_dir = isolated_loras_root / "lora-g-slug" / "previews"
        lora_dir.mkdir(parents=True)
        preview_path = lora_dir / "00.jpg"
        preview_path.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")

        await store.create_pending(
            "lora-lora-g-slug", source_url="https://civitai.com/models/7",
            civitai_model_id=7, civitai_version_id=None,
        )
        await store.update(
            "lora-lora-g-slug", preview_paths=[str(preview_path)], status="ready",
        )

        resp = await client.get("/api/loras/lora-lora-g-slug/preview/0")
        assert resp.status_code == 200
        assert resp.content == b"\xff\xd8\xff\xe0fakejpeg"

    @pytest.mark.asyncio
    async def test_preview_out_of_range_404(self, client, app):
        store = app.state.lora_store
        await store.create_pending(
            "lora-h", source_url="https://civitai.com/models/8",
            civitai_model_id=8, civitai_version_id=None,
        )
        resp = await client.get("/api/loras/lora-h/preview/0")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_preview_refuses_path_outside_root(self, client, app):
        store = app.state.lora_store
        await store.create_pending(
            "lora-i", source_url="https://civitai.com/models/9",
            civitai_model_id=9, civitai_version_id=None,
        )
        outside = str(ls.loras_root().parent.parent / "evil.jpg")
        await store.update("lora-i", preview_paths=[outside], status="ready")

        resp = await client.get("/api/loras/lora-i/preview/0")
        assert resp.status_code == 400


class TestRetryRoute:
    @pytest.mark.asyncio
    async def test_retry_nonexistent(self, client):
        resp = await client.post("/api/loras/lora-nope/retry")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_retry_requires_failed_status(self, client, app):
        store = app.state.lora_store
        await store.create_pending(
            "lora-c", source_url="https://civitai.com/models/3",
            civitai_model_id=3, civitai_version_id=None,
        )
        resp = await client.post("/api/loras/lora-c/retry")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_retry_failed_row(self, client, app, monkeypatch):
        monkeypatch.setattr(
            ls, "_run_ingest_background", AsyncMock(return_value=None),
        )
        store = app.state.lora_store
        await store.create_pending(
            "lora-d", source_url="https://civitai.com/models/4",
            civitai_model_id=4, civitai_version_id=None,
        )
        await store.update("lora-d", status="failed", error="boom")

        resp = await client.post("/api/loras/lora-d/retry")
        assert resp.status_code == 202

        row = await store.get("lora-d")
        assert row["status"] == "pending"
        assert row["error"] == ""


class TestDeleteRoute:
    @pytest.mark.asyncio
    async def test_delete_refuses_path_outside_loras_root(self, client, app):
        store = app.state.lora_store
        await store.create_pending(
            "lora-evil", source_url="https://civitai.com/models/5",
            civitai_model_id=5, civitai_version_id=None,
        )
        outside_path = str(ls.loras_root().parent.parent / "evil.safetensors")
        await store.update("lora-evil", file_path=outside_path, status="ready")

        resp = await client.delete("/api/loras/lora-evil")
        assert resp.status_code == 400

        # Refused wholesale: the row is untouched, not partially deleted.
        still_there = await store.get("lora-evil")
        assert still_there is not None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, client):
        resp = await client.delete("/api/loras/lora-nope")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_removes_row_and_files(
        self, client, app, isolated_loras_root
    ):
        store = app.state.lora_store
        lora_dir = isolated_loras_root / "lora-f-slug"
        lora_dir.mkdir(parents=True)
        file_path = lora_dir / "weights.safetensors"
        file_path.write_bytes(b"x" * 10)

        await store.create_pending(
            "lora-lora-f-slug", source_url="https://civitai.com/models/6",
            civitai_model_id=6, civitai_version_id=None,
        )
        await store.update(
            "lora-lora-f-slug", file_path=str(file_path), status="ready",
        )

        resp = await client.delete("/api/loras/lora-lora-f-slug")
        assert resp.status_code == 200

        assert await store.get("lora-lora-f-slug") is None
        assert not lora_dir.exists()


# ---------------------------------------------------------------------------
# Library hook (section 4)
# ---------------------------------------------------------------------------


class TestLibraryHook:
    def test_detect_kind_civitai(self):
        from tinyagentos.library_pipeline import detect_kind
        assert detect_kind(source_url="https://civitai.com/models/2851174") == "url:civitai"
        assert detect_kind(source_url="https://civitai.red/models/1") == "url:civitai"
        # Non-civitai URLs still fall through to the generic web kind.
        assert detect_kind(source_url="https://example.com/models/1") == "url:web"

    @pytest.mark.asyncio
    async def test_civitai_processor_links_library_item(
        self, isolated_loras_root, monkeypatch, tmp_path
    ):
        from tinyagentos.library_pipeline import CivitaiProcessor
        from tinyagentos.library_store import LibraryStore

        lib_db = tmp_path / "library.db"
        lib_store = LibraryStore(lib_db)
        await lib_store.init()
        storage_dir = tmp_path / "library"
        storage_dir.mkdir()
        # CivitaiProcessor derives data_dir from storage_dir.parent and opens
        # its own LoraStore at data_dir/loras.db -- matches routes/library.py's
        # _library_dir_from_app (data_dir / "library").
        (tmp_path / "config.yaml").write_text("server: {}\n")

        try:
            item_id = await lib_store.create_item(
                kind="url:civitai",
                source_url="https://civitai.com/models/2851174/test-lora",
            )
            item = await lib_store.get_item(item_id)

            _patch_get(monkeypatch, _civitai_response(200, CIVITAI_MODEL_JSON))
            monkeypatch.setattr(ls, "download_file", _fake_download_ok)

            proc = CivitaiProcessor(lib_store, storage_dir)
            artifacts = await proc.process(item)

            assert any(a["kind"] == "lora" for a in artifacts)
            updated = await lib_store.get_item(item_id)
            assert updated["title"] == "Test LoRA"
            meta = json.loads(updated["meta_json"])
            assert meta["lora_id"] == "lora-test-lora-2851174"

            lora_store = LoraStore(tmp_path / "loras.db")
            await lora_store.init()
            try:
                row = await lora_store.get("lora-test-lora-2851174")
                assert row["status"] == "ready"
            finally:
                await lora_store.close()
        finally:
            await lib_store.close()


# ---------------------------------------------------------------------------
# File selection and untrusted file names (bot findings, PR #2374)
# ---------------------------------------------------------------------------


class TestPickFile:
    def test_primary_non_safetensors_is_skipped(self):
        """Civitai marks training data and pickles primary on some versions.

        Archiving a primary .bin would contradict both the error message this
        function raises and what LoRA Studio claims to store.
        """
        version = {
            "files": [
                {"primary": True, "name": "training-data.zip"},
                {"primary": False, "name": "real-lora.safetensors"},
            ]
        }
        assert ls._pick_file(version)["name"] == "real-lora.safetensors"

    def test_primary_safetensors_still_wins(self):
        version = {
            "files": [
                {"primary": False, "name": "other.safetensors"},
                {"primary": True, "name": "primary.safetensors"},
            ]
        }
        assert ls._pick_file(version)["name"] == "primary.safetensors"

    def test_no_safetensors_anywhere_raises(self):
        version = {"files": [{"primary": True, "name": "model.bin"}]}
        with pytest.raises(CivitaiIngestError, match="No .safetensors file"):
            ls._pick_file(version)


class TestUntrustedFileName:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("../../evil.safetensors", "evil.safetensors"),
            ("/tmp/evil.safetensors", "evil.safetensors"),
            ("..\\..\\evil.safetensors", "evil.safetensors"),
            ("nested/dir/ok.safetensors", "ok.safetensors"),
            ("", "fallback.safetensors"),
            ("..", "fallback.safetensors"),
            (None, "fallback.safetensors"),
        ],
    )
    def test_safe_filename_reduces_to_one_component(self, raw, expected):
        assert ls._safe_filename(raw, "fallback.safetensors") == expected

    @pytest.mark.asyncio
    async def test_traversing_file_name_stays_inside_loras_root(
        self, lora_store, isolated_loras_root, monkeypatch
    ):
        """A hostile Civitai response must not write outside loras_root().

        An escaped file is also invisible to the failure cleanup and to
        DELETE /api/loras/{id}, both of which are anchored on that root.
        """
        model_json = json.loads(json.dumps(CIVITAI_MODEL_JSON))
        model_json["modelVersions"][0]["files"][0]["name"] = (
            "../../../escaped.safetensors"
        )
        lora_id = "lora-test-2851174"
        await lora_store.create_pending(
            lora_id, source_url="https://civitai.com/models/2851174/test-lora",
            civitai_model_id=2851174, civitai_version_id=None,
        )
        _patch_get(monkeypatch, _civitai_response(200, model_json))
        monkeypatch.setattr(ls, "download_file", _fake_download_ok)

        await run_civitai_ingest(lora_store, lora_id, proxy_url="")

        row = await lora_store.get(lora_id)
        assert row["status"] == "ready"
        written = Path(row["file_path"]).resolve()
        # relative_to raises if the file landed outside the archive root.
        written.relative_to(isolated_loras_root.resolve())
        assert row["file_name"] == "escaped.safetensors"
        assert not (isolated_loras_root.parent.parent / "escaped.safetensors").exists()


class TestRetryIsAtomic:
    @pytest.mark.asyncio
    async def test_claim_retry_wins_once(self, lora_store):
        await lora_store.create_pending(
            "lora-atomic", source_url="https://civitai.com/models/7",
            civitai_model_id=7, civitai_version_id=None,
        )
        await lora_store.update("lora-atomic", status="failed", error="boom")

        assert await lora_store.claim_retry("lora-atomic") is True
        # Second caller loses: the row is no longer failed.
        assert await lora_store.claim_retry("lora-atomic") is False

        row = await lora_store.get("lora-atomic")
        assert row["status"] == "pending"
        assert row["error"] == ""

    @pytest.mark.asyncio
    async def test_claim_retry_ignores_unknown_row(self, lora_store):
        assert await lora_store.claim_retry("lora-does-not-exist") is False

    @pytest.mark.asyncio
    async def test_concurrent_retries_schedule_one_job(self, client, app, monkeypatch):
        """Two retries in flight must not start two downloads into one directory."""
        scheduled = []

        async def _record(store, lora_id, proxy_url):
            scheduled.append(lora_id)

        monkeypatch.setattr(ls, "_run_ingest_background", _record)

        store = app.state.lora_store
        await store.create_pending(
            "lora-race", source_url="https://civitai.com/models/8",
            civitai_model_id=8, civitai_version_id=None,
        )
        await store.update("lora-race", status="failed", error="boom")

        results = await asyncio.gather(
            client.post("/api/loras/lora-race/retry"),
            client.post("/api/loras/lora-race/retry"),
        )
        codes = sorted(r.status_code for r in results)
        assert codes == [202, 409]
        assert len(scheduled) == 1


class TestLibraryHookConfigSideEffects:
    @pytest.mark.asyncio
    async def test_civitai_processor_never_rewrites_config(
        self, isolated_loras_root, monkeypatch, tmp_path
    ):
        """A background Library ingest must not write to the user's config.

        load_config() persists a legacy litellm_port pin when the key is
        absent, so reading the proxy value through it turned an ingest into a
        config write. The processor reads the single key directly instead.
        """
        from tinyagentos.library_pipeline import CivitaiProcessor
        from tinyagentos.library_store import LibraryStore

        lib_store = LibraryStore(tmp_path / "library.db")
        await lib_store.init()
        storage_dir = tmp_path / "library"
        storage_dir.mkdir()
        config_path = tmp_path / "config.yaml"
        # No server.litellm_port -- exactly the legacy shape load_config pins.
        config_path.write_text("server: {}\nlora_ingest_proxy_url: ''\n")
        before = config_path.read_text()

        try:
            item_id = await lib_store.create_item(
                kind="url:civitai",
                source_url="https://civitai.com/models/2851174/test-lora",
            )
            item = await lib_store.get_item(item_id)
            _patch_get(monkeypatch, _civitai_response(200, CIVITAI_MODEL_JSON))
            monkeypatch.setattr(ls, "download_file", _fake_download_ok)

            await CivitaiProcessor(lib_store, storage_dir).process(item)

            assert config_path.read_text() == before
        finally:
            await lib_store.close()

    def test_read_lora_proxy_url_reads_the_key(self, tmp_path):
        from tinyagentos.library_pipeline import _read_lora_proxy_url

        path = tmp_path / "config.yaml"
        path.write_text("server: {}\nlora_ingest_proxy_url: http://proxy:3128\n")
        assert _read_lora_proxy_url(path) == "http://proxy:3128"
        assert _read_lora_proxy_url(tmp_path / "missing.yaml") == ""
        (tmp_path / "bad.yaml").write_text(": : not [ yaml")
        assert _read_lora_proxy_url(tmp_path / "bad.yaml") == ""
