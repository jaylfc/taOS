# tinyagentos/routes/a2a_gpu_lease.py
"""A2A GPU lease endpoints — CHECK / CLAIM / RELEASE / REQUEST / RENEW.

taOS #893. Two agents sharing one GPU coordinate over the A2A bus with the
one-line text protocol owned by ``tinyagentos/gpu_lease.py``. These endpoints
are the authenticated half:

* ``GET  /api/a2a/gpu/check``   — read the channel, fold open claims, combine
  them with the node's live VRAM, and answer "may I load?".
* ``POST /api/a2a/gpu/claim``   — admission-checked claim: registers a real
  GPU lease in the cluster manager (TTL + auto-expiry, the "keep-alive
  auto-eviction" half of the issue) AND posts the ``[GPU CLAIM]`` line so
  peers that only see the bus are coordinated too.
* ``POST /api/a2a/gpu/release`` — release the lease and post ``[GPU RELEASE]``.
* ``POST /api/a2a/gpu/request`` — post ``[GPU REQUEST]`` when blocked.
* ``POST /api/a2a/gpu/renew``   — keep-alive for a long-running load.

Why both halves
---------------
The bus claim is what a peer product (@taOSmd) can see; the cluster lease is
what THIS controller's scheduler enforces (``GpuArbiter`` already subtracts
``ClusterManager.get_leases()`` when admitting cluster work, taOS #1705). A
claim is only real when both exist, so a failed bus post rolls the lease back
rather than leaving a reservation no peer knows about.

Identity and credentials
------------------------
An agent caller posts as its OWN registry identity: the bus ``from`` is the
token's ``sub`` and the caller's registry JWT travels with it, because the bus
verifies the signature and then requires ``token sub == from`` (taOS #2112,
``routes/a2a_bus.py``). The readable ``holder=`` in the body is the agent's
registry handle. An admin session may post as an explicit handle and forwards
no credential. ``from`` is never taken from the request body for an agent.

Fail closed
-----------
If the channel cannot be read, CHECK and CLAIM return 503 rather than reporting
the node free: an unreadable channel is indistinguishable from "everyone else's
claims are invisible", which is exactly the silent co-load the issue is about.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tinyagentos.agent_token_auth import check_agent_scope
from tinyagentos.gpu_lease import (
    CLAIM,
    GpuLeaseMessage,
    claims_for_node,
    evaluate_admission,
    open_claims,
    parse_vram_mb,
    render_claim,
    render_release,
    render_request,
)
from tinyagentos.routes.a2a_bus import _bus_url, _credential_may_cross

logger = logging.getLogger(__name__)

router = APIRouter()

# Coordination channel. The issue does not name one; "gpu" is the default and an
# operator can point every agent at the same thread with TAOS_A2A_GPU_CHANNEL.
_DEFAULT_CHANNEL = "gpu"
_DEFAULT_RESOURCE = "gpu-cuda-0"

# The channel is folded on every CHECK/CLAIM, so the read window has to be deep
# enough to contain the CLAIM that is still open. 500 is the largest page the
# proxy exposes; a claim that scrolls out of this window stops being visible, so
# a long-running load re-POSTs /claim (idempotent) to refresh its position.
_CHANNEL_LIMIT = 500

# Default lease TTL. Long enough for a model load to start and renew; short
# enough that a crashed holder stops blocking the node (issue: advisory keep-alive).
_DEFAULT_TTL_SECONDS = 300.0

# Upper bound on a caller-supplied TTL. Without it an authenticated agent could
# take the shared GPU with `ttl_seconds: 1e9` and remove the auto-expiry the
# whole mechanism rests on. A longer load is kept alive by renewing, not by one
# enormous lease.
MAX_LEASE_TTL_SECONDS = 3600.0


def _channel() -> str:
    return os.environ.get("TAOS_A2A_GPU_CHANNEL", _DEFAULT_CHANNEL).strip() or _DEFAULT_CHANNEL


def _clean_handle(raw: str | None) -> str:
    """Flatten a caller-supplied handle to one printable token."""
    s = "".join(c for c in (raw or "") if c.isprintable())
    return " ".join(s.split())[:64]


@dataclass(frozen=True)
class _Actor:
    """Who a lease action is attributed to, and the credential proving it."""

    identity: str  # the bus `from` (canonical_id for agents)
    holder: str  # readable handle for the body's holder= field
    credential: str | None = None
    is_admin: bool = False


def _bearer_token(request: Request) -> str | None:
    """Return the caller's raw Bearer credential, verbatim, or None."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None
    return auth_header[7:].strip() or None


