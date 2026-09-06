import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_get_framework_state(client, app):
    app.state.config.agents.append({
        "name": "atlas-fw", "framework": "openclaw",
        "framework_version_tag": "T1", "framework_version_sha": "a1a1a1a",
        "framework_update_status": "idle",
    })
    app.state.latest_framework_versions = {
        "openclaw": {"tag": "T2", "sha": "b2b2b2b", "published_at": "x", "asset_url": "u"},
    }
    r = await client.get("/api/agents/atlas-fw/framework")
    assert r.status_code == 200
    body = r.json()
    assert body["framework"] == "openclaw"
    assert body["installed"]["sha"] == "a1a1a1a"
    assert body["latest"]["sha"] == "b2b2b2b"
    assert body["update_available"] is True
    assert body["update_status"] == "idle"


@pytest.mark.asyncio
async def test_get_framework_404(client):
    r = await client.get("/api/agents/nope-fw/framework")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_framework_no_latest_when_source_missing(client, app):
    app.state.config.agents.append({
        "name": "bob-fw", "framework": "legacy",
        "framework_version_tag": None, "framework_version_sha": None,
        "framework_update_status": "idle",
    })
    app.state.latest_framework_versions = {}
    r = await client.get("/api/agents/bob-fw/framework")
    assert r.json()["latest"] is None
    assert r.json()["update_available"] is False


@pytest.mark.asyncio
async def test_post_update_kicks_off_task(client, app, monkeypatch):
    # The GitHub-asset update route needs a framework with a release_source.
    # OpenClaw is npm-based now (no release_source), so use a synthetic
    # release-sourced framework to exercise the route mechanism generically.
    from tinyagentos import frameworks as fw_mod
    monkeypatch.setitem(fw_mod.FRAMEWORKS, "fwupd", {
        "id": "fwupd", "name": "FwUpd",
        "release_source": "github:example/fwupd",
        "release_asset_pattern": "fwupd-{arch}.tgz",
        "install_script": "/usr/local/bin/taos-framework-update",
        "service_name": "fwupd",
    })
    app.state.config.agents.append({
        "name": "atlas-post", "framework": "fwupd",
        "framework_update_status": "idle",
    })
    app.state.latest_framework_versions = {
        "fwupd": {"tag": "T2", "sha": "b2b2b2b", "asset_url": "u"},
    }
    kicked = {}
    async def fake(agent, manifest, latest, *, save_config):
        kicked["ok"] = True
    monkeypatch.setattr("tinyagentos.framework_update.start_update", fake)
    r = await client.post("/api/agents/atlas-post/framework/update", json={})
    assert r.status_code == 202
    import asyncio
    await asyncio.sleep(0.05)
    assert kicked.get("ok") is True


@pytest.mark.asyncio
async def test_post_update_409_when_already_updating(client, app):
    app.state.config.agents.append({
        "name": "atlas-busy", "framework": "openclaw",
        "framework_update_status": "updating",
    })
    app.state.latest_framework_versions = {
        "openclaw": {"tag": "T", "sha": "s", "asset_url": "u"},
    }
    r = await client.post("/api/agents/atlas-busy/framework/update", json={})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_post_update_400_unknown_target(client, app):
    app.state.config.agents.append({
        "name": "atlas-bad-target", "framework": "openclaw",
        "framework_update_status": "idle",
    })
    app.state.latest_framework_versions = {
        "openclaw": {"tag": "T2", "sha": "s", "asset_url": "u"},
    }
    r = await client.post("/api/agents/atlas-bad-target/framework/update",
                           json={"target_version": "NONE"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_get_latest_returns_cache(client, app):
    app.state.latest_framework_versions = {"openclaw": {"tag": "T", "sha": "s"}}
    r = await client.get("/api/frameworks/latest")
    assert r.status_code == 200
    assert r.json()["openclaw"]["tag"] == "T"


@pytest.mark.asyncio
async def test_get_latest_refresh_triggers_poll(client, app, monkeypatch):
    app.state.latest_framework_versions = {}
    async def fake_poll(manifests, *, http_client, arch, cache):
        cache["openclaw"] = {"tag": "FRESH", "sha": "s"}
    monkeypatch.setattr("tinyagentos.auto_update.poll_frameworks", fake_poll)
    r = await client.get("/api/frameworks/latest?refresh=true")
    assert r.status_code == 200
    assert r.json()["openclaw"]["tag"] == "FRESH"
