"""Follow / friend / circle relationship statements -- hub social slice 3.

See ``docs/design/hub-social-network-foundation.md`` ("Follow edge vs
trusted-cache-circle" and the slice plan, slice 3). This module builds the
*signed* statements a node publishes about its social graph:

- **follow** (one-way): ``{type:"follow", author, target, sig}`` published by
  the follower. Grants nothing beyond "my node subscribes to your public posts."
- **cache-grant** (explicit, on top of friendship): ``{type:"cache-grant",
  author, grantee, quota_hint, sig}`` from the author. It grants the friend the
  right AND responsibility to cache the author's recent content. Slice 3 stores it
  but does NOT yet act on it (caching is slice 6); the Friends view surfaces the
  grant so the UI ships ahead of the cache worker.
- **friend** (mutual): recorded on both nodes as a pair of signed statements when
  a brokered request is accepted. Slice 3 records the local half on accept; slice
  5's sync completes the peer's half.

All three reuse the slice-2 canonical-JSON + Ed25519 sign/verify helpers so a
peer node can verify any statement the same way it verifies a profile. Usernames
never appear inside a statement: the author/target/grantee/peer are signing-key
fingerprints, the canonical author identifier from slice 1. The directory maps a
fingerprint back to a username for display.
"""
from __future__ import annotations

from typing import Optional

from tinyagentos.hub import identity, store as hub_store

# Local relationship kinds persisted in ``hub_relationships``. "friend" is the only
# kind the presence gate authorizes (design: presence lookup requires "an accepted
# edge"); follow grants only subscription and cache-grant is not an edge at all.
REL_FOLLOW_OUT = "follow_out"
REL_FOLLOW_IN = "follow_in"
REL_FRIEND = "friend"
REL_CACHE_GRANT = "cache_grant"
REL_BLOCK = "block"
REL_MUTE = "mute"
REL_REQUEST_OUT = "friend_request_out"
REL_REQUEST_IN = "friend_request_in"
REL_REQUEST_DECLINED = "friend_request_declined"

# Kinds that count as an *accepted edge* for presence authorization. A mutual
# friend edge is the only one (a one-way follow is not enough to see presence).
EDGE_KINDS = (REL_FRIEND,)

# Default per-friend cache budget (a few hundred MB; design open-question 4). The
# grantee enforces its own real quota in slice 6; this is the author's hint.
DEFAULT_CACHE_QUOTA_BYTES = 300 * 1024 * 1024


def build_follow_statement(
    target_fingerprint: str, author: Optional[str] = None
) -> dict:
    """Build an *unsigned* follow statement published by the follower.

    ``author`` defaults to this node's signing fingerprint. Callers sign it with
    :func:`tinyagentos.hub.store.sign_object` before storing or publishing.
    """
    return {
        "type": "follow",
        "author": author or identity.signing_fingerprint(),
        "target": target_fingerprint,
    }


def build_cache_grant_statement(
    grantee_fingerprint: str,
    quota_hint: int,
    author: Optional[str] = None,
) -> dict:
    """Build an *unsigned* cache-grant statement (design "trusted-cache-circle").

    ``quota_hint`` is the suggested per-friend cache budget in bytes; the grantee
    enforces its own real quota in slice 6. Slice 3 stores this statement but does
    not yet act on it.
    """
    return {
        "type": "cache-grant",
        "author": author or identity.signing_fingerprint(),
        "grantee": grantee_fingerprint,
        "quota_hint": int(quota_hint),
    }


def build_friend_statement(
    peer_fingerprint: str, author: Optional[str] = None
) -> dict:
    """Build an *unsigned* friend statement, the local half recorded on accept.

    A friendship is a pair of these (one per node); slice 5's sync completes the
    peer's half. Sign with :func:`sign_object` before storing.
    """
    return {
        "type": "friend",
        "author": author or identity.signing_fingerprint(),
        "peer": peer_fingerprint,
    }


def sign_statement(statement: dict) -> dict:
    """Sign an unsigned relationship statement with this node's key; returns a copy.

    Wraps :func:`store.sign_object` so callers build then sign in one step.
    """
    return hub_store.sign_object(statement)