async def _resolve_actor(
    request: Request, body_holder: str | None, scope: str
) -> _Actor:
    """Resolve the acting identity, or raise 401/403 (fail closed)."""
    if getattr(request.state, "is_admin", False):
        holder = _clean_handle(body_holder) or "@operator"
        # A session admin acts as a FIXED verified principal; `holder` is the
        # readable label only. Reading the body's holder as the admin's IDENTITY
        # would let a `holder=` spelled like another holder's lease satisfy
        # `_lease_owned_by` - i.e. take ownership of an `a2a:` lease the admin
        # does not hold, on the node-scoped release/renew paths that take no
        # lease id (CR on #2988). An operator still frees any lease by EXPLICIT
        # id, which is the operator path.
        return _Actor(identity="@operator", holder=holder, is_admin=True)

    caller = await check_agent_scope(request, scope)
    if caller is None:
        raise HTTPException(status_code=403, detail="forbidden")

    registry = getattr(request.app.state, "agent_registry", None)
    record = await registry.get(caller) if registry is not None else None
    handle = _clean_handle((record or {}).get("handle"))
    if not handle:
        raise HTTPException(status_code=403, detail="agent has no bus handle")
    if not handle.startswith("@"):
        handle = f"@{handle}"
    # The bus `from` must be the identity the token proves, and the token must
    # travel with it -- see the module docstring.
    return _Actor(identity=caller, holder=handle, credential=_bearer_token(request))


async def _read_channel(channel: str, actor: _Actor) -> list[dict]:
    """Fetch the channel's messages oldest-first. Raises on an unreadable bus.

    The caller's registry credential is presented here too: a bus that gates
    reads fails a credential-less GET with 401, which this route would surface
    as an unreadable channel (503). Forwarding it is subject to the same
    loopback/HTTPS guard as the post path.
    """
    headers: dict[str, str] = {}
    if actor.credential:
        bus = _bus_url()
        if _credential_may_cross(bus):
            headers["Authorization"] = f"Bearer {actor.credential}"
        else:
            logger.warning(
                "A2A GPU lease read credential withheld for non-loopback http destination %s",
                bus,
            )
    bus = _bus_url()
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{bus}/a2a/messages",
            params={"thread": channel, "limit": _CHANNEL_LIMIT},
            headers=headers or None,
        )
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict):
        raise ValueError(f"unexpected bus payload: {type(data).__name__}")
    messages = data.get("messages")
    if not isinstance(messages, list):
        raise ValueError("bus payload has no messages list")
    return [m for m in messages if isinstance(m, dict)]


async def _post_line(channel: str, actor: _Actor, text: str) -> dict:
    """Post a protocol line to the channel. Raises HTTPException(502) on failure."""
    headers: dict[str, str] = {}
    if actor.credential:
        bus = _bus_url()
        if _credential_may_cross(bus):
            headers["Authorization"] = f"Bearer {actor.credential}"
        else:
            logger.warning(
                "A2A GPU lease credential withheld for non-loopback http destination %s",
                bus,
            )
    bus = _bus_url()
    payload = {"from": actor.identity, "thread": channel, "body": text}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{bus}/a2a/send", json=payload, headers=headers or None)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("A2A GPU lease post failed (%s): %s", bus, exc)
        raise HTTPException(status_code=502, detail="a2a bus unavailable")
    return data if isinstance(data, dict) else {}


async def _folded_claims(
    request: Request, channel: str, actor: _Actor
) -> dict[str, list[GpuLeaseMessage]]:
    """Read the channel and fold it, mapping an unreadable bus to 503."""
    try:
        messages = await _read_channel(channel, actor)
    except Exception as exc:  # noqa: BLE001
        logger.warning("A2A GPU lease channel read failed (channel=%s): %s", channel, exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "a2a bus unavailable — cannot verify GPU claims, so the node "
                "cannot be reported free"
            ),
        )
    return open_claims(messages)


