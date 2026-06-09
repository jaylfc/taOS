from __future__ import annotations

import logging
import secrets
import shlex
import socket
from contextlib import closing

from tinyagentos.installers.base import AppInstaller
from tinyagentos.installers.port_allocator import RESERVED_PORTS
import tinyagentos.containers as containers

logger = logging.getLogger(__name__)

# Gitea version pinned here; manifest may override via install_config["gitea_version"].
_DEFAULT_GITEA_VERSION = "1.22.6"

_SYSTEMD_UNIT = """\
[Unit]
Description=Gitea (Git with a cup of tea)
After=network.target

[Service]
RestartSec=2s
Type=simple
User=git
WorkingDirectory=/home/git
ExecStart=/usr/local/bin/gitea web -c /etc/gitea/app.ini
Restart=always
Environment=USER=git HOME=/home/git GITEA_WORK_DIR=/home/git

[Install]
WantedBy=multi-user.target
"""

_APP_INI_TEMPLATE = """\
[server]
HTTP_PORT = 3000
ROOT_URL = {root_url}
SSH_PORT = 2222

[database]
DB_TYPE = sqlite3
PATH = /home/git/gitea.db

[security]
INSTALL_LOCK = true
SECRET_KEY = {secret_key}
INTERNAL_TOKEN = {internal_token}

[service]
DISABLE_REGISTRATION = true
"""


def _render_app_ini(app_id: str = "gitea-lxc") -> str:
    """Render app.ini with proxy-aware ROOT_URL.

    Gitea uses ROOT_URL to build absolute URLs for avatars, clone URLs, and
    redirect targets. Hardcoding http://localhost:3000/ breaks any request
    routed through the controller proxy at /apps/{app_id}/ — the browser
    would be sent back to localhost:3000 which isn't reachable.

    Setting ROOT_URL to /apps/{app_id}/ makes Gitea emit relative-to-root
    URLs that the controller proxy will correctly re-route to the container.
    """
    return _APP_INI_TEMPLATE.format(
        secret_key=secrets.token_hex(32),
        internal_token=secrets.token_hex(32),
        root_url=f"/apps/{app_id}/",
    )


def _find_free_port(start: int = 30_000, end: int = 40_000) -> int:
    """Return the first available TCP port in [start, end) that is not reserved."""
    for port in range(start, end):
        if port in RESERVED_PORTS:
            continue
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free non-reserved port found in range {start}-{end}")


