"""Unit tests for LXCInstaller and the store_install.py route backend selection."""
from __future__ import annotations

import pytest
import pytest_asyncio
import yaml
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

from tinyagentos.installers.lxc_installer import LXCInstaller
from tinyagentos.app import create_app
from taos_test_csrf import csrf_event_hooks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

INSTALL_CONFIG = {
    "method": "lxc",
    "image": "images:debian/bookworm",
    "gitea_version": "1.22.6",
    "memory_limit": "512MiB",
    "cpu_limit": 1,
}


GENERIC_INSTALL_CONFIG = {
    "method": "lxc",
    "image": "images:debian/bookworm",
    "service_name": "taosr1",
    "ports": [8080],
    "ui_port": 8080,
    "ui_path": "/",
    "state_paths": ["/var/lib/taosr1"],
}


# ---------------------------------------------------------------------------
# LXCInstaller unit tests
# ---------------------------------------------------------------------------

class TestLXCInstallerMissingPassword:
    @pytest.mark.asyncio
    async def test_raises_value_error_when_no_password(self):
        installer = LXCInstaller()
        with pytest.raises(ValueError, match="admin_password is required"):
            await installer.install("gitea", INSTALL_CONFIG, admin_password="")


class TestLXCInstallerContainerAlreadyExists:
    @pytest.mark.asyncio
    async def test_raises_if_container_exists(self):
        installer = LXCInstaller()
        with patch(
            "tinyagentos.installers.lxc_installer.containers.container_exists",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with pytest.raises(RuntimeError, match="already exists"):
                await installer.install(
                    "gitea", INSTALL_CONFIG, admin_password="secret"
                )


class TestLXCInstallerHappyPath:
    @pytest.mark.asyncio
    async def test_create_container_called_with_correct_args(self):
        installer = LXCInstaller()

        with (
            patch(
                "tinyagentos.installers.lxc_installer.containers.container_exists",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.create_container",
                new_callable=AsyncMock,
                return_value={"success": True, "name": "taos-svc-gitea"},
            ) as mock_create,
            patch(
                "tinyagentos.installers.lxc_installer.containers.exec_in_container",
                new_callable=AsyncMock,
                return_value=(0, "10.0.0.2"),
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.add_proxy_device",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "tinyagentos.installers.lxc_installer.allocate_host_port",
                return_value=13000,
            ),
        ):
            result = await installer.install(
                "gitea",
                INSTALL_CONFIG,
                admin_password="supersecret",
                taos_username="jay",
                taos_email="jay@example.com",
            )

        assert result["success"] is True
        mock_create.assert_awaited_once_with(
            "taos-svc-gitea",
            image="images:debian/bookworm",
            memory_limit="512MiB",
            cpu_limit=1,
        )

    @pytest.mark.asyncio
    async def test_admin_user_command_contains_username_email_password(self):
        installer = LXCInstaller()
        captured_cmds: list[list[str]] = []

        async def fake_exec(container_name, cmd, timeout=300):
            captured_cmds.append(cmd)
            if "hostname" in cmd or "-I" in cmd:
                return (0, "10.0.0.2")
            return (0, "")

        with (
            patch(
                "tinyagentos.installers.lxc_installer.containers.container_exists",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.create_container",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.exec_in_container",
                side_effect=fake_exec,
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.add_proxy_device",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "tinyagentos.installers.lxc_installer.allocate_host_port",
                return_value=13001,
            ),
        ):
            await installer.install(
                "gitea",
                INSTALL_CONFIG,
                admin_password="mypassword",
                taos_username="jaylfc",
                taos_email="jaylfc25@gmail.com",
            )

        admin_cmd = next(
            (cmd for cmd in captured_cmds if "admin user create" in " ".join(cmd)),
            None,
        )
        assert admin_cmd is not None, "Admin user create command not found"
        full_cmd = " ".join(admin_cmd)
        assert "jaylfc" in full_cmd
        assert "jaylfc25@gmail.com" in full_cmd
        assert "mypassword" in full_cmd
        assert "--must-change-password=false" in full_cmd

    @pytest.mark.asyncio
    async def test_host_port_recorded_in_result(self):
        installer = LXCInstaller()

        with (
            patch(
                "tinyagentos.installers.lxc_installer.containers.container_exists",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.create_container",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.exec_in_container",
                new_callable=AsyncMock,
                return_value=(0, "10.0.0.2"),
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.add_proxy_device",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "tinyagentos.installers.lxc_installer.allocate_host_port",
                return_value=13500,
            ),
        ):
            result = await installer.install(
                "gitea",
                INSTALL_CONFIG,
                admin_password="secret",
                taos_username="admin",
            )

        assert result["host_port"] == 13500


class TestLXCInstallerRestoreMode:
    """Tests for install() with restore_tarball set."""

    @pytest.mark.asyncio
    async def test_restore_mode_skips_migrate_and_admin_create(self):
        """With restore_tarball, DB migration and admin user create must not run."""
        installer = LXCInstaller()
        captured_cmds: list[list[str]] = []

        async def fake_exec(container_name, cmd, timeout=300):
            captured_cmds.append(cmd)
            if "hostname" in cmd or "-I" in cmd:
                return (0, "10.0.0.2")
            return (0, "")

        with (
            patch(
                "tinyagentos.installers.lxc_installer.containers._run",
                new_callable=AsyncMock,
                return_value=(1, "not found"),  # container_exists check for remote
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.container_exists",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.create_container",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.exec_in_container",
                side_effect=fake_exec,
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.push_file",
                new_callable=AsyncMock,
                return_value=(0, ""),
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.add_proxy_device",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "tinyagentos.installers.lxc_installer.allocate_host_port",
                return_value=13002,
            ),
        ):
            result = await installer.install(
                "gitea",
                INSTALL_CONFIG,
                admin_password="",  # empty is allowed in restore mode
                restore_tarball="/tmp/fake-state.tar",
            )

        assert result["success"] is True

        full_cmds = [" ".join(c) for c in captured_cmds]
        # DB migration and admin user create must NOT appear
        assert not any("gitea migrate" in c for c in full_cmds), \
            "gitea migrate should not run in restore mode"
        assert not any("admin user create" in c for c in full_cmds), \
            "admin user create should not run in restore mode"

    @pytest.mark.asyncio
    async def test_restore_mode_runs_tar_extract(self):
        """With restore_tarball, the tar extract command must be executed."""
        installer = LXCInstaller()
        captured_cmds: list[list[str]] = []

        async def fake_exec(container_name, cmd, timeout=300):
            captured_cmds.append(cmd)
            if "hostname" in cmd or "-I" in cmd:
                return (0, "10.0.0.2")
            return (0, "")

        with (
            patch(
                "tinyagentos.installers.lxc_installer.containers._run",
                new_callable=AsyncMock,
                return_value=(1, "not found"),
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.container_exists",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.create_container",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.exec_in_container",
                side_effect=fake_exec,
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.push_file",
                new_callable=AsyncMock,
                return_value=(0, ""),
            ) as mock_push,
            patch(
                "tinyagentos.installers.lxc_installer.containers.add_proxy_device",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "tinyagentos.installers.lxc_installer.allocate_host_port",
                return_value=13003,
            ),
        ):
            await installer.install(
                "gitea",
                INSTALL_CONFIG,
                admin_password="",
                restore_tarball="/tmp/state.tar",
            )

        # push_file must have been called with the tarball path
        mock_push.assert_awaited_once()
        push_args = mock_push.call_args[0]
        assert push_args[1] == "/tmp/state.tar"
        assert push_args[2] == "/tmp/restore.tar"

        # tar extract must appear in exec_in_container calls
        full_cmds = [" ".join(c) for c in captured_cmds]
        assert any("tar" in c and "-xpf" in c for c in full_cmds), \
            "tar extract should run in restore mode"

    @pytest.mark.asyncio
    async def test_restore_mode_does_not_write_app_ini(self):
        """With restore_tarball, app.ini must not be written (restored from tarball)."""
        installer = LXCInstaller()
        captured_cmds: list[list[str]] = []

        async def fake_exec(container_name, cmd, timeout=300):
            captured_cmds.append(cmd)
            if "hostname" in cmd or "-I" in cmd:
                return (0, "10.0.0.2")
            return (0, "")

        with (
            patch(
                "tinyagentos.installers.lxc_installer.containers._run",
                new_callable=AsyncMock,
                return_value=(1, "not found"),
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.container_exists",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.create_container",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.exec_in_container",
                side_effect=fake_exec,
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.push_file",
                new_callable=AsyncMock,
                return_value=(0, ""),
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.add_proxy_device",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "tinyagentos.installers.lxc_installer.allocate_host_port",
                return_value=13004,
            ),
        ):
            await installer.install(
                "gitea",
                INSTALL_CONFIG,
                admin_password="",
                restore_tarball="/tmp/state.tar",
            )

        full_cmds = [" ".join(c) for c in captured_cmds]
        # The app.ini write is a bash -c that writes to /etc/gitea/app.ini.
        # The systemd unit also references /etc/gitea/app.ini in ExecStart, so
        # check specifically for the write-to-file pattern, not just mention.
        assert not any(
            "cat > /etc/gitea/app.ini" in c for c in full_cmds
        ), "app.ini should not be written in restore mode"

    @pytest.mark.asyncio
    async def test_restore_mode_empty_password_allowed(self):
        """install() must not raise ValueError when restore_tarball is provided and password is empty."""
        installer = LXCInstaller()

        async def fake_exec(container_name, cmd, timeout=300):
            if "hostname" in cmd or "-I" in cmd:
                return (0, "10.0.0.2")
            return (0, "")

        with (
            patch(
                "tinyagentos.installers.lxc_installer.containers._run",
                new_callable=AsyncMock,
                return_value=(1, "not found"),
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.container_exists",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.create_container",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.exec_in_container",
                side_effect=fake_exec,
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.push_file",
                new_callable=AsyncMock,
                return_value=(0, ""),
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.add_proxy_device",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "tinyagentos.installers.lxc_installer.allocate_host_port",
                return_value=13005,
            ),
        ):
            # Must not raise
            result = await installer.install(
                "gitea",
                INSTALL_CONFIG,
                admin_password="",
                restore_tarball="/tmp/state.tar",
            )
        assert result["success"] is True


class TestLXCInstallerRollback:
    @pytest.mark.asyncio
    async def test_container_deleted_on_failure(self):
        installer = LXCInstaller()
        destroy_mock = AsyncMock(return_value={"success": True})

        async def fail_exec(container_name, cmd, timeout=300):
            if "hostname" in cmd or "-I" in cmd:
                return (0, "10.0.0.2")
            if "apt-get" in " ".join(cmd):
                return (1, "apt error")
            return (0, "")

        with (
            patch(
                "tinyagentos.installers.lxc_installer.containers.container_exists",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.create_container",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.exec_in_container",
                side_effect=fail_exec,
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.destroy_container",
                destroy_mock,
            ),
        ):
            with pytest.raises(RuntimeError, match="Package install failed"):
                await installer.install(
                    "gitea",
                    INSTALL_CONFIG,
                    admin_password="secret",
                )

        destroy_mock.assert_awaited_once_with("taos-svc-gitea")


# ---------------------------------------------------------------------------
# Integration-level route tests
# ---------------------------------------------------------------------------

@pytest.fixture
def lxc_catalog_dir(tmp_path):
    svc = tmp_path / "catalog" / "services" / "gitea-lxc"
    svc.mkdir(parents=True)
    (svc / "manifest-lxc.yaml").write_text(yaml.dump({
        "id": "gitea-lxc",
        "name": "Gitea (LXC)",
        "type": "service",
        "category": "dev-tool",
        "version": "1.22.0",
        "backend": "lxc",
        "description": "Gitea in LXC",
        "install": {
            "method": "lxc",
            "image": "images:debian/bookworm",
            "gitea_version": "1.22.6",
            "memory_limit": "512MiB",
            "cpu_limit": 1,
        },
    }))
    docker_svc = tmp_path / "catalog" / "services" / "gitea"
    docker_svc.mkdir(parents=True)
    (docker_svc / "manifest.yaml").write_text(yaml.dump({
        "id": "gitea",
        "name": "Gitea",
        "type": "service",
        "category": "dev-tool",
        "version": "1.22.0",
        "description": "Gitea via Docker",
        "install": {"method": "docker", "image": "gitea/gitea:1.22"},
    }))
    return tmp_path / "catalog"


@pytest_asyncio.fixture
async def lxc_client(tmp_data_dir, lxc_catalog_dir):
    app = create_app(data_dir=tmp_data_dir, catalog_dir=lxc_catalog_dir)
    store = app.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    installed_apps = app.state.installed_apps
    if installed_apps._db is not None:
        await installed_apps.close()
    await installed_apps.init()
    await app.state.qmd_client.init()
    app.state.auth.setup_user("admin", "Test Admin", "admin@localhost", "testpass")
    _rec = app.state.auth.find_user("admin")
    _token = app.state.auth.create_session(
        user_id=_rec["id"] if _rec else "", long_lived=True
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", cookies={"taos_session": _token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        yield c
    await installed_apps.close()
    await store.close()
    await app.state.qmd_client.close()
    await app.state.http_client.aclose()


@pytest.mark.asyncio
class TestInstallRouteBackendSelection:
    async def test_lxc_install_missing_password_returns_400(self, lxc_client):
        resp = await lxc_client.post(
            "/api/store/install-v2",
            json={"app_id": "gitea-lxc", "metadata": {"method": "lxc"}},
        )
        assert resp.status_code == 400
        assert "admin_password" in resp.json()["error"]

    async def test_lxc_install_calls_lxc_installer(self, lxc_client):
        mock_result = {
            "success": True,
            "app_id": "gitea-lxc",
            "backend": "lxc",
            "container": "taos-svc-gitea-lxc",
            "host_port": 13000,
            "gitea_version": "1.22.6",
            "admin_username": "admin",
        }
        with patch(
            "tinyagentos.routes.store_install.LXCInstaller",
        ) as MockInstaller:
            instance = MockInstaller.return_value
            instance.install = AsyncMock(return_value=mock_result)

            resp = await lxc_client.post(
                "/api/store/install-v2",
                json={
                    "app_id": "gitea-lxc",
                    "admin_password": "hunter2",
                    "metadata": {"method": "lxc"},
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["status"] == "installed"
        instance.install.assert_awaited_once()
        call_kwargs = instance.install.call_args.kwargs
        assert call_kwargs["admin_password"] == "hunter2"

    async def test_docker_install_does_not_require_password(self, lxc_client):
        with patch("tinyagentos.routes.store_install.LXCInstaller") as MockLXC:
            resp = await lxc_client.post(
                "/api/store/install-v2",
                json={"app_id": "gitea", "metadata": {"method": "docker"}},
            )
        # Must not 400 with password error — docker path is unaffected.
        assert resp.status_code != 400 or "admin_password" not in resp.json().get("error", "")
        MockLXC.assert_not_called()

    async def test_lxc_uninstall_destroys_container(self, lxc_client):
        """Uninstalling an LXC app must call LXCInstaller.uninstall before store uninstall."""
        with patch(
            "tinyagentos.routes.store_install.LXCInstaller",
        ) as MockInstaller:
            instance = MockInstaller.return_value
            instance.uninstall = AsyncMock(return_value={"success": True})

            resp = await lxc_client.post(
                "/api/store/uninstall-v2",
                json={"app_id": "gitea-lxc", "metadata": {"method": "lxc"}},
            )

        assert resp.status_code == 200
        instance.uninstall.assert_awaited_once_with("gitea-lxc", target_remote=None)

    async def test_lxc_uninstall_container_error_blocks_store_removal(self, lxc_client):
        """If container destroy fails, return 500 and keep the store record so
        the user doesn't end up with an orphan container that taOS no longer
        tracks. Addresses the CodeRabbit finding that let migrated containers
        live on while the app_id was marked uninstalled."""
        with patch(
            "tinyagentos.routes.store_install.LXCInstaller",
        ) as MockInstaller:
            instance = MockInstaller.return_value
            instance.uninstall = AsyncMock(side_effect=RuntimeError("container gone"))

            resp = await lxc_client.post(
                "/api/store/uninstall-v2",
                json={"app_id": "gitea-lxc", "metadata": {"method": "lxc"}},
            )

        assert resp.status_code == 500
        data = resp.json()
        assert "container_error" in data
        assert "container gone" in data["container_error"]


# ---------------------------------------------------------------------------
# Generic (non-Gitea) LXC service install acceptance tests
# ---------------------------------------------------------------------------


class TestLXCInstallerGenericService:
    """Acceptance tests for a generic (non-Gitea) LXC service install.

    The installer is today hard-wired to Gitea: it writes gitea.service,
    downloads a binary from dl.gitea.com, renders /etc/gitea/app.ini and
    unconditionally requires admin_password. A manifest that declares only
    image, service_name, ports, ui_port, ui_path and state_paths should not be
    forced through that Gitea path. Each test pins one piece of the behaviour
    the generic path MUST provide once it lands. They are xfail(strict=True):
    green today by failing as expected, and the moment the implementation
    ships they XPASS, which strict mode flips into a failure that forces the
    marker to be removed.
    """

    async def _run_generic_install(
        self,
        captured_cmds: list,
        *,
        admin_password: str = "secret",
        host_port: int = 13000,
    ) -> tuple[dict, AsyncMock]:
        installer = LXCInstaller()
        proxy_mock = AsyncMock(return_value={"success": True})

        async def fake_exec(container_name, cmd, timeout=300):
            captured_cmds.append(cmd)
            if "hostname" in cmd or "-I" in cmd:
                return (0, "10.0.0.2")
            return (0, "")

        with (
            patch(
                "tinyagentos.installers.lxc_installer.containers.container_exists",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.create_container",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.exec_in_container",
                side_effect=fake_exec,
            ),
            patch(
                "tinyagentos.installers.lxc_installer.containers.add_proxy_device",
                proxy_mock,
            ),
            patch(
                "tinyagentos.installers.lxc_installer.allocate_host_port",
                return_value=host_port,
            ),
        ):
            result = await installer.install(
                "taosr1",
                GENERIC_INSTALL_CONFIG,
                admin_password=admin_password,
                taos_username="jay",
                taos_email="jay@example.com",
            )
        return result, proxy_mock

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="generic LXC install path not implemented; owned by @hermes",
    )
    async def test_systemd_unit_named_from_service_name(self):
        captured: list[list[str]] = []
        await self._run_generic_install(captured)
        full_cmds = [" ".join(c) for c in captured]
        assert any(
            "/etc/systemd/system/taosr1.service" in cmd for cmd in full_cmds
        ), "systemd unit must be named from service_name (taosr1.service)"
        assert not any(
            "/etc/systemd/system/gitea.service" in cmd for cmd in full_cmds
        ), "must not write the Gitea-specific gitea.service unit"

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="generic LXC install path not implemented; owned by @hermes",
    )
    async def test_no_gitea_binary_download(self):
        captured: list[list[str]] = []
        await self._run_generic_install(captured)
        full_cmds = [" ".join(c) for c in captured]
        assert not any(
            "dl.gitea.com" in cmd for cmd in full_cmds
        ), "must not make any request to dl.gitea.com"
        assert not any(
            "wget" in cmd and "gitea" in cmd.lower()
            for cmd in full_cmds
        ), "must not issue a Gitea binary download command"

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="generic LXC install path not implemented; owned by @hermes",
    )
    async def test_no_app_ini_written(self):
        captured: list[list[str]] = []
        await self._run_generic_install(captured)
        full_cmds = [" ".join(c) for c in captured]
        assert not any(
            "app.ini" in cmd for cmd in full_cmds
        ), "must not write /etc/gitea/app.ini for a generic service"

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="generic LXC install path not implemented; owned by @hermes",
    )
    async def test_declared_ports_are_published(self):
        captured: list[list[str]] = []
        _, proxy_mock = await self._run_generic_install(captured)
        assert proxy_mock.call_count == 1, (
            "add_proxy_device should be called once for the declared port"
        )
        connect = proxy_mock.call_args.kwargs.get("connect")
        assert connect == "tcp:127.0.0.1:8080", (
            f"proxy device must publish the declared port 8080 (ui_port), "
            f"got connect={connect!r}"
        )

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="generic LXC install path not implemented; owned by @hermes",
    )
    async def test_state_paths_created_in_container(self):
        captured: list[list[str]] = []
        await self._run_generic_install(captured)
        full_cmds = [" ".join(c) for c in captured]
        assert any(
            "/var/lib/taosr1" in cmd for cmd in full_cmds
        ), "state_paths must be created inside the container (/var/lib/taosr1)"

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason="generic LXC install path not implemented; owned by @hermes",
    )
    async def test_admin_password_not_required_without_admin_user(self):
        captured: list[list[str]] = []
        result, _ = await self._run_generic_install(captured, admin_password="")
        assert result["success"] is True, (
            "generic manifest with no admin user should not require admin_password"
        )
