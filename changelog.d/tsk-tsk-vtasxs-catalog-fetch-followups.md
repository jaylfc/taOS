### Fixed

- Orb catalog fetch: _accept_and_cache now holds a module-level lock across
  verification, serial recording and both cache writes, so two concurrent
  fetches cannot interleave and leave an older cache behind the recorded
  serial (fail-closed regression). The lock is taken outside accept_catalog's
  own per-state FileLock; ordering is fixed so there is no deadlock.
- Orb catalog fetch test: the compressed-response rejection test now sends
  real gzip content so it fails when the content-encoding guard is missing,
  and asserts the error message contains "compressed response".
