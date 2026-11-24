### Fixed
- PIN lockout: per-key lock is no longer dropped while waiters still hold it. Added a waiter refcount to track in-flight acquisitions and only remove the lock entry when the count reaches zero and the entry still matches the current lock.
