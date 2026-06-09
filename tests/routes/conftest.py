"""Shared fixtures for tests/routes/ — sync TestClient + auth header helpers."""
from __future__ import annotations

import uuid

import pytest
import yaml
from fastapi.testclient import TestClient

from tinyagentos.app import create_app
from tinyagentos.shortcuts.capabilities import CAP_AGENT_SHELL


def _make_app(tmp_path):
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    app = create_app(data_dir=tmp_path)
    # Lifespan-owned objects set to None by create_app() — initialise them
    # eagerly so sync TestClient tests work without running the lifespan.
    from tinyagentos.routes.desktop_browser.vapid import load_or_create_vapid_keypair
    app.state.vapid_keypair = load_or_create_vapid_keypair(tmp_path)
    return app


@pytest.fixture
def app(tmp_path):
    """Minimal taOS app with no agents, used by all routes/ tests."""
    return _make_app(tmp_path)


@pytest.fixture
def test_client(app):
    """Synchronous TestClient; auth is via Cookie header (taos_session)."""
    # The app must be initialised enough for auth to work.  create_app sets up
    # app.state.auth eagerly, so this is fine without running lifespan.
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def _create_user_with_caps(auth_mgr, username: str, caps: list[str]) -> str:
    """Create a user with a specific capability set and return a session token."""
    # First user is always admin; extra users go through the invite flow.
    if not auth_mgr.is_configured():
        auth_mgr.setup_user("admin", "Admin", "", "adminpass")

    # Invite + complete
    invite_code = auth_mgr.add_user_invite(username, "admin")
    auth_mgr.complete_invite(username, invite_code, username, "", "testpass1")

    # Overwrite capabilities directly on the stored record
    data = auth_mgr._read_users()
    for u in data["users"]:
        if u.get("username") == username:
            u["capabilities"] = list(caps)
            break
    auth_mgr._write_users(data)

    record = auth_mgr.find_user(username)
    token = auth_mgr.create_session(user_id=record["id"], long_lived=True)
    return token


@pytest.fixture
def admin_auth_headers(app):
    """Cookie header dict for the admin user (all capabilities)."""
    auth_mgr = app.state.auth
    if not auth_mgr.is_configured():
        auth_mgr.setup_user("admin", "Admin", "", "adminpass")
    record = auth_mgr.find_user("admin")
    if record is None:
        auth_mgr.setup_user("admin", "Admin", "", "adminpass")
        record = auth_mgr.find_user("admin")
    token = auth_mgr.create_session(user_id=record["id"], long_lived=True)
    return {"Cookie": f"taos_session={token}"}


@pytest.fixture
def chat_only_auth_headers(app):
    """Cookie header dict for a user with only the 'chat' capability."""
    token = _create_user_with_caps(app.state.auth, "chat_only_user", ["chat"])
    return {"Cookie": f"taos_session={token}"}


@pytest.fixture
def shell_only_auth_headers(app):
    """Cookie header dict for a user with {chat, agent.shell} capabilities."""
    token = _create_user_with_caps(
        app.state.auth, "shell_only_user", ["chat", CAP_AGENT_SHELL]
    )
    return {"Cookie": f"taos_session={token}"}


@pytest.fixture
def seeded_agent_factory(app, monkeypatch):
    """Factory: create a test agent with an arbitrary framework + shortcuts list.

    Usage::

        agent = seeded_agent_factory(
            framework="openclaw",
            shortcuts=[{"kind": "container-terminal", ...}],
        )
        agent["id"]  # the id for /api/agents/{id}/shortcuts

    Injects a temporary framework entry (keyed by a unique ID) into
    tinyagentos.frameworks.FRAMEWORKS so the route can find the shortcuts,
    and appends the agent to app.state.config.agents.
    """
    import tinyagentos.frameworks as fw_mod

    created_ids: list[str] = []

    def factory(framework: str, shortcuts: list[dict]) -> dict:
        # Register a patched FRAMEWORKS dict that includes our test shortcuts.
        # We use the real framework name so the agent's `framework` field matches.
        # If the framework already exists, we override its shortcuts only.
        original = fw_mod.FRAMEWORKS.get(framework, {"id": framework, "name": framework})
        patched_entry = {**original, "shortcuts": shortcuts}
        patched_frameworks = {**fw_mod.FRAMEWORKS, framework: patched_entry}
        monkeypatch.setattr(fw_mod, "FRAMEWORKS", patched_frameworks)

        agent_id = uuid.uuid4().hex[:12]
        agent = {
            "id": agent_id,
            "name": f"test-agent-{agent_id}",
            "host": "127.0.0.1",
            "qmd_index": "test",
            "color": "#abcdef",
            "framework": framework,
        }
        app.state.config.agents.append(agent)
        created_ids.append(agent_id)
        return agent

    yield factory

    # Cleanup: remove injected agents from config
    app.state.config.agents = [
        a for a in app.state.config.agents if a.get("id") not in created_ids
    ]


@pytest.fixture
def patch_worker_signing_key(monkeypatch):
    """Inject a test ClusterManager with a known signing key for redeem tests.

    Sets up a real ClusterManager with a 'local' worker enrolled using the
    test signing key, then calls set_active_manager so get_local_worker()
    returns the test key (fail-closed registry, no fallback).
    """
    import tinyagentos.cluster.worker_registry as wr_mod
    from tinyagentos.cluster.manager import ClusterManager
    from tinyagentos.cluster.worker_protocol import WorkerInfo

    test_mgr = ClusterManager()
    test_mgr._workers["local"] = WorkerInfo(
        name="local",
        url="http://127.0.0.1:6969",
        worker_url="http://127.0.0.1:6969",
        signing_key=b"test-signing-key-32-bytes-padded",
        platform="local",
    )
    monkeypatch.setattr(wr_mod, "_active_manager", test_mgr)
    yield
