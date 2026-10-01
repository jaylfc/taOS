"""Cloud-provider periodic refresh: persist the catalog only when it changes.

The gateway reads its routing table from the config per call, so a changed
catalog only has to be persisted; there is no proxy to reload (LiteLLM removal
stage 2b-2a).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import tinyagentos.routes.providers as P
from tinyagentos.provider_refresh import CloudProviderRefresher


def _counting_save(monkeypatch):
    saved = {"n": 0}

    async def _save(*a, **k):
        saved["n"] += 1
    monkeypatch.setattr(P, "save_config_locked", _save)
    return saved


@pytest.mark.asyncio
async def test_persists_only_when_catalog_changes(monkeypatch, tmp_path):
    backend = {"name": "kilo", "type": "kilocode", "url": "u", "models": [{"id": "a"}]}
    config = SimpleNamespace(backends=[backend], config_path=tmp_path / "config.yaml")
    state = SimpleNamespace(config=config, llm_proxy=None)
    saved = _counting_save(monkeypatch)

    # 1) Re-probe discovers a NEW model -> change -> persisted, True.
    async def _adds_model(app_state, b, timeout=5.0):
        b["models"] = [{"id": "a"}, {"id": "b"}]
        return b
    monkeypatch.setattr(P, "_refresh_backend", _adds_model)
    assert await P.refresh_cloud_backends_if_changed(state, config) is True
    assert saved["n"] == 1

    # 2) Re-probe finds the SAME models -> no change -> nothing written.
    async def _no_change(app_state, b, timeout=5.0):
        return b
    monkeypatch.setattr(P, "_refresh_backend", _no_change)
    assert await P.refresh_cloud_backends_if_changed(state, config) is False
    assert saved["n"] == 1  # unchanged


@pytest.mark.asyncio
async def test_no_cloud_backends_is_noop(monkeypatch, tmp_path):
    config = SimpleNamespace(backends=[{"name": "local", "type": "rkllama"}],
                             config_path=tmp_path / "c.yaml")
    state = SimpleNamespace(config=config, llm_proxy=None)
    saved = _counting_save(monkeypatch)
    assert await P.refresh_cloud_backends_if_changed(state, config) is False
    assert saved["n"] == 0


@pytest.mark.asyncio
async def test_duplicate_ids_are_not_a_change(monkeypatch, tmp_path):
    # A transient duplicate id in a re-probed catalog must not count as a change.
    backend = {"name": "kilo", "type": "kilocode", "url": "u", "models": [{"id": "a"}, {"id": "b"}]}
    config = SimpleNamespace(backends=[backend], config_path=tmp_path / "config.yaml")
    state = SimpleNamespace(config=config, llm_proxy=None)
    saved = _counting_save(monkeypatch)

    async def _dupes(app_state, b, timeout=5.0):
        b["models"] = [{"id": "a"}, {"id": "b"}, {"id": "a"}]  # same set, with a dupe
        return b
    monkeypatch.setattr(P, "_refresh_backend", _dupes)
    assert await P.refresh_cloud_backends_if_changed(state, config) is False
    assert saved["n"] == 0


@pytest.mark.asyncio
async def test_refresher_start_stop():
    state = SimpleNamespace(config=SimpleNamespace(backends=[]), llm_proxy=None)
    r = CloudProviderRefresher(state, interval=0.01, initial_delay=0.01)
    await r.start()
    await r.stop()  # must not hang/raise
