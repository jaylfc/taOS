"""Two taOS nodes in one process, each with its own data dir and hub identity.

``tinyagentos.hub.identity`` resolves its keystore from ``TAOS_DATA_DIR`` on
every call (there is no in-process cache), so a node is selected purely by
that variable.  Each node's app is wrapped in an ASGI shim that sets the
variable for the duration of a request and restores the previous value on
exit.  The environment is process-global, so drive the nodes sequentially;
two concurrent requests to different nodes would clobber each other.

``create_app`` must see ``TAOS_DATA_DIR`` unset (``resolve_data_dir`` refuses
a conflicting env and argument), so the variable is popped around it.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml
from httpx import ASGITransport, AsyncClient

_ENV = "TAOS_DATA_DIR"


@contextmanager
def node_env(data_dir: Path | None) -> Iterator[None]:
    """Point ``TAOS_DATA_DIR`` at *data_dir* (or unset it for None), then restore."""
    prev = os.environ.get(_ENV)
    if data_dir is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = str(data_dir)
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(_ENV, None)
        else:
            os.environ[_ENV] = prev


class _NodeEnvASGI:
    """ASGI shim: the wrapped app runs with ``TAOS_DATA_DIR`` set to its node."""

    def __init__(self, app: Any, data_dir: Path) -> None:
        self.app = app
        self.data_dir = data_dir

    async def __call__(self, scope, receive, send) -> None:
        with node_env(self.data_dir):
            await self.app(scope, receive, send)


@dataclass
class Node:
    name: str
    data_dir: Path
    app: Any
    fingerprint: str
    signing_pub: str
    encryption_pub: str
    asgi: Any = field(repr=False)

    @property
    def contact_id(self) -> str:
        """The canonical peer identity: ``hub:<signing fingerprint>``."""
        return f"hub:{self.fingerprint}"

    def client(self) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=self.asgi), base_url=f"http://{self.name}"
        )

    def sign_envelope(
        self,
        *,
        to: str,
        kind: str,
        body: dict | None = None,
        from_id: str | None = None,
    ) -> dict:
        """Sign an envelope with THIS node's key, independent of ``peer.build_envelope``.

        Mirrors ``peer._canonical_json`` byte for byte (sorted keys, compact
        separators, ``ensure_ascii=False``, ``sig`` absent, ``body`` omitted
        when None) so a mismatch is a product defect, not a harness one.
        ``from_id`` defaults to the node's own contact id; pass another value
        to build a refusing-direction case.
        """
        from tinyagentos.hub import identity

        envelope: dict = {
            "from": from_id if from_id is not None else self.contact_id,
            "to": to,
            "kind": kind,
            "ts": time.time(),
            "nonce": secrets.token_hex(16),
        }
        if body is not None:
            envelope["body"] = body
        payload = json.dumps(
            envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        with node_env(self.data_dir):
            envelope["sig"] = identity.sign(payload)
        return envelope


def _write_test_config(data_dir: Path) -> None:
    """The same minimal config ``tests/conftest.py::tmp_data_dir`` writes."""
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [
            {"name": "test-backend", "type": "rkllama", "url": "http://localhost:8080", "priority": 1}
        ],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [
            {"name": "test-agent", "host": "192.168.1.100", "qmd_index": "test", "color": "#98fb98"}
        ],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "config.yaml").write_text(yaml.dump(config))
    (data_dir / ".setup_complete").touch()


async def make_node(name: str, data_dir: Path) -> Node:
    """Build one node: app + initialised contacts/outbox stores + fresh identity."""
    from tinyagentos.app import create_app
    from tinyagentos.hub import identity

    _write_test_config(data_dir)
    with node_env(None):
        app = create_app(data_dir=data_dir)

    store = app.state.contacts_store
    if store._db is not None:
        await store.close()
    await store.init()
    outbox = getattr(app.state, "peer_outbox", None)
    if outbox is not None:
        if getattr(outbox, "_db", None) is not None:
            await outbox.close()
        await outbox.init()
    app.state._startup_complete = True

    with node_env(data_dir):
        identity.load_or_create()
        pub = identity.public_identity()

    return Node(
        name=name,
        data_dir=data_dir,
        app=app,
        fingerprint=pub["fingerprint"],
        signing_pub=pub["signing_pubkey"],
        encryption_pub=pub["encryption_pubkey"],
        asgi=_NodeEnvASGI(app, data_dir),
    )


async def close_node(node: Node) -> None:
    store = node.app.state.contacts_store
    if store is not None and store._db is not None:
        await store.close()
    outbox = getattr(node.app.state, "peer_outbox", None)
    if outbox is not None and getattr(outbox, "_db", None) is not None:
        await outbox.close()


async def pin_contact(
    host: Node,
    peer: Node,
    *,
    contact_id: str | None = None,
    hub_username: str = "peer",
    display_name: str = "Peer",
    ed25519_pub: str | None = None,
) -> str:
    """Give *host* a contact row + peer link for *peer*, from PUBLIC keys only.

    Returns the plaintext inbound token *peer* must present to *host*.
    ``contact_id`` / ``ed25519_pub`` overrides exist for legacy-row cases.
    """
    from tinyagentos.contacts_store import generate_peer_token

    store = host.app.state.contacts_store
    cid = contact_id if contact_id is not None else peer.contact_id
    await store.add_contact(
        contact_id=cid,
        hub_username=hub_username,
        display_name=display_name,
        ed25519_pub=ed25519_pub if ed25519_pub is not None else peer.signing_pub,
        x25519_pub=peer.encryption_pub,
        peer_fingerprint=peer.fingerprint,
        status="active",
    )
    inbound = generate_peer_token()
    await store.establish_peer_link(
        contact_id=cid,
        inbound_token=inbound,
        outbound_token=generate_peer_token(),
        endpoints=[{"kind": "lan", "url": f"http://{peer.name}", "priority": 1}],
    )
    return inbound
