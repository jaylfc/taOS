"""Tests for the read-only taOSmd A2A coordination bus proxy (routes/a2a_bus.py).

The outbound bus calls are mocked with respx (already in dev deps). The bus URL
defaults to http://127.0.0.1:7900; we pin it via TAOS_A2A_BUS_URL so the mocked
routes match deterministically.
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from tinyagentos.app import create_app

_BUS = "http://bus.test"


@pytest.fixture(autouse=True)
def _pin_bus_url(monkeypatch):
    monkeypatch.setenv("TAOS_A2A_BUS_URL", _BUS)


def test_bus_routes_registered():
    """Both read-only bus endpoints are registered; no send/post path exists."""
    app = create_app()
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/a2a/bus/channels" in paths
    assert "/api/a2a/bus/messages" in paths


@pytest.mark.asyncio
@respx.mock
async def test_channels_proxied_and_sorted(client):
    """Channels are proxied and sorted by last_ts descending, available:true."""
    respx.get(f"{_BUS}/a2a/channels").mock(
        return_value=Response(
            200,
            json={
                "channels": [
                    {"channel": "old", "members": ["a"], "message_count": 1,
                     "created_ts": 1.0, "last_ts": 100.0},
                    {"channel": "new", "members": ["a", "b"], "message_count": 5,
                     "created_ts": 2.0, "last_ts": 300.0},
                    {"channel": "mid", "members": ["b"], "message_count": 2,
                     "created_ts": 3.0, "last_ts": 200.0},
                ]
            },
        )
    )

    resp = await client.get("/api/a2a/bus/channels")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert [c["channel"] for c in body["channels"]] == ["new", "mid", "old"]


@pytest.mark.asyncio
@respx.mock
async def test_channels_bus_unreachable_is_offline(client):
    """Bus connection error -> available:false, empty list, HTTP 200."""
    respx.get(f"{_BUS}/a2a/channels").mock(
        side_effect=httpx_connect_error()
    )

    resp = await client.get("/api/a2a/bus/channels")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["channels"] == []


@pytest.mark.asyncio
@respx.mock
async def test_channels_bus_non_200_is_offline(client):
    """Bus 500 -> available:false, empty list, HTTP 200."""
    respx.get(f"{_BUS}/a2a/channels").mock(return_value=Response(500))

    resp = await client.get("/api/a2a/bus/channels")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["channels"] == []


@pytest.mark.asyncio
@respx.mock
async def test_messages_maps_channel_to_thread_and_clamps_limit(client):
    """channel maps to the bus thread param and limit is clamped to 500."""
    route = respx.get(f"{_BUS}/a2a/messages").mock(
        return_value=Response(
            200,
            json={
                "messages": [
                    {"id": 1, "ts": 10.0, "from": "@taOS", "body": "hi",
                     "thread": "ops", "reply_to": None},
                ]
            },
        )
    )

    resp = await client.get("/api/a2a/bus/messages", params={"channel": "ops", "limit": 9999})
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["messages"][0]["from"] == "@taOS"

    sent = route.calls.last.request
    assert sent.url.params["thread"] == "ops"
    assert sent.url.params["limit"] == "500"


@pytest.mark.asyncio
async def test_messages_missing_channel_is_400(client):
    """No channel -> 400 with an error payload, no bus call attempted."""
    resp = await client.get("/api/a2a/bus/messages")
    assert resp.status_code == 400
    assert resp.json() == {"error": "channel required"}


@pytest.mark.asyncio
@respx.mock
async def test_messages_bus_error_is_offline(client):
    """Bus error on messages -> available:false, empty list, HTTP 200."""
    respx.get(f"{_BUS}/a2a/messages").mock(side_effect=httpx_connect_error())

    resp = await client.get("/api/a2a/bus/messages", params={"channel": "ops"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["messages"] == []


def httpx_connect_error():
    import httpx

    return httpx.ConnectError("connection refused")


# ---------------------------------------------------------------------------
# Read-path ambiguity: silence that reads as success
#
# Each of these was measured against the LIVE proxy before the fix. The shared
# failure shape is a 200 that carries no information: an agent following our own
# onboarding guide got a permanently quiet bus and a success code confirming it.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_channel_all_reads_every_thread(client):
    """channel=all must read ALL threads, not a channel literally named "all".

    `all` is the idiom the raw bus and `taosmd a2a-watch` document, and it exists
    so a reader cannot miss a thread created after it started. Before the fix the
    proxy forwarded it as thread=all, the bus had no such thread, and the caller
    got HTTP 200 with zero messages -- forever.
    """
    route = respx.get(f"{_BUS}/a2a/messages").mock(
        return_value=Response(200, json={"messages": [
            {"id": 7, "ts": 10.0, "from": "@a", "body": "x", "thread": "build", "reply_to": None},
        ]})
    )
    resp = await client.get("/api/a2a/bus/messages", params={"channel": "all"})
    assert resp.status_code == 200
    assert resp.json()["messages"][0]["id"] == 7
    # All-threads is spelled "omit the thread param" on the bus.
    assert "thread" not in route.calls.last.request.url.params


@pytest.mark.asyncio
@respx.mock
async def test_wildcard_channel_reads_every_thread(client):
    """`*` means the same as `all` and must not be rejected."""
    route = respx.get(f"{_BUS}/a2a/messages").mock(
        return_value=Response(200, json={"messages": []})
    )
    resp = await client.get("/api/a2a/bus/messages", params={"channel": "*"})
    assert resp.status_code == 200
    assert "thread" not in route.calls.last.request.url.params
    # All-threads empty is genuinely empty; there is no channel name to doubt.
    assert "channel_known" not in resp.json()


@pytest.mark.asyncio
@respx.mock
async def test_unknown_channel_is_distinguishable_from_empty(client):
    """A typo'd channel and a quiet channel must not look identical.

    Before the fix `channel=doesnotexist` and `channel=build` with nothing new
    both returned exactly {"messages": [], "available": true}.
    """
    respx.get(f"{_BUS}/a2a/messages").mock(return_value=Response(200, json={"messages": []}))
    respx.get(f"{_BUS}/a2a/channels").mock(
        return_value=Response(200, json={"channels": [{"channel": "build"}]})
    )

    unknown = await client.get("/api/a2a/bus/messages", params={"channel": "doesnotexist"})
    assert unknown.status_code == 200
    assert unknown.json()["channel_known"] is False

    known = await client.get("/api/a2a/bus/messages", params={"channel": "build"})
    assert known.status_code == 200
    assert known.json()["channel_known"] is True


@pytest.mark.asyncio
@respx.mock
async def test_channel_probe_fails_open_when_bus_list_unreachable(client):
    """If the channel list cannot be fetched, do not accuse the caller of a typo."""
    respx.get(f"{_BUS}/a2a/messages").mock(return_value=Response(200, json={"messages": []}))
    respx.get(f"{_BUS}/a2a/channels").mock(side_effect=httpx_connect_error())

    resp = await client.get("/api/a2a/bus/messages", params={"channel": "build"})
    assert resp.status_code == 200
    assert resp.json()["channel_known"] is True


@pytest.mark.asyncio
async def test_unknown_query_param_is_400_not_a_silent_noop(client):
    """An ignored cursor param is indistinguishable from one that works.

    Measured live before the fix: `since_id=2430` was silently dropped and the
    endpoint returned 500 messages starting at id 1890, so an incremental reader
    re-read the whole window every poll believing it held a cursor.
    """
    for bad in ("since_id", "after", "from_id"):
        resp = await client.get("/api/a2a/bus/messages", params={"channel": "build", bad: "2430"})
        assert resp.status_code == 400, f"{bad} was accepted"
        body = resp.json()
        assert bad in body["error"]
        assert "since" in body["hint"]


@pytest.mark.asyncio
@respx.mock
async def test_thread_is_accepted_as_an_alias_for_channel(client):
    """`thread` is the raw bus's own name for this; accept it rather than 400."""
    route = respx.get(f"{_BUS}/a2a/messages").mock(
        return_value=Response(200, json={"messages": [
            {"id": 1, "ts": 1.0, "from": "@a", "body": "x", "thread": "ops", "reply_to": None},
        ]})
    )
    resp = await client.get("/api/a2a/bus/messages", params={"thread": "ops"})
    assert resp.status_code == 200
    assert route.calls.last.request.url.params["thread"] == "ops"


