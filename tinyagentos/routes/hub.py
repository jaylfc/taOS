"""Local hub API the Hub app consumes -- hub social slices 2 and 3.

See ``docs/design/hub-social-network-foundation.md`` ("The Hub app (client)").
This is the controller-side local API, mirroring how the chat routes wrap the
chat stores: it reads and writes the node's own signed objects in the local hub
store (``tinyagentos/hub/store.py``) and mints/uses the node's identity keypair
(slice 1). Directory calls (identity, friend-request brokering, presence along
edges) reach taos.my through ``tinyagentos/routes/account_proxy.py`` additions;
peer traffic is a later slice. Nothing here talks to a peer.

Slice 2 surfaces the node's own profile: render it and create/update it with a
version bump. Slice 3 adds the social-graph surface: signed follow and
cache-grant statements (stored locally; caching itself is a later slice), the
friend-request send/accept/decline flows that broker through the directory,
and the local block/mute operations. Every response carries an explicit ``state``
so the app can render the standard degrade states (design "Client resilience")
without guessing:

- ``no-identity``: the node has not minted a hub identity yet.
- ``no-profile``: identity exists but no profile has been published.
- ``ok``: data is present (returned under the relevant key).

The account signed-out state is handled one layer up by the ``current_user``
dependency (401), exactly like every other local app route.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.contacts_store import generate_peer_token
from tinyagentos.hub import identity, posts, relationships, store as hub_store
from tinyagentos.routes.account_proxy import _forward_to

logger = logging.getLogger(__name__)

router = APIRouter()


# --- request bodies ----------------------------------------------------------


class ProfileIn(BaseModel):
    kind: str = "personal"
    display_name: str = ""
    bio: str = ""
    avatar: str | None = None
    links: list | None = None


class FollowIn(BaseModel):
    target_fingerprint: str


class CacheGrantIn(BaseModel):
    grantee_fingerprint: str
    quota_hint: Optional[int] = None


class FriendRequestIn(BaseModel):
    target_fingerprint: str
    intro: str = ""


class PeerIn(BaseModel):
    peer_fingerprint: str


class AcceptIn(BaseModel):
    peer_fingerprint: Optional[str] = None


# --- shared helpers -----------------------------------------------------------

async def _get_store(request: Request) -> hub_store.HubStore:
    """Return the node's hub store, opening it lazily on first use.

    Cached on ``app.state`` so repeated calls share one connection. The path is
    resolved the same way as the identity keystore (``TAOS_DATA_DIR``), so the
    store and identity file live together under ``<data_dir>/hub/`` and tests get
    an isolated dir for free.
    """
    existing = getattr(request.app.state, "hub_store", None)
    if existing is not None:
        return existing
    store = hub_store.HubStore(Path(hub_store.default_db_path()))
    await store.init()
    request.app.state.hub_store = store
    return store


async def _try_handshake(
    request: Request,
    directory_resp: dict,
    peer_fingerprint: str,
) -> None:
    """Establish a peer channel on friend-accept (collab A2).

    Extracts the peer's Ed25519/X25519 pubkeys and advertised endpoints
    from the directory response.  Falls back to the local hub_authors
    table for pubkeys when the directory omits them.

    On success a contact row is created (or refreshed) and a peer link is
    established with a freshly minted inbound token.  Failures are
    logged but never block the accept — the accept always succeeds even
    when the handshake side-effect temporarily can't complete.
    """
    contacts_store = getattr(request.app.state, "contacts_store", None)
    if contacts_store is None:
        return

    username = directory_resp.get("username") or directory_resp.get("target") or ""

    # contact_id is keyed on the peer's signing-key fingerprint — the canonical
    # author identifier (see hub/store.py) — never the peer-controlled username.
    # A username collision or rename therefore can neither overwrite a pinned
    # contact's key material nor fragment the same peer across two contact rows.
    if not peer_fingerprint:
        # Without a fingerprint there is nothing stable to pin TOFU against;
        # skip the handshake rather than key on a mutable name.
        return
    contact_id = f"hub:{peer_fingerprint}"

    # Pubkeys: directory first, then local hub_authors cache.
    ed25519_pub = directory_resp.get("signing_pubkey") or ""
    x25519_pub = directory_resp.get("encryption_pubkey") or ""

    try:
        # Guard: a blocked peer must not be resurrected on re-accept.
        # Wrapped inside the try block so a store/has_edge failure never
        # blocks the accept — the handshake is always best-effort.
        store = await _get_store(request)
        if peer_fingerprint and await store.has_edge(peer_fingerprint, relationships.REL_BLOCK):
            return
        if not ed25519_pub or not x25519_pub:
            # Fall back to hub_authors (populated during friend-request flow).
            store = await _get_store(request)
            author = await store.get_author(peer_fingerprint) if peer_fingerprint else None
            if author:
                ed25519_pub = ed25519_pub or author.get("signing_pubkey", "")
                x25519_pub = x25519_pub or author.get("encryption_pubkey", "")

        if not ed25519_pub or not x25519_pub:
            logger.warning(
                "friend-accept handshake skipped: no pubkeys for %s", contact_id
            )
            return

        # Verify the directory-supplied pubkey matches the expected peer fingerprint.
        # A mismatch (or malformed hex from a malicious directory) means the
        # directory returned a key for the wrong identity; skip the handshake
        # to avoid pinning TOFU keys from an imposter.
        if peer_fingerprint and identity.fingerprint(ed25519_pub) != peer_fingerprint:
            logger.warning(
                "friend-accept handshake skipped: pubkey fingerprint mismatch for %s "
                "(expected %s)",
                contact_id,
                peer_fingerprint,
            )
            return

        display_name = directory_resp.get("display_name") or username
        endpoints = directory_resp.get("endpoints")
        if isinstance(endpoints, str):
            try:
                endpoints = json.loads(endpoints)
            except (ValueError, TypeError):
                endpoints = []
        if not isinstance(endpoints, list):
            endpoints = []

        # Normalize bare strings to the dict form consumed by peer link
        # consumers (e.g., #2045's contact grid expects url/kind/priority).
        endpoints = [
            {"kind": "hub", "url": e, "priority": i}
            if isinstance(e, str)
            else e
            for i, e in enumerate(endpoints)
        ]

        # Create/refresh the contact row (trust-on-first-use key pinning).
        await contacts_store.add_contact(
            contact_id=contact_id,
            hub_username=username,
            display_name=display_name,
            ed25519_pub=ed25519_pub,
            x25519_pub=x25519_pub,
            peer_fingerprint=peer_fingerprint,
        )

        # Mint the inbound token WE give to the remote instance.
        # NOTE: A2 intentionally stores the inbound token locally but does NOT
        # deliver it to the remote peer — the token exchange channel doesn't
        # exist yet.  A3 (the first handshake reply containing the remote's
        # outbound token) completes the two-way exchange.  Until then, the
        # inbound auth channel is inert (no remote request will carry this
        # token) and find_contact_by_inbound_token() will never match.
        inbound_token = generate_peer_token()
        # The outbound_token is the token THEY mint for us — we don't have it
        # until the first handshake reply arrives.  Store an empty placeholder
        # so the peer link row exists; the first handshake reply (future A3)
        # updates this field.
        outbound_token = ""

        await contacts_store.establish_peer_link(
            contact_id=contact_id,
            inbound_token=inbound_token,
            outbound_token=outbound_token,
            endpoints=endpoints,
        )
        logger.info(
            "friend-accept handshake: contact=%s endpoints=%s",
            contact_id, endpoints,
        )
    except Exception:
        logger.exception("friend-accept handshake failed for %s", contact_id)


# --- slice 2: own profile ---------------------------------------------------


@router.get("/api/hub/profile")
async def get_own_profile(
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Render the node's own profile, with explicit degrade states."""
    if not identity.exists():
        return {"state": "no-identity"}
    store = await _get_store(request)
    fingerprint = identity.signing_fingerprint()
    profile = await store.get_profile(fingerprint)
    if profile is None:
        return {"state": "no-profile", "identity": identity.public_identity()}
    return {"state": "ok", "profile": profile}