def _match_worker(cluster, node: str):
    """Return the cluster worker *node* names, or None.

    Accepts the worker name (case-insensitively, since a bus label is free
    text), a URL host, or the local controller's own aliases ("local",
    "localhost", this hostname) so a bus node label resolves to the worker whose
    VRAM the label refers to. Failing to resolve is a real outcome: an unknown
    node is coordinated over the bus alone, with no local lease.
    """
    if cluster is None:
        return None
    worker = cluster.get_worker(node)
    if worker is not None:
        return worker
    wanted = (node or "").strip().casefold()
    if not wanted:
        return None
    if wanted in ("local", "localhost", socket.gethostname().casefold()):
        return cluster.get_worker("local")
    for w in cluster.get_workers():
        name = getattr(w, "name", "") or ""
        if name and name.casefold() == wanted:
            return w
    for w in cluster.get_workers():
        url = getattr(w, "url", "") or ""
        host = url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        if host and host.casefold() == wanted:
            return w
    return None


def _worker_capacity_mb(worker) -> int | None:
    hw = getattr(worker, "hardware", None)
    if not isinstance(hw, dict):
        return None
    gpu = hw.get("gpu")
    if not isinstance(gpu, dict):
        return None
    vram = gpu.get("vram_mb")
    try:
        vram = int(vram)
    except (TypeError, ValueError):
        return None
    return vram if vram > 0 else None


async def _vram_for_node(request: Request, node: str) -> tuple[int | None, int | None]:
    """Return ``(free_mb, capacity_mb)`` for *node*; either may be None.

    A cluster worker's last-heartbeat figures win. The local controller uses
    the shared VRAM ledger (which nets out in-flight reservations, taOS #185)
    when it is available, falling back to a live nvidia-smi probe.
    """
    cluster = getattr(request.app.state, "cluster_manager", None)
    worker = _match_worker(cluster, node)
    if worker is not None:
        free = getattr(worker, "free_vram_mb", None)
        capacity = _worker_capacity_mb(worker)
        if getattr(worker, "name", "") == "local":
            local_free, local_total = await _local_vram(request)
            return (local_free if local_free is not None else free, capacity or local_total)
        return free, capacity

    if (node or "").strip().casefold() in (
        "local",
        "localhost",
        socket.gethostname().casefold(),
    ):
        return await _local_vram(request)
    return None, None


async def _local_vram(request: Request) -> tuple[int | None, int | None]:
    """Probe this host's VRAM: (free_mb, total_mb), either possibly None."""
    ledger = getattr(request.app.state, "vram_reservation", None)
    if ledger is not None:
        try:
            free, total = await asyncio.to_thread(ledger.available_vram)
            return (free if total and total > 0 else None, total if total and total > 0 else None)
        except Exception:  # noqa: BLE001 (fall through to a raw probe)
            logger.debug("gpu-lease: ledger probe failed", exc_info=True)
    try:
        from tinyagentos.system_stats import read_nvidia_vram

        pair = await asyncio.to_thread(read_nvidia_vram)
        if pair is not None:
            used, total = pair
            return max(0, total - used), total
    except Exception:  # noqa: BLE001
        logger.debug("gpu-lease: nvidia-smi probe failed", exc_info=True)
    return None, None


def _resolve_vram_mb(vram_mb: int | None, vram: str | None) -> int | None:
    """Resolve an explicit MiB figure or a protocol-style ``vram`` string."""
    if vram_mb is not None:
        return max(0, int(vram_mb))
    if vram:
        return parse_vram_mb(vram)
    return None


