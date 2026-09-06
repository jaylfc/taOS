"""Route tests for the Logs app backend (#1548 part 1)."""
import json

import pytest

from tinyagentos import __version__
from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.routes import system_logs


def _fake_journald_line(message: str, cursor: str = "s=fake-cursor") -> bytes:
    return (json.dumps({"MESSAGE": message, "__CURSOR": cursor}) + "\n").encode("utf-8")


@pytest.mark.asyncio
async def test_sources_without_journald(client, app, tmp_path, monkeypatch):
    """With journald absent, journald-kind sources are unavailable, and the
    file/client sources still resolve without raising."""
    monkeypatch.setattr(system_logs, "_journalctl_missing", lambda: True)
    # Point llmproxy at a clean, empty directory so this assertion does not
    # depend on whatever is (or isn't) left over in the real default
    # /tmp/taos-litellm on the host running the tests.
    monkeypatch.setattr(app.state.llm_proxy, "config_dir", tmp_path / "no-litellm-here")

    resp = await client.get("/api/system-logs/sources")
    assert resp.status_code == 200
    sources = resp.json()
    by_id = {s["id"]: s for s in sources}
    assert set(by_id) == {"controller", "rkllama", "qmd", "llmproxy", "client"}

    for sid in ("controller", "rkllama", "qmd"):
        assert by_id[sid]["available"] is False
        assert by_id[sid]["kind"] == "file"

    # llmproxy has no log file in this test data dir, so it should resolve
    # to available: False without raising.
    assert by_id["llmproxy"]["kind"] == "file"
    assert by_id["llmproxy"]["available"] is False

    # client_log_store is initialised by the `client` fixture.
    assert by_id["client"]["kind"] == "client"
    assert by_id["client"]["available"] is True


@pytest.mark.asyncio
async def test_controller_page_redacts_secret(client, monkeypatch):
    """A fake journald line carrying a live-looking secret must never reach
    the JSON response verbatim."""
    async def fake_run_journalctl(args, timeout=10.0, max_bytes=None):
        return 0, _fake_journald_line("connecting with api_key=SECRETVALUE123")

    monkeypatch.setattr(system_logs, "_run_journalctl", fake_run_journalctl)

    resp = await client.get("/api/system-logs/controller?lines=50")
    assert resp.status_code == 200
    body = resp.json()
    raw_body = resp.text
    assert "SECRETVALUE123" not in raw_body
    assert system_logs.redact("api_key=SECRETVALUE123") in raw_body
    assert body["source"] == "controller"
    assert any("[REDACTED]" in line for line in body["lines"])


@pytest.mark.asyncio
async def test_unknown_source_rejected(client):
    resp = await client.get("/api/system-logs/not-a-real-source")
    assert resp.status_code == 400
    assert resp.json()["error"] == "unknown source"


@pytest.mark.asyncio
async def test_client_source_has_no_paged_reader(client):
    """client is advertised in /sources but is not a readable {source} page --
    the frontend hits /api/client-logs directly for that tab."""
    resp = await client.get("/api/system-logs/client")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_bundle_has_banner_and_redacts_secrets(client, monkeypatch):
    monkeypatch.setattr(system_logs, "_journalctl_missing", lambda: False)

    async def fake_run_journalctl(args, timeout=10.0, max_bytes=None):
        return 0, _fake_journald_line("connecting with api_key=SECRETVALUE123")

    monkeypatch.setattr(system_logs, "_run_journalctl", fake_run_journalctl)

    resp = await client.get("/api/system-logs/bundle")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    assert __version__ in body
    assert "python:" in body
    assert "SECRETVALUE123" not in body
    assert "[REDACTED]" in body


@pytest.mark.asyncio
async def test_non_admin_forbidden(app, client):
    app.dependency_overrides[current_user] = lambda: CurrentUser(user_id="bob", is_admin=False)
    try:
        for path in (
            "/api/system-logs/sources",
            "/api/system-logs/controller",
            "/api/system-logs/bundle",
        ):
            resp = await client.get(path)
            assert resp.status_code == 403, path
    finally:
        app.dependency_overrides.pop(current_user, None)
