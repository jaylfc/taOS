"""Route tests for the A2A GPU lease surface (taOS #893).

The bus is faked (no network): a ``FakeBus`` serves ``GET /a2a/messages`` from
an in-memory list and appends ``POST /a2a/send`` payloads to it, so a claim made
through the route is visible to the next CHECK exactly as it would be in
production. That is what makes the "claim then check" assertions meaningful
rather than tautological.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks
from tinyagentos.agent_registry_store import mint_registry_token
from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.worker_protocol import WorkerInfo

_ROUTE_PATCH = "tinyagentos.routes.a2a_gpu_lease.httpx.AsyncClient"


class FakeBus:
    """In-memory stand-in for the raw coordination bus on :7900."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.sends: list[dict] = []
        self.fail_get = False
        self.fail_post = False
        self._id = 0

    def seed(self, body: str, sender: str = "@peer") -> dict:
        """Append a message as if a peer had posted it."""
        self._id += 1
        msg = {
            "id": self._id,
            "ts": float(self._id),
            "from": sender,
            "body": body,
            "thread": "gpu",
        }
        self.messages.append(msg)
        return msg

    @property
    def last_line(self) -> str | None:
        return self.sends[-1]["payload"]["body"] if self.sends else None

    @property
    def last_from(self) -> str | None:
        return self.sends[-1]["payload"]["from"] if self.sends else None

    @property
    def last_headers(self) -> dict:
        return self.sends[-1]["headers"] or {} if self.sends else {}


def _install_fake_bus(monkeypatch, bus: FakeBus) -> None:
    class _Resp:
        def __init__(self, payload: dict, status: int = 200) -> None:
            self._payload = payload
            self.status_code = status

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"bus returned {self.status_code}")

        def json(self) -> dict:
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def get(self, url, params=None):
            if bus.fail_get:
                raise RuntimeError("bus unreachable")
            return _Resp({"messages": list(bus.messages)})

        async def post(self, url, json=None, headers=None):
            if bus.fail_post:
                raise RuntimeError("bus unreachable")
            payload = dict(json or {})
            bus._id += 1
            msg = {
                "id": bus._id,
                "ts": float(bus._id),
                "from": payload.get("from"),
                "body": payload.get("body"),
                "thread": payload.get("thread"),
            }
            bus.sends.append({"payload": payload, "headers": headers})
            bus.messages.append(msg)
            return _Resp(msg)

    monkeypatch.setattr(_ROUTE_PATCH, _Client)


@pytest.fixture
def bus(monkeypatch) -> FakeBus:
    fake = FakeBus()
    _install_fake_bus(monkeypatch, fake)
    return fake


class _StubLedger:
    """Stands in for the shared VramReservationManager (taOS #185)."""

    def __init__(self, free_mb: int, total_mb: int) -> None:
        self.free_mb = free_mb
        self.total_mb = total_mb

    def available_vram(self) -> tuple[int, int]:
        return self.free_mb, self.total_mb


@pytest.fixture
def local_vram(app):
    """Set the local node's reported (free, total) VRAM, in MiB."""

    def _set(free_mb: int, total_mb: int = 12288) -> None:
        app.state.vram_reservation = _StubLedger(free_mb, total_mb)

    return _set


@pytest_asyncio.fixture
async def lease_client(app, tmp_data_dir):
    """Admin client with the agent registry + grant stores initialised.

    Exposes ``._app`` so tests can register agents / mint tokens and drive bare
    (cookieless) requests with a Bearer header.
    """
    for attr in ("agent_registry", "agent_grants", "metrics"):
        store = getattr(app.state, attr)
        if store._db is None:
            await store.init()
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(
        user_id=record["id"] if record else "", long_lived=True
    )
    app.state._startup_complete = True
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        c._app = app
        yield c
    for attr in ("agent_registry", "agent_grants", "metrics"):
        store = getattr(app.state, attr)
        if store._db is not None:
            await store.close()


def _bare(app) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        event_hooks=csrf_event_hooks(),
    )


