"""Route tests for the Game Studio texture/sprite generation endpoint.

ComfyUI is not installed in CI, so the ComfyUI client is patched in every test
that reaches a backend — no real generation happens.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.asset_gen.comfyui_client import ComfyUIResult

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

GAME_FILES = {"index.html": "<!doctype html><html></html>", "game.js": "// game"}


def _set_gpu_tier(app):
    """Give the host a discrete GPU so the resolver picks the SDXL tier."""
    app.state.backend_catalog = None
    app.state.hardware_profile = SimpleNamespace(
        gpu=SimpleNamespace(cuda=True, rocm=False),
        npu=SimpleNamespace(type="none"),
    )


def _set_no_accelerator(app):
    app.state.backend_catalog = None
    app.state.hardware_profile = SimpleNamespace(
        gpu=SimpleNamespace(cuda=False, rocm=False),
        npu=SimpleNamespace(type="none"),
    )


def _patch_client(result):
    """Patch ComfyUIClient so .generate() returns *result* without any network."""
    fake = MagicMock()
    fake.generate = AsyncMock(return_value=result)
    return patch("tinyagentos.routes.game_assets.ComfyUIClient", return_value=fake)


async def _save_game(client, game_id="tex-game"):
    resp = await client.put(f"/api/games/{game_id}", json={"name": "Tex", "files": GAME_FILES})
    assert resp.status_code == 200
    return game_id


@pytest.mark.asyncio
class TestGenerateTexture:
    async def test_success_writes_asset_and_returns_path(self, client):
        app = client._transport.app
        _set_gpu_tier(app)
        gid = await _save_game(client)

        with _patch_client(ComfyUIResult(image_bytes=PNG_BYTES, prompt_id="p1")):
            resp = await client.post(
                f"/api/games/{gid}/assets/texture",
                json={"prompt": "a mossy stone texture", "kind": "texture"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert data["status"] == "generated"
        assert data["tier"] == "sdxl"
        assert data["filename"].startswith("texture-")
        assert data["path"] == f"/api/games/{gid}/preview/{data['filename']}"

        # The PNG is really in the game's file set: the preview route serves it.
        preview = await client.get(data["path"])
        assert preview.status_code == 200
        assert preview.content == PNG_BYTES

    async def test_sprite_kind_prefixes_filename(self, client):
        app = client._transport.app
        _set_gpu_tier(app)
        gid = await _save_game(client, "sprite-game")
        with _patch_client(ComfyUIResult(image_bytes=PNG_BYTES, prompt_id="p1")):
            resp = await client.post(
                f"/api/games/{gid}/assets/texture",
                json={"prompt": "a pixel-art ship", "kind": "sprite"},
            )
        assert resp.json()["filename"].startswith("sprite-")

    async def test_tier_unavailable_returns_available_false(self, client):
        app = client._transport.app
        _set_no_accelerator(app)
        gid = await _save_game(client, "no-gpu-game")

        # No client is patched: the route must short-circuit before generating.
        resp = await client.post(
            f"/api/games/{gid}/assets/texture",
            json={"prompt": "a texture"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False
        assert "reason" in data

    async def test_comfyui_failure_is_fail_soft_502(self, client):
        app = client._transport.app
        _set_gpu_tier(app)
        gid = await _save_game(client, "fail-game")
        with _patch_client(None):  # client swallowed the error, returned None
            resp = await client.post(
                f"/api/games/{gid}/assets/texture",
                json={"prompt": "a texture"},
            )
        assert resp.status_code == 502
        data = resp.json()
        assert "error" in data
        # `available` is a 200-only tier-gate signal; a 502 error body omits it
        # (the client throws on non-2xx and reads only `.error`).
        assert "available" not in data

    async def test_asset_count_cap_returns_409(self, client, monkeypatch):
        import tinyagentos.routes.game_assets as ga

        monkeypatch.setattr(ga, "_MAX_ASSET_FILES", 1)
        app = client._transport.app
        _set_gpu_tier(app)
        gid = await _save_game(client, "cap-game")
        with _patch_client(ComfyUIResult(image_bytes=PNG_BYTES, prompt_id="p1")):
            first = await client.post(
                f"/api/games/{gid}/assets/texture", json={"prompt": "one"}
            )
            assert first.status_code == 200  # wrote the 1st (and only allowed) asset
            second = await client.post(
                f"/api/games/{gid}/assets/texture", json={"prompt": "two"}
            )
        assert second.status_code == 409
        assert "error" in second.json()

    async def test_empty_prompt_rejected(self, client):
        app = client._transport.app
        _set_gpu_tier(app)
        gid = await _save_game(client, "empty-prompt-game")
        resp = await client.post(
            f"/api/games/{gid}/assets/texture",
            json={"prompt": "   "},
        )
        assert resp.status_code == 400

    async def test_invalid_size_rejected(self, client):
        app = client._transport.app
        _set_gpu_tier(app)
        gid = await _save_game(client, "bad-size-game")
        for bad in ({"width": 513}, {"width": 32}, {"height": 4096}):
            resp = await client.post(
                f"/api/games/{gid}/assets/texture",
                json={"prompt": "a texture", **bad},
            )
            assert resp.status_code == 400, bad

    async def test_unknown_game_returns_404(self, client):
        app = client._transport.app
        _set_gpu_tier(app)
        resp = await client.post(
            "/api/games/does-not-exist/assets/texture",
            json={"prompt": "a texture"},
        )
        assert resp.status_code == 404

    async def test_invalid_game_id_returns_400(self, client):
        resp = await client.post(
            "/api/games/Not_Valid!/assets/texture",
            json={"prompt": "a texture"},
        )
        assert resp.status_code == 400

    async def test_catalog_comfyui_backend_wins(self, client):
        """A live ComfyUI backend in the catalog resolves to the SDXL tier and
        its URL is used to build the client."""
        app = client._transport.app
        catalog = MagicMock()
        catalog.backends_with_capability.return_value = [
            SimpleNamespace(type="comfyui", url="http://gpu-worker:8188")
        ]
        app.state.backend_catalog = catalog
        app.state.hardware_profile = None
        gid = await _save_game(client, "catalog-game")

        with patch(
            "tinyagentos.routes.game_assets.ComfyUIClient",
            return_value=MagicMock(generate=AsyncMock(
                return_value=ComfyUIResult(image_bytes=PNG_BYTES, prompt_id="p1")
            )),
        ) as MockClient:
            resp = await client.post(
                f"/api/games/{gid}/assets/texture",
                json={"prompt": "a texture"},
            )
        assert resp.status_code == 200
        assert resp.json()["tier"] == "sdxl"
        MockClient.assert_called_once_with("http://gpu-worker:8188")
