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
async def test_select_options_without_value_default_to_label(client):
    # An agent may declare options with only a label (value is optional in the
    # API). Every option must still get a distinct, non-null value, otherwise
    # the inbox cannot tell them apart (a multi_select would check all at once)
    # and answer validation silently no-ops on an empty valid set.
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "pick apps", "type": "multi_select",
        "options": [{"label": "Images"}, {"label": "Observatory"}, {"label": "Decisions"}],
    })
    assert resp.status_code == 200
    d = resp.json()
    values = [o["value"] for o in d["options"]]
    assert values == ["Images", "Observatory", "Decisions"]
    # Answer validation now has a populated valid set keyed on those values.
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": ["Images", "Decisions"]})
    assert resp.status_code == 200
    resp2 = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "pick one", "type": "single_select",
        "options": [{"label": "A"}, {"label": "B"}],
    })
    d2 = resp2.json()
    # A label that was not declared is still rejected.
    bad = await client.post(f"/api/decisions/{d2['id']}/answer", json={"value": "C"})
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_duplicate_labels_get_distinct_values(client):
    # Two value-less options sharing a label must NOT collapse to one identity,
    # otherwise the multi_select would check both at once again. Collisions are
    # disambiguated with a suffix so every option keeps a distinct value.
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "pick", "type": "multi_select",
        "options": [{"label": "Other"}, {"label": "Other"}, {"label": "Other"}],
    })
    assert resp.status_code == 200
    d = resp.json()
    values = [o["value"] for o in d["options"]]
    assert len(set(values)) == 3, f"values must be distinct, got {values}"
    # Selecting one colliding option does not implicitly select the others.
    one = values[0]
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": [one]})
    assert resp.status_code == 200
    assert resp.json()["answer"]["value"] == [one]


@pytest.mark.asyncio
async def test_answer_routes_back_to_bus_agent(client, monkeypatch):
    import tinyagentos.routes.decisions as dmod

    posted = {}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            posted["url"] = url
            posted["json"] = json
            return None

    monkeypatch.setattr(dmod.httpx, "AsyncClient", _FakeClient)

    resp = await client.post("/api/decisions", json={
        "from_agent": "@taOSmd-dev", "question": "Use arctic?", "type": "approve_deny",
    })
    d = resp.json()
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "approve"})
    assert resp.status_code == 200
    # The answer was routed back to the asking agent on the 'decisions' thread.
    assert posted["url"].endswith("/a2a/send")
    assert posted["json"]["thread"] == "decisions"
    assert "@taOSmd-dev" in posted["json"]["body"]
    assert "approve" in posted["json"]["body"]


@pytest.mark.asyncio
async def test_answer_succeeds_when_bus_unreachable(client, monkeypatch):
    import tinyagentos.routes.decisions as dmod

    class _BrokenClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise RuntimeError("bus down")

    monkeypatch.setattr(dmod.httpx, "AsyncClient", _BrokenClient)

    resp = await client.post("/api/decisions", json={
        "from_agent": "@taOSmd-dev", "question": "q", "type": "approve_deny",
    })
    d = resp.json()
    # Best-effort delivery: a bus failure must not fail the answer.
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "deny"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "answered"


@pytest.mark.asyncio
async def test_answer_no_route_for_non_agent_sender(client, monkeypatch):
    import tinyagentos.routes.decisions as dmod

    calls = {"n": 0}

    class _CountingClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            calls["n"] += 1
            return None

    monkeypatch.setattr(dmod.httpx, "AsyncClient", _CountingClient)

    # from_agent without a leading @ is not an agent handle -> no bus post.
    resp = await client.post("/api/decisions", json={
        "from_agent": "system", "question": "q", "type": "free_text",
    })
    d = resp.json()
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "ok"})
    assert resp.status_code == 200
    assert calls["n"] == 0


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
