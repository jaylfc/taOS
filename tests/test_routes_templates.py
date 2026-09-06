import pytest
from tinyagentos.agent_templates import list_templates, get_template, CATEGORIES, TEMPLATES, EXTERNAL_SOURCES, template_stats


class TestAgentTemplates:
    def test_list_builtin_templates(self):
        templates = list_templates(source="builtin")
        assert len(templates) == 12
        assert all("id" in t for t in templates)

    def test_list_all_includes_vendored(self):
        templates = list_templates()
        # 12 builtin + 196 openclaw + 1259 system-prompts = 1467
        assert len(templates) > 100

    def test_template_stats(self):
        stats = template_stats()
        assert stats["builtin"] == 12
        assert stats["vendored"] > 100
        assert stats["total"] == stats["builtin"] + stats["vendored"]

    def test_filter_by_source(self):
        openclaw = list_templates(source="awesome-openclaw-agents")
        assert len(openclaw) > 50
        assert all(t.get("source") == "awesome-openclaw-agents" for t in openclaw)

    def test_list_by_category(self):
        dev_templates = list_templates(category="development")
        assert len(dev_templates) >= 1
        assert all(t["category"] == "development" for t in dev_templates)

    def test_get_template(self):
        tmpl = get_template("research-assistant")
        assert tmpl is not None
        assert tmpl["name"] == "Research Assistant"
        assert tmpl["system_prompt"] != ""

    def test_get_vendored_template(self):
        tmpl = get_template("openclaw-discord-business")
        assert tmpl is not None
        assert tmpl["source"] == "awesome-openclaw-agents"

    def test_get_nonexistent(self):
        assert get_template("nonexistent") is None

    def test_categories_list(self):
        assert "development" in CATEGORIES
        assert "research" in CATEGORIES

    def test_all_templates_have_required_fields(self):
        required = {"id", "name", "category", "description", "system_prompt", "color"}
        for tmpl in TEMPLATES:
            missing = required - set(tmpl.keys())
            assert not missing, f"Template '{tmpl['id']}' missing fields: {missing}"


class TestTemplateRoutes:
    @pytest.mark.asyncio
    async def test_list_templates_api(self, client):
        resp = await client.get("/api/templates?limit=50")
        assert resp.status_code == 200
        data = resp.json()
        assert "templates" in data
        assert "categories" in data
        assert "total" in data
        assert data["total"] > 100  # builtin + vendored
        assert len(data["templates"]) == 50  # paginated

    @pytest.mark.asyncio
    async def test_list_templates_builtin_only(self, client):
        resp = await client.get("/api/templates?source=builtin")
        assert resp.status_code == 200
        assert resp.json()["total"] == 12

    @pytest.mark.asyncio
    async def test_template_stats_api(self, client):
        resp = await client.get("/api/templates/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["builtin"] == 12
        assert data["total"] > 100

    @pytest.mark.asyncio
    async def test_filter_by_category(self, client):
        resp = await client.get("/api/templates?category=development")
        assert resp.status_code == 200
        templates = resp.json()["templates"]
        assert all(t["category"] == "development" for t in templates)

    @pytest.mark.asyncio
    async def test_get_template_api(self, client):
        resp = await client.get("/api/templates/research-assistant")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Research Assistant"

    @pytest.mark.asyncio
    async def test_get_template_not_found(self, client):
        resp = await client.get("/api/templates/nonexistent")
        assert resp.status_code == 404


class TestExternalSources:
    def test_external_sources_defined(self):
        from tinyagentos.agent_templates import EXTERNAL_SOURCES
        assert len(EXTERNAL_SOURCES) == 2
        ids = [s["id"] for s in EXTERNAL_SOURCES]
        assert "awesome-openclaw" in ids
        assert "system-prompt-library" in ids

    @pytest.mark.asyncio
    async def test_list_sources_api(self, client):
        resp = await client.get("/api/templates/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert "sources" in data
        assert len(data["sources"]) == 2
        # IDs must be the UI aliases so clients can round-trip into
        # /api/personas/library?source=… without a 400.
        ids = {s["id"] for s in data["sources"]}
        assert ids == {"awesome-openclaw", "prompt-library"}

    @pytest.mark.asyncio
    async def test_external_index_unknown_source(self, client):
        resp = await client.get("/api/templates/external/nonexistent")
        assert resp.status_code == 200
        assert resp.json()["templates"] == []

    @pytest.mark.asyncio
    async def test_fetch_external_no_path(self, client):
        resp = await client.get("/api/templates/external/awesome-openclaw/fetch")
        assert resp.status_code == 400
