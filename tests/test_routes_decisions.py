import pytest


@pytest.mark.asyncio
async def test_post_list_get_answer_flow(client):
    resp = await client.post("/api/decisions", json={
        "from_agent": "@taOS-dev",
        "question": "Which engine?",
        "type": "single_select",
        "options": [{"label": "Excalidraw", "value": "excalidraw", "recommended": True, "rationale": "MIT"},
                    {"label": "Konva", "value": "konva"}],
        "context": "canvas replacement",
        "project_id": "prj-x",
    })
    assert resp.status_code == 200
    d = resp.json()
    assert d["id"].startswith("dec-") and d["status"] == "pending"

    resp = await client.get("/api/decisions?status=pending")
    assert resp.status_code == 200
    assert any(x["id"] == d["id"] for x in resp.json()["items"])

    resp = await client.get(f"/api/decisions/{d['id']}")
    assert resp.status_code == 200
    assert resp.json()["question"] == "Which engine?"

    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "excalidraw"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "answered"
    assert resp.json()["answer"]["value"] == "excalidraw"

    # cannot answer twice
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "konva"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_select_requires_options(client):
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "single_select", "options": [],
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_free_text_and_approve_deny_need_no_options(client):
    for t in ("free_text", "approve_deny"):
        resp = await client.post("/api/decisions", json={"from_agent": "@a", "question": "q", "type": t})
        assert resp.status_code == 200, t


@pytest.mark.asyncio
async def test_invalid_type_400(client):
    resp = await client.post("/api/decisions", json={"from_agent": "@a", "question": "q", "type": "nope"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_unknown_404(client):
    resp = await client.get("/api/decisions/dec-missing")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_answer_must_match_options(client):
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "single_select",
        "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
    })
    d = resp.json()
    # A value outside the declared options is rejected.
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "zzz"})
    assert resp.status_code == 400
    # The decision is still answerable with a valid option.
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "a"})
    assert resp.status_code == 200
    assert resp.json()["answer"]["value"] == "a"


@pytest.mark.asyncio
async def test_multi_select_answer_must_be_subset(client):
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "multi_select",
        "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
    })
    d = resp.json()
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": ["a", "nope"]})
    assert resp.status_code == 400
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": ["a", "b"]})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_with_parent_supersedes_it(client):
    # Original decision.
    r = await client.post("/api/decisions", json={
        "from_agent": "@taOS-dev", "question": "Canvas engine?", "type": "single_select",
        "options": [{"label": "Konva", "value": "konva"}], "project_id": "prj-x",
    })
    old = r.json()
    # A replacement that revisits it going forward.
    r = await client.post("/api/decisions", json={
        "from_agent": "@taOS-dev", "question": "Canvas engine (revisited)?", "type": "single_select",
        "options": [{"label": "Excalidraw", "value": "excalidraw"}], "project_id": "prj-x",
        "parent_decision_id": old["id"],
    })
    new = r.json()
    assert r.status_code == 200
    assert new["parent_decision_id"] == old["id"]
    # The old decision is now superseded.
    r = await client.get(f"/api/decisions/{old['id']}")
    assert r.json()["status"] == "superseded"


@pytest.mark.asyncio
async def test_create_with_unknown_parent_rejected(client):
    r = await client.post("/api/decisions", json={
        "from_agent": "@taOS-dev", "question": "q", "type": "free_text",
        "parent_decision_id": "dec-missing",
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_history_returns_lineage_oldest_first(client):
    ids = []
    parent = None
    for i in range(3):
        body = {"from_agent": "@taOS-dev", "question": f"q{i}", "type": "free_text"}
        if parent:
            body["parent_decision_id"] = parent
        r = await client.post("/api/decisions", json=body)
        parent = r.json()["id"]
        ids.append(parent)
    # History of the newest walks back through both ancestors.
    r = await client.get(f"/api/decisions/{ids[-1]}/history")
    assert r.status_code == 200
    chain = [d["id"] for d in r.json()["items"]]
    assert chain == ids  # oldest first
