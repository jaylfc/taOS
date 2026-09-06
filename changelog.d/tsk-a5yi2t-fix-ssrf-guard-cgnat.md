### Fixed

The UnifiedPush SSRF guard now correctly blocks the CGNAT (100.64/10) range, which our own A2A bus lives in. Previously, when `send()` called `validate_url_or_raise` with `allow_private=True`, the guard's `_BLOCKED_NETWORKS` check was bypassed, allowing private and CGNAT addresses.

Now, CGNAT addresses are always blocked regardless of `allow_private`, while other private addresses (RFC1918) are still permitted when `allow_private=True` as intended. This fixes the SSRF vulnerability where devices could target internal infrastructure.