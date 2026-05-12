import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from taosctl.cli import cli


def test_agents_list_human_output():
    fake = [
        {"name": "a1", "host": "192.0.2.1", "qmd_index": "q1", "has_token": True},
        {"name": "a2", "host": "192.0.2.2", "qmd_index": "q2", "has_token": False},
    ]
    with patch("taosctl.http_client.get", return_value=fake):
        result = CliRunner().invoke(cli, ["agents", "list"])
    assert result.exit_code == 0, result.output
    assert "a1" in result.output
    assert "a2" in result.output


def test_agents_list_json_outputs_json():
    fake = [{"name": "a1", "host": "192.0.2.1", "qmd_index": "q1", "has_token": True}]
    with patch("taosctl.http_client.get", return_value=fake):
        result = CliRunner().invoke(cli, ["agents", "list", "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed == fake


def test_agents_get_calls_correct_path():
    with patch("taosctl.http_client.get", return_value={"name": "a1"}) as p:
        result = CliRunner().invoke(cli, ["agents", "get", "a1"])
    assert result.exit_code == 0, result.output
    p.assert_called_once_with("/api/agents/a1")


def test_agents_create_posts_correct_body():
    with patch("taosctl.http_client.post", return_value={"status": "created", "name": "new"}) as p:
        result = CliRunner().invoke(
            cli,
            ["agents", "create", "--name", "new", "--host", "192.0.2.10", "--qmd-index", "test"],
        )
    assert result.exit_code == 0, result.output
    p.assert_called_once()
    call_args = p.call_args
    assert call_args.args[0] == "/api/agents"
    assert call_args.kwargs["json"]["name"] == "new"
    assert call_args.kwargs["json"]["host"] == "192.0.2.10"
    assert call_args.kwargs["json"]["qmd_index"] == "test"
    # Idempotency-Key header so retries are safe
    assert "Idempotency-Key" in call_args.kwargs["headers"]


def test_agents_start_posts():
    with patch("taosctl.http_client.post", return_value={"ok": True}) as p:
        result = CliRunner().invoke(cli, ["agents", "start", "x"])
    assert result.exit_code == 0
    p.assert_called_once_with("/api/agents/x/start")


def test_agents_stop_posts():
    with patch("taosctl.http_client.post", return_value={"ok": True}) as p:
        CliRunner().invoke(cli, ["agents", "stop", "x"])
    p.assert_called_once_with("/api/agents/x/stop")


def test_agents_restart_posts():
    with patch("taosctl.http_client.post", return_value={"ok": True}) as p:
        CliRunner().invoke(cli, ["agents", "restart", "x"])
    p.assert_called_once_with("/api/agents/x/restart")


def test_agents_pause_posts():
    with patch("taosctl.http_client.post", return_value={"ok": True}) as p:
        CliRunner().invoke(cli, ["agents", "pause", "x"])
    p.assert_called_once_with("/api/agents/x/pause")


def test_agents_logs_prints_lines():
    with patch("taosctl.http_client.get", return_value={"lines": ["line1", "line2"]}):
        result = CliRunner().invoke(cli, ["agents", "logs", "x"])
    assert result.exit_code == 0
    assert "line1" in result.output
    assert "line2" in result.output


def test_agents_logs_passes_lines_param():
    with patch("taosctl.http_client.get", return_value={"lines": []}) as p:
        CliRunner().invoke(cli, ["agents", "logs", "x", "--lines", "50"])
    p.assert_called_once_with("/api/agents/x/logs", params={"lines": 50})


def test_agents_update_puts_correct_body():
    with patch("taosctl.http_client.put", return_value={"name": "x", "host": "192.0.2.99"}) as p:
        result = CliRunner().invoke(cli, ["agents", "update", "x", "--host", "192.0.2.99"])
    assert result.exit_code == 0, result.output
    p.assert_called_once()
    call_args = p.call_args
    assert call_args.args[0] == "/api/agents/x"
    assert call_args.kwargs["json"]["host"] == "192.0.2.99"


def test_agents_delete_with_yes_flag_calls_api():
    with patch("taosctl.http_client.delete", return_value=204) as p:
        result = CliRunner().invoke(cli, ["agents", "delete", "x", "--yes"])
    assert result.exit_code == 0, result.output
    p.assert_called_once_with("/api/agents/x")


def test_agents_token_issue_posts_and_warns():
    with patch(
        "taosctl.http_client.post",
        return_value={"token": "taos_agent_xxx", "issued_at": "2026-05-12T13:00:00Z"},
    ) as p:
        result = CliRunner().invoke(cli, ["agents", "token", "issue", "x"])
    assert result.exit_code == 0, result.output
    p.assert_called_once_with("/api/agents/x/token/issue")
    assert "taos_agent_xxx" in result.output
    # Warning about plaintext-once is on stderr; check combined output captured by CliRunner
    assert "once" in result.output.lower() or "only" in result.output.lower()


def test_agents_token_revoke_calls_delete():
    with patch("taosctl.http_client.delete", return_value=204) as p:
        result = CliRunner().invoke(cli, ["agents", "token", "revoke", "x"])
    assert result.exit_code == 0, result.output
    p.assert_called_once_with("/api/agents/x/token")
    assert "revoked" in result.output.lower()
