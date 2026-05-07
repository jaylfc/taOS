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


def test_sw_js_headers_present_with_fake_file(client, monkeypatch, tmp_path):
    """CI-safe coverage of the response headers — uses a fake sw.js so
    the assertions fire even when the real SPA bundle isn't built."""
    fake_dir = tmp_path / "fake-spa"
    fake_dir.mkdir()
    (fake_dir / "sw.js").write_text("// fake sw")
    monkeypatch.setattr("tinyagentos.routes.desktop.SPA_DIR", fake_dir)
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


# ---------------------------------------------------------------------------
# PWA shell pre-login accessibility
# The service worker must be able to precache the shell HTML without a
# session cookie. Auth is enforced client-side by the SPA itself.
# ---------------------------------------------------------------------------


def test_desktop_shell_accessible_without_auth(client, monkeypatch, tmp_path):
    """/desktop returns 200 (or 404 if not built) without an auth cookie.
    A 401 here would prevent the SW from precaching the shell."""
    fake_dir = tmp_path / "spa"
    fake_dir.mkdir()
    (fake_dir / "index.html").write_text("<html>shell</html>")
    monkeypatch.setattr("tinyagentos.routes.desktop.SPA_DIR", fake_dir)
    r = client.get("/desktop")
    assert r.status_code in (200, 404), (
        f"Expected 200 or 404, got {r.status_code} — shell must not return 401"
    )


def test_desktop_shell_not_built_returns_404_without_auth(client, monkeypatch, tmp_path):
    """/desktop returns 404 (not 401) when SPA is not built."""
    monkeypatch.setattr("tinyagentos.routes.desktop.SPA_DIR", tmp_path / "no-such-dir")
    r = client.get("/desktop")
    assert r.status_code == 404


def test_chat_pwa_accessible_without_auth(client, monkeypatch, tmp_path):
    """/chat-pwa returns 200 (or 404 if not built) without an auth cookie."""
    fake_dir = tmp_path / "spa"
    fake_dir.mkdir()
    (fake_dir / "chat.html").write_text("<html>chat pwa</html>")
    monkeypatch.setattr("tinyagentos.routes.desktop.SPA_DIR", fake_dir)
    r = client.get("/chat-pwa")
    assert r.status_code in (200, 404), (
        f"Expected 200 or 404, got {r.status_code} — chat PWA must not return 401"
    )


def test_chat_pwa_subpath_accessible_without_auth(client, monkeypatch, tmp_path):
    """/chat-pwa/<subpath> falls back to chat.html without auth (SPA routing)."""
    fake_dir = tmp_path / "spa"
    fake_dir.mkdir()
    (fake_dir / "chat.html").write_text("<html>chat pwa</html>")
    monkeypatch.setattr("tinyagentos.routes.desktop.SPA_DIR", fake_dir)
    r = client.get("/chat-pwa/some-thread")
    assert r.status_code in (200, 404), (
        f"Expected 200 or 404, got {r.status_code} — chat PWA subpaths must not return 401"
    )


def test_api_agents_still_requires_auth(client):
    """Sanity check: opening the shell to auth-free access must not accidentally
    expose real API endpoints. /api/agents must still return 401 without a cookie."""
    r = client.get("/api/agents")
    assert r.status_code == 401
