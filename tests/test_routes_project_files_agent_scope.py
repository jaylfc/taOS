"""Agent scope gating for project-files routes (tinyagentos/routes/project_files.py).

Mirrors the canvas agent-scope tests: an approved agent reaches a project's
files only with a ``files_read`` / ``files_write`` grant bound to THAT project.
A token bound to a different project, or missing the scope, collapses into an
existence-hiding 404. Session owner behavior is unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token


@pytest_asyncio.fixture
async def ctx(client):
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
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


async def _new_project(ctx, slug):
    resp = await ctx.client.post("/api/projects", json={"name": slug, "slug": slug})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _mint_agent(ctx, project_id, scopes, *, handle="@filer"):
    registry = ctx.app.state.agent_registry
    grants = ctx.app.state.agent_grants
    priv, _pub = ctx.app.state.agent_registry_keypair
    rec = await registry.register(
        framework="grok",
        display_name="Filer",
        origin="external-selfjoin",
        handle=handle,
    )
    cid = rec["canonical_id"]
    await registry.set_status(cid, "active")
    for scope in scopes:
        await grants.add_grant(cid, scope, project_id=project_id)
    token = mint_registry_token(
        cid, priv, user_id="u", framework="grok", project_id=project_id
    )
    return cid, token


@pytest.mark.asyncio
class TestAgentFilesRead:
    async def test_list_allowed_with_files_read(self, ctx):
        pid = await _new_project(ctx, "freadok")
        _cid, token = await _mint_agent(ctx, pid, ("files_read",))
        async with _bare(ctx.app) as bare:
            resp = await bare.get("/api/projects/freadok/files", headers=_hdr(token))
        assert resp.status_code == 200, resp.text

    async def test_list_without_read_scope_is_403(self, ctx):
        pid = await _new_project(ctx, "fnogrant")
        # files_write only: the files_read scope is entirely absent, so the
        # scope check fails before project binding -> 403 (mirrors canvas).
        _cid, token = await _mint_agent(ctx, pid, ("files_write",))
        async with _bare(ctx.app) as bare:
            resp = await bare.get("/api/projects/fnogrant/files", headers=_hdr(token))
        assert resp.status_code == 403
        assert resp.status_code != 200

    async def test_stats_allowed_with_files_read(self, ctx):
        pid = await _new_project(ctx, "fstats")
        _cid, token = await _mint_agent(ctx, pid, ("files_read",))
        async with _bare(ctx.app) as bare:
            resp = await bare.get("/api/projects/fstats/stats", headers=_hdr(token))
        assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
class TestAgentFilesWrite:
    async def test_upload_allowed_with_files_write(self, ctx):
        pid = await _new_project(ctx, "fwriteok")
        _cid, token = await _mint_agent(ctx, pid, ("files_write",))
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                "/api/projects/fwriteok/files/upload",
                files={"file": ("note.md", b"# hello", "text/markdown")},
                headers=_hdr(token),
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "uploaded"

    async def test_upload_without_write_scope_is_403(self, ctx):
        pid = await _new_project(ctx, "freadonly")
        # files_read only: the files_write scope is entirely absent -> 403.
        _cid, token = await _mint_agent(ctx, pid, ("files_read",))
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                "/api/projects/freadonly/files/upload",
                files={"file": ("note.md", b"x", "text/markdown")},
                headers=_hdr(token),
            )
        assert resp.status_code == 403
        assert resp.status_code != 200

    async def test_mkdir_allowed_with_files_write(self, ctx):
        pid = await _new_project(ctx, "fmkdir")
        _cid, token = await _mint_agent(ctx, pid, ("files_write",))
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                "/api/projects/fmkdir/mkdir",
                json={"path": "Reports"},
                headers=_hdr(token),
            )
        assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
class TestAgentFilesProjectBinding:
    async def test_token_for_other_project_is_404(self, ctx):
        pid_a = await _new_project(ctx, "proja")
        await _new_project(ctx, "projb")
        # Grant bound to project A only; attempt to read project B.
        _cid, token = await _mint_agent(ctx, pid_a, ("files_read",))
        async with _bare(ctx.app) as bare:
            resp = await bare.get("/api/projects/projb/files", headers=_hdr(token))
        assert resp.status_code == 404
        assert resp.status_code != 200

    async def test_unknown_slug_is_404(self, ctx):
        pid = await _new_project(ctx, "realproj")
        _cid, token = await _mint_agent(ctx, pid, ("files_read",))
        async with _bare(ctx.app) as bare:
            resp = await bare.get("/api/projects/nosuchproj/files", headers=_hdr(token))
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestSessionFilesUnchanged:
    async def test_owner_session_list_allowed(self, ctx):
        await _new_project(ctx, "ownerfiles")
        resp = await ctx.client.get("/api/projects/ownerfiles/files")
        assert resp.status_code == 200, resp.text

    async def test_owner_session_upload_allowed(self, ctx):
        await _new_project(ctx, "ownerupload")
        resp = await ctx.client.post(
            "/api/projects/ownerupload/files/upload",
            files={"file": ("a.txt", b"data", "text/plain")},
        )
        assert resp.status_code == 200, resp.text