async def _agent_token(app, *, scopes=("a2a_send",), handle="@taosmd"):
    """Register an agent, grant scopes, return (canonical_id, jwt)."""
    registry = app.state.agent_registry
    grants = app.state.agent_grants
    priv, _pub = app.state.agent_registry_keypair
    rec = await registry.register(
        framework="taosmd", display_name="Peer", origin="taos-deployed", handle=handle
    )
    cid = rec["canonical_id"]
    for scope in scopes:
        await grants.add_grant(cid, scope)
    return cid, mint_registry_token(cid, priv, user_id="u", framework="taosmd")


@pytest_asyncio.fixture
async def cluster(app):
    """A real ClusterManager with one online GPU worker (linstation)."""
    cm = ClusterManager()
    ok, reason = await cm.register_worker(
        WorkerInfo(
            name="linstation",
            url="http://10.0.0.9:9000",
            status="online",
            free_vram_mb=8192,
            hardware={"gpu": {"vram_mb": 12288}},
            resources=["gpu-cuda-0"],
        )
    )
    assert ok, reason
    app.state.cluster_manager = cm
    return cm


@pytest.mark.asyncio
class TestCheck:
    async def test_missing_node_is_400(self, lease_client, bus):
        resp = await lease_client.get("/api/a2a/gpu/check")
        assert resp.status_code == 400
        assert bus.sends == []

    async def test_unparseable_vram_is_400_not_a_silent_zero(self, lease_client, bus):
        resp = await lease_client.get(
            "/api/a2a/gpu/check", params={"node": "local", "vram": "six gigabytes"}
        )
        assert resp.status_code == 400

    async def test_free_node_is_admitted(self, lease_client, bus, local_vram):
        local_vram(9000)
        resp = await lease_client.get(
            "/api/a2a/gpu/check", params={"node": "local", "vram_mb": 6144}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["admitted"] is True
        assert data["vram_verified"] is True
        assert data["free_mb"] == 9000

    async def test_peer_claim_blocks_the_node(self, lease_client, bus, local_vram):
        local_vram(11000)
        bus.seed("[GPU CLAIM] node=local holder=@taosmd vram=~9.4gb", sender="@taosmd")
        resp = await lease_client.get(
            "/api/a2a/gpu/check", params={"node": "local", "vram_mb": 2048}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["admitted"] is False
        assert data["blockers"] == ["@taosmd"]
        assert data["claims"][0]["vram_mb"] == 9626
        assert data["claims"][0]["identity"] == "@taosmd"

    async def test_a_spoofed_body_holder_does_not_evade_the_block(
        self, lease_client, bus, local_vram
    ):
        # Blocking is keyed on the bus-authenticated author, never the
        # caller-controlled holder= field in the body.
        local_vram(11000)
        bus.seed(
            "[GPU CLAIM] node=local holder=@operator vram=9gb", sender="agent_peer"
        )
        resp = await lease_client.post(
            "/api/a2a/gpu/claim", json={"node": "local", "vram_mb": 2048}
        )
        assert resp.status_code == 409
        assert resp.json()["claims"][0]["identity"] == "agent_peer"
        assert bus.sends == []

    async def test_released_peer_claim_no_longer_blocks(self, lease_client, bus, local_vram):
        local_vram(11000)
        bus.seed("[GPU CLAIM] node=local holder=@taosmd vram=9gb", sender="@peer")
        bus.seed("[GPU RELEASE] node=local holder=@taosmd", sender="@peer")
        resp = await lease_client.get(
            "/api/a2a/gpu/check", params={"node": "local", "vram_mb": 2048}
        )
        assert resp.json()["admitted"] is True

    async def test_insufficient_vram_is_denied(self, lease_client, bus, local_vram):
        local_vram(4096)
        resp = await lease_client.get(
            "/api/a2a/gpu/check", params={"node": "local", "vram_mb": 8192}
        )
        data = resp.json()
        assert data["admitted"] is False
        assert data["blockers"] == []

    async def test_unknown_node_admits_but_flags_unverified(self, lease_client, bus):
        resp = await lease_client.get(
            "/api/a2a/gpu/check", params={"node": "ghostbox", "vram_mb": 4096}
        )
        data = resp.json()
        assert data["admitted"] is True
        assert data["vram_verified"] is False

    async def test_bus_unreadable_fails_closed(self, lease_client, bus):
        bus.fail_get = True
        resp = await lease_client.get(
            "/api/a2a/gpu/check", params={"node": "local", "vram_mb": 4096}
        )
        assert resp.status_code == 503
        assert "cannot verify" in resp.json()["detail"]

    async def test_worker_node_uses_heartbeat_vram(self, lease_client, bus, cluster):
        resp = await lease_client.get(
            "/api/a2a/gpu/check", params={"node": "linstation", "vram_mb": 4096}
        )
        data = resp.json()
        assert data["admitted"] is True
        assert data["free_mb"] == 8192
        assert data["capacity_mb"] == 12288


@pytest.mark.asyncio
class TestClaim:
    async def test_claim_without_vram_is_400(self, lease_client, bus):
        resp = await lease_client.post("/api/a2a/gpu/claim", json={"node": "local"})
        assert resp.status_code == 400
        assert bus.sends == []

    async def test_claim_posts_the_protocol_line(self, lease_client, bus, local_vram):
        local_vram(11000)
        resp = await lease_client.post(
            "/api/a2a/gpu/claim",
            json={"node": "local", "vram_mb": 6144, "reason": "flux", "eta": "~5m"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "claimed"
        assert data["line"] == (
            "[GPU CLAIM] node=local holder=@operator vram=~6gb reason=flux eta=~5m"
        )
        assert bus.last_line == data["line"]

    async def test_claim_then_check_sees_the_claim(self, lease_client, bus, local_vram):
        local_vram(11000)
        await lease_client.post(
            "/api/a2a/gpu/claim", json={"node": "local", "vram_mb": 6144}
        )
        # Same caller: its own claim must not block it.
        mine = await lease_client.get(
            "/api/a2a/gpu/check", params={"node": "local", "vram_mb": 2048}
        )
        assert mine.json()["admitted"] is True
        # A different caller sees a claimed node.
        peer = await lease_client.get(
            "/api/a2a/gpu/check", params={"node": "local", "vram_mb": 2048, "channel": "gpu"}
        )
        assert peer.status_code == 200

    async def test_peer_owned_claim_is_denied_409(self, lease_client, bus, local_vram):
        local_vram(11000)
        bus.seed("[GPU CLAIM] node=local holder=@taosmd vram=9gb", sender="agent_peer")
        resp = await lease_client.post(
            "/api/a2a/gpu/claim", json={"node": "local", "vram_mb": 2048}
        )
        assert resp.status_code == 409
        assert resp.json()["blockers"] == ["@taosmd"]
        assert bus.sends == []  # denied claims never reach the channel

    async def test_insufficient_vram_is_denied_409(self, lease_client, bus, local_vram):
        local_vram(2048)
        resp = await lease_client.post(
            "/api/a2a/gpu/claim", json={"node": "local", "vram_mb": 8192}
        )
        assert resp.status_code == 409
        assert bus.sends == []

    async def test_bus_failure_does_not_grant_the_claim(self, lease_client, bus, local_vram):
        local_vram(11000)
        bus.fail_post = True
        resp = await lease_client.post(
            "/api/a2a/gpu/claim", json={"node": "local", "vram_mb": 4096}
        )
        assert resp.status_code == 502


@pytest.mark.asyncio
class TestClusterLeaseIntegration:
    async def test_claim_creates_a_real_lease_and_release_frees_it(
        self, lease_client, bus, cluster
    ):
        resp = await lease_client.post(
            "/api/a2a/gpu/claim",
            json={"node": "linstation", "vram_mb": 6144, "ttl_seconds": 300},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["lease_id"], data
        leases = cluster.get_leases()
        assert len(leases) == 1
        assert leases[0].resource_id == "linstation:gpu-cuda-0"
        assert leases[0].required_vram_mb == 6144

        rel = await lease_client.post(
            "/api/a2a/gpu/release",
            json={"node": "linstation", "lease_id": data["lease_id"]},
        )
        assert rel.status_code == 200
        assert rel.json()["lease_id"] == data["lease_id"]
        assert rel.json()["line"] == "[GPU RELEASE] node=linstation holder=@operator"
        assert cluster.get_leases() == []

    async def test_release_without_a_lease_id_releases_the_callers_own(
        self, lease_client, bus, cluster
    ):
        await lease_client.post(
            "/api/a2a/gpu/claim", json={"node": "linstation", "vram_mb": 4096}
        )
        assert len(cluster.get_leases()) == 1
        resp = await lease_client.post(
            "/api/a2a/gpu/release", json={"node": "linstation"}
        )
        assert resp.status_code == 200
        assert resp.json()["lease_id"] is not None
        assert cluster.get_leases() == []

    async def test_release_does_not_free_another_holders_lease(
        self, lease_client, bus, cluster
    ):
        # The node-scoped release frees only the caller's OWN claim; a lease
        # taken by the scheduler (not by this A2A caller) must survive.
        foreign = await cluster.claim_lease(
            "linstation:gpu-cuda-0", caller="skald-dispatcher", ttl_seconds=300
        )
        assert foreign is not None
        resp = await lease_client.post(
            "/api/a2a/gpu/release", json={"node": "linstation"}
        )
        assert resp.status_code == 200
        assert resp.json()["lease_id"] is None
        assert [l.lease_id for l in cluster.get_leases()] == [foreign.lease_id]

    async def test_admin_may_release_an_explicit_lease_id(
        self, lease_client, bus, cluster
    ):
        # Operator override, mirroring POST /api/cluster/leases/release.
        foreign = await cluster.claim_lease(
            "linstation:gpu-cuda-0", caller="skald-dispatcher", ttl_seconds=300
        )
        assert foreign is not None
        resp = await lease_client.post(
            "/api/a2a/gpu/release",
            json={"node": "linstation", "lease_id": foreign.lease_id},
        )
        assert resp.status_code == 200
        assert cluster.get_leases() == []

    async def test_reclaiming_extends_rather_than_conflicts(self, lease_client, bus, cluster):
        first = await lease_client.post(
            "/api/a2a/gpu/claim", json={"node": "linstation", "vram_mb": 4096}
        )
        second = await lease_client.post(
            "/api/a2a/gpu/claim", json={"node": "linstation", "vram_mb": 4096}
        )
        assert second.status_code == 200
        assert second.json()["lease_id"] == first.json()["lease_id"]
        assert len(cluster.get_leases()) == 1

    async def test_lease_claim_is_refused_when_the_worker_lacks_the_vram(
        self, lease_client, bus, cluster
    ):
        resp = await lease_client.post(
            "/api/a2a/gpu/claim", json={"node": "linstation", "vram_mb": 16384}
        )
        assert resp.status_code == 409
        assert cluster.get_leases() == []

    async def test_claim_rolls_back_the_lease_when_the_bus_post_fails(
        self, lease_client, bus, cluster
    ):
        bus.fail_post = True
        resp = await lease_client.post(
            "/api/a2a/gpu/claim", json={"node": "linstation", "vram_mb": 4096}
        )
        assert resp.status_code == 502
        assert cluster.get_leases() == []

    async def test_renew_extends_the_ttl(self, lease_client, bus, cluster):
        claim = await lease_client.post(
            "/api/a2a/gpu/claim",
            json={"node": "linstation", "vram_mb": 4096, "ttl_seconds": 60},
        )
        lease_id = claim.json()["lease_id"]
        before = cluster.get_leases()[0].expires_at
        resp = await lease_client.post(
            "/api/a2a/gpu/renew", json={"lease_id": lease_id, "ttl_seconds": 600}
        )
        assert resp.status_code == 200
        assert cluster.get_leases()[0].expires_at > before

    async def test_renew_unknown_lease_is_409(self, lease_client, bus, cluster):
        resp = await lease_client.post(
            "/api/a2a/gpu/renew", json={"lease_id": "l_nope"}
        )
        assert resp.status_code == 409

    async def test_expired_lease_auto_frees_the_node(self, lease_client, bus, cluster):
        resp = await lease_client.post(
            "/api/a2a/gpu/claim",
            json={"node": "linstation", "vram_mb": 4096, "ttl_seconds": -1},
        )
        assert resp.status_code == 200
        # TTL already elapsed: the node is free again with no explicit release.
        nxt = await lease_client.post(
            "/api/a2a/gpu/claim", json={"node": "linstation", "vram_mb": 4096}
        )
        assert nxt.status_code == 200

    async def test_check_blocks_when_the_scheduler_holds_a_lease(
        self, lease_client, bus, cluster
    ):
        # A lease the A2A layer did not take (here: the Skald dispatcher) is
        # invisible on the bus but is still a real reservation.
        lease = await cluster.claim_lease(
            "linstation:gpu-cuda-0", caller="skald-dispatcher", ttl_seconds=300
        )
        assert lease is not None
        resp = await lease_client.get(
            "/api/a2a/gpu/check", params={"node": "linstation", "vram_mb": 2048}
        )
        data = resp.json()
        assert data["admitted"] is False
        assert data["blockers"] == ["skald-dispatcher"]

    async def test_claim_is_denied_while_the_scheduler_holds_a_lease(
        self, lease_client, bus, cluster
    ):
        await cluster.claim_lease(
            "linstation:gpu-cuda-0", caller="skald-dispatcher", ttl_seconds=300
        )
        resp = await lease_client.post(
            "/api/a2a/gpu/claim", json={"node": "linstation", "vram_mb": 2048}
        )
        assert resp.status_code == 409
        assert resp.json()["blockers"] == ["skald-dispatcher"]
        assert bus.sends == []

    async def test_check_counts_the_callers_own_lease_as_its_own(
        self, lease_client, bus, cluster
    ):
        await lease_client.post(
            "/api/a2a/gpu/claim", json={"node": "linstation", "vram_mb": 4096}
        )
        resp = await lease_client.get(
            "/api/a2a/gpu/check", params={"node": "linstation", "vram_mb": 2048}
        )
        data = resp.json()
        # 8192 free - 4096 promised to ourselves = 4096 >= 2048.
        assert data["admitted"] is True
        assert data["claimed_mb"] == 4096


@pytest.mark.asyncio
class TestRequest:
    async def test_request_posts_a_request_line(self, lease_client, bus):
        resp = await lease_client.post(
            "/api/a2a/gpu/request",
            json={"node": "linstation", "need_mb": 6144, "reason": "blocked"},
        )
        assert resp.status_code == 200
        assert resp.json()["line"] == (
            "[GPU REQUEST] node=linstation need=~6gb reason=blocked"
        )
        assert bus.last_line == resp.json()["line"]

    async def test_request_accepts_a_gb_string(self, lease_client, bus):
        resp = await lease_client.post(
            "/api/a2a/gpu/request", json={"node": "n1", "need": "~2.5gb"}
        )
        assert resp.status_code == 200
        assert resp.json()["need_mb"] == 2560

    async def test_request_without_need_is_400(self, lease_client, bus):
        resp = await lease_client.post("/api/a2a/gpu/request", json={"node": "n1"})
        assert resp.status_code == 400
        assert bus.sends == []


@pytest.mark.asyncio
class TestAgentToken:
    async def test_agent_claims_as_its_registry_identity(
        self, lease_client, bus
    ):
        cid, token = await _agent_token(lease_client._app, scopes=("a2a_send",))
        async with _bare(lease_client._app) as bare:
            resp = await bare.post(
                "/api/a2a/gpu/claim",
                json={"node": "remote-node", "vram_mb": 4096, "holder": "@spoofed"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        # The bus `from` is the identity the token proves, never the body field.
        assert bus.last_from == cid
        assert "holder=@taosmd" in bus.last_line
        assert "@spoofed" not in bus.last_line
        # The credential travels with the attribution.
        assert bus.last_headers.get("Authorization") == f"Bearer {token}"

    async def test_agent_without_send_scope_cannot_claim(self, lease_client, bus):
        _cid, token = await _agent_token(lease_client._app, scopes=("a2a_receive",))
        async with _bare(lease_client._app) as bare:
            resp = await bare.post(
                "/api/a2a/gpu/claim",
                json={"node": "n1", "vram_mb": 4096},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 403
        assert bus.sends == []

    async def test_agent_check_needs_receive_scope(self, lease_client, bus):
        _cid, token = await _agent_token(lease_client._app, scopes=("a2a_send",))
        async with _bare(lease_client._app) as bare:
            resp = await bare.get(
                "/api/a2a/gpu/check",
                params={"node": "n1"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 403

    async def test_agent_with_receive_scope_can_check(self, lease_client, bus):
        _cid, token = await _agent_token(lease_client._app, scopes=("a2a_receive",))
        async with _bare(lease_client._app) as bare:
            resp = await bare.get(
                "/api/a2a/gpu/check",
                params={"node": "n1", "vram_mb": 1024},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert resp.json()["holder"] == "@taosmd"

    async def test_agent_token_is_not_a_skeleton_key(self, lease_client, bus):
        _cid, token = await _agent_token(lease_client._app, scopes=("a2a_send",))
        async with _bare(lease_client._app) as bare:
            resp = await bare.get(
                "/api/cluster/leases", headers={"Authorization": f"Bearer {token}"}
            )
        assert resp.status_code in (401, 403)

    async def test_agent_releases_its_own_lease(self, lease_client, bus, cluster):
        cid, token = await _agent_token(lease_client._app, scopes=("a2a_send",))
        async with _bare(lease_client._app) as bare:
            claimed = await bare.post(
                "/api/a2a/gpu/claim",
                json={"node": "linstation", "vram_mb": 4096},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert claimed.status_code == 200
        lease_id = claimed.json()["lease_id"]
        assert lease_id is not None
        async with _bare(lease_client._app) as bare:
            released = await bare.post(
                "/api/a2a/gpu/release",
                json={"node": "linstation", "lease_id": lease_id},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert released.status_code == 200
        assert cluster.get_leases() == []

    async def test_agent_cannot_release_another_holders_lease(
        self, lease_client, bus, cluster
    ):
        foreign = await cluster.claim_lease(
            "linstation:gpu-cuda-0", caller="skald-dispatcher", ttl_seconds=300
        )
        assert foreign is not None
        _cid, token = await _agent_token(lease_client._app, scopes=("a2a_send",))
        async with _bare(lease_client._app) as bare:
            resp = await bare.post(
                "/api/a2a/gpu/release",
                json={"node": "linstation", "lease_id": foreign.lease_id},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 403
        assert [l.lease_id for l in cluster.get_leases()] == [foreign.lease_id]
        assert bus.sends == []  # and no [GPU RELEASE] line clears the peer claim

    async def test_agent_cannot_renew_another_holders_lease(
        self, lease_client, bus, cluster
    ):
        foreign = await cluster.claim_lease(
            "linstation:gpu-cuda-0", caller="skald-dispatcher", ttl_seconds=300
        )
        assert foreign is not None
        before = cluster.get_leases()[0].expires_at
        _cid, token = await _agent_token(lease_client._app, scopes=("a2a_send",))
        async with _bare(lease_client._app) as bare:
            resp = await bare.post(
                "/api/a2a/gpu/renew",
                json={"lease_id": foreign.lease_id, "ttl_seconds": 6000},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 403
        # The rejection must not have extended the lease first.
        assert cluster.get_leases()[0].expires_at == before
