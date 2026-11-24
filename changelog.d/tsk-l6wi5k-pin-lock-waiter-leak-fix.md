### Fixed
- PIN lock waiter count leak when a waiting request is cancelled. The `_acquire_pin_key_lock` function now properly decrements the waiter count and removes the key entry if the await is cancelled or raises an exception. This prevents an unauthenticated attacker from driving memory growth by sending requests with distinct `unknown:<username>` keys and cancelling them.
