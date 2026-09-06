import os
import stat
from pathlib import Path

import pytest

from tinyagentos.installers.script_installer import ScriptInstaller


@pytest.fixture
def fake_script(tmp_path: Path) -> Path:
    """A trivial bash script that writes a marker file and exits 0."""
    script_path = tmp_path / "scripts" / "install-fake.sh"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("#!/bin/bash\nset -e\necho installed > \"$1/marker\"\n")
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
    return script_path


@pytest.fixture
def failing_script(tmp_path: Path) -> Path:
    p = tmp_path / "scripts" / "install-bust.sh"
    p.parent.mkdir(parents=True)
    p.write_text("#!/bin/bash\necho boom 1>&2\nexit 7\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


class TestScriptInstallerInstall:
    @pytest.mark.asyncio
    async def test_runs_script_with_app_id(self, tmp_path, fake_script):
        installer = ScriptInstaller(project_dir=tmp_path)
        result = await installer.install(
            "fake-svc",
            install_config={"method": "script", "script": str(fake_script.relative_to(tmp_path))},
        )
        assert result["success"] is True
        # The marker the script wrote uses arg $1 as a writable dir.
        # ScriptInstaller passes app_id and project_dir as args; verify the
        # script ran by checking it produced a marker we can find.
        # Implementation detail: ScriptInstaller passes (app_id, project_dir)
        # so marker should be at <project_dir>/marker.
        assert (tmp_path / "marker").read_text().strip() == "installed"

    @pytest.mark.asyncio
    async def test_returns_failure_with_stderr_when_script_fails(self, tmp_path, failing_script):
        installer = ScriptInstaller(project_dir=tmp_path)
        result = await installer.install(
            "bust-svc",
            install_config={"method": "script", "script": str(failing_script.relative_to(tmp_path))},
        )
        assert result["success"] is False
        assert "boom" in result.get("error", "") or "rc=7" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_missing_script_path_returns_error(self, tmp_path):
        installer = ScriptInstaller(project_dir=tmp_path)
        result = await installer.install(
            "x",
            install_config={"method": "script"},  # no `script` key
        )
        assert result["success"] is False
        assert "script" in result.get("error", "").lower()


class TestScriptInstallerUninstall:
    @pytest.mark.asyncio
    async def test_uninstall_runs_uninstall_script_when_provided(self, tmp_path):
        un = tmp_path / "scripts" / "uninstall-fake.sh"
        un.parent.mkdir(parents=True)
        un.write_text("#!/bin/bash\necho removed > \"$1/uninstalled\"\n")
        un.chmod(un.stat().st_mode | stat.S_IXUSR)
        installer = ScriptInstaller(project_dir=tmp_path)
        # Direct uninstall_with_script call — bypasses install_config tracking
        # (that's the dispatcher's job).
        result = await installer.uninstall_with_script(
            "x", str(un.relative_to(tmp_path))
        )
        assert result["success"] is True
        assert (tmp_path / "uninstalled").read_text().strip() == "removed"
