"""Tests for the macOS launchd plist migration function.

The migration rewrites old bare-uvicorn plists (from pre-#3313 installs) to use
the package entrypoint `python -m tinyagentos`, which starts the LLM gateway
agent listener. Without this, local agent deploys are refused because a
bare-uvicorn controller has no verified gateway port.
"""

from __future__ import annotations

import plistlib
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest


def _make_old_uvicorn_plist(
    install_dir: str = "/Users/test/tinyagentos",
    host: str = "0.0.0.0",
    port: int = 6969,
) -> bytes:
    """Create a plist with bare uvicorn ProgramArguments (old installer format)."""
    plist = {
        "Label": "com.tinyagentos.controller",
        "ProgramArguments": [
            f"{install_dir}/.venv/bin/python",
            "-m",
            "uvicorn",
            "tinyagentos.app:create_app",
            f"--host={host}",
            f"--port={port}",
        ],
        "WorkingDirectory": install_dir,
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": f"{install_dir}/controller.log",
        "StandardErrorPath": f"{install_dir}/controller.err",
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "TAOS_BROWSER_PROXY_PORT": "6970",
            "TAOS_SPA_DIR": f"{install_dir}/static/desktop",
        },
    }
    return plistlib.dumps(plist, fmt=plistlib.FMT_XML)


def _make_migrated_plist(
    install_dir: str = "/Users/test/tinyagentos",
    host: str = "0.0.0.0",
    port: int = 6969,
) -> bytes:
    """Create a plist already using the new entrypoint (idempotent test)."""
    plist = {
        "Label": "com.tinyagentos.controller",
        "ProgramArguments": [
            f"{install_dir}/.venv/bin/python",
            "-m",
            "tinyagentos",
        ],
        "WorkingDirectory": install_dir,
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": f"{install_dir}/controller.log",
        "StandardErrorPath": f"{install_dir}/controller.err",
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "TAOS_HOST": host,
            "TAOS_PORT": str(port),
            "TAOS_BROWSER_PROXY_PORT": "6970",
            "TAOS_SPA_DIR": f"{install_dir}/static/desktop",
        },
    }
    return plistlib.dumps(plist, fmt=plistlib.FMT_XML)


def _make_plist_with_tinyagentos_app_module(
    install_dir: str = "/Users/test/tinyagentos",
    host: str = "0.0.0.0",
    port: int = 6969,
) -> bytes:
    """Create a plist with uvicorn and tinyagentos.app: module reference (another old pattern)."""
    plist = {
        "Label": "com.tinyagentos.controller",
        "ProgramArguments": [
            f"{install_dir}/.venv/bin/python",
            "-m",
            "uvicorn",
            "tinyagentos.app:app",
            f"--host={host}",
            f"--port={port}",
        ],
        "WorkingDirectory": install_dir,
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": f"{install_dir}/controller.log",
        "StandardErrorPath": f"{install_dir}/controller.err",
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "TAOS_BROWSER_PROXY_PORT": "6970",
            "TAOS_SPA_DIR": f"{install_dir}/static/desktop",
        },
    }
    return plistlib.dumps(plist, fmt=plistlib.FMT_XML)


