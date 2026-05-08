"""Tests for HFMultiInstaller — covers happy path, exclude patterns,
existing-file skip, single-file fallback, and error envelopes.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tinyagentos.installers.hf_multi_installer import (
    HFMultiInstaller,
    _file_excluded,
    _safe_relative_path,
    list_hf_repo_files,
)


class TestSafeRelativePath:
    """Path-traversal guard. HF rfilenames must always be repo-relative."""

    def test_normal_relative_path_ok(self):
        assert _safe_relative_path("config.json") == Path("config.json")
        assert _safe_relative_path("subdir/model.safetensors") == Path("subdir/model.safetensors")

    def test_absolute_path_rejected(self):
        assert _safe_relative_path("/etc/passwd") is None
        # Windows-style drive letters survive Path() but are still absolute.
        # We don't claim to handle that — just that the leading slash check fires.

    def test_dotdot_traversal_rejected(self):
        assert _safe_relative_path("../etc/passwd") is None
        assert _safe_relative_path("a/../b") is None
        assert _safe_relative_path("a/b/../../c") is None

    def test_empty_rejected(self):
        assert _safe_relative_path("") is None


class TestFileExcluded:
    def test_md_glob_matches_root(self):
        assert _file_excluded("README.md", ["*.md"])

    def test_md_glob_matches_nested(self):
        assert _file_excluded("docs/foo.md", ["*.md"])

    def test_does_not_match_unrelated(self):
        assert not _file_excluded("model.bin", ["*.md"])

    def test_matches_basename(self):
        # ``.gitattributes`` at the root or anywhere in the tree.
        assert _file_excluded(".gitattributes", [".gitattributes"])
        assert _file_excluded("subdir/.gitattributes", [".gitattributes"])


@pytest.fixture
def fake_repo_listing():
    """Return a stub HF API response covering the file shapes we care
    about: tiny config + tokenizer, larger weights file, an LFS-marked
    safetensors shard."""
    return {
        "siblings": [
            {"rfilename": "config.json", "size": 1234},
            {"rfilename": "tokenizer.json", "size": 5678},
            {"rfilename": "model.safetensors", "size": 1024 * 1024, "lfs": True},
            {"rfilename": "README.md", "size": 100},
            {"rfilename": ".gitattributes", "size": 50},
        ]
    }


def _stub_listing_client(files: dict):
    class _Stub:
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False
        async def get(self, *a, **kw):
            class _Resp:
                def raise_for_status(self): return None
                def json(self): return files
            return _Resp()
        async def aclose(self): return None
    return _Stub()


class TestHFMultiInstallerHappyPath:
    @pytest.mark.asyncio
    async def test_downloads_filtered_files(self, tmp_path, monkeypatch, fake_repo_listing):
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path / "models"))
        installer = HFMultiInstaller()

        downloaded: list[Path] = []

        async def fake_download(url, dest, expected_sha256=None, on_progress=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake")
            downloaded.append(dest)
            if on_progress:
                on_progress(len(b"fake"), len(b"fake"))

        with patch("tinyagentos.installers.hf_multi_installer.httpx.AsyncClient",
                   return_value=_stub_listing_client(fake_repo_listing)), \
             patch("tinyagentos.installers.hf_multi_installer.download_file",
                   side_effect=fake_download):
            result = await installer.install(
                "llama-3-8b-mlc",
                install_config={"backend": "mlc-llm"},
                variant={
                    "id": "q4f16",
                    "hf_repo": "mlc-ai/Llama-3-8B-Instruct-q4f16_1-MLC",
                    "multi_file": True,
                },
            )

        assert result["success"] is True
        # README.md and .gitattributes are excluded by default
        names = sorted(p.name for p in downloaded)
        assert names == ["config.json", "model.safetensors", "tokenizer.json"]
        assert result["files_downloaded"] == 3

    @pytest.mark.asyncio
    async def test_skips_already_downloaded_files(self, tmp_path, monkeypatch, fake_repo_listing):
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path / "models"))
        installer = HFMultiInstaller()

        # Pre-create config.json so the installer should skip it.
        target = tmp_path / "models" / "mlc-llm" / "llama" / "llama-3-8b-mlc"
        target.mkdir(parents=True)
        (target / "config.json").write_bytes(b"already here")

        downloaded: list[Path] = []

        async def fake_download(url, dest, expected_sha256=None, on_progress=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake")
            downloaded.append(dest)

        with patch("tinyagentos.installers.hf_multi_installer.httpx.AsyncClient",
                   return_value=_stub_listing_client(fake_repo_listing)), \
             patch("tinyagentos.installers.hf_multi_installer.download_file",
                   side_effect=fake_download):
            result = await installer.install(
                "llama-3-8b-mlc",
                install_config={"backend": "mlc-llm"},
                variant={"id": "q4f16", "hf_repo": "mlc-ai/X", "multi_file": True},
            )

        assert result["success"] is True
        # Only the un-existing files (tokenizer.json + model.safetensors) re-download
        names = sorted(p.name for p in downloaded)
        assert "config.json" not in names
        assert "tokenizer.json" in names
        assert "model.safetensors" in names


class TestHFMultiInstallerErrors:
    @pytest.mark.asyncio
    async def test_missing_variant_returns_error(self):
        result = await HFMultiInstaller().install("x", {}, variant=None)
        assert result["success"] is False
        assert "variant required" in result["error"]

    @pytest.mark.asyncio
    async def test_no_hf_repo_falls_back_to_download_installer(self, tmp_path, monkeypatch):
        """A variant with download_url but no hf_repo should be handled by
        the existing single-file DownloadInstaller — the multi-file path
        is opt-in.
        """
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path / "models"))

        called = {}

        class _StubDownload:
            async def install(self, app_id, install_config, variant=None, **kw):
                called["app_id"] = app_id
                called["variant"] = variant
                return {"success": True, "path": "fake"}

        with patch(
            "tinyagentos.installers.download_installer.DownloadInstaller",
            return_value=_StubDownload(),
        ):
            result = await HFMultiInstaller().install(
                "single-file-model",
                {"backend": "llama-cpp"},
                variant={"id": "q4", "download_url": "https://x/y.gguf"},
            )
        assert result["success"] is True
        assert called["app_id"] == "single-file-model"

    @pytest.mark.asyncio
    async def test_hf_api_failure_returns_error_envelope(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path / "models"))

        class _ErrClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *exc): return False
            async def get(self, *a, **kw):
                raise httpx.ConnectError("network down")
            async def aclose(self): return None

        with patch(
            "tinyagentos.installers.hf_multi_installer.httpx.AsyncClient",
            return_value=_ErrClient(),
        ):
            result = await HFMultiInstaller().install(
                "x", {"backend": "mlc-llm"},
                variant={"id": "q4f16", "hf_repo": "mlc-ai/Z", "multi_file": True},
            )

        assert result["success"] is False
        assert "failed to list files" in result["error"]


class TestPathTraversalGuard:
    @pytest.mark.asyncio
    async def test_traversal_rfilename_skipped_not_downloaded(
        self, tmp_path, monkeypatch
    ):
        """If the HF API response contains an rfilename that resolves outside
        target_dir, the installer must skip it (not download)."""
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path / "models"))
        downloaded: list[Path] = []

        async def fake_download(url, dest, expected_sha256=None, on_progress=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x")
            downloaded.append(dest)

        bad_listing = {
            "siblings": [
                {"rfilename": "../../../../etc/passwd", "size": 100},
                {"rfilename": "/etc/shadow", "size": 100},
                {"rfilename": "config.json", "size": 100},  # legit
            ]
        }
        with patch("tinyagentos.installers.hf_multi_installer.httpx.AsyncClient",
                   return_value=_stub_listing_client(bad_listing)), \
             patch("tinyagentos.installers.hf_multi_installer.download_file",
                   side_effect=fake_download):
            await HFMultiInstaller().install(
                "x", {"backend": "mlc-llm"},
                variant={"id": "q4", "hf_repo": "a/b", "multi_file": True},
            )

        # Only the legit file got through.
        names = sorted(p.name for p in downloaded)
        assert names == ["config.json"]


class TestUninstallResilience:
    @pytest.mark.asyncio
    async def test_locked_file_does_not_fail_whole_uninstall(self, tmp_path, monkeypatch):
        """A locked / un-deletable file should be reported in `failed` but
        the rest of the manifest dir should still get cleaned."""
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path / "models"))
        target = tmp_path / "models" / "mlc-llm" / "test" / "test-model"
        target.mkdir(parents=True)
        good = target / "config.json"
        bad = target / "model.safetensors"
        good.write_bytes(b"a")
        bad.write_bytes(b"b")

        original_unlink = Path.unlink

        def conditional_unlink(self, *args, **kwargs):
            if self.name == "model.safetensors":
                raise OSError(16, "Device or resource busy")
            return original_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", conditional_unlink):
            result = await HFMultiInstaller().uninstall("test-model")

        assert result["success"] is False  # there was a failure...
        assert "config.json" in result["deleted"]  # ...but the good file still went
        assert any("model.safetensors" in f["path"] for f in result["failed"])


class TestProgressAggregation:
    @pytest.mark.asyncio
    async def test_progress_is_cumulative_across_files(
        self, tmp_path, monkeypatch, fake_repo_listing
    ):
        """The installer's on_progress must report cumulative bytes across
        the whole repo, not per-file. Otherwise the install-progress bar
        resets every time a new file starts and looks broken to the user.
        """
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path / "models"))
        progress_seen: list[tuple[int, int]] = []

        def cb(done, total):
            progress_seen.append((done, total))

        async def fake_download(url, dest, expected_sha256=None, on_progress=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x" * 100)
            if on_progress:
                # Simulate streaming 100 bytes in two halves
                on_progress(50, 100)
                on_progress(100, 100)

        with patch("tinyagentos.installers.hf_multi_installer.httpx.AsyncClient",
                   return_value=_stub_listing_client(fake_repo_listing)), \
             patch("tinyagentos.installers.hf_multi_installer.download_file",
                   side_effect=fake_download):
            await HFMultiInstaller().install(
                "x", {"backend": "mlc-llm"},
                variant={"id": "q4", "hf_repo": "a/b", "multi_file": True},
                on_progress=cb,
            )

        # Cumulative bytes must be monotonically non-decreasing
        cumulative = [done for done, _ in progress_seen]
        assert cumulative == sorted(cumulative), (
            f"progress went backwards: {cumulative}"
        )
        # Final value should be at least the sum of the per-file totals
        # (100 bytes × 3 included files = 300)
        assert max(cumulative) >= 300
