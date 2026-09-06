import pytest
from pathlib import Path

from tinyagentos.catalog_sync import sync_catalog


@pytest.mark.asyncio
class TestCatalogSync:
    async def test_non_git_dir_no_url(self, tmp_path):
        """sync_catalog returns error for non-git dir without repo_url."""
        result = await sync_catalog(tmp_path)
        assert result["success"] is False
        assert "Not a git repo" in result["error"]

    async def test_non_git_dir_with_url_bad_repo(self, tmp_path):
        """sync_catalog returns error for a bad clone URL."""
        target = tmp_path / "catalog"
        result = await sync_catalog(target, repo_url="file:///nonexistent/repo.git")
        assert result["success"] is False
        assert result.get("error")

    async def test_git_dir_pull(self, tmp_path):
        """sync_catalog pulls when .git exists (use a real tiny git repo)."""
        import asyncio
        import subprocess

        # Create a source repo with at least one commit
        source = tmp_path / "source"
        source.mkdir()
        subprocess.run(["git", "init", str(source)], capture_output=True)
        subprocess.run(["git", "-C", str(source), "config", "user.email", "test@test.com"], capture_output=True)
        subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], capture_output=True)
        (source / "README.md").write_text("hello")
        subprocess.run(["git", "-C", str(source), "add", "."], capture_output=True)
        subprocess.run(["git", "-C", str(source), "commit", "-m", "init"], capture_output=True)

        # Clone it
        clone = tmp_path / "catalog"
        subprocess.run(["git", "clone", str(source), str(clone)], capture_output=True)

        result = await sync_catalog(clone)
        assert result["success"] is True
        assert result["action"] == "pulled"
