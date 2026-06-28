import json
import pytest
from unittest.mock import AsyncMock, patch, call
from tinyagentos.containers import (
    list_containers, create_container, set_root_quota, set_env,
    start_container, stop_container, destroy_container,
    container_exists,
    _parse_memory, ContainerInfo,
)


class TestParseMemory:
    def test_gb(self):
        assert _parse_memory("2GB") == 2048

    def test_mb(self):
        assert _parse_memory("512MB") == 512

    def test_zero(self):
        assert _parse_memory("0") == 0

    def test_empty(self):
        assert _parse_memory("") == 0


class TestListContainers:
    @pytest.mark.asyncio
    async def test_parses_incus_output(self):
        mock_output = json.dumps([
            {
                "name": "taos-agent-naira",
                "status": "Running",
                "config": {"limits.memory": "2GB", "limits.cpu": "2"},
                "state": {
                    "network": {
                        "eth0": {
                            "addresses": [
                                {"family": "inet", "address": "10.0.0.5", "scope": "global"}
                            ]
                        }
                    }
                }
            },
            {
                "name": "not-an-agent",
                "status": "Running",
                "config": {},
                "state": {"network": {}},
            }
        ])
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, mock_output)
            containers = await list_containers()
            assert len(containers) == 1
            assert containers[0].name == "taos-agent-naira"
            assert containers[0].status == "Running"
            assert containers[0].ip == "10.0.0.5"
            assert containers[0].memory_mb == 2048

    @pytest.mark.asyncio
    async def test_handles_incus_failure(self):
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (1, "error")
            containers = await list_containers()
            assert containers == []


class TestCreateContainer:
    @pytest.mark.asyncio
    async def test_creates_and_configures(self):
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "")
            result = await create_container("taos-agent-test", memory_limit="1GB", cpu_limit=1)
            assert result["success"] is True
            # Should have called: launch, set memory, set cpu
            assert mock_run.call_count == 3

    @pytest.mark.asyncio
    async def test_handles_launch_failure(self):
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (1, "launch failed")
            result = await create_container("taos-agent-test")
            assert result["success"] is False

    @pytest.mark.asyncio
    async def test_remote_qualifies_image_and_target_and_skips_mounts(self):
        """remote= creates on the worker: image + instance name are
        <remote>:-qualified and host bind mounts are skipped."""
        calls = []

        async def mock_run(cmd, timeout=120):
            calls.append(cmd)
            return (0, "")

        with patch("tinyagentos.containers._run", side_effect=mock_run):
            result = await create_container(
                "taos-agent-bob",
                image="taos-hermes-base",
                env={"FOO": "bar"},
                mounts=[("/host/path", "/ctr/path")],
                root_size_gib=None,
                remote="fedora-worker",
            )
        assert result["success"] is True
        assert result["remote"] == "fedora-worker"
        # launch references the remote-qualified image + instance.
        launch = calls[0]
        assert launch[:2] == ["incus", "launch"]
        assert launch[2] == "fedora-worker:taos-hermes-base"
        assert launch[3] == "fedora-worker:taos-agent-bob"
        # env is set against the remote-qualified target.
        env_calls = [c for c in calls if "environment.FOO=bar" in c]
        assert env_calls and env_calls[0][3] == "fedora-worker:taos-agent-bob"
        # No bind-mount device was added (host paths don't exist on the worker).
        assert not [c for c in calls if "disk" in c and "taos-mount-0" in c]
        # raw.idmap (a local trace-mount concern) must NOT be set for a remote create.
        assert not [c for c in calls if "raw.idmap" in c]

    @pytest.mark.asyncio
    async def test_remote_does_not_double_qualify_image_server_ref(self):
        """A remote-image-server ref (cold fallback) keeps its own remote and is
        NOT prefixed with the worker remote, else incus rejects it."""
        calls = []

        async def mock_run(cmd, timeout=120):
            calls.append(cmd)
            return (0, "")

        with patch("tinyagentos.containers._run", side_effect=mock_run):
            await create_container(
                "taos-agent-bob",
                image="images:debian/bookworm",
                root_size_gib=None,
                remote="fedora-worker",
            )
        launch = calls[0]
        assert launch[2] == "images:debian/bookworm"
        assert launch[3] == "fedora-worker:taos-agent-bob"