def _cluster_lease_claims(cluster, node: str, resource: str) -> list[GpuLeaseMessage]:
    """Represent this controller's own GPU leases on *node* as open claims.

    The bus only carries what peers posted; a lease taken by the local
    scheduler (``skald-dispatcher``, the GPU arbiter) is invisible there but is
    just as real a reservation. Folding the two together is what makes CHECK
    account for pending local loads whose VRAM a heartbeat has not yet seen
    (the gap ``GpuArbiter._check_cluster_admission`` closes for the scheduler,
    taOS #1705).

    ``caller`` is stripped of the ``a2a:`` prefix our own claims add so a
    caller's own lease matches its identity instead of blocking it.

    A holder that already has an open BUS claim is skipped: that claim and its
    lease are the same reservation seen twice, and charging both would double
    the VRAM it holds.
    """
    if cluster is None:
        return []
    resource_id = _resource_id(_canonical_node(cluster, node), resource)
    out: list[GpuLeaseMessage] = []
    seen: set[str] = set()
    for lease in cluster.get_leases():
        if lease.resource_id != resource_id:
            continue
        caller = lease.caller or ""
        who = caller[4:] if caller.startswith("a2a:") else caller
        if who.casefold() in seen:
            continue
        seen.add(who.casefold())
        out.append(
            GpuLeaseMessage(
                kind=CLAIM,
                node=node,
                holder=who,
                vram_mb=(lease.required_vram_mb or None),
                reason="cluster lease",
                bus_from=who,
            )
        )
    return out


async def _check_node(
    request: Request,
    *,
    node: str,
    required_mb: int,
    actor: _Actor,
    channel: str,
    resource: str = _DEFAULT_RESOURCE,
    replace_own: bool = False,
) -> tuple[dict, list[GpuLeaseMessage]]:
    """Run the full CHECK for a node; returns (admission dict, node claims)."""
    folded = await _folded_claims(request, channel, actor)
    cluster = getattr(request.app.state, "cluster_manager", None)
    bus_claims = claims_for_node(folded, node)
    # An A2A claim and the cluster lease it created are the same reservation;
    # charge it once (the bus line is the holder's own declared figure).
    on_bus = set()
    for c in bus_claims:
        on_bus.add(c.identity_key.casefold())
        on_bus.add(c.holder.casefold())
    node_claims = bus_claims + [
        c
        for c in _cluster_lease_claims(cluster, node, resource)
        if c.holder.casefold() not in on_bus
    ]
    free_mb, capacity_mb = await _vram_for_node(request, node)
    admission = evaluate_admission(
        node=node,
        required_mb=required_mb,
        identity=actor.identity,
        claims=node_claims,
        free_mb=free_mb,
        capacity_mb=capacity_mb,
        replace_own=replace_own,
    )
    body = admission.as_dict()
    body["claims"] = [c.as_dict() for c in node_claims]
    body["channel"] = channel
    return body, node_claims


# ── request bodies ────────────────────────────────────────────────────────────


class _LeaseBody(BaseModel):
    node: str
    channel: str | None = None
    resource: str = _DEFAULT_RESOURCE


class ClaimBody(_LeaseBody):
    vram_mb: int | None = None
    vram: str | None = None
    reason: str = ""
    eta: str = ""
    ttl_seconds: float = Field(
        default=_DEFAULT_TTL_SECONDS, gt=0, le=MAX_LEASE_TTL_SECONDS
    )
    holder: str | None = None  # honored for admin callers only


class ReleaseBody(_LeaseBody):
    lease_id: str | None = None
    holder: str | None = None


class RequestBody(_LeaseBody):
    need_mb: int | None = None
    need: str | None = None
    reason: str = ""
    holder: str | None = None


class RenewBody(BaseModel):
    lease_id: str
    ttl_seconds: float = Field(
        default=_DEFAULT_TTL_SECONDS, gt=0, le=MAX_LEASE_TTL_SECONDS
    )


def _resource_id(node: str, resource: str) -> str:
    res = (resource or _DEFAULT_RESOURCE).strip() or _DEFAULT_RESOURCE
    return f"{node}:{res}"


def _canonical_node(cluster, node: str) -> str:
    """Return the cluster worker's own name for *node*, else *node*.

    The bus label is free text ("Linstation", "linstation", a URL host) while
    leases are keyed on the worker's registered name, so resolving through the
    manager keeps two spellings of one host from taking two leases on one GPU.
    """
    worker = _match_worker(cluster, node)
    return getattr(worker, "name", None) or node


def _lease_for_actor(cluster, resource_id: str, actor: _Actor):
    """The actor's own active lease on *resource_id*, if any."""
    existing = cluster.find_existing_lease(resource_id)
    if existing is None:
        return None
    if _lease_owned_by(existing, actor):
        return existing
    return None