@pytest.mark.asyncio
async def test_since_rejects_an_id_shaped_cursor(client):
    """`since` is a message ts, not an id. Say so instead of quietly mis-reading."""
    resp = await client.get(
        "/api/a2a/bus/messages", params={"channel": "build", "since": "not-a-ts"}
    )
    assert resp.status_code == 400
    assert "ts" in resp.json()["error"]


@pytest.mark.asyncio
@respx.mock
async def test_since_is_forwarded_as_the_cursor(client):
    """A valid ts cursor reaches the bus (the raw bus does honour it)."""
    route = respx.get(f"{_BUS}/a2a/messages").mock(
        return_value=Response(200, json={"messages": []})
    )
    await client.get(
        "/api/a2a/bus/messages", params={"channel": "build", "since": "1786630185.75"}
    )
    assert route.calls.last.request.url.params["since"] == "1786630185.75"


@pytest.mark.asyncio
async def test_channel_and_thread_disagreeing_is_400(client):
    """`thread` is an ALIAS for `channel`, so both-with-different-values has no
    correct reading -- and silently preferring one drops the other, which is the
    same "ignored param reads as a working one" defect this endpoint was fixed
    for. Reintroducing it through the alias would be the quietest possible
    regression: the caller names the channel they want and reads another one.
    """
    resp = await client.get(
        "/api/a2a/bus/messages", params={"channel": "build", "thread": "ops"}
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "build" in body["error"] and "ops" in body["error"]


@pytest.mark.asyncio
@respx.mock
async def test_channel_and_thread_agreeing_is_accepted(client):
    """The 400 above must fire on DISAGREEMENT, not on the alias being present.

    Without this pair the check could reject every request carrying both params
    and still pass its own test.
    """
    route = respx.get(f"{_BUS}/a2a/messages").mock(
        return_value=Response(200, json={"messages": []})
    )
    resp = await client.get(
        "/api/a2a/bus/messages", params={"channel": "build", "thread": "build"}
    )
    assert resp.status_code == 200
    assert route.calls.last.request.url.params["thread"] == "build"


@pytest.mark.asyncio
async def test_since_rejects_non_finite_cursors(client):
    """`float()` accepts "nan", "inf" and "-inf".

    A NaN cursor makes every comparison on the bus side false, so the reader
    gets an empty window and a 200 confirming it, forever -- the exact silence
    this endpoint was fixed for, smuggled back in through the validator that was
    supposed to close it.
    """
    for bad in ("nan", "NaN", "inf", "-inf", "Infinity"):
        resp = await client.get(
            "/api/a2a/bus/messages", params={"channel": "build", "since": bad}
        )
        assert resp.status_code == 400, f"{bad} was accepted as a cursor"
        assert "finite" in resp.json()["error"]


@pytest.mark.asyncio
@respx.mock
async def test_channel_probe_fails_open_on_an_unreadable_payload(client):
    """Fail open on a payload we cannot read, not only on transport failure.

    A bus returning HTTP 200 with an error body leaves the channel list empty,
    which would report every channel as unknown -- accusing the caller of a typo
    because of a fault on the bus side. The docstring promised fail-open; only
    the transport path delivered it.
    """
    respx.get(f"{_BUS}/a2a/messages").mock(return_value=Response(200, json={"messages": []}))
    for payload in ({"error": "bus is having a bad day"}, ["build", "ops"], "nope"):
        respx.get(f"{_BUS}/a2a/channels").mock(return_value=Response(200, json=payload))
        resp = await client.get("/api/a2a/bus/messages", params={"channel": "build"})
        assert resp.status_code == 200
        assert resp.json()["channel_known"] is True, f"accused a typo on payload {payload!r}"


@pytest.mark.asyncio
@respx.mock
async def test_channel_probe_still_reports_unknown_on_a_real_empty_list(client):
    """The fail-open above must not swallow the real signal.

    A bus that genuinely knows no channels answers with the `channels` key and
    an empty list -- that is a real "unknown", and it has to stay reportable or
    the typo-distinction feature is gone.
    """
    respx.get(f"{_BUS}/a2a/messages").mock(return_value=Response(200, json={"messages": []}))
    respx.get(f"{_BUS}/a2a/channels").mock(return_value=Response(200, json={"channels": []}))

    resp = await client.get("/api/a2a/bus/messages", params={"channel": "build"})
    assert resp.status_code == 200
    assert resp.json()["channel_known"] is False
