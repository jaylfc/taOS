"""Doc-review stamp store: state machine, actor recording, invalid-transition
rejects, and project-scoped agent-token gating on writes.

The store (tinyagentos/projects/doc_review_store.py) is exercised directly for
the review_state machine and actor recording.  The routes
(tinyagentos/routes/project_doc_review.py) are exercised over HTTP to pin the
three security invariants introduced by the dual-auth gate:

  1. A session owner (admin) can read and write any doc's review state.
  2. An approved agent token (scope project_doc_review) bound to project A can
     drive ONLY project A's doc-review routes; a token bound elsewhere is
     collapsed into an existence-hiding 404 (indistinguishable from a
     non-owner session).
  3. A token missing the project_doc_review scope is rejected 403; an
     unauthenticated (no session, no token) request is 401.

Invalid review_state transitions raise ValueError in the store and map to HTTP
409 at the route; an unknown state maps to 400.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token
from tinyagentos.projects.doc_review_store import (
    VALID_TRANSITIONS,
    DocReviewStore,
)


# ---------------------------------------------------------------------------
# Store-level: review_state machine + actor recording
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_missing_returns_none(tmp_path):
    store = DocReviewStore(tmp_path / "projects.db")
    await store.init()
    assert await store.get_review("p1", "a.md") is None


@pytest.mark.asyncio
async def test_awaiting_to_approved_records_actor(tmp_path):
    store = DocReviewStore(tmp_path / "projects.db")
    await store.init()
    first = await store.set_review_state("p1", "a.md", "awaiting_review", "user-1")
    assert first["review_state"] == "awaiting_review"
    # First write to awaiting_review does not stamp an approver.
    assert first["reviewed_by"] is None
    assert first["changes_requested_by"] is None

    approved = await store.set_review_state("p1", "a.md", "approved", "agent-7")
    assert approved["review_state"] == "approved"
    assert approved["reviewed_by"] == "agent-7"
    assert approved["reviewed_at"] is not None
    assert approved["changes_requested_by"] is None


@pytest.mark.asyncio
async def test_awaiting_to_changes_requested_records_actor(tmp_path):
    store = DocReviewStore(tmp_path / "projects.db")
    await store.init()
    await store.set_review_state("p1", "b.md", "awaiting_review", "user-1")
    r = await store.set_review_state("p1", "b.md", "changes_requested", "agent-3")
    assert r["review_state"] == "changes_requested"
    assert r["changes_requested_by"] == "agent-3"
    assert r["changes_requested_at"] is not None
    assert r["reviewed_by"] is None


@pytest.mark.asyncio
async def test_approved_back_to_awaiting_keeps_actor(tmp_path):
    store = DocReviewStore(tmp_path / "projects.db")
    await store.init()
    await store.set_review_state("p1", "c.md", "awaiting_review", "u")
    await store.set_review_state("p1", "c.md", "approved", "a1")
    r = await store.set_review_state("p1", "c.md", "awaiting_review", "u")
    assert r["review_state"] == "awaiting_review"
    # Returning to awaiting_review does not clear the stamped approver.
    assert r["reviewed_by"] == "a1"
    assert r["reviewed_at"] is not None


@pytest.mark.asyncio
async def test_changes_requested_back_to_awaiting_keeps_actor(tmp_path):
    store = DocReviewStore(tmp_path / "projects.db")
    await store.init()
    await store.set_review_state("p1", "c.md", "awaiting_review", "u")
    await store.set_review_state("p1", "c.md", "changes_requested", "a2")
    r = await store.set_review_state("p1", "c.md", "awaiting_review", "u")
    assert r["review_state"] == "awaiting_review"
    assert r["changes_requested_by"] == "a2"
    assert r["changes_requested_at"] is not None


@pytest.mark.asyncio
async def test_invalid_transition_raises_value_error(tmp_path):
    store = DocReviewStore(tmp_path / "projects.db")
    await store.init()
    await store.set_review_state("p1", "d.md", "awaiting_review", "u")
    await store.set_review_state("p1", "d.md", "approved", "a1")
    # approved -> changes_requested is not a permitted transition.
    with pytest.raises(ValueError) as exc:
        await store.set_review_state("p1", "d.md", "changes_requested", "a2")
    assert "invalid transition" in str(exc.value)

    # changes_requested -> approved is likewise invalid.
    store2 = DocReviewStore(tmp_path / "projects2.db")
    await store2.init()
    await store2.set_review_state("p1", "e.md", "awaiting_review", "u")
    await store2.set_review_state("p1", "e.md", "changes_requested", "a2")
    with pytest.raises(ValueError) as exc2:
        await store2.set_review_state("p1", "e.md", "approved", "a1")
    assert "invalid transition" in str(exc2.value)


@pytest.mark.asyncio
async def test_unknown_state_raises_value_error(tmp_path):
    store = DocReviewStore(tmp_path / "projects.db")
    await store.init()
    with pytest.raises(ValueError):
        await store.set_review_state("p1", "f.md", "banana", "u")


@pytest.mark.asyncio
async def test_first_write_can_be_approved_or_changes_requested(tmp_path):
    store = DocReviewStore(tmp_path / "projects.db")
    await store.init()
    # A brand-new doc may be stamped approved (or changes_requested) directly.
    approved = await store.set_review_state("p1", "g.md", "approved", "a1")
    assert approved["review_state"] == "approved"
    assert approved["reviewed_by"] == "a1"
    cr = await store.set_review_state("p1", "h.md", "changes_requested", "a2")
    assert cr["review_state"] == "changes_requested"
    assert cr["changes_requested_by"] == "a2"


@pytest.mark.asyncio
async def test_list_reviews_filters_by_state(tmp_path):
    store = DocReviewStore(tmp_path / "projects.db")
    await store.init()
    await store.set_review_state("p1", "x.md", "awaiting_review", "u")
    await store.set_review_state("p1", "y.md", "approved", "a1")
    await store.set_review_state("p1", "z.md", "awaiting_review", "u")

    approved = await store.list_reviews("p1", state="approved")
    assert [r["doc_path"] for r in approved] == ["y.md"]

    all_reviews = await store.list_reviews("p1")
    assert len(all_reviews) == 3
    assert {r["doc_path"] for r in all_reviews} == {"x.md", "y.md", "z.md"}


@pytest.mark.asyncio
async def test_delete_review(tmp_path):
    store = DocReviewStore(tmp_path / "projects.db")
    await store.init()
    await store.set_review_state("p1", "k.md", "awaiting_review", "u")
    assert await store.delete_review("p1", "k.md") is True
    assert await store.get_review("p1", "k.md") is None
    assert await store.delete_review("p1", "missing.md") is False


# ---------------------------------------------------------------------------
# Route-level: project-scoped agent-token gating on writes
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def ctx(client):
    """Reuse the session-admin `client` app and additionally init the
    doc-review store and the agent registry + grants stores so agent tokens
    can be minted against real stores."""
    app = client._transport.app
    for attr in ("agent_registry", "agent_grants", "doc_review_store"):
        store = getattr(app.state, attr)
        if store._db is None:
            await store.init()
    uid = app.state.auth.find_user("admin")["id"]
    yield SimpleNamespace(client=client, app=app, uid=uid)
    for attr in ("agent_registry", "agent_grants", "doc_review_store"):
        store = getattr(app.state, attr)
        if store._db is not None:
            await store.close()


def _bare(app):
    """Cookieless client so requests carry only the Bearer header."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _new_project(ctx, slug):
    resp = await ctx.client.post(
        "/api/projects", json={"name": slug, "slug": slug}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _mint_agent(ctx, project_id, scopes=("project_doc_review",)):
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
class TestOwnerSession:
    async def test_owner_can_write_and_read(self, ctx):
        pid = await _new_project(ctx, "alpha")
        resp = await ctx.client.put(
            f"/api/projects/{pid}/doc-review/README.md",
            json={"state": "awaiting_review"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["review_state"] == "awaiting_review"

        got = await ctx.client.get(f"/api/projects/{pid}/doc-review/README.md")
        assert got.status_code == 200
        assert got.json()["review_state"] == "awaiting_review"

    async def test_owner_list_reviews(self, ctx):
        pid = await _new_project(ctx, "alpha")
        await ctx.client.put(
            f"/api/projects/{pid}/doc-review/a.md", json={"state": "approved"}
        )
        await ctx.client.put(
            f"/api/projects/{pid}/doc-review/b.md", json={"state": "awaiting_review"}
        )
        listed = await ctx.client.get(f"/api/projects/{pid}/doc-reviews")
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert {r["doc_path"] for r in items} == {"a.md", "b.md"}

    async def test_owner_invalid_transition_is_409(self, ctx):
        pid = await _new_project(ctx, "alpha")
        await ctx.client.put(
            f"/api/projects/{pid}/doc-review/README.md",
            json={"state": "awaiting_review"},
        )
        await ctx.client.put(
            f"/api/projects/{pid}/doc-review/README.md", json={"state": "approved"}
        )
        resp = await ctx.client.put(
            f"/api/projects/{pid}/doc-review/README.md",
            json={"state": "changes_requested"},
        )
        assert resp.status_code == 409, resp.text

    async def test_owner_unknown_state_is_400(self, ctx):
        pid = await _new_project(ctx, "alpha")
        resp = await ctx.client.put(
            f"/api/projects/{pid}/doc-review/README.md", json={"state": "banana"}
        )
        assert resp.status_code == 400, resp.text

    async def test_owner_missing_doc_is_null_state(self, ctx):
        pid = await _new_project(ctx, "alpha")
        resp = await ctx.client.get(f"/api/projects/{pid}/doc-review/ghost.md")
        assert resp.status_code == 200
        assert resp.json()["review_state"] is None


@pytest.mark.asyncio
class TestAgentScopeGating:
    async def test_agent_can_write_own_project(self, ctx):
        pid = await _new_project(ctx, "alpha")
        _cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.put(
                f"/api/projects/{pid}/doc-review/README.md",
                json={"state": "awaiting_review"},
                headers=_hdr(token),
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["review_state"] == "awaiting_review"

    async def test_agent_write_to_own_project_records_actor(self, ctx):
        pid = await _new_project(ctx, "alpha")
        cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.put(
                f"/api/projects/{pid}/doc-review/README.md",
                json={"state": "approved"},
                headers=_hdr(token),
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["reviewed_by"] == cid
        assert body["review_state"] == "approved"

    async def test_agent_can_read_own_project(self, ctx):
        pid = await _new_project(ctx, "alpha")
        await ctx.client.put(
            f"/api/projects/{pid}/doc-review/README.md", json={"state": "approved"}
        )
        _cid, token = await _mint_agent(ctx, pid)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/doc-review/README.md", headers=_hdr(token)
            )
        assert resp.status_code == 200
        assert resp.json()["review_state"] == "approved"

    async def test_agent_other_project_is_404(self, ctx):
        pid_a = await _new_project(ctx, "alpha")
        pid_b = await _new_project(ctx, "bravo")
        _cid, token_a = await _mint_agent(ctx, pid_a)
        async with _bare(ctx.app) as bare:
            resp = await bare.put(
                f"/api/projects/{pid_b}/doc-review/README.md",
                json={"state": "awaiting_review"},
                headers=_hdr(token_a),
            )
        # Existence-hiding 404, identical to a non-owner session.
        assert resp.status_code == 404, resp.text

    async def test_agent_missing_scope_is_403(self, ctx):
        pid = await _new_project(ctx, "alpha")
        _cid, token = await _mint_agent(ctx, pid, scopes=("a2a_receive",))
        async with _bare(ctx.app) as bare:
            resp = await bare.put(
                f"/api/projects/{pid}/doc-review/README.md",
                json={"state": "awaiting_review"},
                headers=_hdr(token),
            )
        assert resp.status_code == 403, resp.text

    async def test_unauthenticated_is_401(self, ctx):
        pid = await _new_project(ctx, "alpha")
        async with _bare(ctx.app) as bare:
            resp = await bare.put(
                f"/api/projects/{pid}/doc-review/README.md",
                json={"state": "awaiting_review"},
            )
        assert resp.status_code == 401, resp.text
