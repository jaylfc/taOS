### Security
- Rate limiters can no longer be made to exhaust memory. The per-IP registries
  behind the unauthenticated project-invite redeem, the cluster manual-claim,
  the routine webhook, the client-log capture and the peer routes grew without
  bound, so a caller with a large address range (an IPv6 /64 is 2^64 keys)
  could push the controller into an out-of-memory kill on a small board. All
  five now share one bounded limiter in `tinyagentos/rate_limit.py` and evict
  the least recently used key once 2000 are tracked.

### Fixed
- Rate-limit windows no longer allow twice the documented burst at the window
  edge. The 20-per-10s limiters reset their counter on the first request after
  the window elapsed, so 20 requests just before the boundary plus 20 just
  after went through in a fraction of a second; the shared limiter now counts
  over a moving window, which halves the reachable brute-force rate against
  the invite PIN.
- Rate-limit windows no longer freeze when the system clock steps backwards.
  They measured elapsed time with the wall clock, so an NTP correction after a
  cold boot on a board without an RTC could lock a caller out until the clock
  caught up. Every limiter now uses the monotonic clock.
- `429 Too Many Requests` responses now carry `Retry-After`, so a client that
  is throttled can back off instead of retrying as fast as it can fail.
