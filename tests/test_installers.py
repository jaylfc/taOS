import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from tinyagentos.installers.base import get_installer
from tinyagentos.installers.pip_installer import PipInstaller
from tinyagentos.installers.docker_installer import DockerInstaller
from tinyagentos.installers.download_installer import DownloadInstaller
from tinyagentos.installers.port_allocator import (
    RESERVED_PORTS,
    allocate_host_port,
    _POOL_START,
    _POOL_END,
)


class TestGetInstaller:
    def test_returns_pip(self):
        assert isinstance(get_installer("pip"), PipInstaller)

    def test_returns_docker(self):
        assert isinstance(get_installer("docker"), DockerInstaller)

    def test_returns_download(self):
        assert isinstance(get_installer("download"), DownloadInstaller)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown install method"):
            get_installer("unknown")


class TestPipInstaller:
    @pytest.mark.asyncio
    async def test_install_creates_venv(self, tmp_path):
        installer = PipInstaller(apps_dir=tmp_path)
        with patch("tinyagentos.installers.pip_installer.run_cmd", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "")
            result = await installer.install("testapp", {"method": "pip", "package": "testpkg"})
            assert result["success"] is True
            # Should have called python -m venv and pip install
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("venv" in c for c in calls)
            assert any("pip" in c and "testpkg" in c for c in calls)

    @pytest.mark.asyncio
    async def test_uninstall_removes_dir(self, tmp_path):
        installer = PipInstaller(apps_dir=tmp_path)
        app_dir = tmp_path / "testapp"
        app_dir.mkdir()
        (app_dir / "venv").mkdir()
        result = await installer.uninstall("testapp")
        assert result["success"] is True
        assert not app_dir.exists()


