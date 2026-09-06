"""Project lists routes: session owner/admin and project-bound agent JWT.

Pin the dual-auth contract for /api/projects/{id}/lists and entries:

   1. A session owner/admin can CRUD lists and their entries.
   2. An approved agent token (scope project_lists) bound to project A can
      CRUD lists and entries ONLY for project A; a token bound elsewhere
      collapses into an existence-hiding 404.
   3. A token missing the project_lists scope is rejected 403 (it proved who it
      is, it just may not do this); an unauthenticated request - no session and
      no token - is 401.
   4. An unknown scope is rejected at mint.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token


@pytest_asyncio.fixture
async def ctx(client):
    """Reuse the session-admin `client` app and additionally init the agent
    registry + grants stores so tokens can be minted against real stores."""
    app = client._transport.app
    for attr in ("agent_registry", "agent_grants"):
        store = getattr(app.state, attr)
        if store._db is None:
            await store.init()
    uid = app.state.auth.find_user("admin")["id"]
    yield SimpleNamespace(client=client, app=app, uid=uid)
    for attr in ("agent_registry", "agent_grants"):
        store = getattr(app.state, attr)
        if store._db is not None:
            await store.close()


def _bare(app):
    """Cookieless client so requests carry only the Bearer header."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _new_project(ctx, slug):
    resp = await ctx.client.post("/api/projects", json={"name": slug, "slug": slug})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _mint_agent(ctx, project_id, scopes=("project_lists",)):
    registry = ctx.app.state.agent_registry
    grants = ctx.app.state.agent_grants
    priv, _pub = ctx.app.state.agent_registry_keypair
    rec = await registry.register(
        framework="grok",
        display_name="Grok",
        origin="external-selfjoin",
        handle="@grok",
    )
    cid = rec["canonical_id"]
    await registry.set_status(cid, "active")
    for scope in scopes:
        await grants.add_grant(cid, scope, project_id=project_id)
    token = mint_registry_token(
        cid, priv, user_id="u", framework="grok", project_id=project_id
    )
    return cid, token


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
class TestOwnerSessionCRUD:
    async def test_create_list(self, ctx):
        pid = await _new_project(ctx, "alpha")
        resp = await ctx.client.post(
            f"/api/projects/{pid}/lists", json={"title": "Shopping", "description": "groceries"}
        )
        assert resp.status_code == 200, resp.text
        lst = resp.json()
        assert lst["title"] == "Shopping"
        assert lst["description"] == "groceries"
        assert lst["status"] == "active"
        assert lst["id"].startswith("lst-")

    async def test_list_lists(self, ctx):
        pid = await _new_project(ctx, "alpha")
        await ctx.client.post(
            f"/api/projects/{pid}/lists", json={"title": "A", "description": ""}
        )
        await ctx.client.post(
            f"/api/projects/{pid}/lists", json={"title": "B", "description": ""}
        )
        resp = await ctx.client.get(f"/api/projects/{pid}/lists")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2

    async def test_get_list(self, ctx):
        pid = await _new_project(ctx, "alpha")
        created = await ctx.client.post(
            f"/api/projects/{pid}/lists", json={"title": "A", "description": ""}
        )
        lst_id = created.json()["id"]
        resp = await ctx.client.get(f"/api/projects/{pid}/lists/{lst_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == lst_id

    async def test_update_list(self, ctx):
        pid = await _new_project(ctx, "alpha")
        created = await ctx.client.post(
            f"/api/projects/{pid}/lists", json={"title": "A", "description": ""}
        )
        lst_id = created.json()["id"]
        resp = await ctx.client.patch(
            f"/api/projects/{pid}/lists/{lst_id}", json={"title": "Renamed", "status": "archived"}
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Renamed"
        assert resp.json()["status"] == "archived"

    async def test_delete_list(self, ctx):
        pid = await _new_project(ctx, "alpha")
        created = await ctx.client.post(
            f"/api/projects/{pid}/lists", json={"title": "Gone", "description": ""}
        )
        lst_id = created.json()["id"]
        resp = await ctx.client.delete(f"/api/projects/{pid}/lists/{lst_id}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_add_list_entry(self, ctx):
        pid = await _new_project(ctx, "alpha")
        created = await ctx.client.post(
            f"/api/projects/{pid}/lists", json={"title": "A", "description": ""}
        )
        lst_id = created.json()["id"]
        resp = await ctx.client.post(
            f"/api/projects/{pid}/lists/{lst_id}/entries",
            json={"text": "Buy milk", "position": 0},
        )
        assert resp.status_code == 200, resp.text
        entry = resp.json()
        assert entry["text"] == "Buy milk"
        assert entry["position"] == 0

    async def test_list_entries(self, ctx):
        pid = await _new_project(ctx, "alpha")
        created = await ctx.client.post(
            f"/api/projects/{pid}/lists", json={"title": "A", "description": ""}
        )
        lst_id = created.json()["id"]
        await ctx.client.post(
            f"/api/projects/{pid}/lists/{lst_id}/entries",
            json={"text": "A", "position": 0},
        )
        await ctx.client.post(
            f"/api/projects/{pid}/lists/{lst_id}/entries",
            json={"text": "B", "position": 1},
        )
        resp = await ctx.client.get(f"/api/projects/{pid}/lists/{lst_id}/entries")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2

    async def test_update_entry(self, ctx):
        pid = await _new_project(ctx, "alpha")
        created = await ctx.client.post(
            f"/api/projects/{pid}/lists", json={"title": "A", "description": ""}
        )
        lst_id = created.json()["id"]
        entry = await ctx.client.post(
            f"/api/projects/{pid}/lists/{lst_id}/entries",
            json={"text": "A", "position": 0},
        )
        entry_id = entry.json()["id"]
        resp = await ctx.client.patch(
            f"/api/projects/{pid}/lists/{lst_id}/entries/{entry_id}",
            json={"done": True, "text": "done"},
        )
        assert resp.status_code == 200
        assert resp.json()["done"] == 1
        assert resp.json()["text"] == "done"

    async def test_delete_entry(self, ctx):
        pid = await _new_project(ctx, "alpha")
        created = await ctx.client.post(
            f"/api/projects/{pid}/lists", json={"title": "A", "description": ""}
        )
        lst_id = created.json()["id"]
        entry = await ctx.client.post(
            f"/api/projects/{pid}/lists/{lst_id}/entries",
            json={"text": "A", "position": 0},
        )
        entry_id = entry.json()["id"]
        resp = await ctx.client.delete(
            f"/api/projects/{pid}/lists/{lst_id}/entries/{entry_id}"
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_reorder_entries(self, ctx):
        pid = await _new_project(ctx, "alpha")
        created = await ctx.client.post(
            f"/api/projects/{pid}/lists", json={"title": "A", "description": ""}
        )
        lst_id = created.json()["id"]
        e1 = await ctx.client.post(
            f"/api/projects/{pid}/lists/{lst_id}/entries",
            json={"text": "A", "position": 0},
        )
        e2 = await ctx.client.post(
            f"/api/projects/{pid}/lists/{lst_id}/entries",
            json={"text": "B", "position": 1},
        )
        resp = await ctx.client.post(
            f"/api/projects/{pid}/lists/{lst_id}/entries/reorder",
            json={
                "entries": [
                    {"id": e2.json()["id"], "position": 0},
                    {"id": e1.json()["id"], "position": 1},
                ]
            },
        )
        assert resp.status_code == 200
        items = await ctx.client.get(
            f"/api/projects/{pid}/lists/{lst_id}/entries"
        )
        texts = [e["text"] for e in items.json()["items"]]
        assert texts == ["B", "A"]


@pytest.mark.asyncio
class TestAgentScopeGating:
    async def test_agent_with_scope_can_crud_lists(self, ctx):
        pid = await _new_project(ctx, "alpha")
        cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/lists",
                json={"title": "A", "description": ""},
                headers=_hdr(token),
            )
            assert resp.status_code == 200
            lst_id = resp.json()["id"]

            resp = await bare.get(f"/api/projects/{pid}/lists", headers=_hdr(token))
            assert resp.status_code == 200

            resp = await bare.get(
                f"/api/projects/{pid}/lists/{lst_id}", headers=_hdr(token)
            )
            assert resp.status_code == 200

            resp = await bare.patch(
                f"/api/projects/{pid}/lists/{lst_id}",
                json={"title": "B"},
                headers=_hdr(token),
            )
            assert resp.status_code == 200

            resp = await bare.delete(
                f"/api/projects/{pid}/lists/{lst_id}", headers=_hdr(token)
            )
            assert resp.status_code == 200

    async def test_agent_with_scope_can_crud_entries(self, ctx):
        pid = await _new_project(ctx, "alpha")
        cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/lists",
                json={"title": "A", "description": ""},
                headers=_hdr(token),
            )
            assert resp.status_code == 200
            lst_id = resp.json()["id"]

            resp = await bare.post(
                f"/api/projects/{pid}/lists/{lst_id}/entries",
                json={"text": "milk", "position": 0},
                headers=_hdr(token),
            )
            assert resp.status_code == 200
            entry_id = resp.json()["id"]

            resp = await bare.get(
                f"/api/projects/{pid}/lists/{lst_id}/entries", headers=_hdr(token)
            )
            assert resp.status_code == 200

            resp = await bare.patch(
                f"/api/projects/{pid}/lists/{lst_id}/entries/{entry_id}",
                json={"done": True},
                headers=_hdr(token),
            )
            assert resp.status_code == 200

            resp = await bare.delete(
                f"/api/projects/{pid}/lists/{lst_id}/entries/{entry_id}",
                headers=_hdr(token),
            )
            assert resp.status_code == 200

    async def test_agent_wrong_project_is_404(self, ctx):
        pid_a = await _new_project(ctx, "alpha")
        pid_b = await _new_project(ctx, "beta")
        _, token_a = await _mint_agent(ctx, pid_a)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid_b}/lists", headers=_hdr(token_a)
            )
            assert resp.status_code == 404

    async def test_agent_missing_scope_is_403(self, ctx):
        pid = await _new_project(ctx, "alpha")
        _, token = await _mint_agent(ctx, pid, scopes=("a2a_receive",))
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/lists", headers=_hdr(token)
            )
        assert resp.status_code == 403, resp.text

    async def test_unauthenticated_is_401(self, ctx):
        pid = await _new_project(ctx, "alpha")
        async with _bare(ctx.app) as bare:
            resp = await bare.get(f"/api/projects/{pid}/lists")
        assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