class TestMigrateLaunchdPlist:
    """Tests for the pure migration function."""

    @pytest.mark.asyncio
    async def test_old_uvicorn_plist_migrates_to_module_entrypoint(self):
        """An old bare-uvicorn plist migrates to `-m tinyagentos` with TAOS_HOST/PORT."""
        from tinyagentos.launchd_migration import migrate_launchd_plist

        old_plist = _make_old_uvicorn_plist()
        result = migrate_launchd_plist(old_plist, install_dir="/Users/test/tinyagentos")

        assert result is not None, "migration should return new plist bytes"
        parsed = plistlib.loads(result)

        # ProgramArguments rewritten to module entrypoint
        assert parsed["ProgramArguments"] == [
            "/Users/test/tinyagentos/.venv/bin/python",
            "-m",
            "tinyagentos",
        ], f"ProgramArguments: {parsed['ProgramArguments']}"

        # EnvironmentVariables has TAOS_HOST and TAOS_PORT from old --host/--port args
        env = parsed["EnvironmentVariables"]
        assert env["TAOS_HOST"] == "0.0.0.0"
        assert env["TAOS_PORT"] == "6969"

        # Other env vars preserved
        assert env["PYTHONUNBUFFERED"] == "1"
        assert env["TAOS_BROWSER_PROXY_PORT"] == "6970"
        assert env["TAOS_SPA_DIR"] == "/Users/test/tinyagentos/static/desktop"

        # All other keys preserved
        assert parsed["Label"] == "com.tinyagentos.controller"
        assert parsed["WorkingDirectory"] == "/Users/test/tinyagentos"
        assert parsed["RunAtLoad"] is True
        assert parsed["KeepAlive"] is True
        assert parsed["StandardOutPath"] == "/Users/test/tinyagentos/controller.log"
        assert parsed["StandardErrorPath"] == "/Users/test/tinyagentos/controller.err"

    @pytest.mark.asyncio
    async def test_plist_with_tinyagentos_app_module_reference_migrates(self):
        """A plist with `uvicorn tinyagentos.app:app` also migrates."""
        from tinyagentos.launchd_migration import migrate_launchd_plist

        old_plist = _make_plist_with_tinyagentos_app_module()
        result = migrate_launchd_plist(old_plist, install_dir="/Users/test/tinyagentos")

        assert result is not None
        parsed = plistlib.loads(result)

        assert parsed["ProgramArguments"] == [
            "/Users/test/tinyagentos/.venv/bin/python",
            "-m",
            "tinyagentos",
        ]

        env = parsed["EnvironmentVariables"]
        assert env["TAOS_HOST"] == "0.0.0.0"
        assert env["TAOS_PORT"] == "6969"

    @pytest.mark.asyncio
    async def test_already_migrated_plist_returns_none_idempotent(self):
        """An already-migrated plist returns None (idempotent)."""
        from tinyagentos.launchd_migration import migrate_launchd_plist

        migrated_plist = _make_migrated_plist()
        result = migrate_launchd_plist(migrated_plist, install_dir="/Users/test/tinyagentos")

        assert result is None, "already-migrated plist should return None"

    @pytest.mark.asyncio
    async def test_non_tinyagentos_plist_returns_none(self):
        """A plist for a different service returns None (no-op)."""
        from tinyagentos.launchd_migration import migrate_launchd_plist

        other_plist = {
            "Label": "com.other.service",
            "ProgramArguments": ["/usr/bin/python", "-m", "uvicorn", "app:app"],
        }
        result = migrate_launchd_plist(plistlib.dumps(other_plist), install_dir="/any/path")
        assert result is None

    @pytest.mark.asyncio
    async def test_preserves_all_other_keys_and_env_vars(self):
        """All keys and env vars not explicitly migrated are preserved."""
        from tinyagentos.launchd_migration import migrate_launchd_plist

        # Add extra custom keys and env vars
        plist = {
            "Label": "com.tinyagentos.controller",
            "ProgramArguments": [
                "/Users/test/tinyagentos/.venv/bin/python",
                "-m",
                "uvicorn",
                "tinyagentos.app:create_app",
                "--host=127.0.0.1",
                "--port=8080",
            ],
            "WorkingDirectory": "/Users/test/tinyagentos",
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": "/Users/test/tinyagentos/controller.log",
            "StandardErrorPath": "/Users/test/tinyagentos/controller.err",
            "EnvironmentVariables": {
                "PYTHONUNBUFFERED": "1",
                "TAOS_BROWSER_PROXY_PORT": "6970",
                "TAOS_SPA_DIR": "/Users/test/tinyagentos/static/desktop",
                "CUSTOM_VAR": "custom_value",
                "ANOTHER_VAR": "another_value",
            },
            "CustomKey": "custom_value",
            "AnotherKey": 42,
        }
        old_bytes = plistlib.dumps(plist, fmt=plistlib.FMT_XML)
        result = migrate_launchd_plist(old_bytes, install_dir="/Users/test/tinyagentos")

        assert result is not None
        parsed = plistlib.loads(result)

        # Custom env vars preserved
        assert parsed["EnvironmentVariables"]["CUSTOM_VAR"] == "custom_value"
        assert parsed["EnvironmentVariables"]["ANOTHER_VAR"] == "another_value"

        # Custom top-level keys preserved
        assert parsed["CustomKey"] == "custom_value"
        assert parsed["AnotherKey"] == 42

    @pytest.mark.asyncio
    async def test_invalid_plist_returns_none_no_crash(self):
        """Invalid plist bytes return None without raising (safety)."""
        from tinyagentos.launchd_migration import migrate_launchd_plist

        result = migrate_launchd_plist(b"not a valid plist", install_dir="/any/path")
        assert result is None


