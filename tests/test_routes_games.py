"""Route tests for games endpoints."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient, Request as HttpxRequest, Response

LEGAL_MOVES = ["e2e4", "d2d4", "g1f3"]
CHESS_BODY = {
    "agent_name": "chess-bot",
    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "legal_moves": LEGAL_MOVES,
    "history": ["e2e4", "e7e5"],
}


def _mock_httpx_response(content: str):
    mock_request = HttpxRequest("POST", "http://localhost:9999/message")
    return Response(
        status_code=200,
        json={"content": content},
        request=mock_request,
    )


def _set_hub_router_port(app, port: int = 9999):
    hub_router = MagicMock()
    hub_router.get_adapter_port.return_value = port
    app.state.channel_hub_router = hub_router
    return hub_router


@pytest.mark.asyncio
class TestChessMove:
    async def test_missing_agent_name_returns_400(self, client):
        resp = await client.post(
            "/api/games/chess/move",
            json={"legal_moves": LEGAL_MOVES},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "Missing agent_name or legal_moves"

    async def test_missing_legal_moves_returns_400(self, client):
        resp = await client.post(
            "/api/games/chess/move",
            json={"agent_name": "chess-bot"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "Missing agent_name or legal_moves"

    async def test_empty_legal_moves_returns_400(self, client):
        resp = await client.post(
            "/api/games/chess/move",
            json={"agent_name": "chess-bot", "legal_moves": []},
        )
        assert resp.status_code == 400

    async def test_unreachable_agent_returns_random_legal_move(self, client):
        app = client._transport.app
        app.state.channel_hub_router = None
        app.state.channel_hub = None

        resp = await client.post("/api/games/chess/move", json=CHESS_BODY)
        assert resp.status_code == 200
        data = resp.json()
        assert data["move"] in LEGAL_MOVES
        assert "not reachable" in data["commentary"].lower()

    async def test_agent_move_via_hub_router_port(self, client):
        app = client._transport.app
        _set_hub_router_port(app, 9999)

        with patch("tinyagentos.routes.games.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = _mock_httpx_response("Best move: e2e4")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            resp = await client.post("/api/games/chess/move", json=CHESS_BODY)

        assert resp.status_code == 200
        data = resp.json()
        assert data["move"] == "e2e4"
        assert "e2e4" in data["commentary"]
        mock_instance.post.assert_awaited_once()
        call_url = mock_instance.post.await_args.args[0]
        assert call_url == "http://localhost:9999/message"

    async def test_agent_move_via_channel_hub_adapter_url(self, client):
        app = client._transport.app
        app.state.channel_hub_router = None
        adapter_mgr = MagicMock()
        adapter_mgr.get_adapter_url.return_value = "http://localhost:8888"
        app.state.channel_hub = adapter_mgr

        with patch("tinyagentos.routes.games.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = _mock_httpx_response("d2d4")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            resp = await client.post("/api/games/chess/move", json=CHESS_BODY)

        assert resp.status_code == 200
        assert resp.json()["move"] == "d2d4"
        adapter_mgr.get_adapter_url.assert_called_once_with("chess-bot")

    async def test_invalid_agent_response_falls_back_to_random_move(self, client):
        app = client._transport.app
        _set_hub_router_port(app, 9999)

        with patch("tinyagentos.routes.games.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = _mock_httpx_response("I cannot decide")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            resp = await client.post("/api/games/chess/move", json=CHESS_BODY)

        assert resp.status_code == 200
        data = resp.json()
        assert data["move"] in LEGAL_MOVES
        assert "Agent returned" in data["commentary"]

    async def test_httpx_error_falls_back_to_random_move(self, client):
        app = client._transport.app
        _set_hub_router_port(app, 9999)

        with patch("tinyagentos.routes.games.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post.side_effect = ConnectionError("connection refused")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            resp = await client.post("/api/games/chess/move", json=CHESS_BODY)

        assert resp.status_code == 200
        data = resp.json()
        assert data["move"] in LEGAL_MOVES
        assert data["commentary"].startswith("Error:")

    async def test_unauthenticated_returns_401(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            resp = await c.post("/api/games/chess/move", json=CHESS_BODY)
            assert resp.status_code in (401, 403)


GAME_FILES = {
    "index.html": "<!doctype html><html><body><script src=\"./game.js\"></script></body></html>",
    "game.js": "console.log('hi');",
}


@pytest.mark.asyncio
class TestGamesCrud:
    async def test_list_empty(self, client):
        resp = await client.get("/api/games")
        assert resp.status_code == 200
        assert resp.json()["games"] == []

    async def test_save_and_get_game(self, client):
        resp = await client.put(
            "/api/games/my-game",
            json={"name": "My Game", "prompt": "a fun game", "template": "breakout", "files": GAME_FILES},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "my-game"
        assert data["name"] == "My Game"
        assert data["files"] == GAME_FILES

        resp = await client.get("/api/games/my-game")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "My Game"
        assert data["prompt"] == "a fun game"
        assert data["files"] == GAME_FILES
        assert "created" in data and "updated" in data

    async def test_save_updates_existing_and_removes_stale_files(self, client):
        await client.put("/api/games/my-game", json={"name": "v1", "files": GAME_FILES})
        resp = await client.put(
            "/api/games/my-game",
            json={"files": {"index.html": GAME_FILES["index.html"]}},
        )
        assert resp.status_code == 200
        data = resp.json()
        # name/prompt/template persist from the first save when omitted
        assert data["name"] == "v1"
        # game.js was dropped from the new file set, so it must be removed on disk
        resp = await client.get("/api/games/my-game")
        assert resp.json()["files"] == {"index.html": GAME_FILES["index.html"]}

    async def test_save_preserves_binary_assets(self, client):
        """Generated binary assets (textures/sprites) live in the game dir but
        never appear in the text file set the editor PUTs. A save must not sweep
        them, or a generated texture would vanish on the next edit."""
        app = client._transport.app
        await client.put("/api/games/asset-game", json={"name": "A", "files": GAME_FILES})
        # Simulate a generated PNG written into the game dir out-of-band.
        game_dir = app.state.data_dir / "games" / "asset-game"
        png = b"\x89PNG\r\n\x1a\n\x00binary-not-utf8\xff\xfe"
        (game_dir / "texture-1.png").write_bytes(png)
        # A subsequent text-only save (full-replace) must leave the PNG intact.
        resp = await client.put(
            "/api/games/asset-game",
            json={"files": {"index.html": GAME_FILES["index.html"]}},
        )
        assert resp.status_code == 200
        assert (game_dir / "texture-1.png").read_bytes() == png
        # The stale text file game.js was still swept as before.
        assert not (game_dir / "game.js").exists()

    async def test_list_after_save(self, client):
        await client.put("/api/games/game-a", json={"name": "A", "files": GAME_FILES})
        await client.put("/api/games/game-b", json={"name": "B", "files": GAME_FILES})
        resp = await client.get("/api/games")
        assert resp.status_code == 200
        ids = {g["id"] for g in resp.json()["games"]}
        assert ids == {"game-a", "game-b"}

    async def test_get_nonexistent_returns_404(self, client):
        resp = await client.get("/api/games/nope")
        assert resp.status_code == 404

    async def test_save_invalid_slug_rejected(self, client):
        resp = await client.put("/api/games/Not_Valid!", json={"files": GAME_FILES})
        assert resp.status_code == 400

    async def test_save_empty_files_rejected(self, client):
        resp = await client.put("/api/games/empty-game", json={"files": {}})
        assert resp.status_code == 400

    async def test_save_too_many_files_rejected(self, client):
        files = {f"file{i}.js": "x" for i in range(41)}
        resp = await client.put("/api/games/too-many", json={"files": files})
        assert resp.status_code == 413

    async def test_save_file_too_large_rejected(self, client):
        resp = await client.put(
            "/api/games/too-big",
            json={"files": {"game.js": "x" * (2 * 1024 * 1024 + 1)}},
        )
        assert resp.status_code == 413

    async def test_save_traversal_filename_rejected(self, client):
        for bad_name in ["../evil.js", "sub/dir.js", "sub\\dir.js", "..", "."]:
            resp = await client.put(
                "/api/games/traversal-test",
                json={"files": {bad_name: "x"}},
            )
            assert resp.status_code == 400, f"expected 400 for {bad_name!r}"

    async def test_delete_game(self, client):
        await client.put("/api/games/to-delete", json={"files": GAME_FILES})
        resp = await client.delete("/api/games/to-delete")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        resp = await client.get("/api/games/to-delete")
        assert resp.status_code == 404

    async def test_delete_nonexistent_returns_404(self, client):
        resp = await client.delete("/api/games/nope")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestGamesPreview:
    async def test_preview_serves_file_with_csp(self, client):
        await client.put("/api/games/preview-game", json={"files": GAME_FILES})
        resp = await client.get("/api/games/preview-game/preview/index.html")
        assert resp.status_code == 200
        assert resp.text == GAME_FILES["index.html"]
        csp = resp.headers["content-security-policy"]
        assert "sandbox allow-scripts" in csp
        assert "allow-same-origin" not in csp
        assert "default-src 'none'" in csp

    async def test_preview_nonexistent_game_404(self, client):
        resp = await client.get("/api/games/nope/preview/index.html")
        assert resp.status_code == 404

    async def test_preview_nonexistent_file_404(self, client):
        await client.put("/api/games/preview-game2", json={"files": GAME_FILES})
        resp = await client.get("/api/games/preview-game2/preview/missing.js")
        assert resp.status_code == 404

    async def test_preview_traversal_rejected(self, client):
        await client.put("/api/games/preview-game3", json={"files": GAME_FILES})
        resp = await client.get("/api/games/preview-game3/preview/..%2F..%2Fetc%2Fpasswd")
        assert resp.status_code in (400, 404)

    async def test_preview_cannot_serve_game_json(self, client):
        await client.put("/api/games/preview-game4", json={"files": GAME_FILES})
        resp = await client.get("/api/games/preview-game4/preview/game.json")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestGamesPackage:
    async def test_package_builds_valid_taosapp_zip(self, client):
        await client.put(
            "/api/games/pkg-game", json={"name": "Pkg Game", "files": GAME_FILES}
        )
        resp = await client.get("/api/games/pkg-game/package")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "pkg-game.taosapp" in resp.headers["content-disposition"]

        import io
        import zipfile
        import yaml

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = set(zf.namelist())
        assert names == {"manifest.yaml", "index.html", "game.js"}
        manifest = yaml.safe_load(zf.read("manifest.yaml").decode())
        assert manifest["id"] == "pkg-game"
        assert manifest["name"] == "Pkg Game"
        assert manifest["app_type"] == "web"
        assert manifest["entry"] == "index.html"
        assert manifest["permissions"] == []
        assert zf.read("index.html").decode() == GAME_FILES["index.html"]

    async def test_package_nonexistent_game_404(self, client):
        resp = await client.get("/api/games/nope/package")
        assert resp.status_code == 404

    async def test_package_installs_via_userspace_apps_endpoint(self, client):
        """The package this endpoint builds must be installable through the
        existing, unmodified userspace-apps install pipeline -- this is the
        actual reuse the Share flow depends on."""
        app = client._transport.app
        store = app.state.userspace_apps
        if store._db is not None:
            await store.close()
        await store.init()
        data_store = app.state.userspace_data
        if data_store._db is not None:
            await data_store.close()
        await data_store.init()

        await client.put(
            "/api/games/installable-game", json={"name": "Installable", "files": GAME_FILES}
        )
        pkg_resp = await client.get("/api/games/installable-game/package")
        assert pkg_resp.status_code == 200

        install_resp = await client.post(
            "/api/userspace-apps/install",
            files={"package": ("installable-game.taosapp", pkg_resp.content, "application/zip")},
            data={"provenance": "ai-generated"},
        )
        assert install_resp.status_code == 200
        data = install_resp.json()
        assert data["app_id"] == "installable-game"