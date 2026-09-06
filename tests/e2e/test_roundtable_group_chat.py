"""End-to-end: verify multi-agent context threading lands.

Marked `slow` and requires a Pi fixture with at least 2 live agents.
Skipped in local CI; run with `pytest -m slow` against a live stack.
"""
import os
import pytest
import httpx

PI_URL = os.environ.get("TAOS_E2E_URL")
PI_TOKEN = os.environ.get("TAOS_E2E_TOKEN")
CHANNEL_ID = os.environ.get("TAOS_E2E_CHANNEL")

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (PI_URL and PI_TOKEN and CHANNEL_ID),
                       reason="E2E requires TAOS_E2E_URL/TOKEN/CHANNEL"),
]


@pytest.mark.asyncio
async def test_agents_reference_each_other_in_lively_roundtable():
    headers = {"Authorization": f"Bearer {PI_TOKEN}"}
    async with httpx.AsyncClient(base_url=PI_URL, headers=headers, timeout=60) as c:
        # Force channel into lively mode via the admin endpoint
        r = await c.patch(f"/api/chat/channels/{CHANNEL_ID}", json={"response_mode": "lively"})
        assert r.status_code < 300

        # Post the addressed prompt. We'll use its id as the trace_id to filter replies.
        r = await c.post("/api/chat/messages", json={
            "channel_id": CHANNEL_ID, "author_id": "user",
            "author_type": "user",
            "content": "@all please introduce yourselves in one sentence. Mention at least one other agent by name in your reply.",
            "content_type": "text",
        })
        assert r.status_code < 300
        sent_id = r.json().get("id")
        assert sent_id, "server did not return message id"

        import asyncio
        for _ in range(40):
            r = await c.get(f"/api/chat/channels/{CHANNEL_ID}/messages?limit=50")
            msgs = r.json().get("messages", [])
            mine = [m for m in msgs
                    if m.get("author_type") == "agent"
                    and (m.get("metadata") or {}).get("trace_id") == sent_id]
            names = {m["author_id"] for m in mine}
            cross_refs = sum(
                1 for m in mine
                if any(other in (m.get("content") or "") for other in names if other != m["author_id"])
            )
            if len(mine) >= 3 and cross_refs >= 1:
                return
            await asyncio.sleep(3)
        pytest.fail("No agents referenced each other after 2 minutes")