class TestSetRootQuota:
    @pytest.mark.asyncio
    async def test_success_via_override(self):
        """set_root_quota uses incus config device override (not set) as primary path."""
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "")
            result = await set_root_quota("taos-agent-test", 40)
            assert result["success"] is True
            assert "40" in result["note"]
            cmd = mock_run.call_args[0][0]
            assert "incus" in cmd
            assert "config" in cmd
            assert "device" in cmd
            assert "override" in cmd
            assert "root" in cmd
            assert "size=40GiB" in cmd

    @pytest.mark.asyncio
    async def test_fallback_to_set_when_override_already_exists(self):
        """Falls back to device set when override reports 'already exists'."""
        calls = []
        async def mock_run(cmd, timeout=120):
            calls.append(cmd)
            if "override" in cmd:
                return (1, "Device already exists")
            return (0, "")

        with patch("tinyagentos.containers._run", side_effect=mock_run):
            result = await set_root_quota("taos-agent-test", 40)
        assert result["success"] is True
        # First call must be override, second must be set
        assert "override" in calls[0]
        assert "set" in calls[1]

    @pytest.mark.asyncio
    async def test_failure_returns_success_false(self):
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (1, "device not found")
            result = await set_root_quota("taos-agent-test", 40)
            assert result["success"] is False
            assert "device not found" in result["note"]

    @pytest.mark.asyncio
    async def test_create_container_passes_root_size_gib(self):
        """root_size_gib passed to create_container triggers set_root_quota."""
        calls = []
        async def mock_run(cmd, timeout=120):
            calls.append(cmd)
            return (0, "")

        with patch("tinyagentos.containers._run", side_effect=mock_run):
            result = await create_container("taos-agent-test", root_size_gib=40)
        assert result["success"] is True
        # At least one call should set the root size via override
        quota_calls = [c for c in calls if "size=40GiB" in " ".join(c)]
        assert quota_calls, "expected a quota set call with size=40GiB"
        override_calls = [c for c in calls if "override" in c]
        assert override_calls, "expected an override call for profile-inherited root device"


class TestSetEnv:
    @pytest.mark.asyncio
    async def test_env_uses_key_equals_value_form(self):
        """incus env set uses key=value single-arg form (not separate positional value)."""
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "")
            result = await set_env("taos-agent-test", "MY_KEY", "myvalue")
            assert result["success"] is True
            cmd = mock_run.call_args[0][0]
            # The key=value must appear as one element, not split across two
            assert "environment.MY_KEY=myvalue" in cmd
            # The value must NOT appear as a separate trailing argument
            assert cmd[-1] == "environment.MY_KEY=myvalue"

    @pytest.mark.asyncio
    async def test_env_dash_prefixed_value_no_flag_error(self):
        """A token value starting with '-' must not be parsed as a CLI flag.

        Regression for: incus env set TAOS_LOCAL_TOKEN failed:
        Error: unknown shorthand flag: 'X' in -XOvCacuHM1H...
        """
        dash_token = "-Xabc123secrettoken"
        calls = []
        async def mock_run(cmd, timeout=120):
            calls.append(cmd)
            # Simulate incus succeeding (no flag parse error)
            return (0, "")

        with patch("tinyagentos.containers._run", side_effect=mock_run):
            result = await set_env("taos-agent-test", "TAOS_LOCAL_TOKEN", dash_token)
        assert result["success"] is True
        assert len(calls) == 1
        cmd = calls[0]
        # Value embedded in key=value arg — never a standalone arg that could be a flag
        assert f"environment.TAOS_LOCAL_TOKEN={dash_token}" in cmd
        # Confirm the token is NOT a separate final element
        assert cmd[-1] != dash_token

    @pytest.mark.asyncio
    async def test_create_container_env_uses_key_equals_value_form(self):
        """create_container env loop also uses key=value form."""
        calls = []
        async def mock_run(cmd, timeout=120):
            calls.append(cmd)
            return (0, "")

        with patch("tinyagentos.containers._run", side_effect=mock_run):
            result = await create_container(
                "taos-agent-test",
                env={"TAOS_LOCAL_TOKEN": "-Xsecret", "OTHER": "val"},
            )
        assert result["success"] is True
        env_calls = [c for c in calls if any("environment." in e for e in c)]
        assert len(env_calls) == 2
        for c in env_calls:
            # Each env arg must be a single key=value element
            env_args = [e for e in c if e.startswith("environment.")]
            assert len(env_args) == 1
            assert "=" in env_args[0]


