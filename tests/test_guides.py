"""Coverage for GET /api/guides/recommendations, /api/guides/tiers, /api/guides/use-cases."""

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app

# Test data matching the structure of the real guides.yaml
TEST_GUIDES = {
    "hardware_tiers": {
        "pi-16gb": {"label": "Pi 16GB", "description": "ARM SBC", "icon": "cpu"},
        "nvidia-12gb": {"label": "NVIDIA 12GB", "description": "GPU", "icon": "monitor"},
    },
    "use_cases": {
        "chat": {"label": "Chat", "description": "Conversation", "icon": "message-circle"},
        "coding": {"label": "Coding", "description": "Code gen", "icon": "code"},
    },
    "recommendations": {
        "pi-16gb": {
            "chat": [
                {"model": "Test Model 1", "reason": "Best for Pi", "note": "Fast"},
                {"model": "Test Model 2", "reason": "Good fallback"},
            ],
            "coding": [
                {"model": "Test Coder", "reason": "Decent at code"},
            ],
        },
        "nvidia-12gb": {
            "chat": [
                {"model": "GPU Model", "reason": "GPU accelerated"},
            ],
        },
    },
}


def _make_test_app(tmp_path, *, with_guides=True):
    """Create a test app with config, .setup_complete, and optionally guides.yaml."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [
            {"name": "test-backend", "type": "rkllama", "url": "http://localhost:8080", "priority": 1}
        ],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
    }
    (data_dir / "config.yaml").write_text(yaml.dump(config))
    (data_dir / ".setup_complete").touch()
    if with_guides:
        (data_dir / "guides.yaml").write_text(yaml.dump(TEST_GUIDES))
    return create_app(data_dir=data_dir)


@pytest_asyncio.fixture
async def client(tmp_path):
    """Async HTTP test client with auth and guides.yaml."""
    app = _make_test_app(tmp_path, with_guides=True)
    # Setup auth user (same as conftest.py)
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def client_no_guides(tmp_path):
    """Async HTTP test client WITHOUT guides.yaml."""
    app = _make_test_app(tmp_path, with_guides=False)
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as ac:
        yield ac


@pytest.mark.asyncio
class TestTiersEndpoint:
    async def test_returns_tiers(self, client):
        r = await client.get("/api/guides/tiers")
        assert r.status_code == 200
        data = r.json()
        assert "tiers" in data
        assert "pi-16gb" in data["tiers"]
        assert data["tiers"]["pi-16gb"]["label"] == "Pi 16GB"

    async def test_tiers_structure(self, client):
        r = await client.get("/api/guides/tiers")
        data = r.json()
        for tier in data["tiers"].values():
            assert "label" in tier
            assert "description" in tier


@pytest.mark.asyncio
class TestUseCasesEndpoint:
    async def test_returns_use_cases(self, client):
        r = await client.get("/api/guides/use-cases")
        assert r.status_code == 200
        data = r.json()
        assert "use_cases" in data
        assert "chat" in data["use_cases"]
        assert data["use_cases"]["chat"]["label"] == "Chat"


@pytest.mark.asyncio
class TestRecommendationsEndpoint:
    async def test_valid_query_returns_recommendations(self, client):
        r = await client.get("/api/guides/recommendations?hardware=pi-16gb&use_case=chat")
        assert r.status_code == 200
        data = r.json()
        assert data["hardware"] == "pi-16gb"
        assert data["use_case"] == "chat"
        assert len(data["recommendations"]) == 2
        assert data["recommendations"][0]["model"] == "Test Model 1"
        assert data["recommendations"][0]["reason"] == "Best for Pi"
        assert data["recommendations"][0]["note"] == "Fast"
        assert "note" not in data["recommendations"][1]

    async def test_different_use_case(self, client):
        r = await client.get("/api/guides/recommendations?hardware=pi-16gb&use_case=coding")
        assert r.status_code == 200
        data = r.json()
        assert len(data["recommendations"]) == 1
        assert data["recommendations"][0]["model"] == "Test Coder"

    async def test_different_hardware(self, client):
        r = await client.get("/api/guides/recommendations?hardware=nvidia-12gb&use_case=chat")
        assert r.status_code == 200
        data = r.json()
        assert len(data["recommendations"]) == 1
        assert data["recommendations"][0]["model"] == "GPU Model"

    async def test_unknown_hardware_returns_404(self, client):
        r = await client.get("/api/guides/recommendations?hardware=mega-cluster&use_case=chat")
        assert r.status_code == 404
        data = r.json()
        assert "detail" in data
        assert "mega-cluster" in data["detail"]

    async def test_unknown_use_case_returns_404(self, client):
        r = await client.get("/api/guides/recommendations?hardware=pi-16gb&use_case=mining")
        assert r.status_code == 404
        data = r.json()
        assert "detail" in data
        assert "mining" in data["detail"]

    async def test_missing_hardware_param_returns_422(self, client):
        r = await client.get("/api/guides/recommendations?use_case=chat")
        assert r.status_code == 422

    async def test_missing_use_case_param_returns_422(self, client):
        r = await client.get("/api/guides/recommendations?hardware=pi-16gb")
        assert r.status_code == 422

    async def test_empty_recommendations(self, client):
        """Tier with no matching use case returns 404, not empty list."""
        r = await client.get("/api/guides/recommendations?hardware=nvidia-12gb&use_case=coding")
        assert r.status_code == 404


@pytest.mark.asyncio
class TestGuidesYamlMissing:
    async def test_tiers_returns_empty_when_no_yaml(self, client_no_guides):
        r = await client_no_guides.get("/api/guides/tiers")
        assert r.status_code == 200
        assert r.json() == {"tiers": {}}

    async def test_use_cases_returns_empty_when_no_yaml(self, client_no_guides):
        r = await client_no_guides.get("/api/guides/use-cases")
        assert r.status_code == 200
        assert r.json() == {"use_cases": {}}

    async def test_recommendations_404_when_no_yaml(self, client_no_guides):
        r = await client_no_guides.get("/api/guides/recommendations?hardware=pi-16gb&use_case=chat")
        assert r.status_code == 404