def _lease_owned_by(lease, actor: _Actor) -> bool:
    """True when *lease* was taken by *actor*.

    Identity only - never the body's ``holder=``. That field is caller-supplied
    display text, so matching on it would let any caller claim another holder's
    ``a2a:`` lease as its own (CR on #2988). The node-scoped release/renew paths
    must not let an admin's session free a lease it did not take either (that is
    what the explicit-id paths and the cluster lease API are for), which the
    fixed ``@operator`` principal enforces.
    """
    return lease.caller == f"a2a:{actor.identity}"


def _lease_bus_identity(lease) -> str | None:
    """The bus identity *lease* was taken under, or None.

    ``gpu_claim`` records its leases as ``a2a:<bus identity>``. A lease taken
    through the cluster API directly (``skald-dispatcher``) has no bus claim
    behind it, so there is no identity to attribute a RELEASE to either.
    """
    caller = (getattr(lease, "caller", "") or "").strip()
    prefix = "a2a:"
    if caller.startswith(prefix):
        return caller[len(prefix):].strip() or None
    return None


async def _holder_for(request: Request, identity: str) -> str:
    """The readable ``@handle`` for a bus *identity*, else the identity itself."""
    registry = getattr(request.app.state, "agent_registry", None)
    record = await registry.get(identity) if registry is not None else None
    handle = _clean_handle((record or {}).get("handle"))
    if not handle:
        return identity
    return handle if handle.startswith("@") else f"@{handle}"


async def _claim_holder_actor(request: Request, lease, actor: _Actor) -> _Actor:
    """The actor a line ABOUT *lease* must be attributed to.

    A claim is keyed on its bus author, so an operator (or any caller acting on
    a lease it does not hold) must post as the holder whose claim the line
    closes - otherwise the post clears nothing and peers keep reading the node
    as claimed while the local lease is already gone (an admin session may post
    with an explicit ``from``, docs/agent-coordination.md). A lease with no bus
    claim behind it (a non-``a2a:`` caller, e.g. ``skald-dispatcher``) has no
    holder to attribute to, so the acting identity stands.
    """
    if _lease_owned_by(lease, actor):
        return actor
    owner = _lease_bus_identity(lease)
    if owner is None:
        return actor
    return _Actor(
        identity=owner,
        holder=await _holder_for(request, owner),
        credential=actor.credential,
        is_admin=actor.is_admin,
    )


def _may_act_on(lease, actor: _Actor) -> bool:
    """Ownership for an EXPLICIT lease id: the holder, or an operator."""
    return _lease_owned_by(lease, actor) or actor.is_admin


def _find_lease(cluster, lease_id: str):
    """Look up an active lease by id, or None."""
    for lease in cluster.get_leases():
        if lease.lease_id == lease_id:
            return lease
    return None


# ── endpoints ─────────────────────────────────────────────────────────────────


@router.get("/api/a2a/gpu/check")
async def gpu_check(request: Request):
    """May the caller load on *node*? Reads the channel, folds claims, probes VRAM.

    Authorized readers: an admin session / host local token, or an active agent
    registry JWT holding ``a2a_receive``. Fails closed with 503 when the channel
    cannot be read (an unreadable channel must never read as "free").
    """
    actor = await _resolve_actor(request, None, "a2a_receive")
    node = (request.query_params.get("node") or "").strip()
    if not node:
        return JSONResponse({"error": "node required"}, status_code=400)
    # A figure that cannot be parsed must be a 400, not a silent 0: a CHECK that
    # quietly downgrades to "how many claims are open" is indistinguishable from
    # one that checked the VRAM, which is the failure mode this surface exists
    # to remove.
    raw_vram = request.query_params.get("vram_mb") or request.query_params.get("vram")
    required_mb = _resolve_vram_mb(None, raw_vram)
    if raw_vram is not None and required_mb is None:
        return JSONResponse(
            {
                "error": (
                    "vram_mb must be an integer number of MiB "
                    "(or use vram='~6gb')"
                )
            },
            status_code=400,
        )
    channel = (request.query_params.get("channel") or "").strip() or _channel()
    resource = (request.query_params.get("resource") or _DEFAULT_RESOURCE).strip()
    body, _claims = await _check_node(
        request,
        node=node,
        required_mb=required_mb or 0,
        actor=actor,
        channel=channel,
        resource=resource or _DEFAULT_RESOURCE,
    )
    body["holder"] = actor.holder
    return body


