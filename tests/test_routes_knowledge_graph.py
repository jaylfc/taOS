"""Endpoint tests for tinyagentos/routes/knowledge_graph.py."""

from __future__ import annotations

import pytest
import pytest_asyncio

from taosmd import KnowledgeGraph as TemporalKnowledgeGraph


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _init_knowledge_graph(app, tmp_data_dir):
    """The client fixture does not init knowledge_graph (lifespan is skipped).

    Create and attach a fresh TemporalKnowledgeGraph for each test so
    routes that read request.app.state.knowledge_graph work correctly."""
    kg = TemporalKnowledgeGraph(db_path=tmp_data_dir / "knowledge-graph.db")
    await kg.init()
    app.state.knowledge_graph = kg
    yield
    await kg.close()


class TestAddEntity:
    async def test_add_entity_returns_id_and_status(self, client):
        resp = await client.post(
            "/api/kg/entities",
            json={"name": "Alice", "type": "person", "properties": '{"age": 30}'},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["status"] == "ok"

    async def test_add_entity_minimal_body(self, client):
        resp = await client.post("/api/kg/entities", json={"name": "Bob"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestListEntities:
    async def test_list_entities_returns_list_and_count(self, client):
        await client.post("/api/kg/entities", json={"name": "Carol"})
        resp = await client.get("/api/kg/entities")
        assert resp.status_code == 200
        data = resp.json()
        assert "entities" in data
        assert "count" in data
        assert isinstance(data["entities"], list)
        assert data["count"] == len(data["entities"])

    async def test_list_entities_filter_by_type(self, client):
        await client.post(
            "/api/kg/entities",
            json={"name": "Widget", "type": "object"},
        )
        resp = await client.get("/api/kg/entities", params={"type": "object"})
        assert resp.status_code == 200
        for ent in resp.json()["entities"]:
            assert ent["type"] == "object"


class TestGetEntity:
    async def test_get_entity_returns_entity(self, client):
        await client.post("/api/kg/entities", json={"name": "Dave", "type": "person"})
        resp = await client.get("/api/kg/entities/Dave")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Dave"
        assert data["type"] == "person"

    async def test_get_entity_not_found_returns_404(self, client):
        resp = await client.get("/api/kg/entities/NoSuchEntity")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not found"


class TestAddTriple:
    async def test_add_triple_returns_id_and_status(self, client):
        await client.post("/api/kg/entities", json={"name": "Eve"})
        await client.post("/api/kg/entities", json={"name": "Frank"})
        resp = await client.post(
            "/api/kg/triples",
            json={"subject": "Eve", "predicate": "knows", "object": "Frank"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["status"] == "ok"

    async def test_add_triple_with_metadata(self, client):
        await client.post("/api/kg/entities", json={"name": "Grace"})
        await client.post("/api/kg/entities", json={"name": "Heidi"})
        resp = await client.post(
            "/api/kg/triples",
            json={
                "subject": "Grace",
                "predicate": "works_with",
                "object": "Heidi",
                "confidence": 0.9,
                "source": "test",
                "subject_type": "person",
                "object_type": "person",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestInvalidateTriple:
    async def test_invalidate_returns_status_invalidated(self, client):
        await client.post("/api/kg/entities", json={"name": "Ivan"})
        await client.post("/api/kg/entities", json={"name": "Judy"})
        triple_resp = await client.post(
            "/api/kg/triples",
            json={"subject": "Ivan", "predicate": "knows", "object": "Judy"},
        )
        triple_id = triple_resp.json()["id"]
        resp = await client.post(
            "/api/kg/triples/invalidate",
            json={"triple_id": triple_id},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "invalidated"

    async def test_invalidate_unknown_id_returns_404(self, client):
        resp = await client.post(
            "/api/kg/triples/invalidate",
            json={"triple_id": "does-not-exist"},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["error"]


class TestUpdateFact:
    async def test_update_fact_returns_id_and_status(self, client):
        await client.post("/api/kg/entities", json={"name": "Karl"})
        await client.post("/api/kg/entities", json={"name": "Lisa"})
        await client.post("/api/kg/entities", json={"name": "Mona"})
        await client.post(
            "/api/kg/triples",
            json={"subject": "Karl", "predicate": "knows", "object": "Lisa"},
        )
        resp = await client.post(
            "/api/kg/triples/update",
            json={
                "subject": "Karl",
                "predicate": "knows",
                "old_object": "Lisa",
                "new_object": "Mona",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["status"] == "updated"


class TestQueryEntity:
    async def test_query_entity_returns_results(self, client):
        await client.post("/api/kg/entities", json={"name": "Nina"})
        await client.post("/api/kg/entities", json={"name": "Oscar"})
        await client.post(
            "/api/kg/triples",
            json={"subject": "Nina", "predicate": "knows", "object": "Oscar"},
        )
        resp = await client.get("/api/kg/query/Nina")
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "count" in data
        assert data["count"] >= 1

    async def test_query_entity_unknown_returns_empty(self, client):
        resp = await client.get("/api/kg/query/NoOne")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


class TestQueryPredicate:
    async def test_query_predicate_returns_results(self, client):
        await client.post("/api/kg/entities", json={"name": "Pat"})
        await client.post("/api/kg/entities", json={"name": "Quinn"})
        await client.post(
            "/api/kg/triples",
            json={"subject": "Pat", "predicate": "knows", "object": "Quinn"},
        )
        resp = await client.get("/api/kg/query/predicate/knows")
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert data["count"] >= 1

    async def test_query_predicate_unknown_returns_empty(self, client):
        resp = await client.get("/api/kg/query/predicate/nonexistent")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


class TestTimeline:
    async def test_timeline_returns_events(self, client):
        await client.post("/api/kg/entities", json={"name": "Rita"})
        resp = await client.get("/api/kg/timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data
        assert "count" in data

    async def test_timeline_with_name_filter(self, client):
        await client.post("/api/kg/entities", json={"name": "Sam"})
        await client.post("/api/kg/entities", json={"name": "Tina"})
        await client.post(
            "/api/kg/triples",
            json={"subject": "Sam", "predicate": "knows", "object": "Tina"},
        )
        resp = await client.get("/api/kg/timeline", params={"name": "Sam"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1


class TestStats:
    async def test_stats_returns_entity_and_triple_counts(self, client):
        resp = await client.get("/api/kg/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "entities" in data
        assert "triples" in data
        assert isinstance(data["entities"], int)
        assert isinstance(data["triples"], int)


class TestClassify:
    async def test_classify_returns_type(self, client):
        resp = await client.post(
            "/api/kg/classify",
            json={"text": "The capital of France is Paris"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "type" in data
        assert "text" in data
        assert data["type"] in ("fact", "reflection", "preference", "meta")

    async def test_classify_empty_text(self, client):
        resp = await client.post("/api/kg/classify", json={"text": ""})
        assert resp.status_code == 200
        assert "type" in resp.json()
