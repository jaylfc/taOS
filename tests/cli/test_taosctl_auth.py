import json
from click.testing import CliRunner

from taosctl.cli import cli


def test_auth_login_writes_credentials_file(tmp_path, monkeypatch):
    cred_path = tmp_path / "credentials"
    monkeypatch.setattr("taosctl.config.CREDENTIALS_PATH", cred_path)
    runner = CliRunner()
    result = runner.invoke(
        cli, ["auth", "login", "--token", "taos_agent_test123"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(cred_path.read_text())
    assert data["token"] == "taos_agent_test123"


def test_auth_login_chmods_credentials_file(tmp_path, monkeypatch):
    """The credentials file is created with 0600 perms (owner-only)."""
    import stat
    cred_path = tmp_path / "credentials"
    monkeypatch.setattr("taosctl.config.CREDENTIALS_PATH", cred_path)
    runner = CliRunner()
    runner.invoke(cli, ["auth", "login", "--token", "taos_agent_chmod"])
    mode = cred_path.stat().st_mode
    assert stat.S_IMODE(mode) == 0o600


def test_auth_status_no_token_exits_2(monkeypatch):
    monkeypatch.setattr("taosctl.config.resolve_token", lambda: None)
    runner = CliRunner()
    result = runner.invoke(cli, ["auth", "status"])
    assert result.exit_code == 2
    assert "no token" in result.output.lower()


def test_auth_whoami_calls_api_and_prints_json(monkeypatch):
    monkeypatch.setattr("taosctl.config.resolve_token", lambda: "taos_agent_t1")
    monkeypatch.setattr("taosctl.config.resolve_url", lambda: "http://test.example")
    monkeypatch.setattr(
        "taosctl.http_client.get",
        lambda path: {"user_id": "u-1", "agent_id": "a-1", "scope": ["*"]},
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["auth", "whoami"])
    assert result.exit_code == 0, result.output
    assert "u-1" in result.output
    assert "a-1" in result.output