@router.post("/api/a2a/gpu/claim")
async def gpu_claim(request: Request, body: ClaimBody):
    """Claim *node* for this caller: admission check + cluster lease + bus post.

    Returns 409 with the admission detail when the node is claimed by someone
    else or lacks the VRAM; 503 when the channel cannot be read. The cluster
    lease is rolled back if the bus post fails, so a claim is never half-made.
    """
    node = (body.node or "").strip()
    if not node:
        return JSONResponse({"error": "node required"}, status_code=400)
    actor = await _resolve_actor(request, body.holder, "a2a_send")

    vram_mb = _resolve_vram_mb(body.vram_mb, body.vram)
    if vram_mb is None or vram_mb <= 0:
        return JSONResponse(
            {"error": "vram_mb (or vram, e.g. '~6gb') required and must be > 0"},
            status_code=400,
        )

    channel = (body.channel or "").strip() or _channel()

    # Productized half: a real lease the scheduler enforces. Only for a node
    # this controller knows as a worker; an external node is bus-governed. The
    # lease identity/resource are resolved BEFORE admission so a re-claim of
    # the caller's OWN lease is admitted as a REPLACEMENT of it (see below).
    cluster = getattr(request.app.state, "cluster_manager", None)
    lease_node = cluster is not None and _match_worker(cluster, node) is not None
    caller = f"a2a:{actor.identity}"
    resource_id = (
        _resource_id(_canonical_node(cluster, node), body.resource)
        if lease_node and cluster is not None
        else None
    )
    existing = (
        cluster.find_existing_lease(resource_id)
        if cluster is not None and resource_id is not None
        else None
    )
    # A re-claim of our own lease replaces the reservation rather than adding
    # a second one. Charging the caller's own claim again would read a card
    # whose load is already reflected in the live free VRAM as full and deny
    # the idempotent re-POST the fold window needs (CR on #2988).
    reclaimer = existing is not None and existing.caller == caller

    admission, _claims = await _check_node(
        request,
        node=node,
        required_mb=vram_mb,
        actor=actor,
        channel=channel,
        resource=body.resource,
        replace_own=reclaimer,
    )
    if not admission["admitted"]:
        return JSONResponse({"status": "denied", **admission}, status_code=409)

    lease_id: str | None = None
    lease = None
    created_lease = False
    if cluster is not None and resource_id is not None:
        if existing is not None and existing.caller != caller:
            return JSONResponse(
                {
                    "status": "denied",
                    **admission,
                    "reason": f"{resource_id} is leased by {existing.caller}",
                    "blockers": [existing.caller],
                },
                status_code=409,
            )
        if existing is not None:
            # Idempotent re-claim: extend our own lease rather than failing on
            # the "already leased" guard (which the manager applies to every
            # caller, including the current holder).
            lease = await cluster.renew_lease(
                existing.lease_id, ttl_seconds=float(body.ttl_seconds)
            )
            lease_id = existing.lease_id if lease is not None else None
        else:
            lease = await cluster.claim_lease(
                resource_id=resource_id,
                caller=caller,
                ttl_seconds=float(body.ttl_seconds),
                required_vram_mb=vram_mb,
                claim_channel=channel,
            )
            if lease is None:
                return JSONResponse(
                    {
                        "status": "denied",
                        **admission,
                        "reason": f"cluster refused a lease on {resource_id}",
                    },
                    status_code=409,
                )
            lease_id = lease.lease_id
            created_lease = True

    # Publish the holder's own expiry on the line, so a peer's fold can drop a
    # claim whose holder crashed or stopped keeping alive instead of blocking
    # the card forever. It is the backing cluster lease's expiry when there is
    # one, else the TTL this call asked for (a bus-only node is governed by the
    # bus alone, so the line is the only thing that can free it).
    claim_expires_at = (
        getattr(lease, "expires_at", None)
        if lease is not None
        else time.time() + float(body.ttl_seconds)
    )
    line = render_claim(
        node,
        actor.holder,
        vram_mb,
        body.reason,
        body.eta,
        expires_at=claim_expires_at,
    )
    try:
        posted = await _post_line(channel, actor, line)
    except HTTPException:
        # Roll back only a lease THIS call created. A re-claim renews the
        # caller's own pre-existing lease, and freeing that on a transient bus
        # failure would drop a reservation the holder still believes it owns.
        if created_lease and lease_id is not None and cluster is not None:
            await cluster.release_lease(lease_id)
        raise

    return {
        "status": "claimed",
        "node": node,
        "holder": actor.holder,
        "vram_mb": vram_mb,
        "lease_id": lease_id,
        "expires_at": getattr(lease, "expires_at", None),
        "claim_expires_at": claim_expires_at,
        "line": line,
        "channel": channel,
        "message": posted,
        "admission": admission,
    }


