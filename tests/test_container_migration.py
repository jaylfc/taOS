"""Unit tests for container migration helpers and routes.

All tests mock the incus CLI helper — no real incus calls are made.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, call, patch

from tinyagentos.containers import migrate_container, remote_add, remote_generate_token, remote_list, remote_remove


# ---------------------------------------------------------------------------
# remote_add / remote_list / remote_remove
# ---------------------------------------------------------------------------

class TestRemoteAdd:
    @pytest.mark.asyncio
    async def test_calls_incus_remote_add_with_token(self):
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            # First call is the idempotency list-check; return empty so we fall
            # through to the real remote add.
            mock_run.side_effect = [(0, "{}"), (0, "")]
            result = await remote_add(
                "fedora-worker", "https://192.168.1.50:8443", token="abc123token"
            )
        # Second call should be the actual `incus remote add` invocation.
        add_call = mock_run.call_args_list[1]
        assert add_call.args[0] == [
            "incus", "remote", "add", "fedora-worker", "https://192.168.1.50:8443",
            "--token", "abc123token", "--accept-certificate",
        ]
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_accept_certificate_omitted_when_false(self):
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = [(0, "{}"), (0, "")]
            await remote_add("fw", "https://10.0.0.5:8443", token="tok", accept_certificate=False)
        cmd = mock_run.call_args_list[1].args[0]
        assert "--accept-certificate" not in cmd
        assert "--token" in cmd

    @pytest.mark.asyncio
    async def test_returns_failure_on_nonzero_exit(self):
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = [(0, "{}"), (1, "connection refused")]
            result = await remote_add("bad", "https://10.0.0.99:8443", token="tok")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_idempotent_when_same_url_already_registered(self):
        existing = '{"fedora-worker": {"Addr": "https://10.0.0.5:8443"}}'
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, existing)
            result = await remote_add("fedora-worker", "https://10.0.0.5:8443", token="tok")
        # Only the list call should fire; no actual `incus remote add`.
        assert mock_run.call_count == 1
        assert result["success"] is True
        assert "already enrolled" in result["output"]


class TestRemoteGenerateToken:
    @pytest.mark.asyncio
    async def test_calls_incus_config_trust_add(self):
        output = "To enroll use this token:\nabc123base64token=="
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, output)
            result = await remote_generate_token("my-client")
        mock_run.assert_called_once_with(
            ["incus", "config", "trust", "add", "my-client"]
        )
        assert result["success"] is True
        assert result["token"] == "abc123base64token=="

    @pytest.mark.asyncio
    async def test_restricted_and_projects_flags(self):
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "token123")
            await remote_generate_token("c", projects=["p1", "p2"], restricted=True)
        cmd = mock_run.call_args[0][0]
        assert "--restricted" in cmd
        assert "--projects" in cmd
        assert "p1,p2" in cmd

    @pytest.mark.asyncio
    async def test_returns_failure_on_nonzero_exit(self):
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (1, "permission denied")
            result = await remote_generate_token("c")
        assert result["success"] is False
        assert result["token"] == ""


class TestRemoteList:
    @pytest.mark.asyncio
    async def test_parses_csv_output(self):
        csv = (
            "local,unix://,incus,,False,True,False\n"
            "fedora-worker,https://192.168.1.50:8443,incus,,False,False,False\n"
        )
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, csv)
            remotes = await remote_list()
        assert len(remotes) == 2
        assert remotes[1]["name"] == "fedora-worker"
        assert remotes[1]["addr"] == "https://192.168.1.50:8443"
        assert remotes[1]["protocol"] == "incus"

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_error(self):
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (1, "daemon not running")
            remotes = await remote_list()
        assert remotes == []


class TestRemoteRemove:
    @pytest.mark.asyncio
    async def test_calls_incus_remote_remove(self):
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (0, "")
            result = await remote_remove("fedora-worker")
        mock_run.assert_called_once_with(["incus", "remote", "remove", "fedora-worker"])
        assert result["success"] is True


# ---------------------------------------------------------------------------
# migrate_container
# ---------------------------------------------------------------------------

# CSV output for remote_list showing local + fedora-worker registered.
_REMOTE_CSV = (
    "local,unix://,incus,,False,True,False\n"
    "fedora-worker,https://192.168.1.50:8443,incus,,False,False,False\n"
)

# incus info output for a stopped container.
_INFO_STOPPED = "Name: taos-svc-gitea\nStatus: Stopped\nType: container\n"

# incus info output for a running container (mixed-case, as old incus versions emitted).
_INFO_RUNNING = "Name: taos-svc-gitea\nStatus: Running\nType: container\n"

# Real incus 6.x output uses upper-case STATUS values.
_INFO_RUNNING_UPPER = "Name: taos-svc-gitea-lxc\nStatus: RUNNING\nType: container\n"


def _make_run_mock(responses: list[tuple[int, str]]):
    """Return an AsyncMock whose side_effect cycles through the given responses."""
    mock = AsyncMock()
    mock.side_effect = responses
    return mock


class TestMigrateContainerMove:
    """keep_source=False → incus move."""

    @pytest.mark.asyncio
    async def test_move_stopped_container(self):
        responses = [
            (0, _INFO_STOPPED),   # incus info
            (0, _REMOTE_CSV),     # incus remote list
            (0, ""),              # incus move
        ]
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = responses
            result = await migrate_container(
                "taos-svc-gitea", "fedora-worker", keep_source=False
            )
        assert result["success"] is True
        assert result["source"] == "local:taos-svc-gitea"
        assert result["target"] == "fedora-worker:taos-svc-gitea"
        # move command must use incus move with --mode=push
        move_call = mock_run.call_args_list[2]
        cmd = move_call[0][0]
        assert cmd[1] == "move"
        assert "--mode=push" in cmd

    @pytest.mark.asyncio
    async def test_move_running_container_stops_and_starts_on_target(self):
        responses = [
            (0, _INFO_RUNNING),   # incus info
            (0, _REMOTE_CSV),     # incus remote list
            (0, ""),              # snapshot_create (pre-stop)
            (0, ""),              # incus stop
            (0, ""),              # incus move
            (0, ""),              # incus start on target
        ]
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = responses
            result = await migrate_container(
                "taos-svc-gitea", "fedora-worker", stateless=True, keep_source=False
            )
        assert result["success"] is True
        calls = [c[0][0] for c in mock_run.call_args_list]
        # Verify stop happened before move
        stop_idx = next(i for i, c in enumerate(calls) if "stop" in c and "taos-svc-gitea" in c)
        move_idx = next(i for i, c in enumerate(calls) if "move" in c)
        start_idx = next(i for i, c in enumerate(calls) if "start" in c and "fedora-worker:" in " ".join(c))
        assert stop_idx < move_idx < start_idx

    @pytest.mark.asyncio
    async def test_rollback_restarts_source_on_move_failure(self):
        """If incus move fails after stopping the container, source is restarted."""
        responses = [
            (0, _INFO_RUNNING),   # incus info
            (0, _REMOTE_CSV),     # incus remote list
            (0, ""),              # snapshot_create
            (0, ""),              # incus stop
            (1, "transfer error"),  # incus move FAILS
            (0, ""),              # rollback: incus start (source)
        ]
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = responses
            result = await migrate_container(
                "taos-svc-gitea", "fedora-worker", stateless=True, keep_source=False
            )
        assert result["success"] is False
        assert "move failed" in result["error"]
        # Rollback start must be the last call
        last_cmd = mock_run.call_args_list[-1][0][0]
        assert "start" in last_cmd
        assert "taos-svc-gitea" in last_cmd

    @pytest.mark.asyncio
    async def test_upper_case_running_status_detected(self):
        """Regression: real incus 6.x outputs 'Status: RUNNING' (all caps).

        The status-detection loop must treat RUNNING and Running identically.
        A container reported as RUNNING must be stopped before move and started
        on the target afterwards.
        """
        responses = [
            (0, _INFO_RUNNING_UPPER),  # incus info — Status: RUNNING
            (0, _REMOTE_CSV),          # incus remote list
            (0, ""),                   # snapshot_create (pre-stop)
            (0, ""),                   # incus stop
            (0, ""),                   # incus move
            (0, ""),                   # incus start on target
        ]
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = responses
            result = await migrate_container(
                "taos-svc-gitea-lxc", "fedora-worker", stateless=True, keep_source=False
            )
        assert result["success"] is True
        calls = [c[0][0] for c in mock_run.call_args_list]
        # stop, move, and start on target must all be present
        stop_idx = next(i for i, c in enumerate(calls) if "stop" in c)
        move_idx = next(i for i, c in enumerate(calls) if "move" in c)
        start_idx = next(
            i for i, c in enumerate(calls) if "start" in c and "fedora-worker:" in " ".join(c)
        )
        assert stop_idx < move_idx < start_idx


class TestMigrateContainerCopy:
    """keep_source=True → incus copy."""

    @pytest.mark.asyncio
    async def test_copy_uses_incus_copy(self):
        responses = [
            (0, _INFO_STOPPED),
            (0, _REMOTE_CSV),
            (0, ""),  # incus copy
        ]
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = responses
            result = await migrate_container(
                "taos-svc-gitea", "fedora-worker", keep_source=True
            )
        assert result["success"] is True
        copy_call = mock_run.call_args_list[2]
        cmd = copy_call[0][0]
        assert cmd[1] == "copy"
        assert "--mode=push" in cmd

    @pytest.mark.asyncio
    async def test_copy_does_not_rollback_start(self):
        """On copy failure nothing is restarted (source was never stopped)."""
        responses = [
            (0, _INFO_STOPPED),
            (0, _REMOTE_CSV),
            (1, "copy failed"),   # incus copy FAILS
        ]
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = responses
            result = await migrate_container(
                "taos-svc-gitea", "fedora-worker", keep_source=True
            )
        assert result["success"] is False
        # Only 3 calls: info, remote list, copy — no rollback start
        assert mock_run.call_count == 3


class TestMigrateContainerErrors:
    @pytest.mark.asyncio
    async def test_fails_when_container_not_found(self):
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (1, "not found")
            result = await migrate_container("ghost", "fedora-worker")
        assert result["success"] is False
        assert "ghost" in result["error"]
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_fails_when_remote_not_registered(self):
        csv_only_local = "local,unix://,incus,,False,True,False\n"
        responses = [
            (0, _INFO_STOPPED),   # incus info
            (0, csv_only_local),  # incus remote list — no fedora-worker
        ]
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = responses
            result = await migrate_container("taos-svc-gitea", "fedora-worker")
        assert result["success"] is False
        assert "fedora-worker" in result["error"]
        # Error should include the incus remote add command hint
        assert "incus remote add" in result["error"]

    @pytest.mark.asyncio
    async def test_custom_new_name_used_in_target_ref(self):
        responses = [
            (0, _INFO_STOPPED),
            (0, _REMOTE_CSV),
            (0, ""),  # incus move
        ]
        with patch("tinyagentos.containers._run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = responses
            result = await migrate_container(
                "taos-svc-gitea", "fedora-worker", new_name="taos-svc-gitea-copy"
            )
        assert result["success"] is True
        assert result["target"] == "fedora-worker:taos-svc-gitea-copy"


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

class TestMigrateRoutes:
    @pytest.mark.asyncio
    async def test_post_remotes_registers(self, client):
        with patch("tinyagentos.routes.cluster_migrate.remote_add", new_callable=AsyncMock) as m:
            m.return_value = {"success": True, "output": ""}
            resp = await client.post("/api/cluster/remotes", json={
                "name": "fedora-worker",
                "url": "https://192.168.1.50:8443",
                "token": "abc123token",
            })
        assert resp.status_code == 200
        assert resp.json()["status"] == "registered"

    @pytest.mark.asyncio
    async def test_post_remotes_missing_fields(self, client):
        resp = await client.post("/api/cluster/remotes", json={"name": "", "url": "", "token": "t"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_post_remotes_missing_token(self, client):
        resp = await client.post("/api/cluster/remotes", json={
            "name": "fw", "url": "https://10.0.0.5:8443", "token": "",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_post_remotes_token_generates_token(self, client):
        with patch("tinyagentos.routes.cluster_migrate.remote_generate_token", new_callable=AsyncMock) as m:
            m.return_value = {"success": True, "token": "xyz789==", "output": ""}
            resp = await client.post("/api/cluster/remotes/token", json={"client_name": "pi-node"})
        assert resp.status_code == 200
        assert resp.json()["token"] == "xyz789=="

    @pytest.mark.asyncio
    async def test_get_remotes_lists(self, client):
        with patch("tinyagentos.routes.cluster_migrate.remote_list", new_callable=AsyncMock) as m:
            m.return_value = [{"name": "local", "addr": "unix://", "protocol": "incus"}]
            resp = await client.get("/api/cluster/remotes")
        assert resp.status_code == 200
        assert resp.json()[0]["name"] == "local"

    @pytest.mark.asyncio
    async def test_delete_remote(self, client):
        with patch("tinyagentos.routes.cluster_migrate.remote_remove", new_callable=AsyncMock) as m:
            m.return_value = {"success": True, "output": ""}
            resp = await client.delete("/api/cluster/remotes/fedora-worker")
        assert resp.status_code == 200
        assert resp.json()["name"] == "fedora-worker"

    @pytest.mark.asyncio
    async def test_post_migrate_triggers_migration(self, client):
        with patch("tinyagentos.routes.cluster_migrate.migrate_container", new_callable=AsyncMock) as m:
            m.return_value = {
                "success": True,
                "source": "local:taos-svc-gitea",
                "target": "fedora-worker:taos-svc-gitea",
                "duration_s": 12.3,
            }
            resp = await client.post("/api/cluster/migrate", json={
                "container": "taos-svc-gitea",
                "target_remote": "fedora-worker",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["target"] == "fedora-worker:taos-svc-gitea"

    @pytest.mark.asyncio
    async def test_post_migrate_missing_fields(self, client):
        resp = await client.post("/api/cluster/migrate", json={
            "container": "", "target_remote": "",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_post_migrate_propagates_error(self, client):
        with patch("tinyagentos.routes.cluster_migrate.migrate_container", new_callable=AsyncMock) as m:
            m.return_value = {"success": False, "error": "Remote 'x' is not registered"}
            resp = await client.post("/api/cluster/migrate", json={
                "container": "taos-svc-gitea",
                "target_remote": "x",
            })
        assert resp.status_code == 500
        assert "not registered" in resp.json()["error"]