class TestDarwinGate:
    """Tests that the migration is only called on Darwin."""

    def test_update_calls_migration_only_on_darwin(self, monkeypatch):
        """The update path calls _maybe_migrate_launchd_plist only when sys.platform == 'darwin'."""
        from unittest.mock import patch

        # Track calls to the internal migrate function
        calls = []

        def fake_migrate(plist_bytes, install_dir):
            calls.append((plist_bytes, install_dir))
            return None  # already migrated

        # Monkeypatch sys.platform
        for platform in ("darwin", "linux", "win32"):
            calls.clear()
            monkeypatch.setattr("sys.platform", platform)

            with patch(
                "tinyagentos.launchd_migration.migrate_launchd_plist",
                new=fake_migrate,
            ):
                # Simulate the update path calling the migration
                from tinyagentos.launchd_migration import _maybe_migrate_launchd_plist

                # This function wraps the platform check
                _maybe_migrate_launchd_plist(b"dummy", "/install/dir")

            if platform == "darwin":
                assert len(calls) == 1, f"migration should be called on {platform}"
            else:
                assert len(calls) == 0, f"migration should NOT be called on {platform}"


class TestApplyLaunchdMigrationNoSubprocess:
    """apply_launchd_migration must not call launchctl from the controller process."""

    def test_apply_launchd_migration_spawns_no_launchctl(self, tmp_path, monkeypatch):
        """apply_launchd_migration on an old plist must NOT call any launchctl subprocess."""
        import asyncio
        from unittest.mock import patch, MagicMock, AsyncMock

        plist_path = tmp_path / "com.tinyagentos.controller.plist"
        old_plist = _make_old_uvicorn_plist(install_dir="/tmp/test")
        plist_path.write_bytes(old_plist)

        helper_path = tmp_path / "com.tinyagentos.plist-reload.plist"
        monkeypatch.setattr(
            "tinyagentos.launchd_migration.PLIST_PATH",
            plist_path,
        )
        monkeypatch.setattr(
            "tinyagentos.launchd_migration.HELPER_PLIST_PATH",
            helper_path,
        )
        monkeypatch.setattr("sys.platform", "darwin")

        subprocess_calls = []

        async def fake_create_subprocess_exec(*args, **kwargs):
            subprocess_calls.append(args)
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.wait = AsyncMock()
            return mock_proc

        from tinyagentos.launchd_migration import apply_launchd_migration

        with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
            result = asyncio.run(apply_launchd_migration("/tmp/test"))

        launchctl_calls = [c for c in subprocess_calls if c and c[0] == "launchctl"]
        assert len(launchctl_calls) == 0, (
            f"Expected no launchctl calls, got: {launchctl_calls}"
        )


class TestAtomicWrite:
    """Atomic write must not clobber the live plist on failure."""

    def test_write_failure_leaves_original_plist_in_place(self, tmp_path, monkeypatch):
        """If the atomic write fails, the original plist must not be touched."""
        plist_path = tmp_path / "com.tinyagentos.controller.plist"
        original_plist = _make_old_uvicorn_plist(install_dir="/tmp/test")
        plist_path.write_bytes(original_plist)

        helper_path = tmp_path / "com.tinyagentos.plist-reload.plist"
        monkeypatch.setattr(
            "tinyagentos.launchd_migration.PLIST_PATH",
            plist_path,
        )
        monkeypatch.setattr(
            "tinyagentos.launchd_migration.HELPER_PLIST_PATH",
            helper_path,
        )
        monkeypatch.setattr("sys.platform", "darwin")

        # Import atomic_write_bytes to patch it where it's actually used
        from tinyagentos.launchd_migration import atomic_write_bytes

        original_atomic_write_bytes = atomic_write_bytes

        def fake_atomic_write_bytes(path, data, *, mode=None):
            # Simulate a write failure
            raise OSError("simulated write failure")

        with patch("tinyagentos.launchd_migration.atomic_write_bytes", side_effect=fake_atomic_write_bytes):
            from tinyagentos.launchd_migration import apply_launchd_migration
            import asyncio

            result = asyncio.run(apply_launchd_migration("/tmp/test"))

        assert plist_path.read_bytes() == original_plist, (
            "Original plist was modified despite write failure"
        )


