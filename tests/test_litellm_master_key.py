"""Tests for the per-install LiteLLM master key loader (Fix A / issue #637)."""
from __future__ import annotations

import pytest
from pathlib import Path

import tinyagentos.litellm_config as cfg_mod
from tinyagentos.litellm_config import get_litellm_master_key, generate_litellm_config
from tinyagentos.llm_proxy import LLMProxy


@pytest.fixture(autouse=True)
def clear_key_cache():
    """Isolate the in-process cache between tests."""
    cfg_mod._master_key_cache.clear()
    yield
    cfg_mod._master_key_cache.clear()


class TestMasterKeyGeneration:
    def test_key_has_correct_prefix(self, tmp_path):
        key = get_litellm_master_key(tmp_path)
        assert key.startswith("sk-taos-")

    def test_key_longer_than_hardcoded(self, tmp_path):
        """Generated key must be longer than the old 'sk-taos-master' constant."""
        key = get_litellm_master_key(tmp_path)
        assert len(key) > len("sk-taos-master")

    def test_key_persisted_to_file(self, tmp_path):
        key = get_litellm_master_key(tmp_path)
        key_file = tmp_path / ".litellm_master_key"
        assert key_file.exists()
        assert key_file.read_text().strip() == key

    def test_key_file_mode_600(self, tmp_path):
        import stat
        get_litellm_master_key(tmp_path)
        key_file = tmp_path / ".litellm_master_key"
        mode = stat.S_IMODE(key_file.stat().st_mode)
        assert mode == 0o600

    def test_same_key_on_second_call_same_process(self, tmp_path):
        k1 = get_litellm_master_key(tmp_path)
        k2 = get_litellm_master_key(tmp_path)
        assert k1 == k2

    def test_same_key_after_cache_cleared(self, tmp_path):
        k1 = get_litellm_master_key(tmp_path)
        cfg_mod._master_key_cache.clear()
        k2 = get_litellm_master_key(tmp_path)
        assert k1 == k2

    def test_different_data_dirs_get_different_keys(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        ka = get_litellm_master_key(dir_a)
        kb = get_litellm_master_key(dir_b)
        # Independently generated keys should not collide (with overwhelming probability).
        assert ka != kb

    def test_no_data_dir_returns_in_memory_key(self):
        key = get_litellm_master_key(None)
        assert key.startswith("sk-taos-")

    def test_exclusive_create_race_loser_reads_winner_key(self, tmp_path):
        """Simulate the O_EXCL race: pre-write a key file, then call the loader.

        The loader must detect the FileExistsError path and return the
        pre-existing key rather than the freshly-generated token it minted,
        ensuring the cache ends up with whatever key actually won on disk.
        """
        import os
        key_path = tmp_path / ".litellm_master_key"
        winner_key = "sk-taos-winner-key-abc123"
        # Write the winner's key with O_CREAT|O_EXCL to simulate what the
        # winning process does.
        fd = os.open(str(key_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, winner_key.encode())
        os.close(fd)

        # Now call the loader — it should see the file and return the winner's key.
        loaded = get_litellm_master_key(tmp_path)
        assert loaded == winner_key, (
            "Loader must return the on-disk key (the race winner), "
            "not a freshly generated token."
        )
        assert cfg_mod._master_key_cache[str(tmp_path)] == winner_key

    def test_loser_path_via_file_exists_error(self, tmp_path, monkeypatch):
        """Directly exercise the FileExistsError branch by patching os.open."""
        import os as _os
        key_path = tmp_path / ".litellm_master_key"
        winner_key = "sk-taos-on-disk-winner"
        key_path.write_text(winner_key)

        original_open = _os.open

        def _raise_exists(path, flags, mode=0o666):
            if "litellm_master_key" in str(path) and (flags & _os.O_EXCL):
                raise FileExistsError("simulated race")
            return original_open(path, flags, mode)

        monkeypatch.setattr(_os, "open", _raise_exists)

        loaded = get_litellm_master_key(tmp_path)
        assert loaded == winner_key
        assert cfg_mod._master_key_cache[str(tmp_path)] == winner_key

    def test_generate_litellm_config_uses_supplied_key(self, tmp_path):
        key = get_litellm_master_key(tmp_path)
        config = generate_litellm_config([], master_key=key)
        assert config["general_settings"]["master_key"] == key

    def test_generate_litellm_config_no_master_key_falls_back_to_loader(self):
        """When master_key is not supplied, generate_litellm_config calls the loader."""
        config = generate_litellm_config([])
        mk = config["general_settings"]["master_key"]
        assert mk.startswith("sk-taos-")


class TestProxyConsistency:
    """The proxy must use the SAME key for LITELLM_MASTER_KEY env and /key/generate auth."""

    @pytest.mark.asyncio
    async def test_proxy_env_and_bearer_use_same_key(self, tmp_path, monkeypatch):
        """LITELLM_MASTER_KEY env and the Authorization header must match."""
        import shutil
        import tinyagentos.llm_proxy as mod

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *exc):
                return False
            async def get(self, url):
                raise RuntimeError("no proxy")

        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeClient)
        monkeypatch.setattr(mod, "_pids_listening_on", lambda port: [])
        monkeypatch.setattr(shutil, "which", lambda _: "/fake/litellm")

        captured: dict = {}

        class _FakePopen:
            def __init__(self, *args, **kwargs):
                captured["env"] = kwargs.get("env") or {}
                raise FileNotFoundError("stubbed")

        monkeypatch.setattr(mod.subprocess, "Popen", _FakePopen)

        proxy = mod.LLMProxy(port=14099, data_dir=tmp_path)
        await proxy.start(backends=[])

        env_key = captured["env"].get("LITELLM_MASTER_KEY", "")
        file_key = get_litellm_master_key(tmp_path)
        assert env_key == file_key, (
            "LITELLM_MASTER_KEY env var must match the on-disk key so the "
            "controller can auth against its own proxy"
        )
        assert env_key.startswith("sk-taos-")

    @pytest.mark.asyncio
    async def test_create_agent_key_header_matches_env_key(self, tmp_path, monkeypatch):
        """Bearer token in /key/generate request must equal the generated master key."""
        import tinyagentos.llm_proxy as mod

        expected_key = get_litellm_master_key(tmp_path)
        captured_headers: list[dict] = []

        class _FakeResp:
            status_code = 200
            def json(self):
                return {"key": "sk-virtual-abc"}

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *exc):
                return False
            async def post(self, url, json=None, headers=None):
                captured_headers.append(headers or {})
                return _FakeResp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeClient)

        proxy = mod.LLMProxy(port=14098, database_url="postgres://x:y@h/db", data_dir=tmp_path)

        class _FakeProc:
            def poll(self):
                return None
        proxy._process = _FakeProc()

        await proxy.create_agent_key("test-agent")

        assert captured_headers, "No HTTP call was made"
        auth = captured_headers[0].get("Authorization", "")
        assert auth == f"Bearer {expected_key}"