class TestReorderContract:
    """A failed reorder must be reported as failed, and a malformed reorder body
    must be rejected by validation rather than crashing the store."""

    async def test_failed_reorder_logs_no_activity(self, ctx):
        pid = await _new_project(ctx, "alpha")
        created = await ctx.client.post(
            f"/api/projects/{pid}/lists", json={"title": "A", "description": ""}
        )
        lst_id = created.json()["id"]
        resp = await ctx.client.post(
            f"/api/projects/{pid}/lists/{lst_id}/entries/reorder",
            json={"entries": [{"id": "ent-nope", "position": 0}]},
        )
        assert resp.status_code == 400, resp.text
        activity = await ctx.app.state.project_store.list_activity(pid)
        kinds = [a["kind"] for a in activity]
        assert "entry.reordered" not in kinds, kinds

    async def test_malformed_reorder_entry_is_422(self, ctx):
        pid = await _new_project(ctx, "alpha")
        created = await ctx.client.post(
            f"/api/projects/{pid}/lists", json={"title": "A", "description": ""}
        )
        lst_id = created.json()["id"]
        resp = await ctx.client.post(
            f"/api/projects/{pid}/lists/{lst_id}/entries/reorder",
            json={"entries": [{"id": "ent-1"}]},
        )
        assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
class TestSessionNonOwner:
    async def test_session_non_owner_cannot_tell_the_project_exists(self, ctx):
        """A signed-in NON-ADMIN who does not own the project gets the same 404
        as for a project that does not exist.

        `_get_owned_project` decides this by comparing ids, so no second user
        record is needed (and `setup_user` refuses one once a user exists). The
        route is called directly because the only session the test client can
        present is the owner's.
        """
        from tinyagentos.routes.project_lists import list_lists

        pid = await _new_project(ctx, "alpha")
        intruder = SimpleNamespace(
            app=ctx.app,
            state=SimpleNamespace(user_id="not-the-owner", is_admin=False),
        )

        resp = await list_lists(pid, intruder)

        assert resp.status_code == 404
        assert b"not found" in bytes(resp.body)

    async def test_owner_still_reaches_the_route(self, ctx):
        """Guard against the assertion above passing for the wrong reason: the
        same call shape with the OWNER's id must succeed."""
        from tinyagentos.routes.project_lists import list_lists

        pid = await _new_project(ctx, "alpha")
        owner = SimpleNamespace(
            app=ctx.app,
            state=SimpleNamespace(user_id=ctx.uid, is_admin=False),
        )

        result = await list_lists(pid, owner)

        assert result == {"items": []}
