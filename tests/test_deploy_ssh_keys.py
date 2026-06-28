"""Tests for SSH key materialization during agent deploy.

SSH key secrets (category == "ssh-keys") must be written to
~/.ssh/<name> with 0600 perms and ~/.ssh with 0700. Non-ssh-keys
secrets must not trigger file materialization.
"""
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, patch, call

from tinyagentos.deployer import deploy_agent, DeployRequest


def _req(**overrides) -> DeployRequest:
    defaults = dict(
        name="test",
        framework="smolagents",
        model=None,
        data_dir=Path("/tmp/taos-test-data"),
    )
    defaults.update(overrides)
    return DeployRequest(**defaults)


class FakeSecretsStore:
    def __init__(self, secrets):
        self._secrets = secrets

    async def get_agent_secrets(self, agent_name):
        return list(self._secrets)


async def _run_deploy(req, tmp_path):
    """Run deploy_agent with all container calls mocked.

    Returns (push_file_calls, exec_calls) so tests can assert what
    the deployer tried to push and what it exec'd.
    """
    push_calls: list = []
    exec_calls: list = []

    async def mock_push(container, src, dst):
        # Record what was pushed (read the temp file content).
        try:
            with open(src) as fh:
                content = fh.read()
        except (FileNotFoundError, OSError):
            content = ""
        push_calls.append({"container": container, "src": src, "dst": dst, "content": content})
        return (0, "")

    async def mock_exec(container, cmd, **kwargs):
        exec_calls.append({"container": container, "cmd": list(cmd)})
        if "hostname" in cmd and "-I" in cmd:
            return (0, "10.0.0.5")
        return (0, "ok")

    with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
         patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec), \
         patch("tinyagentos.deployer.push_file", side_effect=mock_push), \
         patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock,
               return_value={"success": True, "output": ""}):
        mock_create.return_value = {"success": True, "name": "taos-agent-test"}
        result = await deploy_agent(req)
        return result, push_calls, exec_calls


