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
