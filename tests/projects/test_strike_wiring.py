"""Wiring tests for the strike store (tsk-orqoif / #2333 follow-up).

#2333 added StrikeStore and an optional ``strikes=`` param on ProjectTaskStore
but never constructed the store, attached it to app.state, or passed it into
ProjectTaskStore -- the param was inert. These tests exercise the REAL app
lifespan (mirroring tests/projects/test_routes_beads.py) rather than the
'client' fixture, because 'client' bypasses the lifespan entirely and would
pass even with the store never wired up.
"""
from __future__ import annotations

import asyncio
import pytest
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks


def _auth_client(app):
    """Return a session-cookie-authenticated AsyncClient for the given app."""
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )


async def _make_project_and_task(c, slug: str) -> tuple[str, str]:
    r = await c.post("/api/projects", json={"name": "Demo", "slug": slug})
    assert r.status_code == 200, r.text
    project_id = r.json()["id"]
    r = await c.post(f"/api/projects/{project_id}/tasks", json={"title": "T1"})
    assert r.status_code == 200, r.text
    return project_id, r.json()["id"]


@pytest.mark.asyncio
async def test_app_state_has_task_strikes_store(app):
    """StrikeStore must be attached to app.state and initialised by the lifespan."""
    async with app.router.lifespan_context(app):
        strikes = app.state.task_strikes
        assert strikes is not None
        # Prove it is actually usable (init() ran), not just non-None.
        count = await strikes.record_strike("tsk-fake", "verify", log_tail="boom")
        assert count == 1
        assert await strikes.count_strikes("tsk-fake") == 1


@pytest.mark.asyncio
async def test_close_task_clears_strikes_via_taskstore_wiring(app):
    """ProjectTaskStore must have actually received the strike store: closing
    a task with recorded strikes should clear them (task_store.py's
    close_task -> self._strikes.clear_strikes). This fails if the
    strikes=strike_store constructor arg was never wired in app.py."""
    async with app.router.lifespan_context(app):
        async with _auth_client(app) as c:
            _project_id, task_id = await _make_project_and_task(c, "strike-close")

            strikes = app.state.task_strikes
            await strikes.record_strike(task_id, "verify", log_tail="fail 1")
            await strikes.record_strike(task_id, "verify", log_tail="fail 2")
            assert await strikes.count_strikes(task_id) == 2

            r = await c.post(
                f"/api/projects/{_project_id}/tasks/{task_id}/close",
                json={"closed_by": "tester"},
            )
            assert r.status_code == 200, r.text

            assert await strikes.count_strikes(task_id) == 0


@pytest.mark.asyncio
async def test_get_task_surfaces_strike_count_and_latest(app):
    """Task-detail response must surface strike_count + latest_strike."""
    async with app.router.lifespan_context(app):
        async with _auth_client(app) as c:
            project_id, task_id = await _make_project_and_task(c, "strike-surface")

            strikes = app.state.task_strikes
            await strikes.record_strike(task_id, "verify", log_tail="first")
            await strikes.record_strike(task_id, "verify", log_tail="second")

            r = await c.get(f"/api/projects/{project_id}/tasks/{task_id}")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["strike_count"] == 2
            assert body["latest_strike"] is not None
            assert body["latest_strike"]["log_tail"] == "second"


