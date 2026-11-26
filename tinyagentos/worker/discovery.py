"""mDNS discovery of taOS controllers on the local network (#119).

The controller advertises itself as ``_tinyagentos._tcp.local.`` (see
``tinyagentos.services.mdns_publisher``) with TXT records carrying its
URL, hub identity fingerprint, and hardware tier. A fresh worker browses
for that service during pairing/install so the user can pick a found
controller instead of typing its URL -- and falls back to manual URL
entry when nothing is found (mDNS unavailable, restricted network).

Discovery is LAN-only by construction: mDNS multicast never leaves the
local link. Remote attach stays on the pairing-code + mesh path.

The advertised fingerprint is not trusted on its own -- anyone on the
LAN can copy a TXT record. The pair CLI verifies it by
challenge-response before pairing (see
``tinyagentos.worker.pairing.verify_controller_proof``); the scan here
only supplies the candidates and the fingerprint to check against.

Everything here is best-effort: a failed or empty scan returns an empty
list, never an exception, so callers can always fall through to the
manual path.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass

from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf

from tinyagentos.services.mdns_publisher import (
    DISCOVERY_SERVICE_TYPE,
    TXT_KEY_FINGERPRINT,
    TXT_KEY_TIER,
    TXT_KEY_URL,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ControllerCandidate",
    "choose_candidate",
    "discover_controllers",
    "parse_controller_info",
]


@dataclass(frozen=True)
class ControllerCandidate:
    """One controller found on the LAN."""

    name: str
    url: str
    fingerprint: str
    tier: str | None
    host: str | None
    port: int | None


def _decode_properties(properties) -> dict[str, str]:
    """Normalise a ServiceInfo properties dict to str->str.

    zeroconf hands browser callbacks raw bytes keys/values; tests and
    locally built ServiceInfo objects may carry str. Accept both.
    """
    out: dict[str, str] = {}
    if not properties:
        return out
    for key, value in properties.items():
        if value is None:
            continue
        k = key.decode("utf-8", "replace") if isinstance(key, bytes) else str(key)
        v = (
            value.decode("utf-8", "replace")
            if isinstance(value, bytes)
            else str(value)
        )
        out[k] = v
    return out


def _first_address(info) -> str | None:
    parsed = getattr(info, "parsed_addresses", None)
    if callable(parsed):
        try:
            addrs = parsed()
        except Exception:
            addrs = None
        if addrs:
            return addrs[0]
    return None


def parse_controller_info(info) -> ControllerCandidate | None:
    """Turn a resolved ServiceInfo into a candidate, or None.

    A record is only a taOS controller if it is on the discovery service
    type and carries a non-empty fingerprint TXT -- anything else is a
    foreign or malformed service and is ignored. The URL comes from the
    TXT record, falling back to the service's own address + port.
    """
    if info is None:
        return None
    info_type = getattr(info, "type", DISCOVERY_SERVICE_TYPE)
    if info_type != DISCOVERY_SERVICE_TYPE:
        return None
    props = _decode_properties(getattr(info, "properties", None))
    fingerprint = (props.get(TXT_KEY_FINGERPRINT) or "").strip()
    if not fingerprint:
        return None
    port = getattr(info, "port", None)
    url = (props.get(TXT_KEY_URL) or "").strip()
    host = getattr(info, "server", None)
    if host:
        host = host.rstrip(".")
    if not url:
        address = _first_address(info) or host
        if not address or not port:
            return None
        url = f"http://{address}:{port}"
    name = getattr(info, "name", "") or ""
    suffix = f".{DISCOVERY_SERVICE_TYPE}"
    if name.endswith(suffix):
        name = name[: -len(suffix)]
    return ControllerCandidate(
        name=name or "taOS",
        url=url,
        fingerprint=fingerprint,
        tier=(props.get(TXT_KEY_TIER) or "").strip() or None,
        host=host,
        port=port,
    )


class _NameCollector:
    """AsyncServiceBrowser listener that just records service names."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def add_service(self, zc, service_type: str, name: str) -> None:
        if name not in self.names:
            self.names.append(name)

    def remove_service(self, zc, service_type: str, name: str) -> None:
        if name in self.names:
            self.names.remove(name)

    def update_service(self, zc, service_type: str, name: str) -> None:
        pass