@router.post("/api/a2a/gpu/release")
async def gpu_release(request: Request, body: ReleaseBody):
    """Release this caller's claim on *node*: cluster lease + ``[GPU RELEASE]``.

    Idempotent: the release line is posted even when no local lease is found, so
    a peer holding the bus-side claim learns the node is free.
    """
    node = (body.node or "").strip()
    if not node:
        return JSONResponse({"error": "node required"}, status_code=400)
    actor = await _resolve_actor(request, body.holder, "a2a_send")
    channel = (body.channel or "").strip() or _channel()

    cluster = getattr(request.app.state, "cluster_manager", None)
    released_id = body.lease_id
    lease = None
    if cluster is not None:
        if released_id is not None:
            # A caller-supplied id must belong to the caller: releasing another
            # holder's lease (and posting the RELEASE that clears its bus claim)
            # would hand any agent with a2a_send the power to free someone
            # else's GPU. An id that names no ACTIVE lease is a no-op (it has
            # already expired), so it falls through to the idempotent post.
            lease = _find_lease(cluster, released_id)
            if lease is not None and not _may_act_on(lease, actor):
                return JSONResponse(
                    {"error": "not the lease holder", "lease_id": released_id},
                    status_code=403,
                )
        else:
            lease = _lease_for_actor(
                cluster,
                _resource_id(_canonical_node(cluster, node), body.resource),
                actor,
            )
            released_id = lease.lease_id if lease is not None else None

    # Whose claim the [GPU RELEASE] closes. An operator freeing another holder's
    # lease by explicit id must attribute the line to THAT holder: a claim is
    # keyed on its bus AUTHOR, so a line posted as @operator clears nothing and
    # every peer's fold keeps reading the node as claimed while the local lease
    # is already gone (CodeRabbit on #2988). A bus that authenticates senders
    # refuses the substitution, and the post-before-release ordering below then
    # leaves the local lease intact, so the override cannot half-apply.
    line_actor = (
        actor if lease is None else await _claim_holder_actor(request, lease, actor)
    )

    # Post BEFORE releasing the local lease, so a bus failure changes nothing
    # and the caller can retry. Releasing first would free the node here while
    # peers still read an open claim, i.e. block a node that is actually free
    # (Kilo review of #2988). release_lease itself is an idempotent in-memory
    # pop, so the two halves cannot be left disagreeing in the other direction.
    line = render_release(node, line_actor.holder)
    posted = await _post_line(channel, line_actor, line)
    if released_id is not None and cluster is not None:
        await cluster.release_lease(released_id)
    return {
        "status": "released",
        "node": node,
        "holder": actor.holder,
        "released_holder": line_actor.holder,
        "lease_id": released_id,
        "line": line,
        "channel": channel,
        "message": posted,
    }


@router.post("/api/a2a/gpu/request")
async def gpu_request(request: Request, body: RequestBody):
    """Post ``[GPU REQUEST]`` — the caller is blocked and asks for a window."""
    node = (body.node or "").strip()
    if not node:
        return JSONResponse({"error": "node required"}, status_code=400)
    actor = await _resolve_actor(request, body.holder, "a2a_send")
    need_mb = _resolve_vram_mb(body.need_mb, body.need)
    if need_mb is None or need_mb <= 0:
        return JSONResponse(
            {"error": "need_mb (or need, e.g. '~6gb') required and must be > 0"},
            status_code=400,
        )
    channel = (body.channel or "").strip() or _channel()
    line = render_request(node, need_mb, body.reason)
    posted = await _post_line(channel, actor, line)
    return {
        "status": "requested",
        "node": node,
        "holder": actor.holder,
        "need_mb": need_mb,
        "line": line,
        "channel": channel,
        "message": posted,
    }


