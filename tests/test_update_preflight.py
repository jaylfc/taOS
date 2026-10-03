import asyncio
import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestUpdatePreflight:
    """Test preflight validation for taOS updates."""

    def test_check_preflight_valid_checkout(self, tmp_path):
        """A clean default clone should have no problems."""
        from tinyagentos.update_preflight import check_preflight

        # Create a mock git repo structure
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        # Mock the necessary git commands
        with patch("tinyagentos.update_preflight._ls_remote_heads") as mock_ls_remote:
            mock_ls_remote.return_value = (True, True)  # Remote reachable, branch exists
            with patch("tinyagentos.update_preflight._get_fetch_refspecs") as mock_get_specs:
                mock_get_specs.return_value = ["+refs/heads/*:refs/remotes/origin/*"]  # Default glob
                issues = check_preflight(tmp_path)

                assert issues == []

    def test_check_preflight_branch_not_on_origin(self, tmp_path):
        """Branch missing on origin should be detected."""
        from tinyagentos.update_preflight import check_preflight

        with patch("tinyagentos.update_preflight._ls_remote_heads") as mock_ls_remote:
            mock_ls_remote.return_value = (True, False)  # Remote reachable, but branch not found

            issues = check_preflight(tmp_path)

            # Should have branch_not_on_origin error
            branch_issues = [i for i in issues if i.code == "branch_not_on_origin"]
            assert len(branch_issues) == 1
            assert "branch" in branch_issues[0].message.lower()

    def test_check_preflight_narrow_fetch_refspec(self, tmp_path):
        """Narrow remote.origin.fetch should be detected."""
        from tinyagentos.update_preflight import check_preflight

        with patch("tinyagentos.update_preflight._ls_remote_heads") as mock_ls_remote:
            mock_ls_remote.return_value = (True, True)  # Remote reachable, branch exists

            with patch("tinyagentos.update_preflight._get_fetch_refspecs") as mock_get_specs:
                # Mock narrow refspec that doesn't cover our branch
                mock_get_specs.return_value = ["+refs/heads/feat/x:refs/remotes/origin/feat/x"]

                issues = check_preflight(tmp_path)

                # Should have narrow_fetch_refspec error
                narrow_issues = [i for i in issues if i.code == "narrow_fetch_refspec"]
                assert len(narrow_issues) == 1
                assert "fetch" in narrow_issues[0].message.lower()

    def test_check_preflight_foreign_owned_files(self, tmp_path):
        """Foreign-owned files should be detected."""
        from tinyagentos.update_preflight import check_preflight, _find_foreign_owned_files

        # Create a file with different owner (simulate root-owned file)
        test_file = tmp_path / "test_file.py"
        test_file.write_text("test")

        # Mock os.geteuid to simulate a non-root service user.
        with patch("os.geteuid", return_value=1000):
            # Report root ownership for the planted file. A real stat_result
            # is required because pathlib calls S_ISREG on the result.
            real = os.stat(test_file)
            mock_stat = os.stat_result(
                (real.st_mode, real.st_ino, real.st_dev, real.st_nlink,
                 0, real.st_gid, real.st_size, real.st_atime,
                 real.st_mtime, real.st_ctime)  # uid=0 (root)
            )
            with patch("pathlib.Path.stat", return_value=mock_stat):
                count, paths = _find_foreign_owned_files(tmp_path)

                assert count > 0
                assert len(paths) > 0

    def test_auto_repair_narrow_refspec(self, tmp_path):
        """Auto-repair narrow_fetch_refspec by adding branch refspec."""
        from tinyagentos.update_preflight import auto_repair_narrow_refspec

        # Mock git config --add to succeed
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            # Mock resolve_tracked_branch to return a branch
            with patch("tinyagentos.update_preflight.resolve_tracked_branch") as mock_resolve:
                mock_resolve.return_value = "dev"

                # Mock _get_fetch_refspecs to return empty list (needs repair)
                with patch("tinyagentos.update_preflight._get_fetch_refspecs") as mock_get_specs:
                    mock_get_specs.return_value = []

                    result = auto_repair_narrow_refspec(tmp_path)
                    assert result is True

                    # Verify git config --add was called with correct args
                    mock_run.assert_called()
                    call_args = mock_run.call_args[0][0]
                    assert "git" in call_args[0]
                    assert "config" in call_args[1]
                    assert "--add" in call_args[2]

    @pytest.mark.asyncio
    async def test_check_for_updates_with_preflight_errors(self, client, monkeypatch):
        """check_for_updates should return preflight_errors when issues are found."""
        from tinyagentos.update_preflight import PreflightIssue

        # Mock the check_preflight function to return issues
        mock_issues = [
            PreflightIssue(
                code="branch_not_on_origin", 
                message="Branch not on origin", 
                repair="Fix it"
            )
        ]

        with patch("tinyagentos.update_preflight.check_preflight") as mock_check:
            mock_check.return_value = mock_issues

            resp = await client.get("/api/settings/update-check")

            assert resp.status_code == 200
            data = resp.json()
            assert data["has_updates"] is False
            assert "preflight_errors" in data
            assert len(data["preflight_errors"]) == 1
            # PreflightIssue is a NamedTuple which serializes to a tuple: (code, message, repair)
            # Access code from index 0 of the tuple
            assert data["preflight_errors"][0][0] == "branch_not_on_origin"

    @pytest.mark.asyncio
    async def test_apply_update_with_preflight_errors(self, client, monkeypatch):
        """apply_update should return 409 with preflight errors and not modify tree."""
        from tinyagentos.update_preflight import PreflightIssue

        # Mock the check_preflight function to return issues
        mock_issues = [
            PreflightIssue(
                code="foreign_owned_files", 
                message="Foreign owned files", 
                repair="Change ownership"
            )
        ]

        with patch("tinyagentos.update_preflight.check_preflight") as mock_check:
            mock_check.return_value = mock_issues

            # Mock stash to ensure it's NOT called when preflight fails
            with patch("tinyagentos.routes.settings._stash_local_source_changes") as mock_stash:
                resp = await client.post("/api/settings/update")

                assert resp.status_code == 409
                data = resp.json()
                assert "preflight_errors" in data
                assert len(data["preflight_errors"]) == 1
                # PreflightIssue is a NamedTuple which serializes to a tuple: (code, message, repair)
                # Access code from index 0 of the tuple
                assert data["preflight_errors"][0][0] == "foreign_owned_files"

                # Verify stash was NOT called (update blocked early)
                mock_stash.assert_not_called()

    def test_narrow_refspec_auto_repair_success(self, tmp_path):
        """Narrow refspec auto-repair should work when safe."""
        from tinyagentos.update_preflight import _covers_ref

        refspecs = ["+refs/heads/feat/x:refs/remotes/origin/feat/x"]
        expected_ref = "refs/heads/dev"

        # _covers_ref should return False for non-matching refspecs
        assert not _covers_ref(expected_ref, refspecs)

    def test_ls_remote_heads_remote_unreachable(self, tmp_path):
        """When git ls-remote fails, treat as remote unreachable, not branch missing."""
        from tinyagentos.update_preflight import _ls_remote_heads

        # Mock subprocess.run to raise an exception (simulating network error)
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("Network error")

            reachable, exists = _ls_remote_heads("origin", "dev")

            assert not reachable
            assert not exists

    def test_empty_fetch_refspecs(self, tmp_path):
        """Empty git config for remote.origin.fetch should be detected."""
        from tinyagentos.update_preflight import _get_fetch_refspecs

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ""
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            refspecs = _get_fetch_refspecs(tmp_path)

            assert refspecs == []

    def test_valid_fetch_refspecs(self, tmp_path):
        """Valid fetch refspecs should be parsed correctly."""
        from tinyagentos.update_preflight import _get_fetch_refspecs

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "+refs/heads/*:refs/remotes/origin/*\n+refs/tags/*:refs/tags/*"
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            refspecs = _get_fetch_refspecs(tmp_path)

            assert len(refspecs) == 2
            assert "refs/heads/*" in refspecs[0]
            assert "refs/tags/*" in refspecs[1]

    def test_check_preflight_accepts_str_path(self, tmp_path):
        """The update-check route passes project_dir as a str; coercion must not
        crash and the foreign-file scan must actually run over a real repo.

        A genuine temp git repository is created with a tracked file plus an
        untracked file that is foreign to the running user. The preflight is
        called with ``str(repo)`` exactly as the settings route does, and the
        asserted issue shows the scan ran rather than an empty short-circuit.

        Ownership is simulated by patching ``os.stat`` (called by ``Path.stat``
        through the accessor), so every file reports root ownership and is
        foreign to the non-root service user. This test fails without the
        coercion fix with ``AttributeError: 'str' object has no attribute
        'rglob'``, and also fails if the scan never runs or the str is not
        normalised.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                    ["git", "config", "user.name", "t"], ["git", "checkout", "-q", "-b", "dev"]):
            subprocess.run(cmd, cwd=repo, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (repo / "code.py").write_text("v1")
        subprocess.run(["git", "add", "."], cwd=repo, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (repo / "foreign.txt").write_text("owned by others")  # untracked, foreign

        _real_stat = os.stat

        def foreign_stat(path, *args, **kwargs):
            # Report root ownership for every file -> all files are foreign
            # to the non-root service user.
            real = _real_stat(path, *args, **kwargs)
            return os.stat_result(
                (real.st_mode, real.st_ino, real.st_dev, real.st_nlink, 0,
                 real.st_gid, real.st_size, real.st_atime,
                 real.st_mtime, real.st_ctime)
            )

        with patch("tinyagentos.update_preflight._ls_remote_heads") as mock_ls_remote:
            mock_ls_remote.return_value = (True, True)  # remote reachable, branch exists
            with patch("tinyagentos.update_preflight._get_fetch_refspecs") as mock_get_specs:
                mock_get_specs.return_value = [
                    "+refs/heads/*:refs/remotes/origin/*",
                    "+refs/tags/*:refs/tags/*",
                ]  # realistic two-refspec config
                with patch("os.geteuid", return_value=1000):  # service user, not root
                    with patch("os.stat", side_effect=foreign_stat):
                        from tinyagentos.update_preflight import check_preflight

                        issues = check_preflight(str(repo))

        foreign_issues = [i for i in issues if i.code == "foreign_owned_files"]
        assert foreign_issues
        assert "foreign.txt" in foreign_issues[0].message
