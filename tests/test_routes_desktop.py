"""Coverage for GET /sw.js (the service worker entry served at root scope)."""
import pytest
from fastapi.testclient import TestClient
from tinyagentos.app import create_app
from tinyagentos.routes.desktop import SPA_DIR


@pytest.fixture
def client(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path / "data", catalog_dir=tmp_path / "catalog")
    return TestClient(app)


def test_sw_js_returns_javascript_with_root_scope_header(client):
    """When the SPA is built, /sw.js must:
    - serve the file with application/javascript content-type
    - declare Service-Worker-Allowed: / so the SW can claim root scope
      even though it lives under /static/desktop/sw.js
    - not be aggressively cached (Cache-Control no-cache)"""
    sw_path = SPA_DIR / "sw.js"
    if not sw_path.exists():
        pytest.skip("SPA not built (no static/desktop/sw.js); skipping live route test")
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("application/javascript")
    assert r.headers.get("Service-Worker-Allowed") == "/"
    assert "no-cache" in r.headers.get("Cache-Control", "")


def test_sw_js_returns_404_when_not_built(client, monkeypatch, tmp_path):
    """Graceful fallback when the bundle hasn't been built yet."""
    monkeypatch.setattr("tinyagentos.routes.desktop.SPA_DIR", tmp_path / "no-such-dir")
    r = client.get("/sw.js")
    assert r.status_code == 404
