from __future__ import annotations

import shutil
import sys
from pathlib import Path

from tinyagentos.installers.base import AppInstaller, run_cmd


class PipInstaller(AppInstaller):
    def __init__(self, apps_dir: Path | None = None):
        # tinyagentos/installers/pip_installer.py -> parents[2] is the
        # install root, so this tracks wherever taOS is actually installed.
        self.apps_dir = apps_dir or Path(__file__).parents[2] / "apps"

    async def install(self, app_id: str, install_config: dict, **kwargs) -> dict:
        app_dir = self.apps_dir / app_id
        venv_dir = app_dir / "venv"
        app_dir.mkdir(parents=True, exist_ok=True)

        # Create venv
        code, output = await run_cmd([sys.executable, "-m", "venv", str(venv_dir)])
        if code != 0:
            return {"success": False, "error": f"venv creation failed: {output}"}

        # Install package
        pip = str(venv_dir / "bin" / "pip")
        package = install_config["package"]
        extras = install_config.get("extras", [])
        pkg_spec = f"{package}[{','.join(extras)}]" if extras else package

        code, output = await run_cmd([pip, "install", pkg_spec])
        if code != 0:
            return {"success": False, "error": f"pip install failed: {output}"}

        return {"success": True, "path": str(app_dir)}

    async def uninstall(self, app_id: str) -> dict:
        app_dir = self.apps_dir / app_id
        if app_dir.exists():
            shutil.rmtree(app_dir)
        return {"success": True}