@router.post("/api/a2a/gpu/renew")
async def gpu_renew(request: Request, body: RenewBody):
    """Keep-alive: extend the TTL of a lease taken through :func:`gpu_claim`.

    The lease expires on its own if the holder stops renewing, which is how a
    crashed or idle consumer frees the node without anyone releasing it.
    """
    actor = await _resolve_actor(request, None, "a2a_send")
    cluster = getattr(request.app.state, "cluster_manager", None)
    if cluster is None:
        return JSONResponse({"error": "cluster manager unavailable"}, status_code=503)
    # Ownership is checked BEFORE the renewal: renewing first and rejecting
    # afterwards would already have extended another holder's lease.
    existing = _find_lease(cluster, body.lease_id)
    if existing is None:
        return JSONResponse(
            {"error": "lease not found or expired", "lease_id": body.lease_id},
            status_code=409,
        )
    if not _may_act_on(existing, actor):
        return JSONResponse({"error": "not the lease holder"}, status_code=403)
    # The renewal's other half is the bus claim. The manager returns the expiry
    # this renewal actually REPLACED (read under its lock): if the claim cannot
    # be refreshed the local expiry is rolled back to it, so the two views agree
    # rather than this controller holding a reservation every peer has already
    # seen lapse. Reading the expiry here instead would be stale as soon as a
    # concurrent renewal lands (CR on #2988).
    lease, previous_expiry = await cluster.renew_lease_with_previous(
        body.lease_id, ttl_seconds=float(body.ttl_seconds)
    )
    if lease is None:
        return JSONResponse(
            {"error": "lease not found or expired", "lease_id": body.lease_id},
            status_code=409,
        )
    # Keep the BUS claim alive too. A fold drops a claim whose published expiry
    # has passed, so a holder that only renewed its lease would let the claim
    # lapse while it still holds the card - and a peer's fold would then read
    # the node as free, which is the co-load this surface exists to prevent.
    # The repost is the claim line shape, so the fold replaces the previous
    # claim (same node + identity) rather than double-counting it.
    attempted_expiry = lease.expires_at
    node, _, _resource = (lease.resource_id or "").partition(":")
    hold = await _claim_holder_actor(request, lease, actor)
    # The channel is an input to the CLAIM, never to its renewal: refreshing
    # onto a different thread would leave the original claim to expire while
    # this lease is still held (CR on #2988).
    channel = getattr(lease, "claim_channel", "") or _channel()
    line = render_claim(
        node,
        hold.holder,
        lease.required_vram_mb or 0,
        reason="keep-alive",
        expires_at=lease.expires_at,
    )
    bus_claim_refreshed = False
    refresh_error: str | None = None
    if node:
        try:
            await _post_line(channel, hold, line)
            bus_claim_refreshed = True
        except HTTPException:
            # Half a renewal is no renewal: the claim never reached the bus, so
            # peers free the card at the expiry they still hold. Undo the local
            # extension and report it, rather than hold a reservation nobody
            # else can see.
            refresh_error = "a2a bus unavailable"
            restored = await cluster.restore_lease_expiry(
                lease.lease_id, previous_expiry, attempted_expiry=attempted_expiry
            )
            if not restored:
                # The lease went away, or another renewal landed while this
                # one's bus post was in flight: that newer expiry stands.
                refresh_error = "a2a bus unavailable; a newer renewal stands"
            logger.warning(
                "A2A GPU lease %s keep-alive rolled back (restored=%s): "
                "claim repost failed (channel=%s)",
                lease.lease_id,
                restored,
                channel,
            )
    return {
        "status": "renewed",
        "lease_id": lease.lease_id,
        "resource_id": lease.resource_id,
        "expires_at": lease.expires_at,
        "line": line,
        "channel": channel,
        "bus_claim_refreshed": bus_claim_refreshed,
        "bus_refresh_error": refresh_error,
    }
