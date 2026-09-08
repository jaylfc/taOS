"""Regression tests for markup injection through the HTMX notification fragment.

``GET /api/notifications`` renders stored ``title``/``message`` values into an
HTML fragment for HTMX requests.  Both fields are attacker-controlled: the
broker access-request route builds its message out of the free-form
``agent_identity`` / ``provider_id`` / ``reason`` an agent posts.  The fragment
must therefore escape at the sink, while the JSON view keeps the raw text.
"""
import asyncio

import pytest

from tinyagentos.cluster.worker_protocol import WorkerInfo

# Markup that executes if it reaches the dashboard unescaped.
SCRIPT_TITLE = "<script>alert(1)</script>"
IMG_PAYLOAD = "<img src=x onerror=alert(1)>"


async def _fragment(client) -> str:
    resp = await client.get("/api/notifications", headers={"hx-request": "true"})
    assert resp.status_code == 200
    return resp.text


@pytest.mark.asyncio
class TestNotificationFragmentEscaping:
    async def test_title_markup_is_escaped(self, client, app):
        await app.state.notifications.add(
            title=SCRIPT_TITLE, message="plain", level="info", source="test"
        )
        body = await _fragment(client)
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
        assert SCRIPT_TITLE not in body

    async def test_message_markup_is_escaped(self, client, app):
        await app.state.notifications.add(
            title="plain", message=IMG_PAYLOAD, level="info", source="test"
        )
        body = await _fragment(client)
        assert "&lt;img src=x onerror=alert(1)&gt;" in body
        assert "<img" not in body

    async def test_broker_access_request_is_escaped_end_to_end(self, client):
        resp = await client.post(
            "/api/broker/request",
            json={
                "provider_id": "exa",
                "agent_identity": "<b>agent-x</b>",
                "reason": IMG_PAYLOAD,
            },
        )
        assert resp.status_code == 200
        body = await _fragment(client)
        assert "<img" not in body
        assert "<b>" not in body
        assert "&lt;img src=x onerror=alert(1)&gt;" in body

    async def test_json_view_keeps_raw_text(self, client, app):
        """Escaping belongs at the HTML sink, not in the store."""
        await app.state.notifications.add(
            title=SCRIPT_TITLE, message=IMG_PAYLOAD, level="info", source="test"
        )
        items = (await client.get("/api/notifications")).json()
        assert items[0]["title"] == SCRIPT_TITLE
        assert items[0]["message"] == IMG_PAYLOAD

    async def test_level_icon_still_rendered_as_markup(self, client, app):
        """The static level icon is trusted markup and must survive escaping."""
        await app.state.notifications.add(
            title="plain", message="plain", level="error", source="test"
        )
        body = await _fragment(client)
        assert "&#x274C;" in body

    async def test_heartbeat_drain_reason_escaped_in_fragment(self, client, app):
        """A worker heartbeat with a draining status and HTML drain_reason must
        render escaped in the HTMX notification fragment."""
        worker = WorkerInfo(
            name="xss-hb-worker",
            url="http://localhost:9000",
            capabilities=["chat"],
            platform="linux",
        )
        await app.state.cluster_manager.register_worker(worker)
        app.state.cluster_manager.heartbeat(
            "xss-hb-worker",
            status="draining",
            drain_reason="<i>update me</i>",
        )
        await asyncio.sleep(0.1)
        body = await _fragment(client)
        assert "&lt;i&gt;update me&lt;/i&gt;" in body
        assert "<i>" not in body

    async def test_heartbeat_drain_reason_not_corrupted_by_replace_chain(self, client, app):
        """The manager drain_reason sanitisation must not corrupt the text
        before it reaches the HTML sink."""
        worker = WorkerInfo(
            name="xss-hb-worker2",
            url="http://localhost:9000",
            capabilities=["chat"],
            platform="linux",
        )
        await app.state.cluster_manager.register_worker(worker)
        app.state.cluster_manager.heartbeat(
            "xss-hb-worker2",
            status="draining",
            drain_reason="it's a test",
        )
        await asyncio.sleep(0.1)
        body = await _fragment(client)
        assert "it&#x27;s a test" in body
        assert "it\\'s a test" not in body
