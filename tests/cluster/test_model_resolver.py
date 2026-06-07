"""Tests for collect_cloud_model_ids and resolve_model_location.

These cover the two helpers extracted from the deploy/update-model preamble
in routes/agents.py.  find_model_hosts internals are already tested elsewhere;
here we only verify the wiring (gather from app.state + call find_model_hosts).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tinyagentos.providers import CLOUD_TYPES
from tinyagentos.cluster.model_resolver import (
    ModelLocation,
    collect_cloud_model_ids,
    resolve_model_location,
)

# A cloud provider type guaranteed to be in CLOUD_TYPES.
_CLOUD_TYPE = next(iter(sorted(CLOUD_TYPES)))


def _make_config(backends):
    return SimpleNamespace(backends=backends)


class TestCollectCloudModelIds:
    def test_cloud_backend_dict_models_with_id(self):
        config = _make_config([
            {
                "type": _CLOUD_TYPE,
                "models": [
                    {"id": "gpt-4o"},
                    {"id": "gpt-4o-mini"},
                ],
            }
        ])
        assert collect_cloud_model_ids(config) == ["gpt-4o", "gpt-4o-mini"]

    def test_dict_model_falls_back_to_name_when_no_id(self):
        config = _make_config([
            {
                "type": _CLOUD_TYPE,
                "models": [{"name": "claude-3-opus"}],
            }
        ])
        assert collect_cloud_model_ids(config) == ["claude-3-opus"]

    def test_bare_string_model(self):
        config = _make_config([
            {"type": _CLOUD_TYPE, "models": ["gpt-4"]},
        ])
        assert collect_cloud_model_ids(config) == ["gpt-4"]

    def test_non_cloud_backend_ignored(self):
        config = _make_config([
            {"type": "ollama", "models": [{"id": "llama3"}]},
        ])
        assert collect_cloud_model_ids(config) == []

    def test_empty_backends_returns_empty(self):
        assert collect_cloud_model_ids(_make_config([])) == []

    def test_none_backends_returns_empty(self):
        assert collect_cloud_model_ids(_make_config(None)) == []

    def test_backend_with_none_models_returns_empty(self):
        config = _make_config([{"type": _CLOUD_TYPE, "models": None}])
        assert collect_cloud_model_ids(config) == []

    def test_malformed_backend_does_not_propagate(self):
        # A non-dict backend that would raise on .get() — should return
        # whatever was gathered before the error, never propagate.
        config = _make_config([
            {"type": _CLOUD_TYPE, "models": [{"id": "gpt-4"}]},
            "not-a-dict",  # will raise AttributeError on .get()
        ])
        result = collect_cloud_model_ids(config)
        # Must not raise; may return the models collected before the bad entry
        assert isinstance(result, list)

    def test_dict_model_with_neither_id_nor_name_skipped(self):
        config = _make_config([
            {"type": _CLOUD_TYPE, "models": [{"other": "x"}]},
        ])
        assert collect_cloud_model_ids(config) == []


class TestResolveModelLocation:
    def _make_request(self, *, local_model_names=None, backends=None, workers=None):
        """Build a minimal fake request with app.state wired up."""
        local_model_names = local_model_names or []
        backends = backends or []

        catalog = SimpleNamespace(
            all_models=lambda: [{"name": n} for n in local_model_names],
        )

        cluster = SimpleNamespace(
            get_workers=lambda: workers or [],
        )

        config = SimpleNamespace(backends=backends)

        state = SimpleNamespace(
            cluster_manager=cluster,
            backend_catalog=catalog,
            config=config,
        )

        app = SimpleNamespace(state=state)
        return SimpleNamespace(app=app)

    def test_model_on_controller_returns_controller(self):
        request = self._make_request(local_model_names=["llama3"])
        location = resolve_model_location(request, "llama3")
        assert location.kind == "controller"

    def test_unknown_model_returns_not_found(self):
        request = self._make_request(local_model_names=["llama3"])
        location = resolve_model_location(request, "gpt-99-unknown")
        assert location.kind == "not_found"

    def test_cloud_model_returns_cloud(self):
        request = self._make_request(
            local_model_names=[],
            backends=[{"type": _CLOUD_TYPE, "models": [{"id": "gpt-4o"}]}],
        )
        location = resolve_model_location(request, "gpt-4o")
        assert location.kind == "cloud"

    def test_returns_model_location_instance(self):
        request = self._make_request()
        result = resolve_model_location(request, "anything")
        assert isinstance(result, ModelLocation)

    def test_no_catalog_does_not_raise(self):
        """backend_catalog missing from state → local_models=[] gracefully."""
        state = SimpleNamespace(
            cluster_manager=SimpleNamespace(get_workers=lambda: []),
            config=SimpleNamespace(backends=[]),
        )
        # No backend_catalog attribute at all
        request = SimpleNamespace(app=SimpleNamespace(state=state))
        location = resolve_model_location(request, "some-model")
        assert location.kind == "not_found"
