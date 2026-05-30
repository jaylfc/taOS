from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from tinyagentos.installers.base import AppInstaller, run_cmd


class DockerInstaller(AppInstaller):
    def __init__(self, apps_dir: Path | None = None):
        self.apps_dir = apps_dir or Path("/opt/tinyagentos/apps")

    def _compose_path(self, app_id: str) -> Path:
        return self.apps_dir / app_id / "docker-compose.yaml"

    @staticmethod
    def _is_named_volume(source: str) -> bool:
        """True when a compose volume source is a named volume (not a host path).

        Host bind mounts start with /, ./, ../ or ~; anything else (e.g.
        ``config``) is a named volume that must be declared at the top level.
        """
        return bool(source) and not source.startswith(("/", "./", "../", "~"))

    def _generate_compose(self, app_id: str, install_config: dict) -> dict:
        """Generate a docker-compose.yaml from the manifest install config."""
        service = {
            "image": install_config["image"],
            "restart": "unless-stopped",
        }
        named_volumes: dict[str, None] = {}
        if "volumes" in install_config:
            service["volumes"] = install_config["volumes"]
            # Named volumes (e.g. "config:/etc/searxng") must also be declared
            # in a top-level `volumes:` block or compose rejects the project
            # with "refers to undefined volume".
            for vol in install_config["volumes"]:
                source = str(vol).split(":", 1)[0]
                if self._is_named_volume(source):
                    named_volumes[source] = None
        if "env" in install_config:
            service["environment"] = install_config["env"]
        if "ports" in install_config.get("requires", {}):
            service["ports"] = [f"{p}:{p}" for p in install_config["requires"]["ports"]]
        elif "ports" in install_config:
            service["ports"] = [f"{p}:{p}" for p in install_config["ports"]]

        # No top-level `version:` — it's obsolete in Compose v2 and emits a
        # warning on every command.
        compose: dict = {"services": {app_id: service}}
        if named_volumes:
            compose["volumes"] = named_volumes
        return compose

    async def install(self, app_id: str, install_config: dict, **kwargs) -> dict:
        app_dir = self.apps_dir / app_id
        app_dir.mkdir(parents=True, exist_ok=True)

        compose = self._generate_compose(app_id, install_config)
        compose_path = self._compose_path(app_id)
        compose_path.write_text(yaml.dump(compose, default_flow_style=False))

        # Pull image
        code, output = await run_cmd(
            ["docker", "compose", "-f", str(compose_path), "pull"],
            cwd=str(app_dir),
        )
        if code != 0:
            return {"success": False, "error": f"docker pull failed: {output}"}

        return {"success": True, "path": str(app_dir)}

    async def uninstall(self, app_id: str) -> dict:
        compose_path = self._compose_path(app_id)
        if compose_path.exists():
            await run_cmd(
                ["docker", "compose", "-f", str(compose_path), "down", "-v"],
                cwd=str(compose_path.parent),
            )
        app_dir = self.apps_dir / app_id
        if app_dir.exists():
            shutil.rmtree(app_dir)
        return {"success": True}

    async def start(self, app_id: str) -> dict:
        compose_path = self._compose_path(app_id)
        if not compose_path.exists():
            return {"success": False, "error": "docker-compose.yaml not found"}
        code, output = await run_cmd(
            ["docker", "compose", "-f", str(compose_path), "up", "-d"],
            cwd=str(compose_path.parent),
        )
        return {"success": code == 0, "output": output}

    async def stop(self, app_id: str) -> dict:
        compose_path = self._compose_path(app_id)
        if not compose_path.exists():
            return {"success": False, "error": "docker-compose.yaml not found"}
        code, output = await run_cmd(
            ["docker", "compose", "-f", str(compose_path), "down"],
            cwd=str(compose_path.parent),
        )
        return {"success": code == 0, "output": output}
