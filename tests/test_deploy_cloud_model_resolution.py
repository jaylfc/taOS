"""Deploy/model-change must resolve models from every cloud provider type.

Regression: the resolver only gathered models from openai/anthropic backends,
so deploying an agent on a kilocode / openrouter / openai-compatible model
(e.g. the kilo-auto/free test model) 404'd as "model not found". See the
discussion #357 fresh-install repro.
"""
import pytest

import tinyagentos.cluster.model_resolver as model_resolver


class _NotFound:
    kind = "not_found"
    hosts: list = []


@pytest.fixture
def capture_cloud_models(monkeypatch):
    """Patch find_model_hosts to record the cloud_models it was handed."""
    seen: dict = {}

    def _fake(model, *, cluster_state=None, local_models=None, cloud_models=None):
        seen["cloud_models"] = list(cloud_models or [])
        return _NotFound()

    monkeypatch.setattr(model_resolver, "find_model_hosts", _fake)
    return seen


def _add_kilocode_backend(app):
    app.state.config.backends.append({
        "name": "kilocode",
        "type": "kilocode",
        "url": "https://api.kilo.ai/api/gateway",
        "models": [{"name": "kilo-auto/free", "size_mb": 0}],
    })


@pytest.mark.asyncio
async def test_deploy_gathers_kilocode_models(client, app, capture_cloud_models):
    _add_kilocode_backend(app)

    resp = await client.post(
        "/api/agents/deploy",
        json={"name": "Hermes Test", "framework": "none", "model": "kilo-auto/free"},
    )

    # Resolution must have considered the kilocode model (the fix). The patched
    # resolver returns not_found, so the request 404s — that's fine; we assert
    # on what the resolver was actually given.
    assert "kilo-auto/free" in capture_cloud_models["cloud_models"]
    assert resp.status_code >= 400  # _NotFound short-circuits before container creation


@pytest.mark.asyncio
async def test_model_change_gathers_openai_compatible_models(client, app, capture_cloud_models):
    app.state.config.backends.append({
        "name": "local-llama",
        "type": "openai-compatible",
        "url": "http://localhost:8081",
        "models": [{"id": "llama-3.1-8b"}],
    })

    resp = await client.post(
        "/api/agents/test-agent/model",
        json={"model": "llama-3.1-8b"},
    )

    assert "llama-3.1-8b" in capture_cloud_models["cloud_models"]
    assert resp.status_code >= 400