class TestContainerLifecycle:
    @pytest.mark.asyncio
    async def test_start(self):
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "")
            result = await start_container("taos-agent-test")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_stop(self):
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "")
            result = await stop_container("taos-agent-test")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_destroy(self):
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            # 1st call resolves the project (empty list -> ambient fallback),
            # then stop --force, then delete --force.
            mock_run.side_effect = [(0, "[]"), (0, ""), (0, "")]
            result = await destroy_container("taos-agent-test")
            assert result["success"] is True
            assert mock_run.call_count == 3
            # No --project flag when the container is not found in any project.
            stop_cmd = mock_run.call_args_list[1].args[0]
            assert "--project" not in stop_cmd


class TestSnapshotCreateSelfHeal:
    """snapshot_create must succeed in restricted projects (BUG A)."""

    @pytest.mark.asyncio
    async def test_self_heals_project_snapshot_restriction(self):
        from tinyagentos.containers import snapshot_create

        forbidden = (
            'Error: Failed to create instance snapshot: Project "user-999" '
            "doesn't allow for snapshot creation"
        )
        calls = []

        async def mock_run(cmd, timeout=120):
            calls.append(cmd)
            # 1) snapshot create -> forbidden
            # 2) project set restricted.snapshots allow -> ok
            # 3) snapshot create retry -> ok
            if cmd[:3] == ["incus", "snapshot", "create"] and len(calls) == 1:
                return (1, forbidden)
            if cmd[:3] == ["incus", "project", "set"]:
                return (0, "")
            return (0, "")

        with patch("tinyagentos.containers._run", side_effect=mock_run):
            result = await snapshot_create("taos-agent-x", "taos-archive-1")

        assert result["success"] is True
        # The project named in the error was relaxed before the retry.
        set_cmd = next(c for c in calls if c[:3] == ["incus", "project", "set"])
        assert set_cmd == [
            "incus", "project", "set", "user-999",
            "restricted.snapshots", "allow",
        ]
        # Two snapshot-create attempts: original + retry.
        snap_attempts = [c for c in calls if c[:3] == ["incus", "snapshot", "create"]]
        assert len(snap_attempts) == 2

    @pytest.mark.asyncio
    async def test_non_restriction_failure_does_not_retry(self):
        from tinyagentos.containers import snapshot_create

        calls = []

        async def mock_run(cmd, timeout=120):
            calls.append(cmd)
            return (1, "Error: storage pool is offline")

        with patch("tinyagentos.containers._run", side_effect=mock_run):
            result = await snapshot_create("taos-agent-x", "taos-archive-1")

        assert result["success"] is False
        # A non-restriction failure must NOT touch project config nor retry.
        assert all(c[:3] != ["incus", "project", "set"] for c in calls)
        assert len(calls) == 1