class LXCInstaller(AppInstaller):
    """Install a service (e.g. Gitea) into an isolated incus/LXC container."""

    CONTAINER_PREFIX = "taos-svc-"

    def _container_name(self, app_id: str) -> str:
        return f"{self.CONTAINER_PREFIX}{app_id}"

    async def install(
        self,
        app_id: str,
        install_config: dict,
        *,
        admin_password: str,
        taos_username: str = "admin",
        taos_email: str = "",
        restore_tarball: str | None = None,
        target_remote: str | None = None,
        **kwargs,
    ) -> dict:
        """Install app_id into a new LXC container.

        Parameters
        ----------
        app_id:
            Catalog app identifier (used as container name suffix).
        install_config:
            ``install`` block from the manifest YAML.
        admin_password:
            Password for the initial service admin account. Required unless
            ``restore_tarball`` is set (state is restored from the tarball).
        taos_username:
            taOS username — becomes the Gitea admin username.
        taos_email:
            taOS user email — becomes the Gitea admin email.
        restore_tarball:
            Path on the HOST filesystem to a tar archive containing service
            state (e.g. /etc/gitea/, /home/git/). When set, DB migration and
            admin-user creation are skipped and app.ini is restored from the
            tarball instead of being freshly generated.
        target_remote:
            incus remote name. When set, all container operations target
            ``<target_remote>:<container_name>`` instead of the local daemon.
        """
        if not restore_tarball and not admin_password:
            raise ValueError("admin_password is required for LXC installs")

        container_name = self._container_name(app_id)
        # Qualify container name with remote when targeting a remote host.
        incus_name = f"{target_remote}:{container_name}" if target_remote else container_name

        # Fail cleanly if container already exists.
        # For remote targets, container_exists uses `incus list` which doesn't
        # accept remote:name — fall back to `incus info` which does.
        if target_remote:
            _info_code, _ = await containers._run(["incus", "info", incus_name])
            _exists = _info_code == 0
        else:
            _exists = await containers.container_exists(incus_name)
        if _exists:
            raise RuntimeError(
                f"Container '{container_name}' already exists. "
                "Uninstall first or choose a different app_id."
            )

        image = install_config.get("image", "images:debian/bookworm")
        memory_limit = install_config.get("memory_limit", "512MiB")
        cpu_limit = int(install_config.get("cpu_limit", 1))
        gitea_version = install_config.get("gitea_version", _DEFAULT_GITEA_VERSION)

        # Step 1: Create container.
        logger.info("LXCInstaller: creating container %s from %s", incus_name, image)
        result = await containers.create_container(
            incus_name,
            image=image,
            memory_limit=memory_limit,
            cpu_limit=cpu_limit,
        )
        if not result.get("success"):
            raise RuntimeError(f"Container creation failed: {result.get('error', '')}")

        try:
            # Step 2: Wait for network readiness.
            import asyncio
            for _ in range(15):
                code, output = await containers.exec_in_container(
                    incus_name, ["hostname", "-I"], timeout=10
                )
                if code == 0 and output.strip():
                    break
                await asyncio.sleep(2)
            else:
                raise RuntimeError("Container did not get a network address in time")

            # Step 3: Install base packages and create git system user.
            logger.info("LXCInstaller: installing packages in %s", incus_name)
            code, output = await containers.exec_in_container(
                incus_name,
                [
                    "bash", "-c",
                    "apt-get update -qq && "
                    "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
                    "--no-install-recommends git sqlite3 wget ca-certificates && "
                    "useradd --system --create-home --shell /bin/bash git",
                ],
                timeout=300,
            )
            if code != 0:
                raise RuntimeError(f"Package install failed: {output}")

            # Step 4: Download Gitea binary (auto-detect arch).
            logger.info("LXCInstaller: downloading Gitea %s", gitea_version)
            code, output = await containers.exec_in_container(
                incus_name,
                [
                    "bash", "-c",
                    f"ARCH=$(dpkg --print-architecture) && "
                    f"wget -q -O /usr/local/bin/gitea "
                    f"https://dl.gitea.com/gitea/{gitea_version}/gitea-{gitea_version}-linux-${{ARCH}} && "
                    f"chmod +x /usr/local/bin/gitea",
                ],
                timeout=300,
            )
            if code != 0:
                raise RuntimeError(f"Gitea download failed: {output}")

            # Step 5: Write systemd unit.
            logger.info("LXCInstaller: writing systemd unit")
            code, output = await containers.exec_in_container(
                incus_name,
                ["bash", "-c", f"cat > /etc/systemd/system/gitea.service << 'TAOS_EOF'\n{_SYSTEMD_UNIT}\nTAOS_EOF"],
            )
            if code != 0:
                raise RuntimeError(f"Failed to write systemd unit: {output}")

            if restore_tarball:
                # Restore mode: push the tarball and extract over state paths.
                # app.ini, DB, and repos come from the tarball — skip writing app.ini
                # and skip DB migration + admin user creation.
                logger.info("LXCInstaller: restore mode — pushing tarball %s", restore_tarball)
                push_code, push_out = await containers.push_file(
                    incus_name, restore_tarball, "/tmp/restore.tar"
                )
                if push_code != 0:
                    raise RuntimeError(f"Failed to push restore tarball: {push_out}")

                logger.info("LXCInstaller: extracting tarball in container")
                code, output = await containers.exec_in_container(
                    incus_name,
                    ["tar", "--numeric-owner", "-xpf", "/tmp/restore.tar", "-C", "/"],
                    timeout=300,
                )
                if code != 0:
                    raise RuntimeError(f"Failed to extract restore tarball: {output}")

                # Clean up tarball inside container.
                await containers.exec_in_container(incus_name, ["rm", "-f", "/tmp/restore.tar"])
            else:
                # Fresh install: create /etc/gitea, write app.ini with new secrets.
                logger.info("LXCInstaller: writing config")
                code, output = await containers.exec_in_container(
                    incus_name,
                    ["bash", "-c", "mkdir -p /etc/gitea && chown root:git /etc/gitea && chmod 770 /etc/gitea"],
                )
                if code != 0:
                    raise RuntimeError(f"Failed to create /etc/gitea: {output}")

                app_ini = _render_app_ini(app_id=app_id)
                code, output = await containers.exec_in_container(
                    incus_name,
                    ["bash", "-c",
                     f"cat > /etc/gitea/app.ini << 'TAOS_EOF'\n{app_ini}\nTAOS_EOF\n"
                     "chown root:git /etc/gitea/app.ini && chmod 660 /etc/gitea/app.ini"],
                )
                if code != 0:
                    raise RuntimeError(f"Failed to write app.ini: {output}")

                # Step 6: First-boot DB migration + admin user creation.
                logger.info("LXCInstaller: running Gitea DB migration")
                code, output = await containers.exec_in_container(
                    incus_name,
                    ["su", "-", "git", "-c", "GITEA_WORK_DIR=/home/git gitea migrate -c /etc/gitea/app.ini"],
                    timeout=120,
                )
                if code != 0:
                    raise RuntimeError(f"Gitea migrate failed: {output}")

                logger.info("LXCInstaller: creating Gitea admin user '%s'", taos_username)
                safe_username = shlex.quote(taos_username)
                safe_email = shlex.quote(taos_email or taos_username + "@localhost")
                safe_password = shlex.quote(admin_password)
                code, output = await containers.exec_in_container(
                    incus_name,
                    [
                        "su", "-", "git", "-c",
                        f"GITEA_WORK_DIR=/home/git gitea admin user create "
                        f"--admin "
                        f"--username {safe_username} "
                        f"--email {safe_email} "
                        f"--password {safe_password} "
                        f"--must-change-password=false "
                        f"-c /etc/gitea/app.ini",
                    ],
                    timeout=60,
                )
                if code != 0:
                    raise RuntimeError(f"Gitea admin user creation failed: {output}")

            # Step 7: Enable and start service.
            logger.info("LXCInstaller: enabling and starting gitea.service")
            code, output = await containers.exec_in_container(
                incus_name,
                ["bash", "-c", "systemctl daemon-reload && systemctl enable gitea && systemctl start gitea"],
                timeout=60,
            )
            if code != 0:
                raise RuntimeError(f"Failed to start gitea service: {output}")

            # Step 8: Add proxy device (host_port → container:3000).
            # Retry up to 10 times to handle the TOCTOU window between the
            # port probe and the actual bind by incus.
            for _attempt in range(10):
                host_port = _find_free_port()
                logger.info(
                    "LXCInstaller: adding proxy device host:%d -> container:3000 (attempt %d)",
                    host_port, _attempt + 1,
                )
                res = await containers.add_proxy_device(
                    incus_name,
                    device_name="gitea-http",
                    listen=f"tcp:0.0.0.0:{host_port}",
                    connect="tcp:127.0.0.1:3000",
                )
                if res.get("success"):
                    break
                if "address already in use" not in res.get("output", "").lower():
                    raise RuntimeError(
                        f"Failed to add proxy device: {res.get('output', '')}"
                    )
                logger.warning(
                    "LXCInstaller: port %d already in use, retrying", host_port
                )
            else:
                raise RuntimeError("Failed to allocate a free proxy port after 10 attempts")

            # Step 9: Record install metadata.
            install_record = {
                "app_id": app_id,
                "backend": "lxc",
                "container": container_name,
                "host_port": host_port,
                "gitea_version": gitea_version,
                "admin_username": taos_username,
            }
            logger.info("LXCInstaller: install complete — %s", install_record)
            return {"success": True, **install_record}

        except Exception:
            logger.exception(
                "LXCInstaller: rolling back — destroying container %s", incus_name
            )
            await containers.destroy_container(incus_name)
            raise

    async def uninstall(self, app_id: str, target_remote: str | None = None) -> dict:
        """Stop and delete the service container.

        Parameters
        ----------
        target_remote:
            incus remote name. When set, the container is destroyed on the
            remote host rather than locally.
        """
        container_name = self._container_name(app_id)
        incus_name = f"{target_remote}:{container_name}" if target_remote else container_name
        result = await containers.destroy_container(incus_name)
        return {"success": result["success"], "app_id": app_id}

    async def start(self, app_id: str) -> dict:
        container_name = self._container_name(app_id)
        result = await containers.start_container(container_name)
        return result

    async def stop(self, app_id: str) -> dict:
        container_name = self._container_name(app_id)
        result = await containers.stop_container(container_name)
        return result
