"""Agent-token access to the project kanban board (project_tasks scope).

An APPROVED external agent drives ONLY its own project's task board with its
Ed25519 registry JWT (scope project_tasks), no session cookie. These tests pin
the five non-negotiable security invariants:

  1. A token touches task routes ONLY for its own project_id claim; a different
     project is an existence-hiding 404 (indistinguishable from a non-owner).
  2. project_tasks grants task read + lifecycle + comments ONLY, never
     create-task / members / project lifecycle.
  3. The verified token's canonical_id is the authoritative actor; a lifecycle
     body actor id that differs is rejected 403.
  4. Session owner/admin behavior is unchanged (regression).
  5. The token is not a skeleton key: it authenticates no route off the
     allowlist.
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


async def _new_task(ctx, pid, title="T"):
    resp = await ctx.client.post(
        f"/api/projects/{pid}/tasks", json={"title": title}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _mint_agent(ctx, project_id, scopes=("project_tasks",)):
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
class TestAgentCanDriveOwnBoard:
    async def test_list_tasks(self, ctx):
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        _cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(f"/api/projects/{pid}/tasks", headers=_hdr(token))
        assert resp.status_code == 200
        assert any(t["id"] == tid for t in resp.json()["items"])

    async def test_ready_tasks(self, ctx):
        pid = await _new_project(ctx, "alpha")
        await _new_task(ctx, pid)
        _cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/tasks/ready", headers=_hdr(token)
            )
        assert resp.status_code == 200

    async def test_get_task(self, ctx):
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        _cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/tasks/{tid}", headers=_hdr(token)
            )
        assert resp.status_code == 200
        assert resp.json()["id"] == tid

    async def test_patch_task_is_session_only(self, ctx):
        """PATCH free-mutates task fields (title/assignee_id/parent), broader than
        the project_tasks scope, so it is NOT on the agent allowlist: an agent
        token is rejected. Lifecycle is driven via claim/close/reopen instead."""
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        _cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.patch(
                f"/api/projects/{pid}/tasks/{tid}",
                json={"priority": 5},
                headers=_hdr(token),
            )
        assert resp.status_code in (401, 403, 404)

    async def test_claim_own_task_as_self(self, ctx):
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/tasks/{tid}/claim",
                json={"claimer_id": cid},
                headers=_hdr(token),
            )
        assert resp.status_code == 200
        assert resp.json()["claimed_by"] == cid

    async def test_close_own_task(self, ctx):
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/tasks/{tid}/close",
                json={"closed_by": cid},
                headers=_hdr(token),
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "closed"

    async def test_comments_read_and_post(self, ctx):
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            post = await bare.post(
                f"/api/projects/{pid}/tasks/{tid}/comments",
                json={"body": "on it", "author_id": cid},
                headers=_hdr(token),
            )
            assert post.status_code == 200, post.text
            listed = await bare.get(
                f"/api/projects/{pid}/tasks/{tid}/comments", headers=_hdr(token)
            )
        assert listed.status_code == 200
        assert len(listed.json()["items"]) == 1


@pytest.mark.asyncio
class TestProjectScoping:
    async def test_different_project_is_404(self, ctx):
        """Invariant 1: a token bound to project A must not reach project B; the
        existence-hiding 404 is identical to a non-owner session's response."""
        pid_a = await _new_project(ctx, "alpha")
        pid_b = await _new_project(ctx, "bravo")
        tid_b = await _new_task(ctx, pid_b)
        _cid, token_a = await _mint_agent(ctx, pid_a)
        async with _bare(ctx.app) as bare:
            listing = await bare.get(
                f"/api/projects/{pid_b}/tasks", headers=_hdr(token_a)
            )
            single = await bare.get(
                f"/api/projects/{pid_b}/tasks/{tid_b}", headers=_hdr(token_a)
            )
        assert listing.status_code == 404
        assert single.status_code == 404

    async def test_context_route_respects_project_binding(self, ctx):
        """The project-agnostic context path resolves the project from the task;
        a token bound elsewhere still gets a 404."""
        pid_a = await _new_project(ctx, "alpha")
        pid_b = await _new_project(ctx, "bravo")
        tid_b = await _new_task(ctx, pid_b)
        _cid, token_a = await _mint_agent(ctx, pid_a)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/tasks/{tid_b}/context", headers=_hdr(token_a)
            )
        assert resp.status_code == 404

    async def test_missing_scope_is_403(self, ctx):
        pid = await _new_project(ctx, "alpha")
        await _new_task(ctx, pid)
        # Agent with a project-bound grant but NOT project_tasks.
        _cid, token = await _mint_agent(ctx, pid, scopes=("a2a_receive",))
        async with _bare(ctx.app) as bare:
            resp = await bare.get(f"/api/projects/{pid}/tasks", headers=_hdr(token))
        assert resp.status_code == 403


