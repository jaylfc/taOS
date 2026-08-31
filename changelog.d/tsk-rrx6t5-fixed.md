### Fixed
- Test ordering dependencies caused by global state leaks in three modules:
  - `group_policy.py`: `_now` changed from function to module-level variable for reliable monkeypatching
  - `health.py`: emit_event guard made explicit with `is not None` check against `self.notifications`
  - `port_allocator.py`: added `reset_pool_boundaries()` to reset stale pool boundary values