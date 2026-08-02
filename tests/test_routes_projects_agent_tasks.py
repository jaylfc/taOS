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

PATCH field mutation (title/body/labels/priority) is gated by the NARROWER
project_tasks_update scope, not the read+lifecycle project_tasks scope (tsk-b6ugu5).
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

    async def test_lead_agent_patch_body_succeeds(self, ctx):
        """An agent holding project_tasks_update may PATCH a card body on a board
        it leads -> 200 and the change is reflected in the response."""
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        cid, token = await _mint_agent(ctx, pid, scopes=("project_tasks_update",))
        await ctx.app.state.project_store.add_member(pid, cid, "native")
        await ctx.app.state.project_store.set_lead(pid, cid)
        async with _bare(ctx.app) as bare:
            resp = await bare.patch(
                f"/api/projects/{pid}/tasks/{tid}",
                json={"body": "revised body"},
                headers=_hdr(token),
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["body"] == "revised body"

    async def test_lead_agent_patch_body_persists(self, ctx):
        """The patched body survives a fresh read."""
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        cid, token = await _mint_agent(
            ctx, pid, scopes=("project_tasks", "project_tasks_update")
        )
        await ctx.app.state.project_store.add_member(pid, cid, "native")
        await ctx.app.state.project_store.set_lead(pid, cid)
        async with _bare(ctx.app) as bare:
            await bare.patch(
                f"/api/projects/{pid}/tasks/{tid}",
                json={"body": "persisted body"},
                headers=_hdr(token),
            )
            resp = await bare.get(
                f"/api/projects/{pid}/tasks/{tid}", headers=_hdr(token)
            )
        assert resp.status_code == 200
        assert resp.json()["body"] == "persisted body"

    async def test_lead_agent_patch_priority_succeeds(self, ctx):
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        cid, token = await _mint_agent(ctx, pid, scopes=("project_tasks_update",))
        await ctx.app.state.project_store.add_member(pid, cid, "native")
        await ctx.app.state.project_store.set_lead(pid, cid)
        async with _bare(ctx.app) as bare:
            resp = await bare.patch(
                f"/api/projects/{pid}/tasks/{tid}",
                json={"priority": 7},
                headers=_hdr(token),
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["priority"] == 7

    async def test_lead_agent_patch_labels_succeeds(self, ctx):
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        cid, token = await _mint_agent(ctx, pid, scopes=("project_tasks_update",))
        await ctx.app.state.project_store.add_member(pid, cid, "native")
        await ctx.app.state.project_store.set_lead(pid, cid)
        async with _bare(ctx.app) as bare:
            resp = await bare.patch(
                f"/api/projects/{pid}/tasks/{tid}",
                json={"labels": ["bug", "urgent"]},
                headers=_hdr(token),
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["labels"] == ["bug", "urgent"]

    async def test_lead_agent_patch_title_succeeds(self, ctx):
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        cid, token = await _mint_agent(ctx, pid, scopes=("project_tasks_update",))
        await ctx.app.state.project_store.add_member(pid, cid, "native")
        await ctx.app.state.project_store.set_lead(pid, cid)
        async with _bare(ctx.app) as bare:
            resp = await bare.patch(
                f"/api/projects/{pid}/tasks/{tid}",
                json={"title": "renamed by agent"},
                headers=_hdr(token),
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["title"] == "renamed by agent"

    async def test_agent_patch_assignee_id_rejected(self, ctx):
        """assignee_id stays human-only: an agent PATCH of it -> 403."""
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        cid, token = await _mint_agent(ctx, pid, scopes=("project_tasks_update",))
        await ctx.app.state.project_store.add_member(pid, cid, "native")
        await ctx.app.state.project_store.set_lead(pid, cid)
        async with _bare(ctx.app) as bare:
            resp = await bare.patch(
                f"/api/projects/{pid}/tasks/{tid}",
                json={"assignee_id": "someone-else"},
                headers=_hdr(token),
            )
        assert resp.status_code == 403

    async def test_agent_patch_parent_task_id_rejected(self, ctx):
        """parent_task_id stays human-only: an agent PATCH of it -> 403."""
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        cid, token = await _mint_agent(ctx, pid, scopes=("project_tasks_update",))
        await ctx.app.state.project_store.add_member(pid, cid, "native")
        await ctx.app.state.project_store.set_lead(pid, cid)
        async with _bare(ctx.app) as bare:
            resp = await bare.patch(
                f"/api/projects/{pid}/tasks/{tid}",
                json={"parent_task_id": "some-parent"},
                headers=_hdr(token),
            )
        assert resp.status_code == 403

    async def test_non_lead_agent_patch_rejected(self, ctx):
        """An agent that neither created nor leads the project cannot PATCH
        its cards -> 403."""
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        cid, token = await _mint_agent(ctx, pid, scopes=("project_tasks_update",))
        async with _bare(ctx.app) as bare:
            resp = await bare.patch(
                f"/api/projects/{pid}/tasks/{tid}",
                json={"body": "hacked"},
                headers=_hdr(token),
            )
        assert resp.status_code == 403

    async def test_project_tasks_only_token_cannot_patch(self, ctx):
        """Regression pin (tsk-b6ugu5): a plain project_tasks grant (read +
        lifecycle + comments) must NOT authorize PATCH field mutation -- that
        requires the narrower project_tasks_update scope. A lead agent carrying
        only project_tasks must be refused 403, not allowed to edit card
        bodies/priority. This fails RED against the PR 2240 branch (which
        collapsed the PATCH scope to project_tasks) and passes after the fix."""
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        cid, token = await _mint_agent(ctx, pid, scopes=("project_tasks",))
        await ctx.app.state.project_store.add_member(pid, cid, "native")
        await ctx.app.state.project_store.set_lead(pid, cid)
        async with _bare(ctx.app) as bare:
            resp = await bare.patch(
                f"/api/projects/{pid}/tasks/{tid}",
                json={"body": "hacked by read-only token"},
                headers=_hdr(token),
            )
        assert resp.status_code == 403

    async def test_admin_patch_assignee_id_unchanged(self, ctx):
        """Human session path unchanged: admin may still patch assignee_id."""
        pid = await _new_project(ctx, "alpha")
        tid = await _new_task(ctx, pid)
        resp = await ctx.client.patch(
            f"/api/projects/{pid}/tasks/{tid}",
            json={"assignee_id": "worker-1"},
        )
        assert resp.status_code == 200
        assert resp.json()["assignee_id"] == "worker-1"

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

    async def test_project_tasks_alone_cannot_create_task(self, ctx):
        """project_tasks is read + lifecycle + comments. Authoring requires the
        SEPARATE project_tasks_create grant, so an agent approved only for
        project_tasks must still be refused (Invariant 2 + 5 preserved)."""
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


@pytest.mark.asyncio
class TestAgentElementFilter:
    """Slice 1: an agent token filters tasks by element with ZERO auth change.

    The element tag is a view of data the token already reads under
    project_tasks, so the existing dual-auth gate passes element-filtered
    reads straight through. No new agent surface, no scope change.
    """

    async def _new_element(self, ctx, pid, name="Website"):
        resp = await ctx.client.post(
            f"/api/projects/{pid}/elements", json={"name": name}
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["id"]

    async def test_agent_filters_by_element(self, ctx):
        pid = await _new_project(ctx, "alpha")
        eid = await self._new_element(ctx, pid)
        tagged = await _new_task(ctx, pid)
        await ctx.client.patch(
            f"/api/projects/{pid}/tasks/{tagged}", json={"element_id": eid}
        )
        await _new_task(ctx, pid)  # untagged task

        _cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            by_element = await bare.get(
                f"/api/projects/{pid}/tasks",
                params={"element_id": eid},
                headers=_hdr(token),
            )
            assert by_element.status_code == 200
            assert [t["id"] for t in by_element.json()["items"]] == [tagged]

            none_only = await bare.get(
                f"/api/projects/{pid}/tasks",
                params={"element_id": "none"},
                headers=_hdr(token),
            )
            assert none_only.status_code == 200
            assert all(t["element_id"] is None for t in none_only.json()["items"])

    async def test_agent_ready_filter_by_element(self, ctx):
        pid = await _new_project(ctx, "alpha")
        eid = await self._new_element(ctx, pid)
        tagged = await _new_task(ctx, pid)
        await ctx.client.patch(
            f"/api/projects/{pid}/tasks/{tagged}", json={"element_id": eid}
        )

        _cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/tasks/ready",
                params={"element_id": eid},
                headers=_hdr(token),
            )
        assert resp.status_code == 200
        assert [t["id"] for t in resp.json()["items"]] == [tagged]


@pytest.mark.asyncio
class TestLeadAgentMarkClaimable:
    """The project LEAD agent may flag cards claimable; a non-lead project_tasks
    agent may not (curation is lead-only, narrower than the read/claim scope)."""

    async def test_lead_agent_marks_and_unmarks_claimable(self, ctx):
        pid = await _new_project(ctx, "claimable-lead")
        tid = await _new_task(ctx, pid)
        cid, token = await _mint_agent(ctx, pid)  # project_tasks (claimable route scope)
        await ctx.app.state.project_store.add_member(pid, cid, "native")
        await ctx.app.state.project_store.set_lead(pid, cid)
        async with _bare(ctx.app) as bare:
            on = await bare.post(
                f"/api/projects/{pid}/tasks/{tid}/claimable",
                json={"claimable": True},
                headers=_hdr(token),
            )
            assert on.status_code == 200, on.text
            assert "claimable" in on.json()["labels"]
            off = await bare.post(
                f"/api/projects/{pid}/tasks/{tid}/claimable",
                json={"claimable": False},
                headers=_hdr(token),
            )
            assert off.status_code == 200
            assert "claimable" not in off.json()["labels"]

    async def test_non_lead_agent_cannot_mark_claimable(self, ctx):
        pid = await _new_project(ctx, "claimable-nonlead")
        tid = await _new_task(ctx, pid)
        _cid, token = await _mint_agent(ctx, pid)  # project_tasks but NOT the lead
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/tasks/{tid}/claimable",
                json={"claimable": True},
                headers=_hdr(token),
            )
        assert resp.status_code == 404, resp.text  # existence-hiding refusal

    async def test_claimable_preserves_other_labels(self, ctx):
        pid = await _new_project(ctx, "claimable-preserve")
        tid = await _new_task(ctx, pid)
        await ctx.client.patch(
            f"/api/projects/{pid}/tasks/{tid}", json={"labels": ["urgent"]}
        )
        cid, token = await _mint_agent(ctx, pid)  # project_tasks (claimable route scope)
        await ctx.app.state.project_store.add_member(pid, cid, "native")
        await ctx.app.state.project_store.set_lead(pid, cid)
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/tasks/{tid}/claimable",
                json={"claimable": True},
                headers=_hdr(token),
            )
        assert resp.status_code == 200, resp.text
        labels = resp.json()["labels"]
        assert "claimable" in labels and "urgent" in labels

    async def test_session_owner_marks_claimable(self, ctx):
        pid = await _new_project(ctx, "claimable-owner")
        tid = await _new_task(ctx, pid)
        resp = await ctx.client.post(
            f"/api/projects/{pid}/tasks/{tid}/claimable", json={"claimable": True}
        )
        assert resp.status_code == 200, resp.text
        assert "claimable" in resp.json()["labels"]

@pytest.mark.asyncio
class TestTaskCreationScope:
    """project_tasks_create is a separate, narrower grant for AUTHORING cards.

    Both halves matter: an agent WITH it can create, an agent WITHOUT it cannot.
    A test that only asserted the refusal would pass against a route nobody can
    reach, and one that only asserted success would not prove the scope is
    enforced at all.
    """

    async def test_project_tasks_create_allows_authoring(self, ctx):
        pid = await _new_project(ctx, "alpha")
        cid, token = await _mint_agent(ctx, pid, scopes=("project_tasks_create",))
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/tasks",
                json={"title": "authored by an agent"},
                headers=_hdr(token),
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["title"] == "authored by an agent"
        # Authorship is attributed to the AGENT, not to the project owner.
        assert body["created_by"] == cid

    async def test_create_scope_is_project_bound(self, ctx):
        """A grant on project A must not authorise creation on project B."""
        pid_a = await _new_project(ctx, "alpha")
        pid_b = await _new_project(ctx, "beta")
        _cid, token = await _mint_agent(ctx, pid_a, scopes=("project_tasks_create",))
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid_b}/tasks",
                json={"title": "cross project"},
                headers=_hdr(token),
            )
        # Existence-hiding: indistinguishable from a project that is not theirs.
        assert resp.status_code == 404, resp.text

    async def test_create_scope_does_not_widen_other_routes(self, ctx):
        """project_tasks_create authorises AUTHORING only, not member management."""
        pid = await _new_project(ctx, "alpha")
        _cid, token = await _mint_agent(ctx, pid, scopes=("project_tasks_create",))
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/members",
                json={"mode": "native", "agent_id": "x"},
                headers=_hdr(token),
            )
        assert resp.status_code in (401, 403, 404)