@pytest.mark.asyncio
class TestActorBinding:
    async def test_claim_as_someone_else_is_403(self, ctx):
        """Invariant 3: a claim body naming a different actor is rejected."""
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/tasks/{tid}/claim",
                json={"claimer_id": "some-other-agent"},
                headers=_hdr(token),
            )
        assert resp.status_code == 403
        assert cid != "some-other-agent"

    async def test_close_as_someone_else_is_403(self, ctx):
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        _cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/tasks/{tid}/close",
                json={"closed_by": "not-me"},
                headers=_hdr(token),
            )
        assert resp.status_code == 403

    async def test_comment_as_someone_else_is_403(self, ctx):
        """Invariant 3: a comment author is an actor id; an agent may not post a
        comment authored as another identity."""
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/tasks/{tid}/comments",
                json={"body": "spoofed", "author_id": "some-other-agent"},
                headers=_hdr(token),
            )
        assert resp.status_code == 403
        assert cid != "some-other-agent"

    async def test_comment_as_self_succeeds_and_stores_token_id(self, ctx):
        """The mirror of the 403 case: an agent commenting as its own canonical
        id is accepted and the stored author is that id."""
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/tasks/{tid}/comments",
                json={"body": "on it", "author_id": cid},
                headers=_hdr(token),
            )
        assert resp.status_code == 200
        assert resp.json()["author_id"] == cid

    async def test_comment_author_pinned_to_token_when_omitted(self, ctx):
        """An agent may omit author_id; the route pins the comment to its own
        canonical id rather than storing a null or caller-supplied author."""
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/tasks/{tid}/comments",
                json={"body": "on it"},
                headers=_hdr(token),
            )
        assert resp.status_code == 200
        assert resp.json()["author_id"] == cid


@pytest.mark.asyncio
class TestExcludedRoutes:
    """Invariant 2 + 5: project_tasks must NOT reach create-task, members, or
    project-lifecycle routes; the token authenticates nothing off the allowlist."""

    async def test_cannot_create_task(self, ctx):
        pid = await _new_project(ctx, "alpha")
        _cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/tasks",
                json={"title": "sneaky"},
                headers=_hdr(token),
            )
        assert resp.status_code in (401, 403, 404)

    async def test_cannot_add_member(self, ctx):
        pid = await _new_project(ctx, "alpha")
        _cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/members",
                json={"mode": "native", "agent_id": "x"},
                headers=_hdr(token),
            )
        assert resp.status_code in (401, 403, 404)

    async def test_cannot_patch_project(self, ctx):
        pid = await _new_project(ctx, "alpha")
        _cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.patch(
                f"/api/projects/{pid}", json={"name": "hijack"}, headers=_hdr(token)
            )
        assert resp.status_code in (401, 403, 404)

    async def test_cannot_delete_project(self, ctx):
        pid = await _new_project(ctx, "alpha")
        _cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.delete(f"/api/projects/{pid}", headers=_hdr(token))
        assert resp.status_code in (401, 403, 404)

    async def test_cannot_list_all_projects(self, ctx):
        pid = await _new_project(ctx, "alpha")
        _cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.get("/api/projects", headers=_hdr(token))
        assert resp.status_code == 401

    async def test_cannot_read_task_audit(self, ctx):
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        _cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/tasks/{tid}/audit", headers=_hdr(token)
            )
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestSessionRegression:
    """Invariant 4: the admin session path is unchanged by the dual-auth gate."""

    async def test_admin_lists_tasks(self, ctx):
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        resp = await ctx.client.get(f"/api/projects/{pid}/tasks")
        assert resp.status_code == 200
        assert any(t["id"] == tid for t in resp.json()["items"])

    async def test_admin_claims_and_closes_as_any_actor(self, ctx):
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        # A session admin may still act on behalf of any worker id (unchanged).
        claim = await ctx.client.post(
            f"/api/projects/{pid}/tasks/{tid}/claim",
            json={"claimer_id": "worker-7"},
        )
        assert claim.status_code == 200
        assert claim.json()["claimed_by"] == "worker-7"
        close = await ctx.client.post(
            f"/api/projects/{pid}/tasks/{tid}/close",
            json={"closed_by": "worker-7"},
        )
        assert close.status_code == 200
        assert close.json()["status"] == "closed"

    async def test_unauthenticated_still_401(self, ctx):
        pid = await _new_project(ctx, "alpha")
        async with _bare(ctx.app) as bare:
            resp = await bare.get(f"/api/projects/{pid}/tasks")
        assert resp.status_code == 401