# Helper to run RED-FIRST: we write a stub that returns None
# The tests above will fail until the real implementation exists.
# We also need the _maybe_migrate_launchd_plist function in settings.py

if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestApplyLaunchdMigrationRedFirst:
    """RED-FIRST tests for B1 (backup order) and B2 (quoted paths in reload helper)."""

    def test_bak_holds_original_bytes_before_overwrite(self, tmp_path, monkeypatch):
        """The .bak must contain the ORIGINAL plist bytes, not the migrated ones."""
        import asyncio
        import shutil

        plist_path = tmp_path / "com.tinyagentos.controller.plist"
        old_plist = _make_old_uvicorn_plist(install_dir="/tmp/test")
        plist_path.write_bytes(old_plist)

        helper_path = tmp_path / "com.tinyagentos.plist-reload.plist"
        monkeypatch.setattr(
            "tinyagentos.launchd_migration.PLIST_PATH",
            plist_path,
        )
        monkeypatch.setattr(
            "tinyagentos.launchd_migration.HELPER_PLIST_PATH",
            helper_path,
        )
        monkeypatch.setattr("sys.platform", "darwin")

        from tinyagentos.launchd_migration import apply_launchd_migration

        result = asyncio.run(apply_launchd_migration("/tmp/test"))
        assert result[0] is True, f"migration should succeed: {result}"

        bak_path = plist_path.with_suffix(".plist.bak")
        assert bak_path.exists(), ".bak file was not created"
        assert bak_path.read_bytes() == old_plist, (
            ".bak must contain original bytes, not the migrated plist"
        )

    def test_write_reload_helper_quotes_paths_with_spaces(self, tmp_path, monkeypatch):
        """Paths with spaces in _write_reload_helper must be shlex.quoted."""
        import shlex
        from tinyagentos.launchd_migration import HELPER_PLIST_PATH as REAL_HELPER_PATH

        controller_plist = tmp_path / "path with spaces" / "controller.plist"
        controller_plist.parent.mkdir(parents=True)
        controller_plist.write_bytes(b"dummy")

        helper_path = tmp_path / "helper.plist"
        monkeypatch.setattr(
            "tinyagentos.launchd_migration.HELPER_PLIST_PATH",
            helper_path,
        )

        from tinyagentos.launchd_migration import _write_reload_helper

        _write_reload_helper(controller_plist)

        assert helper_path.exists(), "helper plist was not written"
        helper_plist = plistlib.loads(helper_path.read_bytes())
        script = helper_plist["ProgramArguments"][2]

        # shlex.split must preserve the full quoted path as a single token
        tokens = shlex.split(script)
        matching = [t for t in tokens if t.startswith(str(controller_plist))]
        assert len(matching) == 1, (
            f"controller plist path must be a single token in shlex.split output, "
            f"tokens: {tokens}"
        )