@pytest.mark.asyncio
async def test_unquarantine_route_lead_success_and_noauth_failure(app):
    """The unquarantine route must work through the real HTTP layer with
    proper (lead) auth, clear strikes, and be refused 401 without auth."""
    async with app.router.lifespan_context(app):
        async with _auth_client(app) as c:
            project_id, task_id = await _make_project_and_task(c, "strike-unq")

            task_store = app.state.project_task_store
            strikes = app.state.task_strikes
            await strikes.record_strike(task_id, "verify", log_tail="strike 3")
            ok = await task_store.quarantine_task(task_id, "system")
            assert ok is True

            unauth = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
            try:
                r = await unauth.post(
                    f"/api/projects/{project_id}/tasks/{task_id}/unquarantine"
                )
            finally:
                await unauth.aclose()
            # No session cookie and no Authorization header at all: the global
            # auth middleware gate refuses the request before it ever reaches
            # the route (same as every other task route with zero credentials).
            assert r.status_code == 401

            # Task must still be quarantined -- the unauthenticated call above
            # must not have mutated it.
            still_quarantined = await task_store.get_task(task_id)
            assert still_quarantined["status"] == "quarantined"

            r = await c.post(
                f"/api/projects/{project_id}/tasks/{task_id}/unquarantine"
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "open"

            assert await strikes.count_strikes(task_id) == 0


@pytest.mark.asyncio
async def test_release_parks_after_threshold_strikes(app):
    """Releasing a claimed task records a strike; after STRIKE_THRESHOLD
    cumulative releases the task is quarantined."""
    async with app.router.lifespan_context(app):
        async with _auth_client(app) as c:
            _, task_id = await _make_project_and_task(c, "strike-quarantine")

            task_store = app.state.project_task_store
            strikes = app.state.task_strikes

            for i in range(strikes.STRIKE_THRESHOLD):
                await task_store.claim_task(task_id, "worker-1")
                ok = await task_store.release_task(task_id, "worker-1")
                assert ok is True
                count = await strikes.count_strikes(task_id)
                if i < strikes.STRIKE_THRESHOLD - 1:
                    assert count == i + 1

            fetched = await task_store.get_task(task_id)
            assert fetched["status"] == "quarantined"
            assert await strikes.count_strikes(task_id) == strikes.STRIKE_THRESHOLD


@pytest.mark.asyncio
async def test_concurrent_claim_during_release(app):
    """During release_task, if another worker claims the task, the strike
    recording and quarantine must not leave the task in an invalid state with a
    stale claimed_by held in quarantined status.

    The release must record the *threshold* strike for the quarantine path to
    run at all, so seed STRIKE_THRESHOLD - 1 strikes first; with fewer the
    release never reaches quarantine_task and the test proves nothing."""
    async with app.router.lifespan_context(app):
        async with _auth_client(app) as c:
            _, task_id = await _make_project_and_task(c, "strike-concurrent")

            task_store = app.state.project_task_store
            strikes = app.state.task_strikes

            # Claim the task so release_task can clear claimed_by and set status to 'open'
            await task_store.claim_task(task_id, "worker-1")

            # Seed up to one below the threshold so the strike recorded by
            # release_task below is the one that trips quarantine.
            for _ in range(strikes.STRIKE_THRESHOLD - 1):
                await strikes.record_strike(task_id, "dispatch_failed", actor="worker-1")
            assert await strikes.count_strikes(task_id) == strikes.STRIKE_THRESHOLD - 1

            # Now race: release the task (which records the threshold strike and
            # attempts to quarantine) while another worker tries to claim it.
            released = asyncio.create_task(
                task_store.release_task(task_id, "worker-1")
            )
            claimed = asyncio.create_task(task_store.claim_task(task_id, "worker-2"))

            release_ok, claim_ok = await asyncio.gather(released, claimed)
            assert release_ok is True

            fetched = await task_store.get_task(task_id)
            status = fetched["status"]
            assert status in ("open", "claimed", "quarantined")
            # Quarantine may never swallow a live claim, and a quarantined card
            # may never carry an owner that can no longer release it.
            if status == "quarantined":
                assert fetched.get("claimed_by") is None, "Task quarantined with stale claimed_by"
                assert fetched.get("claimed_at") is None, "Task quarantined with stale claimed_at"
            if claim_ok:
                # worker-2 won the claim, so the release path must have left it
                # alone rather than quarantining someone else's active card.
                assert status == "claimed", f"concurrent claim was overridden ({status})"
                assert fetched.get("claimed_by") == "worker-2"
            # The threshold strike from the release is recorded regardless of
            # whether quarantine was skipped.
            count = await strikes.count_strikes(task_id)
            assert count == strikes.STRIKE_THRESHOLD


@pytest.mark.asyncio
async def test_three_releases_quarantine_via_http(app):
    """Three cumulative dispatch_failed strikes through the release HTTP route
    must quarantine the card.  This is the end-to-end proof that the trigger
    wiring is live: if record_strike is dropped or quarantine_task is not
    called on the threshold, the task remains open instead of quarantined."""
    from tinyagentos.projects.strike_store import StrikeStore

    async with app.router.lifespan_context(app):
        async with _auth_client(app) as c:
            project_id, task_id = await _make_project_and_task(c, "strike-e2e")

            for _ in range(StrikeStore.STRIKE_THRESHOLD):
                r = await c.post(
                    f"/api/projects/{project_id}/tasks/{task_id}/claim",
                    json={"claimer_id": "worker-1"},
                )
                assert r.status_code == 200, r.text
                r = await c.post(
                    f"/api/projects/{project_id}/tasks/{task_id}/release",
                    json={"releaser_id": "worker-1"},
                )
                assert r.status_code == 200, r.text

            r = await c.get(f"/api/projects/{project_id}/tasks/{task_id}")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "quarantined"