class TestSshKeyMaterialization:
    @pytest.mark.asyncio
    async def test_ssh_key_written_to_dot_ssh(self, tmp_path):
        """An ssh-keys secret is pushed to /root/.ssh/<name> inside the container."""
        key_value = "-----BEGIN OPENSSH PRIVATE KEY-----\nfakekey\n-----END OPENSSH PRIVATE KEY-----\n"
        store = FakeSecretsStore([
            {"name": "my-deploy-key", "category": "ssh-keys", "value": key_value},
        ])
        req = _req(data_dir=tmp_path, secrets_store=store)

        result, push_calls, exec_calls = await _run_deploy(req, tmp_path)

        assert result["success"] is True

        # A push to /root/.ssh/my-deploy-key must have happened.
        ssh_pushes = [p for p in push_calls if p["dst"] == "/root/.ssh/my-deploy-key"]
        assert ssh_pushes, f"expected push to /root/.ssh/my-deploy-key; got: {[p['dst'] for p in push_calls]}"

        # The content pushed must be the key value verbatim (with trailing newline).
        assert ssh_pushes[0]["content"] == key_value

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_name", [
        "../authorized_keys",
        "../../etc/cron.d/evil",
        "foo/bar",
        "..",
        ".",
        "a b",
    ])
    async def test_ssh_key_unsafe_name_is_skipped(self, tmp_path, bad_name):
        """A traversal/unsafe secret name must never be written outside ~/.ssh."""
        store = FakeSecretsStore([
            {"name": bad_name, "category": "ssh-keys", "value": "key\n"},
        ])
        req = _req(data_dir=tmp_path, secrets_store=store)

        result, push_calls, exec_calls = await _run_deploy(req, tmp_path)

        assert result["success"] is True
        # Nothing should be pushed under /root/.ssh for an unsafe name.
        ssh_pushes = [p for p in push_calls if "/root/.ssh/" in p["dst"]]
        assert not ssh_pushes, f"unsafe name {bad_name!r} produced a push: {[p['dst'] for p in ssh_pushes]}"

    @pytest.mark.asyncio
    async def test_ssh_key_trailing_newline_added_when_missing(self, tmp_path):
        """If the stored value has no trailing newline, the deployer adds one."""
        key_value = "-----BEGIN OPENSSH PRIVATE KEY-----\nfakekey\n-----END OPENSSH PRIVATE KEY-----"
        store = FakeSecretsStore([
            {"name": "my-key", "category": "ssh-keys", "value": key_value},
        ])
        req = _req(data_dir=tmp_path, secrets_store=store)

        result, push_calls, _exec = await _run_deploy(req, tmp_path)

        assert result["success"] is True
        ssh_pushes = [p for p in push_calls if p["dst"] == "/root/.ssh/my-key"]
        assert ssh_pushes
        assert ssh_pushes[0]["content"].endswith("\n")

    @pytest.mark.asyncio
    async def test_ssh_dir_created_with_700(self, tmp_path):
        """The deployer runs a command that creates ~/.ssh with 0700 perms."""
        store = FakeSecretsStore([
            {"name": "deploy-key", "category": "ssh-keys", "value": "key\n"},
        ])
        req = _req(data_dir=tmp_path, secrets_store=store)

        _result, _push, exec_calls = await _run_deploy(req, tmp_path)

        # The deployer issues a bash compound command: mkdir + chmod 700.
        # Match on the full joined command string to support both forms.
        ssh_dir_cmds = [
            " ".join(e["cmd"]) for e in exec_calls
            if "/root/.ssh" in " ".join(e["cmd"]) and "700" in " ".join(e["cmd"])
        ]
        assert ssh_dir_cmds, (
            f"expected a command creating ~/.ssh with 700 perms; "
            f"exec calls: {[e['cmd'] for e in exec_calls]}"
        )

    @pytest.mark.asyncio
    async def test_ssh_key_chmod_600_applied(self, tmp_path):
        """After pushing, deployer runs `chmod 600 /root/.ssh/<name>`."""
        store = FakeSecretsStore([
            {"name": "my-key", "category": "ssh-keys", "value": "key\n"},
        ])
        req = _req(data_dir=tmp_path, secrets_store=store)

        _result, _push, exec_calls = await _run_deploy(req, tmp_path)

        chmod_cmds = [
            e["cmd"] for e in exec_calls
            if e["cmd"][:2] == ["chmod", "600"]
        ]
        assert chmod_cmds, f"expected chmod 600; exec calls: {[e['cmd'] for e in exec_calls]}"
        assert any("/root/.ssh/my-key" in " ".join(c) for c in chmod_cmds)

    @pytest.mark.asyncio
    async def test_non_ssh_secret_not_materialized_as_file(self, tmp_path):
        """A secret with category != ssh-keys must not be pushed to ~/.ssh."""
        store = FakeSecretsStore([
            {"name": "OPENROUTER_API_KEY", "category": "api-keys", "value": "sk-xxx"},
        ])
        req = _req(data_dir=tmp_path, secrets_store=store)

        result, push_calls, _exec = await _run_deploy(req, tmp_path)

        assert result["success"] is True
        ssh_pushes = [p for p in push_calls if ".ssh" in p["dst"]]
        assert not ssh_pushes, f"non-ssh secret wrongly pushed to .ssh: {ssh_pushes}"

    @pytest.mark.asyncio
    async def test_mixed_secrets_only_ssh_goes_to_file(self, tmp_path):
        """With both ssh-keys and api-keys granted, only the ssh-keys one lands in ~/.ssh."""
        store = FakeSecretsStore([
            {"name": "OPENROUTER_API_KEY", "category": "api-keys", "value": "sk-xxx"},
            {"name": "github-deploy-key", "category": "ssh-keys", "value": "key\n"},
        ])
        req = _req(data_dir=tmp_path, secrets_store=store)

        result, push_calls, _exec = await _run_deploy(req, tmp_path)

        assert result["success"] is True
        ssh_pushes = [p for p in push_calls if ".ssh" in p["dst"]]
        assert len(ssh_pushes) == 1
        assert ssh_pushes[0]["dst"] == "/root/.ssh/github-deploy-key"

    @pytest.mark.asyncio
    async def test_ssh_keys_step_recorded(self, tmp_path):
        """When SSH keys are materialized, the deploy result includes the step."""
        store = FakeSecretsStore([
            {"name": "key1", "category": "ssh-keys", "value": "key\n"},
        ])
        req = _req(data_dir=tmp_path, secrets_store=store)

        result, _push, _exec = await _run_deploy(req, tmp_path)

        assert result["success"] is True
        assert "ssh_keys_materialized" in result["steps"]

    @pytest.mark.asyncio
    async def test_no_ssh_keys_no_step(self, tmp_path):
        """When no ssh-keys secrets exist, the ssh_keys_materialized step is absent."""
        store = FakeSecretsStore([
            {"name": "SOME_TOKEN", "category": "tokens", "value": "tok-xxx"},
        ])
        req = _req(data_dir=tmp_path, secrets_store=store)

        result, _push, _exec = await _run_deploy(req, tmp_path)

        assert result["success"] is True
        assert "ssh_keys_materialized" not in result["steps"]

    @pytest.mark.asyncio
    async def test_no_secrets_store_no_ssh_materialization(self, tmp_path):
        """When secrets_store is None, no ~/.ssh activity happens."""
        req = _req(data_dir=tmp_path)  # secrets_store defaults to None

        result, push_calls, _exec = await _run_deploy(req, tmp_path)

        assert result["success"] is True
        ssh_pushes = [p for p in push_calls if ".ssh" in p["dst"]]
        assert not ssh_pushes

    @pytest.mark.asyncio
    async def test_multiple_ssh_keys_all_materialized(self, tmp_path):
        """Multiple ssh-keys secrets each get their own file."""
        store = FakeSecretsStore([
            {"name": "key-github", "category": "ssh-keys", "value": "keyA\n"},
            {"name": "key-gitlab", "category": "ssh-keys", "value": "keyB\n"},
        ])
        req = _req(data_dir=tmp_path, secrets_store=store)

        result, push_calls, _exec = await _run_deploy(req, tmp_path)

        assert result["success"] is True
        ssh_pushes = {p["dst"]: p["content"] for p in push_calls if ".ssh" in p["dst"]}
        assert "/root/.ssh/key-github" in ssh_pushes
        assert "/root/.ssh/key-gitlab" in ssh_pushes
        assert ssh_pushes["/root/.ssh/key-github"] == "keyA\n"
        assert ssh_pushes["/root/.ssh/key-gitlab"] == "keyB\n"