async def discover_controllers(timeout: float = 5.0) -> list[ControllerCandidate]:
    """Browse the LAN for taOS controllers for up to ``timeout`` seconds.

    Returns candidates deduplicated by fingerprint. Never raises: any
    zeroconf failure (multicast disabled, no LAN) yields an empty list
    so the caller can fall back to manual URL entry.
    """
    collector = _NameCollector()
    zc: AsyncZeroconf | None = None
    browser: AsyncServiceBrowser | None = None
    try:
        try:
            zc = AsyncZeroconf()
            # AsyncServiceBrowser needs the underlying Zeroconf instance,
            # not the AsyncZeroconf wrapper: it reaches for zc.cache,
            # which the wrapper does not expose.
            browser = AsyncServiceBrowser(
                zc.zeroconf, DISCOVERY_SERVICE_TYPE, listener=collector
            )
            await asyncio.sleep(max(timeout, 0.0))
        except Exception:
            logger.exception(
                "discovery: browse failed -- treating as no controllers"
            )
            return []

        candidates: list[ControllerCandidate] = []
        seen: set[str] = set()
        for name in collector.names:
            try:
                info = await zc.async_get_service_info(
                    DISCOVERY_SERVICE_TYPE, name
                )
            except Exception:
                logger.debug(
                    "discovery: resolving %s failed", name, exc_info=True
                )
                continue
            cand = parse_controller_info(info)
            if cand is None or cand.fingerprint in seen:
                continue
            seen.add(cand.fingerprint)
            candidates.append(cand)
        return candidates
    finally:
        # One finally for both resources: a browse failure, an early
        # return, or a cancellation during the browse sleep must not
        # leak the AsyncZeroconf instance.
        if browser is not None:
            try:
                await browser.async_cancel()
            except Exception:
                logger.debug("discovery: browser cancel failed", exc_info=True)
        if zc is not None:
            try:
                await zc.async_close()
            except Exception:
                logger.debug("discovery: zeroconf close failed", exc_info=True)


def choose_candidate(
    candidates: list[ControllerCandidate],
    *,
    expect_fingerprint: str | None = None,
    input_fn=input,
    print_fn=None,
) -> ControllerCandidate | None:
    """Pick one candidate, prompting only when there is a real choice.

    With ``expect_fingerprint`` the match is returned (or None) without
    ever prompting -- this is the configured-auto-attach shape. A single
    candidate is picked as-is. Several candidates get a numbered prompt;
    EOF or an invalid answer returns None so the caller can fall back
    to manual URL entry.
    """
    if print_fn is None:
        print_fn = lambda *a: print(*a, file=sys.stderr)  # noqa: E731
    if expect_fingerprint:
        for cand in candidates:
            if cand.fingerprint == expect_fingerprint:
                return cand
        return None
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    print_fn("Discovered taOS controllers on this network:")
    for idx, cand in enumerate(candidates, start=1):
        tier = f", tier {cand.tier}" if cand.tier else ""
        print_fn(
            f"  {idx}. {cand.name} -- {cand.url} "
            f"(fingerprint {cand.fingerprint[:8]}...{tier})"
        )
    # The prompt goes through print_fn (stderr), never input_fn:
    # builtin input() writes its prompt to stdout, and --pick callers
    # capture stdout as the machine-readable URL/fingerprint pair.
    print_fn("Pick a controller by number (blank for none): ")
    try:
        answer = input_fn("")
    except (EOFError, KeyboardInterrupt):
        return None
    answer = (answer or "").strip()
    if not answer:
        return None
    try:
        idx = int(answer)
    except ValueError:
        return None
    if not 1 <= idx <= len(candidates):
        return None
    return candidates[idx - 1]


def main(argv: list[str] | None = None) -> int:
    """CLI: scan and print controllers, or pick one interactively.

    ``python -m tinyagentos.worker.discovery`` prints one line per found
    controller (url, fingerprint, tier, name). With ``--pick`` it offers
    the interactive list on stderr and prints the chosen URL and its
    fingerprint (one per line) to stdout, so shell callers
    (install-worker.sh) can capture both and pass the fingerprint to
    the pair CLI, which verifies the controller holds that hub key
    before pairing.
    """
    parser = argparse.ArgumentParser(
        description="Discover taOS controllers on the local network (mDNS)"
    )
    parser.add_argument(
        "--timeout", type=float, default=6.0,
        help="Seconds to browse (default: 6)",
    )
    parser.add_argument(
        "--expect-fingerprint", default=None,
        help="Only consider the controller with this hub fingerprint",
    )
    parser.add_argument(
        "--pick", action="store_true",
        help="Offer a pick list; print the chosen URL and fingerprint "
             "to stdout",
    )
    args = parser.parse_args(argv)

    candidates = asyncio.run(discover_controllers(timeout=args.timeout))
    if not candidates:
        print(
            "No taOS controllers discovered on this network.",
            file=sys.stderr,
        )
        return 1
    if args.pick:
        chosen = choose_candidate(
            candidates, expect_fingerprint=args.expect_fingerprint
        )
        if chosen is None:
            print("No controller selected.", file=sys.stderr)
            return 1
        print(chosen.url)
        print(chosen.fingerprint)
        return 0
    if args.expect_fingerprint:
        candidates = [
            c for c in candidates if c.fingerprint == args.expect_fingerprint
        ]
        if not candidates:
            return 1
    for cand in candidates:
        print(f"{cand.url}\t{cand.fingerprint}\t{cand.tier or '-'}\t{cand.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
