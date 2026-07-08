from __future__ import annotations

import secrets
import shutil
from pathlib import Path

import yaml

from tinyagentos.installers.base import AppInstaller, run_cmd
from tinyagentos.installers.port_allocator import allocate_host_port


class DockerInstaller(AppInstaller):
    def __init__(self, apps_dir: Path | None = None):
        # tinyagentos/installers/docker_installer.py -> parents[2] is the
        # install root, so this tracks wherever taOS is actually installed.
        self.apps_dir = apps_dir or Path(__file__).parents[2] / "apps"

    def _compose_path(self, app_id: str) -> Path:
        return self.apps_dir / app_id / "docker-compose.yaml"

    def _write_config_files(self, app_id: str, install_config: dict) -> None:
        """Write declarative config files from the manifest to the app directory.

        ``install_config['config_files']`` is a list of ``{path, content}``
        objects.  Each file is written to ``<app_dir>/<path>``, creating
        parent directories as needed.  The string ``{secret_key}`` in
        ``content`` is replaced with a random 64-hex-char secret so that
        apps like SearXNG can ship a default settings file without a
        hard-coded key.  The secret is persisted in ``<app_dir>/.secret_key``
        so re-installs keep it stable.
        """
        config_files = install_config.get("config_files", [])
        if not config_files:
            return
        app_dir = self.apps_dir / app_id

        # Validate each entry has required keys before using them.
        for i, entry in enumerate(config_files):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"config_files[{i}] must be a dict, got {type(entry).__name__}"
                )
            if "path" not in entry:
                raise ValueError(f"config_files[{i}] missing required key 'path'")
            if "content" not in entry:
                raise ValueError(f"config_files[{i}] missing required key 'content'")

        # Validate paths: reject '..' components and absolute paths; confirm
        # the resolved path stays within app_dir (path-traversal protection).
        resolved_app_dir = app_dir.resolve()
        for i, entry in enumerate(config_files):
            path = entry["path"]
            if path.startswith("/"):
                raise ValueError(
                    f"config_files[{i}].path must be relative, got {path!r}"
                )
            if ".." in Path(path).parts:
                raise ValueError(
                    f"config_files[{i}].path must not contain '..', got {path!r}"
                )
            resolved = (app_dir / path).resolve()
            if not str(resolved).startswith(str(resolved_app_dir) + "/"):
                raise ValueError(
                    f"config_files[{i}].path resolves outside app_dir: {path!r}"
                )

        # Persist secret_key per app so re-installs don't rotate it. It signs
        # sessions, so keep it owner-only and regenerate if a prior write left
        # it empty or malformed.
        secret_key_path = app_dir / ".secret_key"
        secret_key = ""
        if secret_key_path.exists():
            secret_key = secret_key_path.read_text().strip()
        if len(secret_key) != 64 or not all(c in "0123456789abcdef" for c in secret_key):
            secret_key = secrets.token_hex(32)
            app_dir.mkdir(parents=True, exist_ok=True)
            secret_key_path.write_text(secret_key)
            secret_key_path.chmod(0o600)

        for entry in config_files:
            path = entry["path"]
            content = entry["content"]
            if "{secret_key}" in content:
                content = content.replace("{secret_key}", secret_key)
            full_path = app_dir / path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)

    @staticmethod
    def _is_named_volume(source: str) -> bool:
        """True when a compose volume source is a named volume (not a host path).

        Host bind mounts start with /, ./, ../ or ~; anything else (e.g.
        ``config``) is a named volume that must be declared at the top level.
        """
        return bool(source) and not source.startswith(("/", "./", "../", "~"))

    def _generate_compose(
        self, app_id: str, install_config: dict
    ) -> tuple[dict, int | None]:
        """Generate a docker-compose.yaml from the manifest install config.

        The host port for each container port is always allocated from the
        managed high pool (30000-40000) via ``allocate_host_port``.  Apps must
        not bind core/well-known ports on the host regardless of what the
        manifest declares.  The container-side port is preserved as-is so the
        app's internal wiring is unaffected.

        Returns a ``(compose_dict, host_port)`` tuple.  ``host_port`` is
        ``None`` when the manifest declares no ports.
        """
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

        # Collect the container-internal ports from the manifest.
        container_ports: list[int] = []
        if "ports" in install_config.get("requires", {}):
            container_ports = [int(p) for p in install_config["requires"]["ports"]]
        elif "ports" in install_config:
            container_ports = [int(p) for p in install_config["ports"]]

        allocated_host_port: int | None = None
        if container_ports:
            # Allocate a host port from the managed pool for each container
            # port.  Every port is individually probed free on the host; a
            # bare `allocated + idx` for the extra ports could hand out a
            # port something else is bound to and crash compose up.  The
            # first port keeps app_id as its hash seed so existing
            # single-port installs keep their stable assignment.
            taken: set[int] = set()
            host_ports: list[int] = []
            for idx in range(len(container_ports)):
                seed = app_id if idx == 0 else f"{app_id}#{idx}"
                hp = allocate_host_port(seed, exclude=taken)
                taken.add(hp)
                host_ports.append(hp)
            allocated_host_port = host_ports[0]
            service["ports"] = [
                f"{hp}:{cport}"
                for hp, cport in zip(host_ports, container_ports)
            ]

        # No top-level `version:` — it's obsolete in Compose v2 and emits a
        # warning on every command.
        compose: dict = {"services": {app_id: service}}
        if named_volumes:
            compose["volumes"] = named_volumes
        return compose, allocated_host_port

    async def install(self, app_id: str, install_config: dict, **kwargs) -> dict:
        app_dir = self.apps_dir / app_id
        app_dir.mkdir(parents=True, exist_ok=True)

        # Seed any declarative config files before compose generation so
        # bind mounts like ./settings.yml resolve against the app dir.
        self._write_config_files(app_id, install_config)

        compose, host_port = self._generate_compose(app_id, install_config)
        compose_path = self._compose_path(app_id)
        compose_path.write_text(yaml.dump(compose, default_flow_style=False))

        # Pull image
        code, output = await run_cmd(
            ["docker", "compose", "-f", str(compose_path), "pull"],
            cwd=str(app_dir),
        )
        if code != 0:
            return {"success": False, "error": f"docker pull failed: {output}"}

        result: dict = {"success": True, "path": str(app_dir)}
        if host_port is not None:
            result["host_port"] = host_port
        return result

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
