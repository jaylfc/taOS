"""Unit tests for migrate_service() in tinyagentos.cluster.service_migrator."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tinyagentos.cluster.service_migrator import migrate_service

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_INSTALL_CONFIG = {
    "method": "lxc",
    "image": "images:debian/bookworm",
    "gitea_version": "1.22.6",
    "memory_limit": "512MiB",
    "cpu_limit": 1,
}

_STATE_PATHS = ["/etc/gitea/", "/home/git/"]


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

class TestMigrateServiceErrors:
    @pytest.mark.asyncio
    async def test_remote_not_registered(self):
        """Returns error dict if target_remote is not in incus remote list."""
        with patch(
            "tinyagentos.cluster.service_migrator.containers.remote_list",
            new_callable=AsyncMock,
            return_value=[{"name": "local", "addr": "unix://", "protocol": "incus"}],
        ):
            result = await migrate_service(
                "gitea", "fedora-worker",
                install_config=_INSTALL_CONFIG,
                state_paths=_STATE_PATHS,
                service_name="gitea",
            )
        assert result["success"] is False
        assert "fedora-worker" in result["error"]
        assert "incus remote add" in result["error"]

    @pytest.mark.asyncio
    async def test_source_container_not_found(self):
        """Returns error dict when the source container does not exist."""
        with (
            patch(
                "tinyagentos.cluster.service_migrator.containers.remote_list",
                new_callable=AsyncMock,
                return_value=[
                    {"name": "local", "addr": "unix://", "protocol": "incus"},
                    {"name": "fedora-worker", "addr": "https://x:8443", "protocol": "incus"},
                ],
            ),
            patch(
                "tinyagentos.cluster.service_migrator.containers._run",
                new_callable=AsyncMock,
                return_value=(1, "not found"),
            ),
        ):
            result = await migrate_service(
                "gitea", "fedora-worker",
                install_config=_INSTALL_CONFIG,
                state_paths=_STATE_PATHS,
                service_name="gitea",
            )
        assert result["success"] is False
        assert "taos-svc-gitea" in result["error"]

    @pytest.mark.asyncio
    async def test_tarball_creation_fails_restarts_source(self):
        """When tar inside the container fails, the source service is restarted."""
        exec_calls: list = []

        async def fake_exec(container_name, cmd, timeout=300):
            exec_calls.append(list(cmd))
            if "-cpf" in " ".join(cmd):
                return (1, "tar: error creating archive")
            return (0, "")

        with (
            patch(
                "tinyagentos.cluster.service_migrator.containers.remote_list",
                new_callable=AsyncMock,
                return_value=[
                    {"name": "local", "addr": "unix://", "protocol": "incus"},
                    {"name": "fedora-worker", "addr": "https://x:8443", "protocol": "incus"},
                ],
            ),
            patch(
                "tinyagentos.cluster.service_migrator.containers._run",
                new_callable=AsyncMock,
                return_value=(0, "Name: taos-svc-gitea\nStatus: RUNNING\n"),
            ),
            patch(
                "tinyagentos.cluster.service_migrator.containers.exec_in_container",
                side_effect=fake_exec,
            ),
        ):
            with pytest.raises(RuntimeError, match="Failed to create state tarball"):
                await migrate_service(
                    "gitea", "fedora-worker",
                    install_config=_INSTALL_CONFIG,
                    state_paths=_STATE_PATHS,
                    service_name="gitea",
                )

        full_cmds = [" ".join(c) for c in exec_calls]
        assert any("systemctl" in c and "start" in c for c in full_cmds), \
            "source service restart not attempted after tarball failure"

    @pytest.mark.asyncio
    async def test_install_on_target_fails_restarts_source(self):
        """When LXCInstaller.install() raises, the source service is restarted."""
        exec_calls: list = []

        async def fake_exec(container_name, cmd, timeout=300):
            exec_calls.append(list(cmd))
            return (0, "")

        with (
            patch(
                "tinyagentos.cluster.service_migrator.containers.remote_list",
                new_callable=AsyncMock,
                return_value=[
                    {"name": "local", "addr": "unix://", "protocol": "incus"},
                    {"name": "fedora-worker", "addr": "https://x:8443", "protocol": "incus"},
                ],
            ),
            patch(
                "tinyagentos.cluster.service_migrator.containers._run",
                new_callable=AsyncMock,
                side_effect=[
                    (0, "Name: taos-svc-gitea\nStatus: RUNNING\n"),
                    (0, ""),
                ],
            ),
            patch(
                "tinyagentos.cluster.service_migrator.containers.exec_in_container",
                side_effect=fake_exec,
            ),
            patch(
                "tinyagentos.cluster.service_migrator.LXCInstaller",
            ) as MockInstaller,
            patch("os.path.getsize", return_value=1024),
            patch("os.unlink"),
        ):
            instance = MockInstaller.return_value
            instance.install = AsyncMock(side_effect=RuntimeError("apt-get failed"))

            with pytest.raises(RuntimeError, match="apt-get failed"):
                await migrate_service(
                    "gitea", "fedora-worker",
                    install_config=_INSTALL_CONFIG,
                    state_paths=_STATE_PATHS,
                    service_name="gitea",
                )

        full_cmds = [" ".join(c) for c in exec_calls]
        assert any("systemctl" in c and "start" in c for c in full_cmds), \
            "source service restart not attempted after install failure"


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

class TestMigrateServiceSuccess:
    @pytest.mark.asyncio
    async def test_success_path_destroys_source_by_default(self):
        """On success with keep_source=False, source container is destroyed."""
        exec_calls: list = []

        async def fake_exec(container_name, cmd, timeout=300):
            exec_calls.append(list(cmd))
            return (0, "")

        run_calls: list = []

        async def fake_run(cmd, timeout=120):
            run_calls.append(list(cmd))
            return (0, "Name: taos-svc-gitea\nStatus: RUNNING\n" if "info" in cmd else "")

        with (
            patch(
                "tinyagentos.cluster.service_migrator.containers.remote_list",
                new_callable=AsyncMock,
                return_value=[
                    {"name": "local", "addr": "unix://", "protocol": "incus"},
                    {"name": "fedora-worker", "addr": "https://x:8443", "protocol": "incus"},
                ],
            ),
            patch(
                "tinyagentos.cluster.service_migrator.containers._run",
                side_effect=fake_run,
            ),
            patch(
                "tinyagentos.cluster.service_migrator.containers.exec_in_container",
                side_effect=fake_exec,
            ),
            patch(
                "tinyagentos.cluster.service_migrator.LXCInstaller",
            ) as MockInstaller,
            patch("os.path.getsize", return_value=2048),
            patch("os.unlink"),
        ):
            instance = MockInstaller.return_value
            instance.install = AsyncMock(return_value={
                "success": True, "app_id": "gitea", "host_port": 13000,
            })

            result = await migrate_service(
                "gitea", "fedora-worker",
                install_config=_INSTALL_CONFIG,
                state_paths=_STATE_PATHS,
                service_name="gitea",
                keep_source=False,
            )

        assert result["success"] is True
        assert result["source"] == "local:taos-svc-gitea"
        assert result["target"] == "fedora-worker:taos-svc-gitea"
        assert result["tarball_size_bytes"] == 2048
        delete_cmds = [c for c in run_calls if "delete" in c]
        assert any("taos-svc-gitea" in c for c in delete_cmds), \
            "source container delete not called after success"

    @pytest.mark.asyncio
    async def test_success_path_keeps_source_when_requested(self):
        """On success with keep_source=True, source container is NOT destroyed."""
        async def fake_exec(container_name, cmd, timeout=300):
            return (0, "")

        run_calls: list = []

        async def fake_run(cmd, timeout=120):
            run_calls.append(list(cmd))
            return (0, "Name: taos-svc-gitea\nStatus: RUNNING\n" if "info" in cmd else "")

        with (
            patch(
                "tinyagentos.cluster.service_migrator.containers.remote_list",
                new_callable=AsyncMock,
                return_value=[
                    {"name": "local", "addr": "unix://", "protocol": "incus"},
                    {"name": "fedora-worker", "addr": "https://x:8443", "protocol": "incus"},
                ],
            ),
            patch(
                "tinyagentos.cluster.service_migrator.containers._run",
                side_effect=fake_run,
            ),
            patch(
                "tinyagentos.cluster.service_migrator.containers.exec_in_container",
                side_effect=fake_exec,
            ),
            patch(
                "tinyagentos.cluster.service_migrator.LXCInstaller",
            ) as MockInstaller,
            patch("os.path.getsize", return_value=512),
            patch("os.unlink"),
        ):
            instance = MockInstaller.return_value
            instance.install = AsyncMock(return_value={"success": True, "app_id": "gitea"})

            result = await migrate_service(
                "gitea", "fedora-worker",
                install_config=_INSTALL_CONFIG,
                state_paths=_STATE_PATHS,
                service_name="gitea",
                keep_source=True,
            )

        assert result["success"] is True
        delete_cmds = [c for c in run_calls if "delete" in c]
        assert not delete_cmds, "source container should not be deleted when keep_source=True"

    @pytest.mark.asyncio
    async def test_installer_called_with_restore_tarball_and_target_remote(self):
        """LXCInstaller.install must be called with restore_tarball and target_remote."""
        async def fake_exec(container_name, cmd, timeout=300):
            return (0, "")

        async def fake_run(cmd, timeout=120):
            return (0, "Name: taos-svc-gitea\nStatus: RUNNING\n" if "info" in cmd else "")

        with (
            patch(
                "tinyagentos.cluster.service_migrator.containers.remote_list",
                new_callable=AsyncMock,
                return_value=[
                    {"name": "local", "addr": "unix://", "protocol": "incus"},
                    {"name": "fedora-worker", "addr": "https://x:8443", "protocol": "incus"},
                ],
            ),
            patch(
                "tinyagentos.cluster.service_migrator.containers._run",
                side_effect=fake_run,
            ),
            patch(
                "tinyagentos.cluster.service_migrator.containers.exec_in_container",
                side_effect=fake_exec,
            ),
            patch(
                "tinyagentos.cluster.service_migrator.LXCInstaller",
            ) as MockInstaller,
            patch("os.path.getsize", return_value=100),
            patch("os.unlink"),
        ):
            instance = MockInstaller.return_value
            instance.install = AsyncMock(return_value={"success": True, "app_id": "gitea"})

            await migrate_service(
                "gitea", "fedora-worker",
                install_config=_INSTALL_CONFIG,
                state_paths=_STATE_PATHS,
                service_name="gitea",
            )

        install_kwargs = instance.install.call_args
        assert install_kwargs.kwargs["target_remote"] == "fedora-worker"
        assert install_kwargs.kwargs["restore_tarball"] is not None
        assert install_kwargs.kwargs["admin_password"] == ""


# ---------------------------------------------------------------------------
# source_remote tests
# ---------------------------------------------------------------------------

class TestMigrateServiceSourceRemote:
    @pytest.mark.asyncio
    async def test_same_source_and_target_remote_returns_error(self):
        """When source_remote == target_remote, returns error without any incus calls."""
        with patch(
            "tinyagentos.cluster.service_migrator.containers.remote_list",
            new_callable=AsyncMock,
            return_value=[
                {"name": "fedora-worker", "addr": "https://x:8443", "protocol": "incus"},
            ],
        ):
            result = await migrate_service(
                "gitea", "fedora-worker",
                install_config=_INSTALL_CONFIG,
                state_paths=_STATE_PATHS,
                service_name="gitea",
                source_remote="fedora-worker",
            )
        assert result["success"] is False
        assert "fedora-worker" in result["error"]
        assert "no-op" in result["error"]

    @pytest.mark.asyncio
    async def test_source_remote_prefixes_all_source_ops_not_target(self):
        """With source_remote set, all incus source ops use remote:name; target ops do not."""
        exec_calls: list = []

        async def fake_exec(container_name, cmd, timeout=300):
            exec_calls.append((container_name, list(cmd)))
            return (0, "")

        run_calls: list = []

        async def fake_run(cmd, timeout=120):
            run_calls.append(list(cmd))
            return (0, "Name: taos-svc-gitea\nStatus: RUNNING\n" if "info" in cmd else "")

        with (
            patch(
                "tinyagentos.cluster.service_migrator.containers.remote_list",
                new_callable=AsyncMock,
                return_value=[
                    {"name": "local", "addr": "unix://", "protocol": "incus"},
                    {"name": "fedora-worker", "addr": "https://x:8443", "protocol": "incus"},
                    {"name": "pi-worker", "addr": "https://y:8443", "protocol": "incus"},
                ],
            ),
            patch(
                "tinyagentos.cluster.service_migrator.containers._run",
                side_effect=fake_run,
            ),
            patch(
                "tinyagentos.cluster.service_migrator.containers.exec_in_container",
                side_effect=fake_exec,
            ),
            patch(
                "tinyagentos.cluster.service_migrator.LXCInstaller",
            ) as MockInstaller,
            patch("os.path.getsize", return_value=1024),
            patch("os.unlink"),
        ):
            instance = MockInstaller.return_value
            instance.install = AsyncMock(return_value={"success": True, "app_id": "gitea"})

            result = await migrate_service(
                "gitea", "pi-worker",
                install_config=_INSTALL_CONFIG,
                state_paths=_STATE_PATHS,
                service_name="gitea",
                source_remote="fedora-worker",
                keep_source=False,
            )

        assert result["success"] is True
        assert result["source"] == "fedora-worker:taos-svc-gitea"
        assert result["target"] == "pi-worker:taos-svc-gitea"

        # All exec_in_container calls must use the source remote prefix.
        for container_arg, _ in exec_calls:
            assert container_arg == "fedora-worker:taos-svc-gitea", (
                f"exec_in_container called with unexpected name: {container_arg!r}"
            )

        # incus info and incus file pull must reference the source remote.
        info_cmds = [c for c in run_calls if "info" in c]
        assert all("fedora-worker:taos-svc-gitea" in c for c in info_cmds)

        pull_cmds = [c for c in run_calls if "file" in c and "pull" in c]
        assert all("fedora-worker:taos-svc-gitea" in " ".join(c) for c in pull_cmds)

        # Delete must reference the source remote prefix.
        delete_cmds = [c for c in run_calls if "delete" in c]
        assert any("fedora-worker:taos-svc-gitea" in c for c in delete_cmds)

        # LXCInstaller.install must target pi-worker (not fedora-worker).
        install_kwargs = instance.install.call_args
        assert install_kwargs.kwargs["target_remote"] == "pi-worker"


# ---------------------------------------------------------------------------
# Route tests for /api/cluster/migrate-service
# ---------------------------------------------------------------------------

class TestMigrateServiceRoute:
    @pytest.mark.asyncio
    async def test_post_migrate_service_success(self, client):
        """POST /api/cluster/migrate-service returns success payload."""
        mock_result = {
            "success": True,
            "source": "local:taos-svc-gitea",
            "target": "fedora-worker:taos-svc-gitea",
            "duration_s": 8.5,
            "tarball_size_bytes": 4096,
        }
        mock_manifest = MagicMock()
        mock_manifest.install = {
            "method": "lxc",
            "state_paths": ["/etc/gitea/", "/home/git/"],
            "service_name": "gitea",
        }
        mock_manifest.manifest_dir = None
        with (
            patch(
                "tinyagentos.routes.cluster_migrate.migrate_service",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch("tinyagentos.registry.AppRegistry.get", return_value=mock_manifest),
        ):
            resp = await client.post("/api/cluster/migrate-service", json={
                "app_id": "gitea",
                "target_remote": "fedora-worker",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["target"] == "fedora-worker:taos-svc-gitea"

    @pytest.mark.asyncio
    async def test_post_migrate_service_missing_fields(self, client):
        """POST /api/cluster/migrate-service with empty fields returns 400."""
        resp = await client.post("/api/cluster/migrate-service", json={
            "app_id": "", "target_remote": "",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_post_migrate_service_app_not_found(self, client):
        """POST /api/cluster/migrate-service returns 404 when app_id not in catalog."""
        with patch("tinyagentos.registry.AppRegistry.get", return_value=None):
            resp = await client.post("/api/cluster/migrate-service", json={
                "app_id": "nonexistent-app",
                "target_remote": "fedora-worker",
            })
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_post_migrate_service_no_state_paths(self, client):
        """POST /api/cluster/migrate-service returns 400 when manifest has no state_paths."""
        mock_manifest = MagicMock()
        mock_manifest.install = {"method": "lxc"}
        mock_manifest.manifest_dir = None
        with patch("tinyagentos.registry.AppRegistry.get", return_value=mock_manifest):
            resp = await client.post("/api/cluster/migrate-service", json={
                "app_id": "gitea",
                "target_remote": "fedora-worker",
            })
        assert resp.status_code == 400
        assert "state_paths" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_post_migrate_service_propagates_error(self, client):
        """POST /api/cluster/migrate-service returns 500 when migration raises."""
        mock_manifest = MagicMock()
        mock_manifest.install = {
            "method": "lxc",
            "state_paths": ["/etc/gitea/"],
            "service_name": "gitea",
        }
        mock_manifest.manifest_dir = None
        with (
            patch(
                "tinyagentos.routes.cluster_migrate.migrate_service",
                new_callable=AsyncMock,
                side_effect=RuntimeError("remote not reachable"),
            ),
            patch("tinyagentos.registry.AppRegistry.get", return_value=mock_manifest),
        ):
            resp = await client.post("/api/cluster/migrate-service", json={
                "app_id": "gitea",
                "target_remote": "fedora-worker",
            })
        assert resp.status_code == 500
        assert resp.json()["error"] == "service migration failed"

    @pytest.mark.asyncio
    async def test_update_runtime_location_called_after_migrate(self, app, client):
        """After a successful migrate-service, update_runtime_location is called."""
        mock_result = {
            "success": True,
            "source": "local:taos-svc-gitea",
            "target": "fedora-worker:taos-svc-gitea",
            "duration_s": 5.0,
            "tarball_size_bytes": 1024,
            "host_port": 13500,
            "target_remote": "fedora-worker",
        }
        mock_manifest = MagicMock()
        mock_manifest.install = {
            "method": "lxc",
            "state_paths": ["/etc/gitea/", "/home/git/"],
            "service_name": "gitea",
            "ui_path": "/",
        }
        mock_manifest.manifest_dir = None
        update_called_with: list = []

        async def fake_update(app_id, host, port, backend="", ui_path="/"):
            update_called_with.append((app_id, host, port, backend, ui_path))

        installed_apps_mock = MagicMock()
        installed_apps_mock.update_runtime_location = AsyncMock(side_effect=fake_update)
        app.state.installed_apps = installed_apps_mock

        with (
            patch(
                "tinyagentos.routes.cluster_migrate.migrate_service",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch("tinyagentos.registry.AppRegistry.get", return_value=mock_manifest),
            patch(
                "tinyagentos.routes.cluster_migrate.remote_list",
                new_callable=AsyncMock,
                return_value=[
                    {"name": "fedora-worker", "addr": "https://192.168.6.108:8443", "protocol": "incus"},
                ],
            ),
        ):
            resp = await client.post("/api/cluster/migrate-service", json={
                "app_id": "gitea",
                "target_remote": "fedora-worker",
            })

        assert resp.status_code == 200
        assert len(update_called_with) == 1
        app_id, host, port, backend, ui_path = update_called_with[0]
        assert app_id == "gitea"
        assert host == "192.168.6.108"  # parsed from remote URL
        assert port == 13500
        assert backend == "lxc"
