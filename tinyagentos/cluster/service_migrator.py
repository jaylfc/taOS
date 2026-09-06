"""State-path service migration for LXC-hosted taOS services.

Migrates a running service to a new incus host by:
  1. Deploying a fresh container on the target (correct arch).
  2. Stopping the source service gracefully.
  3. Streaming state paths out as a tarball.
  4. Restoring the tarball into the target container.
  5. Starting the target service.
  6. Optionally destroying the source.
"""
from __future__ import annotations

import asyncio
import logging
import shlex
import time
import uuid

import tinyagentos.containers as containers
from tinyagentos.installers.lxc_installer import LXCInstaller

logger = logging.getLogger(__name__)

_INSTALLER_PREFIX = "taos-svc-"


async def migrate_service(
    app_id: str,
    target_remote: str,
    *,
    install_config: dict,
    state_paths: list[str],
    service_name: str = "gitea",
    keep_source: bool = False,
    restart_target: bool = True,
    source_remote: str | None = None,
) -> dict:
    """Migrate an installed LXC service to another incus host.

    Steps:
    1. Verify target_remote is registered (incus remote list).
    2. Derive source container name (taos-svc-<app_id>).
    3. Stop source service gracefully (systemctl stop <service> inside container).
    4. tar the state_paths inside the source container, stream to a temp file
       on the controller host via incus file pull.
    5. Call LXCInstaller.install() with target_remote and restore_tarball.
    6. On success + keep_source=False: incus delete source --force.
    7. On failure: restart source service so user doesn't lose access, raise.

    TODO (P5): re-point external routing (reverse proxy / DNS) after migration.

    Parameters
    ----------
    app_id:
        Catalog app identifier (e.g. "gitea").
    target_remote:
        Registered incus remote name for the destination host.
    install_config:
        ``install`` block from the manifest (passed through to LXCInstaller).
    state_paths:
        Container-side paths to include in the state tarball
        (e.g. ["/etc/gitea/", "/home/git/"]).
    service_name:
        systemd unit name inside the container (e.g. "gitea").
    keep_source:
        When True, do not destroy the source container after migration.
    restart_target:
        When True (default), enable and start the service on the target after
        restore. LXCInstaller already does this, so this flag is informational.
    source_remote:
        Registered incus remote name for the source host. None means local.
        Cannot equal target_remote (no-op migration not allowed).

    Returns
    -------
    dict with keys: success, source, target, duration_s, tarball_size_bytes.
    """
    t0 = time.monotonic()

    # Normalise "local" / "" / None to None — incus treats the local daemon
    # as an implicit remote, not a registered one.
    def _normalise(r: str | None) -> str | None:
        if r is None or r == "" or r == "local":
            return None
        return r

    source_remote = _normalise(source_remote)
    target_remote_norm = _normalise(target_remote)

    # Reject same-remote migration (including local→local).
    if source_remote == target_remote_norm:
        label = source_remote or "local"
        return {
            "success": False,
            "error": (
                f"source_remote and target_remote both resolve to '{label}'. "
                "Migration between the same host is a no-op."
            ),
        }

    container_name = f"{_INSTALLER_PREFIX}{app_id}"
    tarball_path = f"/tmp/taos-migrate-{uuid.uuid4().hex}.tar"

    # Build the incus name used for all source-side operations.
    source_incus_name = (
        f"{source_remote}:{container_name}" if source_remote else container_name
    )

    # 1. Verify target_remote is registered (skip for local target).
    if target_remote_norm is not None:
        remotes = await containers.remote_list()
        registered = {r["name"] for r in remotes}
        if target_remote_norm not in registered:
            return {
                "success": False,
                "error": (
                    f"Remote '{target_remote_norm}' is not registered. "
                    f"Register it first with: incus remote add {target_remote_norm} <url> --accept-certificate"
                ),
            }

    # 2. Verify source container exists.
    info_code, _ = await containers._run(["incus", "info", source_incus_name])
    if info_code != 0:
        host_label = source_remote or "local"
        return {
            "success": False,
            "error": f"Source container '{container_name}' not found on {host_label} host.",
        }

    # 3. Stop source service gracefully.
    logger.info("migrate_service: stopping %s in %s", service_name, source_incus_name)
    stop_code, stop_out = await containers.exec_in_container(
        source_incus_name,
        ["systemctl", "stop", shlex.quote(service_name)],
        timeout=60,
    )
    # systemctl stop is best-effort; log but continue if it fails.
    if stop_code != 0:
        logger.warning(
            "migrate_service: systemctl stop %s returned %d: %s",
            service_name, stop_code, stop_out,
        )

    try:
        # 4. Create tar of state paths inside the container, then pull out to host.
        quoted_paths = " ".join(shlex.quote(p) for p in state_paths)
        logger.info(
            "migrate_service: archiving state paths %s from %s", state_paths, source_incus_name
        )
        tar_code, tar_out = await containers.exec_in_container(
            source_incus_name,
            ["bash", "-c", f"tar -cpf /tmp/state.tar --numeric-owner {quoted_paths}"],
            timeout=300,
        )
        if tar_code != 0:
            raise RuntimeError(f"Failed to create state tarball in container: {tar_out}")

        # Pull the tarball from the container to the controller host.
        pull_code, pull_out = await containers._run(
            ["incus", "file", "pull", f"{source_incus_name}/tmp/state.tar", tarball_path],
            timeout=300,
        )
        if pull_code != 0:
            raise RuntimeError(f"Failed to pull state tarball from container: {pull_out}")

        # Clean up tarball inside source container.
        await containers.exec_in_container(source_incus_name, ["rm", "-f", "/tmp/state.tar"])

        import os
        tarball_size = os.path.getsize(tarball_path)
        logger.info(
            "migrate_service: tarball pulled to %s (%d bytes)", tarball_path, tarball_size
        )

        # 5. Deploy fresh container on target and restore state.
        installer = LXCInstaller()
        install_result = await installer.install(
            app_id,
            install_config,
            admin_password="",  # ignored in restore mode
            restore_tarball=tarball_path,
            target_remote=target_remote_norm,
        )
        if not install_result.get("success"):
            raise RuntimeError(
                f"Install on target failed: {install_result.get('error', 'unknown error')}"
            )

        # 6. Destroy source unless keep_source.
        if not keep_source:
            logger.info("migrate_service: destroying source container %s", source_incus_name)
            await containers._run(["incus", "delete", source_incus_name, "--force"])

        duration = round(time.monotonic() - t0, 1)
        source_label = source_remote or "local"
        target_label = target_remote_norm or "local"
        return {
            "success": True,
            "source": f"{source_label}:{container_name}",
            "target": f"{target_label}:{container_name}",
            "duration_s": duration,
            "tarball_size_bytes": tarball_size,
            # Expose install result fields so the caller can update the registry.
            "host_port": install_result.get("host_port"),
            "target_remote": target_remote_norm,
        }

    except Exception as exc:
        # Rollback: restart source service so user doesn't lose access.
        logger.error("migrate_service: failed (%s) — restarting source service", exc)
        restart_code, restart_out = await containers.exec_in_container(
            source_incus_name,
            ["systemctl", "start", shlex.quote(service_name)],
            timeout=60,
        )
        if restart_code != 0:
            logger.error(
                "migrate_service: source service restart also failed: %s", restart_out
            )
        raise
    finally:
        # Always clean up the host-side tarball.
        import os
        try:
            os.unlink(tarball_path)
        except OSError:
            pass