class TestDockerInstaller:
    @pytest.mark.asyncio
    async def test_install_writes_compose(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        install_config = {
            "method": "docker",
            "image": "gitea/gitea:1.22",
            "volumes": ["data:/data"],
            "env": {"ROOT_URL": "http://localhost:3000"},
        }
        with patch("tinyagentos.installers.docker_installer.run_cmd", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "")
            result = await installer.install("gitea", install_config)
            assert result["success"] is True
            compose_file = tmp_path / "gitea" / "docker-compose.yaml"
            assert compose_file.exists()

    def test_generate_compose_declares_named_volumes_and_omits_version(self, tmp_path):
        # Regression: named volumes (e.g. searxng's "config:/etc/searxng") must
        # be declared at the top level or compose rejects the project with
        # "refers to undefined volume". The obsolete `version` key is dropped.
        installer = DockerInstaller(apps_dir=tmp_path)
        compose, host_port = installer._generate_compose("searxng", {
            "image": "searxng/searxng:latest",
            "volumes": ["config:/etc/searxng", "/host/path:/data"],
            "ports": [8080],
        })
        assert "version" not in compose
        assert compose["volumes"] == {"config": None}  # only the named volume
        assert compose["services"]["searxng"]["volumes"] == ["config:/etc/searxng", "/host/path:/data"]
        # Host port must be in the managed pool, never 8080
        assert host_port is not None
        assert _POOL_START <= host_port < _POOL_END
        assert host_port not in RESERVED_PORTS
        assert compose["services"]["searxng"]["ports"] == [f"{host_port}:8080"]

    def test_generate_compose_omits_volumes_block_for_bind_mounts_only(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        compose, host_port = installer._generate_compose("app", {
            "image": "x:1",
            "volumes": ["/host:/data", "./rel:/r", "~/h:/hh"],
        })
        assert "volumes" not in compose  # no named volumes → no top-level block
        assert host_port is None

    def test_write_config_files_creates_files_with_substitution(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        installer._write_config_files("searxng", {
            "config_files": [
                {"path": "settings.yml", "content": 'secret_key: "{secret_key}"'},
                {"path": "sub/deep.yml", "content": "key: static"},
            ]
        })
        settings_yml = tmp_path / "searxng" / "settings.yml"
        deep_yml = tmp_path / "searxng" / "sub" / "deep.yml"
        assert settings_yml.exists()
        assert deep_yml.exists()

        content = settings_yml.read_text()
        # {secret_key} must be replaced with a 64-hex-char random string
        assert "{secret_key}" not in content
        assert content.startswith('secret_key: "')
        key_val = content.split('"')[1]  # extract the value between quotes
        assert len(key_val) == 64
        assert all(c in "0123456789abcdef" for c in key_val)

        assert deep_yml.read_text() == "key: static"

    def test_secret_key_file_is_owner_only(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        installer._write_config_files("searxng", {
            "config_files": [{"path": "settings.yml", "content": '{secret_key}'}]
        })
        secret_path = tmp_path / "searxng" / ".secret_key"
        assert secret_path.exists()
        assert (secret_path.stat().st_mode & 0o777) == 0o600

    def test_empty_or_malformed_persisted_secret_is_regenerated(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        app_dir = tmp_path / "searxng"
        app_dir.mkdir(parents=True)
        (app_dir / ".secret_key").write_text("   ")  # empty/whitespace from a prior bad write
        installer._write_config_files("searxng", {
            "config_files": [{"path": "settings.yml", "content": 'k: "{secret_key}"'}]
        })
        key_val = (app_dir / "settings.yml").read_text().split('"')[1]
        assert len(key_val) == 64
        assert all(c in "0123456789abcdef" for c in key_val)

    def test_write_config_files_noop_when_no_config_files(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        installer._write_config_files("app", {"image": "x:1"})
        # Should not raise, should not create the app dir
        assert not (tmp_path / "app").exists()

    def test_write_config_files_rejects_missing_path(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        with pytest.raises(ValueError, match="missing required key 'path'"):
            installer._write_config_files("app", {
                "config_files": [{"content": "x"}]
            })

    def test_write_config_files_rejects_missing_content(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        with pytest.raises(ValueError, match="missing required key 'content'"):
            installer._write_config_files("app", {
                "config_files": [{"path": "f.yml"}]
            })

    def test_write_config_files_rejects_absolute_path(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        with pytest.raises(ValueError, match="must be relative"):
            installer._write_config_files("app", {
                "config_files": [{"path": "/etc/passwd", "content": "x"}]
            })

    def test_write_config_files_rejects_dotdot_path(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        with pytest.raises(ValueError, match="must not contain '..'"):
            installer._write_config_files("app", {
                "config_files": [{"path": "../escape.yml", "content": "x"}]
            })

    def test_write_config_files_rejects_path_resolving_outside_app_dir(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        # Create a symlink that points outside app_dir
        app_dir = tmp_path / "app"
        app_dir.mkdir(parents=True)
        symlink = app_dir / "escape"
        symlink.symlink_to(tmp_path / "outside")
        with pytest.raises(ValueError, match="resolves outside app_dir"):
            installer._write_config_files("app", {
                "config_files": [{"path": "escape/target.yml", "content": "x"}]
            })

    def test_write_config_files_persists_secret_key_across_calls(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        config = {
            "config_files": [
                {"path": "settings.yml", "content": 'secret_key: "{secret_key}"'},
            ]
        }
        installer._write_config_files("searxng", config)
        first_key = (tmp_path / "searxng" / "settings.yml").read_text()
        # Call again — must reuse the persisted secret, not generate a new one
        installer._write_config_files("searxng", config)
        second_key = (tmp_path / "searxng" / "settings.yml").read_text()
        assert first_key == second_key
        # The .secret_key file must exist
        assert (tmp_path / "searxng" / ".secret_key").exists()

    @pytest.mark.asyncio
    async def test_start_runs_compose_up(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        app_dir = tmp_path / "gitea"
        app_dir.mkdir()
        (app_dir / "docker-compose.yaml").write_text("version: '3'")
        with patch("tinyagentos.installers.docker_installer.run_cmd", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "")
            result = await installer.start("gitea")
            assert result["success"] is True
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("up" in c and "-d" in c for c in calls)


class TestLinkwardenCompose:
    @pytest.mark.asyncio
    async def test_generate_compose_linkwarden_has_postgres_companion(self, tmp_path):
        installer = DockerInstaller(apps_dir=tmp_path)
        compose, host_port = installer._generate_compose(
            "linkwarden",
            {
                "image": "ghcr.io/linkwarden/linkwarden:latest",
                "volumes": ["data:/data/data"],
                "ports": [3000],
                "env": {
                    "NEXTAUTH_SECRET": "changeme",
                    "NEXTAUTH_URL": "http://localhost:3000",
                    "DATABASE_URL": "postgresql://linkwarden:{secret_key}@postgres:5432/linkwarden",
                },
                "companions": [
                    {
                        "name": "postgres",
                        "image": "postgres:16-alpine",
                        "volumes": ["pgdata:/var/lib/postgresql/data"],
                        "env": {
                            "POSTGRES_PASSWORD": "{secret_key}",
                            "POSTGRES_USER": "linkwarden",
                            "POSTGRES_DB": "linkwarden",
                        },
                    }
                ],
            },
        )
        # Compose must have both linkwarden and postgres services
        assert "linkwarden" in compose["services"]
        assert "postgres" in compose["services"]
        # postgres service must use the alpine image
        pg_service = compose["services"]["postgres"]
        assert pg_service["image"] == "postgres:16-alpine"
        # postgres must have a named volume
        assert "volumes" in pg_service
        pg_volumes = pg_service["volumes"]
        assert any(
            v.startswith("pgdata:") or (":" in v and v.split(":")[0] == "pgdata")
            for v in pg_volumes
        )
        # linkwarden must have DATABASE_URL pointing at the postgres service
        lw_env = compose["services"]["linkwarden"]["environment"]
        docker_url = lw_env["DATABASE_URL"]
        assert "postgres" in docker_url
        assert "localhost" not in docker_url
        # The host in DATABASE_URL must resolve to the postgres service name
        # Format: postgresql://[user[:password]@][host][:port][/dbname]
        # e.g. postgresql://linkwarden:{password}@postgres:5432/linkwarden
        assert docker_url.startswith("postgresql://linkwarden:")
        # Extract host (between @ and :port or /dbname)
        after_at = docker_url.split("@")[1]  # e.g. "postgres:5432/linkwarden"
        host_with_port = after_at.split("/")[0]  # e.g. "postgres:5432"
        host_part = host_with_port.split(":")[0]  # e.g. "postgres"
        assert host_part == "postgres", f"Expected host 'postgres', got '{host_part}'"
        # Host port must be in the managed pool, not 3000
        assert host_port is not None
        assert _POOL_START <= host_port < _POOL_END
        assert host_port not in RESERVED_PORTS

    @pytest.mark.asyncio
    async def test_generate_compose_linkwarden_secret_key_persisted(self, tmp_path):
        """Verify {secret_key} is replaced with a persisted 64-hex-char secret."""
        installer = DockerInstaller(apps_dir=tmp_path)
        # Verify {secret_key} is replaced with a persisted 64-hex-char secret.
        installer = DockerInstaller(apps_dir=tmp_path)
        # First install - should generate a secret
        compose1, _ = installer._generate_compose(
            "linkwarden",
            {
                "image": "ghcr.io/linkwarden/linkwarden:latest",
                "volumes": ["data:/data/data"],
                "ports": [3000],
                "env": {
                    "NEXTAUTH_SECRET": "changeme",
                    "NEXTAUTH_URL": "http://localhost:3000",
                    "DATABASE_URL": "postgresql://linkwarden:{secret_key}@postgres:5432/linkwarden",
                },
                "companions": [
                    {
                        "name": "postgres",
                        "image": "postgres:16-alpine",
                        "volumes": ["pgdata:/var/lib/postgresql/data"],
                        "env": {
                            "POSTGRES_PASSWORD": "{secret_key}",
                            "POSTGRES_USER": "linkwarden",
                            "POSTGRES_DB": "linkwarden",
                        },
                    }
                ],
            },
        )
        # Verify {secret_key} was replaced
        for env_key, env_val in compose1["services"]["linkwarden"]["environment"].items():
            assert "{secret_key}" not in str(env_val)
        # Check DATABASE_URL host resolves to postgres
        db_url = compose1["services"]["linkwarden"]["environment"]["DATABASE_URL"]
        assert "postgres" in db_url
        assert "localhost" not in db_url
        after_at = db_url.split("@")[1]
        host_with_port = after_at.split("/")[0]
        host_part = host_with_port.split(":")[0]
        assert host_part == "postgres", f"Expected host 'postgres', got '{host_part}'"

        # Second install with same app_dir - should reuse the same secret
        compose2, _ = installer._generate_compose(
            "linkwarden",
            {
                "image": "ghcr.io/linkwarden/linkwarden:latest",
                "volumes": ["data:/data/data"],
                "ports": [3000],
                "env": {
                    "NEXTAUTH_SECRET": "changeme",
                    "NEXTAUTH_URL": "http://localhost:3000",
                    "DATABASE_URL": "postgresql://linkwarden:{secret_key}@postgres:5432/linkwarden",
                },
                "companions": [
                    {
                        "name": "postgres",
                        "image": "postgres:16-alpine",
                        "volumes": ["pgdata:/var/lib/postgresql/data"],
                        "env": {
                            "POSTGRES_PASSWORD": "{secret_key}",
                            "POSTGRES_USER": "linkwarden",
                            "POSTGRES_DB": "linkwarden",
                        },
                    }
                ],
            },
        )
        # Same secret should be reused
        for env_key, env_val in compose2["services"]["linkwarden"]["environment"].items():
            assert "{secret_key}" not in str(env_val)
        # The secret values should match across installs
        db_url_1 = compose1["services"]["linkwarden"]["environment"]["DATABASE_URL"]
        db_url_2 = compose2["services"]["linkwarden"]["environment"]["DATABASE_URL"]
        assert db_url_1 == db_url_2

    @pytest.mark.asyncio
    async def test_generate_compose_linkwarden_no_companions(self, tmp_path):
        """Verify linkwarden without companions still works (single-service compose)."""
        installer = DockerInstaller(apps_dir=tmp_path)
        compose, host_port = installer._generate_compose(
            "linkwarden",
            {
                "image": "ghcr.io/linkwarden/linkwarden:latest",
                "volumes": ["data:/data/data"],
                "ports": [3000],
                "env": {
                    "NEXTAUTH_SECRET": "changeme",
                    "NEXTAUTH_URL": "http://localhost:3000",
                    # No companions - DATABASE_URL would point at localhost which is fine
                    # for a single-service setup, but the app wouldn't have a real DB
                    "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/linkwarden",
                },
                # No 'companions' key
            },
        )
        # Should still produce a single-service compose
        assert "linkwarden" in compose["services"]
        assert "postgres" not in compose["services"]
        assert host_port is not None
        assert _POOL_START <= host_port < _POOL_END
        # DATABASE_URL should still point at localhost since there's no companion
        lw_env = compose["services"]["linkwarden"]["environment"]
        docker_url = lw_env["DATABASE_URL"]
        assert "localhost" in docker_url


class TestDownloadInstaller:
    @pytest.mark.asyncio
    async def test_install_downloads_file(self, tmp_path):
        installer = DownloadInstaller(models_dir=tmp_path)
        variant = {
            "id": "q4_k_m",
            "download_url": "https://example.com/model.gguf",
            "size_mb": 100,
            "sha256": "abc123",
        }
        with patch("tinyagentos.installers.download_installer.download_file", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = tmp_path / "qwen3-8b-q4_k_m.gguf"
            result = await installer.install("qwen3-8b", {"method": "download"}, variant=variant)
            assert result["success"] is True
            mock_dl.assert_called_once()


class TestPortAllocator:
    """Allocator must never return a core/reserved port."""

    def test_allocate_returns_port_in_pool(self):
        port = allocate_host_port("searxng")
        assert _POOL_START <= port < _POOL_END

    def test_allocate_never_returns_reserved_port(self):
        # Verify none of the well-known reserved ports are ever returned.
        seen: set[int] = set()
        # Generate ports for a range of distinct app IDs.
        for i in range(50):
            port = allocate_host_port(f"testapp-{i}")
            seen.add(port)
        assert seen.isdisjoint(RESERVED_PORTS), (
            f"Allocator returned reserved port(s): {seen & RESERVED_PORTS}"
        )

    def test_allocate_skips_8080(self):
        # 8080 is the searxng container port — the host port must never be 8080.
        assert 8080 in RESERVED_PORTS
        port = allocate_host_port("searxng")
        assert port != 8080

    def test_allocate_skips_taos_port(self):
        assert 6969 in RESERVED_PORTS
        port = allocate_host_port("some-app")
        assert port != 6969

    def test_allocate_is_deterministic(self):
        # Same app_id always gets the same preferred starting slot.
        port_a = allocate_host_port("stable-app")
        port_b = allocate_host_port("stable-app")
        assert port_a == port_b

    def test_allocate_skips_in_use_port(self):
        import socket
        # Hold an OS-assigned free port so the allocator is forced to skip it.
        # A fixed port collides across workers under `pytest -n auto` (EADDRINUSE
        # flakiness); an ephemeral port (bind to 0) is unique per run. Bind
        # 0.0.0.0 to match the allocator's _is_port_free check and listen() so the
        # port is unambiguously in use (no SO_REUSEADDR, which could let the
        # allocator's own bind succeed and break the assertion).
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("0.0.0.0", 0))
            s.listen(1)
            blocked = s.getsockname()[1]
            # Temporarily stub allocate_host_port's pool to surround `blocked`.
            from tinyagentos.installers import port_allocator
            original_start = port_allocator._POOL_START
            original_end = port_allocator._POOL_END
            port_allocator._POOL_START = blocked
            port_allocator._POOL_END = blocked + 5
            try:
                port = allocate_host_port("testapp-blocked")
                assert port != blocked
                assert blocked <= port < blocked + 5
            finally:
                port_allocator._POOL_START = original_start
                port_allocator._POOL_END = original_end

    def test_docker_installer_searxng_host_port_not_8080(self, tmp_path):
        """End-to-end: searxng compose must not map host 8080."""
        installer = DockerInstaller(apps_dir=tmp_path)
        compose, host_port = installer._generate_compose("searxng", {
            "image": "searxng/searxng:latest",
            "volumes": ["config:/etc/searxng"],
            "ports": [8080],
        })
        assert host_port is not None
        assert host_port != 8080
        assert host_port not in RESERVED_PORTS
        # Container-internal port must still be 8080.
        port_mappings = compose["services"]["searxng"]["ports"]
        assert len(port_mappings) == 1
        host_side, _, container_side = port_mappings[0].partition(":")
        assert int(host_side) == host_port
        assert int(container_side) == 8080
