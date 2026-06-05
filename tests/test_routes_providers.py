import pytest
import time as _time
from unittest.mock import patch, AsyncMock

@pytest.mark.asyncio
class TestProviderAPI:
    async def test_list_providers(self, client):
        resp = await client.get("/api/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    async def test_test_connection_missing_url(self, client):
        resp = await client.post("/api/providers/test", json={"type": "ollama"})
        assert resp.status_code == 422  # Pydantic validation requires url field

    async def test_add_provider(self, client):
        resp = await client.post("/api/providers", json={
            "name": "test-ollama", "type": "ollama",
            "url": "http://localhost:11434", "priority": 1,
        })
        assert resp.status_code == 200

    async def test_delete_provider(self, client):
        # Add then delete
        await client.post("/api/providers", json={
            "name": "to-delete", "type": "ollama",
            "url": "http://localhost:11434", "priority": 5,
        })
        resp = await client.delete("/api/providers/to-delete")
        assert resp.status_code == 200

    async def test_add_duplicate_provider(self, client):
        await client.post("/api/providers", json={
            "name": "dup-test", "type": "ollama",
            "url": "http://localhost:11434", "priority": 1,
        })
        resp = await client.post("/api/providers", json={
            "name": "dup-test", "type": "ollama",
            "url": "http://localhost:11434", "priority": 2,
        })
        assert resp.status_code == 409

    async def test_add_duplicate_by_type_and_url(self, client):
        """Two providers with different names but the same (type, url) is
        always wrong — they'd point at the same backend and the picker /
        lifecycle logic can't disambiguate. johny on #312 saw two rkllama
        entries because this case wasn't blocked.
        """
        first = await client.post("/api/providers", json={
            "name": "rkllama-one", "type": "rkllama",
            "url": "http://localhost:18080", "priority": 1,
        })
        assert first.status_code == 200
        second = await client.post("/api/providers", json={
            "name": "rkllama-two", "type": "rkllama",
            "url": "http://localhost:18080", "priority": 2,
        })
        assert second.status_code == 409
        body = second.json()
        assert "rkllama" in body.get("error", "").lower()
        assert "already exists" in body.get("error", "").lower()

    async def test_add_kilocode_autofills_url_and_models(self, client, app):
        """Kilocode add form only collects name + api_key — server must fill
        the canonical base URL and a routable model list (from live probe,
        falling back to the seed) so generate_litellm_config registers at
        least one routable model."""
        # Stub the probe to a deterministic empty result so the test
        # doesn't touch the real kilocode endpoint; the seed list then
        # kicks in, which is the guarantee we care about.
        with patch(
            "tinyagentos.routes.providers._discover_provider_models",
            new=AsyncMock(return_value=[]),
        ):
            resp = await client.post("/api/providers", json={
                "name": "kilo-auto-test",
                "type": "kilocode",
                "api_key_secret": "provider-kilo-auto-test-key",
            })
            assert resp.status_code == 200

        stored = next(
            b for b in app.state.config.backends
            if b.get("name") == "kilo-auto-test"
        )
        assert stored["url"] == "https://api.kilo.ai/api/gateway"
        assert stored.get("models"), "models list should be auto-populated"
        model_ids = [
            m.get("id") if isinstance(m, dict) else m for m in stored["models"]
        ]
        assert "kilo-auto/free" in model_ids

    async def test_add_kilocode_respects_caller_supplied_url_and_models(self, client, app):
        """Caller-supplied url/models override the autofill defaults."""
        resp = await client.post("/api/providers", json={
            "name": "kilo-custom",
            "type": "kilocode",
            "url": "https://example.test/api",
            "models": [{"id": "custom/model-a"}],
            "api_key_secret": "provider-kilo-custom-key",
        })
        assert resp.status_code == 200
        stored = next(
            b for b in app.state.config.backends
            if b.get("name") == "kilo-custom"
        )
        assert stored["url"] == "https://example.test/api"
        model_ids = [
            m.get("id") if isinstance(m, dict) else m for m in stored["models"]
        ]
        assert model_ids == ["custom/model-a"]

    async def test_add_provider_discovers_models_when_empty(self, client, app):
        """Empty models list on a cloud provider triggers a server-side
        probe of ``{url}/models``. Works for any provider type that
        returns an OpenAI-shaped payload — no per-type branching."""
        fake_ids = ["disco/model-x", "disco/model-y"]
        with patch(
            "tinyagentos.routes.providers._discover_provider_models",
            new=AsyncMock(return_value=[{"id": mid} for mid in fake_ids]),
        ):
            resp = await client.post("/api/providers", json={
                "name": "disco",
                "type": "openrouter",
                "api_key_secret": "provider-disco-key",
            })
            assert resp.status_code == 200

        # Assert against stored config (list-providers live-probes and
        # replaces `models`, which would mask what add_provider actually
        # persisted).
        stored_entry = next(
            b for b in app.state.config.backends if b.get("name") == "disco"
        )
        assert stored_entry["url"] == "https://openrouter.ai/api/v1"
        stored_ids = [
            m.get("id") if isinstance(m, dict) else m
            for m in stored_entry["models"]
        ]
        assert stored_ids == fake_ids

    async def test_add_provider_discovery_failure_keeps_entry(self, client, app):
        """A failing probe must NOT block saving the provider — we still
        persist the entry, log a warning, and let the user refine models
        by hand. kilocode falls back to the seed list; a cloud type with
        no seed saves with an empty models list."""
        with patch(
            "tinyagentos.routes.providers._discover_provider_models",
            new=AsyncMock(return_value=[]),
        ):
            resp = await client.post("/api/providers", json={
                "name": "offline-openrouter",
                "type": "openrouter",
                "api_key_secret": "provider-offline-key",
            })
            assert resp.status_code == 200

            resp_kilo = await client.post("/api/providers", json={
                "name": "offline-kilo",
                "type": "kilocode",
                "api_key_secret": "provider-offline-kilo-key",
            })
            assert resp_kilo.status_code == 200

        or_entry = next(
            b for b in app.state.config.backends
            if b.get("name") == "offline-openrouter"
        )
        assert or_entry["url"] == "https://openrouter.ai/api/v1"
        # openrouter has no default seed — empty/missing models is fine;
        # generate_litellm_config will log the incomplete-backend warning.
        assert not or_entry.get("models")

        kilo_entry = next(
            b for b in app.state.config.backends
            if b.get("name") == "offline-kilo"
        )
        kilo_ids = [
            m.get("id") if isinstance(m, dict) else m
            for m in kilo_entry["models"]
        ]
        assert "kilo-auto/free" in kilo_ids


@pytest.mark.asyncio
class TestModelsPassthrough:
    async def test_get_models_endpoint_passthrough(self, client, app):
        """``GET /api/providers/models`` returns LiteLLM's /v1/models payload
        verbatim under ``data``, plus ``object``, ``cached_at``, ``refreshed``.
        """
        fake_models = [{"id": "alpha/model-1"}, {"id": "beta/model-2"}]
        with patch(
            "tinyagentos.routes.providers._fetch_litellm_models",
            new=AsyncMock(return_value=fake_models),
        ), patch(
            "tinyagentos.routes.providers._refresh_all_cloud_backends",
            new=AsyncMock(return_value=0),
        ):
            resp = await client.get("/api/providers/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == fake_models
        assert body["object"] == "list"
        assert "cached_at" in body
        assert "refreshed" in body

    async def test_get_models_refresh_triggers_discovery(self, client, app):
        """``?refresh=true`` always re-probes cloud backends before fetching."""
        refresh_mock = AsyncMock(return_value=1)
        with patch(
            "tinyagentos.routes.providers._refresh_all_cloud_backends",
            new=refresh_mock,
        ), patch(
            "tinyagentos.routes.providers._fetch_litellm_models",
            new=AsyncMock(return_value=[{"id": "x"}]),
        ):
            resp = await client.get("/api/providers/models?refresh=true")
        assert resp.status_code == 200
        assert refresh_mock.await_count == 1
        assert resp.json()["refreshed"] is True

    async def test_get_models_cache_hit_skips_refresh(self, client, app):
        """A recent cache entry skips the refresh probe and returns
        ``refreshed=false``."""
        app.state.litellm_models_cache = {
            "data": [{"id": "cached/model"}],
            "object": "list",
        }
        # Fresh monotonic timestamp — well inside the 60s TTL window.
        import time as _time
        app.state.litellm_models_cache_at = _time.monotonic()
        app.state.litellm_models_cache_wallclock = _time.time()

        refresh_mock = AsyncMock(return_value=0)
        fetch_mock = AsyncMock(return_value=[])
        with patch(
            "tinyagentos.routes.providers._refresh_all_cloud_backends",
            new=refresh_mock,
        ), patch(
            "tinyagentos.routes.providers._fetch_litellm_models",
            new=fetch_mock,
        ):
            resp = await client.get("/api/providers/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["refreshed"] is False
        assert body["data"] == [{"id": "cached/model"}]
        # Neither probe should have fired on a cache hit.
        assert refresh_mock.await_count == 0
        assert fetch_mock.await_count == 0

    async def test_patch_provider_reprobes_cloud_backend(self, client, app):
        """PATCH on a cloud provider re-probes its /models endpoint so the
        new URL/key is live without requiring a full app restart."""
        # Seed a kilocode provider so we can PATCH it.
        with patch(
            "tinyagentos.routes.providers._discover_provider_models",
            new=AsyncMock(return_value=[]),
        ):
            add_resp = await client.post("/api/providers", json={
                "name": "kilo-patch-target",
                "type": "kilocode",
                "api_key_secret": "provider-kilo-patch-key",
            })
            assert add_resp.status_code == 200

        refresh_mock = AsyncMock(side_effect=lambda state, b, timeout=3.0: b)
        with patch(
            "tinyagentos.routes.providers._refresh_backend",
            new=refresh_mock,
        ):
            patch_resp = await client.patch(
                "/api/providers/kilo-patch-target",
                json={"enabled": True},
            )
        assert patch_resp.status_code == 200
        assert refresh_mock.await_count == 1
        called_backend = refresh_mock.await_args.args[1]
        assert called_backend.get("name") == "kilo-patch-target"

    async def test_add_local_provider_with_api_key_secret(self, client, app):
        """A local provider (llama-cpp) can be created with api_key_secret and
        the secret name is persisted in the config — backend does not block it."""
        resp = await client.post("/api/providers", json={
            "name": "llama-with-key",
            "type": "llama-cpp",
            "url": "http://localhost:8080",
            "api_key_secret": "provider-llama-with-key-key",
        })
        assert resp.status_code == 200
        stored = next(
            b for b in app.state.config.backends
            if b.get("name") == "llama-with-key"
        )
        assert stored["api_key_secret"] == "provider-llama-with-key-key"

    async def test_add_openai_compatible_provider_with_custom_url(self, client, app):
        """An openai-compatible provider can be created with a caller-supplied
        URL; no URL autofill applies since there is no canonical endpoint."""
        custom_url = "http://192.168.1.50:8000/v1"
        resp = await client.post("/api/providers", json={
            "name": "my-litellm",
            "type": "openai-compatible",
            "url": custom_url,
            "api_key_secret": "provider-my-litellm-key",
        })
        assert resp.status_code == 200
        stored = next(
            b for b in app.state.config.backends
            if b.get("name") == "my-litellm"
        )
        assert stored["url"] == custom_url
        assert stored["api_key_secret"] == "provider-my-litellm-key"

    async def test_empty_live_fetch_falls_back_to_cache(self, client, app):
        """When _fetch_litellm_models returns [] (LiteLLM down / timeout),
        the endpoint must serve the last-good cache rather than an empty list.
        This is the primary fix for issue #606.
        """
        # Pre-warm the cache with a good catalog.
        app.state.litellm_models_cache = {
            "data": [{"id": "good/model-cached"}],
            "object": "list",
        }
        app.state.litellm_models_cache_at = _time.monotonic()
        app.state.litellm_models_cache_wallclock = _time.time()

        with patch(
            "tinyagentos.routes.providers._fetch_litellm_models",
            new=AsyncMock(return_value=[]),  # LiteLLM returns nothing
        ), patch(
            "tinyagentos.routes.providers._refresh_all_cloud_backends",
            new=AsyncMock(return_value=0),
        ):
            # Force-bypass the TTL so a refresh is attempted.
            resp = await client.get("/api/providers/models?refresh=true")
        assert resp.status_code == 200
        body = resp.json()
        # Must serve the cached catalog, not an empty list.
        assert body["data"] == [{"id": "good/model-cached"}]
        # refreshed=false signals the UI that the live fetch didn't produce new data.
        assert body["refreshed"] is False

    async def test_force_refresh_endpoint(self, client, app):
        """POST /api/providers/models/refresh always re-probes and returns
        fresh data, bypassing the TTL.
        """
        fresh_models = [{"id": "new/model-1"}, {"id": "new/model-2"}]
        refresh_mock = AsyncMock(return_value=1)
        with patch(
            "tinyagentos.routes.providers._refresh_all_cloud_backends",
            new=refresh_mock,
        ), patch(
            "tinyagentos.routes.providers._fetch_litellm_models",
            new=AsyncMock(return_value=fresh_models),
        ):
            resp = await client.post("/api/providers/models/refresh")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == fresh_models
        assert body["refreshed"] is True
        assert refresh_mock.await_count == 1

    async def test_force_refresh_falls_back_to_cache_on_empty(self, client, app):
        """POST /api/providers/models/refresh: if the live fetch returns
        empty, serve the last-good cache (don't wipe a good catalog).
        """
        app.state.litellm_models_cache = {
            "data": [{"id": "preserved/model"}],
            "object": "list",
        }
        app.state.litellm_models_cache_wallclock = _time.time()

        with patch(
            "tinyagentos.routes.providers._refresh_all_cloud_backends",
            new=AsyncMock(return_value=0),
        ), patch(
            "tinyagentos.routes.providers._fetch_litellm_models",
            new=AsyncMock(return_value=[]),
        ):
            resp = await client.post("/api/providers/models/refresh")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == [{"id": "preserved/model"}]
        assert body["refreshed"] is False

    async def test_stale_cache_triggers_refresh(self, client, app):
        """A cache entry older than MODELS_CACHE_TTL_SECONDS triggers a
        live refresh on GET /api/providers/models.
        """
        from tinyagentos.routes.providers import MODELS_CACHE_TTL_SECONDS
        app.state.litellm_models_cache = {
            "data": [{"id": "stale/model"}],
            "object": "list",
        }
        # Put the cache timestamp PAST the TTL.
        app.state.litellm_models_cache_at = _time.monotonic() - MODELS_CACHE_TTL_SECONDS - 1
        app.state.litellm_models_cache_wallclock = _time.time() - MODELS_CACHE_TTL_SECONDS - 1

        refresh_mock = AsyncMock(return_value=1)
        with patch(
            "tinyagentos.routes.providers._refresh_all_cloud_backends",
            new=refresh_mock,
        ), patch(
            "tinyagentos.routes.providers._fetch_litellm_models",
            new=AsyncMock(return_value=[{"id": "fresh/model"}]),
        ):
            resp = await client.get("/api/providers/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == [{"id": "fresh/model"}]
        assert body["refreshed"] is True
        assert refresh_mock.await_count == 1

    async def test_add_provider_invalidates_cache(self, client, app):
        """Adding a provider must invalidate the models cache so the next
        dialog open re-reads LiteLLM with the new provider included.
        """
        import asyncio as _asyncio
        app.state.litellm_models_cache = {"data": [{"id": "x"}], "object": "list"}
        app.state.litellm_models_cache_at = _asyncio.get_event_loop().time()

        with patch(
            "tinyagentos.routes.providers._discover_provider_models",
            new=AsyncMock(return_value=[]),
        ):
            resp = await client.post("/api/providers", json={
                "name": "new-provider",
                "type": "ollama",
                "url": "http://localhost:11434",
            })
        assert resp.status_code == 200
        # Cache timestamp must be 0 (invalidated).
        assert app.state.litellm_models_cache_at == 0.0

    async def test_delete_provider_invalidates_cache(self, client, app):
        """Deleting a provider must invalidate the models cache."""
        import asyncio as _asyncio
        # Add then delete.
        await client.post("/api/providers", json={
            "name": "to-delete-cache",
            "type": "ollama",
            "url": "http://localhost:11435",
        })
        app.state.litellm_models_cache = {"data": [{"id": "y"}], "object": "list"}
        app.state.litellm_models_cache_at = _asyncio.get_event_loop().time()

        resp = await client.delete("/api/providers/to-delete-cache")
        assert resp.status_code == 200
        assert app.state.litellm_models_cache_at == 0.0
