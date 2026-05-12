import pytest


@pytest.mark.asyncio
async def test_openapi_documents_agents_list(client):
    resp = await client.get("/openapi.json")
    spec = resp.json()
    op = spec["paths"]["/api/agents"]["get"]
    assert op.get("summary"), "GET /api/agents missing summary"
    assert op.get("description"), "GET /api/agents missing description"
    assert "200" in op["responses"]


@pytest.mark.asyncio
async def test_openapi_documents_agents_create_example(client):
    resp = await client.get("/openapi.json")
    spec = resp.json()
    op = spec["paths"]["/api/agents"]["post"]
    body_content = op["requestBody"]["content"]["application/json"]
    schema_ref = body_content.get("schema", {}).get("$ref", "")
    has_inline_example = "example" in body_content or "examples" in body_content
    has_schema_example = False
    if schema_ref:
        schema_name = schema_ref.split("/")[-1]
        schema = spec["components"]["schemas"].get(schema_name, {})
        has_schema_example = "example" in schema or "examples" in schema
    assert has_inline_example or has_schema_example, "POST /api/agents missing request body example"


@pytest.mark.asyncio
async def test_openapi_documents_404_on_get_agent(client):
    resp = await client.get("/openapi.json")
    spec = resp.json()
    op = spec["paths"]["/api/agents/{name}"]["get"]
    assert "404" in op["responses"]
