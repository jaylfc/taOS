import pytest
import pytest_asyncio
from types import SimpleNamespace
from fastapi import HTTPException

from tinyagentos.device_store import DeviceStore
from tinyagentos.device_auth import extract_bearer, require_device


def _request(app, auth_header=None):
    headers = {}
    if auth_header is not None:
        headers["authorization"] = auth_header
    # Minimal stand-in for a Starlette request: .headers.get + .app.state.
    return SimpleNamespace(
        headers=SimpleNamespace(get=lambda k, d=None: headers.get(k.lower(), d)),
        app=SimpleNamespace(state=app.state),
    )


class _AppState:
    def __init__(self, store):
        self.state = SimpleNamespace(device_store=store)


@pytest_asyncio.fixture
async def app_with_store(tmp_path):
    store = DeviceStore(tmp_path / "devices.db")
    await store.init()
    yield _AppState(store)
    await store.close()


def test_extract_bearer():
    assert extract_bearer(_request(_AppState(None), "Bearer abc")) == "abc"
    assert extract_bearer(_request(_AppState(None), "Basic abc")) is None
    assert extract_bearer(_request(_AppState(None))) is None


@pytest.mark.asyncio
async def test_require_device_resolves_valid_token(app_with_store):
    dev = await app_with_store.state.device_store.register(user_id="u1", platform="ios")
    req = _request(app_with_store, f"Bearer {dev['scoped_token']}")
    resolved = await require_device(req)
    assert resolved["device_id"] == dev["device_id"]


@pytest.mark.asyncio
async def test_require_device_rejects_missing_and_revoked(app_with_store):
    with pytest.raises(HTTPException) as ei:
        await require_device(_request(app_with_store))
    assert ei.value.status_code == 401

    dev = await app_with_store.state.device_store.register(user_id="u1", platform="ios")
    await app_with_store.state.device_store.revoke(dev["device_id"])
    with pytest.raises(HTTPException) as ei2:
        await require_device(_request(app_with_store, f"Bearer {dev['scoped_token']}"))
    assert ei2.value.status_code == 401
