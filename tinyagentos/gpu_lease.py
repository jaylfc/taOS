# tinyagentos/gpu_lease.py
"""A2A GPU lease protocol — CHECK / CLAIM / RELEASE / REQUEST over the bus.

taOS #893: two agents (@taOS and @taOSmd) share one physical GPU and must not
silently co-load past its VRAM. Found when a FLUX image generation OOM-killed
itself because the other agent had ~9.4 GB of Ollama models loaded on the same
card despite an earlier "free" signal.

The coordination channel is the A2A bus (see ``routes/a2a_bus.py``) and the wire
format is a single readable line so a human watching the channel can follow it::

    [GPU CLAIM] node=linstation holder=@taOSmd vram=~9.4gb reason=ollama eta=~10m
    [GPU RELEASE] node=linstation holder=@taOSmd
    [GPU REQUEST] node=linstation need=~6gb
    [GPU CHECK] node=linstation need=~6gb

This module owns the FORMAT and the FOLD: parsing a line, rendering a line, and
reducing a channel's message history to the claims still open (a CLAIM is closed
by a later RELEASE from the same holder). It is deliberately pure — no FastAPI,
no httpx — so the admission rules can be tested without a bus.

Identity
--------
The authoritative holder of a claim is the BUS MESSAGE AUTHOR (``from``), not
the ``holder=`` field in the body. The body is caller-controlled text; ``from``
is the identity the bus authenticated (taOS #2112, ``routes/a2a_bus.py``).
``holder=`` is kept in the rendered line for human readability, and is also
accepted when matching an identity so the interim handle-spelled posts that
predate bus auth still close correctly. Bus ids are matched first and wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping

# Message kinds.
CLAIM = "CLAIM"
RELEASE = "RELEASE"
REQUEST = "REQUEST"
CHECK = "CHECK"
_KINDS = (CLAIM, RELEASE, REQUEST, CHECK)

_HEADER_RE = re.compile(
    r"^\s*\[\s*GPU\s+(?P<kind>CLAIM|RELEASE|REQUEST|CHECK)\s*\]\s*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)

# A field starts at the beginning of the string or after whitespace. Values can
# contain spaces (`reason=flux image gen`), so a field's value runs until the
# next `key=` token rather than to the next whitespace.
_FIELD_START_RE = re.compile(r"(?:^|\s)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=")

_VRAM_RE = re.compile(
    r"^\s*~?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>gib|gb|g|mib|mb|m)?\s*$",
    re.IGNORECASE,
)

# Field names understood in a line body. Unknown keys are ignored (forward
# compatibility: a future `priority=` must not break an older reader).
_FIELD_ALIASES = {"needed": "need", "vram_mb": "vram"}


def _clean(text: object) -> str:
    """Collapse *text* to a single printable, structural-free line.

    Every field value is caller-supplied text that ends up inside a one-line bus
    message. A raw newline would let a caller inject a SECOND protocol line
    (e.g. a RELEASE that closes another holder's claim), and an unescaped ``=``
    would let it inject a `key=` INSIDE a value that a reader re-parses as a new
    field (`reason=a node=ghost` moved the node). So: whitespace runs collapse
    to one space, non-printable characters are dropped, and ``=`` is neutralised
    by turning it into a space (a value is never structural).
    """
    keep = "".join(
        " " if ch == "=" or ch.isspace() else (ch if ch.isprintable() else "")
        for ch in str(text)
    )
    return " ".join(keep.split())


def _split_fields(rest: str) -> dict[str, str]:
    """Return ``key=value`` pairs from a line body, values allowed spaces.

    A key seen twice keeps its FIRST value. Values may legitimately contain the
    text of a later field, so last-wins would let a trailing ``node=`` inside a
    ``reason`` overwrite the real node (CWE-290 by value injection); the fields
    this module renders are always emitted node/holder/vram first, so first-wins
    keeps the structural fields stable even for a hand-written line.
    """
    matches = list(_FIELD_START_RE.finditer(rest))
    fields: dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(rest)
        key = _FIELD_ALIASES.get(m.group("key").lower(), m.group("key").lower())
        fields.setdefault(key, rest[start:end].strip())
    return fields


def parse_vram_mb(text: object) -> int | None:
    """Parse a VRAM figure (``"~9.4gb"``, ``"4096mb"``, ``"6"``) into MiB.

    A unit-less number is MiB, matching the internal convention. Returns None
    for anything unparseable so a caller can tell "no figure given" from "zero".
    """
    if text is None:
        return None
    m = _VRAM_RE.match(str(text))
    if m is None:
        return None
    num = float(m.group("num"))
    unit = (m.group("unit") or "mb").lower()
    if unit in ("gib", "gb", "g"):
        return int(round(num * 1024))
    return int(round(num))


def format_vram_mb(mb: int | None) -> str:
    """Render MiB as a bus-friendly figure (``~9.4gb`` / ``512mb``)."""
    if mb is None:
        return "?"
    if mb >= 1024:
        gb = f"{mb / 1024:.1f}".rstrip("0").rstrip(".")
        return f"~{gb}gb"
    return f"{mb}mb"


def same_holder(a: str | None, b: str | None) -> bool:
    """True when two identity spellings name the same holder.

    Case-insensitive and ``@``-insensitive so a registry canonical_id and the
    readable ``@handle`` alias of the same agent compare equal. Nil/empty
    identities never match anything (an unidentified claim must not be
    mistaken for the caller's own).
    """
    if not a or not b:
        return False
    return a.strip().lstrip("@").casefold() == b.strip().lstrip("@").casefold()


@dataclass(frozen=True)
class GpuLeaseMessage:
    """One parsed ``[GPU ...]`` line plus the bus metadata it arrived with."""

    kind: str
    node: str
    holder: str = ""
    vram_mb: int | None = None
    reason: str = ""
    eta: str = ""
    raw: str = ""
    message_id: int | None = None
    ts: float | None = None
    bus_from: str | None = None

    @property
    def identity_key(self) -> str:
        """The authenticated author when known, else the body's holder."""
        return (self.bus_from or self.holder or "").strip()

    def display_holder(self) -> str:
        """Readable holder for a rendered line (never empty)."""
        return (self.holder or self.bus_from or "@unknown").strip()

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "node": self.node,
            "holder": self.display_holder(),
            "identity": self.identity_key,
            "vram_mb": self.vram_mb,
            "reason": self.reason,
            "eta": self.eta,
            "message_id": self.message_id,
            "ts": self.ts,
        }


def parse_message(
    raw: object,
    *,
    sender: str | None = None,
    message_id: int | None = None,
    ts: float | None = None,
) -> GpuLeaseMessage | None:
    """Parse a bus message (dict) or a bare line body into a message.

    Returns None when *raw* is not a GPU lease line or carries no ``node=``.
    A dict is expected in the bus shape (``id``/``ts``/``from``/``body``); its
    ``from`` becomes the authoritative ``bus_from`` identity.
    """
    if isinstance(raw, GpuLeaseMessage):
        return raw

    body: str
    if isinstance(raw, Mapping):
        body = str(raw.get("body") or raw.get("text") or "")
        sender = raw.get("from") or sender
        message_id = raw.get("id", message_id)
        ts = raw.get("ts", ts)
    else:
        body = str(raw)

    m = _HEADER_RE.match(body)
    if m is None:
        return None
    fields = _split_fields(m.group("rest"))
    node = fields.get("node", "").strip()
    if not node:
        return None
    kind = m.group("kind").upper()
    vram_raw = fields.get("vram")
    if vram_raw is None and kind in (REQUEST, CHECK):
        vram_raw = fields.get("need")
    return GpuLeaseMessage(
        kind=kind,
        node=node,
        holder=_clean(fields.get("holder", ""))[:64],
        vram_mb=parse_vram_mb(vram_raw) if vram_raw is not None else None,
        reason=_clean(fields.get("reason", ""))[:200],
        eta=_clean(fields.get("eta", ""))[:64],
        raw=body.strip(),
        message_id=int(message_id) if isinstance(message_id, int) else None,
        ts=float(ts) if isinstance(ts, (int, float)) else None,
        bus_from=sender.strip() if isinstance(sender, str) and sender.strip() else None,
    )


def render_claim(
    node: str, holder: str, vram_mb: int, reason: str = "", eta: str = ""
) -> str:
    """Render a ``[GPU CLAIM]`` line. Every value is flattened to one line."""
    parts = [
        f"node={_clean(node)}",
        f"holder={_clean(holder)}",
        f"vram={format_vram_mb(vram_mb)}",
    ]
    if reason:
        parts.append(f"reason={_clean(reason)}")
    if eta:
        parts.append(f"eta={_clean(eta)}")
    return f"[GPU CLAIM] {' '.join(parts)}"


def render_release(node: str, holder: str) -> str:
    """Render a ``[GPU RELEASE]`` line."""
    return f"[GPU RELEASE] node={_clean(node)} holder={_clean(holder)}"


def render_request(node: str, need_mb: int, reason: str = "") -> str:
    """Render a ``[GPU REQUEST]`` line."""
    parts = [f"node={_clean(node)}", f"need={format_vram_mb(need_mb)}"]
    if reason:
        parts.append(f"reason={_clean(reason)}")
    return f"[GPU REQUEST] {' '.join(parts)}"


def render_check(node: str, need_mb: int | None = None) -> str:
    """Render a ``[GPU CHECK]`` line (informational; CHECK is normally local)."""
    parts = [f"node={_clean(node)}"]
    if need_mb is not None:
        parts.append(f"need={format_vram_mb(need_mb)}")
    return f"[GPU CHECK] {' '.join(parts)}"


def open_claims(messages: Iterable[object]) -> dict[str, list[GpuLeaseMessage]]:
    """Fold a channel's messages into the claims that are still open.

    Messages are expected oldest-first (the bus returns them in that order). A
    CLAIM opens a slot keyed by ``(node, identity)``; a RELEASE from the same
    identity on the same node closes it. A re-CLAIM by the same identity
    replaces the previous one (so a reposted claim is never double-counted).

    Both the node and the identity are matched case-insensitively: peers do not
    agree on the spelling of a hostname, and a `node=Linstation` claim whose
    release says `node=linstation` would otherwise stay open forever while a
    differently-spelled claim hid in a second group. The returned mapping is
    therefore keyed by the CASE-FOLDED node name; each message keeps the
    spelling it arrived with.
    """
    open_by_key: dict[tuple[str, str], GpuLeaseMessage] = {}
    order: list[tuple[str, str]] = []
    for raw in messages:
        msg = parse_message(raw)
        if msg is None or msg.kind not in (CLAIM, RELEASE):
            continue
        key = (msg.node.strip().casefold(), msg.identity_key.casefold())
        if msg.kind == CLAIM:
            if key not in open_by_key:
                order.append(key)
            open_by_key[key] = msg
        else:
            open_by_key.pop(key, None)

    out: dict[str, list[GpuLeaseMessage]] = {}
    for key in order:
        msg = open_by_key.get(key)
        if msg is not None:
            out.setdefault(key[0], []).append(msg)
    return out


def claims_for_node(
    claims: Mapping[str, list[GpuLeaseMessage]], node: str
) -> list[GpuLeaseMessage]:
    """Claims for a node, matching the node case-insensitively."""
    wanted = (node or "").strip().casefold()
    entries = claims.get(wanted)
    if entries:
        return list(entries)
    # Tolerate a caller-built mapping whose keys were not case-folded.
    for name, entries in claims.items():
        if name.strip().casefold() == wanted:
            return list(entries)
    return []


def _is_mine(claim: GpuLeaseMessage, identity: str) -> bool:
    """True when *claim* was made by *identity*.

    When the bus authenticated the author (``bus_from``), ONLY that value is
    compared. The body's ``holder=`` is caller-controlled, so accepting it would
    let an attacker post ``from=@attacker holder=@victim`` and make the victim's
    own admission treat the attacker's claim as the victim's, i.e. not a blocker
    (CWE-290). The readable holder is only a fallback for the interim posts that
    predate bus auth and therefore have no authenticated author.
    """
    if claim.bus_from:
        return same_holder(claim.bus_from, identity)
    return same_holder(claim.holder, identity)


@dataclass(frozen=True)
class Admission:
    """The outcome of a CHECK: may this holder load on this node?"""

    admitted: bool
    node: str
    required_mb: int
    claimed_mb: int = 0
    free_mb: int | None = None
    capacity_mb: int | None = None
    blockers: tuple[str, ...] = ()
    reason: str | None = None
    verified: bool = True

    def as_dict(self) -> dict:
        return {
            "admitted": self.admitted,
            "node": self.node,
            "required_mb": self.required_mb,
            "claimed_mb": self.claimed_mb,
            "free_mb": self.free_mb,
            "capacity_mb": self.capacity_mb,
            "blockers": list(self.blockers),
            "reason": self.reason,
            "vram_verified": self.verified,
        }


def evaluate_admission(
    *,
    node: str,
    required_mb: int,
    identity: str,
    claims: Iterable[GpuLeaseMessage] = (),
    free_mb: int | None = None,
    capacity_mb: int | None = None,
) -> Admission:
    """Decide whether *identity* may use *node* for *required_mb* of VRAM.

    Rules, in order:

    1. A claim by ANOTHER holder blocks the node outright — the issue's rule is
       "load only if free + unclaimed", independent of how much VRAM is free.
    2. With no other holder, the caller's remaining need is checked against the
       node's budget: live free VRAM when known, else total capacity. The
       caller's own prior claim is subtracted (it is a pending load not yet
       reflected in a physical probe) so a re-check cannot over-admit.
    3. When neither figure is known the claim is admitted but flagged
       ``verified=False`` with a reason: a node that cannot report VRAM is a
       gap in the guarantee, and the caller must probe before loading rather
       than be told a silent "free".
    """
    required_mb = max(0, int(required_mb))
    entries = list(claims)
    others = [c for c in entries if not _is_mine(c, identity)]
    own_mb = sum((c.vram_mb or 0) for c in entries if _is_mine(c, identity))

    if others:
        blockers = tuple(
            sorted({c.display_holder() for c in others if c.display_holder()})
        )
        return Admission(
            admitted=False,
            node=node,
            required_mb=required_mb,
            claimed_mb=own_mb,
            free_mb=free_mb,
            capacity_mb=capacity_mb,
            blockers=blockers,
            reason=(
                f"{node} is already claimed by {', '.join(blockers)}; "
                "release it or post a [GPU REQUEST] to negotiate a window"
            ),
        )

    budget = free_mb if free_mb is not None else capacity_mb
    if budget is None:
        return Admission(
            admitted=True,
            node=node,
            required_mb=required_mb,
            claimed_mb=own_mb,
            verified=False,
            reason=(
                f"{node} reports no VRAM figure — claim admitted unverified; "
                "probe nvidia-smi before loading"
            ),
        )

    available = max(0, int(budget) - own_mb)
    if required_mb > available:
        return Admission(
            admitted=False,
            node=node,
            required_mb=required_mb,
            claimed_mb=own_mb,
            free_mb=free_mb,
            capacity_mb=capacity_mb,
            reason=(
                f"insufficient VRAM on {node}: need {required_mb} MiB, "
                f"{available} MiB available"
            ),
        )
    return Admission(
        admitted=True,
        node=node,
        required_mb=required_mb,
        claimed_mb=own_mb,
        free_mb=free_mb,
        capacity_mb=capacity_mb,
    )
