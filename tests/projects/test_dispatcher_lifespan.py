"""Tests for dispatcher loop wiring into app lifespan (tsk-mzf37f)."""

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


@pytest.mark.asyncio
async def test_lifespan_boots_with_dispatcher_loop(app):
    """Boot the app THROUGH the lifespan, assert app.state.dispatcher_service
    exists and a dispatcher task is in app.state._background_tasks; shut down cleanly.
    """
    async with app.router.lifespan_context(app):
        assert app.state.dispatcher_service is not None
        # Check that a dispatcher-related background task exists
        tasks = app.state._background_tasks
        assert len(tasks) > 0
        # Look for dispatcher in task names or descriptions
        found_dispatcher = False
        for task in tasks:
            # tasks contain asyncio.Task objects; check their names or check by repr
            if hasattr(task, "get_name"):
                name = task.get_name()
                if "dispatcher" in name:
                    found_dispatcher = True
                    break
            # Also check coro names if accessible
            coro = task.get_coro() if hasattr(task, "get_coro") else None
            if coro and hasattr(coro, "__name__") and "dispatcher" in coro.__name__:
                found_dispatcher = True
                break
            # Fallback to string check
            task_str = str(task)
            if "dispatcher" in task_str:
                found_dispatcher = True
                break
        assert found_dispatcher, "dispatcher task should be in background tasks"