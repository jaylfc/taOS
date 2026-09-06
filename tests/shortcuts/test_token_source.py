import json
import subprocess
from unittest.mock import MagicMock, patch
import pytest

from tinyagentos.shortcuts.token_source import read_token_source, _cache


AGENT = "test-agent"


def _clear_cache():
    _cache.clear()


def test_static_source_returns_value():
    _clear_cache()
    source = {"kind": "static", "value": "my-static-token"}
    result = read_token_source(AGENT, source)
    assert result == "my-static-token"


def test_container_env_calls_incus(monkeypatch):
    _clear_cache()
    mock_run = MagicMock(
        return_value=MagicMock(stdout="env-token-value\n", returncode=0)
    )
    monkeypatch.setattr(subprocess, "run", mock_run)
    source = {"kind": "container_env", "var": "MY_TOKEN"}
    result = read_token_source(AGENT, source)
    assert result == "env-token-value"
    args = mock_run.call_args[0][0]
    assert "incus" in args[0]
    assert f"taos-agent-{AGENT}" in args


def test_container_file_extracts_json_pointer(monkeypatch):
    _clear_cache()
    data = {"gateway": {"auth": {"token": "file-token-123"}}}
    mock_run = MagicMock(
        return_value=MagicMock(stdout=json.dumps(data), returncode=0)
    )
    monkeypatch.setattr(subprocess, "run", mock_run)
    source = {
        "kind": "container_file",
        "path": "/root/.openclaw/openclaw.json",
        "json_pointer": "/gateway/auth/token",
    }
    result = read_token_source(AGENT, source)
    assert result == "file-token-123"


def test_container_env_returns_none_on_incus_failure(monkeypatch):
    _clear_cache()
    mock_run = MagicMock(
        return_value=MagicMock(stdout="", returncode=1)
    )
    monkeypatch.setattr(subprocess, "run", mock_run)
    source = {"kind": "container_env", "var": "MISSING_VAR"}
    result = read_token_source(AGENT, source)
    assert result is None


def test_result_is_cached_on_second_call(monkeypatch):
    _clear_cache()
    call_count = 0

    def fake_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return MagicMock(stdout="cached-token\n", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    source = {"kind": "container_env", "var": "CACHED_VAR"}
    read_token_source(AGENT, source)
    read_token_source(AGENT, source)
    assert call_count == 1


def test_unknown_kind_raises():
    _clear_cache()
    with pytest.raises(ValueError, match="unknown token_source kind"):
        read_token_source(AGENT, {"kind": "magic", "value": "x"})


# ---------------------------------------------------------------------------
# M1 — shell injection safety
# ---------------------------------------------------------------------------

def test_container_env_uses_printenv_not_sh_c(monkeypatch):
    """container_env must invoke printenv, not sh -c (shell injection fix)."""
    _clear_cache()
    captured_cmd: list = []

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return MagicMock(stdout="token123\n", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    source = {"kind": "container_env", "var": "MY_TOKEN"}
    read_token_source(AGENT, source)

    assert "sh" not in captured_cmd
    assert "-c" not in captured_cmd
    assert "printenv" in captured_cmd


def test_container_env_rejects_invalid_var_name(monkeypatch):
    """var names with shell-special chars must be rejected (return None)."""
    _clear_cache()
    mock_run = MagicMock()
    monkeypatch.setattr(subprocess, "run", mock_run)

    # These should all be rejected without calling subprocess.run
    bad_vars = [
        "FOO; rm -rf /tmp",
        "FOO|bar",
        "foo",          # lowercase not allowed
        "123STARTS",    # starts with digit
        "",
    ]
    for bad_var in bad_vars:
        source = {"kind": "container_env", "var": bad_var}
        result = read_token_source(AGENT, source)
        assert result is None, f"Expected None for var={bad_var!r}, got {result!r}"
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# M5 — subprocess exception handling
# ---------------------------------------------------------------------------

def test_container_env_handles_file_not_found(monkeypatch):
    """FileNotFoundError (incus missing) must return None, not raise."""
    _clear_cache()

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("incus not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    source = {"kind": "container_env", "var": "MY_TOKEN"}
    result = read_token_source(AGENT, source)
    assert result is None


def test_container_file_handles_timeout(monkeypatch):
    """TimeoutExpired must return None, not raise."""
    _clear_cache()

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=10)

    monkeypatch.setattr(subprocess, "run", fake_run)
    source = {
        "kind": "container_file",
        "path": "/etc/config.json",
        "json_pointer": "/key",
    }
    result = read_token_source(AGENT, source)
    assert result is None
