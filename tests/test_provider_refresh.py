"""Cloud-provider periodic refresh: reload LiteLLM only when the catalog changes."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import tinyagentos.routes.providers as P
from tinyagentos.provider_refresh import CloudProviderRefresher


class _FakeProxy:
    def __init__(self):
        self.reloads = 0
    def is_running(self):
        return True
    async def reload_config(self, backends, secrets=None):
        self.reloads += 1


@pytest.mark.asyncio
async def test_reloads_only_when_catalog_changes(monkeypatch, tmp_path):
    backend = {"name": "kilo", "type": "kilocode", "url": "u", "models": [{"id": "a"}]}
    config = SimpleNamespace(backends=[backend], config_path=tmp_path / "config.yaml")
    proxy = _FakeProxy()
    state = SimpleNamespace(config=config, llm_proxy=proxy)

    async def _noop_save(*a, **k):
        return None
    async def _no_secrets(*a, **k):
        return []
    monkeypatch.setattr(P, "save_config_locked", _noop_save)
    monkeypatch.setattr(P, "_resolve_backend_secrets", _no_secrets)

    # 1) Re-probe discovers a NEW model -> change -> reload.
    async def _adds_model(app_state, b, timeout=5.0):
        b["models"] = [{"id": "a"}, {"id": "b"}]
        return b
    monkeypatch.setattr(P, "_refresh_backend", _adds_model)
    assert await P.refresh_cloud_backends_if_changed(state, config, proxy) is True
    assert proxy.reloads == 1

    # 2) Re-probe finds the SAME models -> no change -> no reload.
    async def _no_change(app_state, b, timeout=5.0):
        return b
    monkeypatch.setattr(P, "_refresh_backend", _no_change)
    assert await P.refresh_cloud_backends_if_changed(state, config, proxy) is False
    assert proxy.reloads == 1  # unchanged


@pytest.mark.asyncio
async def test_no_cloud_backends_is_noop(monkeypatch, tmp_path):
    config = SimpleNamespace(backends=[{"name": "local", "type": "rkllama"}],
                             config_path=tmp_path / "c.yaml")
    proxy = _FakeProxy()
    state = SimpleNamespace(config=config, llm_proxy=proxy)
    assert await P.refresh_cloud_backends_if_changed(state, config, proxy) is False
    assert proxy.reloads == 0


@pytest.mark.asyncio
async def test_change_with_proxy_down_persists_but_reports_no_reload(monkeypatch, tmp_path):
    # Catalog changed but the proxy isn't running: persist the config, but the
    # return value must be False (no reload happened) rather than claiming one.
    backend = {"name": "kilo", "type": "kilocode", "url": "u", "models": [{"id": "a"}]}
    config = SimpleNamespace(backends=[backend], config_path=tmp_path / "config.yaml")

    class _DownProxy:
        def __init__(self): self.reloads = 0
        def is_running(self): return False
        async def reload_config(self, backends, secrets=None): self.reloads += 1

    proxy = _DownProxy()
    state = SimpleNamespace(config=config, llm_proxy=proxy)

    saved = {"n": 0}
    async def _save(*a, **k): saved["n"] += 1
    monkeypatch.setattr(P, "save_config_locked", _save)
    async def _adds_model(app_state, b, timeout=5.0):
        b["models"] = [{"id": "a"}, {"id": "b"}]
        return b
    monkeypatch.setattr(P, "_refresh_backend", _adds_model)

    assert await P.refresh_cloud_backends_if_changed(state, config, proxy) is False
    assert saved["n"] == 1          # config WAS persisted
    assert proxy.reloads == 0       # but no reload was attempted


@pytest.mark.asyncio
async def test_duplicate_ids_are_not_a_change(monkeypatch, tmp_path):
    # A transient duplicate id in a re-probed catalog must not count as a change.
    backend = {"name": "kilo", "type": "kilocode", "url": "u", "models": [{"id": "a"}, {"id": "b"}]}
    config = SimpleNamespace(backends=[backend], config_path=tmp_path / "config.yaml")
    proxy = _FakeProxy()
    state = SimpleNamespace(config=config, llm_proxy=proxy)

    async def _noop_save(*a, **k): return None
    async def _no_secrets(*a, **k): return []
    monkeypatch.setattr(P, "save_config_locked", _noop_save)
    monkeypatch.setattr(P, "_resolve_backend_secrets", _no_secrets)

    async def _dupes(app_state, b, timeout=5.0):
        b["models"] = [{"id": "a"}, {"id": "b"}, {"id": "a"}]  # same set, with a dupe
        return b
    monkeypatch.setattr(P, "_refresh_backend", _dupes)
    assert await P.refresh_cloud_backends_if_changed(state, config, proxy) is False
    assert proxy.reloads == 0


@pytest.mark.asyncio
async def test_refresher_start_stop():
    state = SimpleNamespace(config=SimpleNamespace(backends=[]), llm_proxy=None)
    r = CloudProviderRefresher(state, interval=0.01, initial_delay=0.01)
    await r.start()
    await r.stop()  # must not hang/raise
