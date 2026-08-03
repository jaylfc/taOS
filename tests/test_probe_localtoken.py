import pytest


@pytest.mark.asyncio
async def test_probe_local_token_on_create(client, app):
    """Determine CURRENT status of a local-token bearer on POST /api/decisions,
    so we do not regress it."""
    local_token = app.state.auth.get_local_token()
    resp = await client.post(
        "/api/decisions",
        json={"from_agent": "@a", "question": "q", "type": "free_text"},
        headers={"Authorization": f"Bearer {local_token}"},
    )
    print("LOCAL-TOKEN bearer on create ->", resp.status_code, resp.text)