@router.put("/api/hub/profile")
async def put_own_profile(
    body: ProfileIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Create or update the node's own profile, bumping the version.

    Minting the identity on first use (design: the free username mints the
    keypair) so publishing a profile is the natural first act. The new version is
    one past the current profile's, keeping the highest-version-wins invariant.
    """
    # Mint the identity if this is the node's first hub action.
    identity.load_or_create()
    store = await _get_store(request)
    fingerprint = identity.signing_fingerprint()
    current = await store.get_profile(fingerprint)
    next_version = (current["version"] + 1) if current else 1
    try:
        profile = hub_store.build_profile(
            version=next_version,
            kind=body.kind,
            display_name=body.display_name,
            bio=body.bio,
            avatar=body.avatar,
            links=body.links,
            author=fingerprint,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    signed = hub_store.sign_object(profile)
    stored = await store.put_profile(signed)
    return {"state": "ok", "profile": stored}


# --- slice 3: follow / friend / circle ----------------------------------------


@router.put("/api/hub/follow")
async def follow_peer(
    body: FollowIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Publish a signed one-way follow of ``target_fingerprint`` (design "Follow").

    A follow grants nothing beyond subscribing to the target's public posts; the
    target need not approve it (they can block). The signed statement is stored
    locally as an out-follow and is what this node later publishes to peers.
    """
    identity.load_or_create()
    store = await _get_store(request)
    fingerprint = identity.signing_fingerprint()
    statement = relationships.sign_statement(
        relationships.build_follow_statement(body.target_fingerprint, author=fingerprint)
    )
    await store.put_relationship(
        body.target_fingerprint, relationships.REL_FOLLOW_OUT, statement=statement
    )
    return {
        "state": "following",
        "target": body.target_fingerprint,
        "statement": statement,
    }


@router.put("/api/hub/cache-grant")
async def grant_cache(
    body: CacheGrantIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Publish a signed cache-grant to ``grantee_fingerprint`` (design "circle").

    The grant sits on top of friendship: it authorizes the friend to cache the
    author's recent content and to serve it when the author is offline. Slice 3
    stores the signed statement but does NOT yet act on it (the cache worker and
    quotas are slice 6); the Friends view surfaces the grant so the UI ships
    ahead of the cache engine. ``quota_hint`` is the suggested per-friend budget
    in bytes (defaults to a few hundred MB per design open-question 4).
    """
    identity.load_or_create()
    store = await _get_store(request)
    fingerprint = identity.signing_fingerprint()
    quota = (
        body.quota_hint
        if body.quota_hint is not None
        else relationships.DEFAULT_CACHE_QUOTA_BYTES
    )
    statement = relationships.sign_statement(
        relationships.build_cache_grant_statement(
            body.grantee_fingerprint, quota, author=fingerprint
        )
    )
    await store.put_relationship(
        body.grantee_fingerprint,
        relationships.REL_CACHE_GRANT,
        statement=statement,
        quota_hint=quota,
    )
    return {
        "state": "granted",
        "grantee": body.grantee_fingerprint,
        "quota_hint": quota,
        "statement": statement,
        "note": "stored, not yet acted on (cache worker lands in slice 6)",
    }


@router.post("/api/hub/friends/request")
async def send_friend_request(
    body: FriendRequestIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Send a brokered friend request (design "Friend" + "Directory API surface").

    Builds a signed intro ``{to, author, intro, sig}`` and brokers it through
    the directory (``POST /api/hub/requests``) so the target can accept out of
    band. The directory is the only place the signed request envelope rests (it is
    a request, not content). We record the outgoing request locally so the Friends
    view can show the pending state; the directory's own response (e.g. a request
    id) passes through verbatim.
    """
    identity.load_or_create()
    store = await _get_store(request)
    fingerprint = identity.signing_fingerprint()
    intro = relationships.sign_statement(
        {"type": "friend-request", "author": fingerprint, "to": body.target_fingerprint,
         "intro": body.intro}
    )
    # Record locally before brokering so a later failure still leaves the UI state.
    await store.put_relationship(
        body.target_fingerprint, relationships.REL_REQUEST_OUT, statement=intro
    )
    upstream = await _forward_to(
        request, "POST", "/api/hub/requests", body=json.dumps(intro).encode("utf-8")
    )
    if upstream.status_code < 200 or upstream.status_code >= 300:
        # Directory rejected (rate-limited, target unknown, ...): surface it without
        # pretending the request landed. The local pending marker stays so the user
        # can retry; nothing was revoked.
        return JSONResponse(
            {"state": "rejected", "target": body.target_fingerprint,
             "directory": upstream.status_code},
            status_code=upstream.status_code,
        )
    try:
        directory = json.loads(upstream.body)
    except (ValueError, TypeError):
        directory = None
    return {
        "state": "sent",
        "target": body.target_fingerprint,
        "directory": directory,
    }


@router.get("/api/hub/friends/requests")
async def inbox_friend_requests(
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Read the directory's friend-request inbox (the brokered request envelope)."""
    upstream = await _forward_to(request, "GET", "/api/hub/requests")
    return JSONResponse(
        json.loads(upstream.body) if upstream.body else {},
        status_code=upstream.status_code,
        media_type=upstream.media_type,
    )


@router.post("/api/hub/friends/requests/{rid}/accept")
async def accept_friend_request(
    rid: str,
    body: AcceptIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Accept a brokered friend request and record the local accepted edge.

    The directory accept records the server-side authorization edge (who may query
    whose presence / leave hints) and returns the parties' endpoints so the nodes
    complete the handshake directly. On a 2xx we record the mutual ``friend``
    edge locally so this node's own presence gate and sync worker treat the peer as
    an accepted edge (design: presence requires "an accepted edge"). The peer
    fingerprint comes from the directory response when present, else the body.
    """
    upstream = await _forward_to(request, "POST", f"/api/hub/requests/{rid}/accept")
    if upstream.status_code < 200 or upstream.status_code >= 300:
        return JSONResponse(
            {"state": "rejected"}, status_code=upstream.status_code
        )
    try:
        resp = json.loads(upstream.body) if upstream.body else {}
    except (ValueError, TypeError):
        resp = {}
    peer = body.peer_fingerprint or resp.get("peer") or resp.get("target") or resp.get(
        "username"
    )
    store = await _get_store(request)
    fingerprint = identity.signing_fingerprint()
    if peer:
        statement = relationships.sign_statement(
            relationships.build_friend_statement(peer, author=fingerprint)
        )
        await store.put_relationship(
            peer, relationships.REL_FRIEND, statement=statement
        )

    # --- A2: friend-accept -> contact row + peer-link handshake ---
    # The directory response carries the peer's identity material (pubkeys,
    # endpoints) so we can establish the peer channel immediately on accept.
    # When the directory omits these fields, we fall back to the locally cached
    # hub_authors record (populated during the friend-request flow).
    if peer:
        await _try_handshake(request, resp, peer)

    return {"state": "accepted", "peer": peer, "directory": resp}


@router.post("/api/hub/friends/requests/{rid}/decline")
async def decline_friend_request(
    rid: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Decline a brokered friend request; record it locally as declined."""
    upstream = await _forward_to(request, "POST", f"/api/hub/requests/{rid}/decline")
    if upstream.status_code < 200 or upstream.status_code >= 300:
        return JSONResponse(
            {"state": "rejected"}, status_code=upstream.status_code
        )
    # A social action implies this node has an identity (its fingerprint is the
    # local party); mint it if this is the first act.
    identity.load_or_create()
    store = await _get_store(request)
    # The directory owns the target's fingerprint; if it echoed it back we record
    # the decline against that peer, else just pass the directory response through.
    try:
        resp = json.loads(upstream.body) if upstream.body else {}
    except (ValueError, TypeError):
        resp = {}
    peer = resp.get("peer") or resp.get("target") or resp.get("username")
    if peer:
        await store.put_relationship(peer, relationships.REL_REQUEST_DECLINED)
    return {"state": "declined", "peer": peer, "directory": resp}


@router.post("/api/hub/friends/block")
async def block_peer(
    body: PeerIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Block a peer: strong, local-first (design "Abuse and safety").

    Blocking unfollows, removes the peer from friendship and circle, and severs
    every local edge to them (follow / friend / cache-grant / pending requests)
    so nothing about them is rendered or cached. It also asks the hub to sever the
    server-side accepted edge (no more presence visibility or hints either way);
    that directory call is best-effort and its result never blocks the local op.
    """
    store = await _get_store(request)
    # A social action implies this node has an identity (its fingerprint is the
    # local party in every relationship), so mint it if this is the first act.
    identity.load_or_create()
    peer = body.peer_fingerprint
    # Sever first, then record the block so the purge does not wipe the marker.
    severed = await store.sever_edges(peer, keep={relationships.REL_BLOCK})
    await store.put_relationship(peer, relationships.REL_BLOCK)
    # Best-effort: tell the hub to drop the accepted edge. Never let a directory
    # failure (or an unconfigured proxy -> 503) block the local operation.
    try:
        await _forward_to(
            request,
            "POST",
            "/api/hub/edges/revoke",
            body=json.dumps({"peer": peer}).encode("utf-8"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("hub block: directory edge revoke failed: %s", exc)

    # Cascade to contacts: revoke the peer link so the blocked contact can no
    # longer authenticate on the peer channel (A2 subscribe-to-block).
    # Resolve the peer via its signing-key fingerprint — the canonical contact
    # key — rather than the hub_authors username cache, which is peer-controlled
    # and can be stale (renamed since the contact was pinned).  A present-but-
    # stale username row must never break revocation.
    contacts_store = getattr(request.app.state, "contacts_store", None)
    if contacts_store is not None:
        try:
            # Resolve ALL contacts pinned to this fingerprint.  Legacy
            # username-keyed rows (or a rename mid-flight) can leave several
            # contacts sharing a fingerprint; revoke each one rather than
            # silently picking the first.
            contacts = await contacts_store.get_contacts_by_fingerprint(peer)
            if not contacts:
                logger.warning(
                    "hub block: could not resolve fingerprint %s to a "
                    "contact; peer link may still be active", peer,
                )
            for contact in contacts:
                cid = contact["contact_id"]
                revoked = await contacts_store.revoke_peer_link(cid)
                if not revoked:
                    # A revoke that matched no peer_link row must not be
                    # silently treated as success — log it loudly so a
                    # fail-open regression is visible.
                    logger.warning(
                        "hub block: revoke_peer_link matched no peer_link row "
                        "for contact %s (fingerprint %s); link may already be "
                        "absent or revoked", cid, peer,
                    )
                # Mark the contact as blocked so the UI reflects the distinct
                # status rather than leaving it at the prior accepted state.
                try:
                    await contacts_store.set_contact_status(cid, "blocked")
                except Exception:
                    logger.warning(
                        "hub block: set_contact_status blocked failed for %s", cid
                    )
        except Exception:
            logger.exception("hub block: contacts-store cascade failed for %s", peer)

    return {"state": "blocked", "peer": peer, "severed": severed}


@router.post("/api/hub/friends/mute")
async def mute_peer(
    body: PeerIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Mute a peer locally: stop rendering, keep the edge (design "block and mute
    are local + circle operations"; mute is the local-only subset)."""
    identity.load_or_create()
    store = await _get_store(request)
    await store.put_relationship(body.peer_fingerprint, relationships.REL_MUTE)
    return {"state": "muted", "peer": body.peer_fingerprint}


@router.post("/api/hub/friends/unmute")
async def unmute_peer(
    body: PeerIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Unmute a previously muted peer (local-only operation)."""
    identity.load_or_create()
    store = await _get_store(request)
    await store.delete_relationship(body.peer_fingerprint, relationships.REL_MUTE)
    return {"state": "unmuted", "peer": body.peer_fingerprint}


@router.get("/api/hub/friends")
async def list_friends(
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """The local social-graph view for the Friends pane (not the directory inbox).

    Returns the node's own follows, accepted friends, cache grants, blocks, mutes,
    and pending/declined requests. Presence and the directory request inbox are
    separate calls (``GET /api/hub/presence``, ``GET /api/hub/friends/requests``).
    """
    if not identity.exists():
        return {"state": "no-identity"}
    store = await _get_store(request)

    async def _peers(kind: str) -> list[str]:
        return [r["peer"] for r in await store.list_relationships(kind)]

    grants = [
        {"peer": r["peer"], "quota_hint": r["quota_hint"]}
        for r in await store.list_relationships(relationships.REL_CACHE_GRANT)
    ]
    return {
        "state": "ok",
        "identity": identity.public_identity(),
        "follows_out": await _peers(relationships.REL_FOLLOW_OUT),
        "friends": await _peers(relationships.REL_FRIEND),
        "cache_grants": grants,
        "blocks": await _peers(relationships.REL_BLOCK),
        "mutes": await _peers(relationships.REL_MUTE),
        "requests_out": await _peers(relationships.REL_REQUEST_OUT),
        "requests_in": await _peers(relationships.REL_REQUEST_IN),
        "requests_declined": await _peers(relationships.REL_REQUEST_DECLINED),
    }


@router.get("/api/hub/presence")
async def lookup_presence(
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Look up a peer's presence endpoints, gated on an accepted edge.

    Design "Directory API surface": ``GET /api/hub/presence`` returns endpoints
    only if the requester's identity is authorized (an accepted edge exists). We
    enforce that locally first (the node must hold a ``friend`` edge to the peer)
    and, when authorized, forward to the directory, which enforces the same rule
    server-side; either way, no accepted edge means no endpoints. The peer is
    identified by signing fingerprint (``peer``); a ``username`` is resolved to a
    fingerprint via the locally cached author map when only the username is known.
    """
    if not identity.exists():
        return {"state": "no-identity"}
    store = await _get_store(request)
    peer = request.query_params.get("peer", "")
    username = request.query_params.get("username", "")
    if peer:
        target_fp = peer
    elif username:
        author = await store.get_author_by_username(username)
        if author is None:
            return JSONResponse(
                {"error": "unknown username", "username": username}, status_code=404
            )
        target_fp = author["fingerprint"]
    else:
        return JSONResponse(
            {"error": "peer or username required"}, status_code=400
        )
    # Edge authorization: presence requires an accepted (mutual friend) edge.
    if not await store.has_edge(target_fp, relationships.REL_FRIEND):
        return JSONResponse(
            {"error": "not authorized", "reason": "no accepted edge", "peer": target_fp},
            status_code=403,
        )
    upstream = await _forward_to(
        request, "GET", f"/api/hub/presence?username={username or target_fp}"
    )
    return JSONResponse(
        json.loads(upstream.body) if upstream.body else {},
        status_code=upstream.status_code,
        media_type=upstream.media_type,
    )


# --- slice 4: posts, own timeline, tombstone -----------------------------------


class PostIn(BaseModel):
    visibility: str = "circle"  # friends-only by default (design: composer default)
    text: str = ""
    # Optional inline attachments, each {"data": <base64|data URI>, "mime": str}.
    # The server re-encodes (strips EXIF, caps dimensions) and stores the blob.
    attachments: list | None = None


@router.post("/api/hub/posts")
async def create_post(
    body: PostIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Publish a signed post to this node's own chain (design "Post", slice 4).

    The visibility switch defaults to ``circle`` (friends-only), the design's
    loud default. Inline image attachments are re-encoded and their EXIF stripped
    (``posts.ingest_image``) before the blob is stored and referenced by hash;
    the post body itself only ever carries the blob hash, never the bytes. The
    post is chain-positioned and signed before it lands in the store, so it is
    self-verifying the moment it is created.
    """
    identity.load_or_create()
    store = await _get_store(request)
    attachments: list[dict] = []
    if body.attachments:
        for att in body.attachments:
            if not isinstance(att, dict) or not att.get("data"):
                return JSONResponse(
                    {"error": "each attachment needs base64 'data'"},
                    status_code=400,
                )
            try:
                raw = posts.decode_attachment_data(str(att["data"]))
                ingested = posts.ingest_image(raw, att.get("mime"))
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            await store.put_blob(ingested["data"], mime=ingested["mime"])
            attachments.append(
                {"blob": ingested["blob"], "size": ingested["size"], "mime": ingested["mime"]}
            )
    try:
        post = await posts.append_post(
            store,
            visibility=body.visibility,
            text=body.text,
            attachments=attachments or None,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    # Include the content address so a client can reference the post (e.g. to
    # delete it) without recomputing the SHA-256 of the canonical bytes.
    post = {**post, "hash": hub_store.object_hash(post)}
    return {"state": "ok", "post": post}


@router.get("/api/hub/timeline")
async def own_timeline(
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """The node's own posts, assembled from the local store (design slice 4).

    Slice 4 is single-author only (no peer sync yet): the timeline is every post
    this node has published that still has a body, in chain (seq) order, with
    ``created_at`` as the display sort. Degrade states mirror the profile routes:
    ``no-identity`` before the node has minted a hub identity, else ``ok`` with
    the posts. The timeline works fully offline from the local store.
    """
    if not identity.exists():
        return {"state": "no-identity"}
    store = await _get_store(request)
    fingerprint = identity.signing_fingerprint()
    posts_list = await store.list_posts(fingerprint)
    posts_list.sort(key=lambda p: (p.get("created_at", ""), p.get("seq", 0)))
    return {"state": "ok", "posts": posts_list}


@router.post("/api/hub/posts/{post_hash}/delete")
async def delete_post_route(
    post_hash: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Delete a post via a signed tombstone (design "Deletion", slice 4).

    Appends a tombstone to the chain (advancing the head) and drops the post body
    and its blobs from the local store; the tombstone's chain-index row survives
    so the chain stays verifiable. Best-effort: a peer that later pulls will see
    the tombstone and drop its copy too. Returns the tombstone statement.
    """
    identity.load_or_create()
    store = await _get_store(request)
    try:
        tomb = await posts.delete_post(store, post_hash)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    return {"state": "ok", "tombstone": tomb}
