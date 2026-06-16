"""Shared fixtures for tests/userspace/ route-level tests."""
from __future__ import annotations

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.userspace.store import UserspaceAppStore
from tinyagentos.userspace.data_store import UserspaceDataStore


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
    from tinyagentos.routes.desktop_browser.vapid import load_or_create_vapid_keypair
    app.state.vapid_keypair = load_or_create_vapid_keypair(tmp_path)
    return app


@pytest.fixture
def app(tmp_path):
    return _make_app(tmp_path)


@pytest_asyncio.fixture
async def client(app, tmp_path):
    """Async client with userspace stores initialised and auth set up."""
    # Initialise the minimal set of stores that routes require.
    await app.state.metrics.init()
    await app.state.notifications.init()
    await app.state.qmd_client.init()
    await app.state.secrets.init()

    # Userspace stores -- created fresh per test in tmp_path.
    userspace_apps = UserspaceAppStore(tmp_path / "userspace_apps.db")
    await userspace_apps.init()
    app.state.userspace_apps = userspace_apps

    userspace_data = UserspaceDataStore(tmp_path / "userspace_data.db")
    await userspace_data.init()
    app.state.userspace_data = userspace_data

    # Auth setup.
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    app.state._startup_complete = True

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        yield c

    await userspace_data.close()
    await userspace_apps.close()
    await app.state.secrets.close()
    await app.state.qmd_client.close()
    await app.state.notifications.close()
    await app.state.metrics.close()
    await app.state.http_client.aclose()
