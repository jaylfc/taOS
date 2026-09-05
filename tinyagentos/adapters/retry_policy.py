"""Shared outbound-retry policy for the framework adapters.

Every adapter's ``/message`` proxy makes the same shape of call: one POST to a
locally-running framework agent, behind ``with_retry``, inside a request that
``channel_hub/router.py`` is already waiting on.  The numbers therefore have to
agree with each other, so they live here once instead of being copy-pasted into
every adapter module.

The arithmetic that matters:

    worst case = RETRY_BUDGET_SECONDS + CONTROLLER_TIMEOUT_SECONDS

``with_retry`` starts no attempt once the budget has elapsed and clamps every
backoff to what is left of it, so the only overrun is the final in-flight call.
That total must stay under ``ROUTER_TIMEOUT_SECONDS``: past it the router has
already abandoned the request and every further retry is work nobody reads.
"""
from __future__ import annotations

# How long channel_hub/router.py waits for an adapter's POST /message.
ROUTER_TIMEOUT_SECONDS = 120.0

# Per-attempt HTTP timeout for the adapter -> framework-agent call.
CONTROLLER_TIMEOUT_SECONDS = 60.0

# Wall-clock budget for the whole retry loop (attempts plus backoff).
RETRY_BUDGET_SECONDS = 45.0

# Keyword arguments every proxying adapter passes to with_retry().  max_delay
# stays generous because the budget, not the per-sleep cap, is what ends the
# loop; seven attempts cover a framework-agent restart window.
RETRY_KWARGS = dict(
    max_attempts=7,
    base_delay=0.5,
    multiplier=2.0,
    max_delay=60.0,
    max_total_seconds=RETRY_BUDGET_SECONDS,
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
