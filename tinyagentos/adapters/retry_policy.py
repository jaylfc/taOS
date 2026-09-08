"""Shared outbound-retry policy for the framework adapters.

Every adapter's ``/message`` proxy makes the same shape of call: one POST to a
locally-running framework agent, behind ``with_retry``, inside a request that
``channel_hub/router.py`` is already waiting on.  The numbers therefore have to
agree with each other, so they live here once instead of being copy-pasted into
every adapter module.

The arithmetic that matters:

    worst case = RETRY_BUDGET_SECONDS + CONTROLLER_TIMEOUT_SECONDS

``with_retry`` ends the loop rather than starting an attempt the budget cannot
cover, so the only overrun is the final in-flight call.  That total must stay
under ``ROUTER_TIMEOUT_SECONDS``: past it the router has already abandoned the
request and every further retry is work nobody reads.

``deer_flow_adapter`` is the one adapter these numbers do NOT describe, and the
exemption is deliberate.  It drives a long-horizon LangGraph run behind its own
600 s call timeout, so it keeps a private ``_RETRY_KWARGS`` and is absent from
the parametrised gate in ``tests/clients/test_retry.py``.  The consequence is
worth stating plainly: a DeerFlow run routed through ``channel_hub/router.py``
is abandoned by the router at ``ROUTER_TIMEOUT_SECONDS`` while the run itself
continues.  That mismatch predates this module -- DeerFlow is meant to be
driven directly rather than through the hub -- and giving the router a
per-adapter deadline is the fix if it ever is.
"""
from __future__ import annotations

from types import MappingProxyType

# How long channel_hub/router.py waits for an adapter's POST /message.
ROUTER_TIMEOUT_SECONDS = 120.0

# Per-attempt HTTP timeout for the adapter -> framework-agent call.
CONTROLLER_TIMEOUT_SECONDS = 60.0

# Wall-clock budget for the whole retry loop (attempts plus backoff).
RETRY_BUDGET_SECONDS = 45.0

# Keyword arguments every proxying adapter passes to with_retry().  max_delay
# stays generous because the budget, not the per-sleep cap, is what ends the
# loop; seven attempts cover a framework-agent restart window.
#
# Read-only on purpose.  Each adapter binds this very object as its own
# module-level ``_RETRY_KWARGS`` -- the sharing is the point, since it is what
# keeps the arithmetic above true for all of them at once -- so a plain dict
# would let one module's ``_RETRY_KWARGS["max_attempts"] = 3`` silently retune
# every other adapter and invalidate the assertion below.  A proxy unpacks with
# ``**`` exactly like a dict and refuses the write.
RETRY_KWARGS = MappingProxyType(
    dict(
        max_attempts=7,
        base_delay=0.5,
        multiplier=2.0,
        max_delay=60.0,
        max_total_seconds=RETRY_BUDGET_SECONDS,
    )
)

# Checked at import time, the way the adapter registry validates itself: a
# later edit to any single constant cannot quietly push the loop past the
# router's own deadline.
assert (
    RETRY_BUDGET_SECONDS + CONTROLLER_TIMEOUT_SECONDS < ROUTER_TIMEOUT_SECONDS
), (
    "adapter retry budget outlives the channel-hub router timeout: "
    f"{RETRY_BUDGET_SECONDS} + {CONTROLLER_TIMEOUT_SECONDS} "
    f">= {ROUTER_TIMEOUT_SECONDS}"
)
