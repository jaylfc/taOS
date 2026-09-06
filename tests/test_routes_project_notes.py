"""Project notes routes: session owner/admin and project-bound agent JWT.

Pin the dual-auth contract for /api/projects/{id}/notes (GET/POST/PATCH/DELETE):

  1. A session owner/admin can CRUD notes (regression on the projects
     ownership pattern).
  2. An approved agent token (scope project_notes) bound to project A can
     read + author + edit + delete notes ONLY for project A; a token bound
     elsewhere collapses into an existence-hiding 404.
  3. A token missing the project_notes scope is rejected 403; an
     unauthenticated (no session, no token) request is 401.

The store (tinyagentos/projects/notes_store.py) is exercised directly in
test_project_notes_store.py; these tests pin the HTTP auth + project scoping.
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


async def _mint_agent(ctx, project_id, scopes=("project_notes",)):
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
    async def test_create_list_update_delete(self, ctx):
        pid = await _new_project(ctx, "alpha")
        created = await ctx.client.post(
            f"/api/projects/{pid}/notes", json={"title": "Idea", "body": "thought"}
        )
        assert created.status_code == 200, created.text
        note = created.json()
        assert note["title"] == "Idea"
        assert note["body"] == "thought"
        assert note["author_kind"] == "user"

        listed = await ctx.client.get(f"/api/projects/{pid}/notes")
        assert listed.status_code == 200
        assert any(n["id"] == note["id"] for n in listed.json()["items"])

        patched = await ctx.client.patch(
            f"/api/projects/{pid}/notes/{note['id']}", json={"title": "Renamed"}
        )
        assert patched.status_code == 200
        assert patched.json()["title"] == "Renamed"
        # Body preserved on partial update.
        assert patched.json()["body"] == "thought"

        deleted = await ctx.client.delete(f"/api/projects/{pid}/notes/{note['id']}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["ok"] is True

        after = await ctx.client.get(f"/api/projects/{pid}/notes")
        assert not any(n["id"] == note["id"] for n in after.json()["items"])

    async def test_list_newest_first(self, ctx):
        pid = await _new_project(ctx, "alpha")
        a = await ctx.client.post(
            f"/api/projects/{pid}/notes", json={"title": "A", "body": ""}
        )
        b = await ctx.client.post(
            f"/api/projects/{pid}/notes", json={"title": "B", "body": ""}
        )
        listed = await ctx.client.get(f"/api/projects/{pid}/notes")
        ids = [n["id"] for n in listed.json()["items"]]
        assert ids == [b.json()["id"], a.json()["id"]]

    async def test_update_unknown_note_is_404(self, ctx):
        pid = await _new_project(ctx, "alpha")
        resp = await ctx.client.patch(
            f"/api/projects/{pid}/notes/note-missing", json={"title": "x"}
        )
        assert resp.status_code == 404

    async def test_delete_unknown_note_is_404(self, ctx):
        pid = await _new_project(ctx, "alpha")
        resp = await ctx.client.delete(f"/api/projects/{pid}/notes/note-missing")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestAgentScopeGating:
    async def test_agent_can_read_and_write_own_project(self, ctx):
        pid = await _new_project(ctx, "alpha")
        # Session owner seeds a note so the agent has something to read.
        seeded = await ctx.client.post(
            f"/api/projects/{pid}/notes", json={"title": "seed", "body": "seed"}
        )
        seed_id = seeded.json()["id"]

        cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            listed = await bare.get(f"/api/projects/{pid}/notes", headers=_hdr(token))
            assert listed.status_code == 200
            ids = [n["id"] for n in listed.json()["items"]]
            assert seed_id in ids

            post = await bare.post(
                f"/api/projects/{pid}/notes",
                json={"title": "agent idea", "body": "posted by agent"},
                headers=_hdr(token),
            )
        assert post.status_code == 200, post.text
        note = post.json()
        assert note["author_id"] == cid
        assert note["author_kind"] == "agent"

    async def test_agent_patch_succeeds(self, ctx):
        pid = await _new_project(ctx, "alpha")
        created = await ctx.client.post(
            f"/api/projects/{pid}/notes", json={"title": "orig", "body": "b"}
        )
        note_id = created.json()["id"]
        cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.patch(
                f"/api/projects/{pid}/notes/{note_id}",
                json={"title": "agent-edited"},
                headers=_hdr(token),
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["title"] == "agent-edited"

    async def test_agent_delete_succeeds(self, ctx):
        pid = await _new_project(ctx, "alpha")
        created = await ctx.client.post(
            f"/api/projects/{pid}/notes", json={"title": "todelete", "body": "b"}
        )
        note_id = created.json()["id"]
        cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.delete(
                f"/api/projects/{pid}/notes/{note_id}", headers=_hdr(token)
            )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_agent_note_persists(self, ctx):
        pid = await _new_project(ctx, "alpha")
        cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            await bare.post(
                f"/api/projects/{pid}/notes",
                json={"title": "persist", "body": "kept"},
                headers=_hdr(token),
            )
            listed = await bare.get(f"/api/projects/{pid}/notes", headers=_hdr(token))
        assert listed.status_code == 200
        titles = [n["title"] for n in listed.json()["items"]]
        assert "persist" in titles
        assert listed.json()["items"][0]["author_id"] == cid

    async def test_agent_other_project_is_404(self, ctx):
        pid_a = await _new_project(ctx, "alpha")
        pid_b = await _new_project(ctx, "bravo")
        _cid, token_a = await _mint_agent(ctx, pid_a)
        async with _bare(ctx.app) as bare:
            listing = await bare.get(f"/api/projects/{pid_b}/notes", headers=_hdr(token_a))
            create = await bare.post(
                f"/api/projects/{pid_b}/notes",
                json={"title": "x", "body": ""},
                headers=_hdr(token_a),
            )
            patch = await bare.patch(
                f"/api/projects/{pid_b}/notes/note-x",
                json={"title": "y"},
                headers=_hdr(token_a),
            )
            delete = await bare.delete(
                f"/api/projects/{pid_b}/notes/note-x", headers=_hdr(token_a)
            )
        # Existence-hiding 404 for every method: indistinguishable from a
        # non-owner session on a missing project.
        assert listing.status_code == 404
        assert create.status_code == 404
        assert patch.status_code == 404
        assert delete.status_code == 404

    async def test_agent_missing_scope_is_403(self, ctx):
        pid = await _new_project(ctx, "alpha")
        _cid, token = await _mint_agent(ctx, pid, scopes=("a2a_receive",))
        async with _bare(ctx.app) as bare:
            resp = await bare.get(f"/api/projects/{pid}/notes", headers=_hdr(token))
        assert resp.status_code == 403, resp.text

    async def test_unauthenticated_is_401(self, ctx):
        pid = await _new_project(ctx, "alpha")
        async with _bare(ctx.app) as bare:
            resp = await bare.get(f"/api/projects/{pid}/notes")
        assert resp.status_code == 401, resp.text

    async def test_agent_author_attributed_to_token(self, ctx):
        """An agent creating a note is always attributed to its own canonical id;
        the route does not accept a client-supplied author_id."""
        pid = await _new_project(ctx, "alpha")
        cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/notes",
                json={"title": "idea", "body": "b"},
                headers=_hdr(token),
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["author_id"] == cid
