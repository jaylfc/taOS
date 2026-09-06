"""Route tests for POST/GET /api/feedback endpoints."""
import pytest


class TestFeedbackRoutes:
    @pytest.mark.asyncio
    async def test_post_feedback_returns_201_with_id(self, client):
        resp = await client.post(
            "/api/feedback",
            json={"type": "bug", "title": "Login fails", "body": "Cannot sign in"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["type"] == "bug"
        assert data["title"] == "Login fails"
        assert "created_at" in data

    @pytest.mark.asyncio
    async def test_get_feedback_includes_posted_item(self, client):
        posted = await client.post(
            "/api/feedback",
            json={"type": "feature", "title": "Add dark mode", "body": ""},
        )
        item_id = posted.json()["id"]

        resp = await client.get("/api/feedback")
        assert resp.status_code == 200
        items = resp.json()
        ids = [item["id"] for item in items]
        assert item_id in ids

    @pytest.mark.asyncio
    async def test_get_feedback_unknown_id_returns_404(self, client):
        resp = await client.get("/api/feedback/does-not-exist")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_post_feedback_missing_required_field_returns_422(self, client):
        resp = await client.post(
            "/api/feedback",
            json={"title": "Missing type field"},
        )
        assert resp.status_code == 422
