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
    _compute_combined_hash,
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


class TestCombinedHashVerification:
    @pytest.mark.asyncio
    async def test_combined_hash_matches(self, tmp_path, monkeypatch, fake_repo_listing):
        """When the variant declares file_set_hash, the installer must verify a
        combined hash of the downloaded files after the transfer."""
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path / "models"))
        installer = HFMultiInstaller()

        async def fake_download(url, dest, expected_sha256=None, on_progress=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake")

        with patch("tinyagentos.installers.hf_multi_installer.httpx.AsyncClient",
                   return_value=_stub_listing_client(fake_repo_listing)), \
             patch("tinyagentos.installers.hf_multi_installer.download_file",
                   side_effect=fake_download):
            target_dir = tmp_path / "models" / "mlc-llm" / "llama" / "llama-3-8b-mlc"
            selected = [
                {"rfilename": "config.json", "size": 1234},
                {"rfilename": "model.safetensors", "size": 1048576, "lfs": True},
                {"rfilename": "tokenizer.json", "size": 5678},
            ]
            # Compute expected hash from what fake_download will write.
            for f in selected:
                rel = Path(f["rfilename"])
                local = target_dir / rel
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(b"fake")
            expected = _compute_combined_hash(target_dir, selected)
            # Clean the dir so install() re-creates the files.
            for f in selected:
                local = target_dir / Path(f["rfilename"])
                if local.exists():
                    local.unlink()
            result = await installer.install(
                "llama-3-8b-mlc",
                install_config={"backend": "mlc-llm"},
                variant={
                    "id": "q4f16",
                    "hf_repo": "mlc-ai/Llama-3-8B-Instruct-q4f16_1-MLC",
                    "multi_file": True,
                    "file_set_hash": expected,
                },
            )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_combined_hash_mismatch_fails(self, tmp_path, monkeypatch, fake_repo_listing):
        """A wrong file_set_hash in the variant must fail the install."""
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path / "models"))
        installer = HFMultiInstaller()

        async def fake_download(url, dest, expected_sha256=None, on_progress=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake")

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
                    "file_set_hash": "a" * 64,
                },
            )

        assert result["success"] is False
        assert "manifest hash mismatch" in result["error"]

    @pytest.mark.asyncio
    async def test_combined_hash_skips_unwritten_files(self, tmp_path, monkeypatch):
        """Files skipped by _safe_relative_path or already-present must not
        raise FileNotFoundError in the post-download hash; only files that
        were actually written are included in the combined hash."""
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path / "models"))
        installer = HFMultiInstaller()

        bad_listing = {
            "siblings": [
                {"rfilename": "../../../etc/passwd", "size": 100},
                {"rfilename": "config.json", "size": 1234},
                {"rfilename": "tokenizer.json", "size": 5678},
            ]
        }

        downloaded: list[Path] = []

        async def fake_download(url, dest, expected_sha256=None, on_progress=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake")
            downloaded.append(dest)

        with patch("tinyagentos.installers.hf_multi_installer.httpx.AsyncClient",
                   return_value=_stub_listing_client(bad_listing)), \
             patch("tinyagentos.installers.hf_multi_installer.download_file",
                   side_effect=fake_download):
            target_dir = tmp_path / "models" / "mlc-llm" / "llama" / "llama-3-8b-mlc"
            written = [
                {"rfilename": "config.json", "size": 1234},
                {"rfilename": "tokenizer.json", "size": 5678},
            ]
            for f in written:
                local = target_dir / Path(f["rfilename"])
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(b"fake")
            expected = _compute_combined_hash(target_dir, written)
            for f in written:
                local = target_dir / Path(f["rfilename"])
                if local.exists():
                    local.unlink()
            result = await installer.install(
                "llama-3-8b-mlc",
                install_config={"backend": "mlc-llm"},
                variant={
                    "id": "q4f16",
                    "hf_repo": "mlc-ai/Llama-3-8B-Instruct-q4f16_1-MLC",
                    "multi_file": True,
                    "file_set_hash": expected,
                },
            )

        assert result["success"] is True
        names = sorted(p.name for p in downloaded)
        assert names == ["config.json", "tokenizer.json"]


class TestPinAwareListing:
    @pytest.mark.asyncio
    async def test_nonexistent_revision_returns_error(self, tmp_path, monkeypatch):
        """A nonexistent revision must 404 on the revision-path endpoint,
        surfacing as an install error rather than silently returning main's
        listing."""
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path / "models"))

        class _NotFoundClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *exc): return False
            async def get(self, *a, **kw):
                import httpx
                resp = httpx.Response(404, text="revision not found")
                raise httpx.HTTPStatusError(
                    "404 Not Found", request=None, response=resp
                )
            async def aclose(self): return None

        with patch(
            "tinyagentos.installers.hf_multi_installer.httpx.AsyncClient",
            return_value=_NotFoundClient(),
        ):
            result = await HFMultiInstaller().install(
                "x", {"backend": "mlc-llm"},
                variant={
                    "id": "q4f16",
                    "hf_repo": "google/paligemma2-3b-mix-224",
                    "multi_file": True,
                    "hf_revision": "0" * 40,
                },
            )

        assert result["success"] is False
        assert "failed to list files" in result["error"]

    @pytest.mark.asyncio
    async def test_lfs_sha256_mismatch_returns_failure(self, tmp_path, monkeypatch):
        """When the listing carries lfs.sha256, a mismatch between the pinned
        hash and the on-disk file must fail the install with success=False."""
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path / "models"))

        listing_with_lfs = {
            "siblings": [
                {
                    "rfilename": "model.safetensors",
                    "size": 100,
                    "lfs": {"sha256": "abcd" * 16},
                },
            ]
        }

        async def fake_download(url, dest, expected_sha256=None, on_progress=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"wrong-content")

        with patch("tinyagentos.installers.hf_multi_installer.httpx.AsyncClient",
                   return_value=_stub_listing_client(listing_with_lfs)), \
             patch("tinyagentos.installers.hf_multi_installer.download_file",
                   side_effect=fake_download):
            result = await HFMultiInstaller().install(
                "x", {"backend": "mlc-llm"},
                variant={"id": "q4", "hf_repo": "a/b", "multi_file": True},
            )

        assert result["success"] is False
        assert "sha256 mismatch" in result["error"]

    @pytest.mark.asyncio
    async def test_changed_size_flips_file_set_hash(self, tmp_path, monkeypatch):
        """file_set_hash must depend on file sizes, not just filenames.
        Changing one size must produce a different hash, proving sizes
        participate in the pin."""
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path / "models"))

        selected_a = [
            {"rfilename": "config.json", "size": 1234},
            {"rfilename": "tokenizer.json", "size": 5678},
        ]
        selected_b = [
            {"rfilename": "config.json", "size": 1234},
            {"rfilename": "tokenizer.json", "size": 9999},
        ]

        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target_dir = Path(tmp)
            hash_a = _compute_combined_hash(target_dir, selected_a)
            hash_b = _compute_combined_hash(target_dir, selected_b)

        assert hash_a != hash_b, (
            "file_set_hash must change when a file size changes"
        )

    @pytest.mark.asyncio
    async def test_empty_downloaded_file_fails_sha_check(self, tmp_path, monkeypatch):
        """A download that lands 0 bytes (disk full, truncated write, empty
        200 body) must FAIL sha verification, not skip it. file_set_hash is
        computed from the listing, not local disk, so the per-file sha is the
        only check on what actually landed."""
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(tmp_path / "models"))

        listing_with_lfs = {
            "siblings": [
                {
                    "rfilename": "model.safetensors",
                    "size": 100,
                    "lfs": {"sha256": "abcd" * 16},
                },
            ]
        }

        async def fake_empty_download(url, dest, expected_sha256=None, on_progress=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"")

        with patch("tinyagentos.installers.hf_multi_installer.httpx.AsyncClient",
                   return_value=_stub_listing_client(listing_with_lfs)), \
             patch("tinyagentos.installers.hf_multi_installer.download_file",
                   side_effect=fake_empty_download):
            result = await HFMultiInstaller().install(
                "x", {"backend": "mlc-llm"},
                variant={"id": "q4", "hf_repo": "a/b", "multi_file": True},
            )

        assert result["success"] is False, (
            "a 0-byte downloaded file must fail sha verification, not pass unverified"
        )
        assert "sha256 mismatch" in result["error"]

    @pytest.mark.asyncio
    async def test_existing_empty_file_fails_sha_check(self, tmp_path, monkeypatch):
        """A pre-existing 0-byte file (crashed earlier run) must fail sha
        verification on the resume path, not be treated as already installed."""
        models_root = tmp_path / "models"
        monkeypatch.setenv("TAOS_MODELS_ROOT", str(models_root))

        listing_with_lfs = {
            "siblings": [
                {
                    "rfilename": "model.safetensors",
                    "size": 100,
                    "lfs": {"sha256": "abcd" * 16},
                },
            ]
        }

        async def fail_if_called(url, dest, expected_sha256=None, on_progress=None):
            raise AssertionError("existing file must not be re-downloaded silently")

        with patch("tinyagentos.installers.hf_multi_installer.httpx.AsyncClient",
                   return_value=_stub_listing_client(listing_with_lfs)), \
             patch("tinyagentos.installers.hf_multi_installer.download_file",
                   side_effect=fail_if_called):
            installer = HFMultiInstaller()
            target = installer._target_dir("mlc-llm", "x")
            target.mkdir(parents=True, exist_ok=True)
            (target / "model.safetensors").write_bytes(b"")
            result = await installer.install(
                "x", {"backend": "mlc-llm"},
                variant={"id": "q4", "hf_repo": "a/b", "multi_file": True},
            )

        assert result["success"] is False, (
            "a 0-byte existing file must fail sha verification on resume"
        )
        assert "sha256 mismatch" in result["error"]