class TestResolveAgentContainer:
    """resolve_agent_container probes both naming conventions across projects."""

    @pytest.mark.asyncio
    async def test_resolves_legacy_name_across_projects(self):
        from tinyagentos.containers import resolve_agent_container

        listing = json.dumps([
            {"name": "taos-test", "project": "user-999"},
            {"name": "some-other-vm", "project": "default"},
        ])
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, listing)
            # Derived ``taos-agent-test`` is absent; legacy ``taos-test`` is found.
            name = await resolve_agent_container("test")
        assert name == "taos-test"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_taos_container_matches(self):
        from tinyagentos.containers import resolve_agent_container

        listing = json.dumps([{"name": "unrelated", "project": "default"}])
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, listing)
            assert await resolve_agent_container("test") is None

    @pytest.mark.asyncio
    async def test_list_all_taos_containers_filters_non_taos(self):
        from tinyagentos.containers import list_all_taos_containers

        listing = json.dumps([
            {"name": "taos-agent-a", "project": "default"},
            {"name": "taos-b", "project": "user-1"},
            {"name": "postgres", "project": "default"},
        ])
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, listing)
            out = await list_all_taos_containers()
        names = {c["name"] for c in out}
        assert names == {"taos-agent-a", "taos-b"}

    @pytest.mark.asyncio
    async def test_destroy_targets_restricted_project(self):
        """A container living in a restricted project (user-999) must be
        destroyed there, not left orphaned because the ambient project is
        'default'."""
        listing = json.dumps([{"name": "taos-agent-x", "project": "user-999"}])
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = [(0, listing), (0, ""), (0, "")]
            result = await destroy_container("taos-agent-x")
            assert result["success"] is True
            stop_cmd = mock_run.call_args_list[1].args[0]
            del_cmd = mock_run.call_args_list[2].args[0]
            assert stop_cmd[:4] == ["incus", "stop", "--project", "user-999"]
            assert del_cmd[:4] == ["incus", "delete", "--project", "user-999"]

    @pytest.mark.asyncio
    async def test_container_exists_finds_in_any_project(self):
        listing = json.dumps([
            {"name": "taos-agent-a", "project": "default"},
            {"name": "taos-agent-b", "project": "user-999"},
        ])
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, listing)
            assert await container_exists("taos-agent-b") is True
            assert await container_exists("taos-agent-missing") is False


class TestAddProxyDeviceSelfHeal:
    """Restricted multi-user incus projects block proxy devices; add_proxy_device
    self-heals by allowing them on the named project and retrying once."""

    @pytest.mark.asyncio
    async def test_relaxes_restricted_project_and_retries(self):
        from tinyagentos.containers import add_proxy_device
        forbidden = (
            'Invalid device "taos-proxy-litellm" on container '
            '"taos-agent-x" of project "user-999": Proxy devices are forbidden'
        )
        calls = []
        async def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:3] == ["incus", "config", "device"]:
                # first add fails, retry (after project relax) succeeds
                add_attempts = [c for c in calls if c[:3] == ["incus", "config", "device"]]
                return (1, forbidden) if len(add_attempts) == 1 else (0, "")
            if cmd[:3] == ["incus", "project", "set"]:
                return (0, "")
            return (0, "")
        with patch("tinyagentos.containers._run", new_callable=AsyncMock, side_effect=fake_run):
            res = await add_proxy_device("taos-agent-x", "taos-proxy-litellm",
                                         "tcp:127.0.0.1:4000", "tcp:127.0.0.1:4000")
        assert res["success"] is True
        assert ["incus", "project", "set", "user-999", "restricted.devices.proxy", "allow"] in calls
        # device add attempted twice (initial + retry)
        assert sum(1 for c in calls if c[:3] == ["incus", "config", "device"]) == 2

    @pytest.mark.asyncio
    async def test_non_forbidden_failure_not_retried(self):
        from tinyagentos.containers import add_proxy_device
        with patch("tinyagentos.containers._run", new_callable=AsyncMock, return_value=(1, "some other error")) as mr:
            res = await add_proxy_device("c", "d", "tcp:127.0.0.1:1", "tcp:127.0.0.1:1")
        assert res["success"] is False
        # only the single add attempt, no project-set self-heal
        assert mr.call_count == 1
