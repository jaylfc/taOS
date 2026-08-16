"""Route-level tests for the task checklist endpoints.

The PR that added `POST`/`GET
/api/projects/{project_id}/tasks/{task_id}/checklist-items` shipped store-level
tests only, so the ROUTE surface (scope split, existence-hiding 404, request
shape, archive filtering) was unverified. These pin it.

The scope split is the security-relevant part and is asserted in the REFUSING
direction: `project_tasks` is documented and tested as read + lifecycle +
comments, so it must NOT be able to author a checklist item. Authoring needs the
narrower `project_tasks_create`, the same grant task creation uses.
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
    """Cookieless client so requests carry only the Bearer header."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


async def _new_project(ctx, slug):
    resp = await ctx.client.post("/api/projects", json={"name": slug, "slug": slug})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _new_task(ctx, pid, title="T"):
    resp = await ctx.client.post(f"/api/projects/{pid}/tasks", json={"title": title})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _mint_agent(ctx, project_id, scopes, handle="@grok"):
    registry = ctx.app.state.agent_registry
    grants = ctx.app.state.agent_grants
    priv, _pub = ctx.app.state.agent_registry_keypair
    rec = await registry.register(
        framework="grok",
        display_name="Grok",
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


def _url(pid, tid):
    return f"/api/projects/{pid}/tasks/{tid}/checklist-items"


_ALLOWLIST_GAP = (
    "agent tokens cannot reach the checklist routes at all: auth_middleware's "
    "Bearer allowlist has no checklist pattern, so the request is refused 401 "
    "before any scope check. The routes nevertheless call "
    "_authorize_task_actor(..., scope='project_tasks_create') and their "
    "docstrings advertise agent access, so that code is currently unreachable. "
    "strict=True on purpose: when the allowlist gains the patterns these turn "
    "XPASS and fail, forcing this note and the docs to be updated."
)


@pytest.mark.asyncio
class TestRequestShape:
    async def test_create_takes_a_json_body(self, ctx):
        """The item text arrives in a JSON body, matching POST
        /api/projects/{id}/tasks beside it, not as a query parameter."""
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        resp = await ctx.client.post(_url(pid, tid), json={"text": "step one"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["text"] == "step one"

    async def test_create_without_text_is_422(self, ctx):
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        resp = await ctx.client.post(_url(pid, tid), json={})
        assert resp.status_code == 422

    async def test_created_item_is_listed(self, ctx):
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        await ctx.client.post(_url(pid, tid), json={"text": "step one"})
        resp = await ctx.client.get(_url(pid, tid))
        assert resp.status_code == 200, resp.text
        items = resp.json()
        rows = items["items"] if isinstance(items, dict) else items
        assert [r["text"] for r in rows] == ["step one"]


@pytest.mark.asyncio
class TestScopeSplit:
    @pytest.mark.xfail(strict=True, reason=_ALLOWLIST_GAP)
    async def test_project_tasks_create_may_author(self, ctx):
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        _cid, token = await _mint_agent(ctx, pid, scopes=("project_tasks_create",))
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                _url(pid, tid), json={"text": "agent step"}, headers=_hdr(token)
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["text"] == "agent step"

    async def test_project_tasks_alone_may_NOT_author(self, ctx):
        """The refusing direction: read+lifecycle scope must not author.

        NOTE this currently passes on 401 from the allowlist, NOT on 403 from
        the scope check, so today it does not actually prove the scope split.
        See _ALLOWLIST_GAP. It becomes a real scope assertion once agent tokens
        can reach these routes.
        """
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        _cid, token = await _mint_agent(ctx, pid, scopes=("project_tasks",))
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                _url(pid, tid), json={"text": "nope"}, headers=_hdr(token)
            )
        assert resp.status_code in (401, 403), resp.text

    @pytest.mark.xfail(strict=True, reason=_ALLOWLIST_GAP)
    async def test_project_tasks_may_read(self, ctx):
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        await ctx.client.post(_url(pid, tid), json={"text": "step one"})
        _cid, token = await _mint_agent(ctx, pid, scopes=("project_tasks",))
        async with _bare(ctx.app) as bare:
            resp = await bare.get(_url(pid, tid), headers=_hdr(token))
        assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
class TestCrossProjectIsolation:
    async def test_task_from_another_project_is_404(self, ctx):
        """A task id that exists but belongs to a different project is
        existence-hiding 404, not 403."""
        pid_a = await _new_project(ctx, "alpha")
        pid_b = await _new_project(ctx, "beta")
        tid_b = await _new_task(ctx, pid_b, title="in beta")
        resp = await ctx.client.post(
            _url(pid_a, tid_b), json={"text": "leak"}
        )
        assert resp.status_code == 404, resp.text
        resp = await ctx.client.get(_url(pid_a, tid_b))
        assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
class TestArchiveFiltering:
    async def test_archived_items_hidden_unless_requested(self, ctx):
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        created = await ctx.client.post(_url(pid, tid), json={"text": "done step"})
        item_id = created.json()["id"]
        await ctx.client.post(_url(pid, tid), json={"text": "live step"})

        store = ctx.app.state.project_task_store
        # archive_checklist_item refuses unless the item is verified AND
        # reported, so satisfy that first rather than asserting on a refusal.
        await store.update_checklist_item(item_id, verified=True, reported=True)
        await store.archive_checklist_item(item_id, reported_by=ctx.uid)

        resp = await ctx.client.get(_url(pid, tid))
        rows = resp.json()
        rows = rows["items"] if isinstance(rows, dict) else rows
        assert [r["text"] for r in rows] == ["live step"]

        resp = await ctx.client.get(_url(pid, tid), params={"include_archived": "true"})
        rows = resp.json()
        rows = rows["items"] if isinstance(rows, dict) else rows
        assert {r["text"] for r in rows} == {"done step", "live step"}
