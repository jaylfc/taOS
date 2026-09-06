import json
import pytest
from unittest.mock import AsyncMock, patch
from tinyagentos.containers.docker import DockerBackend


class TestDockerList:
    @pytest.mark.asyncio
    async def test_parses_docker_output(self):
        lines = '{"Names":"taos-agent-test","State":"running","ID":"abc123"}\n'
        inspect_output = json.dumps([{"NetworkSettings": {"Networks": {"bridge": {"IPAddress": "172.17.0.2"}}}}])

        call_count = 0
        async def mock_run(cmd, timeout=120):
            nonlocal call_count
            call_count += 1
            if "ps" in cmd:
                return (0, lines)
            if "inspect" in cmd:
                return (0, inspect_output)
            return (0, "")

        backend = DockerBackend()
        with patch.object(backend, "_run", side_effect=mock_run):
            containers = await backend.list_containers()
            assert len(containers) == 1
            assert containers[0].name == "taos-agent-test"

    @pytest.mark.asyncio
    async def test_handles_failure(self):
        backend = DockerBackend()
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock:
            mock.return_value = (1, "error")
            containers = await backend.list_containers()
            assert containers == []


class TestDockerCreate:
    @pytest.mark.asyncio
    async def test_creates_container(self):
        backend = DockerBackend()
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "container_id")
            result = await backend.create_container("agent-test", "ubuntu:22.04", "2GB", 2)
            assert result["success"] is True
            call_args = mock.call_args[0][0]
            assert "docker" in call_args
            assert "run" in call_args
            assert "--name" in call_args

    @pytest.mark.asyncio
    async def test_handles_failure(self):
        backend = DockerBackend()
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock:
            mock.return_value = (1, "error")
            result = await backend.create_container("agent-test", "ubuntu:22.04")
            assert result["success"] is False

    @pytest.mark.asyncio
    async def test_publishes_ports_bound_to_localhost_only(self):
        """App Runtime M4: a published port must bind to 127.0.0.1, never
        0.0.0.0 -- only the controller may reach a container app's backend."""
        backend = DockerBackend()
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "container_id")
            result = await backend.create_container(
                "taos-app-echo", "myimage:latest", ports=[(13042, 5678)],
            )
            assert result["success"] is True
            call_args = mock.call_args[0][0]
            assert "-p" in call_args
            assert "127.0.0.1:13042:5678" in call_args
            # never publish on all interfaces
            assert not any(a.startswith("0.0.0.0:") for a in call_args)

    @pytest.mark.asyncio
    async def test_no_ports_argument_publishes_nothing(self):
        """Backward compatible: existing agent-deploy callers never pass ports."""
        backend = DockerBackend()
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "container_id")
            await backend.create_container("agent-test", "ubuntu:22.04")
            call_args = mock.call_args[0][0]
            assert "-p" not in call_args


class TestDockerLifecycle:
    @pytest.mark.asyncio
    async def test_start(self):
        backend = DockerBackend()
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "")
            result = await backend.start_container("agent-test")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_stop(self):
        backend = DockerBackend()
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "")
            result = await backend.stop_container("agent-test")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_destroy(self):
        backend = DockerBackend()
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "")
            result = await backend.destroy_container("agent-test")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_exec(self):
        backend = DockerBackend()
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "hello")
            code, output = await backend.exec_in_container("agent-test", ["echo", "hello"])
            assert code == 0
            assert output == "hello"

    @pytest.mark.asyncio
    async def test_logs(self):
        backend = DockerBackend()
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "log line 1\nlog line 2")
            logs = await backend.get_container_logs("agent-test", lines=50)
            assert "log line 1" in logs

    @pytest.mark.asyncio
    async def test_push_file(self):
        backend = DockerBackend()
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "")
            code, output = await backend.push_file("agent-test", "/tmp/file.txt", "/etc/file.txt")
            assert code == 0


class TestDockerSetRootQuota:
    @pytest.mark.asyncio
    async def test_quota_success(self):
        backend = DockerBackend()
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "")
            result = await backend.set_root_quota("agent-test", 40)
            assert result["success"] is True
            assert "40" in result["note"]
            cmd = mock.call_args[0][0]
            assert "--storage-opt" in cmd
            assert "size=40g" in cmd

    @pytest.mark.asyncio
    async def test_quota_overlay2_no_pquota_returns_soft(self):
        """overlay2 without pquota: treat as success with note, not hard failure."""
        backend = DockerBackend()
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock:
            mock.return_value = (1, "storage driver does not support storage-opt")
            result = await backend.set_root_quota("agent-test", 40)
            assert result["success"] is True
            assert "storage driver does not enforce quota" in result["note"]

    @pytest.mark.asyncio
    async def test_quota_genuine_failure(self):
        backend = DockerBackend()
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock:
            mock.return_value = (1, "connection refused")
            result = await backend.set_root_quota("agent-test", 40)
            assert result["success"] is False

    @pytest.mark.asyncio
    async def test_create_container_with_root_size_gib(self):
        backend = DockerBackend()
        run_calls = []
        async def mock_run(cmd, timeout=120):
            run_calls.append(cmd)
            return (0, "")
        with patch.object(backend, "_run", side_effect=mock_run):
            result = await backend.create_container("agent-test", root_size_gib=40)
        assert result["success"] is True
        quota_calls = [c for c in run_calls if "--storage-opt" in c]
        assert quota_calls, "expected a docker container update --storage-opt call"


class TestPodmanBinary:
    @pytest.mark.asyncio
    async def test_uses_podman_binary(self):
        backend = DockerBackend(binary="podman")
        with patch.object(backend, "_run", new_callable=AsyncMock) as mock:
            mock.return_value = (0, "")
            await backend.start_container("test")
            call_args = mock.call_args[0][0]
            assert call_args[0] == "podman"
