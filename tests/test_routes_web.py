import pytest

from tinyagentos.routes.web import MAX_CONTENT_BYTES


@pytest.mark.asyncio
async def test_create_list_get_update_delete_site(client):
    resp = await client.post(
        "/api/web/sites",
        json={"title": "Landing", "content": '{"sections": []}'},
    )
    assert resp.status_code == 200
    created = resp.json()
    site_id = created["id"]
    assert created["title"] == "Landing"
    assert created["content"] == '{"sections": []}'
    assert isinstance(created["updated_at"], int)

    resp = await client.get("/api/web/sites")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["id"] == site_id
    assert "content" not in items[0]

    resp = await client.get(f"/api/web/sites/{site_id}")
    assert resp.status_code == 200
    assert resp.json()["content"] == '{"sections": []}'

    resp = await client.put(
        f"/api/web/sites/{site_id}",
        json={"title": "Landing v2", "content": '{"sections": [1]}'},
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["title"] == "Landing v2"
    assert updated["content"] == '{"sections": [1]}'
    assert updated["updated_at"] >= created["updated_at"]

    resp = await client.delete(f"/api/web/sites/{site_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

    resp = await client.get(f"/api/web/sites/{site_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_rejects_missing_title(client):
    resp = await client.post(
        "/api/web/sites",
        json={"title": "   ", "content": "{}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_missing_returns_404(client):
    resp = await client.get("/api/web/sites/site-missing")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_missing_returns_404(client):
    resp = await client.put(
        "/api/web/sites/site-missing",
        json={"title": "X", "content": "{}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_missing_returns_404(client):
    resp = await client.delete("/api/web/sites/site-missing")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_rejects_malformed_json(client):
    resp = await client.post(
        "/api/web/sites",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_rejects_malformed_json(client):
    resp = await client.put(
        "/api/web/sites/site-missing",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_rejects_oversized_content(client):
    resp = await client.post(
        "/api/web/sites",
        json={"title": "Big", "content": "x" * (MAX_CONTENT_BYTES + 1)},
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_update_rejects_oversized_content(client):
    resp = await client.post(
        "/api/web/sites",
        json={"title": "Landing", "content": "{}"},
    )
    site_id = resp.json()["id"]

    resp = await client.put(
        f"/api/web/sites/{site_id}",
        json={"content": "x" * (MAX_CONTENT_BYTES + 1)},
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_create_and_update_store_index_html(client):
    resp = await client.post(
        "/api/web/sites",
        json={"title": "Landing", "content": "{}", "index_html": "<p>v1</p>"},
    )
    assert resp.status_code == 200
    site_id = resp.json()["id"]
    assert resp.json()["index_html"] == "<p>v1</p>"

    resp = await client.put(
        f"/api/web/sites/{site_id}",
        json={"index_html": "<p>v2</p>"},
    )
    assert resp.status_code == 200
    assert resp.json()["index_html"] == "<p>v2</p>"


@pytest.mark.asyncio
async def test_create_rejects_oversized_index_html(client):
    resp = await client.post(
        "/api/web/sites",
        json={"title": "Landing", "content": "{}", "index_html": "x" * (MAX_CONTENT_BYTES + 1)},
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_create_treats_explicit_null_content_and_index_html_as_empty(client):
    # An explicit JSON null (not just a missing key) must be coerced to "" and
    # NOT rejected as a spurious 400 by the string type-check (Kilo finding).
    resp = await client.post(
        "/api/web/sites",
        json={"title": "Landing", "content": None, "index_html": None},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == ""
    assert body["index_html"] == ""


@pytest.mark.asyncio
async def test_update_treats_explicit_null_as_keep_existing(client):
    resp = await client.post(
        "/api/web/sites",
        json={"title": "Landing", "content": '{"sections": []}', "index_html": "<p>v1</p>"},
    )
    site_id = resp.json()["id"]

    # Explicit nulls must fall back to the existing values, not 400 or wipe them.
    resp = await client.put(
        f"/api/web/sites/{site_id}",
        json={"content": None, "index_html": None},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == '{"sections": []}'
    assert body["index_html"] == "<p>v1</p>"


@pytest.mark.asyncio
class TestWebSitePreview:
    async def test_preview_serves_stored_html_with_csp(self, client):
        resp = await client.post(
            "/api/web/sites",
            json={"title": "Landing", "content": "{}", "index_html": "<!doctype html><p>Hi</p>"},
        )
        site_id = resp.json()["id"]

        resp = await client.get(f"/api/web/sites/{site_id}/preview")
        assert resp.status_code == 200
        assert resp.text == "<!doctype html><p>Hi</p>"
        csp = resp.headers["content-security-policy"]
        assert "sandbox allow-scripts" in csp
        assert "allow-same-origin" not in csp
        assert "default-src 'none'" in csp
        assert resp.headers["x-content-type-options"] == "nosniff"

    async def test_preview_nonexistent_site_404(self, client):
        resp = await client.get("/api/web/sites/site-nope/preview")
        assert resp.status_code == 404

    async def test_preview_unsaved_render_404s(self, client):
        # Created with no index_html yet (matches a pre-migration/legacy row).
        resp = await client.post("/api/web/sites", json={"title": "Landing", "content": "{}"})
        site_id = resp.json()["id"]
        resp = await client.get(f"/api/web/sites/{site_id}/preview")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestWebSitePackage:
    async def test_package_builds_valid_taosapp_zip(self, client):
        resp = await client.post(
            "/api/web/sites",
            json={"title": "My Landing", "content": "{}", "index_html": "<!doctype html><p>Hi</p>"},
        )
        site_id = resp.json()["id"]

        resp = await client.get(f"/api/web/sites/{site_id}/package")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert f"{site_id}.taosapp" in resp.headers["content-disposition"]

        import io
        import zipfile
        import yaml

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        assert set(zf.namelist()) == {"manifest.yaml", "index.html"}
        manifest = yaml.safe_load(zf.read("manifest.yaml").decode())
        assert manifest["id"] == site_id
        assert manifest["name"] == "My Landing"
        assert manifest["app_type"] == "web"
        assert manifest["entry"] == "index.html"
        assert zf.read("index.html").decode() == "<!doctype html><p>Hi</p>"

    async def test_package_nonexistent_site_404(self, client):
        resp = await client.get("/api/web/sites/site-nope/package")
        assert resp.status_code == 404

    async def test_package_unsaved_render_rejected(self, client):
        resp = await client.post("/api/web/sites", json={"title": "Landing", "content": "{}"})
        site_id = resp.json()["id"]
        resp = await client.get(f"/api/web/sites/{site_id}/package")
        assert resp.status_code == 400

    async def test_package_installs_via_userspace_apps_endpoint(self, client):
        """The package this endpoint builds must be installable through the
        existing, unmodified userspace-apps install pipeline -- this is the
        actual reuse the Share flow depends on."""
        app = client._transport.app
        store = app.state.userspace_apps
        if store._db is not None:
            await store.close()
        await store.init()
        data_store = app.state.userspace_data
        if data_store._db is not None:
            await data_store.close()
        await data_store.init()

        resp = await client.post(
            "/api/web/sites",
            json={"title": "Installable", "content": "{}", "index_html": "<!doctype html><p>Hi</p>"},
        )
        site_id = resp.json()["id"]
        pkg_resp = await client.get(f"/api/web/sites/{site_id}/package")
        assert pkg_resp.status_code == 200

        install_resp = await client.post(
            "/api/userspace-apps/install",
            files={"package": (f"{site_id}.taosapp", pkg_resp.content, "application/zip")},
            data={"provenance": "ai-generated"},
        )
        assert install_resp.status_code == 200
        assert install_resp.json()["app_id"] == site_id