class TestHelperRetryLogic:
    """RED-FIRST tests for B1: helper script retries bootstrap and only deletes itself on success."""

    def test_helper_script_retries_bootstrap_and_preserves_plist_on_failure(
        self, tmp_path, monkeypatch
    ):
        """Helper script must retry bootstrap up to 5 times and only delete helper plist on success.

        If bootstrap fails all retries, the helper plist must remain so RunAtLoad re-runs it.
        """
        import asyncio
        import shlex
        import subprocess
        import os
        import stat

        # Create a fake launchctl that fails on 'bootstrap' but succeeds on other commands
        fake_launchctl = tmp_path / "launchctl"
        fake_launchctl.write_text(
            """#!/bin/sh
# Fake launchctl for testing
# Fails on 'bootstrap' command, succeeds on others
if [ "$1" = "bootstrap" ]; then
    echo "fake launchctl: bootstrap failed" >&2
    exit 1
fi
if [ "$1" = "print" ]; then
    # Simulate service not found on print (controller not loaded)
    echo "fake launchctl: print failed - service not found" >&2
    exit 1
fi
if [ "$1" = "bootout" ]; then
    # bootout succeeds
    exit 0
fi
# Unknown command
exit 0
"""
        )
        fake_launchctl.chmod(fake_launchctl.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        # Put fake launchctl first on PATH
        monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")

        controller_plist = tmp_path / "controller.plist"
        controller_plist.write_bytes(b"dummy")

        helper_path = tmp_path / "com.tinyagentos.plist-reload.plist"
        monkeypatch.setattr(
            "tinyagentos.launchd_migration.HELPER_PLIST_PATH",
            helper_path,
        )
        monkeypatch.setattr("sys.platform", "darwin")

        from tinyagentos.launchd_migration import _write_reload_helper

        _write_reload_helper(controller_plist)

        assert helper_path.exists(), "helper plist was not written"
        helper_plist = plistlib.loads(helper_path.read_bytes())
        script = helper_plist["ProgramArguments"][2]

        # Run the helper script with our fake launchctl
        # The script should retry bootstrap 5 times, then leave the helper plist in place
        result = subprocess.run(
            ["/bin/sh", "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # The helper plist should STILL EXIST because bootstrap failed
        assert helper_path.exists(), (
            f"Helper plist was deleted despite bootstrap failure! "
            f"Script stdout: {result.stdout}, stderr: {result.stderr}, returncode: {result.returncode}"
        )

class TestFlaglessPlistNotMigrated:
    """A plist with no --host/--port must NOT be migrated.

    Bare uvicorn without --host/--port binds 127.0.0.1:8000. Migrating it with
    the TAOS_HOST=0.0.0.0/TAOS_PORT=6969 defaults would WIDEN exposure, so the
    plist is left alone and the caller is told to re-run the installer.
    """

    def test_flagless_plist_returns_none_and_warns(self, tmp_path, monkeypatch):
        """migrate_launchd_plist returns None and apply surfaces a warning naming the plist."""
        import asyncio
        from tinyagentos.launchd_migration import apply_launchd_migration, migrate_launchd_plist

        plist = {
            "Label": "com.tinyagentos.controller",
            "ProgramArguments": [
                "/tmp/test/.venv/bin/python",
                "-m",
                "uvicorn",
                "tinyagentos.app:create_app",
            ],
            "WorkingDirectory": "/tmp/test",
            "RunAtLoad": True,
            "KeepAlive": True,
            "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
        }
        original = plistlib.dumps(plist, fmt=plistlib.FMT_XML)

        assert migrate_launchd_plist(original, install_dir="/tmp/test") is None, (
            "flagless plist must not be migrated: defaulting to "
            "TAOS_HOST=0.0.0.0/TAOS_PORT=6969 would widen exposure beyond "
            "uvicorn's 127.0.0.1:8000 default"
        )

        plist_path = tmp_path / "com.tinyagentos.controller.plist"
        plist_path.write_bytes(original)
        monkeypatch.setattr("tinyagentos.launchd_migration.PLIST_PATH", plist_path)
        monkeypatch.setattr(
            "tinyagentos.launchd_migration.HELPER_PLIST_PATH",
            tmp_path / "com.tinyagentos.plist-reload.plist",
        )
        monkeypatch.setattr("sys.platform", "darwin")

        success, warning = asyncio.run(apply_launchd_migration("/tmp/test"))

        assert success is True
        assert warning is not None, "a skipped flagless plist must surface a warning"
        assert str(plist_path) in warning, f"warning must name the plist: {warning}"
        assert plist_path.read_bytes() == original, "flagless plist was modified on disk"


class TestApplyLaunchdMigrationPrepFailure:
    """If the post-replace prep fails, the original plist must be restored."""

    def test_reload_prep_failure_restores_original_plist(self, tmp_path, monkeypatch):
        """A failing _write_reload_helper leaves the ORIGINAL bytes on disk."""
        import asyncio
        from tinyagentos.launchd_migration import apply_launchd_migration

        plist_path = tmp_path / "com.tinyagentos.controller.plist"
        original = _make_old_uvicorn_plist(install_dir="/tmp/test")
        plist_path.write_bytes(original)

        monkeypatch.setattr("tinyagentos.launchd_migration.PLIST_PATH", plist_path)
        monkeypatch.setattr(
            "tinyagentos.launchd_migration.HELPER_PLIST_PATH",
            tmp_path / "com.tinyagentos.plist-reload.plist",
        )
        monkeypatch.setattr("sys.platform", "darwin")

        with patch(
            "tinyagentos.launchd_migration._write_reload_helper",
            side_effect=OSError("simulated reload prep failure"),
        ):
            success, warning = asyncio.run(apply_launchd_migration("/tmp/test"))

        assert plist_path.read_bytes() == original, (
            "a new-format plist with no reload scheduled is seen as migrated by "
            "later updates and never retried, so the original must be restored"
        )
        assert success is True
        assert warning is not None, f"prep failure must surface a warning, got {warning}"
