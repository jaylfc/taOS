"""Installer for backend service manifests using ``install: {method: script}``.

The script receives one argument: the project root path. The script is
expected to be idempotent — invoked again on a host where the backend
already exists, it should detect that and exit 0.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from tinyagentos.installers.base import AppInstaller, run_cmd

logger = logging.getLogger(__name__)


class ScriptInstaller(AppInstaller):
    """Run a shell script declared in ``install.script`` (path relative to project_dir)."""

    def __init__(self, project_dir: Path | str | None = None, timeout: int = 1800):
        self.project_dir = Path(project_dir) if project_dir else Path.cwd()
        self.timeout = timeout

    async def install(
        self,
        app_id: str,
        install_config: dict,
        **_: Any,
    ) -> dict:
        rel_script = install_config.get("script")
        if not rel_script:
            return {"success": False, "error": "install.script not declared in manifest"}
        script_path = (self.project_dir / rel_script).resolve()
        if not script_path.is_file():
            return {"success": False, "error": f"script not found: {script_path}"}

        rc, out = await run_cmd(
            ["bash", str(script_path), str(self.project_dir)],
            cwd=str(self.project_dir),
            timeout=self.timeout,
        )
        if rc != 0:
            return {
                "success": False,
                "error": f"install script failed (rc={rc}): {out.strip()[-1000:]}",
            }
        return {"success": True, "app_id": app_id, "method": "script"}

    async def uninstall(self, app_id: str) -> dict:
        # The uninstall script (if any) is recorded in install_config and
        # run via uninstall_with_script() — direct uninstall(app_id) on a
        # ScriptInstaller without that context is a no-op.
        return {"success": True, "status": "uninstalled", "note": "no uninstall script declared"}

    async def uninstall_with_script(self, app_id: str, rel_script: str) -> dict:
        """Run an explicit uninstall script."""
        script_path = (self.project_dir / rel_script).resolve()
        if not script_path.is_file():
            return {"success": False, "error": f"uninstall script not found: {script_path}"}
        rc, out = await run_cmd(
            ["bash", str(script_path), str(self.project_dir)],
            cwd=str(self.project_dir),
            timeout=self.timeout,
        )
        if rc != 0:
            return {
                "success": False,
                "error": f"uninstall script failed (rc={rc}): {out.strip()[-1000:]}",
            }
        return {"success": True, "app_id": app_id}
