import pytest


@pytest.mark.asyncio
async def test_library_source_filter_builtin(client):
    resp = await client.get("/api/personas/library?source=builtin")
    assert resp.status_code == 200
    data = resp.json()
    assert "personas" in data
    assert all(p["source"] == "builtin" for p in data["personas"])
    assert any(p["source"] == "builtin" for p in data["personas"])


@pytest.mark.asyncio
async def test_library_combines_builtin_and_user(client):
    # Create a user persona
    r = await client.post("/api/user-personas", json={"name": "Mine", "soul_md": "my soul"})
    assert r.status_code == 201

    # All sources (no filter)
    resp = await client.get("/api/personas/library")
    assert resp.status_code == 200
    personas = resp.json()["personas"]
    sources = {p["source"] for p in personas}
    assert "builtin" in sources
    assert "user" in sources
    assert any(p["name"] == "Mine" for p in personas)


@pytest.mark.asyncio
async def test_library_user_source_filter(client):
    r = await client.post("/api/user-personas", json={"name": "Mine", "soul_md": "s"})
    assert r.status_code == 201

    resp = await client.get("/api/personas/library?source=user")
    assert resp.status_code == 200
    personas = resp.json()["personas"]
    assert all(p["source"] == "user" for p in personas)
    assert any(p["name"] == "Mine" for p in personas)


@pytest.mark.asyncio
async def test_library_unknown_source_returns_400(client):
    resp = await client.get("/api/personas/library?source=nonexistent")
    assert resp.status_code == 400
    assert "unknown source" in resp.json()["error"]


@pytest.mark.asyncio
async def test_library_q_filter(client):
    await client.post("/api/user-personas", json={"name": "AlphaBot", "soul_md": "alpha content"})
    await client.post("/api/user-personas", json={"name": "BetaBot", "soul_md": "beta content"})

    resp = await client.get("/api/personas/library?source=user&q=alpha")
    assert resp.status_code == 200
    personas = resp.json()["personas"]
    assert all("alpha" in (p["name"] + (p["description"] or "") + p["preview"]).lower() for p in personas)
    assert any(p["name"] == "AlphaBot" for p in personas)
    assert not any(p["name"] == "BetaBot" for p in personas)


@pytest.mark.asyncio
async def test_library_q_filter_case_insensitive(client):
    await client.post("/api/user-personas", json={"name": "CaseSensitiveTest", "soul_md": "UPPERCASE content"})

    resp = await client.get("/api/personas/library?source=user&q=uppercase")
    assert resp.status_code == 200
    assert any(p["name"] == "CaseSensitiveTest" for p in resp.json()["personas"])


@pytest.mark.asyncio
async def test_library_pagination(client):
    # Builtin templates provide enough items; test against builtin only
    resp_all = await client.get("/api/personas/library?source=builtin")
    assert resp_all.status_code == 200
    total = resp_all.json()["total"]
    assert total >= 10, "need at least 10 builtin templates for pagination test"

    resp1 = await client.get("/api/personas/library?source=builtin&limit=5&offset=0")
    assert resp1.status_code == 200
    page1 = resp1.json()["personas"]
    assert len(page1) == 5

    resp2 = await client.get("/api/personas/library?source=builtin&limit=5&offset=5")
    assert resp2.status_code == 200
    page2 = resp2.json()["personas"]
    assert len(page2) == 5

    # Pages must not overlap
    ids1 = {p["id"] for p in page1}
    ids2 = {p["id"] for p in page2}
    assert ids1.isdisjoint(ids2)


@pytest.mark.asyncio
async def test_library_response_shape(client):
    resp = await client.get("/api/personas/library?source=builtin&limit=1")
    assert resp.status_code == 200
    p = resp.json()["personas"][0]
    assert set(p.keys()) >= {"source", "id", "name", "description", "preview"}
    assert p["source"] == "builtin"
    assert isinstance(p["preview"], str)
    assert len(p["preview"]) <= 120


@pytest.mark.asyncio
async def test_library_external_sources_populated(client):
    # Vendored persona files ship with the repo — both sources should surface items.
    for src in ("awesome-openclaw", "prompt-library"):
        resp = await client.get(f"/api/personas/library?source={src}&limit=500")
        assert resp.status_code == 200
        personas = resp.json()["personas"]
        assert len(personas) > 0, f"expected vendored personas for source={src}"
        assert all(p["source"] == src for p in personas)
