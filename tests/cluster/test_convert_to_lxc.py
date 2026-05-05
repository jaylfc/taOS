"""T9: Tests for convert_to_lxc — flat-mode to worker-LXC migration."""
from unittest.mock import patch
import pytest

from tinyagentos.cluster.convert_to_lxc import (
    list_flat_mode_agents,
    drain_and_delete_agents,
    redeploy_agents,
)


def test_list_flat_mode_agents_filters_taos_agent_prefix():
    fake_output = "taos-agent-foo,RUNNING\ntaos-agent-bar,STOPPED\nrandom-thing,RUNNING\n"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = fake_output
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""
        agents = list_flat_mode_agents()
    assert agents == [
        {"name": "taos-agent-foo", "state": "RUNNING"},
        {"name": "taos-agent-bar", "state": "STOPPED"},
    ]


def test_list_flat_mode_agents_returns_empty_on_incus_error():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "incus not found"
        mock_run.return_value.stdout = ""
        assert list_flat_mode_agents() == []


@pytest.mark.asyncio
async def test_drain_and_delete_agents_calls_stop_then_delete():
    calls = []

    async def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    with patch("tinyagentos.cluster.convert_to_lxc._run_async", fake_run):
        await drain_and_delete_agents([
            {"name": "taos-agent-foo", "state": "RUNNING"},
            {"name": "taos-agent-bar", "state": "STOPPED"},
        ])
    assert ["incus", "stop", "taos-agent-foo"] in calls
    assert ["incus", "delete", "--force", "taos-agent-foo"] in calls
    assert ["incus", "delete", "--force", "taos-agent-bar"] in calls
    assert ["incus", "stop", "taos-agent-bar"] not in calls


@pytest.mark.asyncio
async def test_redeploy_agents_calls_deployer_for_each(monkeypatch):
    deployed = []

    async def fake_deploy(req):
        deployed.append(req.name)
        return {"success": True}

    class FakeDeployRequest:
        def __init__(self, **kw):
            self.name = kw["name"]

    monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)
    monkeypatch.setattr("tinyagentos.deployer.DeployRequest", FakeDeployRequest)

    await redeploy_agents([
        {"name": "agent-a", "framework": "openclaw", "model": "gpt-4o"},
        {"name": "agent-b", "framework": "openclaw", "model": "claude"},
    ])
    assert deployed == ["agent-a", "agent-b"]
