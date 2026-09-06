"""RED test for S2-12: /data/workspace must be user-scoped.

Before the fix, any authenticated user can read any agent's workspace files
because the StaticFiles mount at /data/workspace has no ownership check.

After the fix, only the agent owner or an admin may read; unauthenticated
requests still return 401; traversal '../' is rejected.
"""
from __future__ import annotations

import pytest

from httpx import ASGITransport, AsyncClient


def _add_user(app, username: str, password: str) -> str:
    auth = app.state.auth
    invite_code = auth.add_user_invite(username, invited_by_username="admin")
    auth.complete_invite(
        username=username,
        invite_code=invite_code,
        full_name=username.title(),
        email=f"{username}@test.local",
        password=password,
    )
    record = auth.find_user(username)
    return record["id"]


@pytest.mark.asyncio
async def test_workspace_files_scoped_to_owner(app, tmp_path):
    from taos_test_csrf import csrf_event_hooks

    registry_store = app.state.agent_registry
    if registry_store._db is None:
        await registry_store.init()

    for attr in ("metrics", "notifications", "qmd_client"):
        store = getattr(app.state, attr, None)
        if store is not None and getattr(store, "_db", None) is None:
            if hasattr(store, "init"):
                await store.init()

    if not app.state.auth.is_configured():
        app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    admin_record = app.state.auth.find_user("admin")
    admin_uid = admin_record["id"] if admin_record else ""

    bob_uid = _add_user(app, "bob", "bobpass1")
    alice_uid = _add_user(app, "alice", "alicepass1")

    agent_name = "bob-agent"
    rec = await registry_store.register(
        framework="openclaw",
        display_name=agent_name,
        user_id=bob_uid,
    )
    canonical_id = rec["canonical_id"]

    agent_workspaces_dir = app.state.agent_workspaces_dir
    agent_dir = agent_workspaces_dir / agent_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    secret_file = agent_dir / "secret.txt"
    secret_file.write_text("bob-secret-data")

    app.state._startup_complete = True

    admin_token = app.state.auth.create_session(user_id=admin_uid, long_lived=True)
    bob_token = app.state.auth.create_session(user_id=bob_uid, long_lived=True)
    alice_token = app.state.auth.create_session(user_id=alice_uid, long_lived=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": admin_token},
        event_hooks=csrf_event_hooks(),
    ) as admin_c, AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": bob_token},
        event_hooks=csrf_event_hooks(),
    ) as bob_c, AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": alice_token},
        event_hooks=csrf_event_hooks(),
    ) as alice_c:
        file_url = f"/data/workspace/{agent_name}/secret.txt"

        admin_resp = await admin_c.get(file_url)
        assert admin_resp.status_code == 200, f"admin expected 200, got {admin_resp.status_code}"
        assert admin_resp.text == "bob-secret-data"

        bob_resp = await bob_c.get(file_url)
        assert bob_resp.status_code == 200, f"owner expected 200, got {bob_resp.status_code}"
        assert bob_resp.text == "bob-secret-data"

        alice_resp = await alice_c.get(file_url)
        assert alice_resp.status_code in (403, 404), f"stranger expected 403/404, got {alice_resp.status_code}"

        admin_c.cookies.clear()
        unauth_resp = await admin_c.get(file_url)
        assert unauth_resp.status_code == 401, f"unauthenticated expected 401, got {unauth_resp.status_code}"

        traversal_resp = await alice_c.get(f"/data/workspace/{agent_name}/../secret.txt")
        assert traversal_resp.status_code in (403, 404, 307), f"traversal expected 403/404/307, got {traversal_resp.status_code}"

        traversal_resp2 = await alice_c.get(f"/data/workspace/{agent_name}/../../etc/passwd")
        assert traversal_resp2.status_code in (403, 404, 307), f"traversal2 expected 403/404/307, got {traversal_resp2.status_code}"

    await registry_store.close()
